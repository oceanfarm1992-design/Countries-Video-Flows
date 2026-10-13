#!/usr/bin/env python3
"""
ONE-TIME, run on your own computer: grants the weekly retention report READ-ONLY access
to your channel's analytics and prints the token to store as a GitHub secret.

It is separate from the upload token on purpose -- posting keeps working exactly as
before, and this token can only read statistics (yt-analytics.readonly + youtube.readonly).

Steps:
  1. Set the same OAuth client the uploader uses (Google Cloud Console > Credentials):
       PowerShell:  $env:YOUTUBE_CLIENT_ID="..."; $env:YOUTUBE_CLIENT_SECRET="..."
  2. Run:  python scripts/authorize_youtube_analytics.py
     A browser window opens; sign in with the channel's Google account and allow access.
     (If Google says "redirect_uri_mismatch", the OAuth client is a "Web application":
     add  http://localhost:8080/  to its Authorized redirect URIs and run it again.)
  3. Add the printed token to the repo:
       gh secret set YOUTUBE_ANALYTICS_REFRESH_TOKEN
     and paste it when asked.
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
          "https://www.googleapis.com/auth/youtube.readonly"]


def main():
    cid, secret = os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not (cid and secret):
        sys.exit("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET first (see the top of this file).")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": cid, "client_secret": secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token",
                       "redirect_uris": ["http://localhost:8080/"]}}, SCOPES)
    # fixed port so a "Web application" client works too (its redirect URI must match exactly)
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")
    print("\nAdd this as the GitHub secret YOUTUBE_ANALYTICS_REFRESH_TOKEN:\n")
    print(creds.refresh_token)


if __name__ == "__main__":
    main()
