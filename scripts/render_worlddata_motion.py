#!/usr/bin/env python3
"""
Motion visuals for the worlddata "flags" sub-type (replaces a static flag with a
slow zoom): the flag waves on a pole with cloth lighting rippling across it, a
soft light sweeps the background, and each segment's callout ("Adopted 1881")
animates in as a pill under the flag. The wave runs continuously across scene
cuts (each clip renders its own slice of one timeline).

Exits with code 2 for the "stats" sub-type or if the flag can't be fetched, so
the workflow falls back to render_worlddata_graphics.py.

Layout stays clear of the hook card (top, first 4 s), the caption band
(y ~1100-1360) and the end CTA (bottom, last 4 s).

Output: build/footage_clip{i}.mp4, build/footage.json

Usage:
    python scripts/render_worlddata_motion.py --config config/countries.json
"""
import argparse
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import motion_kit as mk
from generate_intro import fetch_flag
from pipeline_common import segment_durations
from render_geography_map import resolve_duration

W, H = 1080, 1920
FLAG_MAX = (860, 560)  # tall flags (Nepal) scale down instead of covering the callout
FLAG_TOP = 360
PILL_Y = 1000
TAIL_PAD = 0.5


def _background():
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(-1, 1, W)[None, :]
    glow = np.exp(-((x * 1.1) ** 2 + ((y - 0.33) * 2.2) ** 2))
    base = np.stack([8 + 22 * glow + 6 * y, 12 + 30 * glow + 14 * y, 26 + 46 * glow + 28 * y], -1)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")


class WavingFlag:
    """Column-wise vertical displacement + shading -- cheap enough per frame."""

    def __init__(self, flag_path):
        f = Image.open(flag_path).convert("RGBA")  # keep transparency (Nepal isn't a rectangle)
        scale = min(FLAG_MAX[0] / f.width, FLAG_MAX[1] / f.height)
        self.w, self.h = int(f.width * scale), int(f.height * scale)
        self.still = f.resize((self.w, self.h), Image.LANCZOS)
        self.img = np.asarray(self.still).astype(np.float32)
        self.pad = 40
        self.x = np.arange(self.w)
        self.reach = (self.x / self.w) ** 1.3  # pinned at the pole, freest at the fly end

    def frame(self, t):
        phase = 2 * math.pi * (self.x / 420.0 - 0.55 * t)
        dy = 22 * self.reach * np.sin(phase) + 6 * self.reach * np.sin(2.3 * phase + 1.1)
        shade = 1 + 0.16 * self.reach * np.cos(phase)
        rows = np.arange(self.h + 2 * self.pad)[:, None] - self.pad - dy[None, :]
        valid = (rows >= 0) & (rows < self.h)
        src = np.clip(rows, 0, self.h - 1).astype(np.int32)
        px = self.img[src, self.x[None, :]]
        out = np.zeros((self.h + 2 * self.pad, self.w, 4), np.uint8)
        out[..., :3] = np.clip(px[..., :3] * shade[None, :, None], 0, 255).astype(np.uint8)
        out[..., 3] = (px[..., 3] * valid).astype(np.uint8)
        return Image.fromarray(out, "RGBA")


