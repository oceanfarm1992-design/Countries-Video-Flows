#!/usr/bin/env python3
"""
Motion version of the rankings Short visuals (replaces the still leaderboard PNGs).

One animated clip per narration beat, timed to the same segment durations the
assembler uses:
  intro  - slow drift over the tilted world map, the 8 countries glowing in turn,
           title animating in below the hook text
  #N..#1 - camera flies from the previous country to this one, the country rises
           out of the map (tiny countries get a light beam), the stat card drops in
           with a rank pop, flag, a count-up that lands exactly on the published
           value, and a bar against #1; countdown progress along the bottom
  outro  - camera pulls back to the world, all 8 marked, top 3 slide in

Layout keeps clear of the overlays assemble_video.py burns in: the hook title
(top, first 4 s), the caption band (y ~1100-1360) and the end CTA (bottom, last
4 s). Also writes build/sfx.wav (whooshes, pops, dings, a riser and impact
before #1) aligned to the same timeline.

Output: build/footage_clip{i}.mp4, build/footage.json, build/sfx.wav

Usage:
    python scripts/render_rankings_motion.py --video-config config/rankings_video.json
"""
import argparse
import json
import os

from PIL import Image, ImageDraw

import motion_kit as mk
from fetch_rankings_stats import format_value, year_range_label
from generate_rankings_script import title_name
from pipeline_common import segment_durations
from render_geography_map import resolve_duration
from render_rankings_graphics import _fetch_flag_resilient

W, H = 1080, 1920
PLANE = [(-60, 170), (1140, 170), (2350, 2350), (-1270, 2350)]
FOCUS = (540, 880)   # country sits between the card (ends y=620) and the caption band
CARD = (60, 110, 1020, 620)
TAIL_PAD = 0.5            # clips run a little past their beat; the assembler trims
REVEALED_TINT = (240, 190, 60)


def _caption_band(frame):
    """Darken the caption zone slightly so burned-in captions keep their contrast."""
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(1040, 1420, 4):
        a = int(110 * (1 - abs((y - 1230) / 190)))
        d.rectangle((0, y, W, y + 4), fill=(4, 8, 18, max(0, a)))
    frame.alpha_composite(layer)


def _flag(path, width):
    if not path or not os.path.exists(path):
        return Image.new("RGBA", (width, int(width * 0.66)), (40, 46, 66, 255))
    f = Image.open(path).convert("RGBA")
    return f.resize((width, max(1, int(width * f.height / f.width))), Image.LANCZOS)


def _progress(frame, chips, current, t):
    """Countdown chips along the bottom: revealed ones lit, the current one pulsing."""
    n = len(chips)
    gap, r = 108, 34
    x0 = W / 2 - gap * (n - 1) / 2
    d = ImageDraw.Draw(frame)
    for i, chip in enumerate(chips):
        cx, cy = x0 + i * gap, 1760
        on = chip["revealed"]
        is_cur = chip["iso2"] == current
        rr = r * (1 + 0.12 * mk.ease_back(mk.phase(t, 1.4, 0.4))) if is_cur else r
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr),
                  fill=(mk.ACCENT if is_cur else (60, 74, 104) if on else (24, 30, 48)) + (255,),
                  outline=(mk.ACCENT + (255,)) if on else (70, 80, 110, 255), width=3)
        f = mk.font(26, 800)
        label = f"#{chip['rank']}"
        d.text((cx, cy), label, font=f, anchor="mm",
               fill=(14, 20, 38, 255) if is_cur else (mk.TEXT + (255,)) if on else (110, 120, 150, 255))


_CAM = []


def _camera():
    """One map camera per worker process, shared by every clip it renders."""
    if not _CAM:
        _CAM.append(mk.MapCamera((W, H), PLANE, FOCUS))
    return _CAM[0]


