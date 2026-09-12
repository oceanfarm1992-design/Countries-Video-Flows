#!/usr/bin/env python3
"""
Stage 2: fetch a MIX of relevant, high-quality B-roll clips for today's country and
concatenate them into one montage, instead of looping a single clip for the whole video.

  * Each clip is searched by a KEYWORD tied to the country (config `footage_query`,
    e.g. Japan -> "Japan cherry blossoms Mount Fuji cinematic") so the footage is
    on-theme. One clip's query is always steered toward an aerial/drone shot for a
    strong establishing angle in the mix (config `footage.include_drone_shot`).
  * It prefers proper HD stock footage and only falls back to archive.org / a generated
    animation when no stock API key is configured or a search comes back empty.

Sources are tried in config `footage.source_order` (default pexels -> pixabay -> archive),
using whichever API keys are present. All are free:
    PEXELS_API_KEY    https://www.pexels.com/api/  (free, keyworded HD portrait video)
    PIXABAY_API_KEY   https://pixabay.com/api/docs/ (free, keyworded HD video)
    (archive.org needs no key; NASA public-domain space/Earth footage, on-tone fallback)

Output: build/footage.mp4   (concatenated montage; the assemble stage crops/loops it)
        build/footage.json  (per-clip source + query + url, for attribution logging)

Usage:
    python scripts/fetch_footage.py
    python scripts/fetch_footage.py --query "Japan cherry blossoms Mount Fuji cinematic"
    python scripts/fetch_footage.py --source archive
"""
import argparse
import datetime
import glob
import json
import os
import random
import shutil
import subprocess
import sys

import requests

HEADERS = {"User-Agent": "yt-shorts-generator/1.0 (personal pipeline)"}

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
PEXELS_PHOTO_SEARCH = "https://api.pexels.com/v1/search"
PIXABAY_PHOTO_SEARCH = "https://pixabay.com/api/"
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA = "https://archive.org/metadata/{identifier}"
ARCHIVE_DOWNLOAD = "https://archive.org/download/{identifier}/{filename}"

ARCHIVE_VIDEO_EXT = (".mp4", ".ogv", ".mpeg", ".mpg", ".mov", ".m4v")


def _download(url, dest, headers=None):
    with requests.get(url, headers=headers or HEADERS, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as out:
            for chunk in r.iter_content(chunk_size=1 << 20):
                out.write(chunk)


def _rotate(seq, key=0):
    """Deterministic day-based pick so each run varies but is stable within a day."""
    if not seq:
        return None
    day = datetime.date.today().timetuple().tm_yday
    return seq[(day + key) % len(seq)]


# --------------------------------------------------------------------------- Pexels
def fetch_pexels(query, dest, want_portrait, min_height):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    params = {
        "query": query,
        "orientation": "portrait" if want_portrait else "landscape",
        "size": "medium",
        "per_page": 40,
    }
    r = requests.get(PEXELS_SEARCH, params=params,
                     headers={"Authorization": key, **HEADERS}, timeout=60)
    r.raise_for_status()
    videos = r.json().get("videos", [])
    if not videos:
        return None
    # pick a RANDOM clip from the results so repeated runs don't reuse the same video
    video = random.choice(videos)

    # pick the highest-resolution portrait-ish .mp4 file for this video
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("link")]
    if not files:
        return None
    if want_portrait:
        portrait = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 0)]
        files = portrait or files
    files = [f for f in files if (f.get("height") or 0) >= min_height] or files
    best = max(files, key=lambda f: (f.get("height") or 0) * (f.get("width") or 0))

    _download(best["link"], dest)
    return {
        "source": "pexels",
        "query": query,
        "source_url": best["link"],
        "attribution": f"Pexels — {video.get('user', {}).get('name', 'unknown')} "
                       f"({video.get('url', '')})",
        "resolution": f"{best.get('width')}x{best.get('height')}",
        "license": "Pexels License (free commercial use, no attribution required)",
    }


# -------------------------------------------------------------------------- Pixabay
def _pixabay_raise_clean(resp):
    """Pixabay only accepts its key as a URL query param, so requests' own
    raise_for_status() embeds the key-bearing URL in the exception message — and the
    caller logs failures to stderr (public CI logs on a public repo). Re-raise HTTP
    errors as a status-code-only message so the key never reaches the log."""
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        raise RuntimeError(f"Pixabay request failed: HTTP {resp.status_code}") from None


