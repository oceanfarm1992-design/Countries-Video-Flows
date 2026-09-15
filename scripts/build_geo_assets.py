#!/usr/bin/env python3
"""
ONE-TIME, OFFLINE, DEV-ONLY tool. Not part of the CI pipeline and never imported
by any runtime script — run this locally whenever the country list or the source
boundary data changes, then commit the generated PNGs.

Rasterizes Natural Earth's public-domain admin-0 country polygons into:
  - assets/geo/basemap_world.png       a flat cartographic world map (land/ocean
                                        fill, no photographic texture) covering
                                        every Natural Earth feature.
  - assets/geo/country_masks/{iso2}.png  one alpha mask per country in
                                        config/countries.json, white where that
                                        country's territory is, black elsewhere.

Both share the exact same equirectangular pixel space (OUT_W x OUT_H), so the
runtime renderer (scripts/render_geography_map.py) can composite them with a
plain alpha paste — no resizing/reprojection needed at render time.

Uses pure Pillow (no shapely/geopandas/GDAL) — see the plan doc for why. Each
polygon is rasterized within its OWN local bounding box (not one shared bbox
per country), which is what makes this correct for antimeridian-crossing
countries (Russia, Fiji, ...) with zero special-case unwrapping: Natural Earth
already pre-splits those into separate polygons on each side of the dateline,
confirmed empirically (no single ring in this dataset spans >180deg longitude).
Holes (enclaves like Lesotho-in-South-Africa) are handled by filling exterior
rings white and hole rings black, in ring order, on the same local canvas.

Data source: Natural Earth 1:50m admin-0 countries (public domain), via the
well-known nvkelso/natural-earth-vector GitHub mirror. Not fetched automatically
by this script (keeps it network-free and reproducible) -- download once:
    curl -sL -o /tmp/ne_50m_admin_0_countries.geojson \\
        https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson

Usage:
    python scripts/build_geo_assets.py --geojson /tmp/ne_50m_admin_0_countries.geojson
    python scripts/build_geo_assets.py --geojson ... --only US,RU,LU  # test a few
"""
import argparse
import json
import os

from PIL import Image, ImageDraw, ImageFilter
import PIL.ImageChops as ImageChops

# Both the basemap and every country mask share this pixel space.
OUT_W, OUT_H = 8192, 4096

# Per-polygon local supersample factor (affordable since each polygon is
# rasterized in its own tight bounding box, not the whole world canvas).
SUPERSAMPLE = 4
PAD_PX = 3  # local-canvas padding so anti-aliased edges don't get clipped

LAND_COLOR = (58, 74, 62)       # muted moss-green land fill
OCEAN_COLOR = (16, 28, 40)      # dark desaturated navy ocean
COASTLINE_COLOR = (110, 130, 118)


def lonlat_to_px(lon, lat, w, h):
    x = (lon + 180.0) / 360.0 * w
    y = (90.0 - lat) / 180.0 * h
    return x, y


