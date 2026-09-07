#!/usr/bin/env python3
"""
Post the short to TikTok via the Content Posting API, refreshing auth on every run.

Why refresh every run: TikTok access tokens expire in ~24h, and the refresh_token
ROTATES on each use (the old one becomes invalid). So each run must:
  1. Exchange the stored refresh_token for a new access_token + new refresh_token.
     POST https://open.tiktokapis.com/v2/oauth/token/  (x-www-form-urlencoded)
        client_key, client_secret, grant_type=refresh_token, refresh_token
  2. IMMEDIATELY persist the NEW refresh_token back to GitHub Secrets, or the next
     run is locked out.
  3. Upload + publish the video.

Publishing (FILE_UPLOAD flow, single chunk for our small <64MB video):
  a. POST /v2/post/publish/video/init/   with post_info + source_info
     -> { data: { publish_id, upload_url } }
  b. PUT the raw bytes to upload_url with a Content-Range header.
Docs: https://developers.tiktok.com/doc/content-posting-api-get-started

# NOTE: Until the app passes TikTok's audit, direct posts are restricted. We publish
# with privacy_level=SELF_ONLY (only visible to the account owner). The video will need
# a manual visibility change in the TikTok app after the app is approved. An alternative
# is the "inbox" endpoint (/v2/post/publish/inbox/video/init/) which drops the video into
# the user's TikTok drafts to finish manually — switch INIT_URL below if you prefer that.

# VERIFY: current required scopes are video.publish (and video.upload for inbox). Confirm
# your app has them at https://developers.tiktok.com and that the exact host
# open.tiktokapis.com / endpoint paths still match the current docs.

Env / GitHub Secrets:
    TIKTOK_CLIENT_KEY
    TIKTOK_CLIENT_SECRET
    TIKTOK_REFRESH_TOKEN   (rotated + rewritten on every run)
    GH_PAT                 PAT with Secrets:write
    GITHUB_REPOSITORY      owner/name (auto in Actions)

Usage:
    python scripts/post_tiktok.py --video build/final.mp4 --title-file build/caption_tiktok.txt
    python scripts/post_tiktok.py --video build/final.mp4 --no-write   # skip secret rewrite (local test)
"""
import argparse
import os
import sys

import requests

from update_github_secret import update_secret

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
# inbox/draft alternative:
# INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"


def refresh_tokens(client_key, client_secret, refresh_token):
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise SystemExit(f"TikTok token refresh failed: {data}")
    return data["access_token"], data["refresh_token"]


def init_upload(access_token, title, video_size):
    body = {
        "post_info": {
            "title": title,
            "privacy_level": "SELF_ONLY",  # required while app is unaudited
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,     # whole file in one chunk
            "total_chunk_count": 1,
        },
    }
    r = requests.post(
        INIT_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()["data"]
    return data["publish_id"], data["upload_url"]


def upload_bytes(upload_url, video_path, video_size):
    with open(video_path, "rb") as f:
        content = f.read()
    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(video_size),
        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
    }
    r = requests.put(upload_url, headers=headers, data=content, timeout=600)
    r.raise_for_status()
    print(f"[post_tiktok] uploaded {video_size} bytes (HTTP {r.status_code})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="build/final.mp4")
    ap.add_argument("--title-file", default="build/caption_tiktok.txt")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-write", action="store_true",
                    help="Do not rewrite the rotated refresh_token to GitHub secrets.")
    args = ap.parse_args()

    client_key = os.environ["TIKTOK_CLIENT_KEY"]
    client_secret = os.environ["TIKTOK_CLIENT_SECRET"]
    refresh_token = os.environ["TIKTOK_REFRESH_TOKEN"]

    if args.title is not None:
        title = args.title
    elif os.path.exists(args.title_file):
        with open(args.title_file, encoding="utf-8") as f:
            title = f.read().strip()
    else:
        title = "Daily motivation"

    # 1. refresh (rotates the refresh token)
    access_token, new_refresh = refresh_tokens(client_key, client_secret, refresh_token)
    print("[post_tiktok] refreshed access token")

    # 2. persist rotated refresh token IMMEDIATELY
    if not args.no_write:
        repo = os.environ["GITHUB_REPOSITORY"]
        gh_pat = os.environ["GH_PAT"]
        update_secret(repo, "TIKTOK_REFRESH_TOKEN", new_refresh, gh_pat)
    else:
        print("[post_tiktok] --no-write set; new refresh token NOT persisted.",
              file=sys.stderr)

    # 3. upload + publish
    video_size = os.path.getsize(args.video)
    publish_id, upload_url = init_upload(access_token, title, video_size)
    print(f"[post_tiktok] init publish_id={publish_id}")
    upload_bytes(upload_url, args.video, video_size)
    print("[post_tiktok] done. Video is SELF_ONLY (draft) until reviewed/made public in-app.")


if __name__ == "__main__":
    main()
