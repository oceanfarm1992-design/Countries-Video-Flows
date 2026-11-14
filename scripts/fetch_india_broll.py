#!/usr/bin/env python3
"""
Real Indian background footage for the India Rising charts: ports, ships, factories, skylines,
power lines... chosen by the measure being charted (script.json "broll" queries).

Authentic only: a stock clip counts when its own title or tags name India or an Indian
city or state; Wikimedia Commons photos count when their file name does (free licences,
authors credited). Videos first. The credits line is appended to the captions.

Output: build/broll_<N>.mp4|jpg + build/broll.json [{path, type, source, ...}]
Exit 0 even with nothing found: the chart then keeps its plain background.

Usage:
    python scripts/fetch_india_broll.py [--out build] [--want 6]
"""
import argparse
import json
import os
import re
import sys

import fetch_footage as stock
import fetch_place_footage as place

INDIA_TERMS = ["india", "indian", "mumbai", "bombay", "delhi", "kolkata", "chennai", "bangalore",
               "bengaluru", "hyderabad", "pune", "ahmedabad", "gujarat", "kerala", "goa", "jaipur",
               "rajasthan", "varanasi", "kochi", "surat", "lucknow", "punjab", "tamil nadu",
               "maharashtra", "karnataka", "odisha", "assam", "himalaya", "ladakh", "kashmir"]


def names_india(text):
    low = (text or "").lower()
    return any(re.search(rf"\b{re.escape(t)}\b", low) for t in INDIA_TERMS)


def stock_videos(queries, out_dir, want):
    """Stock clips whose own slug/tags name India or an Indian place."""
    found, seen = [], set()
    stock.EXTRA_MATCH_TERMS[:] = INDIA_TERMS[1:]
    for query in queries:
        for fetch in (stock.fetch_pexels, stock.fetch_pixabay):
            if len(found) >= want:
                return found
            dest = os.path.join(out_dir, f"broll_tmp_{len(found)}.mp4")
            try:
                if fetch is stock.fetch_pexels:
                    info = fetch(query, dest, False, 720, "India", require_match=True)
                else:
                    info = fetch(query, dest, 720, "India", require_match=True)
            except Exception as exc:  # noqa: BLE001 -- one source failing must not stop the rest
                print(f"[india_broll] {fetch.__name__} failed: {type(exc).__name__}")
                info = None
            if info and info.get("country_verified") and info["source_url"] not in seen:
                seen.add(info["source_url"])
                found.append(dict(info, type="video", path=dest))
    return found


def commons_photos(queries, out_dir, want, start_index):
    """Commons photos whose file names name India or an Indian place."""
    titles = []
    for query in queries:
        data = place._api({"action": "query", "list": "search", "srsearch": query,
                           "srnamespace": 6, "srlimit": 50})
        titles += [m["title"] for m in data.get("query", {}).get("search", [])
                   if names_india(m["title"]) and place.MEDIA_EXT.search(m["title"])
                   and not place.SKIP_WORDS.search(m["title"])]
    found = []
    for item in place.commons_media(list(dict.fromkeys(titles))):
        if len(found) >= want or item["type"] != "photo":
            continue
        try:
            path = place.download_commons(item, os.path.join(out_dir, f"broll_tmp_{start_index + len(found)}"))
        except Exception as exc:  # noqa: BLE001
            print(f"[india_broll] skipped {item['title']}: {type(exc).__name__}")
            continue
        found.append({"source": "commons", "type": "photo", "path": path, "license": item["license"],
                      "author": item["author"], "source_url": item["page_url"]})
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build")
    ap.add_argument("--want", type=int, default=6)
    args = ap.parse_args()
    with open(os.path.join(args.out, "script.json"), encoding="utf-8") as fh:
        queries = json.load(fh).get("broll") or []
    for f in os.listdir(args.out):
        if f.startswith("broll"):
            os.remove(os.path.join(args.out, f))
    pieces = stock_videos(queries, args.out, args.want)
    if len(pieces) < args.want:
        pieces += commons_photos(queries, args.out, args.want - len(pieces), len(pieces))
    final = []
    for i, p in enumerate(pieces):
        ext = os.path.splitext(p["path"])[1]
        dest = os.path.join(args.out, f"broll_{i}{ext}")
        os.replace(p["path"], dest)
        final.append({k: v for k, v in p.items() if k != "path"} | {"path": os.path.basename(dest)})
    with open(os.path.join(args.out, "broll.json"), "w", encoding="utf-8") as fh:
        json.dump(final, fh, indent=2, ensure_ascii=False)
    print(f"[india_broll] {len(final)} confirmed Indian pieces "
          f"({sum(p['type'] == 'video' for p in final)} video) for: {', '.join(queries)}")
    if final:
        line = place.credits_line(final)
        for name in ("caption_youtube.txt", "caption_meta.txt", "caption_tiktok.txt"):
            path = os.path.join(args.out, name)
            if os.path.exists(path):
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write("\n" + line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
