#!/usr/bin/env python3
"""
Post the video to a Facebook Page as a Reel, directly through the Graph API with a Page access
token (no Buffer). Used by the India channel.

Reels publishing is three calls: start an upload session, let Facebook pull the video from
its public URL (our GitHub release asset), then publish with the caption.

Environment:
    FACEBOOK_PAGE_ID      the Page's numeric id
    FACEBOOK_PAGE_TOKEN   a Page access token with pages_manage_posts (and
                          pages_read_engagement); use a long-lived one, or posting stops when
                          a short-lived token expires after about an hour
    FB_GRAPH_VERSION      optional, default v23.0

Usage:
    python scripts/post_facebook.py --video-url URL --caption-file build/caption_meta.txt
    python scripts/post_facebook.py --check      # only confirm the token can see the Page
"""
import argparse
import os
import sys
import time

import requests

GRAPH = "https://graph.facebook.com/{version}"
RUPLOAD = "https://rupload.facebook.com/video-upload/{version}/{video_id}"
STATUS_TRIES, STATUS_WAIT = 30, 10   # up to 5 minutes for Facebook to fetch and process


class FacebookError(RuntimeError):
    pass


def _check(resp):
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        # never echo the request (it carries the token); the message and code are enough
        raise FacebookError(f"HTTP {resp.status_code}: {err.get('message', resp.text[:200])} "
                            f"(code {err.get('code')})")
    return data


class Page:
    def __init__(self, page_id, token, version="v23.0", session=requests):
        self.page_id, self.token, self.version, self.http = page_id, token, version, session
        self.base = GRAPH.format(version=version)

    def use_page_token(self):
        """A Business Manager system-user (or user) token cannot post as the Page itself
        (Facebook answers "(#200) Subject does not have permission to post videos"). Ask for the
        Page's own access token with it and use that; returns True when switched."""
        data = _check(self.http.get(f"{self.base}/{self.page_id}", timeout=30,
                                    params={"fields": "access_token", "access_token": self.token}))
        page_token = data.get("access_token")
        if page_token and page_token != self.token:
            self.token = page_token
            return True
        return False

    def name(self):
        """The Page this token acts as (proves the token and the id match)."""
        data = _check(self.http.get(f"{self.base}/{self.page_id}", timeout=30,
                                    params={"fields": "id,name", "access_token": self.token}))
        return data.get("name", "?")

    def start(self):
        data = _check(self.http.post(f"{self.base}/{self.page_id}/video_reels", timeout=30,
                                     params={"upload_phase": "start", "access_token": self.token}))
        return data["video_id"]

    def pull(self, video_id, video_url):
        _check(self.http.post(RUPLOAD.format(version=self.version, video_id=video_id), timeout=120,
                              headers={"Authorization": f"OAuth {self.token}", "file_url": video_url}))

    def finish(self, video_id, caption):
        return _check(self.http.post(f"{self.base}/{self.page_id}/video_reels", timeout=60,
                                     params={"upload_phase": "finish", "video_id": video_id,
                                             "video_state": "PUBLISHED", "description": caption,
                                             "access_token": self.token}))

    def wait_until_uploaded(self, video_id, sleep=time.sleep):
        """Facebook fetches the file in the background; publish only once it has it."""
        for _ in range(STATUS_TRIES):
            data = _check(self.http.get(f"{self.base}/{video_id}", timeout=30,
                                        params={"fields": "status", "access_token": self.token}))
            status = data.get("status", {})
            upload = (status.get("uploading_phase") or {}).get("status")
            if upload == "complete" or status.get("video_status") in ("ready", "upload_complete"):
                return
            if upload == "error" or status.get("video_status") == "error":
                raise FacebookError(f"Facebook could not fetch the video: {status}")
            sleep(STATUS_WAIT)
        raise FacebookError("Facebook did not finish fetching the video in time")

    def post_reel(self, video_url, caption, sleep=time.sleep):
        video_id = self.start()
        self.pull(video_id, video_url)
        self.wait_until_uploaded(video_id, sleep)
        self.finish(video_id, caption)
        return video_id


def post_video(page, video_url, title, description):
    """A regular (non-Reel) Page video, for the long-form: Facebook pulls it from the URL."""
    data = _check(page.http.post(f"{page.base}/{page.page_id}/videos", timeout=300,
                                 params={"file_url": video_url, "title": title[:250],
                                         "description": description, "access_token": page.token}))
    return data.get("id")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video-url")
    ap.add_argument("--caption-file", default="build/caption_meta.txt")
    ap.add_argument("--check", action="store_true", help="only confirm the token can see the Page")
    ap.add_argument("--regular", action="store_true", help="a regular Page video (long-form), not a Reel")
    ap.add_argument("--title-file", default=None, help="title for --regular")
    args = ap.parse_args()
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    token = os.environ.get("FACEBOOK_PAGE_TOKEN", "").strip()
    if not (page_id and token):
        print("[post_facebook] FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN not set -- skipped")
        return 0
    page = Page(page_id, token, os.environ.get("FB_GRAPH_VERSION", "v23.0"))
    try:
        switched = page.use_page_token()
        print(f"[post_facebook] token works for the Page: {page.name()}"
              + (" (using the Page's own token)" if switched else ""))
        if args.check:
            return 0
        if not args.video_url:
            print("[post_facebook] --video-url is required to post")
            return 2
        with open(args.caption_file, encoding="utf-8") as fh:
            caption = fh.read().strip()
        if args.regular:
            title = open(args.title_file, encoding="utf-8").read().strip() if args.title_file else ""
            video_id = post_video(page, args.video_url, title, caption)
            print(f"[post_facebook] done: https://www.facebook.com/{page.page_id}/videos/{video_id}")
            return 0
        video_id = page.post_reel(args.video_url, caption)
        print(f"[post_facebook] done: https://www.facebook.com/reel/{video_id}")
        return 0
    except FacebookError as exc:
        print(f"[post_facebook] failed: {exc}")
        return 1
    except requests.RequestException as exc:  # its text can contain the URL with the token
        print(f"[post_facebook] failed: network error ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
