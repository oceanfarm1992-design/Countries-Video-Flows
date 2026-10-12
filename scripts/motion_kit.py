#!/usr/bin/env python3
"""
Shared motion-graphics engine (PIL + numpy + ffmpeg, CPU only, runs on GitHub's
free runners). Used by the motion renderers to replace still slides with animated
scenes:

  - easing curves and timeline helpers
  - Inter typography with soft drop shadows (assets/fonts, SIL OFL)
  - MapCamera: a tilted 2.5D world map plane with depth fog and a graticule, a
    camera that flies between countries (rising mid-flight on long trips), and a
    country that rises out of the map as a lit 3D slab with shadow and glow,
    its top surface painted with the country's flag; countries too small to read
    get a light-beam marker (a small flagpole flying their flag) instead
  - render_clip(): renders a scene function frame by frame across all CPU cores
    and pipes the frames straight into ffmpeg
  - synthesized sound effects (whoosh, pop, ding, riser, impact) -- no licensing

Scene functions take (t, ctx) and return an RGBA PIL image of the canvas size.
"""
import math
import os
import subprocess
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FPS = 30
FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Inter-Variable.ttf")
GEO_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "geo")
SOURCE_W, SOURCE_H = 8192, 4096

ACCENT = (240, 190, 60)
TEXT = (245, 246, 250)
TEXT_DIM = (165, 174, 198)
PANEL = (14, 20, 38)
SKY_TOP, SKY_BOT = (6, 10, 24), (18, 32, 58)
LAND_LIT = (72, 196, 140)     # default raised-country colour
RIVAL = (236, 112, 92)        # second country in two-country scenes
FLAG_MAX_STRETCH = 2.5        # how far a flag's shape may bend to fit a country
FLAG_TEX_W = 640              # flag texture width (a raised country tops out ~900 px)


# ---------------------------------------------------------------- easing / time
def clamp(t):
    return max(0.0, min(1.0, t))


def phase(t, start, dur):
    return clamp((t - start) / dur) if dur > 0 else float(t >= start)


def ease_io(t):
    t = clamp(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out(t):
    return 1 - (1 - clamp(t)) ** 3


def ease_back(t, s=1.70158):
    t = clamp(t) - 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2


# ---------------------------------------------------------------- typography
_FONT_CACHE = {}


def font(size, weight=700):
    key = (int(size), int(weight))
    if key not in _FONT_CACHE:
        f = ImageFont.truetype(FONT_PATH, max(6, int(size)))
        try:
            f.set_variation_by_axes([min(32, max(14, size)), weight])
        except (OSError, AttributeError):  # older FreeType: falls back to the default weight
            pass
        _FONT_CACHE[key] = f
    return _FONT_CACHE[key]


def text_w(text, f):
    return f.getbbox(text)[2]


def fit_font(text, max_size, min_size, max_width, weight=700):
    size = max_size
    while size > min_size and text_w(text, font(size, weight)) > max_width:
        size -= 2
    return font(size, weight)


def draw_text(img, xy, text, f, fill, shadow=0.75, anchor="la"):
    """Text with a soft drop shadow so it reads over any background."""
    if shadow:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((xy[0] + 3, xy[1] + 5), text, font=f,
                                   fill=(0, 0, 0, int(255 * shadow)), anchor=anchor)
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(5)))
    ImageDraw.Draw(img).text(xy, text, font=f, fill=fill, anchor=anchor)


def shadowed_panel(img, box, radius=34, fill=PANEL + (236,), accent=True):
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((box[0] + 8, box[1] + 18, box[2] + 8, box[3] + 18), radius,
                                         fill=(0, 0, 0, 160))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20)))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius, fill=fill)
    if accent:
        d.rounded_rectangle((box[0] + radius, box[1], box[2] - radius, box[1] + 7), 3, fill=ACCENT + (255,))
    img.alpha_composite(layer)


def with_alpha(img, a):
    if a >= 1:
        return img
    out = img.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * max(0.0, a))))
    return out


# ---------------------------------------------------------------- map camera
def perspective_coeffs(dst, src):
    """PIL PERSPECTIVE coefficients mapping points in `dst` to points in `src`."""
    a = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    b = np.array([c for p in src for c in p], dtype=float)
    return np.linalg.solve(np.array(a, dtype=float), b).tolist()


