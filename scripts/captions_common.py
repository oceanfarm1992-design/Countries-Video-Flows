#!/usr/bin/env python3
"""
Shared platform-caption builder for all three series (country / hook / trending).

Previously each generator carried its own near-identical _write_platform_captions;
this consolidates them into one function and layers deterministic per-day variation
on top (see daily_variation.py) so the hashtags, description wording, and TikTok
search tags differ day to day instead of being a fixed template — a fixed footprint
is itself a bot signal.

TikTok captions always carry #creatorsearchinsights plus exactly one of five
rotating search tags (config variation.tiktok_seo_rotating), per the channel's
TikTok-SEO requirement.

Writes: caption_meta.txt, caption_tiktok.txt, caption_youtube.txt, yt_title.txt
"""
import hashlib
import os
import re
import unicodedata


def _country_hashtag(name: str) -> str:
    """Country-specific hashtag: accent-stripped, lowercase, letters only.
    e.g. "São Tomé and Príncipe" -> "#saotomeandprincipe", "Japan" -> "#japan"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "#" + re.sub(r"[^a-z]", "", ascii_name.lower())


def _seed(seed_int, salt):
    h = hashlib.sha256(f"{seed_int}:{salt}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _title_from_hook(hook: str) -> str:
    """Title-case an ALL-CAPS/plain hook for a readable YouTube title. str.title()
    capitalises the letter after an apostrophe ("World'S") — put it back to lower."""
    return re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), hook.title()).rstrip(".")


def _vary_hashtags(pool_str: str, seed_int, salt, keep: int, exclude=()) -> str:
    """Deterministically pick and reorder a subset of `keep` hashtags from a pool
    string, so the tag set differs per day without authoring many static variants.
    Order is shuffled by the seed too. Preserves the original tags (no invented
    ones). If the pool has <= keep tags, returns them in a seeded order.

    `exclude` drops tags that are managed elsewhere in the caption (the TikTok SEO
    block), so the pool can't re-emit one and produce a duplicate hashtag."""
    tags = [t for t in pool_str.split()
            if t.startswith("#") and t.lower() not in exclude]
    if not tags:
        return pool_str
    # Deterministic shuffle: sort by a per-tag hash salted with the day seed.
    ordered = sorted(tags, key=lambda t: _seed(seed_int, f"{salt}:{t}"))
    n = min(keep, len(ordered)) if keep else len(ordered)
    return " ".join(ordered[:n])


# Per-series caption voice. subscribe_lines / fb_questions are small pools rotated
# by the day seed so the description wording varies while keeping each series' tone.
SERIES_COPY = {
    "country": {
        "yt_emoji": "🌍",
        "tt_suffix": "🌍",
        "subscribe_lines": [
            "Subscribe for a new country every day! 🌏",
            "Follow along — a new country every single day. 🌏",
            "New country, every day. Hit subscribe. 🌏",
        ],
        "fb_questions": [
            "Which fact surprised you the most? Drop it in the comments! 👇",
            "Had you heard any of these before? Let us know below! 👇",
            "What would you add about this country? Comment below! 👇",
        ],
    },
    "hook": {
        "yt_emoji": "🌍",
        "tt_suffix": "👀",
        "subscribe_lines": [
            "Subscribe for more hidden stories from around the world! 🌏",
            "Follow for more things you never knew about the world. 🌏",
            "More hidden gems every week — subscribe. 🌏",
        ],
        "fb_questions": [
            "Did you know this? Drop a comment below! 👇",
            "Bet this surprised you — tell us below! 👇",
            "Ever heard of this before? Let us know! 👇",
        ],
    },
    "trending": {
        "yt_emoji": "🌍",
        "tt_suffix": "📰",
        "subscribe_lines": [
            "Subscribe for daily trending stories from around the world! 🌏",
            "Follow for what's trending around the world each day. 🌏",
            "The world's daily headlines, in under a minute — subscribe. 🌏",
        ],
        "fb_questions": [
            "Did you catch this? Let us know below! 👇",
            "Following this story? Share your take below! 👇",
            "What do you make of this? Comment below! 👇",
        ],
    },
    "geography": {
        "yt_emoji": "🗺️",
        "tt_suffix": "🗺️",
        "subscribe_lines": [
            "Subscribe for a new geography explainer every day! 🌏",
            "Follow for more of the world's geography, explained. 🌏",
            "The world's geography, one country at a time — subscribe. 🌏",
        ],
        "fb_questions": [
            "Did you know this about the map? Let us know below! 👇",
            "What's the geography like where you live? Tell us below! 👇",
            "Which part surprised you most? Comment below! 👇",
        ],
    },
    "worlddata": {
        "yt_emoji": "🚩",
        "tt_suffix": "📊",
        "subscribe_lines": [
            "Subscribe for more flags and stats from around the world! 🌏",
            "Follow for the numbers behind every country. 🌏",
            "A new flag or stat every day — subscribe. 🌏",
        ],
        "fb_questions": [
            "Did you know this stat? Let us know below! 👇",
            "What does your flag mean? Tell us below! 👇",
            "Guess this one right? Comment below! 👇",
        ],
    },
}


