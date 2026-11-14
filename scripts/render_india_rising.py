#!/usr/bin/env python3
"""
Animated year-by-year chart for the "India Rising" Short: India's line climbs year by year
while a big counter shows the current year and value; the World average appears as a dashed
line when the narration compares with it. One clip per narration beat, timed to the real
voice (pipeline_common.segment_durations), so the animation is continuous across cuts.

The chart is drawn as a see-through layer over real Indian footage (fetch_india_broll.py:
ports, factories, skylines...), dimmed a little so the numbers stay readable. Without footage
it falls back to a plain background.

Runs AFTER the voiceover. Output: build/footage_clip<N>.mp4, build/footage.json, build/sfx.wav

Usage:
    python scripts/render_india_rising.py [--script build/script.json] [--audio build/voice.wav]
"""
import argparse
import json
import os
import subprocess

from PIL import Image, ImageDraw

import motion_kit as mk
from pipeline_common import segment_durations
from render_geography_map import resolve_duration

W, H = 1080, 1920
PLOT = (130, 640, 980, 990)          # chart box; the caption band starts at y=1040
INDIA = (255, 153, 51)               # saffron
WORLD_C = (150, 160, 190)
TAIL_PAD = 0.5
LAYER_TOP, LAYER_H = 200, 880        # the chart layer covers y=200..1080 of the frame
PANEL = (60, 230, 1020, 1075)        # translucent card behind title, counter and chart


def eval_line(points, year):
    """Value at a fractional year, linear between the yearly readings."""
    if year <= points[0][0]:
        return points[0][1]
    for (y0, v0), (y1, v1) in zip(points, points[1:]):
        if year <= y1:
            return v0 + (v1 - v0) * (year - y0) / (y1 - y0)
    return points[-1][1]


def progress_at(T, marks):
    """0..1 share of the timeline drawn at absolute time T: the line starts climbing with
    the 'grow' beat and reaches today a little into the 'end' beat."""
    start, finish = marks["grow"], marks["end"] + 0.6 * marks["end_dur"]
    if T <= start:
        return 0.0
    return mk.ease_io(min(1.0, (T - start) / max(0.1, finish - start)))


class Chart:
    def __init__(self, chart, unit, labels):
        self.india = [tuple(p) for p in chart["india"]]
        world = [tuple(p) for p in chart["world"] if p[0] >= self.india[0][0]]
        india_max = max(v for _, v in self.india)
        # A World line only where it is an average on the same scale (a share, years, per
        # person). For totals like GDP the "World" figure is the whole planet's sum, and on a
        # shared axis it flattened India's line to the floor.
        comparable = world and max(v for _, v in world) <= 3 * india_max and unit != "usd_big"
        self.world = world if comparable else []
        self.unit, self.labels = unit, labels
        self.y0, self.y1 = self.india[0][0], self.india[-1][0]
        values = [v for _, v in self.india] + [v for _, v in self.world]
        top = 100.0 if unit == "pct" else max(values) * 1.12
        self.vmin, self.vmax = 0.0, top

    def xy(self, year, value):
        x0, y0, x1, y1 = PLOT
        fx = (year - self.y0) / max(1, self.y1 - self.y0)
        fy = (value - self.vmin) / max(1e-9, self.vmax - self.vmin)
        return x0 + fx * (x1 - x0), y1 - fy * (y1 - y0)

    def label_for(self, year):
        """Chart text for a year, falling back to the nearest reading."""
        nearest = min(self.india, key=lambda p: abs(p[0] - year))
        return self.labels.get(str(nearest[0]), "")


def _background():
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        f = y / H
        c = tuple(int(a + (b - a) * f) for a, b in zip(mk.SKY_TOP, mk.SKY_BOT))
        d.line((0, y, W, y), fill=c + (255,))
    return img


