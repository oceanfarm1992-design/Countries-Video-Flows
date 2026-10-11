#!/usr/bin/env python3
"""
Motion version of the comparison Short visuals (replaces the still table PNGs).

  intro  - split screen: two map panels, each camera flying from the world to its
           own country, which rises out of the tilted map in its colour (A green,
           B coral; tiny countries get a light beam); flags drop in, VS pops on the
           divider. Split panels keep both countries large however far apart they
           are -- one shared view had to zoom out until they were dots.
  stat   - a card slides in: two bars race while both values count up to the exact
           published figures, the winner glows with a badge, and the running
           scoreboard ticks over. Non-comparable stats (e.g. population) are shown
           without a winner, exactly as the narration treats them.
  outro  - verdict card with the final tally

Same scoring rule as before (and as the narration): only comparable stats count,
higher wins. Layout keeps clear of the burned-in caption band (y ~1100-1360) and
the end CTA (bottom, last 4 s). Also writes build/sfx.wav.

Output: build/footage_clip{i}.mp4, build/footage.json, build/sfx.wav

Usage:
    python scripts/render_comparison_motion.py --video-config config/comparison_video.json
"""
import argparse
import json
import os

from PIL import Image, ImageDraw

import motion_kit as mk
from fetch_country_stats import format_value
from pipeline_common import segment_durations
from render_geography_map import resolve_duration
from render_rankings_graphics import _fetch_flag_resilient

W, H = 1080, 1920
PANEL_W, PANEL_H, PANEL_Y = 540, 690, 30
PANEL_PLANE = [(-30, 70), (570, 70), (1175, 1250), (-635, 1250)]
PANEL_FOCUS = (270, 470)
CARD = (60, 740, 1020, 1075)
SCORE = (170, 1395, 910, 1520)
COL_A, COL_B = mk.LAND_LIT, mk.RIVAL
TAIL_PAD = 0.5
WORLD_W = 6400

_CAMS = []


def _cameras():
    """Left and right panel cameras sharing one loaded map, once per worker."""
    if not _CAMS:
        left = mk.MapCamera((PANEL_W, PANEL_H), PANEL_PLANE, PANEL_FOCUS)
        _CAMS.extend([left, mk.MapCamera((PANEL_W, PANEL_H), PANEL_PLANE, PANEL_FOCUS, share=left)])
    return _CAMS


def winner(row):
    """Same rule as the narration (generate_comparison_script._winner): comparable
    stats only, higher wins, and figures that display identically are a tie."""
    if not row["comparable"] or format_value(row["unit"], row["value_a"]) == format_value(row["unit"], row["value_b"]):
        return None
    if row["value_a"] == row["value_b"]:
        return None
    return "a" if row["value_a"] > row["value_b"] else "b"


def _flag(path, width):
    if not path or not os.path.exists(path):
        return Image.new("RGBA", (width, int(width * 0.66)), (40, 46, 66, 255))
    f = Image.open(path).convert("RGBA")
    return f.resize((width, max(1, int(width * f.height / f.width))), Image.LANCZOS)


def _header(frame, t, p, flags, start=0.0):
    """Flags + names over each panel and a VS badge on the divider."""
    k = mk.ease_out(mk.phase(t, start + 0.15, 0.55))
    for side, (flag, name, col) in enumerate(((flags[0], p["a"]["name"], COL_A), (flags[1], p["b"]["name"], COL_B))):
        cx = PANEL_W / 2 + side * PANEL_W
        dy = int((1 - k) * -260)
        frame.alpha_composite(mk.with_alpha(flag, k), (int(cx - flag.width / 2), 70 + dy))
        d = ImageDraw.Draw(frame)
        d.rounded_rectangle((cx - flag.width / 2, 70 + flag.height + 8 + dy, cx + flag.width / 2,
                             70 + flag.height + 14 + dy), 3, fill=col + (int(255 * k),))
        nf = mk.fit_font(name, 50, 26, PANEL_W - 60, 800)
        mk.draw_text(frame, (cx, 70 + flag.height + 52 + dy), name, nf, mk.TEXT + (int(255 * k),), anchor="mm")
    s = mk.ease_back(mk.phase(t, start + 0.55, 0.45))
    if s > 0:
        d = ImageDraw.Draw(frame)
        r = 60 * s
        cy = PANEL_Y + PANEL_H * 0.55
        d.ellipse((W / 2 - r, cy - r, W / 2 + r, cy + r), fill=mk.ACCENT + (255,), outline=(14, 20, 38, 255), width=6)
        d.text((W / 2, cy), "VS", font=mk.font(int(52 * s) + 1, 900), fill=(14, 20, 38, 255), anchor="mm")


