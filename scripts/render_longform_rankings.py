#!/usr/bin/env python3
"""
Visuals for long-form format A (16:9). One transparent overlay PNG per narration
segment, plus an optional background clip of that country from Pexels, and a
1280x720 thumbnail.

Country segments: a data card on the left (rank, flag, name, value, and a trend
line drawn only through real yearly readings, labelled with their actual years and
values), the countdown so far on the right, and the middle left clear so the
country's footage shows through. Intro and recap use full-frame cards.

Output (in --out): slide_NNN.png, bg_NNN.mp4 (when footage was found),
pieces.json [{"overlay", "background"}], thumbnail.jpg

Usage:
    python scripts/render_longform_rankings.py --script build/long/script.json --out build/long
"""
import argparse
import json
import os

from PIL import Image, ImageDraw

from fetch_footage import fetch_pexels
from fetch_rankings_stats import format_value
from render_rankings_graphics import (ACCENT, PANEL_HIGHLIGHT, TEXT_DIM, TEXT_WHITE, _draw_centered,
                                      _fetch_flag_resilient, _fit_text, _font, _paste_flag, _text_w)

W, H = 1920, 1080
CARD = (60, 90, 820, 990)
LIST = (1380, 90, 1860, 990)
CARD_FILL = (12, 16, 30, 215)
LIST_FILL = (12, 16, 30, 190)
LINE_COLOR = (230, 180, 60, 255)
GENERIC_QUERY = "earth from space globe aerial"


def _overlay():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _panel(img, box, fill, radius=24):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill)
    img.alpha_composite(layer)


def _paste_flag_rgba(img, flag_path, cx, top, box_w, box_h):
    rgb = Image.new("RGB", img.size)
    rgb.paste(img.convert("RGB"))
    _paste_flag(rgb, flag_path, cx, top, box_w, box_h)
    mask = Image.new("L", img.size, 0)
    if flag_path:
        flag = Image.open(flag_path)
        scale = min(box_w / flag.width, box_h / flag.height)
        w, h = int(flag.width * scale), int(flag.height * scale)
        x, y = int(cx - w / 2), int(top + (box_h - h) / 2)
        ImageDraw.Draw(mask).rectangle((x - 4, y - 4, x + w + 4, y + h + 4), fill=255)
    else:
        ImageDraw.Draw(mask).rounded_rectangle((cx - box_w / 2, top, cx + box_w / 2, top + box_h), 8, fill=255)
    img.paste(rgb.convert("RGBA"), (0, 0), mask)


def _trend(img, draw, points, box, fmt):
    """Line through real readings only; first and last points labelled with their year/value."""
    x0, y0, x1, y1 = box
    if len(points) < 2:
        return
    years = [p[0] for p in points]
    vals = [p[1] for p in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or abs(hi) or 1
    xy = [(x0 + (y - years[0]) / max(1, years[-1] - years[0]) * (x1 - x0),
           y1 - (v - lo) / span * (y1 - y0)) for y, v in points]
    draw.line(xy, fill=LINE_COLOR, width=5, joint="curve")
    for x, y in (xy[0], xy[-1]):
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=LINE_COLOR)
    small = _font(False, 24)
    draw.text((x0, y1 + 14), f"{years[0]}: {fmt(vals[0])}", font=small, fill=TEXT_DIM)
    right = f"{years[-1]}: {fmt(vals[-1])}"
    draw.text((x1 - _text_w(draw, right, small), y1 + 14), right, font=small, fill=TEXT_WHITE)


def country_slide(record, row, revealed, flag_path):
    img = _overlay()
    _panel(img, CARD, CARD_FILL)
    _panel(img, LIST, LIST_FILL)
    d = ImageDraw.Draw(img)
    cx = (CARD[0] + CARD[2]) / 2
    rank = f"#{row['rank']}" + (" (tied)" if row["tied"] else "")
    _draw_centered(d, cx, CARD[1] + 30, rank, _font(True, 96), ACCENT)
    _paste_flag_rgba(img, flag_path, cx, CARD[1] + 160, 360, 230)
    d = ImageDraw.Draw(img)
    name_font = _fit_text(d, row["country"], True, 64, 34, CARD[2] - CARD[0] - 60)
    _draw_centered(d, cx, CARD[1] + 420, row["country"], name_font, TEXT_WHITE)
    _draw_centered(d, cx, CARD[1] + 505, row["value"], _font(True, 72), TEXT_WHITE)
    _draw_centered(d, cx, CARD[1] + 595, f"{record['slide_title']} | {row['year']}", _font(False, 26), TEXT_DIM)
    if row.get("trend"):
        _trend(img, d, row["trend"], (CARD[0] + 70, CARD[1] + 660, CARD[2] - 70, CARD[3] - 90),
               lambda v: format_value(record["unit"], v))

    d.text((LIST[0] + 30, LIST[1] + 24), "COUNTDOWN", font=_font(True, 30), fill=TEXT_DIM)
    row_h = (LIST[3] - LIST[1] - 90) / max(1, len(record["rows"]))
    for i, r in enumerate(reversed(record["rows"])):
        if r["iso2"] not in revealed:
            continue
        y = LIST[1] + 80 + i * row_h
        current = r["iso2"] == row["iso2"]
        if current:
            d.rounded_rectangle((LIST[0] + 14, y - 4, LIST[2] - 14, y + row_h - 4), 8, fill=PANEL_HIGHLIGHT)
        font = _font(current, 24)
        d.text((LIST[0] + 30, y), f"#{r['rank']}", font=font, fill=ACCENT if current else TEXT_DIM)
        name = _fit_text(d, r["country"], current, 24, 16, 230)
        d.text((LIST[0] + 110, y), r["country"], font=name, fill=TEXT_WHITE)
        d.text((LIST[2] - 30 - _text_w(d, r["value"], font), y), r["value"], font=font, fill=TEXT_WHITE)
    footer = f"Source: {record['source_label']} data, as of {record['as_of']}"
    d.text((CARD[0], H - 62), footer, font=_font(False, 24), fill=TEXT_DIM)
    return img


