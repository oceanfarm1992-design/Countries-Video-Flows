#!/usr/bin/env python3
"""
One-time helper: run the OAuth consent flow locally to mint a YouTube Data API
refresh token for post_youtube.py.

Run this ONCE on your own machine. It opens your browser, asks you to sign in with
the Google account that owns/manages your YouTube channel, and prints a refresh_token.
That token does not expire as long as the OAuth consent screen's Publishing status is
"Production" — tokens minted while the app is in "Testing" status expire after 7 days,
which will silently break the daily pipeline a week later. See the setup guide for how
to set Publishing status to Production.

Prerequisites: a Google Cloud OAuth 2.0 Client ID (Desktop app OR Web application type
both work with this script — see --client-secrets-file below). If your client is a
"Web application" type (Google's console defaults to this now), you must add this
EXACT redirect URI to the client's "Authorized redirect URIs" list first, or the
consent flow will fail with redirect_uri_mismatch:
    http://127.0.0.1:8080/
(Use the literal IP, not "http://localhost:8080/" — Google's console now rejects
"localhost" as a bare domain requiring https. "Desktop app" type clients don't need
this step at all — any localhost port is accepted automatically.)

Usage:
    # Recommended: pass the JSON file Google Cloud lets you download from the
    # Clients page (works for both "web" and "installed" client JSON shapes).
    python scripts/get_youtube_refresh_token.py --client-secrets-file "path/to/client_secret_....json"

    # Or pass the id/secret directly:
    python scripts/get_youtube_refresh_token.py --client-id XXX --client-secret YYY
"""
import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# Must exactly match an "Authorized redirect URI" on the client if it's a Web
# application type. Desktop app types ignore this and accept any localhost port.
FIXED_PORT = 8080


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-secrets-file", default=None,
                    help="Path to the client_secret_*.json downloaded from the "
                         "Google Cloud Console Clients page.")
    ap.add_argument("--client-id", default=None)
    ap.add_argument("--client-secret", default=None)
    args = ap.parse_args()

    if args.client_secrets_file:
        with open(args.client_secrets_file, encoding="utf-8") as f:
            raw = json.load(f)
        key = "web" if "web" in raw else "installed"
        client_id = raw[key]["client_id"]
        client_secret = raw[key]["client_secret"]
        is_web = key == "web"
    elif args.client_id and args.client_secret:
        client_id, client_secret = args.client_id, args.client_secret
        is_web = False  # assume Desktop app type when passed directly
    else:
        raise SystemExit("Pass either --client-secrets-file, or both --client-id and --client-secret.")

    client_config = {
        "web" if is_web else "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://127.0.0.1:{FIXED_PORT}/"] if is_web else ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # access_type=offline is what makes Google issue a refresh_token at all;
    # prompt=consent forces the consent screen even on a repeat run, which is what
    # actually issues a NEW refresh_token (Google silently omits it on a repeat
    # authorization that skips the consent screen).
    #
    # Web-type clients need the exact registered redirect URI (127.0.0.1, not
    # "localhost" — Google's console now rejects "localhost" as a bare domain
    # requiring https, but still exempts the literal loopback IP). Desktop-type
    # clients can use port=0 (any free port) since Google doesn't validate their
    # redirect URI at all.
    port = FIXED_PORT if is_web else 0
    host = "127.0.0.1" if is_web else "localhost"
    creds = flow.run_local_server(host=host, port=port, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise SystemExit(
            "No refresh_token returned. This happens if you've already authorized "
            "this exact app before and Google silently skipped re-issuing one. Go to "
            "https://myaccount.google.com/permissions, remove access for this app, "
            "then run this script again."
        )

    print("\n=== Success — save these as GitHub Secrets on the new repo ===")
    print(f"YOUTUBE_CLIENT_ID={client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
