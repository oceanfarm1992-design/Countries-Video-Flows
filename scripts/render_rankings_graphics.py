#!/usr/bin/env python3
"""
Stage 2 (rankings variant): stands in for fetch_footage.py for the "rankings"
series only, same role render_comparison_graphics.py plays for comparison.
No stock footage -- renders one static countdown-leaderboard image per
narration segment (current rank's flag + country + value, plus an
accumulating leaderboard of ranks already revealed), and lists them in
build/footage.json as "photo" pieces.

Like comparison, this does NOT need its own ffmpeg camera-path render:
assemble_video.py already applies a Ken Burns zoompan to any footage.json
piece marked type="photo".

Output:
    build/footage.json          same schema fetch_footage.py writes
    build/footage_clip{0..N-1}.png

Usage:
    python scripts/render_rankings_graphics.py
    python scripts/render_rankings_graphics.py --script build/script.json --out build
"""
import argparse
import json
import os

from PIL import Image, ImageDraw, ImageFont

from fetch_rankings_stats import format_value
from generate_intro import fetch_flag
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

# Glossy red "pillar" look, borrowed from the reference bar-chart-race style
# video the user asked this series to visually echo (flag mounted on a pole
# above a glossy red column, country/rank labelled on the column). True 3D
# (camera fly-through, physically animated flags, reflective floor) isn't
# reproducible in this project's PIL + ffmpeg pipeline -- this is a 2D
# approximation of the same visual language, not a clone.
PILLAR_TOP = (205, 40, 45)
PILLAR_BOTTOM = (120, 15, 20)
PILLAR_HIGHLIGHT = (235, 90, 90)

# assemble_video.py burns TWO overlays into the bottom ~40% of every frame
# that this renderer has no control over: lower-third captions (measured
# empirically at y=1237-1319 for a single line, with generous headroom for a
# 2-line wrap -- unsafe from roughly y=1140) for the whole video, and a
# static CTA end-card around y=1554-1690 for the last 4 seconds. Keeping all
# of this series' own text (header, spotlight, leaderboard) above y=1100
# avoids both without needing to predict caption line-wrap length or which
# segment lands in the final 4 seconds.
LEADERBOARD_TOP = 700
LEADERBOARD_BOTTOM = 1100
LEADERBOARD_MAX_ROWS = 8  # matches config/rankings_metrics.json's default top_n


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
    max_width, so long country names shrink instead of overflowing."""
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


def _draw_pillar(canvas, cx, top, bottom, width, radius=22):
    """Glossy red vertical pillar -- a 2D approximation of the reference
    bar-chart-race video's 3D pillars (top-to-bottom gradient fill masked to
    a rounded rectangle, plus a lighter highlight stripe for a glossy look).
    Real 3D (camera fly-through, physically animated flags) isn't
    reproducible in this project's PIL + ffmpeg pipeline."""
    width, height = int(width), int(bottom - top)
    grad = Image.new("RGB", (width, height))
    grad_draw = ImageDraw.Draw(grad)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(PILLAR_TOP[i] + (PILLAR_BOTTOM[i] - PILLAR_TOP[i]) * t) for i in range(3))
        grad_draw.line([(0, y), (width, y)], fill=color)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    canvas.paste(grad, (int(cx - width / 2), int(top)), mask)

    draw = ImageDraw.Draw(canvas)
    stripe_x = cx - width / 2 + width * 0.22
    draw.line([(stripe_x, top + 14), (stripe_x, bottom - 14)],
              fill=PILLAR_HIGHLIGHT, width=max(3, int(width * 0.05)))


def _draw_flagpole(canvas, flag_path, cx, pole_top, pole_bottom, flag_w, flag_h):
    """Pole + mounted flag above a pillar, echoing the reference video's
    flag-on-a-pole look (no wave animation -- a static mounted flag)."""
    draw = ImageDraw.Draw(canvas)
    draw.line([(cx, pole_top), (cx, pole_bottom)], fill=(190, 194, 206), width=5)
    _paste_flag(canvas, flag_path, cx, pole_top - flag_h + 10, flag_w, flag_h)