def make_scene(kind, p):
    """Runs inside each render worker; returns scene(t) -> RGBA frame."""
    cam = _camera()
    cache = {}

    def map_layer(t, shot, base, lifts=(), beams=(), static_after=None):
        if static_after is not None and t > static_after and "map" in cache:
            return cache["map"].copy()
        frame = cam.render(shot, base=base, lifts=lifts, beams=beams)
        if static_after is not None and t > static_after:
            cache["map"] = frame.copy()
        return frame

    if kind == "intro":
        isos = [r["iso2"] for r in p["rows"] if cam.has_mask(r["iso2"])]
        shots = [cam.shot_for(i) for i in isos]
        cx = sum(s[0] for s in shots) / len(shots) if shots else mk.SOURCE_W / 2
        cy = sum(s[1] for s in shots) / len(shots) if shots else mk.SOURCE_H / 2

        lit = cam.bake([(iso, REVEALED_TINT, 0.6) for iso in isos])

        def scene(t):
            shot = (cx - 250 + 500 * mk.clamp(t / max(1.0, p["dur"])), cy, 6400)
            glow = mk.ease_out(mk.phase(t, 0.3, 1.0))
            frame = map_layer(t, shot, lit)
            if glow < 1:  # countries light up: blend from the plain map
                frame = Image.blend(map_layer(t, shot, None), frame, glow)
            _caption_band(frame)
            s = mk.ease_back(mk.phase(t, 0.45, 0.55))
            if s > 0:
                mk.draw_text(frame, (W / 2, 620), f"TOP {len(p['rows'])}", mk.font(int(190 * s), 900),
                             mk.ACCENT + (255,), anchor="mm")
            a = mk.ease_out(mk.phase(t, 0.8, 0.5))
            if a > 0:
                f = mk.fit_font(p["title"], 76, 40, W - 140, 800)
                mk.draw_text(frame, (W / 2, 780 + 30 * (1 - a)), p["title"], f, mk.TEXT + (int(255 * a),),
                             anchor="mm")
            b = mk.ease_out(mk.phase(t, 1.15, 0.5))
            if b > 0:
                mk.draw_text(frame, (W / 2, 880), p["source"], mk.font(34, 500),
                             mk.TEXT_DIM + (int(255 * b),), anchor="mm", shadow=0.5)
            return frame
        return scene

    if kind == "row":
        row = p["row"]
        has_mask = cam.has_mask(row["iso2"])
        target = cam.shot_for(row["iso2"], pad=2.9) if has_mask else p["prev_shot"]
        small = has_mask and cam.is_small(row["iso2"], target)
        flag = _flag(p["flag"], 230)
        base = cam.bake([(iso, REVEALED_TINT, 0.35) for iso in p["revealed_before"] if cam.has_mask(iso)])

        def scene(t):
            if "full" in cache and t > 3.4:
                return cache["full"].copy()
            shot = mk.MapCamera.fly(p["prev_shot"], target, mk.phase(t, 0.0, 1.4))
            amount = mk.phase(t, 1.25, 0.75)
            lifts = [(row["iso2"], amount, mk.LAND_LIT)] if has_mask and not small else []
            beams = [(row["iso2"], amount, mk.ACCENT)] if small else []
            frame = map_layer(t, shot, base, lifts=lifts, beams=beams, static_after=2.05)
            _caption_band(frame)
            _card(frame, t, row, flag, p)
            _progress(frame, p["chips"], row["iso2"], t)
            if t > 3.4:
                cache["full"] = frame.copy()
            return frame
        return scene

    # outro (and the defensive "hold")
    rows = p["rows"]
    isos = [r["iso2"] for r in rows if cam.has_mask(r["iso2"])]
    wide = (sum(cam.shot_for(i)[0] for i in isos) / max(1, len(isos)),
            sum(cam.shot_for(i)[1] for i in isos) / max(1, len(isos)), 6800) if isos else p["prev_shot"]
    flags = {r["iso2"]: _flag(p["flags"].get(r["iso2"]), 120) for r in rows[:3]}
    lit = cam.bake([(i, REVEALED_TINT, 0.55) for i in isos])

    def scene(t):
        if "full" in cache and t > 2.6:
            return cache["full"].copy()
        shot = mk.MapCamera.fly(p["prev_shot"], wide, mk.phase(t, 0.0, 1.2))
        frame = map_layer(t, shot, lit, static_after=1.25)
        _caption_band(frame)
        if kind == "outro":
            _top3(frame, t, rows[:3], flags, p)
        if t > 2.6:
            cache["full"] = frame.copy()
        return frame
    return scene


