"""Short India descriptions: title, keywords, source, credits, hashtags. Offline."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import captions_common as cc  # noqa: E402
import generate_india_untold_script as untold  # noqa: E402

with open(os.path.join(os.path.dirname(__file__), "..", "config", "india.json"), encoding="utf-8") as fh:
    CONFIG = json.load(fh)

SOURCES = {"lines": ["2000: $468B", "2010: $1.68T", "2025: $3.96T"],
           "credit": "Source: World Bank, indicator NY.GDP.MKTP.CD",
           "link": "https://data.worldbank.org/indicator/NY.GDP.MKTP.CD?locations=IN"}


def _write(tmp_path, series="india_rising", sources=SOURCES, tags="#IndiaGDP #IndiaRising"):
    cc.write_platform_captions(series, {"name": "India", "specialty": "GDP", "facts": []},
                               "India 2000 vs 2025: India's economy (GDP)", CONFIG, str(tmp_path), 3,
                               sources, keywords=["India GDP", "Indian economy growth"], extra_tags=tags)
    return {f: (tmp_path / f).read_text(encoding="utf-8")
            for f in ("caption_youtube.txt", "caption_meta.txt", "yt_title.txt", "yt_tags.txt")}


def test_title_keeps_its_natural_case(tmp_path):
    assert _write(tmp_path)["yt_title.txt"].strip() == "India 2000 vs 2025: India's economy (GDP) #Shorts"


def test_descriptions_are_short_with_keywords_source_and_one_link(tmp_path):
    out = _write(tmp_path)
    yt, fb = out["caption_youtube.txt"], out["caption_meta.txt"]
    assert len(yt) < 600 and len(fb) < 450
    assert "India GDP · Indian economy growth" in yt and "India GDP" in fb
    assert "2000: $468B → 2025: $3.96T" in yt and "2010" not in yt
    assert "Source: World Bank" in yt and "Source: World Bank" in fb
    assert yt.count("https://") == 1 and "https://" not in fb
    assert not fb.startswith("📈 India — India")


def test_hashtags_start_with_india_and_never_repeat(tmp_path):
    for name in ("caption_youtube.txt", "caption_meta.txt"):
        tags = _write(tmp_path)[name].strip().split("\n")[-1].split()
        assert tags[0] == "#India" and all(t.startswith("#") for t in tags)
        assert len({t.lower() for t in tags}) == len(tags)


def test_youtube_tags_are_the_keywords_then_the_hashtags(tmp_path):
    tags = _write(tmp_path)["yt_tags.txt"].split("\n")
    assert tags[:2] == ["India GDP", "Indian economy growth"] and "IndiaGDP" in tags


def test_the_world_channel_captions_are_unchanged(tmp_path):
    cc.write_platform_captions("rankings", {"name": "Most Polluted Air", "facts": []},
                               "TOP 8 MOST POLLUTED AIR", CONFIG, str(tmp_path), 3, SOURCES)
    assert not (tmp_path / "yt_tags.txt").exists()
    assert "2010: $1.68T" in (tmp_path / "caption_youtube.txt").read_text(encoding="utf-8")


def test_place_keywords_name_the_place_its_state_and_its_kind():
    place = {"name": "Gurez Valley", "region": "Jammu and Kashmir", "kind": "valley"}
    words, tags = untold.place_keywords(place)
    assert words[:3] == ["Gurez Valley", "Gurez Valley Jammu and Kashmir", "Jammu and Kashmir tourism"]
    assert "valley in India" in words
    assert tags.split()[:2] == ["#GurezValley", "#JammuAndKashmir"]
