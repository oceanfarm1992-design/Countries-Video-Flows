#!/usr/bin/env python3
"""
Opening A/B test (started 2026-11-13): which opening keeps viewers longer?

  new      the designed hook card in the first second, the "Can you guess #1?"
           silhouette (rankings / worst-of), "Who wins? Pick a side" (comparison)
  classic  the opening before the 2026-10-14 editing upgrade: plain hook text over
           the first seconds, no tease, the old comparison tagline

Everything else (captions, sound, pacing) is identical. Each series alternates
post by post -- not by calendar day: with one Short a day taking turns, a series
could otherwise always land on the same parity and the two groups would differ in
more than the opening. Every live post is logged to logs/opening_variants.csv;
the weekly retention report matches videos to it by publish date (one Short a day).

Usage (workflows):
    OPENING=$(python scripts/opening_variant.py pick --series rankings)
    python scripts/opening_variant.py log --series rankings --variant "$OPENING"
"""
import argparse
import csv
import os
from datetime import datetime, timezone

LOG = "logs/opening_variants.csv"
VARIANTS = ("new", "classic")


def _rows(path=LOG):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return [r for r in csv.reader(fh) if len(r) >= 3 and r[0] != "date"]


def pick(series, path=LOG):
    """Alternate per series: its 1st, 3rd, 5th... logged post is 'new', the others 'classic'."""
    done = sum(1 for r in _rows(path) if r[1] == series)
    return VARIANTS[done % 2]


def log(series, variant, path=LOG):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["date", "series", "variant"])
        w.writerow([datetime.now(timezone.utc).date().isoformat(), series, variant])


def variants_by_date(path=LOG):
    """{UTC date: variant} for the report (one Short posts per day)."""
    return {r[0]: r[2] for r in _rows(path)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--series", required=True)
    lg = sub.add_parser("log")
    lg.add_argument("--series", required=True)
    lg.add_argument("--variant", required=True, choices=VARIANTS)
    args = ap.parse_args()
    if args.cmd == "pick":
        print(pick(args.series))
    else:
        log(args.series, args.variant)


if __name__ == "__main__":
    main()
