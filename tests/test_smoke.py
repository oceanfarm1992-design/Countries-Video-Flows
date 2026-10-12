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
    assert "up 100% over the past decade" in facts
    assert "best reading in that span was $300.00 in 2020" in facts
    assert "ranked number 1 ten years earlier, in 2015, unchanged since then" in facts
    assert any(f.startswith("ranked number 1 ten years earlier") and "down 1 place" in f for f in rows[1]["facts"])


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


def test_longform_length_estimate_counts_digits():
    import generate_longform_rankings as longform
    plain = longform.estimate_seconds([{"text": "word " * 100}])
    numeric = longform.estimate_seconds([{"text": "$30,769.7 " * 100}])
    assert numeric > plain * 4
    assert format_value("usd_big", 30_769_700_000_000) == "$30.77T"


def test_strict_fact_check_fails_closed_when_checker_breaks(monkeypatch):
    class Broken:
        def __init__(self, **_):
            raise RuntimeError("network down")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(fact_check, "OPENAI_AVAILABLE", True)
    monkeypatch.setattr(fact_check, "OpenAI", Broken, raising=False)
    ok, issues = fact_check.verify_narration("text", "X", {}, reference=["a"], strict=True)
    assert ok is False and issues
    ok, _ = fact_check.verify_narration("text", "X", {}, reference=["a"])
    assert ok is True


def test_motion_easing_and_sfx_track(tmp_path):
    import wave
    import motion_kit as mk
    assert mk.ease_io(0) == 0 and abs(mk.ease_io(1) - 1) < 1e-9 and mk.ease_out(0.5) > 0.5
    assert mk.phase(5, 4, 2) == 0.5 and mk.phase(1, 4, 2) == 0
    path = tmp_path / "sfx.wav"
    mk.write_sfx_track(str(path), 3.0, [(0.5, mk.sfx_pop()), (1.0, mk.sfx_whoosh())])
    with wave.open(str(path)) as w:
        assert w.getframerate() == 44100 and w.getnframes() == int(5.0 * 44100)


def test_motion_rankings_row_frame_renders_and_flight_rises():
    import motion_kit as mk
    import render_rankings_motion as rm
    a, b = (1000.0, 1000.0, 900.0), (7000.0, 1500.0, 900.0)
    assert mk.MapCamera.fly(a, b, 0.5)[2] > 900 * 2  # long trips zoom out mid-flight
    row = {"iso2": "BR", "country": "Brazil", "rank": 8, "tied": False, "value": 4.99e12, "value_text": "$4.99T"}
    payload = {"row": row, "prev_shot": (4000.0, 1500.0, 6400.0), "flag": None, "unit": "usd_big", "bar": 0.12,
               "label": "GDP (PPP)", "chips": [{"iso2": "BR", "rank": 8, "revealed": True}],
               "revealed_before": []}
    frame = rm.make_scene("row", payload)(3.5)
    assert frame.size == (1080, 1920)
    assert frame.getpixel((540, 300))[:3] != frame.getpixel((540, 1900))[:3]  # card drawn over the map


def test_raised_country_wears_its_flag(tmp_path):
    from PIL import Image
    import motion_kit as mk
    # a red/white/blue tricolour with transparent corners (like Nepal's pennant)
    flag = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    for i, c in enumerate([(220, 30, 40), (250, 250, 250), (20, 60, 200)]):
        flag.paste(c + (255,), (20, i * 60 + 10, 280, i * 60 + 70))
    path = tmp_path / "flag.png"
    flag.save(path)
    tex = mk.load_flag(str(path))
    assert tex.mode == "RGB" and tex.getpixel((2, 2)) != (255, 255, 255)  # corners take the field colour
    # a very wide country still shows every stripe (bend capped, then cover-fit)
    fill = mk.flag_fill(tex, (1000, 200), (0, 0, 1000, 200))
    colours = {fill.getpixel((500, y)) for y in (30, 100, 170)}
    assert len(colours) == 3
    # the lift paints the flag onto the raised country
    cam = mk.MapCamera((1080, 1920), [(-140, 470), (1220, 470), (2350, 1950), (-1270, 1950)], (540, 880))
    shot = cam.shot_for("BR", pad=2.9)
    plain = cam.render(shot, lifts=[("BR", 1.0, mk.LAND_LIT)])
    flagged = cam.render(shot, lifts=[("BR", 1.0, mk.LAND_LIT, tex)])
    x, y = (int(v) for v in cam.project("BR", shot))
    assert plain.getpixel((x, y - 30))[:3] != flagged.getpixel((x, y - 30))[:3]
    assert cam.render(shot, beams=[("BR", 1.0, mk.ACCENT, tex)]).size == (1080, 1920)


def test_captions_show_script_numbers_timed_by_whisper():
    import generate_captions as gc
    script = "GDP of $40,430 and 61.7% literacy, about 1.41B people.".split()
    heard = ["GDP", "of", "40", "430", "and", "61", "7", "literacy", "about", "1.41", "billion", "people"]
    times = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(heard)]
    words, share = gc.align_script_words(script, times)
    assert [w["word"] for w in words] == script
    assert share > 0.8
    by = {w["word"]: w for w in words}
    assert by["$40,430"]["start"] == 1.0 and by["$40,430"]["end"] == 1.9
    assert by["61.7%"]["start"] == 2.5
    assert all(b["start"] >= a["start"] for a, b in zip(words, words[1:]))
    srt = gc.build_srt_from_word_timestamps(words, 3)
    assert "$40,430" in srt and "61.7%" in srt


