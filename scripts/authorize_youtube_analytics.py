#!/usr/bin/env python3
"""
ONE-TIME, run on your own computer: grants the weekly retention report READ-ONLY access
to your channel's analytics and saves the token as the GitHub secret
YOUTUBE_ANALYTICS_REFRESH_TOKEN.

It is separate from the upload token on purpose -- posting keeps working exactly as
before, and this token can only read statistics (yt-analytics.readonly + youtube.readonly).

Run:  python scripts/authorize_youtube_analytics.py
  1. paste the OAuth Client ID and Client secret when asked (Google Cloud Console >
     APIs & Services > Credentials -- the same client the uploader uses)
  2. a browser window opens: sign in with the CHANNEL's Google account and click Allow
  3. the token is saved to the repo's secrets automatically (needs the gh CLI logged in)

If Google says "redirect_uri_mismatch", add both  http://localhost:8080/  and
http://127.0.0.1:8080/  to the OAuth client's Authorized redirect URIs and run it again.
"""
import getpass
import os
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
          "https://www.googleapis.com/auth/youtube.readonly"]
REPO = "oceanfarm1992-design/Countries-Video-Flows"
SECRET_NAME = "YOUTUBE_ANALYTICS_REFRESH_TOKEN"


def main():
    # asks for the two values when they aren't set -- no shell quoting to get wrong
    cid = os.environ.get("YOUTUBE_CLIENT_ID") or input("Paste your Client ID and press Enter: ").strip()
    secret = (os.environ.get("YOUTUBE_CLIENT_SECRET")
              or getpass.getpass("Paste your Client secret and press Enter (it stays hidden): ").strip())
    if not (cid and secret):
        sys.exit("Both the Client ID and the Client secret are needed.")
    print("\nA browser window will open: sign in with the CHANNEL's Google account and click Allow.\n")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": cid, "client_secret": secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token",
                       "redirect_uris": ["http://localhost:8080/"]}}, SCOPES)
    # fixed port so a "Web application" client works too (its redirect URI must match exactly)
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")
    # saved straight to the repo's secrets, so the token never needs copying by hand
    saved = subprocess.run(["gh", "secret", "set", SECRET_NAME, "--repo", REPO],
                           input=creds.refresh_token, text=True).returncode == 0
    if saved:
        print(f"\nDone: saved {SECRET_NAME} to GitHub. You can close this window.")
    else:
        print(f"\nCould not save it automatically. Run:  gh secret set {SECRET_NAME} --repo {REPO}")
        print("and paste this token when asked:\n")
        print(creds.refresh_token)


if __name__ == "__main__":
    main()
