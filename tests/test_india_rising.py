import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_india_rising_script as gen  # noqa: E402
import render_india_rising as ren  # noqa: E402

PTS = [(2000, 60.0), (2005, 67.0), (2010, 76.0), (2015, 88.0), (2020, 96.0), (2024, 99.9)]
WORLD = [(2000, 78.0), (2024, 92.0)]
ELEC = next(m for m in gen.METRICS if m["id"] == "electricity")


def test_spoken_and_shown_values():
    assert gen.spoken("pct", 99.9) == "nearly 100 percent" and gen.shown("pct", 99.9) == "99.9%"
    assert gen.spoken("pct", 2.6) == "2.6 percent" and gen.spoken("pct", 60.2) == "60 percent"
    assert gen.spoken("usd_big", 3.96e12) == "$3.96 trillion" and gen.shown("usd_big", 4.68e11) == "$468B"


def test_facts_use_first_middle_and_last_reading():
    f = gen.facts_for(ELEC, PTS, WORLD)
    assert (f["y0"], f["v0"], f["y1"], f["v1"], f["w1"]) == (2000, 60.0, 2024, 99.9, 92.0)
    assert f["ym"] == 2010


def test_narration_says_only_the_data():
    f = gen.facts_for(ELEC, PTS, WORLD)
    text = " ".join(s["text"] for s in gen.narration(ELEC, f))
    assert "In 2000, 60 percent of Indians had electricity." in text
    assert "By 2024, it was nearly 100 percent." in text and "India is now ahead of it." in text
    assert text.endswith(gen.CTA)


def test_behind_the_world_is_said_honestly():
    poverty = next(m for m in gen.METRICS if m["id"] == "poverty")
    f = gen.facts_for(poverty, [(2004, 46.0), (2011, 23.0), (2015, 15.0), (2023, 2.6)], [(2023, 1.0)])
    text = " ".join(s["text"] for s in gen.narration(poverty, f))
    assert "closing the gap" in text and "down 43 percentage points since 2004" in text


def test_too_little_history_stops():
    import pytest
    with pytest.raises(SystemExit):
        gen.facts_for(ELEC, PTS[:2], WORLD)


def test_line_interpolates_and_progress_is_bounded():
    assert ren.eval_line(PTS, 2002.5) == 63.5 and ren.eval_line(PTS, 1990) == 60.0
    marks = {"grow": 5.0, "end": 10.0, "end_dur": 4.0}
    assert ren.progress_at(4.0, marks) == 0.0 and ren.progress_at(30.0, marks) == 1.0
    assert 0 < ren.progress_at(8.0, marks) < 1


def test_world_totals_never_squash_indias_line():
    gdp = {"india": [(2000, 4.7e11), (2025, 3.96e12)], "world": [(2000, 3.4e13), (2025, 1.1e14)], "labels": {}}
    c = ren.Chart(gdp, "usd_big", {})
    assert c.world == [] and c.vmax < 5e12
    _, y_top = c.xy(2025, 3.96e12)
    assert y_top < ren.PLOT[1] + 0.2 * (ren.PLOT[3] - ren.PLOT[1])  # near the top, not the floor


def test_comparable_world_average_is_kept():
    elec = {"india": PTS, "world": WORLD, "labels": {}}
    assert ren.Chart(elec, "pct", {}).world == WORLD
