#!/usr/bin/env python3
"""
Shared helper for the "rankings" series: real Top-N country rankings for one
metric per video (NOT one video per chapter -- see config/rankings_metrics.json).
Two data sources, matching the "never invent a number" rule every other series
in this project already follows (fetch_country_stats.py, worlddata's curated
facts[]):

  - LIVE metrics: fetched fresh from the World Bank public API using a BULK
    "country=all" call (one HTTP request per indicator covers every country at
    once) rather than fetch_country_stats.py's per-country loop -- looping
    per-country here would mean ~11 indicators x ~195 countries per video.
  - STATIC metrics: read from config/rankings_static.json, a curated snapshot
    of real published numbers from named indices (HDI, World Happiness Report,
    Democracy Index, Corruption Perceptions Index, Global Innovation Index,
    PISA, Global Firepower, Henley Passport Index) that have no free live API.

Both paths are unified behind get_ranked(), so generate_rankings_script.py
doesn't need to know which kind of metric it's looking at.

Caching (live metrics only): a fresh call is attempted every run once
CACHE_FRESH_HOURS has elapsed since the last one (these figures get revised);
before that, the same-run/same-day cache hit is reused without a network call.
Only a failed live call falls back to the cached value, and only if that
cached value isn't itself older than CACHE_MAX_AGE_DAYS -- an outage lasting
that long should surface as "too few countries" (and rotate to the next
metric) rather than silently keep posting an arbitrarily stale snapshot.

Ranking uses standard competition ranking (1, 2, 2, 4): when the source
publishes a genuine tie, this never invents a distinction the source doesn't
make. Each returned row carries "rank" (which may repeat) and "tied" (True if
another row in the same list shares its value).

Vintage: live metrics take each country's own most recent reading, which can
span years across countries. Mixing a 2025 figure with a 2008 one and ranking
them as if contemporaneous would misrepresent the source, so candidates older
than MAX_AGE_YEARS relative to the newest year actually available for that
indicator are dropped before ranking -- same principle as fetch_country_stats
never inventing a number, applied to vintage instead of value.

Usage:
    python scripts/fetch_rankings_stats.py gdp_per_capita
    python scripts/fetch_rankings_stats.py hdi --top 10
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

WB_BASE = "https://api.worldbank.org/v2/country/all/indicator"
DATE_RANGE = "2005:2026"
CACHE_PATH = "config/rankings_stats_cache.json"
CACHE_FRESH_HOURS = 12
CACHE_MAX_AGE_DAYS = 30  # a stale-fallback ceiling -- beyond this, treat as no data at all
MAX_AGE_YEARS = 3        # live rows older than (newest_year - this) are dropped before ranking
REQUEST_TIMEOUT = 30
PER_PAGE = 20000  # comfortably covers every country x every year in DATE_RANGE

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}

METRICS_CONFIG_PATH = "config/rankings_metrics.json"
STATIC_CONFIG_PATH = "config/rankings_static.json"


def load_metrics(path=METRICS_CONFIG_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _metric_by_id(metrics_cfg, metric_id):
    for m in metrics_cfg["metrics"]:
        if m["id"] == metric_id:
            return m
    raise KeyError(f"Unknown metric id: {metric_id}")


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


def _fetch_indicator_bulk_live(code, known_iso2=None):
    """One HTTP call covering every country's most recent reading for `code`.
    Returns {iso2: (value, year)}. For a real country row, the World Bank API's
    country.id field IS the real ISO 3166-1 alpha-2 code (verified against
    known countries) -- but several World Bank REGIONAL AGGREGATES also use
    two-letter codes ("EU", "ZG", "1A" is 2 chars too, etc.), so a bare
    len(iso2) == 2 check does NOT exclude them on its own. `known_iso2`, when
    given, is the authoritative filter (this pipeline's own config/
    countries.json) -- passed in so aggregates are dropped at fetch time
    rather than carried into the cache and discarded on every read."""
    url = f"{WB_BASE}/{code}?format=json&per_page={PER_PAGE}&date={DATE_RANGE}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        payload = json.load(resp)
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return {}

    latest = {}  # iso2 -> (value, year)
    for row in payload[1]:
        if row.get("value") is None:
            continue
        iso2 = (row.get("country") or {}).get("id", "")
        if len(iso2) != 2:
            continue
        if known_iso2 is not None and iso2 not in known_iso2:
            continue
        year = int(row["date"])
        current = latest.get(iso2)
        if current is None or year > current[1]:
            latest[iso2] = (float(row["value"]), year)
    return latest


def _get_live_values(wb_indicator, known_iso2=None, use_cache=True):
    """Return {iso2: {"value", "year"}} for one live indicator, resilience-
    cached the same way fetch_country_stats.py caches per-country lookups."""
    cache = _load_cache() if use_cache else {}
    now = time.time()
    entry = cache.get(wb_indicator)
    fresh = entry and (now - entry.get("fetched_at", 0)) < CACHE_FRESH_HOURS * 3600
    if fresh:
        return entry["values"]

    last_exc = None
    values = None
    for attempt in range(2):  # one retry -- the WB API occasionally times out
        try:
            values = _fetch_indicator_bulk_live(wb_indicator, known_iso2=known_iso2)
            last_exc = None
            break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_exc = exc

    if last_exc is not None:
        if not entry:
            print(f"[fetch_rankings_stats] {wb_indicator} live bulk fetch failed ({last_exc}) -- "
                  f"no cached value, giving up")
            return {}
        cache_age_days = (now - entry.get("fetched_at", 0)) / 86400
        if cache_age_days > CACHE_MAX_AGE_DAYS:
            print(f"[fetch_rankings_stats] {wb_indicator} live bulk fetch failed ({last_exc}) -- "
                  f"cached value is {cache_age_days:.0f} days old (> {CACHE_MAX_AGE_DAYS}), "
                  f"treating as no data rather than posting a stale snapshot")
            return {}
        print(f"[fetch_rankings_stats] {wb_indicator} live bulk fetch failed ({last_exc}) -- "
              f"using {cache_age_days:.0f}-day-old cached value")
        return entry["values"]

    out = {iso2: {"value": v, "year": y} for iso2, (v, y) in values.items()}
    if use_cache:
        cache[wb_indicator] = {"values": out, "fetched_at": now}
        try:
            _save_cache(cache)
        except OSError as exc:
            print(f"[fetch_rankings_stats] could not write cache: {exc}")
    return out


def _load_static(static_key, path=STATIC_CONFIG_PATH):
    with open(path, encoding="utf-8") as fh:
        static_cfg = json.load(fh)
    return static_cfg[static_key]


def _assign_competition_ranks(rows):
    """Standard competition ranking (1, 2, 2, 4): rows must already be sorted
    best-first. A row's rank only advances past a tie when its value actually
    differs from the previous row's -- so a genuine published tie is shown as
    a tie, never split into two invented, arbitrarily-ordered ranks. Also sets
    "tied": True on every row that shares its value with at least one other
    row in `rows` (mutates and returns the same list)."""
    rank = 0
    prev_value = object()  # sentinel that can't equal any real value
    for i, row in enumerate(rows):
        if row["value"] != prev_value:
            rank = i + 1
        row["rank"] = rank
        prev_value = row["value"]

    counts = {}
    for row in rows:
        counts[row["value"]] = counts.get(row["value"], 0) + 1
    for row in rows:
        row["tied"] = counts[row["value"]] > 1
    return rows


def get_ranked(metric_id, countries, top_n=None, metrics_cfg=None):
    """Return this metric's ranked country list, best-first (respecting the
    metric's sort_direction -- most static metrics and all live ones are
    "higher is better", but military_strength's PowerIndex is inverted).

    `countries` is config/countries.json's "countries" list -- used to attach
    each country's display name/lat/lon and, for live metrics, to filter the
    World Bank bulk response down to real countries this pipeline knows about.

    Returns a list of dicts: {iso2, country_name, value, year_or_edition,
    source_label, rank, tied}, length capped at top_n (or the metric
    registry's top_n). "rank" uses competition ranking so ties are never
    invented into a false ordering -- see _assign_competition_ranks. If the
    tie at the very last displayed spot extends past top_n (more countries
    share that value than are actually shown), the last row also carries
    "more_tied_beyond": <count> -- displaying only top_n of a wider tie
    without saying so would itself misrepresent the tie's true size (e.g.
    passport_index has 12 countries tied at 185; showing 3 of them as "tied"
    with each other is true but silently hides that 9 more share the exact
    same value)."""
    metrics_cfg = metrics_cfg or load_metrics()
    metric = _metric_by_id(metrics_cfg, metric_id)
    top_n = top_n or metrics_cfg.get("top_n", 8)
    by_iso2 = {c["iso2"]: c for c in countries}

    candidates = []
    if metric["source_type"] == "live":
        values = _get_live_values(metric["wb_indicator"], known_iso2=set(by_iso2))
        for iso2, v in values.items():
            country = by_iso2.get(iso2)
            if not country:
                continue
            candidates.append({
                "iso2": iso2,
                "country_name": country["name"],
                "value": v["value"],
                "year": v["year"],
                "source_label": metric.get("source_label", "World Bank"),
            })
        if candidates:
            newest_year = max(c["year"] for c in candidates)
            cutoff = newest_year - MAX_AGE_YEARS
            before = len(candidates)
            candidates = [c for c in candidates if c["year"] >= cutoff]
            dropped = before - len(candidates)
            if dropped:
                print(f"[fetch_rankings_stats] {metric_id}: dropped {dropped} countries with "
                      f"readings older than {cutoff} (newest available year is {newest_year})")
        for c in candidates:
            c["year_or_edition"] = str(c["year"])
    else:
        static_block = _load_static(metric["static_key"])
        for entry in static_block["entries"]:
            iso2 = entry["iso2"]
            if iso2 not in by_iso2:
                continue  # config/countries.json changed since curation -- skip rather than guess
            candidates.append({
                "iso2": iso2,
                "country_name": entry["country_name"],
                "value": entry["value"],
                "year_or_edition": static_block["edition"],
                "source_label": f"{static_block['source']} ({static_block['edition']})",
            })

    reverse = metric["sort_direction"] == "desc"
    candidates.sort(key=lambda r: r["value"], reverse=reverse)
    top = candidates[:top_n]
    ranked = _assign_competition_ranks(top)

    if ranked and len(candidates) > len(ranked):
        boundary_value = ranked[-1]["value"]
        if candidates[len(ranked)]["value"] == boundary_value:  # the tie extends past the cutoff
            total_at_value = sum(1 for c in candidates if c["value"] == boundary_value)
            shown_at_value = sum(1 for r in ranked if r["value"] == boundary_value)
            ranked[-1]["more_tied_beyond"] = total_at_value - shown_at_value

    return ranked


def year_range_label(ranked):
    """A short 'as of' string for the footer/narration, e.g. "2024" or
    "2021-2024" -- None if no row carries a year (all static metrics use a
    single named edition already folded into source_label instead)."""
    years = {r["year"] for r in ranked if "year" in r}
    if not years:
        return None
    lo, hi = min(years), max(years)
    return str(lo) if lo == hi else f"{lo}-{hi}"


def format_value(unit, value):
    """Human-readable string for a metric value, matched to its unit -- unit
    vocabulary mirrors fetch_country_stats.py's format_value where they
    overlap (usd, pct, years, count) plus the rankings-specific units."""
    if unit in ("usd", "usd_big"):
        if unit == "usd_big" and value >= 1_000_000_000:
            return f"${value / 1_000_000_000:,.1f}B"
        if value >= 1000:
            return f"${value:,.0f}"
        return f"${value:,.2f}"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "years":
        return f"{value:.1f} yrs"
    if unit == "tonnes":
        return f"{value:.1f} t"
    if unit == "km2":
        return f"{value:,.0f} km²"
    if unit == "count":
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f}B"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return f"{value:.0f}"
    if unit == "index_3dp":
        return f"{value:.3f}"
    if unit == "score_3dp":
        return f"{value:.3f}"
    if unit == "score_2dp":
        return f"{value:.2f}"
    if unit == "score_1dp":
        return f"{value:.1f}"
    if unit == "score_0_100":
        return f"{value:.0f}/100"
    if unit == "score_int":
        return f"{value:.0f}"
    if unit == "powerindex":
        return f"{value:.4f}"
    return str(value)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Preview a rankings metric's Top-N list.")
    ap.add_argument("metric_id")
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--countries-config", default="config/countries.json")
    args = ap.parse_args()

    with open(args.countries_config, encoding="utf-8") as fh:
        countries = json.load(fh)["countries"]

    metrics_cfg = load_metrics()
    metric = _metric_by_id(metrics_cfg, args.metric_id)
    rows = get_ranked(args.metric_id, countries, top_n=args.top, metrics_cfg=metrics_cfg)
    years = year_range_label(rows)
    print(f"\n{metric['label']} ({metric['source_type']}, as of {years or 'see edition'}):")
    for r in rows:
        tie_mark = "=" if r["tied"] else " "
        print(f"  #{r['rank']:<2}{tie_mark} {r['country_name']:28} "
              f"{format_value(metric['unit'], r['value']):>14}  ({r['year_or_edition']})")
