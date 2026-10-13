#!/usr/bin/env python3
"""
Backdrop for the long-form video's opening and closing tables: the tilted 3D world
map with every ranked country painted in its flag, under a slow camera drift.
Replaces a generic stock globe clip that barely moved -- the opening sat on what
looked like a frozen frame for ~40 s while the intro was read.

Rendered like the geography Shorts: the 3D scene once as a 2x still, then a cheap
crop-and-scale per frame. The clip is rendered a little longer than the spoken
estimate for its segment so the assembler never has to loop it (a loop would snap
the camera back).
"""
import math
import os

from PIL import Image

import motion_kit as mk

W, H = 1920, 1080
SS = 2
PLANE = [(-120, 150), (2040, 150), (3700, 1650), (-1780, 1650)]
FOCUS = (960, 600)
PUSH = 1.25          # gentle: this sits behind a full table of text
PAN = 0.22           # share of the slack width the camera travels sideways


def render_still(isos, flag_paths, out_path, single=False):
    """2x still of the map framed on the ranked countries, each in its flag. single:
    framed on the one country (a chapter that found no footage)."""
    cam = mk.MapCamera((W * SS, H * SS), [(x * SS, y * SS) for x, y in PLANE],
                       (FOCUS[0] * SS, FOCUS[1] * SS), tex=1150 * SS, ui=SS)
    isos = [i for i in isos if cam.has_mask(i)]
    if not isos:
        raise RuntimeError("no ranked country has a map mask")
    # Frame the bulk of the list, not its outliers: one Iceland or Bhutan among
    # twenty African countries otherwise drags the view north into empty sky.
    shots = [cam.shot_for(i) for i in isos]
    xs, ys = sorted(s[0] for s in shots), sorted(s[1] for s in shots)
    lo, hi = len(isos) // 6, len(isos) - 1 - len(isos) // 6
    span = max(xs[hi] - xs[lo], (ys[hi] - ys[lo]) * 1.6)
    shot = ((xs[lo] + xs[hi]) / 2, (ys[lo] + ys[hi]) / 2, min(6400.0, max(2600.0, span * 1.7)))
    if single:
        shot = cam.shot_for(isos[0], pad=3.4, min_w=1400)
    tints = []
    for iso in isos:
        flag = mk.load_flag(flag_paths.get(iso))
        tints.append((iso, mk.LAND_LIT, 0.9, flag) if flag is not None else (iso, mk.LAND_LIT, 0.6))
    cam.render(shot, base=cam.bake(tints)).convert("RGB").save(out_path)


_STILL = {}


def make_scene(still_path, total):
    """Worker-side factory: a slow push-in with a sideways pan across the still."""
    if still_path not in _STILL:
        _STILL[still_path] = Image.open(still_path).convert("RGB")
    still = _STILL[still_path]

    def scene(t):
        k = mk.ease_io(min(1.0, t / total))
        z = 1 + (PUSH - 1) * k
        cw, ch = W * SS / z, H * SS / z
        # The ranked countries sit at FOCUS; keep them there while closing in, and
        # pan slowly across them.
        slack = still.width - cw
        left = FOCUS[0] * SS - FOCUS[0] / W * cw + slack * PAN * (k - 0.5) + 18 * SS * math.sin(t * 0.21)
        top = FOCUS[1] * SS - FOCUS[1] / H * ch
        left = min(max(0.0, left), slack)
        top = min(max(0.0, top), still.height - ch)
        return still.resize((W, H), Image.BILINEAR, box=(left, top, left + cw, top + ch))
    return scene


def render_backdrop(isos, flag_paths, dest, seconds, still_path, single=False):
    """Write `dest` (mp4, `seconds` long). Returns True on success; the caller keeps
    its stock-footage fallback otherwise."""
    try:
        if not os.path.exists(still_path):
            render_still(isos, flag_paths, still_path, single)
        mk.render_clips([(dest, seconds, (still_path, seconds))], (W, H), make_scene)
        return True
    except (SystemExit, Exception) as exc:  # noqa: BLE001 -- decoration; stock footage is the fallback
        print(f"[longform_map_backdrop] map backdrop failed ({exc}); using stock footage")
        return False
