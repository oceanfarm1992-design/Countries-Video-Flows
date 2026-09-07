#!/usr/bin/env python3
"""
Build the opening "globe zoom" intro: a Google-Earth-style rotate-and-zoom across a
realistic Blue Marble globe that settles on the country, with its flag and name.

How the globe motion works: ffmpeg's v360 filter reprojects an equirectangular Earth
texture into an orthographic ("og") view — i.e. the flat map rendered as a 3D globe seen
from space — for a given yaw (longitude), pitch (latitude) and field of view (zoom).
v360's own timeline/sendcmd animation proved unreliable on a looping still, so instead we
render the motion FRAME BY FRAME: each frame is a static v360 call with that frame's
yaw/pitch/fov (eased so it decelerates into the country), then the frames are stitched and
the flag + country name are composited on top.

Assets:
    --map   equirectangular Blue Marble jpg (assets/map/bluemarble.jpg; NASA, public domain)
    --flag  the country's flag png (fetched from flagcdn by ISO2 code, or passed in)

Output: build/intro.mp4  (silent; the assemble stage puts it before the montage)

Usage:
    python scripts/generate_intro.py --name Japan --lat 36.2 --lon 138.25 --iso jp
    python scripts/generate_intro.py --name Japan --lat 36.2 --lon 138.25 \
        --flag build/flag_jp.png --out build/intro.mp4 --duration 3.0
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import requests

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}


def _find_font(candidates):
    for path in candidates:
        if os.path.exists(path):
            if ":" in path:  # Windows drive-letter path breaks ffmpeg's filtergraph parser
                cache = ".font_cache"
                os.makedirs(cache, exist_ok=True)
                dst = os.path.join(cache, os.path.basename(path))
                if not os.path.exists(dst):
                    shutil.copyfile(path, dst)
                return dst
            return path
    return candidates[0]


FONT_BOLD = _find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
])


def fontfile_escape(path):
    return path.replace("\\", "/").replace(":", "\\:")


def drawtext_escape(text):
    return (text.replace("\\", "\\\\").replace(":", "\\:")
                .replace("'", "\\'").replace("%", "\\%"))


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def fetch_flag(iso2, dest):
    """Download the country's flag png from flagcdn (free, no key)."""
    url = f"https://flagcdn.com/w1280/{iso2.lower()}.png"
    with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest


