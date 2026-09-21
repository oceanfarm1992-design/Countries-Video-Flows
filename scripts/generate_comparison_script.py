#!/usr/bin/env python3
"""
Stage 1 (comparison variant): a 6th series, alongside country/hook/trending/
geography/worlddata, on the same channels -- but unlike those (one country per
video), this one picks TWO countries and narrates a side-by-side "X vs Y"
comparison across a small table of real statistics (GDP per capita, income,
literacy, population, life expectancy). Own dedicated 2-videos/day schedule
(.github/workflows/daily-comparison-short.yml), not part of the 5-day single-
country rotation in daily_variation.py.

Numbers are NEVER invented: they come from scripts/fetch_country_stats.py
(World Bank public API, cached), the same "cite only supplied numbers" rule
worlddata's "stats" sub-type already applies to its curated facts[]. A table
row is only included when BOTH countries in the pair have a value for that
metric -- a missing figure is omitted, never guessed.

Rotation: today's pair is drawn from a full round-robin schedule (the classic
tournament "circle method") indexed by day number + slot, so a re-run of the
same day/slot always picks the same pair, and -- unlike a hashed-partner
offset -- every country is guaranteed to eventually face every other country
exactly once per full cycle, rather than only ever meeting a pseudo-random
subset of partners.

Requires:
    OPENAI_API_KEY environment variable (optional -- falls back to a template
    narration built only from the fetched numbers if unset or if GPT/fact-
    check both fail).

Output:
    build/script.json   { id, name, hook, narration, segments, country_a,
                           country_b, rows, series="comparison", ... }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_comparison_script.py
    python scripts/generate_comparison_script.py --slot 0
    python scripts/generate_comparison_script.py --index-a 12 --index-b 88
"""
import argparse
import hashlib
import json
import os
import textwrap
from datetime import date

from captions_common import write_platform_captions
from daily_variation import day_number_for
from fact_check import verify_narration
from fetch_country_stats import COMPARABLE_METRICS, format_value, get_stats

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

EPOCH = date(2026, 7, 23)  # same pipeline epoch as daily_variation.py

# Preference order for which metrics make the table when more than MAX_ROWS
# are available for a pair -- economic/income/education first (what the user
# asked this series to lead with), population and life expectancy fill out
# "and more".
ROW_PRIORITY = ["gdp_per_capita", "income", "literacy", "population", "life_expectancy"]
MAX_ROWS = 4
MIN_ROWS = 3

# A stat is only comparable if both countries' readings are roughly
# contemporaneous -- the World Bank doesn't publish every indicator for every
# country in the same year, so without this a "current" comparison could
# silently pit e.g. one country's 2024 figure against the other's 2011 one.
# Same threshold fetch_rankings_stats.py uses for the same reason.
MAX_YEAR_GAP = 3

# Some entities in the country list aren't tracked by the World Bank at all
# (e.g. Taiwan) -- how many scheduled pairs to try skipping forward past
# before giving up (see the fallback loop in main()). Comfortably more than
# the handful of data-sparse entities expected in any 195-country list.
MAX_PAIR_ATTEMPTS = 20


