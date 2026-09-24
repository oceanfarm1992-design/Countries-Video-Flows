#!/usr/bin/env python3
"""
Shared deterministic per-day "variation profile" for the two-videos-per-day
rotation. Given a day number (days since the pipeline epoch), it decides — the
same way on every machine, with no stored state — which two series run today
(slots A and B), at which hours, how long each narration should be, and which
hashtag/tag variant to use.

Why this exists: posting the same length, same hashtags, same description, at
the same minute, every single day is itself a strong "automated/bot" signal to
TikTok and other platforms. Rotating these deterministically per day keeps the
account's footprint varied while staying fully unattended and reproducible.

The rotation is intentionally deterministic (a hash of the day number), NOT
random, so a re-run of the same day picks the same slots/series and never
double-posts or drifts.
"""
import hashlib
from datetime import date

# Pipeline epoch — same reference date the generators already use for rotation.
EPOCH = date(2026, 7, 23)

# Each day posts TWO of the five series (slot A and slot B), from a 5-day table
# where every series appears exactly twice and never pairs with the same partner
# twice in a row — so which two series post together isn't itself a fixed pattern.
# This is a clean 5-cycle: country -> hook -> trending -> geography -> worlddata
# -> back to country, each series pairing with its two neighbors in the cycle.
DAILY_PAIRS = {
    0: ["country", "hook"],
    1: ["hook", "trending"],
    2: ["trending", "geography"],
    3: ["geography", "worlddata"],
    4: ["worlddata", "country"],
}

# Candidate UTC hours either slot's post may fire at. The gate workflow triggers
# at every one of these; the job only proceeds on an hour this module picked for
# one of today's slots, so post times vary day to day instead of being fixed.
# Per-slot lanes (UTC) so the two rotating posts never cluster: A lands
# 01:00-03:59 Dubai, B lands 18:00-21:59 Dubai.
SLOT_HOURS = {"A": [21, 22, 23], "B": [14, 15, 16, 17]}

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
    """Return today's two deterministic slot profiles (A and B). Each slot's
    hour, word band, and hashtag/caption variant are seeded independently (by
    day number + slot letter) so the two same-day videos don't end up twins."""
    series_pair = DAILY_PAIRS[day_number % len(DAILY_PAIRS)]
    profiles = []
    for slot, series in zip("AB", series_pair):
        lane = SLOT_HOURS[slot]
        hour = lane[_seed(day_number, f"hour{slot}") % len(lane)]
        word_band = WORD_BANDS[_seed(day_number, f"words{slot}") % len(WORD_BANDS)]
        profiles.append({
            "day_number": day_number,
            "slot": slot,
            "series": series,
            "hour": hour,
            "min_words": word_band[0],
            "max_words": word_band[1],
            # index used by captions_common to pick hashtag/description/tag variants
            "variant_seed": _seed(day_number, f"variant{slot}") % 1_000_000,
        })
    return profiles


if __name__ == "__main__":
    # Quick visibility: print the next 9 days' rotation for sanity-checking.
    base = day_number_for()
    for d in range(base, base + 9):
        for p in profiles_for(d):
            print(f"day {d} slot {p['slot']}: series={p['series']:9} hour={p['hour']:02d}:00 UTC "
                  f"words={p['min_words']}-{p['max_words']}")
