#!/usr/bin/env python3
"""
Where is "Lake Chad"? Coordinates for the geography series' feature labels, so the map
can drop a pin on the feature while the narration talks about it.

Source: Wikipedia's free API (search, then the article's own coordinates) -- no key.
A coordinate is only trusted if it lies in or right next to the country's own outline
(country mask), so a same-named place elsewhere in the world can never be pinned.
"""
import requests

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "Countries-Video-Flows geography pins (https://github.com/oceanfarm1992-design/Countries-Video-Flows)"}
SKIP_WORDS = ("follow", "comment", "subscribe", "like", "share")
NEAR_PX = 60          # source px (~2.6 degrees): rivers and ranges cross borders


def _query(params):
    r = requests.get(API, params={"format": "json", **params}, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


GENERIC = {"region", "regions", "area", "northern", "southern", "eastern", "western", "central",
           "the", "of", "and", "patterns", "loss", "zone", "basin"}


def _coords(title):
    pages = _query({"action": "query", "prop": "coordinates", "titles": title,
                    "redirects": 1})["query"]["pages"]
    for page in pages.values():
        if page.get("coordinates"):
            c = page["coordinates"][0]
            return page.get("title", title), (c["lon"], c["lat"])
    return None, None


def lookup(label, country):
    """(lon, lat) of `label`'s own Wikipedia article, else of the best search hit that
    is really about it -- never the country's own article (its centroid is not the
    feature: "Lake Chad" once resolved to the middle of Chad)."""
    words = {w for w in label.lower().replace("-", " ").split()
             if len(w) > 3 and w not in GENERIC and w not in country.lower()}
    if not words:
        return None
    candidates = [label] + [h["title"] for h in _query(
        {"action": "query", "list": "search", "srsearch": f"{label} {country}", "srlimit": 3})["query"]["search"]]
    for title in candidates:
        resolved, coords = _coords(title)
        if not coords or resolved.lower() == country.lower():
            continue
        if words & set(resolved.lower().replace("-", " ").replace("(", " ").split()):
            return coords
    return None


def pinnable(label, country):
    low = (label or "").strip().lower()
    return bool(low) and "!" not in low and low != country.lower() and not any(w in low for w in SKIP_WORDS)


def near_country(mask, lon, lat, source_w=8192, source_h=4096, near=NEAR_PX):
    """True if (lon, lat) falls on or within `near` px of the country mask (full-res L image)."""
    x, y = (lon + 180.0) / 360.0 * source_w, (90.0 - lat) / 180.0 * source_h
    box = (int(max(0, x - near)), int(max(0, y - near)), int(min(source_w, x + near)), int(min(source_h, y + near)))
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    return mask.crop(box).getbbox() is not None


def find_pins(segments, country, mask):
    """{segment index: (source_x, source_y)} for the labels that can be placed."""
    pins, cache = {}, {}
    for i, seg in enumerate(segments):
        label = (seg.get("visual") or "").strip()
        if not pinnable(label, country):
            continue
        try:
            if label not in cache:
                cache[label] = lookup(label, country)
        except Exception as exc:  # noqa: BLE001 -- a pin is decoration; never fails the render
            print(f"[feature_pins] lookup failed for {label!r} ({type(exc).__name__})")
            cache[label] = None
        hit = cache[label]
        if hit and near_country(mask, *hit):
            pins[i] = ((hit[0] + 180.0) / 360.0 * 8192, (90.0 - hit[1]) / 180.0 * 4096)
            print(f"[feature_pins] {label}: {hit[1]:.2f}, {hit[0]:.2f}")
        elif hit:
            print(f"[feature_pins] {label}: Wikipedia's match is outside {country} -- no pin")
    return pins