def make_scene(p):
    bg = _background()
    flag = WavingFlag(p["flag"])
    pole_x = (W - flag.w) // 2 - 18
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shape = Image.new("RGBA", flag.still.size, (0, 0, 0, 150))
    shape.putalpha(flag.still.getchannel("A").point(lambda v: int(v * 0.6)))
    shadow.alpha_composite(shape, (pole_x + 44, FLAG_TOP + 30))  # the flag's own shape, offset
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    pill_y = max(PILL_Y, FLAG_TOP + flag.h + 80)

    def scene(t):
        g = p["t0"] + t                       # global time: the wave continues across cuts
        frame = bg.copy()
        sweep = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sx = int((g * 140) % (W + 900)) - 450
        ImageDraw.Draw(sweep).polygon([(sx, 0), (sx + 220, 0), (sx - 380, H), (sx - 600, H)], fill=(255, 255, 255, 14))
        frame.alpha_composite(sweep.filter(ImageFilter.GaussianBlur(40)))
        intro = mk.ease_out(mk.phase(g, 0.0, 0.8))
        frame.alpha_composite(shadow)
        d = ImageDraw.Draw(frame)
        d.rounded_rectangle((pole_x, FLAG_TOP - 60, pole_x + 14, H + 20), 7, fill=(196, 170, 112, 255))
        d.ellipse((pole_x - 9, FLAG_TOP - 84, pole_x + 23, FLAG_TOP - 52), fill=mk.ACCENT + (255,))
        cloth = flag.frame(g)
        if intro < 1:  # first appearance: the flag unfurls from the pole
            cw = max(2, int(cloth.width * intro))
            cloth = cloth.crop((0, 0, cw, cloth.height))
        frame.alpha_composite(cloth, (pole_x + 14, FLAG_TOP - flag.pad))
        label = p["label"]
        if label:
            k = mk.ease_out(mk.phase(t, 0.15, 0.5))
            if k > 0:
                f = mk.fit_font(label.upper(), 50, 28, W - 220, 800)
                tw = mk.text_w(label.upper(), f)
                box = (W / 2 - tw / 2 - 44, pill_y + 40 * (1 - k), W / 2 + tw / 2 + 44, pill_y + 92 + 40 * (1 - k))
                layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle(box, 46, fill=(14, 20, 38, int(230 * k)))
                ld.rounded_rectangle((box[0], box[1], box[0] + 12, box[3]), 6, fill=mk.ACCENT + (int(255 * k),))
                frame.alpha_composite(layer)
                mk.draw_text(frame, (W / 2 + 6, (box[1] + box[3]) / 2), label.upper(), f, mk.TEXT + (int(255 * k),),
                             anchor="mm")
        return frame
    return scene


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    if script.get("subtype", "stats") != "flags":
        print("[render_worlddata_motion] stats sub-type -- using the map renderer")
        sys.exit(2)
    with open(args.config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]
    iso2 = (script.get("iso2") or "").lower()
    os.makedirs(args.out, exist_ok=True)
    flag_path = os.path.join(args.out, f"_wd_motion_flag_{iso2}.png")
    try:
        fetch_flag(iso2, flag_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[render_worlddata_motion] flag fetch failed ({exc}) -- using the map renderer")
        sys.exit(2)

    segments = script["segments"]
    duration, src = resolve_duration(script, video_cfg, args.audio)
    durs = segment_durations(script, duration)
    jobs, clips, t0 = [], [], 0.0
    for i, (seg, dur) in enumerate(zip(segments, durs)):
        out = os.path.join(args.out, f"footage_clip{i}.mp4")
        jobs.append((out, dur + TAIL_PAD, ({"flag": flag_path, "label": (seg.get("visual") or "").strip(),
                                           "t0": t0},)))
        clips.append({"source": "worlddata_flag_motion", "query": seg.get("visual", ""),
                      "source_url": f"https://flagcdn.com/w1280/{iso2}.png", "attribution": "Flag image: flagcdn.com",
                      "resolution": f"{W}x{H}", "license": "Public domain / free-use national flag image",
                      "type": "video", "path": os.path.basename(out)})
        t0 += dur
    print(f"[render_worlddata_motion] {len(segments)} segments, {duration:.1f}s ({src})")
    mk.render_clips(jobs, (W, H), make_scene, args.processes)
    os.remove(flag_path)
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump({"clip_count": len(clips), "clips": clips, "source": "worlddata_flags_motion",
                   "identifier": iso2.upper()}, fh, indent=2)
    print(f"[render_worlddata_motion] wrote {len(clips)} clips")


if __name__ == "__main__":
    main()
