#!/usr/bin/env python3
"""
Pictures for the weekly India long-form ("India in 10 Charts"), 16:9.

For every segment of build/long/script.json:
  * chart segments: a transparent animation of India's line climbing year by year (held on
    its last frame for the rest of the segment) and a still of the finished chart;
  * intro/outro: a title card;
  * a real Indian footage clip behind it (fetch_india_broll: the clip must name India or an
    Indian place), or a plain backdrop when none is found.
Writes pieces.json in the shape assemble_long_video.py expects, plus a thumbnail.

Usage:
    python scripts/render_india_longform.py [--dir build/long]
"""
import argparse
import json
import os

from PIL import Image, ImageDraw

import fetch_india_broll as broll
import motion_kit as mk
from render_india_rising import INDIA, WORLD_C, eval_line

W, H = 1920, 1080
PANEL = (80, 70, 1840, 1010)
PLOT = (170, 330, 1250, 880)
ANIM_SECONDS = 9.0


class Chart:
    def __init__(self, chart):
        self.india = [tuple(p) for p in chart["india"]]
        self.unit, self.labels, self.title = chart["unit"], chart["labels"], chart["title"]
        world = [tuple(p) for p in chart.get("world", [])]
        imax = max(v for _, v in self.india)
        self.world = world if world and chart["unit"] not in ("usd_big", "people") \
            and max(v for _, v in world) <= 3 * imax else []
        self.y0, self.y1 = self.india[0][0], self.india[-1][0]
        vals = [v for _, v in self.india] + [v for _, v in self.world]
        self.vmin = min(0.0, min(vals) * 1.15)
        self.vmax = 100.0 if self.unit == "pct" and max(vals) > 40 else max(vals) * 1.12

    def xy(self, year, value):
        x0, y0, x1, y1 = PLOT
        fx = (year - self.y0) / max(1, self.y1 - self.y0)
        fy = (value - self.vmin) / max(1e-9, self.vmax - self.vmin)
        return x0 + fx * (x1 - x0), y1 - fy * (y1 - y0)

    def label(self, year):
        nearest = min(self.india, key=lambda p: abs(p[0] - year))
        return self.labels.get(str(nearest[0]), "")


def _card(img):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(PANEL, 40, fill=(8, 14, 30, 165))
    img.alpha_composite(layer)


def chart_frame(chart, progress, number, total):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _card(img)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = PLOT
    small, mid, big = mk.font(28, 600), mk.font(54, 800), mk.font(120, 800)
    for i in range(5):
        _, yy = chart.xy(chart.y0, chart.vmin + (chart.vmax - chart.vmin) * i / 4)
        d.line((x0, yy, x1, yy), fill=(70, 82, 116, 255), width=1)
    if chart.vmin < 0:
        _, yz = chart.xy(chart.y0, 0)
        d.line((x0, yz, x1, yz), fill=(150, 160, 190, 255), width=2)
    span = chart.y1 - chart.y0
    for year in range(chart.y0, chart.y1 + 1, 5 if span > 14 else 2):
        xx, _ = chart.xy(year, chart.vmin)
        d.text((xx, y1 + 18), str(year), font=small, fill=mk.TEXT_DIM + (255,), anchor="mt")
    head = chart.y0 + progress * (chart.y1 - chart.y0)
    years = [y for y, _ in chart.india if y <= head] + [head]
    pts = [chart.xy(y, eval_line(chart.india, y)) for y in years]
    if len(pts) >= 2:
        base_y = chart.xy(chart.y0, max(chart.vmin, 0))[1]
        d.polygon(pts + [(pts[-1][0], base_y), (pts[0][0], base_y)], fill=INDIA + (50,))
    if chart.world:  # over India's shading, under India's line
        wpts = [chart.xy(y, v) for y, v in chart.world]
        for a, b in zip(wpts, wpts[1:]):
            d.line((a, b), fill=WORLD_C + (230,), width=5)
        d.text((wpts[-1][0] + 12, wpts[-1][1]), "World", font=small, fill=WORLD_C + (255,), anchor="lm")
    if len(pts) >= 2:
        d.line(pts, fill=INDIA + (90,), width=22, joint="curve")
        d.line(pts, fill=INDIA + (255,), width=10, joint="curve")
    hx, hy = pts[-1]
    d.ellipse((hx - 15, hy - 15, hx + 15, hy + 15), fill=(255, 255, 255, 255), outline=INDIA + (255,), width=6)
    mk.draw_text(img, (170, 150), chart.title.upper(), mk.fit_font(chart.title.upper(), 60, 34, 1500, 800),
                 mk.TEXT + (255,), anchor="lm")
    mk.draw_text(img, (170, 225), f"CHART {number} OF {total}  ·  INDIA, {chart.y0} TO {chart.y1}", small,
                 mk.TEXT_DIM + (255,), anchor="lm")
    year_now = int(round(head))
    mk.draw_text(img, (1550, 470), str(year_now), mid, mk.TEXT_DIM + (255,), anchor="mm")
    mk.draw_text(img, (1550, 600), chart.label(year_now), mk.fit_font(chart.label(year_now), 120, 60, 520, 800),
                 INDIA + (255,), anchor="mm")
    mk.draw_text(img, (170, 970), "Source: World Bank", small, mk.TEXT_DIM + (255,), anchor="lm")
    return img


