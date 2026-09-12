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
        "q": country_name,
        "from": since,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": api_key,
    }
    resp = requests.get(NEWS_API_URL, params=params, timeout=30)
    resp.raise_for_status()
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
