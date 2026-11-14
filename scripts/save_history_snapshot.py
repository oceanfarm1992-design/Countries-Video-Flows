#!/usr/bin/env python3
"""
Save the World Bank's year-by-year history for every indicator the videos use, so a World Bank
outage can't stop a video that needs a trend (long-form "decade change", India Rising).

One bulk request per indicator (every country, every year since 2000). Countries come from
config/countries.json, plus "1W" (the World average) for "India vs the world average" lines.
An indicator that fails keeps its previously saved file, so a bad day never loses data.

Output: config/wb_history/<INDICATOR>.json  {"_saved": date, "series": {iso2: [[year, value], ...]}}
Read by fetch_rankings_stats.get_series() when the live request fails, and by the India series.

Usage:
    python scripts/save_history_snapshot.py [--only NY.GDP.MKTP.CD]
"""
import argparse
import json
import os
import sys
import time
from datetime import date

import requests

HISTORY_DIR = "config/wb_history"
START_YEAR = 2000
URL = "https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=20000&date={start}:{end}"
HEADERS = {"User-Agent": "yt-shorts-generator/1.0 (history snapshot)"}
WORLD = "1W"
# Extra indicators for the India series ("how India is developing").
INDIA_RISING = {
    "EG.ELC.ACCS.ZS": "Access to electricity (% of people)",
    "IT.NET.USER.ZS": "Internet users (% of people)",
    "SI.POV.DDAY": "Extreme poverty (% living on under $3 a day)",
    "SE.ADT.LITR.ZS": "Adult literacy rate (%)",
    "NY.GNP.PCAP.CD": "Income per person (US$)",
    "IT.CEL.SETS.P2": "Mobile subscriptions per 100 people",
    "SH.H2O.BASW.ZS": "Basic drinking water (% of people)",
    "SP.DYN.IMRT.IN": "Infant deaths per 1,000 births",
}


def indicators():
    """Every live indicator in the rankings configs, plus the India series' ones."""
    codes = set(INDIA_RISING)
    for path in ("config/rankings_metrics.json", "config/rankings_negative_metrics.json"):
        with open(path, encoding="utf-8") as fh:
            codes |= {m["wb_indicator"] for m in json.load(fh)["metrics"] if m.get("source_type") == "live"}
    return sorted(codes)


def known_countries():
    with open("config/countries.json", encoding="utf-8") as fh:
        return {c["iso2"].upper() for c in json.load(fh)["countries"] if c.get("iso2")} | {WORLD}


def parse(payload, keep):
    """{iso2: [[year, value], ...]} oldest first, only for countries in `keep`."""
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return {}
    series = {}
    for row in payload[1]:
        iso2 = (row.get("country") or {}).get("id", "").upper()
        if row.get("value") is None or iso2 not in keep:
            continue
        series.setdefault(iso2, []).append([int(row["date"]), round(float(row["value"]), 6)])
    return {k: sorted(v) for k, v in sorted(series.items())}


def path_for(code):
    return os.path.join(HISTORY_DIR, f"{code}.json")


def load(code):
    """{iso2: [(year, value), ...]} from the saved file, or {}."""
    try:
        with open(path_for(code), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {iso2: [tuple(p) for p in pts] for iso2, pts in data.get("series", {}).items()}


def fetch(code, keep, tries=3):
    for attempt in range(tries):
        try:
            resp = requests.get(URL.format(code=code, start=START_YEAR, end=date.today().year),
                                headers=HEADERS, timeout=90)
            resp.raise_for_status()
            return parse(resp.json(), keep)
        except (requests.RequestException, ValueError):
            if attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=None, help="just this indicator")
    args = ap.parse_args()
    os.makedirs(HISTORY_DIR, exist_ok=True)
    keep = known_countries()
    failed = []
    for code in ([args.only] if args.only else indicators()):
        try:
            series = fetch(code, keep)
        except (requests.RequestException, ValueError) as exc:
            print(f"[history] {code}: failed ({type(exc).__name__}) -- keeping the saved file")
            failed.append(code)
            continue
        if not series:
            print(f"[history] {code}: empty answer -- keeping the saved file")
            failed.append(code)
            continue
        with open(path_for(code), "w", encoding="utf-8") as fh:
            json.dump({"_saved": date.today().isoformat(), "series": series}, fh, separators=(",", ":"))
        print(f"[history] {code}: {len(series)} countries")
    print(f"[history] done, {len(failed)} kept from before: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