def fetch_pixabay(query, dest, min_height):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    params = {"key": key, "q": query, "per_page": 40, "safesearch": "true"}
    r = requests.get(PIXABAY_SEARCH, params=params, headers=HEADERS, timeout=60)
    _pixabay_raise_clean(r)
    hits = r.json().get("hits", [])
    if not hits:
        return None
    hit = random.choice(hits)

    # Pixabay gives named renditions; prefer the largest that still meets min_height.
    renditions = hit.get("videos", {})
    chosen = None
    for name in ("large", "medium", "small", "tiny"):
        v = renditions.get(name)
        if v and v.get("url"):
            chosen = v
            if (v.get("height") or 0) >= min_height:
                break
    if not chosen:
        return None

    _download(chosen["url"], dest)
    return {
        "source": "pixabay",
        "query": query,
        "source_url": chosen["url"],
        "attribution": f"Pixabay — {hit.get('user', 'unknown')} "
                       f"(https://pixabay.com/videos/id-{hit.get('id')}/)",
        "resolution": f"{chosen.get('width')}x{chosen.get('height')}",
        "license": "Pixabay Content License (free use)",
    }


# --------------------------------------------------------------------- Pexels photos
def fetch_pexels_photo(query, dest, want_portrait, min_height):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    params = {"query": query, "orientation": "portrait" if want_portrait else "landscape",
              "per_page": 40}
    r = requests.get(PEXELS_PHOTO_SEARCH, params=params,
                     headers={"Authorization": key, **HEADERS}, timeout=60)
    r.raise_for_status()
    photos = r.json().get("photos", [])
    if not photos:
        return None
    photo = random.choice(photos)
    src = photo.get("src", {})
    url = src.get("portrait") or src.get("large2x") or src.get("original")
    if not url:
        return None
    _download(url, dest)
    return {
        "source": "pexels_photo", "type": "photo", "query": query, "source_url": url,
        "attribution": f"Pexels — {photo.get('photographer', 'unknown')}",
        "resolution": f"{photo.get('width')}x{photo.get('height')}",
        "license": "Pexels License (free commercial use, no attribution required)",
    }


# -------------------------------------------------------------------- Pixabay photos
def fetch_pixabay_photo(query, dest, min_height):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    params = {"key": key, "q": query, "image_type": "photo", "orientation": "vertical",
              "per_page": 40, "safesearch": "true"}
    r = requests.get(PIXABAY_PHOTO_SEARCH, params=params, headers=HEADERS, timeout=60)
    _pixabay_raise_clean(r)
    hits = r.json().get("hits", [])
    if not hits:
        return None
    hit = random.choice(hits)
    url = hit.get("largeImageURL") or hit.get("webformatURL")
    if not url:
        return None
    _download(url, dest)
    return {
        "source": "pixabay_photo", "type": "photo", "query": query, "source_url": url,
        "attribution": f"Pixabay — {hit.get('user', 'unknown')}",
        "resolution": f"{hit.get('imageWidth')}x{hit.get('imageHeight')}",
        "license": "Pixabay Content License (free use)",
    }


