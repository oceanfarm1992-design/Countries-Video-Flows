#!/usr/bin/env python3
"""
Shared helper: create/update a GitHub Actions repository secret via the REST API.

Used by the token-refresh scripts (TikTok rotates its refresh_token on every use;
Meta long-lived tokens get periodically re-exchanged) so the new value is persisted
back into GitHub Secrets for the next run.

Encryption: GitHub requires the value be encrypted with a libsodium "sealed box"
against the repo's public key, then base64-encoded (PyNaCl).
  GET  /repos/{owner}/{repo}/actions/secrets/public-key
  PUT  /repos/{owner}/{repo}/actions/secrets/{name}
See: https://docs.github.com/en/rest/actions/secrets

Auth: a fine-grained or classic PAT in env var GH_PAT with "Secrets: write"
(repository secrets) permission.

Usage (CLI, for manual testing):
    GH_PAT=... python scripts/update_github_secret.py \
        --repo owner/name --name TIKTOK_REFRESH_TOKEN --value "the-new-token"

Programmatic:
    from update_github_secret import update_secret
    update_secret("owner/name", "TIKTOK_REFRESH_TOKEN", "value", gh_pat)
"""
import argparse
import base64
import os

import requests
from nacl import encoding, public


def _encrypt(public_key_b64, secret_value):
    """Seal secret_value against the repo public key; return base64 ciphertext."""
    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pk)
    encrypted = sealed.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def update_secret(repo, name, value, gh_pat):
    """repo is 'owner/name'."""
    headers = {
        "Authorization": f"Bearer {gh_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = f"https://api.github.com/repos/{repo}/actions/secrets"

    r = requests.get(f"{base}/public-key", headers=headers, timeout=30)
    r.raise_for_status()
    pub = r.json()  # {key_id, key}

    payload = {
        "encrypted_value": _encrypt(pub["key"], value),
        "key_id": pub["key_id"],
    }
    r = requests.put(f"{base}/{name}", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    # 201 = created, 204 = updated
    print(f"[update_github_secret] {name} -> {repo} (HTTP {r.status_code})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                    help="owner/name (defaults to $GITHUB_REPOSITORY in Actions).")
    ap.add_argument("--name", required=True)
    ap.add_argument("--value", required=True)
    args = ap.parse_args()

    gh_pat = os.environ.get("GH_PAT")
    if not gh_pat:
        raise SystemExit("GH_PAT env var is required.")
    if not args.repo:
        raise SystemExit("--repo or GITHUB_REPOSITORY is required.")

    update_secret(args.repo, args.name, args.value, gh_pat)


if __name__ == "__main__":
    main()
