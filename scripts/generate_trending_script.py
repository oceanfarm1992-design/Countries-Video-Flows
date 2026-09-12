#!/usr/bin/env python3
"""
Stage 1 (trending variant): a third, independent series alongside
generate_country_script.py and generate_hook_script.py, on the same channels. Where
those write documentary-style or curiosity-hook narration from curated/GPT-knowledge
facts, this one summarizes REAL, CURRENT news headlines about today's country —
fetched from NewsAPI.org (see fetch_trending_news.py), not invented or recalled from
GPT's training data.

Every downstream stage (fetch_footage.py, generate_tts.py, generate_captions.py,
generate_intro.py, assemble_video.py, post_buffer.py, post_youtube.py) consumes
build/script.json + the caption files purely by key/filename, so this writes the
exact same shape the other two generators do and the rest of the pipeline runs
completely unmodified.

Rotation: same day+slot math as the other two generators, offset by a THIRD of the
country list (distinct from the hook series' half-list offset) so all three series
tend to cover different countries on the same day/slot.

If NewsAPI has no safe, recent headlines for today's country (a quiet news day, or
NEWS_API_KEY missing), falls back to generate_country_script.py's curated-facts
template — same fallback path already used elsewhere when live data isn't available,
so the pipeline never hard-fails or skips a slot.

Requires:
    OPENAI_API_KEY environment variable (GitHub Secret: OPENAI_API_KEY)
    NEWS_API_KEY environment variable (GitHub Secret: NEWS_API_KEY, from newsapi.org)

Output:
    build/script.json   { id, name, text, hook, narration, footage_query, ... }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_trending_script.py
    python scripts/generate_trending_script.py --index 5
    python scripts/generate_trending_script.py --config config/countries.json --out build
"""
import argparse
import json
import os
import re
import textwrap
from datetime import date

from generate_country_script import intro_line_for, _country_hashtag, _fallback_script
from fetch_trending_news import fetch_trending_headlines
from fact_check import verify_narration

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# OpenAI narration, grounded in real fetched headlines
# ---------------------------------------------------------------------------

def _gpt_script(country: dict, openai_cfg: dict, headlines: list):
    """Return (hook, narration, segments). Unlike the other two generators, the
    narration must be grounded ONLY in the provided headlines — no outside facts,
    no embellishment, no speculation. Same segments contract as the other scripts
    ({"text", "visual"})."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot call GPT.")

    client = OpenAI(api_key=api_key)
    name = country["name"]
    headlines_block = "\n".join(
        f"- {h['title']}" + (f" — {h['description']}" if h.get("description") else "")
        for h in headlines
    )

    base_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts narration summarizing what is CURRENTLY
        trending in the news about {name}, for a "trending today" series. The video
        runs 45-70 seconds read at a natural pace. Write ONLY the spoken narration —
        no stage directions, no titles.

        Below are real, recent news headlines about {name}. Base the ENTIRE narration
        ONLY on these headlines — do not add any fact, statistic, name, or claim that
        is not directly stated in them. Do not speculate about causes, motives, or
        outcomes beyond what is stated. If a headline is unclear or thin on detail,
        summarize only what it actually says rather than filling in gaps.

        Open with a hook sentence naming what's happening right now. Then cover the
        most interesting 2-4 of these stories in flowing narration. Close with a brief
        line tying it back to {name}, then speak this call-to-action naturally: "If
        you're from {name}, comment 'I love my country' below! Follow for more
        stories from around the world."

        Tone: neutral, informative, and genuinely curious — like a current-affairs
        explainer, not a tabloid. No political opinions, no editorializing, no taking
        a side. Report what is happening, not what should happen.

        Today's headlines about {name}:
        {headlines_block}
    """).strip()

    system_prompt = base_prompt + " " + textwrap.dedent(f"""
        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70 characters)
            naming what's trending — this is burned onto the video as text, so it
            must stand alone without the narration.
          - "segments": an array of 12 to 16 SHORT objects (short beats — one
            sentence, or even half a sentence, each). Each object has:
              - "text": a short beat of the narration (what the voiceover reads here).
              - "visual": a short English stock-media search query (2-5 words)
                describing ONE concrete, filmable subject that matches this beat —
                prefer a general subject tied to {name} (a city, landmark, landscape,
                or activity) since stock footage cannot depict a specific news event.
        Concatenating every "text" in order must read as one smooth narration that
        starts with the hook and ends with these two calls-to-action, in this order:
        first something like "If you're from {name}, comment 'I love my country'
        below!", then "Follow for more stories from around the world." Both are
        REQUIRED. IMPORTANT: the combined narration must total between 160 and 185
        spoken words — count them and do not go under 160. Output ONLY the JSON
        object.
    """).strip()

    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Write the trending-today narration for {name}."},
        ],
        max_tokens=openai_cfg.get("max_tokens", 700),
        temperature=openai_cfg.get("temperature", 0.7),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    hook = (data.get("hook") or "").strip() or f"WHAT'S TRENDING IN {name.upper()} TODAY"
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual or country["footage_query"]})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# Caption / metadata helpers
# ---------------------------------------------------------------------------

