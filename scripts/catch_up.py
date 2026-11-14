#!/usr/bin/env python3
"""
Safety net for dropped GitHub schedules: start today's Short workflow if GitHub
stopped starting it.

GitHub drops scheduled runs when it is busy. On 2026-11-14 the comparison workflow
(today's turn) got no scheduled run for 6+ hours while the other video workflows
kept ticking -- they saw "not my turn" and stood aside, so the day's video could
have been skipped entirely. Every video workflow now runs this after its gate says
skip: if today's Short isn't posted yet and its workflow hasn't run for
STALE_MINUTES, start it (dry_run=false, force_run=false). That workflow's own gate
still decides whether it's due and not yet posted, and its concurrency group stops
two builds overlapping -- so this can't double-post or post early.

Usage (workflows, GH_TOKEN with actions: write):
    python scripts/catch_up.py --me rankings
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

from daily_variation import short_series_for

EPOCH = date(2026, 7, 23)
WORKFLOWS = {"comparison": "daily-comparison-short.yml", "rankings": "daily-rankings-short.yml",
             "rotating": "daily-rotating-short.yml"}
# Scheduled ticks are every 30 min; this long without a run means GitHub is dropping them.
STALE_MINUTES = 75


def _gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def posted_today(series, now):
    tag = f"-{now:%Y%m%d}-"
    rows = json.loads(_gh("release", "list", "--limit", "100", "--json", "tagName"))
    return any(r["tagName"].startswith(f"{series}-") and tag in r["tagName"] for r in rows)


def minutes_since_last_run(workflow, now):
    rows = json.loads(_gh("run", "list", "--workflow", workflow, "--limit", "1",
                          "--json", "createdAt,status"))
    if not rows:
        return None, None
    started = datetime.fromisoformat(rows[0]["createdAt"].replace("Z", "+00:00"))
    return (now - started) / timedelta(minutes=1), rows[0]["status"]


def decide(me, turn, posted, minutes, status):
    """(start?, reason) -- pure, for tests."""
    if turn is None or turn == me:
        return False, "it's this workflow's own turn -- its gate handles it"
    if posted:
        return False, f"today's {turn} video is already posted"
    if status in ("in_progress", "queued", "pending", "waiting", "requested"):
        return False, f"{turn} is already running"
    if minutes is not None and minutes < STALE_MINUTES:
        return False, f"{turn} ran {minutes:.0f} min ago -- its schedule is ticking"
    return True, (f"{turn} hasn't run for {'ever' if minutes is None else f'{minutes:.0f} min'} "
                  f"and today's video isn't posted -- GitHub is dropping its schedule")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--me", required=True, help="this workflow's series (rankings, comparison, rotating, longform)")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    turn = short_series_for((now.date() - EPOCH).days)
    try:
        if turn is None or turn == args.me:
            start, reason = decide(args.me, turn, False, None, None)
        else:
            minutes, status = minutes_since_last_run(WORKFLOWS[turn], now)
            start, reason = decide(args.me, turn, posted_today(turn, now), minutes, status)
        if start:
            _gh("workflow", "run", WORKFLOWS[turn], "-f", "dry_run=false", "-f", "force_run=false")
            print(f"::warning::[catch_up] started {WORKFLOWS[turn]}: {reason}")
        else:
            print(f"[catch_up] nothing to do: {reason}")
    except Exception as exc:  # noqa: BLE001 -- a safety net must never fail the run it rides on
        print(f"[catch_up] skipped ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
