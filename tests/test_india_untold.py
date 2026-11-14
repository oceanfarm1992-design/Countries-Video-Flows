import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_india_untold_script as gen  # noqa: E402
import post_gate  # noqa: E402

PLACES = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config",
                                     "india_places.json"), encoding="utf-8"))["places"]


def test_one_place_per_day_and_wraps():
    assert gen.pick_place(PLACES, 0)[1] is PLACES[0]
    assert gen.pick_place(PLACES, len(PLACES))[1] is PLACES[0]
    assert gen.pick_place(PLACES, 3)[0] == 3


def test_place_can_be_forced_by_id_or_index():
    assert gen.pick_place(PLACES, 9, place_id="spiti")[1]["id"] == "spiti"
    assert gen.pick_place(PLACES, 9, index=1)[1] is PLACES[1]
    with pytest.raises(SystemExit):
        gen.pick_place(PLACES, 0, place_id="nowhere")


def test_hook_is_the_not_x_twist():
    gurez = PLACES[0]
    assert gen.hook_text(gurez) == "THIS IS NOT SWITZERLAND. THIS IS INDIA."
    assert gen.opening_line(gurez) == "This is not Switzerland. This is India."
    assert "Gurez Valley" in gen.title_hook(gurez)  # the title names the place


@pytest.mark.parametrize("place", PLACES, ids=lambda p: p["id"])
def test_fallback_uses_only_the_verified_facts(place):
    narration, segments = gen.fallback_script(place)
    assert narration.startswith(gen.opening_line(place))
    assert narration.endswith(gen.CTA)
    for fact in place["facts"]:
        assert fact["text"] in narration
    assert all(s["visual"] for s in segments)


def test_without_ai_keys_the_template_is_used(monkeypatch):
    monkeypatch.setattr(gen.ai_client, "available", lambda: None)
    narration, segments, source = gen.build_script(PLACES[0], {}, 80, 115)
    assert source == "fallback" and segments


def test_a_rejected_ai_script_falls_back(monkeypatch):
    monkeypatch.setattr(gen.ai_client, "available", lambda: "key")
    monkeypatch.setattr(gen, "_gpt_script", lambda *a: ("made up claim", [{"text": "x", "visual": "y"}]))
    monkeypatch.setattr(gen, "verify_narration", lambda *a, **k: (False, ["invented"]))
    assert gen.build_script(PLACES[0], {}, 80, 115)[2] == "fallback"


def test_an_accepted_ai_script_is_used(monkeypatch):
    monkeypatch.setattr(gen.ai_client, "available", lambda: "key")
    monkeypatch.setattr(gen, "_gpt_script", lambda *a: ("fine", [{"text": "fine", "visual": "v"}]))
    monkeypatch.setattr(gen, "verify_narration", lambda *a, **k: (True, []))
    assert gen.build_script(PLACES[0], {}, 80, 115)[2] == "openai"


def test_channels_do_not_see_each_others_releases():
    rel = [{"tagName": "india-untold-20261115-100000", "publishedAt": "2026-11-15T10:00:00Z"},
           {"tagName": "rotating-hook-20261115-090000", "publishedAt": "2026-11-15T09:00:00Z"}]
    stamp = lambda r: datetime.fromisoformat(r["publishedAt"].replace("Z", "+00:00"))  # noqa: E731
    assert post_gate.channel_release_times(rel, "india") == [stamp(rel[0])]
    assert post_gate.channel_release_times(rel, "world") == [stamp(rel[1])]


def test_india_post_waits_only_for_india_posts(monkeypatch):
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    recent_world = [{"tagName": "comparison-x-20261115-113000", "publishedAt": "2026-11-15T11:30:00Z"}]
    monkeypatch.setattr(post_gate, "_gh_json", lambda args: recent_world)
    monkeypatch.setattr(post_gate, "_other_build_running", lambda channel="world": False)
    assert post_gate.spacing_block(now, channel="india") is None
    assert "last post" in post_gate.spacing_block(now, channel="world")