def title_slide(record, kind, flags):
    img = Image.new("RGBA", (W, H), (8, 12, 24, 170))
    d = ImageDraw.Draw(img)
    rows = record["rows"]
    if kind == "intro":
        _draw_centered(d, W / 2, 250, f"TOP {len(rows)}", _font(True, 150), ACCENT)
        title_font = _fit_text(d, record["slide_title"].upper(), True, 72, 40, W - 200)
        _draw_centered(d, W / 2, 460, record["slide_title"].upper(), title_font, TEXT_WHITE)
        _draw_centered(d, W / 2, 580, f"Every country explained | {record['source_label']} data, "
                                       f"as of {record['as_of']}", _font(False, 34), TEXT_DIM)
        return img
    _draw_centered(d, W / 2, 50, record["slide_title"], _font(True, 50), TEXT_WHITE)
    half = (len(rows) + 1) // 2
    for i, r in enumerate(rows):
        col, pos = divmod(i, half)
        x = 140 + col * 860
        y = 160 + pos * 82
        d = ImageDraw.Draw(img)
        d.text((x, y), f"#{r['rank']}", font=_font(True, 36), fill=ACCENT)
        _paste_flag_rgba(img, flags.get(r["iso2"]), x + 150, y - 4, 70, 46)
        d = ImageDraw.Draw(img)
        d.text((x + 210, y), r["country"], font=_fit_text(d, r["country"], False, 34, 20, 360), fill=TEXT_WHITE)
        d.text((x + 760 - _text_w(d, r["value"], _font(True, 34)), y), r["value"], font=_font(True, 34),
               fill=TEXT_WHITE)
    return img


def thumbnail(record, flags):
    leader = record["rows"][0]
    img = Image.new("RGB", (1280, 720), (10, 14, 28))
    d = ImageDraw.Draw(img)
    d.text((60, 50), f"TOP {len(record['rows'])}", font=_font(True, 150), fill=ACCENT)
    title = record["slide_title"].upper().replace(f"TOP {len(record['rows'])} ", "")
    d.text((60, 230), title, font=_fit_text(d, title, True, 70, 34, 1160), fill=TEXT_WHITE)
    _paste_flag(img, flags.get(leader["iso2"]), 900, 360, 420, 280)
    d.text((60, 420), "#1", font=_font(True, 120), fill=ACCENT)
    d.text((60, 560), leader["country"], font=_fit_text(d, leader["country"], True, 80, 36, 640), fill=TEXT_WHITE)
    return img


def _background(query, country, dest):
    try:
        info = fetch_pexels(query, dest, want_portrait=False, min_height=720,
                            country_name=country, max_height=1080)
        return os.path.basename(dest) if info else None
    except Exception as exc:  # noqa: BLE001 -- footage is decoration; the card carries the content
        print(f"[render_longform] no footage for {country or query} ({exc})")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/long/script.json")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build/long")
    ap.add_argument("--no-footage", action="store_true")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        record = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        queries = {c["iso2"]: c.get("footage_query", c["name"]) for c in json.load(fh)["countries"]}
    os.makedirs(args.out, exist_ok=True)
    by_iso = {r["iso2"]: r for r in record["rows"]}

    flags = {}
    for iso2 in by_iso:
        path = os.path.join(args.out, f"flag_{iso2}.png")
        flags[iso2] = path if _fetch_flag_resilient(iso2, path) else None

    pieces, revealed = [], set()
    for i, seg in enumerate(record["segments"]):
        visual = seg["visual"]
        if visual in by_iso:
            revealed.add(visual)
            img = country_slide(record, by_iso[visual], revealed, flags[visual])
            query, country = queries.get(visual, by_iso[visual]["country"]), by_iso[visual]["country"]
        else:
            img = title_slide(record, visual, flags)
            query, country = GENERIC_QUERY, None
        overlay = f"slide_{i:03d}.png"
        img.save(os.path.join(args.out, overlay))
        bg = None if args.no_footage else _background(query, country, os.path.join(args.out, f"bg_{i:03d}.mp4"))
        pieces.append({"overlay": overlay, "background": bg})

    thumbnail(record, flags).save(os.path.join(args.out, "thumbnail.jpg"), quality=90)
    with open(os.path.join(args.out, "pieces.json"), "w", encoding="utf-8") as fh:
        json.dump(pieces, fh, indent=2)
    print(f"[render_longform] {len(pieces)} slides, "
          f"{sum(1 for p in pieces if p['background'])} with footage, thumbnail written")


if __name__ == "__main__":
    main()
