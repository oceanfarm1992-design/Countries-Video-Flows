#!/usr/bin/env python3
"""
Stage 2 (world-data variant): stands in for fetch_footage.py for the
"worlddata" series only, same as render_geography_map.py does for
"geography". Reuses that module's camera-path/slicing/basemap-highlight
functions directly (imported, not duplicated) rather than reimplementing
them.

Two sub-types (script.json's "subtype", set by generate_worlddata_script.py):
  - "stats": IDENTICAL rendering to geography -- basemap + this country's
    mask from assets/geo/country_masks/, one continuous camera push, per-
    segment stat-callout labels burned in.
  - "flags": the country's flag (fetched free from flagcdn.com via
    generate_intro.py's existing fetch_flag(), not reimplemented), composited
    onto a portrait canvas and given the same style of camera push-in, per-
    segment symbol-callout labels burned in. No mask/bbox math needed here --
    the whole image IS the subject, so the zoom is just centered on the flag.

Falls back from "flags" to "stats" rendering if the flag fetch fails
(network issue) -- the country mask is always available locally, so this
guarantees the pipeline never fails a whole video over one flag download.

Requires: Pillow (composite/crop), ffmpeg (zoompan camera path + slicing).

Output:
    build/footage.json          same schema fetch_footage.py writes
    build/footage_clip{0..N-1}.mp4

Usage:
    python scripts/render_worlddata_graphics.py
    python scripts/render_worlddata_graphics.py --script build/script.json --out build
"""
import argparse
import json
import os

from PIL import Image

from generate_intro import fetch_flag
from pipeline_common import segment_durations
from render_geography_map import (
    TINT_PALETTE,
    composite_highlight,
    composite_unhighlighted,
    compute_camera_windows,
    crop_to_box,
    render_camera_path,
    resolve_duration,
    slice_segments,
)

FLAG_BG_COLOR = (10, 14, 26)  # matches generate_intro.py's globe-intro background
FLAG_CANVAS_SIZE = (1080, 1920)
FLAG_ZOOM_RATIO = 1.15  # gentle fixed push-in -- no bbox math needed, the whole
                        # image is the subject, unlike a country's map extent


def compose_flag_source(flag_path, canvas_size=FLAG_CANVAS_SIZE):
    """Center the flag on a dark portrait canvas, scaled to fill most of the
    frame width (capped by height for unusually tall/narrow flags) -- same
    compositing style generate_intro.py already uses for its own flag pin
    overlay, just full-frame instead of a small corner badge."""
    canvas_w, canvas_h = canvas_size
    canvas = Image.new("RGB", canvas_size, FLAG_BG_COLOR)
    flag = Image.open(flag_path).convert("RGB")

    target_w = int(canvas_w * 0.86)
    scale = target_w / flag.width
    target_h = int(flag.height * scale)
    max_h = int(canvas_h * 0.6)
    if target_h > max_h:
        scale = max_h / flag.height
        target_w = int(flag.width * scale)
        target_h = max_h

    flag_resized = flag.resize((max(1, target_w), max(1, target_h)), Image.LANCZOS)
    x = (canvas_w - flag_resized.width) // 2
    y = (canvas_h - flag_resized.height) // 2
    canvas.paste(flag_resized, (x, y))
    return canvas


def render_flags(script, args, segments, durs):
    """Render the "flags" sub-type. Returns the list of clip dicts (same shape
    fetch_footage.py's clips use), or None if the flag fetch failed (caller
    should fall back to render_stats)."""
    iso2 = (script.get("iso2") or "").lower()
    if not iso2:
        return None
    flag_path = os.path.join(args.out, f"_worlddata_flag_{iso2}.png")
    try:
        fetch_flag(iso2, flag_path)
    except Exception as exc:  # noqa: BLE001 -- fall back to stats rendering
        print(f"[render_worlddata_graphics] flag fetch failed ({exc}) — "
              f"falling back to stats rendering for this run")
        return None

    print(f"[render_worlddata_graphics] compositing flag for {iso2.upper()} ...")
    source_png = os.path.join(args.out, "_worlddata_camera_source.png")
    compose_flag_source(flag_path).save(source_png)

    total_duration = sum(durs)
    camera_mp4 = os.path.join(args.out, "_worlddata_camera_full.mp4")
    render_camera_path(source_png, camera_mp4, total_duration + 0.15, FLAG_ZOOM_RATIO)

    indices = list(range(len(segments)))
    clip_paths = slice_segments(camera_mp4, segments, durs, indices, args.out)

    for tmp in (flag_path, source_png, camera_mp4):
        try:
            os.remove(tmp)
        except OSError:
            pass

    return [
        {
            "source": "worlddata_flag",
            "query": segments[i].get("visual", ""),
            "source_url": f"https://flagcdn.com/w1280/{iso2}.png",
            "attribution": "Flag image: flagcdn.com",
            "resolution": "1080x1920",
            "license": "Public domain / free-use national flag image",
            "type": "video",
            "path": os.path.basename(clip_paths[i]),
        }
        for i in range(len(segments))
    ]