def apply_coeffs(c, x, y):
    d = c[6] * x + c[7] * y + 1
    return (c[0] * x + c[1] * y + c[2]) / d, (c[3] * x + c[4] * y + c[5]) / d


def dominant_colour(pixels):
    """Most common colour of an (N, 3) pixel array (coarsely binned so anti-aliased
    edges don't split a field) -- Nepal's crimson, not a muddy average with its blue
    border and white emblems."""
    if not len(pixels):
        return (255, 255, 255)
    bins = (pixels // 16).astype(np.int32)
    keys = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    top = np.bincount(keys).argmax()
    return tuple(int(c) for c in pixels[keys == top].mean(axis=0))


def load_flag(path, width=FLAG_TEX_W):
    """RGB flag texture for painting countries, or None when there is no flag file.
    Transparent corners (Nepal's pennants) take the flag's dominant colour, so the
    country reads as one shape rather than half flag, half white."""
    if not path or not os.path.exists(path):
        return None
    f = Image.open(path).convert("RGBA")
    f = f.resize((width, max(1, int(width * f.height / f.width))), Image.LANCZOS)
    px = np.asarray(f).reshape(-1, 4)
    opaque = px[px[:, 3] > 128, :3]
    field = dominant_colour(opaque)
    out = Image.new("RGB", f.size, field)
    out.paste(f, (0, 0), f)
    out.info["field"] = dominant_colour(np.asarray(out).reshape(-1, 3))  # flag_fill runs per frame
    return out


def flag_fill(flag, size, box):
    """RGB image of `size` with the flag laid over `box` (x0, y0, x1, y1 in that
    image's pixels; the country's main landmass). The flag bends toward the box's
    shape -- at most FLAG_MAX_STRETCH, so stripes stay stripes and emblems stay
    recognisable -- then covers it, centred. Land outside the flag (far islands)
    takes the flag's dominant colour."""
    bw, bh = max(1.0, box[2] - box[0]), max(1.0, box[3] - box[1])
    fa = flag.width / flag.height
    aspect = min(fa * FLAG_MAX_STRETCH, max(fa / FLAG_MAX_STRETCH, bw / bh))
    s = max(bw / aspect, bh)  # cover: flag height s, width s * aspect
    fw, fh = max(1, math.ceil(s * aspect)), max(1, math.ceil(s))
    field = flag.info.get("field") or dominant_colour(np.asarray(flag).reshape(-1, 3))
    out = Image.new("RGB", (max(1, int(size[0])), max(1, int(size[1]))), field)
    out.paste(flag.resize((fw, fh), Image.BILINEAR),
              (int(box[0] + (bw - fw) / 2), int(box[1] + (bh - fh) / 2)))
    return out


def main_bbox(half_mask, max_gap=24):
    """Bounding box (full-res source px) of a country's main landmass: the heaviest
    run of occupied map columns, gaps up to max_gap half-res px (~2 degrees) bridged
    (wider merged Alaska into the lower 48: the panhandle is ~5 degrees from Washington).
    The whole-mask box is useless for framing countries whose territory spans the
    map -- the US with Alaska and the Aleutians across the date line framed the
    entire world. Works on the half-res mask; columns only, so it stays cheap."""
    a = np.asarray(half_mask, dtype=np.uint32)
    cols = a.sum(axis=0)
    occupied = np.nonzero(cols)[0]
    if occupied.size == 0:
        return None
    runs, start, prev = [], occupied[0], occupied[0]
    for x in occupied[1:]:
        if x - prev > max_gap:
            runs.append((start, prev))
            start = x
        prev = x
    runs.append((start, prev))
    x0, x1 = max(runs, key=lambda r: cols[r[0]:r[1] + 1].sum())
    rows = np.nonzero(a[:, x0:x1 + 1].sum(axis=1))[0]
    return (int(x0) * 2, int(rows[0]) * 2, int(x1 + 1) * 2, int(rows[-1] + 1) * 2)


class MapCamera:
    """Tilted world-map plane. Coordinates are equirectangular source pixels
    (8192 x 4096, the geography series' assets)."""

    def __init__(self, size, plane_quad, focus_screen, tex=1150, share=None):
        """share: another MapCamera whose loaded map and masks to reuse (several
        cameras in one scene, e.g. split screen, without loading the 8K map twice)."""
        self.w, self.h = size
        self.tex = tex
        self.src_quad = [(0, 0), (tex, 0), (tex, tex), (0, tex)]
        self.to_tex = perspective_coeffs(plane_quad, self.src_quad)    # screen -> texture
        self.to_screen = perspective_coeffs(self.src_quad, plane_quad)  # texture -> screen
        self.focus_tex = apply_coeffs(self.to_tex, *focus_screen)
        if share is not None:
            self.base, self.masks = share.base, share.masks
        else:
            full = Image.open(os.path.join(GEO_DIR, "basemap_world.png")).convert("RGB")
            self.base = {1.0: self._pad(full, 1.0),
                         0.5: self._pad(full.resize((SOURCE_W // 2, SOURCE_H // 2), Image.BILINEAR), 0.5)}
            self.masks = {}
        self.bg = self._sky()
        self.fog = self._fog(plane_quad[0][1])

    PAD = 2048  # source px of wrap-around (left/right) and ocean (top/bottom)

    def _pad(self, img, scale, ocean=(16, 28, 40)):
        p = int(self.PAD * scale)
        w, h = img.size
        out = Image.new(img.mode, (w + 2 * p, h + 2 * p), ocean if img.mode == "RGB" else 0)
        out.paste(img, (p, p))
        out.paste(img.crop((w - p, 0, w, h)), (0, p))        # east edge wraps to the west
        out.paste(img.crop((0, 0, p, h)), (w + p, p))        # west edge wraps to the east
        return out

    def _sky(self):
        g = np.linspace(0, 1, self.h)[:, None]
        rows = (np.array(SKY_TOP) * (1 - g) + np.array(SKY_BOT) * g).astype(np.uint8)
        return Image.fromarray(np.repeat(rows[:, None, :], self.w, axis=1)).convert("RGBA")

    def _fog(self, horizon):
        y = np.arange(self.h, dtype=float)
        near = np.clip(1 - (y - horizon - 40) / 300, 0, 1) * 255
        haze = np.clip(1 - (y - horizon - 300) / 900, 0, 1) ** 2 * 110
        alpha = np.maximum(np.where(y < horizon + 340, near, 0), haze).astype(np.uint8)
        layer = Image.new("RGBA", (self.w, self.h), SKY_BOT + (0,))
        layer.putalpha(Image.fromarray(np.repeat(alpha[:, None], self.w, axis=1)))
        return layer

    def mask(self, iso2):
        if iso2 not in self.masks:
            m = Image.open(os.path.join(GEO_DIR, "country_masks", f"{iso2.upper()}.png")).convert("L")
            half = m.resize((SOURCE_W // 2, SOURCE_H // 2), Image.BILINEAR)
            self.masks[iso2] = {1.0: m, 0.5: half, "full_bbox": m.getbbox(), "bbox": main_bbox(half)}
        return self.masks[iso2]

    def has_mask(self, iso2):
        return os.path.exists(os.path.join(GEO_DIR, "country_masks", f"{iso2.upper()}.png"))

    def shot_for(self, iso2, pad=2.4, min_w=900):
        """(center_x, center_y, window_w) framing a country."""
        x0, y0, x1, y1 = self.mask(iso2)["bbox"]
        return ((x0 + x1) / 2, (y0 + y1) / 2, min(6000, max(min_w, max(x1 - x0, y1 - y0) * pad)))

    @staticmethod
    def fly(a, b, k):
        """Interpolate shots a -> b; long trips rise (zoom out) mid-flight."""
        k = ease_io(k)
        cx = a[0] + (b[0] - a[0]) * k
        cy = a[1] + (b[1] - a[1]) * k
        dist = math.hypot(b[0] - a[0], b[1] - a[1])
        bump = math.log(max(1.0, dist * 1.1 / max(a[2], b[2])))
        logw = math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * k + bump * 4 * k * (1 - k)
        return cx, cy, math.exp(logw)

    def _window(self, shot):
        cx, cy, w = shot
        fu, fv = self.focus_tex
        left = cx - fu / self.tex * w
        top = cy - fv / self.tex * w
        return left, top, w

    def bake(self, tints):
        """Base map with flat country highlights burned in, at both resolutions.
        Done once per scene so a frame costs one map warp however many countries
        are highlighted. tints: [(iso2, rgb, alpha)] or [(iso2, rgb, alpha, flag)]
        -- with a flag (load_flag) the country is painted with it instead of rgb."""
        baked = {}
        for scale, img in self.base.items():
            out = img.copy()
            p = int(self.PAD * scale)
            span = int(SOURCE_W * scale)
            for iso2, rgb, alpha, *rest in tints:
                flag = rest[0] if rest else None
                masks = self.mask(iso2)
                m = masks[scale].point(lambda v, a=alpha: int(v * a))
                paint, at = rgb, (0, 0)
                fx0, fy0, fx1, fy1 = (int(v * scale) for v in masks["full_bbox"])
                if flag is not None and fx1 > fx0 and fy1 > fy0:  # flag over the country's own extent only
                    bx = [v * scale for v in masks["bbox"]]
                    paint = flag_fill(flag, (fx1 - fx0, fy1 - fy0),
                                      (bx[0] - fx0, bx[1] - fy0, bx[2] - fx0, bx[3] - fy0))
                    m, at = m.crop((fx0, fy0, fx1, fy1)), (fx0, fy0)
                for dx in (-span, 0, span):  # also into the wrap-around padding (Chukotka, Fiji)
                    out.paste(paint, (p + dx + at[0], p + at[1]), m)
            baked[scale] = out
        return baked

    def render(self, shot, base=None, lifts=(), beams=()):
        """shot: (cx, cy, w); base: from bake() (default: plain map).
        lifts: [(iso2, amount 0..1, rgb[, flag])] countries raised as lit slabs; with a
        flag (load_flag) its top surface is the flag and rgb colours the walls and rim.
        beams: [(iso2, amount, rgb[, flag])] light-beam markers for countries too small
        to see; a flag rides on top of the beam as a pin."""
        base = base or self.base
        left, top, w = self._window(shot)
        scale = 0.5 if w > 2600 else 1.0
        box = (left * scale, top * scale, (left + w) * scale, (top + w) * scale)
        p = self.PAD * scale
        padded = (box[0] + p, box[1] + p, box[2] + p, box[3] + p)
        tex = base[scale].transform((self.tex, self.tex), Image.EXTENT, padded, Image.BILINEAR).convert("RGBA")
        self._graticule(tex, left, top, w)
        k = self.tex / w
        for iso2, amount, rgb, *rest in lifts:
            if amount > 0:
                m = self.mask(iso2)[scale].transform((self.tex, self.tex), Image.EXTENT, box, Image.BILINEAR)
                x0, y0, x1, y1 = self.mask(iso2)["bbox"]
                main = ((x0 - left) * k, (y0 - top) * k, (x1 - left) * k, (y1 - top) * k)
                self._extrude(tex, m, amount, rgb, flag=rest[0] if rest else None, main=main)
        frame = self.bg.copy()
        frame.alpha_composite(tex.transform((self.w, self.h), Image.PERSPECTIVE, self.to_tex, Image.BILINEAR))
        frame.alpha_composite(self.fog)
        for iso2, amount, rgb, *rest in beams:
            if amount > 0:
                self._beam(frame, iso2, amount, left, top, w, rgb, flag=rest[0] if rest else None)
        return frame

    def project(self, iso2, shot):
        """Screen position of a country's centre for this shot."""
        left, top, w = self._window(shot)
        x0, y0, x1, y1 = self.mask(iso2)["bbox"]
        u = ((x0 + x1) / 2 - left) / w * self.tex
        v = ((y0 + y1) / 2 - top) / w * self.tex
        return apply_coeffs(self.to_screen, u, v)

    def _graticule(self, tex, left, top, w):
        d = ImageDraw.Draw(tex)
        step = SOURCE_W / 36  # 10 degrees
        k = self.tex / w
        x = left - left % step
        while x < left + w:
            d.line([((x - left) * k, 0), ((x - left) * k, self.tex)], fill=(90, 120, 165, 55), width=2)
            x += step
        y = top - top % step
        while y < top + w:
            d.line([(0, (y - top) * k), (self.tex, (y - top) * k)], fill=(90, 120, 165, 55), width=2)
            y += step

    def _extrude(self, tex, mask, amount, rgb=LAND_LIT, flag=None, main=None):
        """Raise the masked country as a lit slab (work only inside its bbox). With a
        flag, the top is the flag laid over `main` (its main landmass, texture px)."""
        bbox = mask.getbbox()
        if not bbox:
            return
        depth = int(26 * ease_back(amount))
        pad = 60 + depth
        x0, y0 = max(0, bbox[0] - pad), max(0, bbox[1] - pad)
        x1, y1 = min(self.tex, bbox[2] + pad), min(self.tex, bbox[3] + pad)
        region = tex.crop((x0, y0, x1, y1))
        m = mask.crop((x0, y0, x1, y1))
        a = min(1.0, amount * 1.5)
        shadow = Image.new("RGBA", region.size, (0, 0, 0, 0))
        shadow.putalpha(m.point(lambda v: int(v * 0.6 * a)))
        region.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)), (int(depth * 0.6), int(depth * 0.9)))
        for k in range(depth):  # side walls: a darker shade of the top colour
            shade = 0.38 + 0.25 * k / max(1, depth)
            region.paste(tuple(int(c * shade) for c in rgb) + (255,), (0, -k), m)
        glow = Image.new("RGBA", region.size, ACCENT + (0,))
        glow.putalpha(m.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * 0.5 * a)))
        region.alpha_composite(glow, (0, -depth))
        h, wd = region.size[1], region.size[0]
        if flag is not None and main is not None:
            # Softer light falloff than a flat colour, so the flag's colours read true.
            ramp = np.linspace(1.06, 0.86, h)[:, None] * np.linspace(1.04, 0.95, wd)[None, :]
            fill = flag_fill(flag, (wd, h), (main[0] - x0, main[1] - y0, main[2] - x0, main[3] - y0))
            top = np.clip(np.asarray(fill, dtype=np.float32) * ramp[..., None], 0, 255).astype(np.uint8)
        else:
            ramp = np.linspace(1.15, 0.82, h)[:, None] * np.linspace(1.08, 0.92, wd)[None, :]
            top = np.clip(np.stack([rgb[0] * ramp, rgb[1] * ramp, rgb[2] * ramp], -1), 0, 255).astype(np.uint8)
        region.paste(Image.fromarray(top).convert("RGBA"), (0, -depth), m)
        if flag is not None:  # thin rim in the slab colour keeps the outline crisp on pale flags
            rim = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(3)))
            region.paste(tuple(rgb) + (255,), (0, -depth), rim)
        tex.paste(region, (x0, y0))

    def _beam(self, frame, iso2, amount, left, top, w, rgb=ACCENT, flag=None):
        """Vertical light beam + ring for countries too small to see when raised;
        with a flag, the beam is a shorter flagpole flying it (kept low so it clears
        the cards and headers drawn above the map)."""
        x0, y0, x1, y1 = self.mask(iso2)["bbox"]
        u = ((x0 + x1) / 2 - left) / w * self.tex
        v = ((y0 + y1) / 2 - top) / w * self.tex
        sx, sy = apply_coeffs(self.to_screen, u, v)
        a = ease_out(amount)
        pole = (190 if flag is not None else 360) * a
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        r = 26 + 18 * a
        d.ellipse((sx - r, sy - r * 0.45, sx + r, sy + r * 0.45), outline=tuple(rgb) + (int(230 * a),), width=6)
        d.rectangle((sx - 7, sy - pole, sx + 7, sy), fill=tuple(rgb) + (int(170 * a),))
        frame.alpha_composite(layer.filter(ImageFilter.GaussianBlur(3)))
        frame.alpha_composite(layer)
        if flag is not None and a > 0.05:
            fw = max(2, int(120 * a))
            pin = flag.resize((fw, max(2, int(fw * flag.height / flag.width))), Image.BILINEAR).convert("RGBA")
            px, py = int(sx + 7), int(sy - pole)
            framed = Image.new("RGBA", (fw + 6, pin.height + 6), tuple(rgb) + (255,))
            framed.paste(pin, (3, 3))
            frame.alpha_composite(with_alpha(framed, a), (px - 3, py - 3))  # blends, never punches alpha

    def is_small(self, iso2, shot):
        x0, y0, x1, y1 = self.mask(iso2)["bbox"]
        return max(x1 - x0, y1 - y0) / shot[2] * self.tex < 60


