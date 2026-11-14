import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import india_series as s  # noqa: E402


def test_series_take_turns_and_each_keeps_its_own_rotation():
    n = len(s.INDIA_CYCLE)
    days = range(0, 3 * n)
    assert [s.series_for(d) for d in days[:n]] == s.INDIA_CYCLE
    for series in s.INDIA_CYCLE:
        steps = [s.rotation_for(d) for d in days if s.series_for(d) == series]
        assert steps == list(range(len(steps)))  # 0, 1, 2: advances only on its own days


def test_day_number_counts_from_the_epoch():
    assert s.day_number(s.EPOCH) == 0 and s.day_number(date(2026, 11, 20)) == 5
