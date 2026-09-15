#!/usr/bin/env python3
"""
Shared deterministic per-day "variation profile" for the single-video-per-day
rotation. Given a day number (days since the pipeline epoch), it decides — the
same way on every machine, with no stored state — which series runs today, at
which hour, how long the narration should be, and which hashtag/tag variant to
use.

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

# The one daily video rotates through these four series, one per day.
SERIES_ROTATION = ["country", "hook", "trending", "geography"]

# Candidate UTC hours the daily post may fire at. The gate workflow triggers at
# every one of these; the job only proceeds on the hour this module picks for
# today, so the post time varies day to day instead of being a fixed minute.
CANDIDATE_HOURS = [8, 10, 11, 13, 15, 17]

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


def profile_for(day_number):
    """Return today's deterministic variation profile."""
    series = SERIES_ROTATION[day_number % len(SERIES_ROTATION)]
    hour = CANDIDATE_HOURS[_seed(day_number, "hour") % len(CANDIDATE_HOURS)]
    word_band = WORD_BANDS[_seed(day_number, "words") % len(WORD_BANDS)]
    return {
        "day_number": day_number,
        "series": series,
        "hour": hour,
        "min_words": word_band[0],
        "max_words": word_band[1],
        # index used by captions_common to pick hashtag/description/tag variants
        "variant_seed": day_number,
    }


if __name__ == "__main__":
    # Quick visibility: print the next 9 days' rotation for sanity-checking.
    base = day_number_for()
    for d in range(base, base + 9):
        p = profile_for(d)
        print(f"day {d}: series={p['series']:8} hour={p['hour']:02d}:00 UTC "
              f"words={p['min_words']}-{p['max_words']}")