# ---------------------------------------------------------------- rendering
_WORKER = {}


def _init_worker(scene_factory):
    _WORKER["factory"], _WORKER["key"] = scene_factory, None


def _render_task(task):
    key, args, t, alpha = task
    if _WORKER["key"] != key:  # first frame of a new clip on this worker
        _WORKER["scene"], _WORKER["key"] = _WORKER["factory"](*args), key
    frame = _WORKER["scene"](t)
    return (frame.convert("RGBA") if alpha else frame.convert("RGB")).tobytes()


def render_clips(jobs, size, scene_factory, processes=None, alpha=False):
    """Render every clip with ONE persistent pool (assets load once per worker,
    not once per clip). jobs: [(out_path, duration_seconds, factory_args)].
    scene_factory(*factory_args) must return scene(t) -> RGBA image.
    alpha=True keeps transparency (PNG-in-MOV) for overlays composited later."""
    tasks = [(k, args, i / FPS, alpha) for k, (_, dur, args) in enumerate(jobs)
             for i in range(max(1, int(round(dur * FPS))))]
    counts = [max(1, int(round(dur * FPS))) for _, dur, _ in jobs]
    with Pool(processes or os.cpu_count(), initializer=_init_worker, initargs=(scene_factory,)) as pool:
        frames = pool.imap(_render_task, tasks, chunksize=3)
        for (out_path, _, _), n in zip(jobs, counts):
            codec = (["-c:v", "png", "-pix_fmt", "rgba"] if alpha else
                     ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p"])
            ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo",
                                   "-pix_fmt", "rgba" if alpha else "rgb24",
                                   "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
                                   *codec, out_path], stdin=subprocess.PIPE)
            for _ in range(n):
                ff.stdin.write(next(frames))
            ff.stdin.close()
            if ff.wait() != 0:
                raise SystemExit(f"ffmpeg failed writing {out_path}")
            print(f"[motion_kit] wrote {os.path.basename(out_path)} ({n} frames)")


