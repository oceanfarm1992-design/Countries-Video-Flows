#!/usr/bin/env python3
"""
Generate a clean animated background with ffmpeg when no suitable stock/archive clip
is available.

The footage fetcher (scripts/fetch_footage.py) tries Pexels -> Pixabay for a theme-matched
clip. When those find nothing (no API key, or no good match), the old behaviour fell back
to random archive.org film that was off-tone and low-res. Instead, this module renders a
calm, cinematic animated gradient — abstract, always on-theme for a reflective quote, and
never "irrelevant". It needs no network and no assets, so it can never fail: it's the
guaranteed last-resort source.

The look: a slowly drifting multi-colour gradient (ffmpeg `gradients` source) with a soft
vignette and a touch of film grain (`noise`) for texture. A palette is picked
deterministically from the quote so the same quote always gets the same mood.

Output: build/footage.mp4  (1080x1920, the same file the assemble stage consumes)

Usage:
    python scripts/generate_animation.py
    python scripts/generate_animation.py --out build/footage.mp4 --duration 62
    python scripts/generate_animation.py --palette 0d1b2a,1b263b,415a77
"""
import argparse
import json
import os
import subprocess
import sys

# Calm, dark, cinematic palettes (hex, no leading #). Each suits reflective/stoic content.
DEFAULT_PALETTES = [
    ["0d1b2a", "1b263b", "415a77"],   # deep ocean blue
    ["1a1423", "3d2c2e", "6b4226"],   # warm dusk / amber
    ["0b1d16", "14342b", "2d6a4f"],   # forest night green
    ["10002b", "240046", "5a189a"],   # deep royal purple
    ["1c2431", "2c3e50", "557a95"],   # slate dawn
]


def pick_palette(cfg, seed_str):
    """Deterministically choose a palette from a seed string (e.g. the quote id)."""
    palettes = cfg.get("footage", {}).get("animation_palettes") or DEFAULT_PALETTES
    idx = (sum(ord(c) for c in seed_str) % len(palettes)) if seed_str else 0
    return palettes[idx]


def render_animation(dest, width, height, duration, palette, seed=0):
    """Render the animated gradient to `dest`. Returns the ffmpeg color spec used.

    `duration` is rendered as exactly ONE gradient cycle (the `gradients` filter's own
    `duration` = its animation period), so the last frame lines up with the first. That
    lets the assemble stage loop this short clip (-stream_loop) to any voiceover length
    with no visible seam — which is why we can render ~14s instead of the full video
    length and keep CI fast."""
    colors = ":".join(f"c{i}=0x{c}" for i, c in enumerate(palette))
    # gradients animates the gradient line over time; a low speed gives a slow, calm drift.
    src = (
        f"gradients=s={width}x{height}:{colors}:nb_colors={len(palette)}"
        f":x0={width // 4}:y0={height // 4}:x1={width * 3 // 4}:y1={height * 3 // 4}"
        f":speed=0.008:duration={duration}:rate=30:seed={seed}"
    )
    # soft vignette for depth + light STATIC grain to avoid banding. The grain must not
    # be temporal (no allf=t): per-frame random noise is incompressible and ballooned the
    # 30s output to ~110MB. Static grain keeps the anti-banding texture at ~1/20th the size.
    vf = "format=yuv420p,vignette=PI/5,noise=alls=3,format=yuv420p"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", src,
        "-t", f"{duration}",
        "-vf", vf,
        # veryfast: the source is a smooth synthetic gradient, so it compresses cleanly
        # even at a fast preset — no visible quality loss, ~3-4x quicker than medium.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        dest,
    ]
    print(f"[generate_animation] rendering {width}x{height} {duration}s "
          f"palette={palette}")
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-2000:])
        raise SystemExit(f"ffmpeg animation render failed ({proc.returncode})")
    return colors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/footage.mp4")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--duration", type=float, default=None,
                    help="Seconds to render (default: config video.max_seconds).")
    ap.add_argument("--palette", default=None,
                    help="Comma-separated hex colours, e.g. 0d1b2a,1b263b,415a77.")
    args = ap.parse_args()

    cfg = {}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    video = cfg.get("video", {})
    width = video.get("width", 1080)
    height = video.get("height", 1920)
    # Default to a short loop cycle (assemble loops it seamlessly), not the full length.
    duration = args.duration or cfg.get("footage", {}).get("animation_seconds", 14)

    seed_str = ""
    if os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as f:
            seed_str = json.load(f).get("id", "")

    if args.palette:
        palette = [c.strip().lstrip("#") for c in args.palette.split(",") if c.strip()]
    else:
        palette = pick_palette(cfg, seed_str)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    render_animation(args.out, width, height, duration, palette,
                     seed=sum(ord(c) for c in seed_str) % 256)
    print(f"[generate_animation] wrote {args.out}")


if __name__ == "__main__":
    main()