def _panels(cams, p, t_a, t_b, lift_a, lift_b, marks, world):
    """Both map panels side by side on a sky background, with a soft divider."""
    frame = cams[0].bg.resize((W, H)).copy() if cams[0].bg.size != (W, H) else cams[0].bg.copy()
    for side, (cam, (iso2, col, small, target), k, amount) in enumerate(
            zip(cams, marks, (t_a, t_b), (lift_a, lift_b))):
        if iso2 is None:
            view = world
            lifts = beams = ()
        else:
            view = mk.MapCamera.fly(world, target, k)
            lifts = [] if small else [(iso2, amount, col)]
            beams = [(iso2, amount, col)] if small else []
        panel = cam.render(view, lifts=lifts, beams=beams)
        frame.alpha_composite(panel, (side * PANEL_W, PANEL_Y))
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(14):  # divider shadow
        d.line([(W / 2 - 7 + i, PANEL_Y), (W / 2 - 7 + i, PANEL_Y + PANEL_H)], fill=(4, 8, 18, int(160 * (1 - abs(i - 7) / 7))))
    d.line([(W / 2, PANEL_Y), (W / 2, PANEL_Y + PANEL_H)], fill=mk.ACCENT + (200,), width=3)
    for y in range(40):  # fade the panels' bottom edge into the background
        d.line([(0, PANEL_Y + PANEL_H - 40 + y), (W, PANEL_Y + PANEL_H - 40 + y)], fill=mk.SKY_BOT + (int(255 * y / 40),))
    frame.alpha_composite(layer)
    return frame


def _bar_card(frame, t, p, row, flags):
    k = mk.ease_out(mk.phase(t, 0.0, 0.45))
    if k <= 0:
        return
    dx = int(700 * (1 - k))
    box = (CARD[0] + dx, CARD[1], CARD[2] + dx, CARD[3])
    mk.shadowed_panel(frame, box)
    mk.draw_text(frame, ((box[0] + box[2]) / 2, box[1] + 50), row["label"].upper(), mk.font(40, 800),
                 mk.TEXT + (255,), anchor="mm")
    c = mk.ease_out(mk.phase(t, 0.4, 1.4))
    top = max(row["value_a"], row["value_b"]) or 1
    win = winner(row)
    glow = mk.ease_out(mk.phase(t, 1.9, 0.4)) if win else 0
    d = ImageDraw.Draw(frame)
    for i, (key, col, flag) in enumerate((("a", COL_A, flags[2]), ("b", COL_B, flags[3]))):
        y = box[1] + 105 + i * 100
        frame.alpha_composite(flag, (box[0] + 40, int(y)))
        x0, x1 = box[0] + 40 + flag.width + 20, box[2] - 40
        d = ImageDraw.Draw(frame)
        d.rounded_rectangle((x0, y + 8, x1, y + 52), 12, fill=(32, 40, 62, 255))
        frac = max(0.0, row[f"value_{key}"] / top) if top > 0 else 0
        if c > 0:
            bar_end = x0 + max(24, (x1 - x0) * frac * c)
            lit = win == key and glow > 0
            fill = tuple(min(255, int(v * (1 + 0.25 * glow))) for v in col) if lit else col
            d.rounded_rectangle((x0, y + 8, bar_end, y + 52), 12, fill=fill + (255,))
            value = format_value(row["unit"], row[f"value_{key}"] * c) if c < 1 else \
                format_value(row["unit"], row[f"value_{key}"])
            vf = mk.font(34, 800)
            tw = mk.text_w(value, vf)
            inside = bar_end + 14 + tw > x1 - 6   # no room after the bar: write it inside, right-aligned
            tx = bar_end - tw - 14 if inside else bar_end + 14
            d.text((tx, y + 30), value, font=vf, anchor="lm",
                   fill=(14, 20, 38, 255) if inside else mk.TEXT + (255,))
        if win == key and glow > 0:
            s = mk.ease_back(mk.phase(t, 1.9, 0.4))
            r = 26 * s
            bx, by = box[0] + 40 + flag.width - 4, y + 2
            d.ellipse((bx - r, by - r, bx + r, by + r), fill=mk.ACCENT + (255,))
            d.text((bx, by), "▲", font=mk.font(int(24 * s) + 1, 900), fill=(14, 20, 38, 255), anchor="mm")
    years = sorted({row["year_a"], row["year_b"]})
    note = f"World Bank, {years[0]}" if len(years) == 1 else f"World Bank, {years[0]}-{years[1]}"
    if not row["comparable"]:
        note += "  ·  not scored"
    mk.draw_text(frame, (box[0] + 40, box[3] - 40), note, mk.font(26, 500), mk.TEXT_DIM + (255,),
                 anchor="lm", shadow=0)