def render_globe_frames(map_path, frames_dir, n, lon, lat, size,
                        start_fov=165.0, end_fov=58.0, approach_deg=55.0):
    """Render each globe frame as a static v360 orthographic view, eased so the camera
    sweeps in from the west and decelerates onto the country."""
    os.makedirs(frames_dir, exist_ok=True)
    start_yaw = lon - approach_deg
    start_pitch = max(-80.0, min(80.0, lat * 0.3))
    for i in range(n):
        p = i / (n - 1) if n > 1 else 1.0
        e = ease_out_cubic(p)
        yaw = start_yaw + (lon - start_yaw) * e
        pitch = start_pitch + (lat - start_pitch) * e
        fov = start_fov + (end_fov - start_fov) * e
        out = os.path.join(frames_dir, f"f{i:04d}.png")
        vf = (f"v360=e:og:yaw={yaw:.3f}:pitch={pitch:.3f}:"
              f"h_fov={fov:.3f}:v_fov={fov:.3f}:w={size}:h={size}")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", map_path,
               "-vf", vf, "-frames:v", "1", "-update", "1", out]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-1500:])
            raise SystemExit(f"globe frame {i} failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Country name to display.")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--iso", default=None, help="ISO2 code to fetch the flag (e.g. jp).")
    ap.add_argument("--flag", default=None, help="Local flag png (skips fetch).")
    ap.add_argument("--map", default="assets/map/bluemarble.jpg")
    ap.add_argument("--out", default="build/intro.mp4")
    ap.add_argument("--duration", type=float, default=3.0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--config", default="config/countries.json")
    args = ap.parse_args()

    if not os.path.exists(args.map):
        print(f"[generate_intro] map texture missing: {args.map} — skipping intro.",
              file=sys.stderr)
        sys.exit(0)  # optional stage; don't break the pipeline

    width, height = 1080, 1920
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            v = json.load(f).get("video", {})
        width, height = v.get("width", 1080), v.get("height", 1920)

    build_dir = os.path.dirname(args.out) or "."
    os.makedirs(build_dir, exist_ok=True)

    # flag
    flag_path = args.flag
    if not flag_path and args.iso:
        flag_path = os.path.join(build_dir, f"flag_{args.iso.lower()}.png")
        try:
            fetch_flag(args.iso, flag_path)
        except Exception as e:  # noqa: BLE001
            print(f"[generate_intro] flag fetch failed ({e}); continuing without flag")
            flag_path = None
    if flag_path and not os.path.exists(flag_path):
        flag_path = None

    n = max(2, int(args.duration * args.fps))
    frames_dir = os.path.join(build_dir, "introframes")
    print(f"[generate_intro] rendering {n} globe frames -> {args.name} "
          f"(lat {args.lat}, lon {args.lon})")
    render_globe_frames(args.map, frames_dir, n, args.lon, args.lat, size=width)

    # Compose: dark background, globe filling the upper frame, a flag PIN planted on the
    # country (pole + flag + marker dot) that drops in as the globe settles, and the
    # country name shown throughout the rotation.
    font = fontfile_escape(FONT_BOLD)
    name_e = drawtext_escape(args.name.upper())
    globe_y = 120
    globe_cx = width // 2
    country_y = globe_y + width // 2          # globe is scaled to width x width; centre = country
    pin_show = max(0.0, args.duration - 1.2)   # pin appears as the zoom settles
    flag_w = 132
    flag_h = 88
    pole_top = country_y - 150
    flag_top = pole_top - flag_h

    inputs = ["-framerate", str(args.fps), "-i", os.path.join(frames_dir, "f%04d.png")]
    if flag_path:
        inputs += ["-loop", "1", "-i", flag_path]

    fc = [
        f"color=c=0x0a0e1a:s={width}x{height}:d={args.duration}[bg]",
        f"[0:v]scale={width}:{width}[globe]",
        f"[bg][globe]overlay=(W-w)/2:{globe_y}[v0]",
    ]
    last = "v0"
    # marker dot (filled circle) at the country point
    fc.append(
        f"color=c=0xE23636:s=30x30:d={args.duration},format=rgba,"
        "geq=r=226:g=54:b=54:a='if(lte((X-15)*(X-15)+(Y-15)*(Y-15),190),255,0)'[dot]"
    )
    # pole from the country point up to the flag
    fc.append(
        f"[{last}]drawbox=x={globe_cx}-2:y={pole_top}:w=4:h={country_y - pole_top}:"
        f"color=white@0.9:t=fill:enable='gte(t,{pin_show:.2f})'[vp]"
    )
    last = "vp"
    if flag_path:
        fc.append(f"[1:v]scale={flag_w}:{flag_h}[flag]")
        fc.append(
            f"[{last}][flag]overlay={globe_cx}-{flag_w // 2}:{flag_top}:"
            f"enable='gte(t,{pin_show:.2f})'[vf]"
        )
        last = "vf"
    fc.append(
        f"[{last}][dot]overlay={globe_cx}-15:{country_y}-15:"
        f"enable='gte(t,{pin_show:.2f})'[vd]"
    )
    last = "vd"
    # country name banner, shown for the whole intro (announced during the rotation)
    fc.append(
        f"[{last}]drawtext=fontfile={font}:text='{name_e}':"
        "fontcolor=white:fontsize=110:borderw=5:bordercolor=black@0.85:"
        f"x=(w-text_w)/2:y={height - 380}[vout]"
    )
    filter_complex = ";".join(fc)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex, "-map", "[vout]",
        "-t", f"{args.duration}", "-r", str(args.fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.out,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-2000:])
        raise SystemExit("intro compose failed")
    print(f"[generate_intro] wrote {args.out}")


if __name__ == "__main__":
    main()
