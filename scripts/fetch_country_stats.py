#!/usr/bin/env python3
"""
Shared helper for the "comparison" series: real per-country statistics from the
World Bank's public API (free, no key required) -- never invented by GPT. Every
other series that cites numbers restricts itself to numbers already curated in
config/countries.json (see worlddata's "stats" sub-type); a country-vs-country
comparison needs consistent, comparable figures across ~195 countries, which
nobody has hand-curated here, so this fetches them from a primary source
instead of ever asking a language model to supply one.

Indicators (World Bank codes), in the order the comparison table prefers them:
    gdp_per_capita   NY.GDP.PCAP.CD       GDP per Capita (current US$)
    income           NY.GNP.PCAP.CD       Income per Person (GNI/capita, Atlas method, US$)
    literacy         SE.ADT.LITR.ZS       Adult Literacy Rate (%)
    population       SP.POP.TOTL          Population
    life_expectancy  SP.DYN.LE00.IN       Life Expectancy (years)

For each indicator, the most recent non-null value in the last ~20 years is
used. Many countries have no recent literacy reading (the World Bank stops
collecting it once it's long been near-universal) -- that metric is simply
omitted for that country rather than guessed, and the caller (generate_
comparison_script.py) only includes a table row when BOTH countries in the
pair have a value for it.

Caching: successful lookups are written to config/country_stats_cache.json
keyed by iso2+indicator. This is a RESILIENCE cache, not a freshness
shortcut -- a fresh live call is attempted every run (these are figures the
World Bank revises periodically, e.g. adding a newer year's number or
correcting an older one, so a video should always reflect what the source
currently reports, not what it reported over a month ago). A cache hit is
only trusted without a network call within CACHE_FRESH_HOURS (same-day
reuse across this run's two lookups); after that, a live call is always
attempted first, and only a failed live call after retries falls back to
the cached value (however old), rather than dropping the metric entirely.

Usage:
    python scripts/fetch_country_stats.py JP BR
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

WB_BASE = "https://api.worldbank.org/v2/country"
DATE_RANGE = "2003:2024"
CACHE_PATH = "config/country_stats_cache.json"
CACHE_FRESH_HOURS = 12  # reuse without a network call only within the same run-day
REQUEST_TIMEOUT = 15

# (key, World Bank indicator code, display label, unit)
INDICATORS = [
    ("gdp_per_capita", "NY.GDP.PCAP.CD", "GDP per Capita", "usd"),
    ("income", "NY.GNP.PCAP.CD", "Income per Person", "usd"),
    ("literacy", "SE.ADT.LITR.ZS", "Adult Literacy Rate", "pct"),
    ("population", "SP.POP.TOTL", "Population", "count"),
    ("life_expectancy", "SP.DYN.LE00.IN", "Life Expectancy", "years"),
]

# Metrics where a higher number is unambiguously "better" -- worth a winner
# badge in the rendered table. Population is informational only (more people
# isn't a "win"), so it's deliberately excluded.
COMPARABLE_METRICS = {"gdp_per_capita", "income", "literacy", "life_expectancy"}

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}


def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, sort_keys=True)
    os.replace(tmp, CACHE_PATH)


def _fetch_indicator_live(iso2, code):
    """Return (value, year) for the most recent non-null reading, or None."""
    url = f"{WB_BASE}/{iso2.lower()}/indicator/{code}?format=json&per_page=100&date={DATE_RANGE}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        payload = json.load(resp)
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return None
    rows = sorted(payload[1], key=lambda r: r.get("date") or "0", reverse=True)
    for row in rows:
        if row.get("value") is not None:
            return float(row["value"]), int(row["date"])
    return None


def get_stats(iso2, use_cache=True):
    """Return {metric_key: {"label", "unit", "value", "year"}} for this country,
    only including metrics a value was actually found for."""
    iso2 = iso2.upper()
    cache = _load_cache() if use_cache else {}
    now = time.time()
    out = {}
    dirty = False

    for key, code, label, unit in INDICATORS:
        cache_key = f"{iso2}:{key}"
        entry = cache.get(cache_key)
        fresh = entry and (now - entry.get("fetched_at", 0)) < CACHE_FRESH_HOURS * 3600

        if fresh:
            if entry.get("value") is not None:
                out[key] = {"label": label, "unit": unit,
                            "value": entry["value"], "year": entry["year"]}
            continue

        result = None
        last_exc = None
        for attempt in range(2):  # one retry -- the WB API occasionally times out transiently
            try:
                result = _fetch_indicator_live(iso2, code)
                last_exc = None
                break
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                last_exc = exc
        if last_exc is not None:
            print(f"[fetch_country_stats] {iso2} {key} live fetch failed ({last_exc}) "
                  f"-- {'using stale cache' if entry else 'no cached value, skipping'}")
            result = (entry["value"], entry["year"]) if entry and entry.get("value") is not None else None
        else:
            cache[cache_key] = {
                "value": result[0] if result else None,
                "year": result[1] if result else None,
                "fetched_at": now,
            }
            dirty = True

        if result is not None:
            out[key] = {"label": label, "unit": unit, "value": result[0], "year": result[1]}

    if dirty and use_cache:
        try:
            _save_cache(cache)
        except OSError as exc:
            print(f"[fetch_country_stats] could not write cache: {exc}")

    return out


def format_value(unit, value):
    """Human-readable string for a metric value, matched to its unit."""
    if unit == "usd":
        if value >= 1000:
            return f"${value:,.0f}"
        return f"${value:,.2f}"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "years":
        return f"{value:.1f} yrs"
    if unit == "count":
        for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
            if value >= div:
                return f"{value / div:.1f}{suffix}"
        return f"{value:.0f}"
    return str(value)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        stats = get_stats(arg)
        print(f"\n{arg.upper()}:")
        for k, v in stats.items():
            print(f"  {v['label']:22} {format_value(v['unit'], v['value']):>14}  (year {v['year']})")
