import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_place_footage as pf  # noqa: E402


def test_place_terms_drop_generic_words():
    assert pf.place_terms({"name": "Gurez Valley", "page": "Gurez"}) == ["gurez"]
    assert pf.place_terms({"name": "Pangong Lake", "page": "Pangong Tso"}) == ["pangong"]
    assert pf.place_terms({"name": "Valley of Flowers", "page": "Valley of Flowers National Park"}) == ["flowers"]


def test_only_free_licences_pass():
    for ok in ("CC BY-SA 4.0", "CC BY 2.0", "CC0", "Public domain"):
        assert pf.FREE_LICENSE.match(ok), ok
    for bad in ("Copyrighted", "CC BY-NC 4.0", "CC BY-ND 3.0", ""):
        assert not pf.FREE_LICENSE.match(bad), bad


def test_maps_and_logos_are_not_footage():
    assert pf.SKIP_WORDS.search("File:Gurez locator map.png")
    assert not pf.SKIP_WORDS.search("File:Gurez valley view.jpg")


def test_videos_then_scenery_then_buildings():
    items = [{"title": "File:Gurez mosque.jpg", "type": "photo"},
             {"title": "File:Gurez valley river view.jpg", "type": "photo"},
             {"title": "File:Gurez clip.webm", "type": "video"}]
    assert [i["type"] for i in sorted(items, key=pf.scenic_rank)] == ["video", "photo", "photo"]
    assert sorted(items, key=pf.scenic_rank)[1]["title"] == "File:Gurez valley river view.jpg"


def test_credits_name_each_author_once():
    pieces = [{"source": "commons", "author": "A", "license": "CC BY-SA 4.0"},
              {"source": "commons", "author": "A", "license": "CC BY-SA 4.0"},
              {"source": "pexels", "attribution": "Pexels — B"}]
    assert pf.credits_line(pieces) == "Footage and photos: A (CC BY-SA 4.0, Wikimedia Commons); Pexels — B"