def _seed(day_number, salt):
    h = hashlib.sha256(f"comparison:{day_number}:{salt}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


_SCHEDULE_CACHE = {}


def _round_robin_schedule(n):
    """Circle-method round-robin schedule: a flat, fixed-order list of (i, j)
    country-index pairs such that EVERY unordered pair of distinct countries
    appears EXACTLY ONCE (matching every country against every other country,
    not just a hashed subset of partners). Deterministic -- same input length
    always produces the same schedule, so no state needs to be stored.

    n-1 "rounds" (n rounds with a bye slot when n is odd) of up to n//2 pairs
    each, built by fixing index 0 and rotating everyone else -- the standard
    tournament-scheduling algorithm. Cheap even for n=195 (~19k pairs total),
    recomputed fresh each run rather than cached to disk."""
    if n in _SCHEDULE_CACHE:
        return _SCHEDULE_CACHE[n]
    teams = list(range(n))
    if n % 2 == 1:
        teams.append(None)  # bye
    m = len(teams)
    arr = teams[:]
    schedule = []
    for _ in range(m - 1):
        for k in range(m // 2):
            a, b = arr[k], arr[m - 1 - k]
            if a is not None and b is not None:
                schedule.append((a, b) if a < b else (b, a))
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    _SCHEDULE_CACHE[n] = schedule
    return schedule


def pick_pair_at(countries, global_index):
    """The country pair at a specific absolute position in the round-robin
    schedule (see _round_robin_schedule). Presentation side (which country
    renders on the left) is hashed by that same position so the same country
    isn't always shown on the same side."""
    schedule = _round_robin_schedule(len(countries))
    idx_a, idx_b = schedule[global_index % len(schedule)]
    if _seed(global_index, "side") % 2 == 1:
        idx_a, idx_b = idx_b, idx_a
    return idx_a, idx_b


def pick_pair(countries, day_number, slot):
    """This day+slot's country pair, drawn in fixed order from the full round-
    robin schedule so every country eventually faces every other country
    exactly once per full cycle (~18,915 pairs for 195 countries -- roughly 26
    years at 2 videos/day -- before it repeats)."""
    return pick_pair_at(countries, day_number * 2 + slot)


def build_rows(stats_a, stats_b):
    """Rows both countries have a value for, in ROW_PRIORITY order, capped at
    MAX_ROWS. Returns [{"key","label","unit","value_a","value_b","year_a",
    "year_b","comparable"}]."""
    rows = []
    for key in ROW_PRIORITY:
        if key in stats_a and key in stats_b:
            a, b = stats_a[key], stats_b[key]
            if abs(a["year"] - b["year"]) > MAX_YEAR_GAP:
                continue  # too far apart in vintage to present as a fair comparison
            rows.append({
                "key": key,
                "label": a["label"],
                "unit": a["unit"],
                "value_a": a["value"],
                "value_b": b["value"],
                "year_a": a["year"],
                "year_b": b["year"],
                "comparable": key in COMPARABLE_METRICS,
            })
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _winner(row):
    if not row["comparable"]:
        return None
    if row["value_a"] > row["value_b"]:
        return "a"
    if row["value_b"] > row["value_a"]:
        return "b"
    return None


# ---------------------------------------------------------------------------
# Fallback: template narration built ONLY from the fetched numbers
# ---------------------------------------------------------------------------

def _fallback_script(name_a, name_b, rows):
    """Kept deliberately terse (one short sentence per row, folding the winner
    callout into that same sentence with a dash rather than a second sentence)
    so the total word count stays comfortably under the video's duration cap
    even at a slow TTS engine's natural pace, without needing to speed up the
    voice to compensate."""
    hook = f"{name_a.upper()} VS {name_b.upper()}"
    segments = [{
        "text": f"{name_a} versus {name_b} -- let's compare the numbers.",
        "visual": "",
    }]
    for row in rows:
        va = format_value(row["unit"], row["value_a"])
        vb = format_value(row["unit"], row["value_b"])
        winner = _winner(row)
        line = f"{row['label']}: {name_a} {va}, {name_b} {vb}"
        if winner == "a":
            line += f" -- {name_a} leads."
        elif winner == "b":
            line += f" -- {name_b} leads."
        else:
            line += "."
        segments.append({"text": line, "visual": row["label"]})
    segments.append({
        "text": "Which country surprised you more? Comment below and follow for more comparisons.",
        "visual": "",
    })
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# OpenAI narration -- STRICTLY limited to the supplied numbers
# ---------------------------------------------------------------------------

def _gpt_script(name_a, name_b, rows, openai_cfg, min_words, max_words):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set -- cannot call GPT.")

    client = OpenAI(api_key=api_key)
    rows_block = "\n".join(
        f"- {r['label']}: {name_a}={format_value(r['unit'], r['value_a'])} "
        f"({r['year_a']}), {name_b}={format_value(r['unit'], r['value_b'])} ({r['year_b']})"
        for r in rows
    )

    system_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts narration comparing {name_a} and
        {name_b} side by side for a country-comparison series. This is shown
        over an on-screen comparison TABLE (not video footage), so the
        narration should read out and react to the numbers as they appear on
        screen, one row at a time. Write ONLY the spoken narration -- no stage
        directions, no titles. The video runs 30-40 seconds read at a natural
        pace, so keep it tight and punchy.

        Open with a hook sentence framing this as a head-to-head comparison,
        then go through the supplied statistics ONE AT A TIME in the order
        given, stating each country's number and a brief, honest reaction
        (which country leads, or how big the gap is). Close with a brief line,
        then speak this call-to-action naturally: "Which country surprised you
        more, {name_a} or {name_b}? Comment below! Follow for more country
        comparisons."

        STRICT RULES (non-negotiable):
        - You may ONLY cite a number that is EXPLICITLY present in the
          statistics supplied below, using the value and year as given. NEVER
          invent, round differently, estimate, or infer a new statistic.
        - Do not introduce any statistic, record, or claim not present below.
        - Stay strictly about the numbers supplied -- no unrelated history,
          culture, or politics.

        Tone: energetic head-to-head comparison, like a great "which country
        wins" video -- not a dry recitation. No bullet points or headers --
        pure flowing prose only.

        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70
            characters) framing this as {name_a} vs {name_b} -- burned onto
            the video as text, so it must stand alone without the narration.
          - "segments": an array of {len(rows) + 2} SHORT objects (one for
            the opening hook line, one per statistic IN THE ORDER SUPPLIED,
            and one for the closing call-to-action). Each object has:
              - "text": a short beat of the narration.
              - "visual": for a statistic beat, the EXACT label of that
                statistic as given below (e.g. "GDP per Capita"); empty
                string "" for the opening and closing beats.
        Concatenating every "text" in order must read as one smooth narration
        that starts with the hook and ends with the call-to-action above
        (both sentences, in that order). IMPORTANT: the combined narration
        must total between {min_words} and {max_words} spoken words -- count
        them; if short, add a touch more reaction to each number rather than
        inventing new figures. Output ONLY the JSON object.
    """).strip()

    user_message = textwrap.dedent(f"""
        Comparison: {name_a} vs {name_b}

        Statistics you may cite (do not use any number not present here):
        {rows_block}
    """).strip()

    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=openai_cfg.get("max_tokens", 700),
        temperature=openai_cfg.get("temperature", 0.7),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    hook = (data.get("hook") or "").strip() or f"{name_a.upper()} VS {name_b.upper()}"
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate a country-comparison short script.")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--video-config", default="config/comparison_video.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--index-a", type=int, default=None, help="Force country A's index (0-based).")
    ap.add_argument("--index-b", type=int, default=None, help="Force country B's index (0-based).")
    ap.add_argument("--slot", type=int, default=0, choices=[0, 1],
                    help="Which of today's 2 comparison videos this is.")
    ap.add_argument("--min-words", type=int, default=None,
                    help="Defaults to video-config's min_seconds * words_per_second.")
    ap.add_argument("--max-words", type=int, default=None,
                    help="Defaults to video-config's max_seconds * words_per_second.")
    ap.add_argument("--variant-seed", type=int, default=None)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)
    with open(args.video_config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]

    countries = config["countries"]
    if len(countries) < 2:
        raise SystemExit("Need at least 2 countries in config to compare.")

    day_number = day_number_for()
    wps = video_cfg.get("words_per_second", 2.3)
    min_words = args.min_words or round(video_cfg["min_seconds"] * wps)
    max_words = args.max_words or round(video_cfg["max_seconds"] * wps)

    if args.index_a is not None and args.index_b is not None:
        idx_a, idx_b = args.index_a % len(countries), args.index_b % len(countries)
        country_a, country_b = countries[idx_a], countries[idx_b]
        name_a, name_b = country_a["name"], country_b["name"]
        print(f"[generate_comparison_script] fetching stats for {name_a} and {name_b} ...")
        stats_a, stats_b = get_stats(country_a["iso2"]), get_stats(country_b["iso2"])
        rows = build_rows(stats_a, stats_b)
        if len(rows) < MIN_ROWS:
            raise SystemExit(
                f"Only {len(rows)} comparable metric(s) available for {name_a} vs {name_b} "
                f"(need >= {MIN_ROWS}) -- World Bank data too sparse for this forced pair.")
    else:
        # Some entities in config/countries.json (e.g. Taiwan) aren't tracked
        # by the World Bank at all, so their round-robin pairing has no real
        # stats to show. Rather than fail the whole day's post over one
        # data-sparse pair, walk forward through the schedule -- deterministic
        # per day+slot (same failure, same fallback, every time), so this
        # never silently invents data, it just skips to the next real pair.
        base_index = day_number * 2 + args.slot
        rows = None
        for attempt in range(MAX_PAIR_ATTEMPTS):
            idx_a, idx_b = pick_pair_at(countries, base_index + attempt)
            country_a, country_b = countries[idx_a], countries[idx_b]
            name_a, name_b = country_a["name"], country_b["name"]
            print(f"[generate_comparison_script] fetching stats for {name_a} and {name_b} ...")
            stats_a, stats_b = get_stats(country_a["iso2"]), get_stats(country_b["iso2"])
            candidate_rows = build_rows(stats_a, stats_b)
            if len(candidate_rows) >= MIN_ROWS:
                rows = candidate_rows
                if attempt:
                    print(f"[generate_comparison_script] skipped {attempt} data-sparse pair(s) "
                          f"before landing on {name_a} vs {name_b}")
                break
            print(f"[generate_comparison_script] only {len(candidate_rows)} comparable metric(s) "
                  f"for {name_a} vs {name_b} -- trying the next scheduled pair")
        if rows is None:
            raise SystemExit(
                f"Could not find a pair with >= {MIN_ROWS} comparable metrics in "
                f"{MAX_PAIR_ATTEMPTS} attempts starting from schedule position {base_index}.")

    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if OPENAI_AVAILABLE and api_key:
        print(f"[generate_comparison_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} ...")
        try:
            hook, narration, segments = _gpt_script(name_a, name_b, rows, openai_cfg, min_words, max_words)
            ok, issues = verify_narration(narration, f"{name_a} and {name_b}", openai_cfg, reference=rows)
            if not ok:
                print(f"[generate_comparison_script] fact-check flagged: {issues} -- regenerating once")
                hook, narration, segments = _gpt_script(name_a, name_b, rows, openai_cfg, min_words, max_words)
                ok, issues = verify_narration(narration, f"{name_a} and {name_b}", openai_cfg, reference=rows)
                if not ok:
                    print(f"[generate_comparison_script] fact-check flagged again: {issues} -- using fallback script")
                    hook, narration, segments = _fallback_script(name_a, name_b, rows)
                    source = "fallback"
                else:
                    source = "openai"
            else:
                source = "openai"
        except Exception as exc:
            print(f"[generate_comparison_script] OpenAI error: {exc} -- using fallback script")
            hook, narration, segments = _fallback_script(name_a, name_b, rows)
            source = "fallback"
    else:
        reason = "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set"
        print(f"[generate_comparison_script] {reason} -- using fallback script")
        hook, narration, segments = _fallback_script(name_a, name_b, rows)
        source = "fallback"

    for seg in segments:
        seg["words"] = len(seg["text"].split())

    seed = args.variant_seed if args.variant_seed is not None else day_number

    record = {
        "id": f"{country_a['id']}_vs_{country_b['id']}",
        "name": f"{name_a} vs {name_b}",
        "title": f"{name_a} vs {name_b}",
        "hook": hook,
        "narration": narration,
        "segments": segments,
        "narration_source": source,
        "series": "comparison",
        "country_a": {"name": name_a, "iso2": country_a["iso2"],
                      "lat": country_a.get("lat"), "lon": country_a.get("lon")},
        "country_b": {"name": name_b, "iso2": country_b["iso2"],
                      "lat": country_b.get("lat"), "lon": country_b.get("lon")},
        "rows": rows,
        "day_number": day_number,
        "slot": args.slot,
        "variant_seed": seed,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    # write_platform_captions expects a single "country" dict -- synthesize one
    # representing the pairing so the existing caption/hashtag machinery (and
    # its per-day variation) works unmodified.
    synthetic_country = {
        "name": f"{name_a} vs {name_b}",
        "specialty": f"{name_a} and {name_b}, compared side by side",
        "facts": [
            f"{r['label']}: {name_a} {format_value(r['unit'], r['value_a'])} vs "
            f"{name_b} {format_value(r['unit'], r['value_b'])}"
            for r in rows
        ],
    }
    write_platform_captions("comparison", synthetic_country, hook, config, args.out, seed)

    words = len(narration.split())
    print(
        f"[generate_comparison_script] {name_a} (#{idx_a}) vs {name_b} (#{idx_b}): "
        f"hook={hook!r}, {len(rows)} rows, {words} narration words, source={source}"
    )


if __name__ == "__main__":
    main()
