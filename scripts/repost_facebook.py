#!/usr/bin/env python3
"""
Post an already-published India Short to the Facebook Page when the Facebook step missed it:
the video comes from its GitHub release, the caption from its own YouTube title and
description, so both platforms carry the same text and source credits.

Environment: the India YouTube secrets (to read the description) and Facebook Page secrets.

Usage (workflow repost-india-facebook.yml):
    python scripts/repost_facebook.py --release india-untold-20261114-163417 --youtube-id nqKCIOZh55o
"""
import argparse
import os
import sys

from post_facebook import FacebookError, Page

REPO = "oceanfarm1992-design/Countries-Video-Flows"


def youtube_text(video_id):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
                        client_id=os.environ["YOUTUBE_CLIENT_ID"],
                        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token")
    items = build("youtube", "v3", credentials=creds, cache_discovery=False) \
        .videos().list(part="snippet", id=video_id).execute().get("items", [])
    if not items:
        raise SystemExit(f"YouTube video {video_id} not found on this channel")
    sn = items[0]["snippet"]
    title = sn["title"].replace("#Shorts", "").strip()
    return f"{title}\n\n{sn['description']}".strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", required=True)
    ap.add_argument("--youtube-id", required=True)
    args = ap.parse_args()
    caption = youtube_text(args.youtube_id).replace("#Shorts", "").strip()
    url = f"https://github.com/{REPO}/releases/download/{args.release}/final.mp4"
    page = Page(os.environ["FACEBOOK_PAGE_ID"], os.environ["FACEBOOK_PAGE_TOKEN"],
                os.environ.get("FB_GRAPH_VERSION", "v23.0"))
    try:
        page.use_page_token()
        print(f"[repost_facebook] posting {args.release} to {page.name()}")
        video_id = page.post_reel(url, caption)
    except FacebookError as exc:
        print(f"[repost_facebook] failed: {exc}")
        return 1
    print(f"[repost_facebook] done: https://www.facebook.com/reel/{video_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
