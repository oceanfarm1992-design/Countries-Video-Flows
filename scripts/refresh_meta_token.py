#!/usr/bin/env python3
"""
Refresh the Meta (Facebook/Instagram) long-lived access token and store it back.

Meta long-lived user tokens last ~60 days. Re-exchanging a still-valid long-lived
token with the fb_exchange_token grant returns a fresh one with the clock reset, so
running this periodically (e.g. weekly, well within 60 days) keeps posting alive.

  GET /oauth/access_token
      ?grant_type=fb_exchange_token
      &client_id=<APP_ID>&client_secret=<APP_SECRET>
      &fb_exchange_token=<current long-lived token>
  -> { "access_token": "...", "expires_in": 5183944 }
Docs: https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived

# VERIFY: for a *Page* token you may additionally need to call /{page_id}?fields=access_token
# with a long-lived *user* token to derive a (non-expiring) page token. The exchange below
# refreshes the user token; adapt if you post with a page token specifically.

The new token is written back to the META_ACCESS_TOKEN GitHub secret so the next
daily run picks it up.

Env / GitHub Secrets:
    META_ACCESS_TOKEN  current (still valid) long-lived token
    META_APP_ID
    META_APP_SECRET
    GH_PAT             PAT with Secrets:write to persist the refreshed token
    GITHUB_REPOSITORY  owner/name (auto-set in Actions)

Usage:
    python scripts/refresh_meta_token.py
    python scripts/refresh_meta_token.py --no-write   # print only, don't touch secrets
"""
import argparse
import os

import requests

from update_github_secret import update_secret

GRAPH = "https://graph.facebook.com/v21.0"  # VERIFY version


def exchange_long_lived(app_id, app_secret, current_token):
    r = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": current_token,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data.get("expires_in")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true",
                    help="Print the new token but do not write it to GitHub secrets.")
    args = ap.parse_args()

    current = os.environ["META_ACCESS_TOKEN"]
    app_id = os.environ["META_APP_ID"]
    app_secret = os.environ["META_APP_SECRET"]

    new_token, expires_in = exchange_long_lived(app_id, app_secret, current)
    print(f"[refresh_meta_token] got new token (expires_in={expires_in}s)")

    if args.no_write:
        print(new_token)
        return

    repo = os.environ["GITHUB_REPOSITORY"]
    gh_pat = os.environ["GH_PAT"]
    update_secret(repo, "META_ACCESS_TOKEN", new_token, gh_pat)


if __name__ == "__main__":
    main()
