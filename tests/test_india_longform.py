import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_india_longform as gl  # noqa: E402

SERIES = {
    "IN": [(2000, 100.0), (2005, 150.0), (2007, 160.0), (2008, 140.0), (2009, 190.0), (2010, 200.0), (2015, 300.0),
           (2020, 420.0), (2024, 500.0)],
    "1W": [(2000, 80.0), (2024, 130.0)],
    "CN": [(2024, 900.0)], "US": [(2024, 1200.0)],
}


def test_every_sentence_comes_from_the_series():
    gl.M["test"] = ("X", "Test measure", "usd", "up", "In {y}, it was {v}.", ["India"])
    text, chart = gl.chart_text(1, "test", SERIES)
    assert text.startswith("Chart 1: Test measure. In 2000, it was $100.")
    assert "And by 2024, $500." in text and "5.0 times the level of 2000" in text
    assert "It went the wrong way only in 2008, and then recovered." in text
    assert "China is at $900, and the United States at $1,200." in text
    assert chart["labels"]["2024"] == "$500"


def test_negative_values_are_said_as_minus():
    assert gl.say("pct", -5.8) == "minus 5.8 percent"


def test_rank_needs_enough_countries():
    assert gl.rank_in(SERIES, 2024, "up") is None  # 3 countries is not a ranking


def test_themes_use_known_measures():
    for name, measures in gl.THEMES.values():
        assert len(measures) >= 10 and all(m in gl.M for m in measures), name
