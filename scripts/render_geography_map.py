#!/usr/bin/env python3
"""
Stage 2 (geography variant): stands in for fetch_footage.py for the "geography"
series only. Instead of stock B-roll, this composites the target country onto a
flat cartographic world map (assets/geo/basemap_world.png + a per-country mask
from assets/geo/country_masks/, both built once offline by build_geo_assets.py),
renders ONE continuous camera push-in across the whole video with ffmpeg's
zoompan filter, then pre-slices that single render into one file per narration
segment and writes build/footage.json in the exact schema fetch_footage.py
already produces -- so assemble_video.py needs no changes at all to consume it.

Pre-slicing (rather than listing one clip N times) is required for camera
continuity: assemble_video.py opens each footage.json entry as an independent
ffmpeg input and trims it from that input's own t=0, so one clip referenced by
every segment would snap the camera back to its start at every caption cut.
Slicing the one continuous render into N sequential files means each slice
already starts at its own 0 -- assemble_video.py's existing per-piece trim
reproduces perfect continuity for free. See the plan doc for the full writeup.

segments[].visual is repurposed here (same key, same schema) to hold the
on-screen map label text for that beat (e.g. "Himalayas") instead of a stock
search query. For up to MAX_CUTAWAYS segments whose label names a concrete
filmable feature (a river, sea, mountain range, forest, ...), it IS also used
as a stock search query: fetch_footage.py's own search/verification machinery
fetches a brief real clip of that feature, shown in place of the map for that
beat before returning to the map -- the "reality check" texture the reference
channels use, capped so the map stays the series' dominant visual identity.

Requires: Pillow (composite/crop), ffmpeg (zoompan camera path + slicing).

Output:
    build/footage.json          same schema fetch_footage.py writes
    build/footage_clip{0..N-1}.mp4

Usage:
    python scripts/render_geography_map.py
    python scripts/render_geography_map.py --script build/script.json --out build
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFilter

from fetch_footage import fetch_pexels, fetch_pixabay
from pipeline_common import segment_durations

# ---------------------------------------------------------------------------
# Font/text helpers -- copied from assemble_video.py rather than imported, to
# match this codebase's existing convention of duplicating these small,
# self-contained utilities per-script (generate_intro.py already does the
# same) rather than centralizing them.
# ---------------------------------------------------------------------------

def _find_font(candidates):
    for path in candidates:
        if os.path.exists(path):
            if ":" in path:
                cache_dir = ".font_cache"
                os.makedirs(cache_dir, exist_ok=True)
                cached = os.path.join(cache_dir, os.path.basename(path))
                if not os.path.exists(cached):
                    shutil.copyfile(path, cached)
                return cached
            return path
    return candidates[0]


FONT_BOLD = _find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
])


def drawtext_escape(text):
    return (text.replace("\\", "\\\\")
                .replace("'", "\u2019")
                .replace(":", "\\:")
                .replace("%", "\\%"))


def fontfile_escape(path):
    return path.replace("\\", "/").replace(":", "\\:")


# ---------------------------------------------------------------------------
# Map compositing
# ---------------------------------------------------------------------------

# Rotated by variant_seed so the highlight color varies day to day rather than
# being visually identical every time (same "vary the footprint" ethos as the
# rest of the pipeline's caption/hashtag rotation, just applied to the palette).
TINT_PALETTE = [
    (168, 60, 178),   # magenta/purple
    (60, 150, 210),   # sky blue
    (214, 148, 40),   # amber
    (60, 178, 130),   # teal green
    (206, 70, 90),    # coral red
    (150, 120, 220),  # violet
]


def composite_highlight(basemap_path, mask_path, tint):
    basemap = Image.open(basemap_path).convert("RGB")
    mask = Image.open(mask_path).convert("L")
    if mask.size != basemap.size:
        raise RuntimeError(
            f"mask size {mask.size} != basemap size {basemap.size} -- "
            "regenerate assets with scripts/build_geo_assets.py")

    glow = mask.filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(20))
    canvas = basemap.copy()
    glow_layer = Image.new("RGB", basemap.size, tint)
    canvas = Image.composite(glow_layer, canvas, glow.point(lambda v: int(v * 0.35)))
    tint_layer = Image.new("RGB", basemap.size, tint)
    canvas = Image.composite(tint_layer, canvas, mask)
    return canvas, mask


def _lonlat_to_px(lon, lat, w, h):
    """Equirectangular lon/lat -> pixel, matching build_geo_assets.py's mapping
    (both the basemap and the masks are rendered in that same pixel space)."""
    return (lon + 180.0) / 360.0 * w, (90.0 - lat) / 180.0 * h


def composite_unhighlighted(basemap_path, script):
    """Fallback for a missing mask asset: the plain basemap plus a synthetic
    framing box at the country's lat/lon, so the camera still lands on the right
    place. Nothing gets tinted — an un-highlighted map is honest, whereas
    guessing at a shape would put a wrong highlight on screen."""
    basemap = Image.open(basemap_path).convert("RGB")
    lat, lon = script.get("lat"), script.get("lon")
    if lat is None or lon is None:
        raise SystemExit(
            "no country mask AND no lat/lon in script.json — cannot frame the map")
    x, y = _lonlat_to_px(float(lon), float(lat), basemap.width, basemap.height)
    half = MIN_WINDOW_PX / 2.0
    mask = Image.new("L", basemap.size, 0)
    ImageDraw.Draw(mask).rectangle(
        (max(0, x - half), max(0, y - half),
         min(basemap.width, x + half), min(basemap.height, y + half)),
        fill=255,
    )
    return basemap, mask


# ---------------------------------------------------------------------------
# Camera path
# ---------------------------------------------------------------------------

OUTPUT_ASPECT = 1080 / 1920  # portrait 9:16
MIN_WINDOW_PX = 300          # never zoom in tighter than this (basemap-pixel space)
END_MARGIN = 1.25            # snug framing around the country at the tightest zoom
START_MARGIN_MULT = 2.6      # how much wider the opening frame is than the end frame

# Surplus tail cut onto the end of every slice (see slice_segments). Absorbs
# frame-rounding so `-stream_loop -1` in assemble_video.py can never wrap a
# slice back to its own start mid-segment.
SLICE_PAD_S = 0.15


def compute_camera_windows(mask, canvas_size):
    """Return (start_box, end_box) -- both (x0,y0,x1,y1) rectangles in the
    basemap's pixel space, sharing OUTPUT_ASPECT, centered on the country's
    bounding-box centroid, sized relative to the country's own extent so tiny
    nations (Luxembourg) and huge ones (Russia) both frame sensibly."""
    bbox = mask.getbbox()
    if bbox is None:
        raise RuntimeError("country mask is empty -- check build_geo_assets.py output")
    x0, y0, x1, y1 = bbox
    bbox_w, bbox_h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    end_h = max(MIN_WINDOW_PX, bbox_h * END_MARGIN, (bbox_w / OUTPUT_ASPECT) * END_MARGIN)
    end_w = end_h * OUTPUT_ASPECT
    start_h = end_h * START_MARGIN_MULT
    start_w = start_h * OUTPUT_ASPECT

    canvas_w, canvas_h = canvas_size

    def fit_within_canvas(w, h):
        """Uniformly scale (w, h) down (preserving OUTPUT_ASPECT exactly) so it
        fits inside the canvas. A country wider than the canvas is tall enough
        to contain in portrait aspect (e.g. Russia, ~9x wider than tall) can't
        have its full bbox framed at ANY zoom level in a 9:16 crop -- this just
        means the achievable frame is height-bound and shows a vertical slice
        of the country's extent rather than 100% of its width, which is the
        honest outcome rather than an error."""
        if w <= canvas_w and h <= canvas_h:
            return w, h
        scale = min(canvas_w / w, canvas_h / h)
        return w * scale, h * scale

    end_w, end_h = fit_within_canvas(end_w, end_h)
    start_w, start_h = fit_within_canvas(start_w, start_h)
    if start_h <= end_h:
        # Canvas itself is the binding constraint for both -- no zoom headroom
        # left (happens for extremely elongated countries like Russia); fall
        # back to a static framing rather than an inverted/zero zoom range.
        start_w, start_h = end_w, end_h

    def centered_box(w, h):
        left = min(max(0, cx - w / 2), canvas_w - w)
        top = min(max(0, cy - h / 2), canvas_h - h)
        return (left, top, left + w, top + h)

    return centered_box(start_w, start_h), centered_box(end_w, end_h)


def crop_to_box(image, box):
    x0, y0, x1, y1 = box
    return image.crop((int(x0), int(y0), int(math.ceil(x1)), int(math.ceil(y1))))


def render_camera_path(source_png, out_mp4, duration_s, zoom_ratio, fps=30):
    """One continuous ffmpeg zoompan push-in over the whole video, reusing the
    exact filter shape assemble_video.py's photo Ken-Burns path already proves
    works in this codebase -- just extended to run for the full duration and
    centered on the pre-cropped source's own center (the country centroid),
    rather than a 2-4s segment centered on frame."""
    frames = max(2, round(duration_s * fps))
    step = (zoom_ratio - 1.0) / frames
    vf = (
        "scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,setsar=1,"
        f"zoompan=z='min(zoom+{step:.8f},{zoom_ratio:.6f})':d={frames}:fps={fps}:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", source_png,
        "-vf", vf, "-t", f"{duration_s:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", out_mp4,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg camera-path render failed:\n{result.stderr[-2000:]}")


def slice_segments(camera_mp4, segments, durs, indices, out_dir):
    """Cut the one continuous camera-path video into sequential files, one per
    index in `indices` (in order), each starting at its own t=0 (see module
    docstring for why this matters), burning that beat's map-label text onto
    its slice.

    `indices` is the subset of segment positions actually rendered onto this
    camera path -- segments handled instead by a real-footage cutaway
    (fetch_cutaway_clips) are excluded, and the cursor only advances for
    indices in this list, so the pan stays continuous across the map segments
    even though their positions in the final clip list aren't contiguous.

    Each slice is cut SLICE_PAD_S longer than the duration assemble_video.py
    will ask for, but the read cursor still advances by the UNPADDED duration —
    so the pan stays continuous across slices and the surplus tail is simply
    trimmed away, never shown. Without that pad, a slice even a single frame
    short of what assemble requests gets looped back to its own start by
    `-stream_loop -1`, which reads on screen as the camera snapping backwards
    at the end of every segment.

    Returns {index: path}."""
    clip_paths = {}
    cursor = 0.0
    for i in indices:
        seg, dur = segments[i], durs[i]
        label = (seg.get("visual") or "").strip()
        out_path = os.path.join(out_dir, f"footage_clip{i}.mp4")
        vf_parts = ["setpts=PTS-STARTPTS"]
        if label:
            label_e = drawtext_escape(label)
            font = fontfile_escape(FONT_BOLD)
            vf_parts.append(
                f"drawtext=fontfile={font}:text='{label_e}':"
                "fontcolor=white:fontsize=52:"
                "box=1:boxcolor=black@0.45:boxborderw=18:"
                "x=(w-text_w)/2:y=h*0.22"
            )
        cmd = [
            "ffmpeg", "-y", "-ss", f"{cursor:.3f}", "-t", f"{dur + SLICE_PAD_S:.3f}",
            "-i", camera_mp4, "-vf", ",".join(vf_parts),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an", out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg slice {i} failed:\n{result.stderr[-2000:]}")
        clip_paths[i] = out_path
        cursor += dur  # unpadded: keeps the camera path continuous across cuts
    return clip_paths


# ---------------------------------------------------------------------------
# Footage cutaways -- real stock clips for named physical features
# ---------------------------------------------------------------------------

# A pure map for the whole runtime reads as static once the zoom settles. When
# a beat names a concrete, filmable feature (a specific river, sea, mountain
# range, forest, ...) rather than the country in general, briefly cutting to
# real footage of it -- then returning to the map -- is the "reality check"
# texture the reference channels use. Capped at MAX_CUTAWAYS so the map stays
# the series' dominant visual identity rather than becoming a stock-footage
# montage with map interludes.
FEATURE_KEYWORDS = (
    "river", "sea", "ocean", "mount", "mountain", "mountains", "range", "alps",
    "forest", "jungle", "rainforest", "desert", "valley", "peninsula",
    "coast", "coastline", "bay", "gulf", "lake", "waterfall", "glacier",
    "volcano", "plain", "plains", "plateau", "delta", "canyon", "cape",
    "island", "islands", "strait", "fjord", "cliff", "cliffs", "wetland",
    "swamp", "marsh", "savanna", "savannah", "tundra", "steppe", "dune",
    "dunes", "reef", "lagoon",
)
MAX_CUTAWAYS = 3


def _is_filmable_feature(visual, country_name):
    """A segment's map-label is worth cutting away to real footage for if it
    names a concrete physical feature rather than the country itself (the safe
    default for general beats) or some other non-geographic label."""
    visual = (visual or "").strip()
    if not visual or visual.lower() == (country_name or "").strip().lower():
        return False
    v = visual.lower()
    return any(kw in v for kw in FEATURE_KEYWORDS)


# Category hints appended to a cutaway's search query, keyed by the feature
# word that matched. Bare feature names drift badly on stock search -- "Japan
# Sea" came back a Tokyo shopping street, which is exactly the
# narration-doesn't-match-footage problem this whole series was built to
# avoid. The hint pins the search to the right KIND of subject.
FEATURE_QUERY_HINTS = (
    (("sea", "ocean", "bay", "gulf", "strait", "lagoon", "reef"), "coast water aerial"),
    (("river", "delta", "waterfall"), "water flowing nature"),
    (("mount", "mountain", "mountains", "range", "alps", "volcano", "cliff", "cliffs"),
     "landscape nature"),
    (("forest", "jungle", "rainforest"), "trees nature"),
    (("desert", "dune", "dunes"), "sand landscape"),
    (("lake",), "water landscape"),
    (("island", "islands", "peninsula", "cape", "fjord", "coast", "coastline"),
     "coast aerial landscape"),
    (("glacier", "tundra"), "ice landscape"),
    (("valley", "canyon", "plateau", "plain", "plains", "steppe",
      "savanna", "savannah", "swamp", "marsh", "wetland"), "landscape nature"),
)


def _matched_feature_words(visual):
    """The FEATURE_KEYWORDS actually present in this label, longest first (so
    'mountains' is preferred over its own 'mount' prefix when verifying)."""
    v = (visual or "").lower()
    return sorted((kw for kw in FEATURE_KEYWORDS if kw in v), key=len, reverse=True)


def _cutaway_query(visual, country_name):
    """Build the stock-search query for a feature label.

    Drops a redundant country prefix -- "Japan" + "Japan Sea" searched as
    "Japan Japan Sea", which is what let generic Japan city footage win over
    anything actually maritime -- and appends a category hint so the search
    lands on the right kind of subject."""
    label = (visual or "").strip()
    hint = ""
    for words, h in FEATURE_QUERY_HINTS:
        if any(w in label.lower() for w in words):
            hint = h
            break
    country = (country_name or "").strip()
    if country and country.lower() in label.lower():
        # the label already carries the geographic anchor
        return f"{label} {hint}".strip(), None
    return f"{label} {hint}".strip(), (country or None)


def _depicts_feature(info, visual):
    """Does the clip's own metadata mention the feature it's standing in for?

    Country verification alone is NOT enough -- a Tokyo street clip passes
    "is this Japan?" while being a category error under narration about a
    sea. Pexels videos expose a descriptive URL slug and Pixabay exposes real
    keyword tags, both carried through in the returned attribution/source_url,
    so require the feature word to appear in one of them. Anything that can't
    be confirmed is rejected in favour of staying on the map, which is always
    correct."""
    # Only the RESULT's own metadata -- never the query we sent, which would
    # match itself and make this check vacuous.
    haystack = " ".join(str(info.get(k, "")) for k in
                        ("tags", "attribution", "source_url")).lower()
    haystack = re.sub(r"[^a-z0-9]+", " ", haystack)
    # Whole-word (plus simple plural) so "season" can't satisfy "sea".
    # Erring strict is right here: a false rejection costs one map segment,
    # a false acceptance costs the credibility this series exists to protect.
    return any(re.search(rf"\b{re.escape(w)}s?\b", haystack)
               for w in _matched_feature_words(visual))


def fetch_cutaway_clips(segments, country_name, cfg, out_dir):
    """Fetch real stock VIDEO for up to MAX_CUTAWAYS segments whose map-label
    names a filmable feature, reusing fetch_footage.py's own search functions.

    Video sources only, deliberately: Pexels videos carry a descriptive URL
    slug and Pixabay carries keyword tags, so both can be checked against the
    feature being claimed (_depicts_feature). Pexels PHOTOS expose neither, so
    a wrong subject there is unverifiable -- and an unverifiable cutaway is
    worse than no cutaway, since the map is always a correct visual. archive
    and animate are excluded for the same reason (no subject signal / not a
    real place).

    Returns {index: info_dict} for segments that found footage that actually
    depicts the named feature; info_dict carries "path" (basename in out_dir).
    Every OTHER index -- attempted-and-rejected included -- stays a map
    segment, which is the caller's job to handle."""
    fcfg = cfg.get("footage", {})
    want_portrait = fcfg.get("orientation", "portrait") == "portrait"
    min_height = fcfg.get("min_height", 720)

    candidates = [i for i, seg in enumerate(segments)
                  if _is_filmable_feature(seg.get("visual"), country_name)][:MAX_CUTAWAYS]

    found = {}
    for i in candidates:
        visual = segments[i]["visual"].strip()
        query, anchor = _cutaway_query(visual, country_name)
        dest = os.path.join(out_dir, f"footage_clip{i}.mp4")
        info = None
        for source in ("pexels", "pixabay"):
            try:
                if source == "pexels":
                    info = fetch_pexels(query, dest, want_portrait, min_height, anchor)
                else:
                    info = fetch_pixabay(query, dest, min_height, anchor)
            except Exception as exc:  # noqa: BLE001 -- try the next source
                print(f"[render_geography_map] {source} cutaway {visual!r} failed: {exc}")
                info = None
            if info and os.path.exists(dest) and os.path.getsize(dest) > 0:
                if _depicts_feature(info, visual):
                    break
                print(f"[render_geography_map] cutaway {visual!r}: {source} result "
                      f"doesn't depict the feature -- rejected")
            info = None
        if info:
            info = dict(info)
            info["type"] = "video"
            info["query"] = visual
            info["path"] = os.path.basename(dest)
            found[i] = info
            verified = " (country-verified)" if info.get("country_verified") else ""
            print(f"[render_geography_map] cutaway: segment {i} ({visual!r}) -> "
                  f"{info['source']}{verified}")
        else:
            # nothing trustworthy found -- stays a map segment; make sure a
            # rejected download doesn't linger where a map slice will be written
            if os.path.exists(dest):
                os.remove(dest)
            print(f"[render_geography_map] cutaway: segment {i} ({visual!r}) -> "
                  f"no verifiable footage, staying on the map")
    return found


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr}")
    return float(out.stdout.strip())


