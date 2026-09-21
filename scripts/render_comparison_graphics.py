#!/usr/bin/env python3
"""
Stage 2 (comparison variant): stands in for fetch_footage.py for the
"comparison" series only, same role render_geography_map.py / render_
worlddata_graphics.py play for their series. No stock footage at all --
renders one static side-by-side comparison-table image per narration
segment (both countries' flags + a table of the real stats from
fetch_country_stats.py, with that segment's row highlighted), and lists them
in build/footage.json as "photo" pieces.

Unlike the map/flag series, this does NOT need its own ffmpeg camera-path
render: assemble_video.py already applies a Ken Burns zoompan to any
footage.json piece marked type="photo", so one PNG per segment is enough --
the motion comes for free from the existing photo path.

Output:
    build/footage.json          same schema fetch_footage.py writes
    build/footage_clip{0..N-1}.png

Usage:
    python scripts/render_comparison_graphics.py
    python scripts/render_comparison_graphics.py --script build/script.json --out build
"""
import argparse
import json
import os

from PIL import Image, ImageDraw, ImageFont

from generate_intro import fetch_flag
from fetch_country_stats import format_value
from pipeline_common import segment_durations
from render_geography_map import resolve_duration

CANVAS = (1080, 1920)
BG_COLOR = (13, 17, 30)
PANEL_COLOR = (24, 29, 48)
PANEL_HIGHLIGHT = (44, 74, 140)
ACCENT = (230, 180, 60)
TEXT_WHITE = (245, 246, 250)
TEXT_DIM = (150, 157, 180)
TEXT_LABEL_DIM = (120, 128, 152)


def _find_font(candidates):
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


FONT_PATH_BOLD = _find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
])
FONT_PATH_REGULAR = _find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
])


def _font(bold, size):
    return ImageFont.truetype(FONT_PATH_BOLD if bold else FONT_PATH_REGULAR, size)


def _text_w(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def _draw_centered(draw, cx, y, text, font, fill):
    w = _text_w(draw, text, font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)
    return w


def _fit_text(draw, text, bold, max_size, min_size, max_width):
    """Largest font size in [min_size, max_size] that keeps `text` within
    max_width, so long country names (Central African Republic) shrink
    instead of overflowing their column."""
    size = max_size
    while size > min_size:
        font = _font(bold, size)
        if _text_w(draw, text, font) <= max_width:
            return font
        size -= 2
    return _font(bold, min_size)


def _rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _paste_flag(canvas, flag_path, center_x, top, box_w, box_h):
    flag = Image.open(flag_path).convert("RGB")
    scale = min(box_w / flag.width, box_h / flag.height)
    w, h = max(1, int(flag.width * scale)), max(1, int(flag.height * scale))
    flag = flag.resize((w, h), Image.LANCZOS)
    x = int(center_x - w / 2)
    y = int(top + (box_h - h) / 2)
    border = 4
    d = ImageDraw.Draw(canvas)
    d.rectangle((x - border, y - border, x + w + border, y + h + border),
                outline=(70, 78, 100), width=border)
    canvas.paste(flag, (x, y))


def render_header(canvas, country_a, country_b, flag_a_path, flag_b_path):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size

    draw.text((w / 2 - _text_w(draw, "COUNTRY COMPARISON", _font(True, 26)) / 2, 70),
               "COUNTRY COMPARISON", font=_font(True, 26), fill=TEXT_DIM)

    flag_box_w, flag_box_h = 380, 320
    _paste_flag(canvas, flag_a_path, w * 0.27, 140, flag_box_w, flag_box_h)
    _paste_flag(canvas, flag_b_path, w * 0.73, 140, flag_box_w, flag_box_h)

    # VS badge
    cx, cy, r = w / 2, 300, 62
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT)
    vs_font = _font(True, 44)
    vs_w = _text_w(draw, "VS", vs_font)
    draw.text((cx - vs_w / 2, cy - 30), "VS", font=vs_font, fill=(20, 16, 6))

    name_font_a = _fit_text(draw, country_a["name"], True, 44, 26, flag_box_w + 40)
    name_font_b = _fit_text(draw, country_b["name"], True, 44, 26, flag_box_w + 40)
    _draw_centered(draw, w * 0.27, 508, country_a["name"], name_font_a, TEXT_WHITE)
    _draw_centered(draw, w * 0.73, 508, country_b["name"], name_font_b, TEXT_WHITE)

    draw.line((60, 600, w - 60, 600), fill=(50, 56, 78), width=2)