def render_header(canvas, metric, mode, current_rank=None, current_row=None, flag_path=None):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size

    kicker = "COUNTRY RANKINGS" if mode != "outro" else "FINAL RANKINGS"
    draw.text((w / 2 - _text_w(draw, kicker, _font(True, 24)) / 2, 50),
               kicker, font=_font(True, 24), fill=TEXT_DIM)

    label_font = _fit_text(draw, metric["label"].upper(), True, 42, 24, w - 120)
    _draw_centered(draw, w / 2, 95, metric["label"].upper(), label_font, TEXT_WHITE)

    if mode == "rank" and current_row is not None:
        # y=270 clears the kicker (50-78) and metric label (95-~145) above,
        # with a 15px margin so the flag never sits on top of that text.
        pillar_top, pillar_bottom, pillar_w = 340, 640, 420
        _draw_flagpole(canvas, flag_path, w / 2, 270, pillar_top, 150, 110)
        _draw_pillar(canvas, w / 2, pillar_top, pillar_bottom, pillar_w)

        rank_font = _font(True, 64)
        _draw_centered(draw, w / 2, pillar_top + 20, f"RANK {current_rank}", rank_font, (255, 235, 150))
        name_font = _fit_text(draw, current_row["country_name"], True, 40, 22, pillar_w - 50)
        _draw_centered(draw, w / 2, pillar_top + 105, current_row["country_name"], name_font, TEXT_WHITE)
        val_font = _font(True, 32)
        val_text = format_value(metric["unit"], current_row["value"])
        _draw_centered(draw, w / 2, pillar_top + 165, val_text, val_font, (255, 235, 150))
    elif mode == "intro":
        # y=400 clears assemble_video.py's own hook title card, a SEPARATE
        # burned-in overlay (drawtext, not rendered by this script) that
        # covers roughly y=140-310 for the first 4 seconds of every video.
        sub_font = _font(False, 26)
        _draw_centered(draw, w / 2, 400, "COUNTDOWN STARTING NOW", sub_font, TEXT_DIM)

    draw.line((60, 670, w - 60, 670), fill=(50, 56, 78), width=2)


def render_leaderboard(canvas, metric, ranked, revealed_ranks, highlight_rank=None):
    """Accumulating leaderboard of every rank revealed so far (or the full
    list on the outro beat), most-recently-revealed rank highlighted."""
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    n = len(ranked)
    # `ranked` is sorted best-first, so a row's true rank is (index + 1).
    rows = [(i + 1, ranked[i]) for i in range(n) if (i + 1) in revealed_ranks]
    rows.sort(key=lambda r: r[0])  # display #1 at top, worst rank at bottom
    if not rows:
        return

    row_h = (LEADERBOARD_BOTTOM - LEADERBOARD_TOP) / max(1, LEADERBOARD_MAX_ROWS)
    for idx, (rank, row) in enumerate(rows):
        ry0 = LEADERBOARD_TOP + idx * row_h
        ry1 = ry0 + row_h
        is_hl = rank == highlight_rank
        pad = 4
        box = (60, ry0 + pad, w - 60, ry1 - pad)
        _rounded_rect(draw, box, 12, PANEL_HIGHLIGHT if is_hl else PANEL_COLOR)

        mid_y = (box[1] + box[3]) / 2
        rank_font = _font(True, 24)
        name_font = _fit_text(draw, row["country_name"], not is_hl, 22, 15, w - 400)
        val_font = _font(True, 20)

        rank_color = ACCENT if is_hl else TEXT_LABEL_DIM
        name_color = TEXT_WHITE if is_hl else TEXT_DIM
        val_color = ACCENT if is_hl else TEXT_LABEL_DIM

        draw.text((90, mid_y - rank_font.size / 2), f"#{rank}", font=rank_font, fill=rank_color)
        draw.text((190, mid_y - name_font.size / 2), row["country_name"], font=name_font, fill=name_color)
        val_text = format_value(metric["unit"], row["value"])
        vw = _text_w(draw, val_text, val_font)
        draw.text((w - 90 - vw, mid_y - val_font.size / 2), val_text, font=val_font, fill=val_color)


