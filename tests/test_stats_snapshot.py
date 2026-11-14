import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_country_stats as fcs  # noqa: E402
from save_stats_snapshot import merge, parse_owid, parse_worldbank  # noqa: E402


def test_worldbank_bulk_rows_become_iso2_values():
    payload = [{"page": 1}, [
        {"country": {"id": "IN"}, "countryiso3code": "IND", "date": "2025", "value": 2702.5},
        {"country": {"id": "UY"}, "countryiso3code": "URY", "date": "2025", "value": None}]]
    assert parse_worldbank(payload) == {"IN": (2702.5, 2025)}
    assert parse_worldbank([{"message": "error"}]) == {}


def test_owid_keeps_each_countrys_latest_year():
    text = "entity,code,year,life_expectancy_0\nIndia,IND,2022,71.0\nIndia,IND,2023,72.0\nWorld,OWID_WRL,2023,73\n"
    assert parse_owid(text, "life_expectancy_0", {"IND": "IN"}) == {"IN": (72.0, 2023)}


def test_merge_keeps_other_metrics():
    snap = {"IN": {"literacy": {"value": 78, "year": 2024}}}
    merge(snap, "population", {"IN": (1.4e9, 2025)}, "World Bank")
    assert set(snap["IN"]) == {"literacy", "population"}


def test_snapshot_is_used_only_while_recent(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"_saved": "2026-11-14", "IN": {"population": {"value": 5.0, "year": 2025}}}))
    soon = datetime(2026, 12, 1, tzinfo=timezone.utc)
    late = datetime(2027, 6, 1, tzinfo=timezone.utc)
    assert fcs.snapshot_value("IN", "population", str(path), soon) == (5.0, 2025)
    assert fcs.snapshot_value("IN", "population", str(path), late) is None
    assert fcs.snapshot_value("IN", "literacy", str(path), soon) is None


def test_live_failure_falls_back_to_the_snapshot(monkeypatch):
    def down(*a, **k):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(fcs, "_fetch_indicator_live", down)
    monkeypatch.setattr(fcs, "snapshot_value", lambda iso2, key, *a, **k: (42.0, 2025))
    stats = fcs.get_stats("UY", use_cache=False)
    assert stats["gdp_per_capita"]["value"] == 42.0 and len(stats) == len(fcs.INDICATORS)