def _scoreboard(frame, t, p, score, bump_at=None):
    mk.shadowed_panel(frame, SCORE, radius=28, accent=False)
    s = 1 + 0.18 * (mk.ease_back(mk.phase(t, bump_at, 0.35)) - mk.ease_out(mk.phase(t, bump_at + 0.35, 0.4))) \
        if bump_at is not None else 1
    cy = (SCORE[1] + SCORE[3]) / 2
    d = ImageDraw.Draw(frame)
    for side, (name, col) in enumerate(((p["a"]["name"], COL_A), (p["b"]["name"], COL_B))):
        x = SCORE[0] + 40 if side == 0 else SCORE[2] - 40
        d.ellipse((x - 10, cy - 10, x + 10, cy + 10), fill=col + (255,))
        nf = mk.fit_font(name.upper(), 30, 18, 230, 700)
        d.text((x + 24 if side == 0 else x - 24, cy), name.upper(), font=nf, anchor="lm" if side == 0 else "rm",
               fill=mk.TEXT_DIM + (255,))
    d.text((W / 2, cy), f"{score[0]}  –  {score[1]}", font=mk.font(int(56 * s), 900), anchor="mm",
           fill=mk.TEXT + (255,))


def _verdict(frame, t, p, score):
    k = mk.ease_out(mk.phase(t, 0.3, 0.5))
    if k <= 0:
        return
    dy = int(260 * (1 - k))
    box = (CARD[0], CARD[1] + dy, CARD[2], CARD[3] + dy)
    mk.shadowed_panel(frame, box)
    mk.draw_text(frame, (W / 2, box[1] + 55), "THE VERDICT", mk.font(36, 800), mk.ACCENT + (255,), anchor="mm")
    s = mk.ease_back(mk.phase(t, 0.6, 0.5))
    if score[0] == score[1]:
        line, col = f"IT'S A TIE  {score[0]}–{score[1]}", mk.TEXT
    else:
        lead = p["a"] if score[0] > score[1] else p["b"]
        col = COL_A if score[0] > score[1] else COL_B
        line = f"{lead['name'].upper()} LEADS {max(score)}–{min(score)}"
    if s > 0:
        f = mk.fit_font(line, int(70 * s) + 1, 30, box[2] - box[0] - 80, 900)
        mk.draw_text(frame, (W / 2, box[1] + 175), line, f, col + (255,), anchor="mm")


