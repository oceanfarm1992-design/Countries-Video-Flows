#!/usr/bin/env python3
"""
Check every curated fact in config/india_places.json against Wikipedia.

A fact passes when one of its `check` strings appears in the plain text of the fact's
article (`page` on the fact, else the place's `page`). Numbers are compared after
removing thousands separators and non-breaking spaces. Exit 1 if any fact fails, so a
wrong or misremembered number never reaches a script.

Usage:
    python scripts/verify_india_facts.py [--config config/india_places.json] [--place gurez]
"""
import argparse
import json
import re
import sys
import time

import requests

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "india-facts-check/1.0 (personal pipeline)"}


def _norm(text):
    text = (text or "").replace(" ", " ").replace(" ", " ").lower()
    return re.sub(r"(?<=\d),(?=\d{3})", "", text)


def page_text(title, session=requests, tries=5):
    params = {"action": "query", "prop": "extracts", "explaintext": 1,
              "redirects": 1, "format": "json", "titles": title}
    for attempt in range(tries):
        resp = session.get(API, params=params, headers=HEADERS, timeout=30)
        if resp.status_code != 429 or attempt == tries - 1:
            break
        time.sleep(5 * (attempt + 1))  # Wikipedia rate-limits bursts
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    return " ".join(p.get("extract", "") for p in pages.values())


def check_fact(fact, text):
    """True when any of the fact's check strings occurs in the (normalised) article text."""
    hay = _norm(text)
    return any(_norm(c) in hay for c in fact["check"])


def verify_place(place, fetch=page_text):
    """[(fact text, passed)] for every fact of one place."""
    cache, out = {}, []
    for fact in place["facts"]:
        title = fact.get("page") or place["page"]
        if title not in cache:
            cache[title] = fetch(title)
        out.append((fact["text"], check_fact(fact, cache[title])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="config/india_places.json")
    ap.add_argument("--place", default=None, help="only this place id")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as fh:
        places = json.load(fh)["places"]
    bad = 0
    for place in places:
        if args.place and place["id"] != args.place:
            continue
        for text, ok in verify_place(place):
            if not ok:
                bad += 1
            print(f"{'ok  ' if ok else 'FAIL'} {place['name']}: {text}")
    print(f"[verify_india_facts] {bad} fact(s) not found in Wikipedia")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