def test_main_landmass_framing_ignores_far_territories():
    import motion_kit as mk
    cam = mk.MapCamera((540, 690), [(-30, 70), (570, 70), (1175, 1250), (-635, 1250)], (270, 470))
    us_full, us_main = cam.mask("US")["full_bbox"], cam.mask("US")["bbox"]
    assert us_full[2] - us_full[0] > 7000          # Alaska/Aleutians/Hawaii span the map
    assert 1000 < us_main[2] - us_main[0] < 1600     # lower 48 only
    assert cam.shot_for("RU")[2] <= 6000


def test_comparison_split_frame_and_scoring():
    import render_comparison_motion as rc
    assert rc.winner({"comparable": True, "unit": "usd", "value_a": 2000, "value_b": 1000}) == "a"
    assert rc.winner({"comparable": False, "unit": "usd", "value_a": 2000, "value_b": 1000}) is None
    assert rc.winner({"comparable": True, "unit": "usd", "value_a": 1000, "value_b": 1000}) is None
    # values that display identically ("82.0 yrs") are a tie, here and in the narration
    same = {"comparable": True, "unit": "years", "value_a": 82.04, "value_b": 82.01}
    assert rc.winner(same) is None and comparison._winner(same) is None
    base = {"a": {"name": "United States", "iso2": "US"}, "b": {"name": "Japan", "iso2": "JP"},
            "flags": [None, None], "subtitle": "HEAD TO HEAD", "tagline": "4 STATS", "dur": 6.0}
    frame = rc.make_scene("intro", base)(3.0)
    assert frame.size == (1080, 1920)
    left, right = frame.getpixel((200, 450))[:3], frame.getpixel((880, 450))[:3]
    assert left != right  # two different panels/countries


def test_motion_intro_frame_and_duration_rule():
    import generate_motion_intro as gmi
    scene = gmi.make_scene({"name": "Japan", "lat": 36.2, "lon": 138.25, "iso": "JP", "flag": None, "duration": 3.4})
    end = scene(3.39)
    assert end.size == (1080, 1920)
    assert end.getpixel((540, 1300))[:3] != scene(0.1).getpixel((540, 1300))[:3]  # title card has landed


def test_transition_sfx_hits_every_cut(tmp_path):
    import json
    import subprocess
    import sys
    import wave
    script = {"narration": "one two three four five six", "segments": [
        {"text": "one two", "visual": ""}, {"text": "three four", "visual": ""}, {"text": "five six", "visual": ""}]}
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    out = tmp_path / "sfx.wav"
    subprocess.run([sys.executable, "scripts/make_transition_sfx.py", "--script", str(tmp_path / "script.json"),
                    "--audio", str(tmp_path / "missing.wav"), "--out", str(out)], check=True)
    with wave.open(str(out)) as w:
        assert w.getnframes() > 0


def test_waving_flag_keeps_transparency_and_fits_box(tmp_path):
    from PIL import Image
    import render_worlddata_motion as rw
    tall = Image.new("RGBA", (300, 600), (0, 0, 0, 0))
    tall.paste((220, 20, 60, 255), (0, 0, 150, 600))   # left half opaque, right half transparent
    path = tmp_path / "tall.png"
    tall.save(path)
    flag = rw.WavingFlag(str(path))
    assert flag.w <= rw.FLAG_MAX[0] and flag.h <= rw.FLAG_MAX[1]
    frame = flag.frame(1.0)
    assert frame.getpixel((int(flag.w * 0.9), flag.pad + flag.h // 2))[3] == 0   # transparent stays transparent
    assert frame.getpixel((5, flag.pad + flag.h // 2))[3] == 255


def test_longform_slide_animates_then_settles():
    import render_longform_rankings as rl
    rec = {"unit": "usd_big", "slide_title": "Top 20 Countries by GDP", "source_label": "World Bank", "as_of": "2025",
           "rows": [{"iso2": "GB", "country": "United Kingdom", "rank": 5, "tied": False, "value": "$4.00T",
                     "raw_value": 4.0e12, "year": "2025", "trend": [[2015, 2.9e12], [2025, 4.0e12]], "facts": []}]}
    row = rec["rows"][0]
    early = rl.country_slide(rec, row, {"GB"}, None, 0.1)
    final = rl.country_slide(rec, row, {"GB"}, None)
    assert final.getbbox() is not None
    assert list(early.getdata()) != list(final.getdata())
    assert list(rl.country_slide(rec, row, {"GB"}, None, rl.ANIM_SECONDS).getdata()) == list(final.getdata())


def test_comparison_rejects_gpt_beats_that_dont_match_the_stats(monkeypatch):
    import json as _json
    rows = [{"label": "GDP per Capita", "unit": "usd", "value_a": 2.0, "value_b": 1.0, "year_a": 2024,
             "year_b": 2024, "comparable": True}]

    class Fake:
        def __init__(self, **_):
            self.chat = self
            self.completions = self

        def create(self, **_):
            segs = [{"text": "Intro.", "visual": ""}, {"text": "GDP.", "visual": "GDP per Capita"},
                    {"text": "GDP again.", "visual": "GDP per Capita"}, {"text": "Bye.", "visual": ""}]
            msg = type("M", (), {"content": _json.dumps({"hook": "A VS B", "segments": segs})})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(comparison, "OpenAI", Fake, raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="don't match"):
        comparison._gpt_script("A", "B", rows, {}, 10, 200)
