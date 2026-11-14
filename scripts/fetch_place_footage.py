#!/usr/bin/env python3
"""
Footage for the India channel that is CONFIRMED to show the place being narrated.

A "this is a real place in India" video must not show another valley. Stock search alone
showed the wrong place (Khajjiar dry run: 0 of 10 pieces confirmed). Every piece used here
is confirmed one of two ways:
  * Wikimedia Commons: files in the place's own category, or whose file name names the
    place. Free licences only (public domain, CC0, CC BY, CC BY-SA); the authors are
    credited in the video descriptions, as those licences require.
  * Pexels / Pixabay: only clips whose own title or tags name the place.
Videos come first (they carry the drone feel), then photos (slow zoom in assembly). With too
few confirmed pieces it exits 3, and the workflow moves on to another place for the day.

Output: build/footage_clip<N>.mp4|jpg + build/footage.json (same contract as fetch_footage.py),
and a credits line appended to build/caption_*.txt.

Usage:
    python scripts/fetch_place_footage.py [--out build] [--places config/india_places.json]
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import time

import requests

import fetch_footage as stock

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "india-place-footage/1.0 (personal video pipeline)"}
FREE_LICENSE = re.compile(r"^(cc0|public domain|pd\b|cc by(-sa)? \d)", re.I)
MEDIA_EXT = re.compile(r"\.(jpe?g|png|webm|ogv)$", re.I)
SKIP_WORDS = re.compile(r"\b(map|logo|flag|diagram|chart|locator|seal|emblem|coat of arms)\b", re.I)
GENERIC = {"valley", "lake", "beach", "the", "of", "national", "park", "tso"}
SCENIC = re.compile(r"aerial|drone|panoram|view|valley|lake|river|mountain|peak|landscape|meadow|sunset|sunrise|beach|snow", re.I)
NOT_SCENIC = re.compile(r"house|home|mosque|temple|church|school|shop|market|road|street|people|man|woman|boy|girl|portrait|office|building|bus|vehicle", re.I)
MIN_PIECES = 4          # fewer distinct confirmed pieces than this -> try another place
MIN_PHOTO_WIDTH = 1000
VIDEO_SECONDS = 20
EXIT_NOT_ENOUGH = 3


def place_terms(place):
    """Words that identify the place itself in a file name or a stock clip's tags
    ("Gurez Valley" -> ["gurez"]; "Valley of Flowers" -> ["valley of flowers"])."""
    terms = set()
    for name in (place["name"], place.get("page", "")):
        words = [w for w in re.findall(r"[a-z]+", name.lower()) if w not in GENERIC]
        terms.update(words if words else [name.lower()])
    return sorted(terms)


def names_place(text, terms):
    low = (text or "").lower()
    return any(t in low for t in terms)


def _api(params, tries=4):
    for attempt in range(tries):
        try:
            resp = requests.get(COMMONS_API, params={**params, "format": "json"},
                                headers=HEADERS, timeout=30)
            return resp.json()
        except (requests.RequestException, ValueError):  # Commons rate-limits bursts with HTML
            time.sleep(3 * (attempt + 1))
    return {}


def commons_titles(place, terms):
    """File titles in the place's category, plus searched files whose name names the place."""
    titles = []
    for cat in dict.fromkeys([place.get("page"), place["name"]]):
        if cat:
            data = _api({"action": "query", "list": "categorymembers", "cmtitle": f"Category:{cat}",
                         "cmtype": "file", "cmlimit": 200})
            titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
    data = _api({"action": "query", "list": "search", "srsearch": place["name"],
                 "srnamespace": 6, "srlimit": 100})
    titles += [m["title"] for m in data.get("query", {}).get("search", [])
               if names_place(m["title"], terms)]
    return [t for t in dict.fromkeys(titles) if MEDIA_EXT.search(t) and not SKIP_WORDS.search(t)]


def _clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", value or ""))).strip()


