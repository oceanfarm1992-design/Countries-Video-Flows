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

WINDOW_DAYS = 28
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
    return videos, s, e


def _fmt(x, pct=False):
    return "–" if x is None else (f"{x * 100:.0f}%" if pct else f"{x:.0f}")


def report(videos, start, end):
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
        videos, start, end = collect()
        text = report(videos, start, end)
        print(f"[retention_report] {len(videos)} videos, {start} -> {end}")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