def resolve_duration(script, video_cfg, audio_path):
    """Return (duration_seconds, source_label).

    Prefers the REAL synthesized voice length, using the exact same formula
    assemble_video.py uses, so the pre-sliced segment boundaries match what
    assemble will actually request down to the millisecond. This is why the
    workflows run this stage AFTER generate_tts.py: a word-count estimate
    drifted ~7% from the real TTS length, and every segment's shortfall got
    filled by looping that slice back to its own start.

    Falls back to the word-count estimate only if the voice isn't there yet,
    so the script still runs standalone or out of order."""
    if audio_path and os.path.exists(audio_path):
        try:
            audio_dur = ffprobe_duration(audio_path)
            duration = max(video_cfg["min_seconds"],
                           min(audio_dur + 0.4, video_cfg["max_seconds"]))
            return duration, f"voice.wav ({audio_dur:.1f}s)"
        except Exception as exc:  # noqa: BLE001 — fall back to the estimate
            print(f"[render_geography_map] could not probe {audio_path}: {exc}")

    narration = script.get("narration", "")
    words = len(narration.split()) or sum(
        s.get("words", len(s.get("text", "").split())) for s in script.get("segments", []))
    raw = words / video_cfg.get("words_per_second", 2.3)
    duration = max(video_cfg["min_seconds"], min(raw, video_cfg["max_seconds"]))
    return duration, f"word-count estimate ({words} words)"


