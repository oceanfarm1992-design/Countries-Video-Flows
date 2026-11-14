import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import famous_split as fs  # noqa: E402

PLACES = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config", "india_places.json"),
                        encoding="utf-8"))["places"]


def test_every_comparison_is_known():
    for p in PLACES:
        assert p["compare"].lower() in fs.FAMOUS, p["compare"]


def test_labels_drop_the_article():
    assert fs.label_for("the Maldives") == "MALDIVES"
    assert fs.label_for("Sri Lanka") == "SRI LANKA"
    assert fs.label_for("Bolivia's salt flats") == "BOLIVIA'S SALT FLATS"


def test_places_without_real_footage_have_no_split():
    assert fs.famous_entry("the Moon") is None and fs.famous_entry("Mars") is None
    assert fs.famous_entry("Dubai")[1] == ["dubai"]


def test_comparison_matches_the_kind_of_place():
    by_kind = {"beach": {"the maldives", "bali", "the galapagos"}, "islands": {"the maldives"},
               "tea": {"sri lanka"}, "skyline": {"dubai"}, "waterfall": {"niagara"}, "backwaters": {"venice"}}
    for p in PLACES:
        if p["kind"] in by_kind:
            assert p["compare"].lower() in by_kind[p["kind"]], (p["id"], p["compare"])