# ---------------------------------------------------------------- sound effects
SR = 44100


def _env(n, attack, decay):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-3)) * np.exp(-t / max(decay, 1e-3))


def sfx_whoosh(dur=0.8):
    n = int(dur * SR)
    noise = np.random.default_rng(7).normal(0, 1, n)
    sweep = np.convolve(noise, np.ones(40) / 40, mode="same")
    shape = np.sin(np.linspace(0, math.pi, n)) ** 2
    return 0.35 * sweep * shape / (np.abs(sweep).max() + 1e-9)


def sfx_pop():
    n = int(0.18 * SR)
    t = np.arange(n) / SR
    freq = 900 * np.exp(-t * 18) + 300
    return 0.45 * np.sin(2 * math.pi * np.cumsum(freq) / SR) * _env(n, 0.002, 0.05)


def sfx_ding():
    n = int(1.2 * SR)
    t = np.arange(n) / SR
    tone = sum(a * np.sin(2 * math.pi * f * t) for f, a in ((1320, 1), (2640, 0.35), (3960, 0.15)))
    return 0.22 * tone * _env(n, 0.003, 0.35)


def sfx_riser(dur=1.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    freq = 180 + 520 * (t / dur) ** 2
    noise = np.random.default_rng(3).normal(0, 0.25, n)
    return 0.22 * (np.sin(2 * math.pi * np.cumsum(freq) / SR) + noise) * (t / dur) ** 1.5


def sfx_impact():
    n = int(1.4 * SR)
    t = np.arange(n) / SR
    boom = np.sin(2 * math.pi * (55 + 40 * np.exp(-t * 8)) * t)
    return 0.6 * boom * _env(n, 0.004, 0.4)


def write_sfx_track(path, total_seconds, events):
    """events: [(start_seconds, samples)] mixed into one mono 44.1 kHz WAV."""
    import wave
    track = np.zeros(int((total_seconds + 2) * SR))
    for start, samples in events:
        i = int(max(0.0, start) * SR)
        j = min(len(track), i + len(samples))
        track[i:j] += samples[: j - i]
    peak = np.abs(track).max()
    if peak > 0.95:
        track *= 0.95 / peak
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((track * 32767).astype(np.int16).tobytes())
