import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import post_facebook as fb  # noqa: E402


class Resp:
    def __init__(self, data, code=200):
        self.data, self.status_code, self.text = data, code, str(data)

    def json(self):
        return self.data


class FakeGraph:
    """Records calls; answers like the Graph API Reels flow."""

    def __init__(self, statuses=("in_progress", "complete")):
        self.calls, self.statuses = [], list(statuses)

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(("GET", url, params or {}, headers or {}))
        if url.endswith("/123") and (params or {}).get("fields") == "access_token":
            return Resp({"access_token": "PAGE_TOKEN", "id": "123"})
        if url.endswith("/123"):
            return Resp({"id": "123", "name": "Incredible India Facts"})
        return Resp({"status": {"uploading_phase": {"status": self.statuses.pop(0)}}})

    def post(self, url, params=None, timeout=None, headers=None):
        self.calls.append(("POST", url, params or {}, headers or {}))
        if params and params.get("upload_phase") == "start":
            return Resp({"video_id": "v1", "upload_url": "https://rupload.facebook.com/x"})
        return Resp({"success": True})


def test_reel_is_started_pulled_from_the_url_and_published():
    g = FakeGraph()
    page = fb.Page("123", "TOKEN", session=g)
    assert page.post_reel("https://github.com/x/final.mp4", "caption", sleep=lambda s: None) == "v1"
    pull = next(c for c in g.calls if "rupload" in c[1])
    assert pull[3]["file_url"] == "https://github.com/x/final.mp4"
    finish = [c for c in g.calls if c[2].get("upload_phase") == "finish"][0]
    assert finish[2]["video_state"] == "PUBLISHED" and finish[2]["description"] == "caption"


def test_check_names_the_page():
    assert fb.Page("123", "T", session=FakeGraph()).name() == "Incredible India Facts"


def test_graph_errors_are_raised_without_the_token():
    with pytest.raises(fb.FacebookError) as e:
        fb._check(Resp({"error": {"message": "Invalid OAuth access token", "code": 190}}, 400))
    assert "190" in str(e.value) and "TOKEN" not in str(e.value)


def test_a_failed_fetch_stops_before_publishing():
    g = FakeGraph(statuses=("error",))
    with pytest.raises(fb.FacebookError):
        fb.Page("123", "T", session=g).post_reel("u", "c", sleep=lambda s: None)
    assert not any(c[2].get("upload_phase") == "finish" for c in g.calls)


def test_without_secrets_it_skips(monkeypatch):
    monkeypatch.delenv("FACEBOOK_PAGE_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_PAGE_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["post_facebook.py", "--check"])
    assert fb.main() == 0


def test_a_system_user_token_is_swapped_for_the_pages_own_token():
    g = FakeGraph()
    page = fb.Page("123", "SYSTEM_TOKEN", session=g)
    assert page.use_page_token() and page.token == "PAGE_TOKEN"
    page.post_reel("u", "c", sleep=lambda s: None)
    finish = [c for c in g.calls if c[2].get("upload_phase") == "finish"][0]
    assert finish[2]["access_token"] == "PAGE_TOKEN"
