"""Cross-series posting guard shared by the per-series workflow gates.

GitHub silently drops most scheduled cron ticks, so every due slot tends to fire
at the first tick that survives. Without a shared check, slots from different
series then post minutes apart. This enforces a minimum gap between ANY two
posts and a nightly blackout, so late catch-up runs spread out over later ticks.
"""
import json
import subprocess
from datetime import datetime, timezone

MIN_GAP_MINUTES = 100
# Blackout 23:50-01:00 Dubai (UTC+4) == 19:50-21:00 UTC.
BLACKOUT_START = (19, 50)
BLACKOUT_END = (21, 0)


def spacing_block(now=None, min_gap_minutes=MIN_GAP_MINUTES):
    """Return a reason string if posting must wait, else None. Fails closed if
    the release list can't be read."""
    now = now or datetime.now(timezone.utc)
    if BLACKOUT_START <= (now.hour, now.minute) < BLACKOUT_END:
        return f"blackout window (now {now:%H:%M} UTC)"
    try:
        out = subprocess.run(
            ["gh", "release", "list", "--limit", "20", "--json", "publishedAt"],
            check=True, capture_output=True, text=True).stdout
        stamps = [datetime.fromisoformat(r["publishedAt"].replace("Z", "+00:00"))
                  for r in json.loads(out) if r.get("publishedAt")]
    except Exception as exc:
        return f"could not read releases for spacing check ({type(exc).__name__}) -- failing closed"
    if not stamps:
        return None
    age_min = (now - max(stamps)).total_seconds() / 60
    if age_min < min_gap_minutes:
        return f"last post was {age_min:.0f} min ago (< {min_gap_minutes} min gap)"
    return None
