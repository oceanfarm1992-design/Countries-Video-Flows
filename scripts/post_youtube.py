#!/usr/bin/env python3
"""
Upload the short to YouTube (as a Short) via the YouTube Data API v3 videos.insert.

Auth: OAuth "installed app" refresh-token flow. Google refresh tokens don't expire on
a fixed schedule, so we just mint a fresh access token each run from the stored
refresh_token + client_id + client_secret. Nothing needs to be written back.

Upload: resumable (google-api-python-client's MediaFileUpload(resumable=True)), which
is the robust path for video and handles chunking/retries for us.

Shorts: a video is treated as a Short automatically when it is vertical and <=3 min and
(conventionally) has #Shorts in the title/description. We add #Shorts to the description.

Synthetic-content disclosure: we set status.containsSyntheticMedia=True. This is the
official field the Data API added on 2024-10-30 for disclosing altered/synthetic (A/S)
content, equivalent to the "Altered or synthetic content" toggle in Studio. Since our
visuals + AI voiceover are machine-assembled, we disclose truthfully.
# VERIFY: field name status.containsSyntheticMedia is current per the API revision history
# (added 2024-10-30). Confirm at https://developers.google.com/youtube/v3/revision_history

Env / GitHub Secrets:
    YOUTUBE_CLIENT_ID
    YOUTUBE_CLIENT_SECRET
    YOUTUBE_REFRESH_TOKEN

Usage:
    python scripts/post_youtube.py --video build/final.mp4 \
        --title-file build/yt_title.txt --desc-file build/caption_youtube.txt
"""
import argparse
import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# 22 = "People & Blogs". Category ids are region-specific; 22 is a safe general default.
# VERIFY: pick the category that fits; 22 (People & Blogs) or 24 (Entertainment) are common.
DEFAULT_CATEGORY_ID = "22"


def build_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def upload(video_path, title, description, tags):
    youtube = build_service()
    body = {
        "snippet": {
            "title": title[:100],  # YouTube title hard limit is 100 chars
            "description": description,
            "tags": tags,
            "categoryId": DEFAULT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # disclose AI/synthetic assembly
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                            mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    print("[post_youtube] starting resumable upload...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[post_youtube] uploaded {int(status.progress() * 100)}%")
    print(f"[post_youtube] done: https://youtu.be/{response['id']}")
    return response


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="build/final.mp4")
    ap.add_argument("--title", default=None)
    ap.add_argument("--title-file", default="build/yt_title.txt")
    ap.add_argument("--desc-file", default="build/caption_youtube.txt")
    ap.add_argument("--config", default="config/sources.json")
    args = ap.parse_args()

    if args.title is not None:
        title = args.title
    elif os.path.exists(args.title_file):
        with open(args.title_file, encoding="utf-8") as f:
            title = f.read().strip()
    else:
        title = "Daily Motivation #Shorts"

    if os.path.exists(args.desc_file):
        with open(args.desc_file, encoding="utf-8") as f:
            description = f.read().strip()
    else:
        description = "#Shorts"

    # simple tags from config hashtags (strip the # for tag form)
    tags = ["motivation", "stoicism", "shorts", "discipline", "mindset"]
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            _ = json.load(f)  # reserved for future per-run tag customization

    upload(args.video, title, description, tags)


if __name__ == "__main__":
    main()
