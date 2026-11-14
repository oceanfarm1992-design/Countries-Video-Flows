#!/usr/bin/env python3
"""
Opening split screen for Untold India: the famous place on top (a confirmed clip of it,
labelled "SWITZERLAND"), the Indian place below (labelled "GUREZ VALLEY, INDIA"), so viewers
see the comparison the narration makes. The famous clip must name that place in its own
title or tags, like every other piece of footage here; without one, there is no split.
"""
import os
import subprocess

from PIL import Image, ImageDraw

import fetch_footage as stock
import motion_kit as mk

W, H, HALF = 1080, 1920, 960
SPLIT_SECONDS = 7
# compare text -> (stock search, words that confirm the clip shows it). None: no real footage.
FAMOUS = {
    "switzerland": ("Switzerland alps valley", ["switzerland", "swiss"]),
    "the swiss alps": ("Swiss alps meadow", ["switzerland", "swiss"]),
    "the alps": ("Alps snow mountains", ["alps", "switzerland", "austria"]),
    "tibet": ("Tibet landscape", ["tibet"]),
    "bhutan": ("Bhutan monastery", ["bhutan"]),
    "japan": ("Japan rice terraces", ["japan"]),
    "bali": ("Bali beach cliff", ["bali"]),
    "the maldives": ("Maldives beach aerial", ["maldives"]),
    "the mekong delta": ("Mekong delta river", ["mekong", "vietnam"]),
    "egypt": ("Egypt ancient ruins", ["egypt"]),
    "patagonia": ("Patagonia lake mountains", ["patagonia"]),
    "the galapagos": ("Galapagos turtle beach", ["galapagos"]),
    "the mediterranean": ("Mediterranean coast", ["mediterranean"]),
    "sri lanka": ("Sri Lanka tea plantation", ["sri lanka"]),
    "dubai": ("Dubai skyline", ["dubai"]),
    "bolivia's salt flats": ("Salar de Uyuni salt flat", ["uyuni", "bolivia"]),
    "niagara": ("Niagara falls", ["niagara"]),
    "venice": ("Venice canal", ["venice", "venezia"]),
    "the moon": None,
    "mars": None,
}


def famous_entry(compare):
    return FAMOUS.get((compare or "").strip().lower())


def label_for(compare):
    """'the Maldives' -> 'MALDIVES', "Bolivia's salt flats" -> "BOLIVIA'S SALT FLATS"."""
    text = compare.strip()
    if text.lower().startswith("the "):
        text = text[4:]
    return text.upper()


def fetch_famous(compare, dest):
    """A confirmed clip of the famous place, or None. Returns the stock info dict."""
    entry = famous_entry(compare)
    if not entry:
        return None
    query, terms = entry
    stock.EXTRA_MATCH_TERMS[:] = terms[1:]
    for fetch in (stock.fetch_pexels, stock.fetch_pixabay):
        try:
            if fetch is stock.fetch_pexels:
                info = fetch(query, dest, False, 720, terms[0], require_match=True)
            else:
                info = fetch(query, dest, 720, terms[0], require_match=True)
        except Exception as exc:  # noqa: BLE001 -- no split is fine
            print(f"[famous_split] {fetch.__name__} failed: {type(exc).__name__}")
            info = None
        if info and info.get("country_verified"):
            stock.EXTRA_MATCH_TERMS[:] = []
            return dict(info, type="video")
    stock.EXTRA_MATCH_TERMS[:] = []
    return None


def _label_png(text, path, accent):
    f = mk.fit_font(text, 64, 34, W - 160, 800)
    tw = mk.text_w(text, f)
    img = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
    box = ((W - tw) / 2 - 34, 14, (W + tw) / 2 + 34, 106)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, 26, fill=(8, 14, 30, 200))
    d.rounded_rectangle((box[0] + 26, box[1], box[2] - 26, box[1] + 6), 3, fill=accent + (255,))
    mk.draw_text(img, (W / 2, 62), text, f, (245, 246, 250, 255), anchor="mm")
    img.save(path)


def _half(kind, path):
    """ffmpeg input args and the filter that turns one piece into a 1080x960 half."""
    n = SPLIT_SECONDS * mk.FPS
    if kind == "photo":
        return (["-i", path], f"scale=1188:1056:force_original_aspect_ratio=increase,crop=1188:1056,"
                              f"zoompan=z='min(zoom+0.0008,1.12)':d={n}:s={W}x{HALF}:fps={mk.FPS}")
    return (["-stream_loop", "-1", "-i", path],
            f"scale={W}:{HALF}:force_original_aspect_ratio=increase,crop={W}:{HALF},fps={mk.FPS}")


def build_split(famous_path, india_kind, india_path, famous_label, india_label, out):
    """Famous place on top, India below, a thin line between, a label on each half."""
    tmp = os.path.dirname(out) or "."
    top_png, bottom_png = os.path.join(tmp, "_label_top.png"), os.path.join(tmp, "_label_bottom.png")
    _label_png(famous_label, top_png, (150, 160, 190))
    _label_png(india_label, bottom_png, (255, 153, 51))
    a_in, a_f = _half("video", famous_path)
    b_in, b_f = _half(india_kind, india_path)
    graph = (f"[0:v]{a_f},setsar=1[a];[1:v]{b_f},setsar=1[b];[a][b]vstack=inputs=2[s];"
             f"[s]drawbox=x=0:y={HALF - 3}:w={W}:h=6:color=white@0.9:t=fill[l];"
             f"[l][2:v]overlay=0:{HALF - 150}[t];[t][3:v]overlay=0:{HALF + 30},format=yuv420p[v]")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", *a_in, *b_in, "-i", top_png, "-i", bottom_png,
                        "-filter_complex", graph, "-map", "[v]", "-t", str(SPLIT_SECONDS), "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out],
                       check=True, timeout=600)
    finally:
        for p in (top_png, bottom_png):
            if os.path.exists(p):
                os.remove(p)
    return out
