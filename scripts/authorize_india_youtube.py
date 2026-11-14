#!/usr/bin/env python3
"""
ONE-TIME, run on your own computer: lets the India workflow upload to the INDIA YouTube channel.

It reads your OAuth client file (client_secret.json in the repo folder, from Google Cloud
Console > APIs & Services > Credentials > Download JSON), opens a browser to sign in, and saves
three GitHub secrets straight from this computer, so nothing is pasted or shown anywhere:
    INDIA_YOUTUBE_CLIENT_ID, INDIA_YOUTUBE_CLIENT_SECRET, INDIA_YOUTUBE_REFRESH_TOKEN
The world channel's secrets are not touched.

Run:  python scripts/authorize_india_youtube.py [--client-file client_secret.json]
  1. a browser window opens: sign in with the Google account that owns the INDIA channel;
     if it asks which channel, pick the India one, then click Allow
  2. the three secrets are saved to the repo (needs the gh CLI logged in)
  3. delete client_secret.json afterwards (git already ignores it)
If Google says "redirect_uri_mismatch", add  http://localhost:8080/  to the OAuth client's
Authorized redirect URIs and run it again.
"""
import argparse
import json
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]  # readonly: to show which channel
REPO = "oceanfarm1992-design/Countries-Video-Flows"


def client_from_file(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    block = data.get("installed") or data.get("web") or {}
    if not (block.get("client_id") and block.get("client_secret")):
        sys.exit(f"{path} has no client_id/client_secret -- download the OAuth client JSON again.")
    return block["client_id"], block["client_secret"]


def save_secret(name, value):
    ok = subprocess.run(["gh", "secret", "set", name, "--repo", REPO], input=value, text=True).returncode == 0
    print(f"  {'saved' if ok else 'COULD NOT SAVE'} {name}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--client-file", default="client_secret.json")
    args = ap.parse_args()
    cid, secret = client_from_file(args.client_file)
    print("\nA browser window will open: sign in and choose the INDIA channel, then click Allow.\n")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": cid, "client_secret": secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token",
                       "redirect_uris": ["http://localhost:8080/"]}}, SCOPES)
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")
    try:  # show which channel was authorised, so a wrong pick is caught now, not on first post
        from googleapiclient.discovery import build
        me = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute()
        title = me["items"][0]["snippet"]["title"] if me.get("items") else "(no channel on this account)"
        print(f"Authorised channel: {title}")
    except Exception as exc:  # noqa: BLE001 -- informational only
        print(f"(could not read the channel name: {type(exc).__name__})")
    ok = all([save_secret("INDIA_YOUTUBE_CLIENT_ID", cid),
              save_secret("INDIA_YOUTUBE_CLIENT_SECRET", secret),
              save_secret("INDIA_YOUTUBE_REFRESH_TOKEN", creds.refresh_token)])
    print("\nDone. Now delete client_secret.json." if ok else
          "\nSome secrets were not saved: check that `gh auth status` works and run this again.")


if __name__ == "__main__":
    main()
