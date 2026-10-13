#!/usr/bin/env python3
"""
Geography series map footage, motion edition (stands in for render_geography_map.py,
which stays as the fallback). Same inputs and the same build/footage.json output,
so the assembler is unchanged.

  - the tilted 3D world map, the country raised as a slab wearing its flag (too
    small to raise: a flagpole flying it)
  - ONE continuous camera dolly toward the country across every map beat, so cuts
    between beats never snap the camera back
  - each beat's map label (the feature being talked about: "Adamawa Plateau")
    drops in as a styled label card with the country's flag
  - up to MAX_CUTAWAYS real clips of filmable features, exactly as before

Cheap on GitHub's runners: the 3D scene is rendered ONCE as a 2x still, and the
dolly is a crop-and-scale of that still per frame (no per-frame map warp).

Usage:
    python scripts/render_geography_motion.py --config config/countries.json
"""
import argparse
import json
import math
import os

from PIL import Image

import motion_kit as mk
from pipeline_common import segment_durations
from render_geography_map import fetch_cutaway_clips, resolve_duration
from render_rankings_graphics import _fetch_flag_resilient

W, H = 1080, 1920
SS = 2                     # still supersampling: the dolly crops into it, so it stays sharp
PLANE = [(-60, 170), (1140, 170), (2350, 2350), (-1270, 2350)]
FOCUS = (540, 800)         # country between the label card and the caption band
START_PAD = 3.6            # framing at the start of the dolly (multiples of the country's size)
PUSH = 1.6                 # how far the dolly closes in over the whole video
DRIFT = 26                 # px of slow sideways drift, so the move never feels mechanical
TAIL_PAD = 0.5             # clips run a little past their beat; the assembler trims
LABEL_BOX = (90, 190, W - 90, 330)
HOOK_END = 4.0             # assemble_video.py burns the hook banner over the label's spot for
                           # the first 4 s of the body; the label card waits until it is gone


def render_still(iso2, flag_path, out_path):
    """The 3D scene at 2x, and the country's screen position in it."""
    cam = mk.MapCamera((W * SS, H * SS), [(x * SS, y * SS) for x, y in PLANE],
                       (FOCUS[0] * SS, FOCUS[1] * SS), tex=1150 * SS, ui=SS)
    if not cam.has_mask(iso2):
        raise SystemExit(f"no country mask for {iso2} -- falling back to the flat map")
    shot = cam.shot_for(iso2, pad=START_PAD, min_w=1000)
    flag = mk.load_flag(flag_path)
    if cam.is_small(iso2, shot):
        frame = cam.render(shot, beams=[(iso2, 1.0, mk.ACCENT, flag)])
    else:
        frame = cam.render(shot, lifts=[(iso2, 1.0, mk.LAND_LIT, flag)])
    frame.convert("RGB").save(out_path)
    return cam.project(iso2, shot)


_STILL = {}


def make_scene(kind, p):
    """Runs inside each render worker; returns scene(t) -> image."""
    if p["still"] not in _STILL:
        _STILL[p["still"]] = Image.open(p["still"]).convert("RGB")
    still = _STILL[p["still"]]
    flag = None
    if p.get("flag") and os.path.exists(p["flag"]):
        f = Image.open(p["flag"]).convert("RGBA")
        flag = f.resize((96, max(1, int(96 * f.height / f.width))), Image.LANCZOS)

    # A beat that ends too soon after the hook banner leaves skips its card rather
    # than flashing it for a few frames before the cut.
    shown = p["label"] and p["card_at"] + 0.6 < p["dur"]
    card = label_layer(p["label"], flag) if shown else None  # drawn once per beat

    def scene(t):
        k = min(1.0, (p["t0"] + t) / p["total"])
        frame = dolly(still, p["anchor"], k).convert("RGBA")
        a = mk.ease_out(mk.phase(t, p["card_at"], 0.45))
        if card is not None and a > 0:  # drops in from above while fading up
            dy = int(50 * (1 - a))
            frame.alpha_composite(mk.with_alpha(card.crop((0, dy, W, card.height)), a))
        return frame
    return scene


