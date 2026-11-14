#!/usr/bin/env python3
"""
Which India-channel series posts today. The series take turns, one Short a day, and each
series keeps its own rotation (the next place, the next measure), which advances only on
its own days. Add a series to INDIA_CYCLE once its generator and workflow steps exist.

Usage (workflow gate):  python scripts/india_series.py   -> prints "<series> <rotation>"
"""
import sys
from datetime import date

EPOCH = date(2026, 11, 15)
INDIA_CYCLE = ["rising", "untold"]  # planned next: "vs_world", "different"


def day_number(today=None):
    return ((today or date.today()) - EPOCH).days


def series_for(day):
    return INDIA_CYCLE[day % len(INDIA_CYCLE)]


def rotation_for(day):
    """How many times today's series has come up before today (its own rotation step)."""
    return day // len(INDIA_CYCLE)


if __name__ == "__main__":
    d = day_number()
    print(series_for(d), rotation_for(d))
    sys.exit(0)
