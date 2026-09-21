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
import time

import requests

from pipeline_common import INTRO_XFADE_SECONDS, ffprobe_duration, speech_end_time

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}

# Floor so a very short intro line (e.g. a one-syllable country name) still gets
# a legible globe rotation before the pin/flag drop-in and the crossfade — see
# pin_show below, which assumes at least ~1.2s of runway before it appears.
MIN_INTRO_SECONDS = 1.8


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
    # Swap the ASCII apostrophe for a typographic one — a literal ' inside our
    # single-quoted -filter_complex argument corrupts the filtergraph (see the same
    # note in assemble_video.py). Country names like "Côte d'Ivoire" need this.
    return (text.replace("\\", "\\\\").replace("'", "’")
                .replace(":", "\\:").replace("%", "\\%"))


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def _name_fontsize(name):
    """Scale the country-name font so it always fits within the 1080px frame width.
    At DejaVu Bold the average char is roughly 0.58× the point size wide."""
    n = len(name)
    if n <= 10: return 110
    if n <= 15: return 90
    if n <= 22: return 72
    return 60   # e.g. "Democratic Republic of the Congo" (32 chars) at 60pt ≈ 1114px — use wrap


def wrap_drawtext(text, max_chars):
    """Word-wrap `text` and return a drawtext-safe string.
    Lines are joined with an actual newline character — confirmed on a real render
    that the two-character "\\n" escape gets its backslash silently dropped by
    ffmpeg's filtergraph parser, leaving a bare "n" instead of a line break (see
    the same fix and evidence in assemble_video.py's wrap_drawtext)."""
    words = text.split()
    lines, current, count = [], [], 0
    for w in words:
        space = 1 if current else 0
        if current and count + space + len(w) > max_chars:
            lines.append(" ".join(current))
            current, count = [w], len(w)
        else:
            current.append(w)
            count += space + len(w)
    if current:
        lines.append(" ".join(current))
    return "\n".join(drawtext_escape(line) for line in lines)


def fetch_flag(iso2, dest, attempts=2):
    """Download the country's flag png from flagcdn (free, no key).

    Retries once on failure (a brief 5xx/timeout blip) before raising -- every
    other network call in this pipeline is retried or has a fallback
    (fetch_country_stats, fetch_rankings_stats); this was the one unguarded
    call, and callers that render several flags per video (comparison: 2,
    rankings: up to 8) had a correspondingly larger chance of a single
    transient failure aborting the whole render step."""
    url = f"https://flagcdn.com/w1280/{iso2.lower()}.png"
    last_exc = None
    for attempt in range(attempts):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            return dest
        except Exception as exc:  # noqa: BLE001 -- network call, many transient failure modes
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(1.5)
    raise last_exc


def wrap_yaw(deg):
    """Wrap a yaw angle into ffmpeg v360's valid [-180, 180] range. Longitude is periodic
    (yaw = -227 and yaw = 133 point at the same spot on the globe), but v360 rejects
    anything outside that range outright. Without this, any country whose approach sweep
    (lon - approach_deg) crosses the antimeridian — e.g. Samoa at lon=-172.1, start_yaw=
    -227.1 — crashes ffmpeg on frame 0, generate_intro.py exits non-zero, and (since that
    workflow step is continue-on-error) the video silently ships with no globe intro."""
    return ((deg + 180) % 360) - 180


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
        yaw = wrap_yaw(start_yaw + (lon - start_yaw) * e)
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
    ap.add_argument("--duration", type=float, default=3.0,
                    help="Used only when --intro-voice doesn't exist. Otherwise the "
                         "globe's length is derived from the intro voiceover's actual "
                         "speech duration (see below) so the video crossfade lines up "
                         "with when assemble_video.py starts the main narration.")
    ap.add_argument("--intro-voice", default="build/intro_voice.wav",
                    help="The rendered intro voiceover (generate_tts.py's --intro-out). "
                         "When present, --duration is overridden to intro_speech_length + "
                         "0.45s + --xfade, matching assemble_video.py's prepend_intro() "
                         "voice_delay_ms exactly (0.3s lead-in + speech + 0.15s breath) so "
                         "the two scripts' independently-computed timings can't diverge.")
    ap.add_argument("--xfade", type=float, default=INTRO_XFADE_SECONDS,
                    help="Must match assemble_video.py's --intro-xfade (same shared "
                         "constant by default) — used here only to size the video so its "
                         "crossfade point lands on the real speech-end time.")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--config", default="config/countries.json")
    args = ap.parse_args()

    if not os.path.exists(args.map):
        print(f"[generate_intro] map texture missing: {args.map} — skipping intro.",
              file=sys.stderr)
        sys.exit(0)  # optional stage; don't break the pipeline

    if args.intro_voice and os.path.exists(args.intro_voice):
        raw_iv_dur = ffprobe_duration(args.intro_voice)
        iv_dur = speech_end_time(args.intro_voice, raw_iv_dur)
        # Must equal assemble_video.py's voice_delay_ms/1000 minus xfade, since
        # voffset = duration - xfade is what actually drives the video crossfade
        # there. 0.3s lead-in + speech + 0.15s breath, same as voice_delay_ms.
        args.duration = max(MIN_INTRO_SECONDS, iv_dur + 0.3 + 0.15 + args.xfade)
        print(f"[generate_intro] intro voice speech ends at {iv_dur:.2f}s "
              f"(raw file {raw_iv_dur:.2f}s) -> sizing globe intro to "
              f"{args.duration:.2f}s so video and audio handoff align")

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
    name_upper = args.name.upper()
    name_fontsize = _name_fontsize(name_upper)
    # Wrap names longer than 22 chars at 20 chars/line so they don't exceed the frame.
    # "Democratic Republic of the Congo" (32 chars) → 2 lines, each well within 1080px.
    if len(name_upper) > 22:
        name_e = wrap_drawtext(name_upper, 20)
    else:
        name_e = drawtext_escape(name_upper)
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
    # Country name banner — adaptive fontsize keeps even long names inside the frame;
    # wrapping splits names >22 chars onto 2 lines.  y is lifted slightly (height-420)
    # to give a 2-line block room before the bottom edge.
    fc.append(
        f"[{last}]drawtext=fontfile={font}:text='{name_e}':"
        f"fontcolor=white:fontsize={name_fontsize}:line_spacing=6:"
        "borderw=4:bordercolor=black@0.85:"
        f"x=(w-text_w)/2:y={height - 420}[vout]"
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
