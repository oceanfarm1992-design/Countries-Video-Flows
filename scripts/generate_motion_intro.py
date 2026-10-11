#!/usr/bin/env python3
"""
Motion intro for the rotating series (country, hook, trending, geography, worlddata):
a drop-in replacement for generate_intro.py's globe zoom, with the same inputs,
output (build/intro.mp4, silent, 1080x1920) and the same rule for sizing the
intro to the spoken intro line so assemble_video.py's crossfade still lines up.

The camera sweeps from the whole world across a tilted 3D-style map to the
country, which rises out of the map as a lit slab (tiny countries get a light
beam); then a title card drops in with the flag and the country name.

Usage:
    python scripts/generate_motion_intro.py --name Japan --lat 36.2 --lon 138.25 --iso jp \
        --out build/intro.mp4 --config config/countries.json
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw

import motion_kit as mk
from generate_intro import MIN_INTRO_SECONDS, fetch_flag
from pipeline_common import INTRO_XFADE_SECONDS, ffprobe_duration, speech_end_time

W, H = 1080, 1920
PLANE = [(-60, 170), (1140, 170), (2350, 2350), (-1270, 2350)]
FOCUS = (540, 860)
TITLE_Y = 1230


def world_shot(lon, lat):
    """A wide shot centred on the country's longitude, so the sweep is a dolly-in,
    not a sideways pan across half the planet."""
    x = (lon + 180.0) / 360.0 * mk.SOURCE_W
    y = (90.0 - lat) / 180.0 * mk.SOURCE_H
    return (x - 700, y + 250, 6400)


def make_scene(p):
    cam = mk.MapCamera((W, H), PLANE, FOCUS)
    has_mask = bool(p["iso"]) and cam.has_mask(p["iso"])
    if has_mask:
        target = cam.shot_for(p["iso"], pad=2.6, min_w=1000)
    else:  # no outline (shouldn't happen for the 196 countries): fly to the coordinates
        x = (p["lon"] + 180.0) / 360.0 * mk.SOURCE_W
        y = (90.0 - p["lat"]) / 180.0 * mk.SOURCE_H
        target = (x, y, 1400)
    small = has_mask and cam.is_small(p["iso"], target)
    start = world_shot(p["lon"], p["lat"])
    flag = None
    if p["flag"] and os.path.exists(p["flag"]):
        f = Image.open(p["flag"]).convert("RGBA")
        flag = f.resize((300, max(1, int(300 * f.height / f.width))), Image.LANCZOS)
    d_total = p["duration"]
    fly_end = max(1.2, d_total * 0.62)
    cache = {}

    def scene(t):
        if "full" in cache and t > d_total - 0.05:
            return cache["full"].copy()
        k = mk.phase(t, 0.0, fly_end)
        shot = mk.MapCamera.fly(start, target, k)
        amount = mk.phase(t, fly_end - 0.25, 0.7)
        lifts = [(p["iso"], amount, mk.LAND_LIT)] if has_mask and not small else []
        beams = [(p["iso"], amount, mk.ACCENT)] if (small or not has_mask) and p["iso"] else []
        frame = cam.render(shot, lifts=lifts, beams=beams)

        c = mk.ease_out(mk.phase(t, fly_end - 0.1, 0.55))
        if c > 0:
            dy = int(220 * (1 - c))
            box = (90, TITLE_Y + dy, W - 90, TITLE_Y + 460 + dy)
            mk.shadowed_panel(frame, box)
            if flag is not None:
                frame.alpha_composite(mk.with_alpha(flag, c), (int(W / 2 - flag.width / 2), box[1] + 50))
            n = mk.ease_back(mk.phase(t, fly_end + 0.15, 0.5))
            if n > 0:
                nf = mk.fit_font(p["name"].upper(), int(96 * min(1.0, n)) + 1, 40, box[2] - box[0] - 80, 900)
                y = box[1] + (335 if flag is not None else 220)
                mk.draw_text(frame, (W / 2, y), p["name"].upper(), nf, mk.TEXT + (255,), anchor="mm")
                ImageDraw.Draw(frame).rounded_rectangle(
                    (W / 2 - 90 * n, y + 62, W / 2 + 90 * n, y + 70), 4, fill=mk.ACCENT + (255,))
        if t > d_total - 0.05:
            cache["full"] = frame.copy()
        return frame
    return scene


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--iso", default=None)
    ap.add_argument("--flag", default=None)
    ap.add_argument("--out", default="build/intro.mp4")
    ap.add_argument("--duration", type=float, default=3.0)
    ap.add_argument("--intro-voice", default="build/intro_voice.wav")
    ap.add_argument("--xfade", type=float, default=INTRO_XFADE_SECONDS)
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--map", default=None, help="Ignored (kept so the old intro's command line still works).")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()

    if args.intro_voice and os.path.exists(args.intro_voice):
        iv_dur = speech_end_time(args.intro_voice, ffprobe_duration(args.intro_voice))
        # identical to generate_intro.py: assemble_video.py's crossfade depends on it
        args.duration = max(MIN_INTRO_SECONDS, iv_dur + 0.3 + 0.15 + args.xfade)
        print(f"[motion_intro] intro voice ends at {iv_dur:.2f}s -> intro {args.duration:.2f}s")
    else:
        # Only without a spoken line: with one, the length must stay exactly as above or
        # the montage video (and its captions) would start later than its audio.
        args.duration = max(args.duration, 2.6)

    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    flag = args.flag
    if not flag and args.iso:
        flag = os.path.join(out_dir, f"flag_{args.iso.lower()}.png")
        try:
            fetch_flag(args.iso, flag)
        except Exception as exc:  # noqa: BLE001 -- the intro still works without a flag
            print(f"[motion_intro] flag fetch failed ({exc}); continuing without flag", file=sys.stderr)
            flag = None
    payload = {"name": args.name, "lat": args.lat, "lon": args.lon,
               "iso": (args.iso or "").upper(), "flag": flag, "duration": args.duration}
    mk.render_clips([(args.out, args.duration, (payload,))], (W, H), make_scene, args.processes)
    print(f"[motion_intro] wrote {args.out} ({args.duration:.2f}s) for {args.name}")


if __name__ == "__main__":
    main()