def title_frame(title, subtitle):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _card(img)
    mk.draw_text(img, (W / 2, 440), title, mk.fit_font(title, 110, 50, 1600, 800), mk.TEXT + (255,), anchor="mm")
    mk.draw_text(img, (W / 2, 590), subtitle, mk.fit_font(subtitle, 56, 30, 1600, 700), INDIA + (255,), anchor="mm")
    return img


def make_scene(chart, number, total):
    """Worker-side scene for the chart animation (the line grows over ANIM_SECONDS)."""
    c = Chart(chart)

    def scene(t):
        return chart_frame(c, mk.ease_io(min(1.0, t / (ANIM_SECONDS - 1.0))), number, total)
    return scene


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="build/long")
    ap.add_argument("--processes", type=int, default=None)
    args = ap.parse_args()
    d = args.dir
    with open(os.path.join(d, "script.json"), encoding="utf-8") as fh:
        script = json.load(fh)
    segs = script["segments"]
    charts = [s for s in segs if s["kind"] == "chart"]
    pieces, jobs, credits, used = [], [], [], set()
    n = 0
    for i, seg in enumerate(segs):
        piece = {}
        if seg["kind"] == "chart":
            n += 1
            still = chart_frame(Chart(seg["chart"]), 1.0, n, len(charts))
            piece["overlay"] = f"overlay_{i:03d}.png"
            still.save(os.path.join(d, piece["overlay"]))
            piece["overlay_anim"] = f"anim_{i:03d}.mov"
            jobs.append((os.path.join(d, piece["overlay_anim"]), ANIM_SECONDS, (seg["chart"], n, len(charts))))
        else:
            heading = script["title"].split(":")[0].upper() if seg["kind"] == "intro" else "SOURCES IN THE DESCRIPTION"
            sub = script["title"].split(":", 1)[-1].strip() if seg["kind"] == "intro" else "Subscribe for new charts every week"
            piece["overlay"] = f"overlay_{i:03d}.png"
            title_frame(heading, sub).save(os.path.join(d, piece["overlay"]))
        # one real Indian clip behind each segment, never the same clip twice in a row
        found = broll.stock_videos(seg.get("broll", []), d, want=1)
        found = [f for f in found if f["source_url"] not in used] or found
        if found:
            dest = os.path.join(d, f"bg_{i:03d}.mp4")
            os.replace(found[0]["path"], dest)
            used.add(found[0]["source_url"])
            piece["background"] = os.path.basename(dest)
            credits.append(found[0])
        elif pieces and pieces[-1].get("background"):
            piece["background"] = pieces[-1]["background"]
        pieces.append(piece)
        print(f"[india_long_render] segment {i + 1}/{len(segs)}: {seg['kind']}, "
              f"{'footage' if piece.get('background') else 'plain'}")
    if jobs:
        mk.render_clips(jobs, (W, H), make_scene, args.processes, alpha=True)
    with open(os.path.join(d, "pieces.json"), "w", encoding="utf-8") as fh:
        json.dump(pieces, fh, indent=1)
    thumb = title_frame(script["title"].split(":")[0].upper(), script["title"].split(":", 1)[-1].strip())
    bg = Image.new("RGBA", (W, H), (12, 20, 40, 255))
    bg.alpha_composite(thumb)
    bg.convert("RGB").resize((1280, 720)).save(os.path.join(d, "thumbnail.jpg"), quality=90)
    if credits:
        import fetch_place_footage as place
        with open(os.path.join(d, "credits.txt"), "w", encoding="utf-8") as fh:
            fh.write(place.credits_line(credits))
    print(f"[india_long_render] {len(pieces)} segments, {len(credits)} footage clips, thumbnail")


if __name__ == "__main__":
    main()
