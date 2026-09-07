#!/usr/bin/env python3
"""
Post the short by appending ONE row to a shared Google Sheet that Zapier watches.

Why this instead of direct platform APIs (post_meta / post_tiktok / post_youtube):
those flows depend on access/refresh tokens that constantly break — Meta's long-lived
token needs weekly re-exchange, TikTok's refresh token rotates on every single run, and
YouTube's OAuth app needs verification. Instead of babysitting tokens, we append one row
per video to a Google Sheet. A free Zapier "New Spreadsheet Row" trigger (one Zap per
platform) then posts to Instagram / Facebook / YouTube using ZAPIER's own already-verified,
managed connections. No tokens live in this repo, nothing to rotate.

  Google Sheets write (free)  ->  Zapier "New Row" trigger (free)  ->  platform action

How the write happens (NO Google Cloud, NO service-account key):
  We POST the row to a Google Apps Script "Web App" that is bound to the sheet. The script
  (scripts/sheet_webhook.gs — paste it into the sheet's Extensions -> Apps Script and
  Deploy as a Web App) receives the JSON and calls sheet.appendRow(). Deploying an Apps
  Script web app needs only a normal Google account — no Cloud project, no JSON key.

TikTok is intentionally NOT handled here: its app hasn't passed TikTok's audit and Zapier
has no free TikTok content-posting integration. Re-add a TikTok path once the app is
approved (see scripts/post_tiktok.py, left in place but no longer wired into the workflow).

Column order the web app writes MUST match the sheet's header row:
    title | description | hashtags | caption | video_url | category

Env / GitHub Secrets:
    SHEET_WEBHOOK_URL     the Apps Script web app URL (…/exec)
    SHEET_WEBHOOK_TOKEN   optional shared secret; if set here it must match the token in
                          the Apps Script so random callers can't write to your sheet

Usage:
    python scripts/post_sheet.py \
        --video-url https://.../final.mp4 \
        --script build/script.json \
        --caption-file build/caption_meta.txt \
        --config config/sources.json
"""
import argparse
import json
import os
import sys

import requests


def read_text(path, default=""):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return default


def build_payload(args):
    script = {}
    if os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as f:
            script = json.load(f)

    hashtags = ""
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            hashtags = json.load(f).get("hashtags", {}).get("instagram", "")

    author = script.get("author", "")
    text = script.get("text", "")
    title = f"{author}: Daily Motivation" if author else (script.get("title") or "Daily Motivation")
    # caption_meta.txt already contains quote + attribution + hashtags; reuse it as the
    # full caption. Fall back to text + hashtags if the file is missing.
    caption = read_text(args.caption_file) or f"{text}\n\n{hashtags}".strip()

    return {
        "title": title,
        "description": text,
        "hashtags": hashtags,
        "caption": caption,
        "video_url": args.video_url,
        "category": args.category,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True,
                    help="PUBLIC https URL to final.mp4 (the release asset).")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--caption-file", default="build/caption_meta.txt")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--category", default="motivation")
    args = ap.parse_args()

    url = os.environ.get("SHEET_WEBHOOK_URL")
    if not url:
        raise SystemExit("SHEET_WEBHOOK_URL is required (the Apps Script web app /exec URL).")

    payload = build_payload(args)
    token = os.environ.get("SHEET_WEBHOOK_TOKEN")
    if token:
        payload["token"] = token

    # Apps Script web apps answer with a 302 redirect to googleusercontent.com; requests
    # follows it automatically and returns the script's final response body.
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()

    ok = False
    try:
        ok = bool(resp.json().get("ok"))
    except ValueError:
        ok = False
    if not ok:
        print(f"[post_sheet] web app did not confirm success: "
              f"HTTP {resp.status_code} body={resp.text[:300]!r}", file=sys.stderr)
        raise SystemExit(1)

    print(f"[post_sheet] appended row: title={payload['title']!r} "
          f"video_url={payload['video_url']}", file=sys.stderr)
    print("[post_sheet] done — Zaps watching the sheet will pick it up "
          "(check Zap history for per-platform results).")


if __name__ == "__main__":
    main()