def _card(frame, t, row, flag, p):
    k = mk.ease_out(mk.phase(t, 1.3, 0.5))
    if k <= 0:
        return
    dy = int(-540 * (1 - k))
    box = (CARD[0], CARD[1] + dy, CARD[2], CARD[3] + dy)
    mk.shadowed_panel(frame, box)
    s = mk.ease_back(mk.phase(t, 1.45, 0.4))
    if s > 0:
        rank = f"#{row['rank']}" + ("=" if row["tied"] else "")
        mk.draw_text(frame, (box[0] + 50, box[1] + 120), rank, mk.font(int(150 * s), 900),
                     mk.ACCENT + (255,), anchor="lm")
    f = mk.ease_out(mk.phase(t, 1.6, 0.45))
    if f > 0:
        fx = int(box[2] - 50 - flag.width + (1 - f) * 140)
        frame.alpha_composite(mk.with_alpha(flag, f), (fx, box[1] + 50))
    n = mk.ease_out(mk.phase(t, 1.7, 0.45))
    if n > 0:
        nf = mk.fit_font(row["country"], 84, 44, box[2] - box[0] - 100, 800)
        mk.draw_text(frame, (box[0] + 50 - int(50 * (1 - n)), box[1] + 255), row["country"], nf,
                     mk.TEXT + (int(255 * n),), anchor="lm")
    c = mk.ease_out(mk.phase(t, 1.8, 1.2))
    if c > 0:
        value = format_value(p["unit"], row["value"] * c) if c < 1 else row["value_text"]
        mk.draw_text(frame, (box[0] + 50, box[1] + 350), value, mk.font(88, 800), mk.TEXT + (255,), anchor="lm")
    if p.get("bar") is not None:
        b = mk.ease_out(mk.phase(t, 2.0, 1.0))
        x0, x1, y = box[0] + 50, box[2] - 50, box[1] + 425
        d = ImageDraw.Draw(frame)
        d.rounded_rectangle((x0, y, x1, y + 14), 7, fill=(40, 48, 72, 255))
        if b > 0:
            d.rounded_rectangle((x0, y, x0 + max(14, (x1 - x0) * p["bar"] * b), y + 14), 7, fill=mk.ACCENT + (255,))
    lab = mk.phase(t, 2.2, 0.5)
    if lab > 0:
        mk.draw_text(frame, (box[0] + 50, box[1] + 465), p["label"], mk.font(30, 500),
                     mk.TEXT_DIM + (int(255 * lab),), shadow=0)