def _write_platform_captions(country: dict, hook: str, config: dict, out_dir: str):
    hashtags = config.get("hashtags", {})
    name = country["name"]
    ctag = _country_hashtag(name)

    hook_title = re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), hook.title()).rstrip(".")
    max_hook = 91
    if len(hook_title) > max_hook:
        hook_title = hook_title[:max_hook].rsplit(" ", 1)[0] + "…"
    yt_title = f"{hook_title} #Shorts"

    yt_desc = (
        f"{name} 🌍 {hook_title}\n\n"
        "What's trending about this country right now, explained in under a minute.\n\n"
        "Subscribe for daily trending stories from around the world! 🌏\n\n"
        f"{ctag} {hashtags.get('youtube', '')}"
    )

    fb_cap = (
        f"🌍 {hook_title}\n\n"
        f"Did you catch this about {name}? Let us know below! 👇\n\n"
        f"{ctag} {hashtags.get('facebook', '')}"
    )

    tt_cap = (
        f"{hook_title} 📰\n\n"
        f"{ctag} {hashtags.get('tiktok', '')}"
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate a trending-today country short script.")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--index", type=int, default=None,
                    help="Force a specific country index (0-based), overriding all rotation.")
    ap.add_argument("--slot", type=int, default=None, choices=[0, 1, 2],
                    help="Which of today's 3 videos this is (0, 1, or 2). "
                         "Combined with date rotation so each slot gets a distinct country.")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)

    countries = config["countries"]
    if not countries:
        raise SystemExit("No countries found in config.")

    # Offset by a third of the list — distinct from the hook series' half-list offset
    # — so all three series tend to land on different countries the same day/slot.
    offset = len(countries) // 3

    if args.index is not None:
        idx = args.index % len(countries)
    elif args.slot is not None:
        day_number = (date.today() - date(2026, 7, 23)).days
        raw_index = day_number * 3 + args.slot + offset
        idx = raw_index % len(countries)
    else:
        day_number = (date.today() - date(2026, 7, 23)).days
        raw_index = day_number + offset
        idx = raw_index % len(countries)

    country = countries[idx]
    name = country["name"]

    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    news_api_key = os.environ.get("NEWS_API_KEY", "").strip()

    headlines = []
    if news_api_key:
        try:
            print(f"[generate_trending_script] fetching NewsAPI headlines for {name} ...")
            headlines = fetch_trending_headlines(name)
        except Exception as exc:
            print(f"[generate_trending_script] NewsAPI error: {exc}")
    else:
        print("[generate_trending_script] NEWS_API_KEY not set — skipping live headlines")

    if headlines and OPENAI_AVAILABLE and api_key:
        print(f"[generate_trending_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} "
              f"with {len(headlines)} headline(s) ...")
        try:
            hook, narration, segments = _gpt_script(country, openai_cfg, headlines)
            ok, issues = verify_narration(narration, name, openai_cfg)
            if not ok:
                print(f"[generate_trending_script] fact-check flagged: {issues} — regenerating once")
                hook, narration, segments = _gpt_script(country, openai_cfg, headlines)
                ok, issues = verify_narration(narration, name, openai_cfg)
                if not ok:
                    print(f"[generate_trending_script] fact-check flagged again: {issues} — using fallback script")
                    narration, segments = _fallback_script(country)
                    hook = f"DISCOVER {name.upper()} TODAY"
                    source = "fallback"
                else:
                    source = "openai"
            else:
                source = "openai"
        except Exception as exc:
            print(f"[generate_trending_script] OpenAI error: {exc} — using fallback script")
            narration, segments = _fallback_script(country)
            hook = f"DISCOVER {name.upper()} TODAY"
            source = "fallback"
    else:
        reason = "no safe/recent headlines found" if not headlines else (
            "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set")
        print(f"[generate_trending_script] {reason} — using fallback script")
        narration, segments = _fallback_script(country)
        hook = f"DISCOVER {name.upper()} TODAY"
        source = "fallback"

    for seg in segments:
        seg["words"] = len(seg["text"].split())

    record = {
        "id": country["id"],
        "name": name,
        "title": name,
        "author": name,
        "specialty": country["specialty"],
        "hook": hook,
        "footage_query": country["footage_query"],
        "intro_line": intro_line_for(country, idx),
        "iso2": country.get("iso2", ""),
        "lat": country.get("lat"),
        "lon": country.get("lon"),
        "text": f"{name} — {hook}",
        "narration": narration,
        "segments": segments,
        "narration_source": source,
        "country_index": idx,
        "country_total": len(countries),
        "headline_count": len(headlines),
        "series": "trending",
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    _write_platform_captions(country, hook, config, args.out)

    words = len(narration.split())
    print(
        f"[generate_trending_script] country {idx + 1}/{len(countries)}: "
        f"{name} (hook={hook!r}, {words} narration words, "
        f"{len(headlines)} headlines, source={source})"
    )


if __name__ == "__main__":
    main()
