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
        assert len(slots) == 1
        assert slots[0]["hour"] <= 17


def test_rotating_series_cycle_covers_every_series_once_per_5_days():
    series_seen = {daily_variation.profiles_for(d)[0]["series"] for d in range(0, 5)}
    assert series_seen == {"country", "hook", "trending", "geography", "worlddata"}


def test_comparison_pair_schedule_advances_one_position_per_day():
    import generate_comparison_script as comparison
    countries = _countries()
    seen = set()
    for day in range(0, 50):
        pair = comparison.pick_pair_at(countries, day)
        seen.add(tuple(sorted(pair)))
    assert len(seen) == 50  # no position revisited within 50 days -> not skipping every other pair


def test_comparison_aired_pairs_reads_history(tmp_path):
    log = tmp_path / "history.csv"
    log.write_text(
        '2026-09-27T05:58:35Z,"South Korea vs Chile",KR_CL,success,success\n'
        '2026-09-28T05:58:35Z,"Chad vs Peru",TD_PE,failure,failure\n'
        '2026-09-21T10:53:08Z,"unknown",unknown,skipped\n',
        encoding="utf-8")
    aired = comparison.aired_pairs(str(log))
    assert aired == {frozenset({"KR", "CL"})}
    assert frozenset({"CL", "KR"}) in aired


def test_refresh_parser_expands_tied_rowspans():
    import refresh_rankings_static as refresh
    html = ("<table><tr><th>Rank</th><th>Country</th><th>HDI value</th></tr>"
            "<tr><td>1</td><td>Iceland</td><td>0.972</td></tr>"
            "<tr><td rowspan='2'>2</td><td>Norway</td><td rowspan='2'>0.970<sup>[a]</sup></td></tr>"
            "<tr><td>Switzerland</td></tr></table>")
    parser = refresh._TableParser()
    parser.feed(html)
    grid = refresh._grid(parser.tables[0]["rows"])
    assert grid[0] == (["Rank", "Country", "HDI value"], True)
    assert [row for row, _ in grid[1:]] == [["1", "Iceland", "0.972"], ["2", "Norway", "0.970"],
                                            ["2", "Switzerland", "0.970"]]


def test_refresh_name_matching_and_boundary_ties():
    import refresh_rankings_static as refresh
    assert refresh._norm("Czech Republic") == refresh._norm("Czechia")
    assert refresh._norm("Côte d'Ivoire") == refresh._norm("Ivory Coast")
    rows = [{"iso2": f"C{i}", "country_name": str(i), "raw": str(100 - i)} for i in range(refresh.TOP_N)]
    rows.append({"iso2": "TIE", "country_name": "tie", "raw": rows[-1]["raw"]})
    rows.append({"iso2": "OUT", "country_name": "out", "raw": "1"})
    top = refresh.top_entries(rows)
    assert [r["iso2"] for r in top][-1] == "TIE" and "OUT" not in {r["iso2"] for r in top}


def test_refresh_update_touches_only_one_block():
    import refresh_rankings_static as refresh
    with open("config/rankings_static.json", encoding="utf-8", newline="") as fh:
        original = fh.read()
    new_entries = [{"iso2": "IS", "country_name": "Iceland", "raw": "0.980"}]
    updated = refresh.apply_update(original, "hdi", "2026 edition", new_entries)
    data = json.loads(updated)
    assert data["hdi"]["edition"] == "2026 edition"
    assert data["hdi"]["entries"] == [{"iso2": "IS", "country_name": "Iceland", "value": 0.98}]
    assert '"value": 0.980}' in updated
    before, after = original.split('"world_happiness"', 1)[1], updated.split('"world_happiness"', 1)[1]
    assert before == after


def test_longform_chapters_follow_real_segment_timings():
    import assemble_long_video as assemble
    segs = [{"chapter": "Intro"}, {"chapter": "#2 B"}, {"chapter": "#1 A"}]
    timings = [{"dur": 45.2}, {"dur": 30.0}, {"dur": 3600.0}]
    assert assemble.chapter_lines(segs, timings) == ["00:00 Intro", "00:45 #2 B", "01:15 #1 A"]


def test_longform_past_ranks_and_trend_facts_use_real_readings():
    import generate_longform_rankings as longform
    metric = {"unit": "usd", "sort_direction": "desc"}
    series = {"AA": [(2015, 100.0), (2020, 300.0), (2025, 200.0)],
              "BB": [(2015, 100.0), (2025, 150.0)],
              "CC": [(2015, 50.0), (2025, 100.0)]}
    assert longform.past_ranks(metric, series, 2015) == {"AA": 1, "BB": 1, "CC": 3}
    ranked = [{"iso2": "AA", "country_name": "A", "rank": 1, "tied": False, "value": 200.0, "year_or_edition": "2025"},
              {"iso2": "BB", "country_name": "B", "rank": 2, "tied": False, "value": 150.0, "year_or_edition": "2025"}]
    rows = longform.build_rows(metric, ranked, series)
    facts = " | ".join(rows[0]["facts"])
    assert "up 100% since 2015, when it stood at $100.00" in facts
    assert "best reading in that span was $300.00 in 2020" in facts
    assert "ranked number 1 in 2015" in facts and "ahead of B, number 2" in facts
    assert any(f.startswith("ranked number 1 in 2015") and "down 1 place" in f for f in rows[1]["facts"])


def test_spoken_units_expand_display_formats():
    from generate_tts import apply_pronunciation_fixups as speak
    assert speak("a population of 1.41B, up from 71.6M") == "a population of 1.41 billion, up from 71.6 million"
    assert speak("82.8 yrs at #7, 12.3 t") == "82.8 years at number 7, 12.3 tonnes"


def test_longform_a_only_uses_world_bank_metrics(monkeypatch):
    import generate_longform_rankings as longform
    seen = []
    monkeypatch.setattr(longform, "get_ranked", lambda mid, *a, **k: seen.append(mid) or [])
    try:
        longform.pick_metric(load_metrics(), _countries())
    except SystemExit:
        pass
    live = {m["id"] for m in load_metrics()["metrics"] if m["source_type"] == "live"}
    assert seen and set(seen) <= live