def _pick(pool, seed_int, salt):
    return pool[_seed(seed_int, salt) % len(pool)]


def _tiktok_seo_tags(config, seed_int):
    """#creatorsearchinsights (always) + exactly one rotating search tag."""
    variation = config.get("variation", {})
    required = variation.get("tiktok_seo_required", "#creatorsearchinsights")
    rotating = variation.get("tiktok_seo_rotating", [])
    if not rotating:
        return required
    chosen = rotating[_seed(seed_int, "ttseo") % len(rotating)]
    return f"{required} {chosen}"


def write_platform_captions(series, country, hook, config, out_dir, seed_int):
    """Build and write the four platform caption files for `series`, applying
    deterministic per-day variation keyed by `seed_int` (the day number)."""
    copy = SERIES_COPY.get(series, SERIES_COPY["country"])
    hashtags = config.get("hashtags", {})
    name = country["name"]
    specialty = country.get("specialty", "")
    facts = country.get("facts", [])
    ctag = _country_hashtag(name)
    first_fact = facts[0] if facts else specialty

    hook_title = _title_from_hook(hook)
    max_hook = 91  # reserve 8 chars for " #Shorts"; hard limit 100
    if len(hook_title) > max_hook:
        hook_title = hook_title[:max_hook].rsplit(" ", 1)[0] + "…"
    yt_title = f"{hook_title} #Shorts"

    subscribe = _pick(copy["subscribe_lines"], seed_int, "sub")
    fb_question = _pick(copy["fb_questions"], seed_int, "fbq")

    # Vary hashtag subsets per day (country tag always prepended separately).
    yt_tags = _vary_hashtags(hashtags.get("youtube", ""), seed_int, "yt", keep=8)
    fb_tags = _vary_hashtags(hashtags.get("facebook", ""), seed_int, "fb", keep=6)

    # The SEO block owns #creatorsearchinsights and the five rotating tags. Some of
    # those also live in the general TikTok pool, so exclude them there — otherwise a
    # caption can carry two rotating tags (or the same tag twice, which reads as spam).
    variation = config.get("variation", {})
    seo_managed = {variation.get("tiktok_seo_required", "#creatorsearchinsights").lower()}
    seo_managed.update(t.lower() for t in variation.get("tiktok_seo_rotating", []))
    tt_tags = _vary_hashtags(hashtags.get("tiktok", ""), seed_int, "tt", keep=8,
                             exclude=seo_managed)
    tt_seo = _tiktok_seo_tags(config, seed_int)

    # Country series historically led its description with the specialty; hook &
    # trending lead with the generated hook title. Preserve that distinction.
    lead = specialty if series == "country" else hook_title

    yt_desc = (
        f"{name} {copy['yt_emoji']} {lead}\n\n"
        f"Did you know? {first_fact}\n\n"
        f"{subscribe}\n\n"
        f"{ctag} {yt_tags}"
    )
    fb_cap = (
        f"{copy['yt_emoji']} {name} — {lead}\n\n"
        f"{fb_question}\n\n"
        f"{ctag} {fb_tags}"
    )
    tt_cap = (
        f"{lead} {copy['tt_suffix']}\n\n"
        f"{ctag} {tt_seo} {tt_tags}"
    )

    files = {
        "caption_meta.txt": fb_cap,
        "caption_tiktok.txt": tt_cap,
        "caption_youtube.txt": yt_desc,
        "yt_title.txt": yt_title[:100],
    }
    for fname, content in files.items():
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(content.strip() + "\n")
