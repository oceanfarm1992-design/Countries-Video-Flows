import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_rankings_stats as frs  # noqa: E402
import save_history_snapshot as hist  # noqa: E402


def test_parse_keeps_known_countries_oldest_first():
    payload = [{"page": 1}, [
        {"country": {"id": "IN"}, "date": "2024", "value": 3.9},
        {"country": {"id": "IN"}, "date": "2010", "value": 1.7},
        {"country": {"id": "EU"}, "date": "2024", "value": 18.0},
        {"country": {"id": "1W"}, "date": "2024", "value": 110.0},
        {"country": {"id": "IN"}, "date": "2023", "value": None}]]
    assert hist.parse(payload, {"IN", "1W"}) == {"1W": [[2024, 110.0]], "IN": [[2010, 1.7], [2024, 3.9]]}


def test_load_reads_a_saved_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hist, "HISTORY_DIR", str(tmp_path))
    (tmp_path / "X.json").write_text(json.dumps({"_saved": "2026-11-14", "series": {"IN": [[2020, 1.0]]}}))
    assert hist.load("X") == {"IN": [(2020, 1.0)]}
    assert hist.load("missing") == {}


def test_long_form_history_falls_back_to_the_saved_file(monkeypatch):
    def down(*a, **k):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(frs, "_fetch_bulk_rows", down)
    monkeypatch.setattr(hist, "load", lambda code: {"IN": [(2010, 1.0), (2020, 2.0)], "US": [(2020, 9.0)]})
    cfg = {"metrics": [{"id": "m", "source_type": "live", "wb_indicator": "X"}]}
    monkeypatch.setattr(frs, "_metric_by_id", lambda c, mid: c["metrics"][0])
    assert frs.get_series("m", ["IN"], cfg) == {"IN": [(2010, 1.0), (2020, 2.0)]}


def test_india_indicators_are_included():
    assert "EG.ELC.ACCS.ZS" in hist.indicators() and "NY.GDP.MKTP.CD" in hist.indicators()
