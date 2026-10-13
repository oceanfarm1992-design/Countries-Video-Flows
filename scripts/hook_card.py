#!/usr/bin/env python3
"""
The opening hook as a designed title card (motion_kit style: dark panel, accent bar,
soft shadow, heavy Inter type) instead of ffmpeg's flat black-box drawtext. Written
as a transparent 1080x1920 PNG that the assembler fades in over the first seconds.
"""
from PIL import Image

import motion_kit as mk

W, H = 1080, 1920
BOX_X0, BOX_X1, BOX_Y0 = 70, W - 70, 150
MAX_LINES = 3


def _wrap(text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and mk.text_w(trial, f) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def make_hook_card(text, out_path):
    """Render `text` (the hook line) as a top-of-frame card; returns out_path."""
    text = " ".join(text.upper().split())
    inner = BOX_X1 - BOX_X0 - 100
    for size in range(74, 38, -4):  # largest size that fits in MAX_LINES lines
        f = mk.font(size, 900)
        lines = _wrap(text, f, inner)
        if len(lines) <= MAX_LINES:
            break
    line_h = int(size * 1.18)
    box = (BOX_X0, BOX_Y0, BOX_X1, BOX_Y0 + 70 + line_h * len(lines))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mk.shadowed_panel(img, box, radius=30)
    y = box[1] + 40 + line_h // 2
    for line in lines:
        mk.draw_text(img, (W / 2, y), line, f, mk.TEXT + (255,), anchor="mm")
        y += line_h
    img.save(out_path)
    return out_path
