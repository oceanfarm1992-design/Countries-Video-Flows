#!/usr/bin/env python3
"""
Shared deterministic per-day "variation profile" for the rotating series.
Given a day number (days since the pipeline epoch), it decides — the same way
on every machine, with no stored state — which ONE of the six series runs
today, at what hour, how long its narration should be, and which hashtag/tag
variant to use.

Why this exists: posting the same length, same hashtags, same description, at
the same minute, every single day is itself a strong "automated/bot" signal to
TikTok and other platforms. Rotating these deterministically per day keeps the
account's footprint varied while staying fully unattended and reproducible.

The rotation is intentionally deterministic (a hash of the day number), NOT
random, so a re-run of the same day picks the same slot/series and never
double-posts or drifts.
"""
import hashlib
from datetime import date

# Pipeline epoch — same reference date the generators already use for rotation.
EPOCH = date(2026, 7, 23)

# One video per rotating turn, cycling through all six series in a fixed order so
# every series posts once every 6 turns (was 2/day across a 5-day pairing table;
# reduced 2026-10-03 to cut posting volume). Since 2026-10-30 the rotating workflow
# posts every third day (see SHORT_CYCLE), so that is once every 18 days.
# "worst": a worst-of countdown (most polluted air, most corrupt, ...) built with the
# rankings pipeline from config/rankings_negative_metrics.json.
SERIES_CYCLE = ["country", "hook", "trending", "geography", "worlddata", "worst"]

# One Short per day across the three Short workflows (reduced 2026-10-30 from one
# each per day): from SHORT_CYCLE_START, day N belongs to
# SHORT_CYCLE[(N - SHORT_CYCLE_START) % 3] and the other two workflows skip.
SHORT_CYCLE = ["comparison", "rankings", "rotating"]
SHORT_CYCLE_START = 99  # 2026-10-30


def short_series_for(day_number):
    """Which Short workflow posts on this day (every one of them, before the switch)."""
    if day_number < SHORT_CYCLE_START:
        return None
    return SHORT_CYCLE[(day_number - SHORT_CYCLE_START) % len(SHORT_CYCLE)]


def is_short_turn(series, day_number):
    turn = short_series_for(day_number)
    return turn is None or turn == series


def series_day(day_number):
    """The day number the Short series rotate by. Their own rotations (metric, pair,
    sub-series, country) were built for one post per day, so after the switch they
    advance one step per post of that series: each post continues from the
    series' last day-numbered pick instead of jumping three, and series cycling
    by day_number % k don't get stuck on the subset that lands on their turns."""
    if day_number < SHORT_CYCLE_START:
        return day_number
    return SHORT_CYCLE_START + (day_number - SHORT_CYCLE_START) // len(SHORT_CYCLE)


# Country rotation for the single-country series. Each posts once every
# len(SERIES_CYCLE) days, and indexing the 196 countries by the day number on those
# days reaches only 196 / gcd(196, 6) = 98 of them -- the other half would never air.
# So each series advances one country per post instead. The offset starts this past
# every index the old day-number scheme used before the switch (day 82, 2026-10-13),
# so no series repeats a country for ~110 posts.
ROTATION_OFFSET = 70


def rotation_index(day_number):
    """One step per post of a series (not per calendar day)."""
    return series_day(day_number) // len(SERIES_CYCLE) + ROTATION_OFFSET

# Target-hour lane (UTC) for the single daily slot: 15:00-17:59 Dubai. Stays
# well before UTC midnight: GitHub often starts late ticks after 00:00 UTC,
# when "today" rolls over and an unposted slot is lost.
SLOT_HOURS = [11, 12, 13]

# Narration length bands (min_words, max_words). A different band per day means a
# different video duration — another varied signal. Kept within the config's
# 45-80s envelope at ~2.3 words/sec (roughly 105-185 words).
WORD_BANDS = [
    (110, 135),
    (135, 160),
    (160, 185),
]


def _seed(day_number, salt):
    """Stable non-negative int derived from the day number + a salt string, so
    each varied dimension rotates on its own cycle instead of all moving in
    lockstep."""
    h = hashlib.sha256(f"{day_number}:{salt}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def day_number_for(today=None):
    return ((today or date.today()) - EPOCH).days


def profiles_for(day_number):
    """Return today's single deterministic slot profile, as a one-element list
    (kept as a list so callers that iterate over "today's slots" don't need a
    separate single-slot code path)."""
    series = SERIES_CYCLE[series_day(day_number) % len(SERIES_CYCLE)]
    hour = SLOT_HOURS[_seed(day_number, "hour") % len(SLOT_HOURS)]
    word_band = WORD_BANDS[_seed(day_number, "words") % len(WORD_BANDS)]
    return [{
        "day_number": day_number,
        "slot": "A",
        "series": series,
        "hour": hour,
        "min_words": word_band[0],
        "max_words": word_band[1],
        # index used by captions_common to pick hashtag/description/tag variants
        "variant_seed": _seed(day_number, "variant") % 1_000_000,
    }]


if __name__ == "__main__":
    # Quick visibility: print the next 9 days' rotation for sanity-checking.
    base = day_number_for()
    for d in range(base, base + 9):
        turn = short_series_for(d)
        if turn not in (None, "rotating"):
            print(f"day {d}: {turn} Short (no rotating post)")
            continue
        for p in profiles_for(d):
            print(f"day {d} slot {p['slot']}: series={p['series']:9} hour={p['hour']:02d}:00 UTC "
                  f"words={p['min_words']}-{p['max_words']}")