def make_scene(kind, p):
    cams = _cameras()
    cache = {}
    flags = (_flag(p["flags"][0], 220), _flag(p["flags"][1], 220), _flag(p["flags"][0], 72), _flag(p["flags"][1], 72))
    world = (mk.SOURCE_W / 2, mk.SOURCE_H * 0.42, WORLD_W)
    marks = []
    for cam, iso2, col in ((cams[0], p["a"]["iso2"], COL_A), (cams[1], p["b"]["iso2"], COL_B)):
        if cam.has_mask(iso2):
            target = cam.shot_for(iso2, pad=2.6, min_w=1000)
            marks.append((iso2, col, cam.is_small(iso2, target), target))
        else:
            marks.append((None, col, False, world))

    if kind == "intro":
        def scene(t):
            if "full" in cache and t > 2.6:
                return cache["full"].copy()
            frame = _panels(cams, p, mk.phase(t, 0.2, 1.4), mk.phase(t, 0.4, 1.4),
                            mk.phase(t, 1.4, 0.75), mk.phase(t, 1.6, 0.75), marks, world)
            _header(frame, t, p, flags)
            sub = mk.ease_out(mk.phase(t, 1.2, 0.5))
            if sub > 0:
                f = mk.fit_font(p["subtitle"], 58, 30, W - 160, 900)
                mk.draw_text(frame, (W / 2, 900 + 30 * (1 - sub)), p["subtitle"], f,
                             mk.ACCENT + (int(255 * sub),), anchor="mm")
                mk.draw_text(frame, (W / 2, 985 + 30 * (1 - sub)), p["tagline"], mk.font(34, 600),
                             mk.TEXT_DIM + (int(255 * sub),), anchor="mm", shadow=0.5)
            if t > 2.6:
                cache["full"] = frame.copy()
            return frame
        return scene

    def static_frame():
        if "base" not in cache:  # panels rendered once, slightly oversized, then drifted cheaply
            frame = _panels(cams, p, 1.0, 1.0, 1.0, 1.0, marks, world)
            _header(frame, 10.0, p, flags)
            cache["base"] = frame.resize((int(W * 1.04), int(H * 1.04)), Image.BILINEAR)
        return cache["base"]

    def drifting(t, dur):
        big = static_frame()
        x = int((big.width - W) * mk.clamp(t / max(1.0, dur)))
        y = (big.height - H) // 2
        return big.crop((x, y, x + W, y + H))

    if kind == "row":
        row = p["row"]
        # Short beats compress the timeline so the count-up always lands on the real
        # value before the assembler cuts (it settles at 2.4 s at normal speed).
        speed = 1 / min(1.0, max(0.3, (p["dur"] - 0.3) / 2.4))

        def scene(t):
            tt = t * speed
            frame = drifting(t, p["dur"])
            _bar_card(frame, tt, p, row, flags)
            _scoreboard(frame, tt, p, p["score_after"] if tt >= 2.0 else p["score_before"],
                        bump_at=2.0 if p["score_after"] != p["score_before"] else None)
            return frame
        return scene

    if kind == "hold":  # unexpected extra beat: keep the scoreboard, never the verdict
        def scene(t):
            frame = drifting(t, p["dur"])
            _scoreboard(frame, 10.0, p, p["score_after"])
            return frame
        return scene

    def scene(t):  # outro
        frame = drifting(t, p["dur"])
        _verdict(frame, t, p, p["score_after"])
        return frame
    return scene


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--video-config", default="config/comparison_video.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.video_config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]
    segments, rows = script["segments"], script["rows"]
    a, b = script["country_a"], script["country_b"]
    duration, src = resolve_duration(script, video_cfg, args.audio)
    durs = segment_durations(script, duration)
    print(f"[render_comparison_motion] {a['name']} vs {b['name']}: {len(segments)} beats, {duration:.1f}s ({src})")
    os.makedirs(args.out, exist_ok=True)

    flags = []
    for c in (a, b):
        path = os.path.join(args.out, f"_cmp_motion_flag_{c['iso2'].lower()}.png")
        flags.append(path if _fetch_flag_resilient(c["iso2"], path) else None)
    hook = script.get("hook", "")
    subtitle = hook.split(":", 1)[1].strip() if ":" in hook else "HEAD TO HEAD"
    base = {"a": {"name": a["name"], "iso2": a["iso2"]}, "b": {"name": b["name"], "iso2": b["iso2"]},
            "flags": flags, "subtitle": subtitle.upper(),
            "tagline": f"{len(rows)} STATS COMPARED  ·  WORLD BANK DATA"}
    by_label = {r["label"].strip().lower(): r for r in rows}
    scored = set()

    jobs, clips, events, start, score = [], [], [], 0.0, [0, 0]
    last = len(segments) - 1
    for i, (seg, dur) in enumerate(zip(segments, durs)):
        label = (seg.get("visual") or "").strip()
        row = by_label.get(label.lower())
        if row is not None and row["label"] in scored:
            row = None  # a repeated label must never count a win twice
        if row is not None:
            scored.add(row["label"])
        payload = dict(base, dur=dur)
        if row is not None:
            before = tuple(score)
            w = winner(row)
            if w:
                score[0 if w == "a" else 1] += 1
            payload.update(kind="row", row=row, score_before=before, score_after=tuple(score))
            kind = "row"
            events += [(start + 0.05, mk.sfx_whoosh(0.6))]
            if w:
                events += [(start + 1.9, mk.sfx_ding()), (start + 2.0, mk.sfx_pop())]
        elif i == 0:
            kind = "intro"
            events += [(0.15, mk.sfx_whoosh()), (0.6, mk.sfx_pop()), (1.35, mk.sfx_pop())]
        else:
            kind = "outro" if i == last else "hold"
            payload.update(score_before=tuple(score), score_after=tuple(score))
            if kind == "outro":
                events += [(start + 0.6, mk.sfx_impact())]
        jobs.append((os.path.join(args.out, f"footage_clip{i}.mp4"), dur + TAIL_PAD, (kind, payload)))
        clips.append({"source": "comparison_motion", "query": label, "source_url": "",
                      "attribution": "Rendered motion graphics (data: World Bank Open Data)",
                      "resolution": f"{W}x{H}", "license": "Generated content -- original render",
                      "type": "video", "path": f"footage_clip{i}.mp4"})
        start += dur

    mk.render_clips(jobs, (W, H), make_scene, args.processes)
    mk.write_sfx_track(os.path.join(args.out, "sfx.wav"), duration, events)
    for path in flags:
        if path and os.path.exists(path):
            os.remove(path)
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump({"clip_count": len(clips), "clips": clips, "source": "comparison_motion",
                   "identifier": f"{a['iso2']}_{b['iso2']}"}, fh, indent=2)
    print(f"[render_comparison_motion] wrote {len(clips)} clips + sfx.wav (score {score[0]}-{score[1]})")


if __name__ == "__main__":
    main()
