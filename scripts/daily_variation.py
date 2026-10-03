#!/usr/bin/env python3
"""
Shared deterministic per-day "variation profile" for the rotating series.
Given a day number (days since the pipeline epoch), it decides — the same way
on every machine, with no stored state — which ONE of the five series runs
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

# One video per day, cycling through all five series in a fixed 5-day order so
# every series posts once every 5 days (was 2/day across a 5-day pairing table;
# reduced 2026-10-03 to cut posting volume).
SERIES_CYCLE = ["country", "hook", "trending", "geography", "worlddata"]

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
    series = SERIES_CYCLE[day_number % len(SERIES_CYCLE)]
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
        for p in profiles_for(d):
            print(f"day {d} slot {p['slot']}: series={p['series']:9} hour={p['hour']:02d}:00 UTC "
                  f"words={p['min_words']}-{p['max_words']}")