def iter_polygons(geometry):
    """Yield (exterior_ring, [hole_rings, ...]) tuples of (lon, lat) point lists
    from a GeoJSON Polygon or MultiPolygon geometry."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    polys = coords if gtype == "MultiPolygon" else [coords]
    for poly in polys:
        if not poly:
            continue
        exterior = [(pt[0], pt[1]) for pt in poly[0]]
        holes = [[(pt[0], pt[1]) for pt in ring] for ring in poly[1:]]
        yield exterior, holes


def rasterize_polygon_local(exterior, holes, out_w, out_h, supersample=SUPERSAMPLE):
    """Rasterize ONE polygon (with its holes) into a small supersampled local
    canvas sized to just its own bounding box, then downsample for anti-aliasing.
    Returns (patch_L_image, (x0, y0)) where (x0, y0) is the patch's top-left
    offset in the shared out_w x out_h pixel space, or None if the polygon has
    no area (degenerate ring)."""
    px_points = [lonlat_to_px(lon, lat, out_w, out_h) for lon, lat in exterior]
    xs = [p[0] for p in px_points]
    ys = [p[1] for p in px_points]
    x0, x1 = max(0, int(min(xs)) - PAD_PX), min(out_w, int(max(xs)) + PAD_PX + 1)
    y0, y1 = max(0, int(min(ys)) - PAD_PX), min(out_h, int(max(ys)) + PAD_PX + 1)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None

    sw, sh = w * supersample, h * supersample
    canvas = Image.new("L", (sw, sh), 0)
    draw = ImageDraw.Draw(canvas)

    def to_local(ring):
        out = []
        for lon, lat in ring:
            px, py = lonlat_to_px(lon, lat, out_w, out_h)
            out.append(((px - x0) * supersample, (py - y0) * supersample))
        return out

    ext_local = to_local(exterior)
    if len(ext_local) >= 3:
        draw.polygon(ext_local, fill=255)
    for hole in holes:
        hole_local = to_local(hole)
        if len(hole_local) >= 3:
            draw.polygon(hole_local, fill=0)

    patch = canvas.resize((w, h), Image.LANCZOS)
    return patch, (x0, y0)


def paint_polygons(canvas_img, geometry):
    """Rasterize every polygon of a geometry onto canvas_img (mode "L"), taking
    the brighter value at each pixel so overlapping polygons never erase each
    other (each part of a multi-island country is painted independently)."""
    for exterior, holes in iter_polygons(geometry):
        result = rasterize_polygon_local(exterior, holes, canvas_img.width, canvas_img.height)
        if result is None:
            continue
        patch, (x0, y0) = result
        region = canvas_img.crop((x0, y0, x0 + patch.width, y0 + patch.height))
        merged = ImageChops.lighter(region, patch)
        canvas_img.paste(merged, (x0, y0))


def build_country_mask(features, out_w=OUT_W, out_h=OUT_H):
    mask = Image.new("L", (out_w, out_h), 0)
    for feat in features:
        paint_polygons(mask, feat["geometry"])
    return mask


def build_world_basemap(all_features, out_w=OUT_W, out_h=OUT_H):
    """Flat cartographic basemap: every Natural Earth land feature filled, over
    a dark ocean base, with a soft coastline stroke. No photographic texture, so
    small countries stay crisp under a tight zoom instead of turning into a
    blurred JPEG smear (see plan doc)."""
    land_mask = Image.new("L", (out_w, out_h), 0)
    for feat in all_features:
        paint_polygons(land_mask, feat["geometry"])

    base = Image.new("RGB", (out_w, out_h), OCEAN_COLOR)
    land_layer = Image.new("RGB", (out_w, out_h), LAND_COLOR)
    base.paste(land_layer, (0, 0), land_mask)

    # Thin coastline: the ring of pixels where the land mask's edge is, drawn in
    # a slightly lighter tone so coastlines read clearly at a distance.
    dilated = land_mask.filter(ImageFilter.MaxFilter(5))
    eroded = land_mask.filter(ImageFilter.MinFilter(5))
    edge_ring = ImageChops.subtract(dilated, eroded)
    coastline_layer = Image.new("RGB", (out_w, out_h), COASTLINE_COLOR)
    base.paste(coastline_layer, (0, 0), edge_ring)

    return base


def load_countries_config(path="config/countries.json"):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    return [(c["id"], c["name"], c["iso2"].upper()) for c in cfg["countries"]]


def load_ne_features(geojson_path):
    with open(geojson_path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["features"]


def ne_iso2(props):
    """Natural Earth's ISO_A2 field is '-99' (or a non-standard code, e.g.
    Taiwan's 'CN-TW') for several real countries due to disputed-sovereignty
    encoding choices; ISO_A2_EH ("economic/geographic" ISO2) is the reliable
    field -- confirmed by cross-checking against all 195 of our countries."""
    iso2 = (props.get("ISO_A2_EH") or "").upper()
    if not iso2 or iso2 == "-99":
        iso2 = (props.get("ISO_A2") or "").upper()
    return iso2 if iso2 and iso2 != "-99" else None


def main():
    ap = argparse.ArgumentParser(description="Generate map/mask assets for the geography series.")
    ap.add_argument("--geojson", required=True,
                    help="Path to a downloaded Natural Earth admin-0 countries GeoJSON file.")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out-dir", default="assets/geo")
    ap.add_argument("--only", default=None,
                    help="Comma-separated ISO2 codes to build (test subset); default: all.")
    ap.add_argument("--skip-basemap", action="store_true",
                    help="Skip the (slower) world basemap render, masks only.")
    args = ap.parse_args()

    countries = load_countries_config(args.config)
    if args.only:
        wanted = {c.strip().upper() for c in args.only.split(",")}
        countries = [c for c in countries if c[2] in wanted]
        if not countries:
            raise SystemExit(f"--only={args.only!r} matched no countries in {args.config}")

    print(f"[build_geo_assets] loading {args.geojson} ...")
    features = load_ne_features(args.geojson)
    by_iso = {}
    for feat in features:
        iso2 = ne_iso2(feat.get("properties", {}))
        if iso2:
            by_iso.setdefault(iso2, []).append(feat)
    print(f"[build_geo_assets] {len(features)} Natural Earth features -> {len(by_iso)} ISO2 codes")

    masks_dir = os.path.join(args.out_dir, "country_masks")
    os.makedirs(masks_dir, exist_ok=True)

    ok, skipped = 0, []
    for country_id, name, iso2 in countries:
        feats = by_iso.get(iso2)
        if not feats:
            skipped.append((country_id, name, iso2, "no matching Natural Earth feature"))
            continue
        try:
            mask = build_country_mask(feats)
            out_path = os.path.join(masks_dir, f"{iso2}.png")
            mask.save(out_path)
            bbox = mask.getbbox()
            if bbox is None:
                skipped.append((country_id, name, iso2, "rasterized to an empty mask"))
                continue
            print(f"[build_geo_assets] {iso2} ({name}): OK -> {out_path} bbox={bbox}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 — one bad geometry must not abort the batch
            skipped.append((country_id, name, iso2, f"{type(exc).__name__}: {exc}"))

    print(f"\n[build_geo_assets] {ok}/{len(countries)} country masks generated")
    if skipped:
        print(f"[build_geo_assets] {len(skipped)} skipped:")
        for country_id, name, iso2, reason in skipped:
            print(f"  - {iso2} ({name}): {reason}")

    if not args.skip_basemap:
        print("[build_geo_assets] rendering world basemap (this is the slow step) ...")
        basemap = build_world_basemap(features)
        basemap_path = os.path.join(args.out_dir, "basemap_world.png")
        basemap.save(basemap_path)
        print(f"[build_geo_assets] basemap -> {basemap_path} ({basemap.size[0]}x{basemap.size[1]})")


if __name__ == "__main__":
    main()
