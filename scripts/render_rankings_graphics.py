#!/usr/bin/env python3
"""
Stage 2 (rankings variant): stands in for fetch_footage.py for the "rankings"
series only, same role render_comparison_graphics.py plays for comparison.
No stock footage -- renders one static countdown-leaderboard image per
narration segment (current row's flag + country + value, plus an
accumulating leaderboard of rows already revealed), and lists them in
build/footage.json as "photo" pieces.

Like comparison, this does NOT need its own ffmpeg camera-path render:
assemble_video.py already applies a Ken Burns zoompan to any footage.json
piece marked type="photo".

Rows are looked up by ISO2 code (each segment's "visual"), not by rank
number -- fetch_rankings_stats.get_ranked() now assigns competition ranks
(1, 2, 2, 4) so a genuine tie is never split into two invented, arbitrarily-
ordered ranks, which means rank numbers alone are no longer a unique key.

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

from fetch_rankings_stats import format_value, year_range_label
from generate_intro import fetch_flag
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

# assemble_video.py burns TWO overlays into every frame that this renderer
# has no control over: lower-third captions (measured empirically at
# y=1237-1319 for a single line, with headroom for a 2-line wrap -- unsafe
# from roughly y=1140) for the whole video, and a static CTA end-card around
# y=1554-1690 for the last 4 seconds. Keeping all of this series' header/
# spotlight/leaderboard content above y=1100 avoids both.
LEADERBOARD_TOP = 700
LEADERBOARD_BOTTOM = 1100

# A THIRD constraint neither of the above accounts for: assemble_video.py's
# Ken Burns zoompan crops the frame edges progressively over each clip's
# duration (up to ~192px off the top AND bottom of this 1920px-tall PNG by
# the end of a clip at the default zoom cap of 1.25 -- verified by replaying
# the exact filter and tracking marker positions). That silently pushed the
# footer (drawn near y=1870, only 50px from the bottom edge) out of frame for
# most of every video's runtime. Rather than guess a footer position that
# survives an unknown zoom, this series requests a much gentler zoom cap via
# footage.json's "zoom_max" (see assemble_video.py's build_video_filter) --
# ZOOM_MAX below MUST match what's written into the manifest in main().
ZOOM_MAX = 1.04
FOOTER_Y = 1830  # verified clear of both the caption band and the Ken Burns crop at ZOOM_MAX


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
    if flag_path is None:
        # fetch_flag failed even after retry -- draw a plain placeholder
        # panel instead of aborting the whole render (a single flagcdn
        # hiccup used to kill the day's post outright).
        d = ImageDraw.Draw(canvas)
        box = (center_x - box_w / 2, top, center_x + box_w / 2, top + box_h)
        _rounded_rect(d, box, 8, (40, 44, 62))
        return
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


def _rank_label(row):
    return f"#{row['rank']}=" if row["tied"] else f"#{row['rank']}"


def render_header(canvas, metric, mode, current_row=None, flag_path=None):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    label = metric.get("display_label", metric["label"])

    kicker = "COUNTRY RANKINGS" if mode != "outro" else "FINAL RANKINGS"
    draw.text((w / 2 - _text_w(draw, kicker, _font(True, 24)) / 2, 50),
               kicker, font=_font(True, 24), fill=TEXT_DIM)

    label_font = _fit_text(draw, label.upper(), True, 42, 24, w - 120)
    _draw_centered(draw, w / 2, 95, label.upper(), label_font, TEXT_WHITE)

    if mode == "row" and current_row is not None:
        # y=270 clears the kicker (50-78) and metric label (95-~145) above,
        # with a 15px margin so the flag never sits on top of that text.
        pillar_top, pillar_bottom, pillar_w = 340, 640, 420
        _draw_flagpole(canvas, flag_path, w / 2, 270, pillar_top, 150, 110)
        _draw_pillar(canvas, w / 2, pillar_top, pillar_bottom, pillar_w)

        rank_font = _font(True, 64)
        _draw_centered(draw, w / 2, pillar_top + 20, f"RANK {_rank_label(current_row)[1:]}",
                       rank_font, (255, 235, 150))
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


def render_leaderboard(canvas, metric, ranked, revealed_isos, highlight_iso=None):
    """Accumulating leaderboard of every row revealed so far (or the full
    list on the outro beat), most-recently-revealed row highlighted. Row
    height is derived from len(ranked) rather than a separate hardcoded
    constant, so changing config/rankings_metrics.json's top_n can never
    silently push rows back down into the caption band (see LEADERBOARD_TOP/
    BOTTOM above) -- a prior version had exactly that drift bug."""
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    n = len(ranked)
    rows = [row for row in ranked if row["iso2"] in revealed_isos]
    if not rows:
        return

    row_h = (LEADERBOARD_BOTTOM - LEADERBOARD_TOP) / max(1, n)
    for idx, row in enumerate(rows):
        ry0 = LEADERBOARD_TOP + idx * row_h
        ry1 = ry0 + row_h
        is_hl = row["iso2"] == highlight_iso
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

        draw.text((90, mid_y - rank_font.size / 2), _rank_label(row), font=rank_font, fill=rank_color)
        draw.text((190, mid_y - name_font.size / 2), row["country_name"], font=name_font, fill=name_color)
        val_text = format_value(metric["unit"], row["value"])
        vw = _text_w(draw, val_text, val_font)
        draw.text((w - 90 - vw, mid_y - val_font.size / 2), val_text, font=val_font, fill=val_color)


def render_footer(canvas, metric, ranked, year_range):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    footer = f"Source: {ranked[0]['source_label']}"
    if year_range:
        footer += f", as of {year_range}"
    if metric.get("attribution_note"):
        footer += f" -- {metric['attribution_note']}"
    more_tied = next((r["more_tied_beyond"] for r in ranked if r.get("more_tied_beyond")), None)
    if more_tied:
        # Showing only top_n of a wider tie without saying so would itself
        # misrepresent the tie -- see get_ranked's docstring. Spoken once in
        # the narration too (generate_rankings_script.py); repeating it here
        # means it's visible even to a viewer who skips the audio.
        plural = "country" if more_tied == 1 else "countries"
        footer += f" ({more_tied} more {plural} tie at the #{ranked[-1]['rank']} value)"
    footer_font = _font(False, 18)
    _draw_centered(draw, w / 2, FOOTER_Y, footer, footer_font, (90, 96, 116))


def compose_frame(metric, ranked, mode, current_row, flag_path, revealed_isos, year_range):
    canvas = Image.new("RGB", CANVAS, BG_COLOR)
    render_header(canvas, metric, mode, current_row, flag_path)
    render_leaderboard(canvas, metric, ranked, revealed_isos,
                       highlight_iso=current_row["iso2"] if current_row else None)
    render_footer(canvas, metric, ranked, year_range)
    return canvas


def _fetch_flag_resilient(iso2, path):
    """fetch_flag() retries transient failures internally (see generate_
    intro.py); this catches the case where it still fails after that, so a
    single unlucky country doesn't abort the whole render -- returns True on
    success, False (placeholder panel via _paste_flag) on repeated failure."""
    try:
        fetch_flag(iso2, path)
        return True
    except Exception as exc:  # noqa: BLE001 -- network call, many transient failure modes
        print(f"[render_rankings_graphics] flag fetch for {iso2} failed ({exc}) -- "
              f"using a placeholder panel instead of aborting the render")
        return False


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

    year_range = script.get("year_range") or year_range_label(ranked)

    duration, dur_source = resolve_duration(script, video_cfg, args.audio)
    print(f"[render_rankings_graphics] duration {duration:.2f}s (from {dur_source})")

    os.makedirs(args.out, exist_ok=True)
    by_iso2 = {row["iso2"]: row for row in ranked}

    flag_paths = {}
    print(f"[render_rankings_graphics] fetching {len(ranked)} flags ...")
    for row in ranked:
        path = os.path.join(args.out, f"_rank_flag_{row['iso2'].lower()}.png")
        ok = _fetch_flag_resilient(row["iso2"], path)
        flag_paths[row["iso2"]] = path if ok else None

    # A segment's "visual" identifies a row by ISO2 code (rank numbers alone
    # aren't unique once ties are possible -- see module docstring). The
    # FIRST segment is always the intro and the LAST is always the outro
    # (generate_rankings_script.py's validator enforces this shape for GPT
    # output; the fallback script is built this way directly) -- inferring
    # "outro" from "any non-row segment after index 0" previously let a
    # single unexpected extra beat mid-video reveal the full leaderboard
    # early and permanently, contradicting the narration for the rest of the
    # video. Any OTHER non-row segment (there shouldn't be one, but this is
    # the defensive path) just holds the current reveal state rather than
    # guessing.
    clips = []
    revealed_isos = set()
    last_idx = len(segments) - 1
    for i, seg in enumerate(segments):
        visual = (seg.get("visual") or "").strip().upper()
        row = by_iso2.get(visual)
        if row is not None:
            mode = "row"
            revealed_isos.add(row["iso2"])
            flag_path = flag_paths[row["iso2"]]
        elif i == 0:
            mode, row, flag_path = "intro", None, None
        elif i == last_idx:
            mode, row, flag_path = "outro", None, None
            revealed_isos = {r["iso2"] for r in ranked}  # full leaderboard on the outro
        else:
            # Unexpected shape (shouldn't happen given the validator) --
            # hold the current reveal state instead of jumping to a full
            # reveal or crashing.
            mode, row, flag_path = "hold", None, None

        frame = compose_frame(metric, ranked, mode, row, flag_path, revealed_isos, year_range)
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
            "zoom_max": ZOOM_MAX,
            "path": os.path.basename(out_path),
        })

    for path in flag_paths.values():
        if path:
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