def render_footer(canvas, metric, mode, source_label):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    footer = f"Source: {source_label}"
    if metric.get("attribution_note"):
        footer += f" -- {metric['attribution_note']}"
    footer_font = _font(False, 18)
    _draw_centered(draw, w / 2, 1870, footer, footer_font, (90, 96, 116))


def compose_frame(metric, ranked, mode, current_rank, flag_path, revealed_ranks):
    canvas = Image.new("RGB", CANVAS, BG_COLOR)
    # `ranked` is sorted best-first, so a row's true rank is (index + 1).
    current_row = ranked[current_rank - 1] if current_rank is not None else None
    render_header(canvas, metric, mode, current_rank, current_row, flag_path)
    render_leaderboard(canvas, metric, ranked, revealed_ranks, highlight_rank=current_rank)
    render_footer(canvas, metric, mode, ranked[0]["source_label"])
    return canvas


def main():
    ap = argparse.ArgumentParser(description="Render the rankings series' countdown-leaderboard graphics.")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--video-config", default="config/rankings_video.json")
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
    ranked = script.get("ranked") or []
    metric = script.get("metric") or {}
    if not segments:
        raise SystemExit(f"{args.script} has no segments -- nothing to render")
    if not ranked:
        raise SystemExit(f"{args.script} has no ranked entries -- nothing to render")

    duration, dur_source = resolve_duration(script, video_cfg, args.audio)
    print(f"[render_rankings_graphics] duration {duration:.2f}s (from {dur_source})")
    durs = segment_durations(script, duration)

    os.makedirs(args.out, exist_ok=True)
    n = len(ranked)

    flag_paths = {}
    print(f"[render_rankings_graphics] fetching {n} flags ...")
    for row in ranked:
        path = os.path.join(args.out, f"_rank_flag_{row['iso2'].lower()}.png")
        fetch_flag(row["iso2"], path)
        flag_paths[row["iso2"]] = path

    clips = []
    revealed_ranks = set()
    for i, seg in enumerate(segments):
        visual = (seg.get("visual") or "").strip()
        # generate_rankings_script.py validates GPT-authored rank labels
        # before writing script.json, but defend here too (e.g. a hand-
        # edited or future-reused script.json) -- an out-of-range rank
        # falls through to the generic "outro" treatment rather than
        # crashing this render step with an IndexError.
        if visual.isdigit() and 1 <= int(visual) <= n:
            mode = "rank"
            current_rank = int(visual)
            revealed_ranks.add(current_rank)
            row = ranked[current_rank - 1]  # ranked is sorted best-first
            flag_path = flag_paths[row["iso2"]]
        elif i == 0:
            mode, current_rank, flag_path = "intro", None, None
        else:
            mode, current_rank, flag_path = "outro", None, None
            revealed_ranks = set(range(1, n + 1))  # full leaderboard on the outro

        frame = compose_frame(metric, ranked, mode, current_rank, flag_path, revealed_ranks)
        out_path = os.path.join(args.out, f"footage_clip{i}.png")
        frame.save(out_path)
        clips.append({
            "source": "rankings_leaderboard",
            "query": visual,
            "source_url": "",
            "attribution": f"Rendered rankings leaderboard (data: {ranked[0]['source_label']})",
            "resolution": f"{CANVAS[0]}x{CANVAS[1]}",
            "license": "Generated content -- original render",
            "type": "photo",
            "path": os.path.basename(out_path),
        })

    for path in flag_paths.values():
        try:
            os.remove(path)
        except OSError:
            pass

    manifest = {
        "clip_count": len(clips),
        "clips": clips,
        "source": "rankings_leaderboard",
        "identifier": metric.get("id", "unknown"),
    }
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"[render_rankings_graphics] wrote {len(clips)} frames + footage.json for "
          f"{metric.get('label', 'unknown metric')}")


if __name__ == "__main__":
    main()