def render_stats(script, args, cfg, segments, durs):
    """Render the "stats" sub-type -- identical approach to
    render_geography_map.py's main(): basemap + this country's mask, one
    continuous camera push, per-segment stat-callout labels."""
    iso2 = (script.get("iso2") or "").upper()
    if not iso2:
        raise SystemExit(f"{args.script} has no iso2 -- cannot select a country mask")

    basemap_path = os.path.join(args.assets_dir, "basemap_world.png")
    mask_path = os.path.join(args.assets_dir, "country_masks", f"{iso2}.png")

    variant_seed = script.get("variant_seed", script.get("cycle", 0)) or 0
    tint = TINT_PALETTE[variant_seed % len(TINT_PALETTE)]

    if os.path.exists(mask_path):
        print(f"[render_worlddata_graphics] compositing {iso2} highlight (tint={tint}) ...")
        highlight, mask = composite_highlight(basemap_path, mask_path, tint)
    else:
        print(f"[render_worlddata_graphics] WARNING: no mask at {mask_path} — "
              f"falling back to an un-highlighted map centered on {iso2}")
        highlight, mask = composite_unhighlighted(basemap_path, script)

    start_box, end_box = compute_camera_windows(mask, highlight.size)
    zoom_ratio = (start_box[3] - start_box[1]) / (end_box[3] - end_box[1])

    source_png = os.path.join(args.out, "_worlddata_camera_source.png")
    crop_to_box(highlight, start_box).save(source_png)

    total_duration = sum(durs)
    camera_mp4 = os.path.join(args.out, "_worlddata_camera_full.mp4")
    render_camera_path(source_png, camera_mp4, total_duration + 0.15, zoom_ratio)

    indices = list(range(len(segments)))
    clip_paths = slice_segments(camera_mp4, segments, durs, indices, args.out)

    for tmp in (source_png, camera_mp4):
        try:
            os.remove(tmp)
        except OSError:
            pass

    return [
        {
            "source": "worlddata_stats",
            "query": segments[i].get("visual", ""),
            "source_url": "",
            "attribution": "Rendered map (Natural Earth boundaries, public domain)",
            "resolution": "1080x1920",
            "license": "Generated content -- original render",
            "type": "video",
            "path": os.path.basename(clip_paths[i]),
        }
        for i in range(len(segments))
    ]


def main():
    ap = argparse.ArgumentParser(description="Render the world-data series' flag/stats graphics.")
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

    segments = script.get("segments") or []
    if not segments:
        raise SystemExit(f"{args.script} has no segments -- nothing to slice")

    duration, dur_source = resolve_duration(script, cfg["video"], args.audio)
    print(f"[render_worlddata_graphics] duration {duration:.2f}s (from {dur_source})")
    durs = segment_durations(script, duration)

    os.makedirs(args.out, exist_ok=True)

    subtype = script.get("subtype", "stats")
    clips = None
    if subtype == "flags":
        clips = render_flags(script, args, segments, durs)
        if clips is None:
            subtype = "stats"  # fetch_flag failed -- fall back
    if clips is None:
        clips = render_stats(script, args, cfg, segments, durs)

    manifest = {
        "clip_count": len(clips),
        "clips": clips,
        "source": f"worlddata_{subtype}",
        "identifier": (script.get("iso2") or "").upper(),
    }
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"[render_worlddata_graphics] wrote {len(clips)} clips + footage.json "
          f"(subtype={subtype!r})")


if __name__ == "__main__":
    main()