# ------------------------------------------------------------------------ archive.org
def _archive_search(collection, rows=50):
    params = {
        "q": f"collection:{collection} AND mediatype:movies",
        "fl[]": "identifier",
        "rows": rows,
        "output": "json",
        "sort[]": "downloads desc",
    }
    r = requests.get(ARCHIVE_SEARCH, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    docs = r.json()["response"]["docs"]
    return [d["identifier"] for d in docs if d.get("identifier")]


def _archive_pick_file(identifier, ac):
    """Return (filename, height) of the BEST (highest-resolution) usable file, or None.

    The old code picked the SMALLEST file to save CI bandwidth, which guaranteed the
    worst-quality derivative. We now prefer the highest resolution within the duration
    bounds (bandwidth is a non-issue for a once-daily job)."""
    r = requests.get(ARCHIVE_METADATA.format(identifier=identifier),
                     headers=HEADERS, timeout=60)
    r.raise_for_status()
    files = r.json().get("files", [])

    candidates = []
    for f in files:
        name = f.get("name", "")
        if not name.lower().endswith(ARCHIVE_VIDEO_EXT):
            continue
        try:
            seconds = float(f["length"]) if f.get("length") is not None else None
        except (TypeError, ValueError):
            seconds = None
        if seconds is not None and not (
                ac["min_source_seconds"] <= seconds <= ac["max_source_seconds"]):
            continue
        try:
            height = int(f.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        ext_rank = next(i for i, e in enumerate(ARCHIVE_VIDEO_EXT)
                        if name.lower().endswith(e))
        candidates.append((height, -ext_rank, name))

    if not candidates:
        return None
    # highest resolution first, then best container
    candidates.sort(reverse=True)
    height, _, name = candidates[0]
    return name, height


def fetch_archive(query, dest, cfg):
    ac = cfg["archive_collections"]
    allowlist = ac["allowlist"]
    collection = _rotate(allowlist) or allowlist[0]
    identifiers = _archive_search(collection)
    if not identifiers:
        return None

    day = datetime.date.today().timetuple().tm_yday
    for offset in range(len(identifiers)):
        identifier = identifiers[(day + offset) % len(identifiers)]
        picked = _archive_pick_file(identifier, ac)
        if not picked:
            continue
        filename, height = picked
        url = ARCHIVE_DOWNLOAD.format(identifier=identifier, filename=filename)
        _download(url, dest)
        return {
            "source": "archive",
            "query": query,
            "collection": collection,
            "identifier": identifier,
            "source_url": url,
            "archive_item": f"https://archive.org/details/{identifier}",
            "resolution": f"?x{height}" if height else "unknown",
            "license": "Public Domain (archive.org allowlisted collection)",
        }
    return None


# --------------------------------------------------------------------- generated animation
def fetch_animate(query, dest, cfg, seed_str):
    """Render an on-tone animated gradient background. This never needs the network and
    can't come back empty, so it's the guaranteed last-resort source — far better than
    dropping a random, irrelevant clip in when stock search finds nothing."""
    from generate_animation import render_animation, pick_palette  # local: only when used

    video = cfg.get("video", {})
    width = video.get("width", 1080)
    height = video.get("height", 1920)
    # short seamless loop; the assemble stage loops it to the voiceover length
    duration = cfg.get("footage", {}).get("animation_seconds", 14)
    palette = pick_palette(cfg, seed_str)
    render_animation(dest, width, height, duration, palette,
                     seed=sum(ord(c) for c in seed_str) % 256)
    return {
        "source": "animate",
        "query": query,
        "palette": palette,
        "resolution": f"{width}x{height}",
        "license": "Generated animation (ffmpeg gradient) — original content",
    }


# --------------------------------------------------------------------------- driver
def read_script(args):
    """The stage-1 output (id, name, footage_query, ...), or {} if not found."""
    script_path = os.path.join(args.out, "script.json")
    if os.path.exists(script_path):
        with open(script_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def choose_query(args, cfg, script):
    if args.query:
        return args.query
    if script.get("footage_query"):
        return script["footage_query"]
    fq = cfg.get("footage", {}).get("fallback_queries")
    return random.choice(fq) if fq else "calm nature cinematic"


def fetch_one_piece(query, out_dir, idx, cfg, order, want_portrait, min_height, seed_str):
    """Fetch one media piece (video OR photo) for a narration beat. Tries video and photo
    sources so the montage mixes both; the preference alternates by index so photos and
    videos interleave for a livelier, piece-by-piece feel. Returns (info, path) or
    (None, None). info carries a 'type' of 'video' or 'photo'."""
    video_sources = [s for s in order if s in ("pexels", "pixabay", "archive")]
    photo_sources = ["pexels_photo", "pixabay_photo"]
    # alternate which medium we try first, so the final montage interleaves video + photo
    if idx % 2 == 1:
        cascade = photo_sources + video_sources + ["animate"]
    else:
        cascade = video_sources + photo_sources + ["animate"]

    for source in cascade:
        is_photo = source.endswith("_photo")
        dest = os.path.join(out_dir, f"footage_clip{idx}." + ("jpg" if is_photo else "mp4"))
        try:
            if source == "pexels":
                info = fetch_pexels(query, dest, want_portrait, min_height)
            elif source == "pixabay":
                info = fetch_pixabay(query, dest, min_height)
            elif source == "archive":
                info = fetch_archive(query, dest, cfg)
            elif source == "pexels_photo":
                info = fetch_pexels_photo(query, dest, want_portrait, min_height)
            elif source == "pixabay_photo":
                info = fetch_pixabay_photo(query, dest, min_height)
            elif source == "animate":
                info = fetch_animate(query, dest, cfg, seed_str)
            else:
                info = None
        except Exception as e:  # noqa: BLE001 — try the next source, don't fail the run
            print(f"[fetch_footage] {source} failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            info = None
        if info and os.path.exists(dest) and os.path.getsize(dest) > 0:
            info.setdefault("type", "photo" if is_photo else "video")
            return info, dest
    return None, None


def segment_queries(script, cfg):
    """One footage search query per narration segment, so each clip matches what's being
    said while it plays. Falls back to the country's base query for any segment missing a
    visual, and to a synthetic single-segment list if the script has no segments."""
    base = script.get("footage_query") or "travel landscape cinematic"
    segs = script.get("segments") or []
    if segs:
        return [(s.get("visual") or base) for s in segs]
    # No segments (old-style script.json): fetch a few generic on-theme clips.
    fallback = cfg.get("footage", {}).get("fallback_queries", [])
    extras = fallback[:2] if fallback else []
    return [base] + extras


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--query", default=None,
                    help="Override: fetch a single clip for this query instead of one "
                         "clip per narration segment.")
    ap.add_argument("--source", default=None,
                    choices=["pexels", "pixabay", "archive", "animate"],
                    help="Force a single source instead of the configured order.")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    fcfg = cfg.get("footage", {})
    want_portrait = fcfg.get("orientation", "portrait") == "portrait"
    min_height = fcfg.get("min_height", 720)
    order = [args.source] if args.source else fcfg.get(
        "source_order", ["pexels", "pixabay", "animate"])

    script = read_script(args)
    country_name = script.get("name", "")
    base_query = choose_query(args, cfg, script)
    if args.query:
        queries = [args.query]
    else:
        queries = segment_queries(script, cfg)

    os.makedirs(args.out, exist_ok=True)
    print(f"[fetch_footage] {len(queries)} pieces (video+photo mix), sources={order}")

    # Clean any stale pieces from a previous run so the assemble stage never picks them up.
    for pat in ("footage_clip*.mp4", "footage_clip*.jpg"):
        for old in glob.glob(os.path.join(args.out, pat)):
            os.remove(old)

    clip_infos = []
    last_good = None  # (path, type) to reuse if a segment finds nothing at all
    for i, query in enumerate(queries):
        seed_str = f"{country_name or query}-{i}"
        # the segment's own query first, then the country base query as a safety net
        info, path = fetch_one_piece(query, args.out, i, cfg, order, want_portrait,
                                     min_height, seed_str)
        if not info and query != base_query:
            print(f"[fetch_footage] segment {i}: {query!r} empty, retrying base query")
            info, path = fetch_one_piece(base_query, args.out, i, cfg, order, want_portrait,
                                         min_height, seed_str)
        if info:
            info["query"] = query
            info["path"] = os.path.basename(path)
            clip_infos.append(info)
            last_good = (path, info.get("type", "video"))
        elif last_good:
            # reuse the previous piece so this beat still has a visual
            src_path, src_type = last_good
            ext = "jpg" if src_type == "photo" else "mp4"
            dup = os.path.join(args.out, f"footage_clip{i}.{ext}")
            shutil.copyfile(src_path, dup)
            clip_infos.append({"source": "reuse", "type": src_type, "query": query,
                               "path": os.path.basename(dup)})

    if not clip_infos:
        print("ERROR: no footage could be fetched from any source.", file=sys.stderr)
        sys.exit(1)

    n_photo = sum(1 for c in clip_infos if c.get("type") == "photo")
    n_video = len(clip_infos) - n_photo
    manifest = {
        "clip_count": len(clip_infos),
        "clips": clip_infos,
        # top-level fields kept for backward-compat with the workflow's history-log step
        "source": "+".join(sorted({c["source"] for c in clip_infos})),
        "identifier": clip_infos[0].get("identifier", clip_infos[0].get("source", "unknown")),
    }
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"[fetch_footage] saved {len(clip_infos)} pieces "
          f"({n_video} video, {n_photo} photo)")


if __name__ == "__main__":
    main()
