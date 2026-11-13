#!/usr/bin/env python3
"""
Weekly retention report: what keeps viewers watching, per series and per video, from
the channel's own YouTube Analytics -- the feedback loop for the editing.

For every video with views in the last WINDOW_DAYS days:
  - views, average % viewed, average seconds watched
  - opening hold: the share of viewers still watching at OPENING of the video (how
    well the first seconds -- the hook -- work)
  - ending hold: still watching at ENDING (did the payoff land)
Grouped per series (rankings, worst-of, comparison, single-country Shorts, long-form),
with the best and worst videos side by side so the difference is visible.

Needs the read-only token from authorize_youtube_analytics.py
(YOUTUBE_ANALYTICS_REFRESH_TOKEN); without it, writes a short how-to instead.

Usage:
    python scripts/retention_report.py --out build/retention_report.md
"""
import argparse
import os
import re
import sys
import time
from datetime import date, timedelta

from opening_variant import variants_by_date

WINDOW_DAYS = 28
# Eras for the before/after table: what changed when, so a drop can be pinned to its cause.
ERAS = [("2026-09-15", "before the editing upgrade"),
        ("2026-10-14", "new editing, 3 Shorts a day"),
        ("2026-10-29", "new editing, 1 Short a day"),
        ("2026-11-14", "own voice (RVC), storyteller style")]
OPENING, ENDING = 0.1, 0.9
TOKEN_URI = "https://oauth2.googleapis.com/token"
WORST_WORDS = ("most corrupt", "most polluted", "least democratic", "unhappiest", "biggest polluters",
               "highest inflation", "highest unemployment", "highest murder")


def series_of(title, seconds):
    t = title.lower()
    if seconds and seconds > 300:
        return "long-form"
    if any(w in t for w in WORST_WORDS):
        return "worst-of"
    if re.search(r"\btop \d+\b", t):
        return "rankings"
    if re.search(r"\bvs\.?\b", t):
        return "comparison"
    return "single-country"


def _iso_seconds(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return sum(int(v or 0) * k for v, k in zip(m.groups(), (3600, 60, 1))) if m else 0


def services():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(token=None, refresh_token=os.environ["YOUTUBE_ANALYTICS_REFRESH_TOKEN"],
                        token_uri=TOKEN_URI, client_id=os.environ["YOUTUBE_CLIENT_ID"],
                        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"])
    return build("youtubeAnalytics", "v2", credentials=creds), build("youtube", "v3", credentials=creds)


def hold_at(analytics, video_id, start, end, ratio):
    """audienceWatchRatio at the closest measured point to `ratio` of the video, or None.
    The API answers 500 for videos with too little data -- retried once, then skipped
    rather than failing the whole report."""
    for attempt in (1, 2):
        try:
            rows = analytics.reports().query(
                ids="channel==MINE", startDate=start, endDate=end, metrics="audienceWatchRatio",
                dimensions="elapsedVideoTimeRatio", filters=f"video=={video_id}").execute().get("rows", [])
            break
        except Exception as exc:  # noqa: BLE001 -- googleapiclient HttpError and transport errors
            if attempt == 2:
                print(f"[retention_report] no retention curve for {video_id} ({type(exc).__name__})")
                return None
            time.sleep(2)
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r[0] - ratio))[1]


def collect():
    analytics, youtube = services()
    end = date.today() - timedelta(days=2)          # analytics lags ~2 days
    start = end - timedelta(days=WINDOW_DAYS)
    s, e = start.isoformat(), end.isoformat()
    rows = analytics.reports().query(
        ids="channel==MINE", startDate=s, endDate=e, dimensions="video",
        metrics="views,averageViewPercentage,averageViewDuration", sort="-views",
        maxResults=200).execute().get("rows", [])
    ids = [r[0] for r in rows]
    meta = {}
    for i in range(0, len(ids), 50):
        for v in youtube.videos().list(part="snippet,contentDetails", id=",".join(ids[i:i + 50])).execute()["items"]:
            meta[v["id"]] = (v["snippet"]["title"], _iso_seconds(v["contentDetails"]["duration"]),
                             v["snippet"]["publishedAt"][:10])
    videos = []
    for vid, views, pct, secs in rows:
        title, length, published = meta.get(vid, (vid, 0, ""))
        videos.append({"id": vid, "title": title, "length": length, "published": published,
                       "views": int(views), "pct": pct, "secs": secs,
                       "series": series_of(title, length),
                       "open": hold_at(analytics, vid, s, e, OPENING),
                       "end": hold_at(analytics, vid, s, e, ENDING)})
    try:
        eras = collect_eras(analytics, youtube, e)
    except Exception as exc:  # noqa: BLE001 -- the comparison is extra; the weekly report must still post
        print(f"[retention_report] before/after table skipped ({type(exc).__name__}: {exc})")
        eras = None
    return videos, s, e, eras


def _era(published):
    name = None
    for start, label in ERAS:
        if published >= start:
            name = label
    return name


