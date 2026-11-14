#!/usr/bin/env python3
"""
Check the India channel's posting connections without posting anything:
  * Facebook: the Page the token belongs to, the permissions it carries (publishing needs
    pages_manage_posts) and when it expires.
  * YouTube: the channel the saved sign-in uploads to.
Prints names, permissions and dates only -- never a token. Exit 1 if a connection is broken.

Environment: FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
YOUTUBE_REFRESH_TOKEN (the India ones, mapped by the workflow).
"""
import os
import sys
from datetime import datetime, timezone

import requests

from post_facebook import GRAPH, FacebookError, Page, _check

NEEDED_FB = {"pages_manage_posts", "pages_read_engagement"}


def describe_expiry(expires_at):
    if not expires_at:
        return "never expires"
    when = datetime.fromtimestamp(expires_at, timezone.utc)
    days = (when - datetime.now(timezone.utc)).days
    return f"expires {when:%Y-%m-%d} ({days} days)"


def check_facebook():
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    token = os.environ.get("FACEBOOK_PAGE_TOKEN", "").strip()
    if not (page_id and token):
        print("Facebook: secrets not set")
        return False
    version = os.environ.get("FB_GRAPH_VERSION", "v23.0")
    try:
        print(f"Facebook: Page = {Page(page_id, token, version).name()}")
        info = _check(requests.get(f"{GRAPH.format(version=version)}/debug_token", timeout=30,
                                   params={"input_token": token, "access_token": token})).get("data", {})
    except FacebookError as exc:
        print(f"Facebook: BROKEN -- {exc}")
        return False
    except requests.RequestException as exc:
        print(f"Facebook: network error ({type(exc).__name__})")
        return False
    scopes = set(info.get("scopes", []))
    print(f"Facebook: token type {info.get('type', '?')}, valid={info.get('is_valid')}, "
          f"{describe_expiry(info.get('expires_at'))}")
    print(f"Facebook: permissions = {', '.join(sorted(scopes)) or '(none listed)'}")
    missing = NEEDED_FB - scopes
    if missing:
        print(f"Facebook: MISSING {', '.join(sorted(missing))} -- publishing will fail")
    return bool(info.get("is_valid")) and not missing


def check_youtube():
    need = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
    if not all(os.environ.get(k) for k in need):
        print("YouTube: secrets not set")
        return False
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
                        client_id=os.environ["YOUTUBE_CLIENT_ID"],
                        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token")
    try:
        items = build("youtube", "v3", credentials=creds, cache_discovery=False) \
            .channels().list(part="snippet", mine=True).execute().get("items", [])
    except Exception as exc:  # noqa: BLE001 -- report, never print credentials
        print(f"YouTube: BROKEN ({type(exc).__name__})")
        return False
    print(f"YouTube: channel = {items[0]['snippet']['title'] if items else '(none on this account)'}")
    return bool(items)


def main():
    ok_fb, ok_yt = check_facebook(), check_youtube()
    print(f"\nFacebook {'OK' if ok_fb else 'NOT READY'} | YouTube {'OK' if ok_yt else 'NOT READY'}")
    return 0 if (ok_fb and ok_yt) else 1


if __name__ == "__main__":
    sys.exit(main())
