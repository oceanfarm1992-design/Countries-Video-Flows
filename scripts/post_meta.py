#!/usr/bin/env python3
"""
Post the finished short to Instagram Reels and a Facebook Page via the Meta Graph API.

Instagram Reels (Content Publishing API) is a 2-step, container-based flow and REQUIRES
a PUBLIC https video URL (Meta fetches the file itself — you cannot upload bytes here):
  1. POST /{ig_user_id}/media
        media_type=REELS, video_url=<public url>, caption=<text>
     -> returns a creation_id (container)
  2. Poll GET /{creation_id}?fields=status_code until it is FINISHED
  3. POST /{ig_user_id}/media_publish  creation_id=<id>
Docs: https://developers.facebook.com/docs/instagram-api/guides/content-publishing

Facebook Page video CAN be uploaded directly (multipart), no public URL needed:
  POST /{page_id}/videos  (file bytes as 'source', plus 'description')

Because IG needs a public URL, the workflow publishes final.mp4 somewhere public first
(e.g. a GitHub Release asset or committed to the repo -> raw.githubusercontent URL) and
passes that URL via --video-url. Facebook uses the local --video file directly.

# VERIFY: Graph API version string. v21.0 is current-ish as of this writing; bump to the
# latest stable at https://developers.facebook.com/docs/graph-api/changelog before relying on it.

Env / GitHub Secrets:
    META_ACCESS_TOKEN   long-lived page access token (see refresh_meta_token.py)
    META_IG_USER_ID     the IG business account user id
    META_PAGE_ID        the Facebook Page id

Usage:
    python scripts/post_meta.py --video build/final.mp4 \
        --video-url https://.../final.mp4 --caption-file build/caption_meta.txt
    python scripts/post_meta.py ... --target instagram    # only one platform
"""
import argparse
import os
import sys
import time

import requests

GRAPH = "https://graph.facebook.com/v21.0"  # VERIFY version (see module docstring)


def _raise_with_body(r):
    """requests' raise_for_status() drops the response body; print Meta's actual
    error JSON (it's the only useful diagnostic) before re-raising."""
    if not r.ok:
        print(f"[post_meta] Graph API error {r.status_code}: {r.text}", file=sys.stderr)
    r.raise_for_status()


def post_instagram_reel(ig_user_id, token, video_url, caption):
    # 1. create container
    r = requests.post(
        f"{GRAPH}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": token,
        },
        timeout=60,
    )
    _raise_with_body(r)
    creation_id = r.json()["id"]
    print(f"[post_meta] IG container created: {creation_id}")

    # 2. poll until the container has finished processing the fetched video
    for attempt in range(30):  # up to ~5 minutes
        s = requests.get(
            f"{GRAPH}/{creation_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=30,
        )
        _raise_with_body(s)
        status = s.json().get("status_code")
        print(f"[post_meta] IG container status: {status}")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise SystemExit(f"IG container errored: {s.json()}")
        time.sleep(10)
    else:
        raise SystemExit("IG container never reached FINISHED.")

    # 3. publish
    p = requests.post(
        f"{GRAPH}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=60,
    )
    _raise_with_body(p)
    print(f"[post_meta] IG published: {p.json()}")
    return p.json()


def post_facebook_video(page_id, token, video_path, description):
    with open(video_path, "rb") as f:
        r = requests.post(
            f"{GRAPH}/{page_id}/videos",
            data={"description": description, "access_token": token},
            files={"source": f},
            timeout=600,
        )
    _raise_with_body(r)
    print(f"[post_meta] FB published: {r.json()}")
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="build/final.mp4")
    ap.add_argument("--video-url", default=None,
                    help="PUBLIC https URL to the video (required for Instagram).")
    ap.add_argument("--caption-file", default="build/caption_meta.txt")
    ap.add_argument("--caption", default=None)
    ap.add_argument("--target", choices=["all", "instagram", "facebook"], default="all")
    args = ap.parse_args()

    token = os.environ.get("META_ACCESS_TOKEN")
    ig_user_id = os.environ.get("META_IG_USER_ID")
    page_id = os.environ.get("META_PAGE_ID")
    if not token:
        raise SystemExit("META_ACCESS_TOKEN is required.")

    if args.caption is not None:
        caption = args.caption
    elif os.path.exists(args.caption_file):
        with open(args.caption_file, encoding="utf-8") as f:
            caption = f.read().strip()
    else:
        caption = ""

    ok = True
    if args.target in ("all", "instagram"):
        if not ig_user_id or not args.video_url:
            print("WARN: skipping Instagram (need META_IG_USER_ID and --video-url).",
                  file=sys.stderr)
            ok = False
        else:
            post_instagram_reel(ig_user_id, token, args.video_url, caption)

    if args.target in ("all", "facebook"):
        if not page_id:
            print("WARN: skipping Facebook (need META_PAGE_ID).", file=sys.stderr)
            ok = False
        else:
            post_facebook_video(page_id, token, args.video, caption)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