def collect_eras(analytics, youtube, end):
    """Every video published since the first era, measured over its whole life so far
    (not just the last 28 days), grouped by series and era."""
    # the top-videos report takes no startIndex (400) -- one page of 200 covers the
    # channel's videos since the first era
    rows = analytics.reports().query(
        ids="channel==MINE", startDate=ERAS[0][0], endDate=end, dimensions="video",
        metrics="views,averageViewPercentage,averageViewDuration", sort="-views",
        maxResults=200).execute().get("rows", [])
    meta = {}
    ids = [r[0] for r in rows]
    for i in range(0, len(ids), 50):
        for v in youtube.videos().list(part="snippet,contentDetails", id=",".join(ids[i:i + 50])).execute()["items"]:
            meta[v["id"]] = (v["snippet"]["title"], _iso_seconds(v["contentDetails"]["duration"]),
                             v["snippet"]["publishedAt"][:10])
    groups, ab = {}, {}
    variants = variants_by_date()   # opening A/B test: which opening each day's Short used

    def add(bucket, key, views, pct, secs):
        g = bucket.setdefault(key, {"videos": 0, "views": 0, "pct_w": 0.0, "secs_w": 0.0})
        g["videos"] += 1
        g["views"] += int(views)
        g["pct_w"] += pct * int(views)       # view-weighted, so a 3-view video can't swing it
        g["secs_w"] += secs * int(views)

    for vid, views, pct, secs in rows:
        title, length, published = meta.get(vid, ("", 0, ""))
        era = _era(published) if published else None
        if era is None or not views:
            continue
        series = series_of(title, length)
        add(groups, (series, era), views, pct, secs)
        if series != "long-form" and published in variants:
            add(ab, (series, variants[published]), views, pct, secs)
    return {"eras": groups, "ab": ab}


def ab_table(ab):
    lines = ["", "## Opening A/B test: new vs classic opening (since 2026-11-13)", "",
             "Each series alternates post by post; only the opening differs. Judge on several videos "
             "per side, not one.", "",
             "| Series | Opening | Videos | Views/video | Avg % viewed | Avg seconds watched |",
             "|---|---|---|---|---|---|"]
    for series in sorted({k[0] for k in ab}):
        for variant in ("new", "classic"):
            g = ab.get((series, variant))
            if g:
                lines.append(f"| {series} | {variant} | {g['videos']} | {g['views'] / g['videos']:.0f} | "
                             f"{g['pct_w'] / g['views']:.0f}% | {g['secs_w'] / g['views']:.0f} |")
    return "\n".join(lines)


def eras_table(groups):
    lines = ["", "## Before vs after (videos by publish date, whole life so far)", "",
             "Avg % viewed and seconds are weighted by views. Newer videos have had less time to collect views.", "",
             "| Series | Period | Videos | Views/video | Avg % viewed | Avg seconds watched |",
             "|---|---|---|---|---|---|"]
    order = [label for _, label in ERAS]
    for series in sorted({k[0] for k in groups}):
        for era in order:
            g = groups.get((series, era))
            if not g:
                continue
            lines.append(f"| {series} | {era} | {g['videos']} | {g['views'] / g['videos']:.0f} | "
                         f"{g['pct_w'] / g['views']:.0f}% | {g['secs_w'] / g['views']:.0f} |")
    return "\n".join(lines)


def _fmt(x, pct=False):
    return "–" if x is None else (f"{x * 100:.0f}%" if pct else f"{x:.0f}")


def report(videos, start, end, eras=None):
    lines = [f"# Retention report {start} → {end}", "",
             f"{len(videos)} videos with views. *Opening hold* = share of viewers still watching "
             f"{OPENING:.0%} into the video (how well the hook works); *ending hold* = still watching at "
             f"{ENDING:.0%} (did the payoff land). Above 100% means viewers replayed (Shorts loop).", "",
             "## By series", "", "| Series | Videos | Views | Avg % viewed | Opening hold | Ending hold |",
             "|---|---|---|---|---|---|"]
    by = {}
    for v in videos:
        by.setdefault(v["series"], []).append(v)

    def avg(vs, k):
        xs = [v[k] for v in vs if v[k] is not None]
        return sum(xs) / len(xs) if xs else None
    for name, vs in sorted(by.items(), key=lambda kv: -sum(v["views"] for v in kv[1])):
        lines.append(f"| {name} | {len(vs)} | {sum(v['views'] for v in vs)} | {_fmt(avg(vs, 'pct'))}% | "
                     f"{_fmt(avg(vs, 'open'), True)} | {_fmt(avg(vs, 'end'), True)} |")
    # only videos with a measured curve: "no data" is not a weak opening
    ranked = sorted([v for v in videos if v["views"] >= 20 and v["open"] is not None], key=lambda v: -v["open"])
    for heading, chunk in (("Best openings (learn from these)", ranked[:5]),
                           ("Weakest openings (what to change)", ranked[-5:][::-1])):
        lines += ["", f"## {heading}", "", "| Video | Series | Views | Opening hold | Avg % viewed |",
                  "|---|---|---|---|---|"]
        for v in chunk:
            title = v["title"][:70].replace("|", "\\|")  # a bare | splits the table cell
            lines.append(f"| [{title}](https://youtu.be/{v['id']}) | {v['series']} | {v['views']} | "
                         f"{_fmt(v['open'], True)} | {_fmt(v['pct'])}% |")
    if eras and eras.get("eras"):
        lines.append(eras_table(eras["eras"]))
    if eras and eras.get("ab"):
        lines.append(ab_table(eras["ab"]))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build/retention_report.md")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if not os.environ.get("YOUTUBE_ANALYTICS_REFRESH_TOKEN"):
        text = ("# Retention report not set up yet\n\nRun `python scripts/authorize_youtube_analytics.py` "
                "once on your computer and add the printed token as the repository secret "
                "`YOUTUBE_ANALYTICS_REFRESH_TOKEN` (instructions at the top of that file).\n")
        print("[retention_report] no analytics token -- wrote the setup note")
    else:
        videos, start, end, eras = collect()
        text = report(videos, start, end, eras)
        print(f"[retention_report] {len(videos)} videos, {start} -> {end}")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
