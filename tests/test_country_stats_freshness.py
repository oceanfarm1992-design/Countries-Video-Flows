"""Offline tests for country-stats freshness: no network. Run with `python -m pytest tests`."""
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

import fetch_country_stats as fcs  # noqa: E402


def test_date_range_ends_at_current_year():
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert fcs._date_range(now) == "2003:2026"
    assert fcs._date_range(datetime(2027, 1, 1, tzinfo=timezone.utc)) == "2003:2027"


def test_live_fetch_url_uses_current_year(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *a, **k):
            return b'[{"page":1},[{"date":"2025","value":null},{"date":"2024","value":5.0}]]'

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(fcs.urllib.request, "urlopen", fake_urlopen)
    value, year = fcs._fetch_indicator_live("JP", "NY.GDP.PCAP.CD")
    assert f":{datetime.now(timezone.utc).year}" in seen["url"]
    # a null for the newest year is skipped, the latest real reading is used
    assert (value, year) == (5.0, 2024)


def _setup_failing_fetch(monkeypatch, tmp_path, entry):
    monkeypatch.setattr(fcs, "CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(fcs, "INDICATORS", [("gdp_per_capita", "NY.GDP.PCAP.CD", "GDP per Capita", "usd")])

    def boom(iso2, code):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(fcs, "_fetch_indicator_live", boom)
    fcs._save_cache({"JP:gdp_per_capita": entry})


def test_failed_live_call_uses_recent_cache(monkeypatch, tmp_path):
    entry = {"value": 1000.0, "year": 2024, "fetched_at": time.time() - 10 * 86400}
    _setup_failing_fetch(monkeypatch, tmp_path, entry)
    stats = fcs.get_stats("JP")
    assert stats["gdp_per_capita"]["value"] == 1000.0


def test_failed_live_call_ignores_cache_older_than_limit(monkeypatch, tmp_path):
    entry = {"value": 1000.0, "year": 2024,
             "fetched_at": time.time() - (fcs.CACHE_MAX_AGE_DAYS + 5) * 86400}
    _setup_failing_fetch(monkeypatch, tmp_path, entry)
    assert fcs.get_stats("JP") == {}