def dolly(still, anchor, k):
    """Crop of the 2x still at camera progress k (0..1): the country stays pinned at
    FOCUS while the view closes in on it, with a slow sideways drift."""
    z = 1 + (PUSH - 1) * mk.ease_io(k)
    cw, ch = W * SS / z, H * SS / z
    drift = DRIFT * SS * math.sin(k * math.pi * 1.5)
    left = anchor[0] - FOCUS[0] / W * cw + drift
    top = anchor[1] - FOCUS[1] / H * ch
    left = min(max(0.0, left), still.width - cw)
    top = min(max(0.0, top), still.height - ch)
    return still.resize((W, H), Image.BILINEAR, box=(left, top, left + cw, top + ch))


def label_layer(label, flag):
    """The beat's map label as a transparent top strip: a card with the country's
    flag and the feature name."""
    box = LABEL_BOX
    layer = Image.new("RGBA", (W, box[3] + 80), (0, 0, 0, 0))
    mk.shadowed_panel(layer, box, radius=28)
    x = box[0] + 40
    if flag is not None:
        layer.alpha_composite(flag, (x, int((box[1] + box[3]) / 2 - flag.height / 2)))
        x += flag.width + 28
    f = mk.fit_font(label.upper(), 60, 30, box[2] - 40 - x, 800)
    mk.draw_text(layer, (x, (box[1] + box[3]) / 2 + 4), label.upper(), f, mk.TEXT + (255,), anchor="lm")
    return layer


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    iso2 = (script.get("iso2") or "").upper()
    segments = script.get("segments") or []
    if not iso2 or not segments:
        raise SystemExit(f"{args.script} needs iso2 and segments")
    os.makedirs(args.out, exist_ok=True)

    duration, src = resolve_duration(script, cfg["video"], args.audio)
    durs = segment_durations(script, duration)
    print(f"[render_geography_motion] {iso2}: {len(segments)} beats, {duration:.1f}s (from {src})")

    flag_path = os.path.join(args.out, f"_geo_motion_flag_{iso2.lower()}.png")
    if not _fetch_flag_resilient(iso2, flag_path):
        flag_path = None
    still_path = os.path.join(args.out, "_geo_motion_still.png")
    anchor = render_still(iso2, flag_path, still_path)

    cutaways = fetch_cutaway_clips(segments, script.get("name", ""), cfg, args.out)
    map_indices = [i for i in range(len(segments)) if i not in cutaways]
    total = max(1.0, sum(durs[i] for i in map_indices))

    jobs, clips, t0, start = [], [], 0.0, 0.0
    for i, (seg, dur) in enumerate(zip(segments, durs)):
        start += durs[i - 1] if i else 0.0  # this beat's start in the body (cutaways included)
        if i in cutaways:
            clips.append(cutaways[i])
            continue
        out = os.path.join(args.out, f"footage_clip{i}.mp4")
        payload = {"still": still_path, "anchor": anchor, "t0": t0, "total": total, "dur": dur,
                   "card_at": max(0.1, HOOK_END + 0.15 - start),
                   "label": (seg.get("visual") or "").strip(), "flag": flag_path}
        jobs.append((out, dur + TAIL_PAD, ("map", payload)))
        clips.append({"source": "geography_motion", "query": seg.get("visual", ""), "source_url": "",
                      "attribution": "Rendered 3D map (Natural Earth boundaries, public domain)",
                      "resolution": f"{W}x{H}", "license": "Generated content -- original render",
                      "type": "video", "path": os.path.basename(out)})
        t0 += dur

    mk.render_clips(jobs, (W, H), make_scene, args.processes)

    for tmp in (still_path, flag_path):
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump({"clip_count": len(clips), "clips": clips, "source": "geography_motion",
                   "identifier": iso2}, fh, indent=2)
    print(f"[render_geography_motion] wrote {len(clips)} clips ({len(cutaways)} cutaways) for {iso2}")


if __name__ == "__main__":
    main()
