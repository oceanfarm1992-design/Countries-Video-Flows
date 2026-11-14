import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_india_broll as broll  # noqa: E402
import generate_india_rising_script as gen  # noqa: E402


def test_only_clips_that_name_india_count():
    assert broll.names_india("File:Container ship at Mumbai port.jpg")
    assert broll.names_india("aerial-view-of-chennai-harbour")
    assert not broll.names_india("File:Container ship at Rotterdam.jpg")
    assert not broll.names_india("indiana-cornfield")  # whole words only


def test_every_measure_has_footage_searches():
    for m in gen.METRICS:
        assert m["broll"] and all("India" in q or any(c in q for c in ("Mumbai", "Bangalore")) for q in m["broll"]), m["id"]
