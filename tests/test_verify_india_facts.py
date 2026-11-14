import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from verify_india_facts import check_fact, verify_place  # noqa: E402


def test_number_matches_despite_thousands_commas_and_nbsp():
    assert check_fact({"check": ["2,400"]}, "at an altitude of 2400 m")
    assert check_fact({"check": ["2400"]}, "at an altitude of 2,400 m")


def test_missing_number_fails():
    assert not check_fact({"check": ["4,350"]}, "an elevation of 14,200 ft (4,300 m)")


def test_any_of_several_checks_is_enough():
    assert check_fact({"check": ["Kishanganga", "Neelum"]}, "the Neelum river")


def test_fact_uses_its_own_page_when_given():
    place = {"page": "Main", "facts": [{"text": "a", "check": ["x"]},
                                       {"text": "b", "page": "Other", "check": ["y"]}]}
    pages = {"Main": "x here", "Other": "y here"}
    assert verify_place(place, fetch=pages.get) == [("a", True), ("b", True)]


def test_every_curated_place_is_complete():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "india_places.json")
    places = json.load(open(path, encoding="utf-8"))["places"]
    ids = [p["id"] for p in places]
    assert len(ids) == len(set(ids))
    for p in places:
        assert p["facts"] and p["footage"] and p["match"] and p["compare"], p["id"]
        assert all(f["check"] for f in p["facts"]), p["id"]