def render_table(canvas, rows, highlight_label):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    top, bottom = 630, 1660
    n = len(rows)
    row_h = (bottom - top) / n

    for i, row in enumerate(rows):
        ry0 = top + i * row_h
        ry1 = ry0 + row_h
        is_hl = row["label"] == highlight_label
        pad = 10
        box = (50, ry0 + pad, w - 50, ry1 - pad)
        _rounded_rect(draw, box, 18, PANEL_HIGHLIGHT if is_hl else PANEL_COLOR)

        label_font = _font(True, 30 if is_hl else 22)
        value_font_a = _font(True, 52 if is_hl else 36)
        value_font_b = _font(True, 52 if is_hl else 36)
        label_color = TEXT_WHITE if is_hl else TEXT_LABEL_DIM
        value_color = TEXT_WHITE if is_hl else TEXT_DIM

        mid_y = (box[1] + box[3]) / 2
        _draw_centered(draw, w / 2, mid_y - label_font.size / 2 - (34 if is_hl else 20),
                       row["label"].upper(), label_font, label_color)

        val_a = format_value(row["unit"], row["value_a"])
        val_b = format_value(row["unit"], row["value_b"])
        va_w = _text_w(draw, val_a, value_font_a)
        vb_w = _text_w(draw, val_b, value_font_b)
        vy = mid_y + (2 if is_hl else 4)
        draw.text((w * 0.27 - va_w / 2, vy), val_a, font=value_font_a, fill=value_color)
        draw.text((w * 0.73 - vb_w / 2, vy), val_b, font=value_font_b, fill=value_color)

        if row["comparable"]:
            winner_x = w * 0.27 if row["value_a"] > row["value_b"] else w * 0.73
            arrow_font = _font(True, 34 if is_hl else 24)
            arrow_color = ACCENT if is_hl else (170, 140, 60)
            arrow_w = _text_w(draw, "\u25b2", arrow_font)
            draw.text((winner_x - arrow_w / 2, vy - arrow_font.size - 4), "\u25b2",
                       font=arrow_font, fill=arrow_color)


def render_footer(canvas, country_a, country_b, rows, mode):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    if mode == "outro":
        wins_a = sum(1 for r in rows if r["comparable"] and r["value_a"] > r["value_b"])
        wins_b = sum(1 for r in rows if r["comparable"] and r["value_b"] > r["value_a"])
        if wins_a == wins_b:
            summary = "DEAD HEAT -- ALL SQUARE"
        else:
            leader = country_a["name"] if wins_a > wins_b else country_b["name"]
            summary = f"{leader.upper()} LEADS {max(wins_a, wins_b)}-{min(wins_a, wins_b)}"
        font = _fit_text(draw, summary, True, 40, 22, w - 120)
        _draw_centered(draw, w / 2, 1700, summary, font, ACCENT)

    year_set = sorted({r["year_a"] for r in rows} | {r["year_b"] for r in rows})
    years = year_set[0] if len(year_set) == 1 else f"{year_set[0]}-{year_set[-1]}"
    footer = f"Source: World Bank ({years})"
    footer_font = _font(False, 20)
    _draw_centered(draw, w / 2, 1860, footer, footer_font, (90, 96, 116))


def compose_frame(country_a, country_b, flag_a_path, flag_b_path, rows, highlight_label, mode):
    canvas = Image.new("RGB", CANVAS, BG_COLOR)
    render_header(canvas, country_a, country_b, flag_a_path, flag_b_path)
    render_table(canvas, rows, highlight_label)
    render_footer(canvas, country_a, country_b, rows, mode)
    return canvas


def main():
    ap = argparse.ArgumentParser(description="Render the comparison series' side-by-side table graphics.")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--video-config", default="config/comparison_video.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--audio", default="build/voice.wav",
                    help="Synthesized voice track; its real length drives the segment "
                         "slice boundaries. Falls back to a word-count estimate if absent.")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.video_config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]

    segments = script.get("segments") or []
    rows = script.get("rows") or []
    country_a, country_b = script["country_a"], script["country_b"]
    if not segments:
        raise SystemExit(f"{args.script} has no segments -- nothing to render")
    if not rows:
        raise SystemExit(f"{args.script} has no rows -- nothing to compare")

    duration, dur_source = resolve_duration(script, video_cfg, args.audio)
    print(f"[render_comparison_graphics] duration {duration:.2f}s (from {dur_source})")
    durs = segment_durations(script, duration)

    os.makedirs(args.out, exist_ok=True)
    flag_a_path = os.path.join(args.out, f"_cmp_flag_{country_a['iso2'].lower()}.png")
    flag_b_path = os.path.join(args.out, f"_cmp_flag_{country_b['iso2'].lower()}.png")
    print(f"[render_comparison_graphics] fetching flags for {country_a['iso2']} / {country_b['iso2']} ...")
    fetch_flag(country_a["iso2"], flag_a_path)
    fetch_flag(country_b["iso2"], flag_b_path)

    clips = []
    for i, seg in enumerate(segments):
        label = (seg.get("visual") or "").strip()
        if label:
            mode = "row"
        elif i == 0:
            mode = "intro"
        else:
            mode = "outro"
        frame = compose_frame(country_a, country_b, flag_a_path, flag_b_path, rows, label, mode)
        out_path = os.path.join(args.out, f"footage_clip{i}.png")
        frame.save(out_path)
        clips.append({
            "source": "comparison_table",
            "query": label,
            "source_url": "",
            "attribution": "Rendered comparison table (data: World Bank Open Data, public domain)",
            "resolution": f"{CANVAS[0]}x{CANVAS[1]}",
            "license": "Generated content -- original render",
            "type": "photo",
            "path": os.path.basename(out_path),
        })

    for tmp in (flag_a_path, flag_b_path):
        try:
            os.remove(tmp)
        except OSError:
            pass

    manifest = {
        "clip_count": len(clips),
        "clips": clips,
        "source": "comparison_table",
        "identifier": f"{country_a['iso2']}_{country_b['iso2']}",
    }
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"[render_comparison_graphics] wrote {len(clips)} frames + footage.json for "
          f"{country_a['name']} vs {country_b['name']}")


if __name__ == "__main__":
    main()
