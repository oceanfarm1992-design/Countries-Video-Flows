"""Offline smoke tests: no network, no API keys. Run with `python -m pytest tests`."""
import json
import os
import py_compile
import sys
from datetime import datetime, timezone
from glob import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

import daily_variation  # noqa: E402
import fact_check  # noqa: E402
import post_gate  # noqa: E402
from fetch_rankings_stats import _assign_competition_ranks, format_value, get_ranked, load_metrics  # noqa: E402
import generate_comparison_script as comparison  # noqa: E402
import generate_rankings_script as rankings  # noqa: E402


def _countries():
    with open("config/countries.json", encoding="utf-8") as fh:
        return json.load(fh)["countries"]


def test_every_script_compiles():
    for path in glob("scripts/*.py"):
        py_compile.compile(path, doraise=True)


def test_competition_ranking_shares_rank_on_ties():
    rows = [{"value": v} for v in (10, 9, 9, 7)]
    ranked = _assign_competition_ranks(rows)
    assert [r["rank"] for r in ranked] == [1, 2, 2, 4]
    assert [r["tied"] for r in ranked] == [False, True, True, False]


def test_format_value_units():
    assert format_value("count", 1_412_400_000) == "1.41B"
    assert format_value("usd_big", 2_500_000_000) == "$2.5B"
    assert format_value("usd", 57_222.71) == "$57,223"


def test_static_rankings_are_consistent():
    metrics = load_metrics()
    for metric in (m for m in metrics["metrics"] if m["source_type"] == "static"):
        ranked = get_ranked(metric["id"], _countries(), top_n=8, metrics_cfg=metrics)
        assert len(ranked) == 8, metric["id"]
        for row in ranked:
            better = sum(1 for r in ranked if r["value"] != row["value"] and r["rank"] < row["rank"])
            assert row["rank"] == better + 1, metric["id"]
        assert ranked[-1].get("more_tied_beyond", 1) > 0


def test_rankings_fallback_script_covers_every_row():
    metrics = load_metrics()
    metric = next(m for m in metrics["metrics"] if m["id"] == "corruption_index")
    ranked = get_ranked(metric["id"], _countries(), top_n=8, metrics_cfg=metrics)
    hook, narration, segments = rankings._fallback_script(metric, ranked)
    assert "LEAST CORRUPT" in hook
    assert len(segments) == len(ranked) + 2
    assert sorted(s["visual"] for s in segments[1:-1]) == sorted(r["iso2"] for r in ranked)
    assert "Least Corrupt Countries of" not in narration


def test_fact_check_reference_uses_display_values():
    metrics = load_metrics()
    metric = next(m for m in metrics["metrics"] if m["id"] == "hdi")
    ranked = get_ranked("hdi", _countries(), top_n=8, metrics_cfg=metrics)
    lines = rankings.reference_lines(metric, ranked)
    assert len(lines) == len(ranked)
    assert fact_check._reference_block(lines).startswith("- ")

    rows = [{"label": "GDP per Capita", "unit": "usd", "value_a": 57222.71, "value_b": 5170.0,
             "year_a": 2024, "year_b": 2024}]
    line = comparison.reference_lines("Sweden", "Cabo Verde", rows)[0]
    assert "Sweden=$57,223" in line and "57222.71" not in line


def test_comparison_skips_rows_with_mismatched_years():
    a = {"gdp_per_capita": {"label": "GDP", "unit": "usd", "value": 1.0, "year": 2024}}
    b = {"gdp_per_capita": {"label": "GDP", "unit": "usd", "value": 2.0, "year": 2015}}
    assert comparison.build_rows(a, b) == []


def test_post_gate_blackout_window():
    assert post_gate.spacing_block(datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc))
    assert post_gate.spacing_block(datetime(2026, 1, 1, 19, 50, tzinfo=timezone.utc))


def test_rotating_lanes_stay_clear_of_utc_midnight():
    for day in range(0, 400):
        slots = daily_variation.profiles_for(day)
        hours = sorted(s["hour"] for s in slots)
        assert hours[-1] <= 17
        assert hours[1] - hours[0] >= 2