def _axes(img, chart):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = PLOT
    small = mk.font(26, 600)
    for i in range(5):
        v = chart.vmin + (chart.vmax - chart.vmin) * i / 4
        _, yy = chart.xy(chart.y0, v)
        d.line((x0, yy, x1, yy), fill=(60, 72, 104, 255), width=1)
    span = chart.y1 - chart.y0
    step = 5 if span > 14 else 2 if span > 6 else 1
    for year in range(chart.y0, chart.y1 + 1, step):
        xx, _ = chart.xy(year, chart.vmin)
        d.text((xx, y1 + 18), str(year), font=small, fill=mk.TEXT_DIM + (255,), anchor="mt")


def _polyline(d, pts, fill, width):
    if len(pts) >= 2:
        d.line(pts, fill=fill, width=width, joint="curve")


def _panel():
    """Full-frame transparent image with the translucent card the chart sits on."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(PANEL, 36, fill=(8, 14, 30, 150))
    return img


def make_scene(chart_data, unit, labels, title, marks, offset, overlay=False):
    """Runs inside each render worker; returns scene(t) -> RGBA frame. overlay=True draws
    only the chart layer (transparent around the card), cropped to LAYER_TOP..+LAYER_H."""
    chart = Chart(chart_data, unit, labels)
    base = _panel() if overlay else _background()
    _axes(base, chart)
    big, mid, small = mk.font(118, 800), mk.font(56, 800), mk.font(28, 600)
    title_f = mk.fit_font(title.upper(), 60, 34, W - 140, 800)

    def scene(t):
        T = offset + t
        frame = base.copy()
        p = progress_at(T, marks)
        head_year = chart.y0 + p * (chart.y1 - chart.y0)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        # World average: dashed, fades in with the comparison beat
        w_alpha = mk.ease_out(mk.phase(T, marks.get("world", 1e9), 0.8))
        if chart.world and w_alpha > 0:
            pts = [chart.xy(y, v) for y, v in chart.world]
            for a, b in zip(pts, pts[1:]):
                d.line((a, b), fill=WORLD_C + (int(220 * w_alpha),), width=5)
            wx, wy = pts[-1]
            d.text((wx - 10, wy - 22), "World", font=small, fill=WORLD_C + (int(255 * w_alpha),), anchor="rb")
        # India: the line so far, an area under it and a glowing head
        years = [y for y, _ in chart.india if y <= head_year] + [head_year]
        pts = [chart.xy(y, eval_line(chart.india, y)) for y in years]
        if len(pts) >= 2:
            area = pts + [(pts[-1][0], PLOT[3]), (pts[0][0], PLOT[3])]
            d.polygon(area, fill=INDIA + (46,))
            _polyline(d, pts, INDIA + (90,), 22)
            _polyline(d, pts, INDIA + (255,), 10)
        hx, hy = pts[-1]
        pulse = 1 + 0.15 * abs(((T * 2) % 2) - 1)
        r = 15 * pulse
        d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=(255, 255, 255, 255), outline=INDIA + (255,), width=6)
        frame.alpha_composite(layer)
        # Title, counter and source
        mk.draw_text(frame, (W / 2, 300), title.upper(), title_f, mk.TEXT + (255,), anchor="mm")
        year_now = int(round(head_year))
        mk.draw_text(frame, (W / 2, 400), str(year_now), mid, mk.TEXT_DIM + (255,), anchor="mm")
        mk.draw_text(frame, (W / 2, 515), chart.label_for(year_now), big, INDIA + (255,), anchor="mm")
        mk.draw_text(frame, (W / 2, 1052), "Source: World Bank", small, mk.TEXT_DIM + (255,), anchor="mm")
        if overlay:
            return frame.crop((0, LAYER_TOP, W, LAYER_TOP + LAYER_H))
        return frame

    return scene


def beat_marks(segments, durs):
    """Absolute start time of each kind of beat (first occurrence)."""
    marks, start = {}, 0.0
    for seg, dur in zip(segments, durs):
        kind = seg.get("visual", "").replace("chart_", "")
        if kind not in marks:
            marks[kind] = start
            marks[f"{kind}_dur"] = dur
        start += dur
    marks.setdefault("grow", marks.get("start", 0.0) + 1.0)
    marks.setdefault("end", start - 3.0)
    marks.setdefault("end_dur", 2.0)
    return marks


def composite(background, kind, layer, out, dur):
    """Real footage (looped video or slowly zooming photo), cover-cropped to 9:16 and dimmed a
    little, with the chart layer on top."""
    n = max(1, int(round(dur * mk.FPS)))
    if kind == "photo":
        src = ["-i", background]  # zoompan makes all n frames from the single image
        bg = (f"[0:v]scale=1188:2112:force_original_aspect_ratio=increase,crop=1188:2112,"
              f"zoompan=z='min(zoom+0.0007,1.15)':d={n}:s={W}x{H}:fps={mk.FPS}")
    else:
        src = ["-stream_loop", "-1", "-i", background]
        bg = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={mk.FPS}"
    graph = (f"{bg},setsar=1,eq=brightness=-0.06:saturation=1.1[bg];"
             f"[bg][1:v]overlay=0:{LAYER_TOP}:format=auto,format=yuv420p[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-i", layer, "-filter_complex", graph,
                    "-map", "[v]", "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", out], check=True, timeout=600)


def load_broll(out_dir):
    try:
        with open(os.path.join(out_dir, "broll.json"), encoding="utf-8") as fh:
            pieces = json.load(fh)
    except (OSError, ValueError):
        return []
    return [p for p in pieces if os.path.exists(os.path.join(out_dir, p["path"]))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--video-config", default="config/india.json")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--out", default="build")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.video_config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]
    segments = script["segments"]
    duration, src = resolve_duration(script, video_cfg, args.audio)
    durs = segment_durations(script, duration)
    marks = beat_marks(segments, durs)
    metric = script["metric"]
    print(f"[render_india_rising] {len(segments)} beats, {duration:.1f}s (from {src})")

    broll = load_broll(args.out)
    overlay = bool(broll)
    print(f"[render_india_rising] background: {len(broll)} real footage pieces" if overlay
          else "[render_india_rising] no real footage found -- plain background")
    jobs, clips, events, start = [], [], [], 0.0
    for i, dur in enumerate(durs):
        out = os.path.join(args.out, f"footage_clip{i}.mp4")
        target = os.path.join(args.out, f"_chart_layer{i}.mov") if overlay else out
        jobs.append((target, dur + TAIL_PAD, (script["chart"], metric["unit"], script["chart"]["labels"],
                                              metric["title"], marks, start, overlay)))
        clips.append({"source": "india_rising", "type": "video", "path": os.path.basename(out),
                      "query": segments[i].get("visual", ""), "license": "Generated content -- original render",
                      "attribution": f"Rendered chart (data: World Bank {metric['code']})"})
        if i:
            events.append((start + 0.02, mk.sfx_whoosh(0.6)))
        start += dur
    events.append((marks["grow"], mk.sfx_riser(1.6)))
    events.append((marks["end"] + 0.6 * marks["end_dur"], mk.sfx_impact()))

    if overlay:
        mk.render_clips(jobs, (W, LAYER_H), make_scene, args.processes, alpha=True)
        for i, (layer, dur, _args) in enumerate(jobs):
            piece = broll[i % len(broll)]
            composite(os.path.join(args.out, piece["path"]), piece["type"], layer,
                      os.path.join(args.out, f"footage_clip{i}.mp4"), dur)
            os.remove(layer)
            clips[i]["background"] = {k: piece.get(k) for k in ("source", "author", "license", "source_url")}
    else:
        mk.render_clips(jobs, (W, H), make_scene, args.processes)
    mk.write_sfx_track(os.path.join(args.out, "sfx.wav"), duration, events)
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump({"clip_count": len(clips), "clips": clips, "source": "india_rising",
                   "identifier": metric["id"]}, fh, indent=2)
    print(f"[render_india_rising] wrote {len(clips)} clips + sfx.wav")


if __name__ == "__main__":
    main()