def _top3(frame, t, rows, flags, p):
    k = mk.ease_out(mk.phase(t, 0.6, 0.5))
    if k <= 0:
        return
    box = (60, 110 - int(400 * (1 - k)), 1020, 760 - int(400 * (1 - k)))
    mk.shadowed_panel(frame, box)
    mk.draw_text(frame, (W / 2, box[1] + 70), "THE TOP 3", mk.font(54, 900), mk.ACCENT + (255,), anchor="mm")
    for i, r in enumerate(rows):
        a = mk.ease_out(mk.phase(t, 0.95 + 0.2 * i, 0.45))
        if a <= 0:
            continue
        y = box[1] + 170 + i * 170
        x = box[0] + 40 + int(120 * (1 - a))
        mk.draw_text(frame, (x, y + 40), f"#{r['rank']}", mk.font(70, 900), mk.ACCENT + (int(255 * a),), anchor="lm")
        frame.alpha_composite(mk.with_alpha(flags[r["iso2"]], a), (x + 150, y))
        nf = mk.fit_font(r["country"], 52, 30, 380, 800)
        mk.draw_text(frame, (x + 300, y + 22), r["country"], nf, mk.TEXT + (int(255 * a),), anchor="lm")
        mk.draw_text(frame, (x + 300, y + 74), r["value_text"], mk.font(40, 600),
                     mk.TEXT_DIM + (int(255 * a),), anchor="lm", shadow=0.4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--video-config", default="config/rankings_video.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.video_config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]
    metric, ranked, segments = script["metric"], script["ranked"], script["segments"]
    duration, src = resolve_duration(script, video_cfg, args.audio)
    durs = segment_durations(script, duration)
    print(f"[render_rankings_motion] {len(segments)} beats, {duration:.1f}s (from {src})")
    os.makedirs(args.out, exist_ok=True)

    flags = {}
    for r in ranked:
        path = os.path.join(args.out, f"_motion_flag_{r['iso2'].lower()}.png")
        flags[r["iso2"]] = path if _fetch_flag_resilient(r["iso2"], path) else None

    unit, top = metric["unit"], ranked[0]["value"]
    asc = metric.get("sort_direction") == "asc"
    rows = [{"iso2": r["iso2"], "country": r["country_name"], "rank": r["rank"], "tied": r["tied"],
             "value": r["value"], "value_text": format_value(unit, r["value"])} for r in ranked]
    by_iso = {r["iso2"]: r for r in rows}
    year_range = script.get("year_range") or year_range_label(ranked)
    label = f"{metric['label']}  ·  {ranked[0]['source_label']}" + (f", {year_range}" if year_range else "")
    title = title_name(metric).upper() if "display_label" in metric else f"COUNTRIES BY {metric['label'].upper()}"

    cam = _camera()
    prev_shot = (mk.SOURCE_W / 2, mk.SOURCE_H / 2, 6400)
    revealed, clips, jobs, events, start = [], [], [], [], 0.0
    one_iso = next((r["iso2"] for r in rows if r["rank"] == 1), None)
    last = len(segments) - 1
    for i, (seg, dur) in enumerate(zip(segments, durs)):
        visual = (seg.get("visual") or "").strip().upper()
        if visual in by_iso:
            row = by_iso[visual]
            bar = None
            if top > 0 and row["value"] > 0:
                bar = (top / row["value"]) if asc else (row["value"] / top)
            chips = [{"iso2": r["iso2"], "rank": r["rank"], "revealed": r["iso2"] in revealed + [visual]}
                     for r in reversed(rows)]
            payload = {"row": row, "prev_shot": prev_shot, "flag": flags.get(visual), "unit": unit,
                       "bar": bar, "label": label, "chips": chips, "revealed_before": list(revealed)}
            kind = "row"
            events.append((start + 0.05, mk.sfx_whoosh()))
            if visual == one_iso:
                events.append((start - 1.5, mk.sfx_riser()))
                events.append((start + 1.45, mk.sfx_impact()))
            else:
                events.append((start + 1.45, mk.sfx_pop()))
            events.append((start + 3.0, mk.sfx_ding()))
            revealed.append(visual)
            if cam.has_mask(visual):
                prev_shot = cam.shot_for(visual, pad=2.9)
        elif i == 0:
            kind = "intro"
            payload = {"rows": rows, "title": title, "dur": dur,
                       "source": f"{ranked[0]['source_label']}" + (f"  ·  {year_range}" if year_range else "")}
            events.append((0.45, mk.sfx_pop()))
        else:
            kind = "outro" if i == last else "hold"
            payload = {"rows": rows, "prev_shot": prev_shot, "flags": flags}
            events.append((start + 0.05, mk.sfx_whoosh(1.0)))
        out = os.path.join(args.out, f"footage_clip{i}.mp4")
        jobs.append((out, dur + TAIL_PAD, (kind, payload)))
        clips.append({"source": "rankings_motion", "query": visual, "source_url": "",
                      "attribution": f"Rendered motion graphics (data: {ranked[0]['source_label']})",
                      "resolution": f"{W}x{H}", "license": "Generated content -- original render",
                      "type": "video", "path": os.path.basename(out)})
        start += dur

    mk.render_clips(jobs, (W, H), make_scene, args.processes)

    mk.write_sfx_track(os.path.join(args.out, "sfx.wav"), duration, events)
    for path in flags.values():
        if path and os.path.exists(path):
            os.remove(path)
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump({"clip_count": len(clips), "clips": clips, "source": "rankings_motion",
                   "identifier": metric.get("id", "unknown")}, fh, indent=2)
    print(f"[render_rankings_motion] wrote {len(clips)} clips + sfx.wav")


if __name__ == "__main__":
    main()
