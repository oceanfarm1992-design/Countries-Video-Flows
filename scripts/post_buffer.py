#!/usr/bin/env python3
"""
Publish the finished short to Facebook and/or TikTok via Buffer's GraphQL API
(https://api.buffer.com).

Buffer holds the actual Facebook/TikTok connections; this script calls Buffer's
`createPost` mutation with the public video URL and a caption, using mode `shareNow`
so it publishes immediately. Buffer is an audited TikTok posting partner and handles
TikTok's consent/privacy requirements on its side, so this is much simpler than a raw
TikTok integration. Facebook is posted as a Reel (vertical video).

Instagram stays on the Google Sheet -> Zapier flow; YouTube stays on the direct API.

Requires (GitHub Secrets):
    BUFFER_API_KEY               personal token from https://publish.buffer.com/settings/api
    BUFFER_FACEBOOK_CHANNEL_ID   Buffer channel id of the Facebook page (optional)
    BUFFER_TIKTOK_CHANNEL_ID     Buffer channel id of the TikTok account (optional)
    BUFFER_ORG_ID                organization id (optional; auto-resolved if omitted)

List your channel ids once with:
    curl -s -X POST https://api.buffer.com -H "Authorization: Bearer $BUFFER_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"query":"query{account{organizations{id}}}"}'
    # then query channels(input:{organizationId:"..."}){id name service}

Usage:
    python scripts/post_buffer.py --video-url https://.../final.mp4 --script build/script.json
"""
import argparse
import json
import os
import sys

import requests

API = "https://api.buffer.com"

CREATE_POST = """
mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id } }
    ... on NotFoundError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on RestProxyError { message }
    ... on LimitReachedError { message }
    ... on InvalidInputError { message }
  }
}
"""


def gql(token, query, variables=None):
    r = requests.post(
        API, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"])[:400])
    return data["data"]


def resolve_org(token):
    d = gql(token, "query { account { organizations { id } } }")
    orgs = d["account"]["organizations"]
    if not orgs:
        raise RuntimeError("Buffer account has no organizations.")
    return orgs[0]["id"]


def read_text(path, default=""):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return default


def create_post(token, channel_id, text, video_url, metadata):
    variables = {"input": {
        "channelId": channel_id,
        "text": text,
        "assets": [{"video": {"url": video_url}}],
        "mode": "shareNow",
        "schedulingType": "automatic",
        "needsApproval": False,
        "metadata": metadata,
    }}
    d = gql(token, CREATE_POST, variables)
    res = d["createPost"]
    if res["__typename"] == "PostActionSuccess":
        return True, res["post"]["id"]
    return False, res.get("message", res["__typename"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True,
                    help="Publicly reachable HTTPS URL of the finished .mp4.")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--caption-facebook", default="build/caption_meta.txt")
    ap.add_argument("--caption-tiktok", default="build/caption_tiktok.txt")
    ap.add_argument("--title-file", default="build/yt_title.txt")
    args = ap.parse_args()

    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        print("[post_buffer] BUFFER_API_KEY not set — skipping Buffer posting.",
              file=sys.stderr)
        sys.exit(0)  # optional platform: don't fail the whole run

    fb_id = os.environ.get("BUFFER_FACEBOOK_CHANNEL_ID", "").strip()
    tt_id = os.environ.get("BUFFER_TIKTOK_CHANNEL_ID", "").strip()
    if not (fb_id or tt_id):
        print("[post_buffer] no BUFFER_FACEBOOK_CHANNEL_ID / BUFFER_TIKTOK_CHANNEL_ID "
              "set — nothing to post.", file=sys.stderr)
        sys.exit(0)

    script = {}
    if os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as f:
            script = json.load(f)
    name = script.get("name", "")
    title = read_text(args.title_file) or (f"{name}" if name else "A new country every day")

    failures = 0

    if fb_id:
        caption = read_text(args.caption_facebook) or f"{name} — {script.get('specialty','')}"
        ok, info = create_post(token, fb_id, caption, args.video_url,
                               {"facebook": {"type": "reel"}})
        print(f"[post_buffer] facebook: {'posted ' + info if ok else 'FAILED ' + info}")
        failures += 0 if ok else 1

    if tt_id:
        caption = read_text(args.caption_tiktok) or f"{name} — {script.get('specialty','')}"
        ok, info = create_post(token, tt_id, caption, args.video_url,
                               {"tiktok": {"isAiGenerated": True, "title": title[:90]}})
        print(f"[post_buffer] tiktok: {'posted ' + info if ok else 'FAILED ' + info}")
        failures += 0 if ok else 1

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
