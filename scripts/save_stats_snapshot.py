#!/usr/bin/env python3
"""
Save a snapshot of every country's comparison figures, so a World Bank outage can't stop a
video (on 2026-11-14 every per-country request timed out and the comparison Short failed).

One bulk request per indicator (all countries, most recent non-empty value) instead of ten
per-country requests per video. When the World Bank is down during the refresh, life
expectancy and population are filled from Our World in Data (same UN figures, free CSV).
Existing values are kept for anything neither source returns, so a bad day never empties it.

fetch_country_stats.get_stats() reads this when a live request fails.
Output: config/country_stats_snapshot.json  {"_saved": date, "IN": {"population": {...}}, ...}

Usage:
    python scripts/save_stats_snapshot.py
"""
import csv
import io
import json
import os
import sys
import time
from datetime import date

import requests

from fetch_country_stats import INDICATORS

SNAPSHOT_PATH = "config/country_stats_snapshot.json"
WB_BULK = "https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=400&mrnev=1"
# Our World in Data backups: (metric key, grapher csv, value column)
OWID = {
    "life_expectancy": ("https://ourworldindata.org/grapher/life-expectancy.csv?v=1&csvType=full&useColumnShortNames=true",
                        "life_expectancy_0"),
    "population": ("https://ourworldindata.org/grapher/population.csv?v=1&csvType=full&useColumnShortNames=true",
                   "population_historical"),
}
HEADERS = {"User-Agent": "yt-shorts-generator/1.0 (stats snapshot)"}


def _get(url, tries=3):
    for attempt in range(tries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == tries - 1:
                raise
            print(f"[snapshot] retrying after {type(exc).__name__}")
            time.sleep(5 * (attempt + 1))


def parse_worldbank(payload):
    """{iso2: (value, year)} from a bulk World Bank response."""
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return {}
    out = {}
    for row in payload[1]:
        iso2 = (row.get("country") or {}).get("id", "")
        if len(iso2) == 2 and row.get("value") is not None:
            out[iso2.upper()] = (float(row["value"]), int(row["date"]))
    return out


def parse_owid(text, column, iso3_to_iso2):
    """{iso2: (value, year)}, latest year per country, from an OWID grapher CSV."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        iso2 = iso3_to_iso2.get(row.get("code", ""))
        if not iso2 or not row.get(column):
            continue
        year, value = int(row["year"]), float(row[column])
        if year <= date.today().year and (iso2 not in out or year > out[iso2][1]):
            out[iso2] = (value, year)
    return out


def merge(snapshot, key, values, source):
    """Write values into the snapshot; return how many were written."""
    for iso2, (value, year) in values.items():
        snapshot.setdefault(iso2, {})[key] = {"value": value, "year": year, "source": source}
    return len(values)


def main():
    snapshot = {}
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
            snapshot = json.load(fh)
    iso3_to_iso2 = dict(snapshot.get("_iso3", {}))  # kept so OWID works even if the World Bank is down
    failed = []
    for key, code, _label, _unit in INDICATORS:
        try:
            payload = _get(WB_BULK.format(code=code)).json()
            values = parse_worldbank(payload)
            for row in payload[1] or []:
                c = row.get("country") or {}
                if row.get("countryiso3code") and len(c.get("id", "")) == 2:
                    iso3_to_iso2[row["countryiso3code"]] = c["id"].upper()
            print(f"[snapshot] {key}: {merge(snapshot, key, values, 'World Bank')} countries from the World Bank")
        except (requests.RequestException, ValueError) as exc:
            print(f"[snapshot] {key}: World Bank failed ({type(exc).__name__})")
            failed.append(key)
    for key in failed:
        if key in OWID and iso3_to_iso2:
            url, column = OWID[key]
            try:
                values = parse_owid(_get(url).text, column, iso3_to_iso2)
                print(f"[snapshot] {key}: {merge(snapshot, key, values, 'Our World in Data')} countries from OWID")
            except (requests.RequestException, ValueError) as exc:
                print(f"[snapshot] {key}: OWID failed too ({type(exc).__name__}); keeping saved values")
    snapshot["_saved"] = date.today().isoformat()
    snapshot["_iso3"] = iso3_to_iso2
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=1, sort_keys=True)
    print(f"[snapshot] saved {SNAPSHOT_PATH} ({sum(1 for k in snapshot if not k.startswith('_'))} countries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
