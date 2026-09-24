"""Cross-series posting guard shared by the per-series workflow gates.

GitHub silently drops most scheduled cron ticks, so every due slot tends to fire
at the first tick that survives. Without a shared check, slots from different
series then post minutes apart. This enforces a minimum gap between ANY two
posts and a nightly blackout, so late catch-up runs spread out over later ticks.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

MIN_GAP_MINUTES = 100
# Blackout 23:50-01:00 Dubai (UTC+4) == 19:50-21:00 UTC.
BLACKOUT_START = (19, 50)
BLACKOUT_END = (21, 0)
VIDEO_WORKFLOWS = {"daily-rotating-short", "daily-comparison-short", "daily-rankings-short"}


def _gh_json(args):
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def _other_build_running():
    """A build takes ~20 min before its release exists, so a release-only check
    would let two workflows that pass their gates close together both post."""
    own_id = os.environ.get("GITHUB_RUN_ID", "")
    runs = _gh_json(["run", "list", "--status", "in_progress", "--limit", "20",
                     "--json", "databaseId,workflowName"])
    return any(r["workflowName"] in VIDEO_WORKFLOWS and str(r["databaseId"]) != own_id
               for r in runs)


def spacing_block(now=None, min_gap_minutes=MIN_GAP_MINUTES):
    """Return a reason string if posting must wait, else None. Fails closed if
    GitHub can't be queried."""
    now = now or datetime.now(timezone.utc)
    if BLACKOUT_START <= (now.hour, now.minute) < BLACKOUT_END:
        return f"blackout window (now {now:%H:%M} UTC)"
    try:
        releases = _gh_json(["release", "list", "--limit", "20", "--json", "publishedAt"])
        building = _other_build_running()
    except Exception as exc:
        return f"could not query GitHub for spacing check ({type(exc).__name__}) -- failing closed"
    if building:
        return "another video workflow is mid-build"
    stamps = [datetime.fromisoformat(r["publishedAt"].replace("Z", "+00:00"))
              for r in releases if r.get("publishedAt")]
    if not stamps:
        return None
    age_min = (now - max(stamps)).total_seconds() / 60
    if age_min < min_gap_minutes:
        return f"last post was {age_min:.0f} min ago (< {min_gap_minutes} min gap)"
    return None