def main():
    ap = argparse.ArgumentParser(description="Render the geography-series map footage.")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--assets-dir", default="assets/geo")
    ap.add_argument("--audio", default="build/voice.wav",
                    help="Synthesized voice track; its real length drives the segment "
                         "slice boundaries. Falls back to a word-count estimate if absent.")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)

    iso2 = (script.get("iso2") or "").upper()
    if not iso2:
        raise SystemExit(f"{args.script} has no iso2 -- cannot select a country mask")

    basemap_path = os.path.join(args.assets_dir, "basemap_world.png")
    mask_path = os.path.join(args.assets_dir, "country_masks", f"{iso2}.png")

    variant_seed = script.get("variant_seed", script.get("cycle", 0)) or 0
    tint = TINT_PALETTE[variant_seed % len(TINT_PALETTE)]

    if os.path.exists(mask_path):
        print(f"[render_geography_map] compositing {iso2} highlight (tint={tint}) ...")
        highlight, mask = composite_highlight(basemap_path, mask_path, tint)
    else:
        # Losing the whole day's post over one missing asset is worse than
        # shipping an un-highlighted map, so fall back to a plain basemap framed
        # on the country's coordinates. Honest (nothing is mis-highlighted), just
        # less striking. Every country in config/countries.json ships with a mask,
        # so this should only ever fire on a genuinely broken checkout.
        print(f"[render_geography_map] WARNING: no mask at {mask_path} — "
              f"falling back to an un-highlighted map centered on {iso2}")
        highlight, mask = composite_unhighlighted(basemap_path, script)

    start_box, end_box = compute_camera_windows(mask, highlight.size)
    zoom_ratio = (start_box[3] - start_box[1]) / (end_box[3] - end_box[1])
    print(f"[render_geography_map] start_box={start_box} end_box={end_box} "
          f"zoom_ratio={zoom_ratio:.3f}")

    os.makedirs(args.out, exist_ok=True)
    source_png = os.path.join(args.out, "_geo_camera_source.png")
    crop_to_box(highlight, start_box).save(source_png)

    duration, dur_source = resolve_duration(script, cfg["video"], args.audio)
    print(f"[render_geography_map] duration {duration:.2f}s (from {dur_source})")

    segments = script.get("segments") or []
    if not segments:
        raise SystemExit(f"{args.script} has no segments -- nothing to slice")
    durs = segment_durations(script, duration)

    country_name = script.get("name", "")
    cutaways = fetch_cutaway_clips(segments, country_name, cfg, args.out)
    map_indices = [i for i in range(len(segments)) if i not in cutaways]

    clip_paths = {}
    camera_mp4 = os.path.join(args.out, "_geo_camera_full.mp4")
    if map_indices:
        # Camera path only needs to span the MAP segments' own combined
        # duration -- cutaway segments show real footage instead, so the pan
        # doesn't need to "cover" that time within the rendered map video.
        map_duration = sum(durs[i] for i in map_indices)
        render_camera_path(source_png, camera_mp4, map_duration + SLICE_PAD_S, zoom_ratio)
        print(f"[render_geography_map] slicing {len(map_indices)} map segment(s)"
              f"{f', {len(cutaways)} cutaway(s) to real footage' if cutaways else ''} ...")
        clip_paths = slice_segments(camera_mp4, segments, durs, map_indices, args.out)

    clips = []
    for i in range(len(segments)):
        if i in cutaways:
            clips.append(cutaways[i])
        else:
            clips.append({
                "source": "geography_map",
                "query": segments[i].get("visual", ""),
                "source_url": "",
                "attribution": "Rendered map (Natural Earth boundaries, public domain)",
                "resolution": "1080x1920",
                "license": "Generated content -- original render",
                "type": "video",
                "path": os.path.basename(clip_paths[i]),
            })
    manifest = {
        "clip_count": len(clips),
        "clips": clips,
        "source": "geography_map",
        "identifier": iso2,
    }
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    for tmp in (source_png, camera_mp4):
        try:
            os.remove(tmp)
        except OSError:
            pass

    print(f"[render_geography_map] wrote {len(clips)} clips + footage.json for {iso2}")


if __name__ == "__main__":
    main()
