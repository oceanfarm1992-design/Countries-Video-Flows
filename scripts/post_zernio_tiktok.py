#!/usr/bin/env python3
"""
Post the finished short to TikTok via the Zernio API (https://zernio.com).

Zernio holds the actual TikTok OAuth connection; this script just calls Zernio's
REST API with the public video URL (the GitHub Release asset from this run) and a
caption. Instagram/Facebook/YouTube keep going through the existing Google Sheet ->
Zapier flow (post_sheet.py) — this is TikTok-only, since Zapier has no free TikTok
posting integration.

Requires (GitHub Secrets):
    ZERNIO_API_KEY            Bearer token — https://zernio.com/dashboard/api-keys
    ZERNIO_PROFILE_ID         The Zernio "profile" (account group) id
    ZERNIO_TIKTOK_ACCOUNT_ID  The connected TikTok account's id within that profile

Find your profile/account ids once with:
    curl -H "Authorization: Bearer $ZERNIO_API_KEY" https://zernio.com/api/v1/profiles
    curl -H "Authorization: Bearer $ZERNIO_API_KEY" \
        "https://zernio.com/api/v1/accounts?profileId=<id-from-above>"

Safety default: this sends the post to TikTok's Creator Inbox as a DRAFT
(platformSpecificData.draft=true) rather than publishing live — it lands as a
notification in the connected TikTok account's inbox, and a human taps "Post" in the
TikTok app to actually publish it. Pass --publish to direct-post immediately instead
(TikTok's DIRECT_POST mode) once you've confirmed the pipeline end-to-end.

Usage:
    python scripts/post_zernio_tiktok.py --video-url https://.../final.mp4 \
        --script build/script.json --caption-file build/caption_tiktok.txt
    python scripts/post_zernio_tiktok.py --video-url ... --publish
"""
import argparse
import json
import os
import sys

import requests

API_BASE = "https://zernio.com/api/v1"


def get_privacy_level(account_id, headers):
    """TikTok requires privacyLevel to be one of the values it returns for this
    specific creator — it isn't a fixed global enum. Prefer public; fall back to
    whatever the connected account actually supports (e.g. an unverified/new
    account may only be allowed SELF_ONLY)."""
    r = requests.get(f"{API_BASE}/accounts/{account_id}/tiktok/creator-info",
                      headers=headers, timeout=30)
    r.raise_for_status()
    levels = [lvl["value"] for lvl in r.json().get("privacyLevels", [])]
    if "PUBLIC_TO_EVERYONE" in levels:
        return "PUBLIC_TO_EVERYONE"
    return levels[0] if levels else "SELF_ONLY"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True,
                    help="Publicly reachable HTTPS URL of the finished .mp4.")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--caption-file", default="build/caption_tiktok.txt")
    ap.add_argument("--publish", action="store_true",
                    help="Direct-post immediately instead of sending a Creator Inbox draft.")
    args = ap.parse_args()

    api_key = os.environ.get("ZERNIO_API_KEY", "").strip()
    profile_id = os.environ.get("ZERNIO_PROFILE_ID", "").strip()
    account_id = os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID", "").strip()
    if not (api_key and profile_id and account_id):
        print("[post_zernio_tiktok] ZERNIO_API_KEY / ZERNIO_PROFILE_ID / "
              "ZERNIO_TIKTOK_ACCOUNT_ID not fully set — skipping TikTok post.",
              file=sys.stderr)
        sys.exit(0)  # optional platform: don't fail the whole run over it

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    caption = ""
    if os.path.exists(args.caption_file):
        with open(args.caption_file, encoding="utf-8") as f:
            caption = f.read().strip()
    elif os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as f:
            s = json.load(f)
        caption = f"{s.get('name', '')} — {s.get('specialty', '')}"

    privacy_level = get_privacy_level(account_id, headers)

    body = {
        "profileId": profile_id,
        "content": caption,
        "mediaItems": [{"type": "video", "url": args.video_url}],
        # Top-level: tells ZERNIO to act on this now (omitting this would just save
        # a Zernio-side draft and never call TikTok at all).
        "publishNow": True,
        "platforms": [{
            "platform": "tiktok",
            "accountId": account_id,
            "platformSpecificData": {
                # TikTok-side: Creator Inbox draft (safe default) vs DIRECT_POST.
                "draft": not args.publish,
                "privacyLevel": privacy_level,
                "allowComment": True,
                "allowDuet": True,
                "allowStitch": True,
                "videoMadeWithAi": True,  # narration + voice are both AI-generated
                "contentPreviewConfirmed": True,
                "expressConsentGiven": True,
            },
        }],
    }

    r = requests.post(f"{API_BASE}/posts", headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        print(f"[post_zernio_tiktok] Zernio API error {r.status_code}: {r.text}",
              file=sys.stderr)
        sys.exit(1)

    mode = "DIRECT_POST" if args.publish else "Creator Inbox draft"
    print(f"[post_zernio_tiktok] submitted ({mode}): {r.text[:500]}")


if __name__ == "__main__":
    main()
