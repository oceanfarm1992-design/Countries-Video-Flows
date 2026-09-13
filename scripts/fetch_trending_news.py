#!/usr/bin/env python3
"""
Fetches recent, real news headlines about a specific country from NewsAPI.org, for
generate_trending_script.py's "trending today" video series.

Filters out headlines about tragedy, violence, war, disaster, or crime — this is a
fully unattended, documentary/curiosity-style entertainment channel with zero human
review before posting, not a news outlet. Turning a real tragedy into "content", or
having GPT summarize a fast-moving/sensitive story and get a detail wrong with no one
checking, is exactly the kind of thing that should never ship automatically.

Requires:
    NEWS_API_KEY environment variable (GitHub Secret: NEWS_API_KEY, from newsapi.org)

Usage:
    python scripts/fetch_trending_news.py --country Japan
"""
import argparse
import datetime
import os
import sys

import requests

NEWS_API_URL = "https://newsapi.org/v2/everything"

# Skip any headline touching these topics entirely, rather than trying to have GPT
# write around them tactfully — safest default for a zero-review pipeline.
SENSITIVE_KEYWORDS = [
    "killed", "kills", "dead", "death", "dies", "died", "murder", "shooting",
    "shot dead", "war", "attack", "bombing", "bomb", "terror", "terrorist",
    "massacre", "disaster", "earthquake", "flood", "wildfire", "famine",
    "genocide", "rape", "abuse", "assault", "kidnap", "hostage", "coup",
    "riot", "crash", "explosion", "casualties", "injured", "wounded", "crisis",
]


def _is_sensitive(title, description):
    text = f"{title or ''} {description or ''}".lower()
    return any(word in text for word in SENSITIVE_KEYWORDS)


def _is_mostly_non_english_script(title):
    """NewsAPI's language=en filter isn't fully reliable — a handful of non-English
    headlines (Japanese, Arabic, etc.) slip through. GPT is instructed to write
    English narration from these headlines, so a title that's mostly non-Latin
    script would just confuse it. Cheap heuristic: if most letters aren't ASCII,
    skip it rather than trying to translate or interpret it blind."""
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for c in letters if ord(c) > 127)
    return non_ascii / len(letters) > 0.3


def fetch_trending_headlines(country_name, max_results=5, days_back=3):
    """Return a list of {"title", "description", "source", "url"} for recent, safe
    headlines mentioning `country_name`. Returns [] if none are found (a quiet news
    day, or everything found was filtered as sensitive) — the caller falls back to
    the existing curated-facts template in that case, same as a missing API key."""
    api_key = os.environ.get("NEWS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("NEWS_API_KEY is not set — cannot fetch trending news.")

    since = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    params = {
        # qInTitle (not q) requires the country name IN THE HEADLINE — a plain full-text
        # "q" search matches any article that mentions the country anywhere, including
        # incidental namedrops (a stock index, a musician's tour date) that aren't
        # actually about the country at all.
        "qInTitle": country_name,
        "from": since,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
    }
    # Pass the key as a header, NOT a query param: on an HTTP error, requests'
    # raise_for_status() embeds the full request URL in the exception message, and the
    # caller logs that exception to CI (public logs on a public repo). A key in the URL
    # would leak; a key in a header never appears there. NewsAPI accepts either.
    headers = {"X-Api-Key": api_key}
    try:
        resp = requests.get(NEWS_API_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        # Belt-and-suspenders: never let an API error string carry the key onward, even
        # if a future change reintroduces it somewhere in the request.
        raise RuntimeError(f"NewsAPI request failed: HTTP {resp.status_code}") from None
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message', 'unknown error')}")

    results = []
    seen_titles = set()
    for article in data.get("articles", []):
        title = (article.get("title") or "").strip()
        description = (article.get("description") or "").strip()
        if not title or title in seen_titles or "[Removed]" in title:
            continue
        if _is_sensitive(title, description):
            continue
        if _is_mostly_non_english_script(title):
            continue
        seen_titles.add(title)
        results.append({
            "title": title,
            "description": description,
            "source": (article.get("source") or {}).get("name", ""),
            "url": article.get("url", ""),
        })
        if len(results) >= max_results:
            break
    return results


def main():
    # Headlines can contain arbitrary Unicode (smart quotes, non-English names) that
    # Windows consoles (cp1252) can't print — widen stdout so this CLI helper doesn't
    # crash on it. Harmless on Linux CI's UTF-8 locale.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", required=True)
    ap.add_argument("--max-results", type=int, default=5)
    args = ap.parse_args()
    headlines = fetch_trending_headlines(args.country, max_results=args.max_results)
    if not headlines:
        print(f"No safe/recent headlines found for {args.country}.")
    for h in headlines:
        print(f"- {h['title']} ({h['source']})")


if __name__ == "__main__":
    main()