def commons_media(titles):
    """Usable files with url, licence and author, from imageinfo in batches of 50."""
    out = []
    for i in range(0, len(titles), 50):
        data = _api({"action": "query", "titles": "|".join(titles[i:i + 50]), "prop": "imageinfo",
                     "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 1440})
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            licence = _clean(meta.get("LicenseShortName", {}).get("value"))
            if not FREE_LICENSE.match(licence):
                continue
            is_video = info.get("mime", "").startswith("video/")
            if not is_video and (info.get("width") or 0) < MIN_PHOTO_WIDTH:
                continue
            out.append({"title": page["title"], "type": "video" if is_video else "photo",
                        "url": info.get("url") if is_video else (info.get("thumburl") or info.get("url")),
                        "page_url": info.get("descriptionurl", ""), "license": licence,
                        "author": _clean(meta.get("Artist", {}).get("value"))[:80] or "unknown"})
    return out


def scenic_rank(item):
    """Sort key: videos, then titles that read like scenery, then the rest."""
    title = item["title"]
    return (item["type"] != "video", not SCENIC.search(title), bool(NOT_SCENIC.search(title)))


def _to_mp4(src, dest):
    """Commons videos are WebM/Ogg: cut the first seconds into an H.264 file."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-t", str(VIDEO_SECONDS), "-an",
                    "-vf", "scale=-2:1080", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", dest],
                   check=True, timeout=300)


def download_commons(item, dest_base):
    if item["type"] == "photo":
        dest = dest_base + ".jpg"
        stock._download(item["url"], dest, headers=HEADERS)
        return dest
    raw = dest_base + ".src"
    stock._download(item["url"], raw, headers=HEADERS)
    dest = dest_base + ".mp4"
    try:
        _to_mp4(raw, dest)
    finally:
        os.remove(raw)
    return dest


def stock_clips(place, terms, out_dir, want):
    """Pexels / Pixabay videos whose own title or tags name the place."""
    found, seen = [], set()
    stock.EXTRA_MATCH_TERMS[:] = []
    for query in place["footage"]:
        for fetch in (stock.fetch_pexels, stock.fetch_pixabay):
            if len(found) >= want:
                return found
            dest = os.path.join(out_dir, f"stock_{len(found)}.mp4")
            try:
                if fetch is stock.fetch_pexels:
                    info = fetch(query, dest, False, 720, terms[0], require_match=True)
                else:
                    info = fetch(query, dest, 720, terms[0], require_match=True)
            except Exception as exc:  # noqa: BLE001 -- one source failing must not stop the rest
                print(f"[place_footage] {fetch.__name__} failed: {type(exc).__name__}: {exc}")
                info = None
            if info and info.get("country_verified") and info["source_url"] not in seen:
                seen.add(info["source_url"])
                found.append((info, dest))
    return found


def credits_line(pieces):
    """'Footage: A (CC BY-SA 4.0); B (Pexels)' for the captions, one entry per author."""
    seen, parts = set(), []
    for p in pieces:
        if p["source"] == "commons":
            label = f"{p['author']} ({p['license']}, Wikimedia Commons)"
        else:
            label = p.get("attribution", p["source"])
        if label not in seen:
            seen.add(label)
            parts.append(label)
    return "Footage and photos: " + "; ".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build")
    ap.add_argument("--places", default="config/india_places.json")
    args = ap.parse_args()

    with open(os.path.join(args.out, "script.json"), encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.places, encoding="utf-8") as fh:
        place = next(p for p in json.load(fh)["places"] if p["id"] == script["id"])
    terms = place_terms(place)
    segments = script.get("segments") or [{}]
    for pat in ("footage_clip", "stock_"):
        for f in os.listdir(args.out):
            if f.startswith(pat):
                os.remove(os.path.join(args.out, f))

    pieces = []
    for info, path in stock_clips(place, terms, args.out, want=3):
        pieces.append(dict(info, type="video", path=path))
    media = commons_media(commons_titles(place, terms))
    media.sort(key=scenic_rank)  # videos first, then wide scenery, buildings and people last
    for item in media:
        if len(pieces) >= len(segments):
            break
        try:
            path = download_commons(item, os.path.join(args.out, f"commons_{len(pieces)}"))
        except Exception as exc:  # noqa: BLE001
            print(f"[place_footage] skipped {item['title']}: {type(exc).__name__}")
            continue
        pieces.append({"source": "commons", "type": item["type"], "path": path, "license": item["license"],
                       "author": item["author"], "source_url": item["page_url"], "country_verified": True})

    print(f"[place_footage] {place['name']}: {len(pieces)} confirmed pieces "
          f"({sum(p['type'] == 'video' for p in pieces)} video) for {len(segments)} beats")
    if len(pieces) < MIN_PIECES:
        print(f"[place_footage] fewer than {MIN_PIECES} confirmed pieces -- choose another place")
        return EXIT_NOT_ENOUGH

    clips = []
    for i, _seg in enumerate(segments):  # cycle the confirmed pieces over the beats
        p = pieces[i % len(pieces)]
        ext = os.path.splitext(p["path"])[1]
        dest = os.path.join(args.out, f"footage_clip{i}{ext}")
        with open(p["path"], "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
        clips.append({k: v for k, v in p.items() if k != "path"} | {"path": os.path.basename(dest),
                                                                      "query": place["name"]})
    for p in pieces:
        if os.path.exists(p["path"]):
            os.remove(p["path"])
    with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as fh:
        json.dump({"clip_count": len(clips), "clips": clips, "source": "confirmed-place",
                   "identifier": place["id"]}, fh, indent=2, ensure_ascii=False)

    line = credits_line(pieces)
    for name in ("caption_youtube.txt", "caption_meta.txt", "caption_tiktok.txt"):
        path = os.path.join(args.out, name)
        if os.path.exists(path):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n" + line + "\n")
    print(f"[place_footage] {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
