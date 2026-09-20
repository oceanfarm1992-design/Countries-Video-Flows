#!/usr/bin/env python3
"""
Stage 1 (world-data variant): a 5th series alongside country/hook/trending/
geography, on the same channels. Alternates between two graphics-only
sub-types for the same country — no stock footage in either, same structural
advantage that made "geography" immune to the wrong-country-clip class of
bug this codebase has already had to fix twice:

  - "flags": what the country's flag colors/symbols actually mean, rendered
    over the flag itself (fetched free from flagcdn.com, same source
    generate_intro.py already uses).
  - "stats": a "did you know" narration built EXCLUSIVELY from numbers
    already present in this country's curated facts[] — the prompt is
    explicitly forbidden from citing any statistic not already supplied,
    the same low-hallucination-risk trick geography uses for physical
    facts. Rendered over the country's map highlight (render_geography_map's
    existing basemap + country mask, reused, not reimplemented).

segments[].visual is repurposed here (same key, same schema every other
series uses) to hold the on-screen SYMBOL/STAT LABEL for that beat (e.g.
"Red = Bloodshed", "80% Sahara") rather than a stock-media search query,
since this series never searches stock footage at all.

Rotation: same day+slot math as the other generators, offset by 1/6 of the
country list (distinct from country=0, trending's 1/3, hook's 1/2,
geography's 3/4) so same-day country collisions across all five series stay
unlikely.

Requires:
    OPENAI_API_KEY environment variable (GitHub Secret: OPENAI_API_KEY)

Output:
    build/script.json   { id, name, text, hook, narration, segments, subtype, ... }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_worlddata_script.py
    python scripts/generate_worlddata_script.py --index 5
    python scripts/generate_worlddata_script.py --config config/countries.json --out build
"""
import argparse
import json
import os
import textwrap
from datetime import date

from generate_country_script import intro_line_for
from captions_common import write_platform_captions
from daily_variation import day_number_for
from fact_check import verify_narration

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

# Cycled by "cycle" (how many full passes through the country list have
# happened) the same way GEOGRAPHY_ANGLES rotates in generate_geography_script.py
# — so the same country gets the OTHER sub-type next time it comes up.
WORLDDATA_SUBTYPES = ["flags", "stats"]


# ---------------------------------------------------------------------------
# Fallback: build a safe narration without GPT (used when no key, or GPT/
# fact-check both fail twice)
# ---------------------------------------------------------------------------

def _fallback_script(country: dict, subtype: str):
    """No-GPT fallback. For BOTH subtypes this only ever narrates the curated
    facts[] verbatim (never invents flag symbolism, which this codebase has
    no curated data for) — safe by construction, even for the "flags"
    subtype, where the flag still renders as the visual backdrop regardless
    of what the narration says. Mirrors the other generators'
    _fallback_script so the pipeline never hard-fails when OPENAI_API_KEY is
    temporarily unavailable."""
    name = country["name"]
    specialty = country["specialty"]
    facts = country.get("facts", [])

    if subtype == "flags":
        hook = f"THE FLAG OF {name.upper()}"
        opener = f"Here's {name}, one flag among 195 in our world. Let's look closer."
    else:
        hook = f"{name.upper()} BY THE NUMBERS"
        opener = f"Here's {name} by the numbers: {specialty}."

    segments = [{"text": opener, "visual": ""}]
    for fact in facts:
        segments.append({"text": fact + ".", "visual": ""})
    segments.append({
        "text": f"That's {name} in a nutshell. "
                f"If you're from {name}, comment 'I love my country' below! "
                "Follow for more stories from around the world.",
        "visual": "",
    })
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# OpenAI narration
# ---------------------------------------------------------------------------

def _gpt_script_flags(country: dict, openai_cfg: dict, min_words: int, max_words: int):
    """Return (hook, narration, segments) for the "flags" sub-type. "visual"
    holds a SHORT on-screen symbol/color label (e.g. "Red = Bloodshed") to
    burn over the flag image."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot call GPT.")

    client = OpenAI(api_key=api_key)
    name = country["name"]

    system_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts narration decoding the national
        flag of {name} for a "flags decoded" series -- what the colors and
        symbols on the flag actually mean and represent. This is shown over the
        flag itself (with short text labels naming each symbol/color as it comes
        up), not video footage. Write ONLY the spoken narration -- no stage
        directions, no titles. The video runs 45-70 seconds read at a natural
        pace.

        Open with a hook sentence about the flag, then explain 3-5 genuinely
        accurate details about what its colors and symbols represent -- stay
        strictly to well-established, widely-documented meaning; if a color or
        symbol's meaning is genuinely disputed or uncertain, say so rather than
        inventing a confident answer. Close with a brief line tying it back to
        {name}, then speak this call-to-action naturally: "If you're from
        {name}, comment 'I love my country' below! Follow for more stories from
        around the world."

        STRICT RULES (non-negotiable):
        - Never invent a color meaning, symbol meaning, or historical claim you
          are not confident is real and well-documented.
        - If you are not confident about a specific meaning, describe the
          symbol/color factually (what it looks like, when it was adopted)
          rather than asserting an unverified interpretation.
        - Stay about the flag itself -- not unrelated culture, cuisine, or
          politics.

        Tone: like a great flags-explained video -- curious and clear, not a
        dry textbook. No bullet points or headers -- pure flowing prose only.

        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70
            characters) about this flag -- burned onto the video as text, so it
            must stand alone without the narration.
          - "segments": an array of 10 to 14 SHORT objects (short beats -- one
            sentence, or even half a sentence, each). Each object has:
              - "text": a short beat of the narration.
              - "visual": a SHORT on-screen label (1-5 words) naming the
                specific color/symbol this beat is about -- e.g. "Red =
                Bloodshed", "5 Stars = Provinces", "Adopted 1947" -- use an
                empty string "" for beats that are general/not about one
                specific color or symbol.
        Concatenating every "text" in order must read as one smooth narration
        that starts with the hook and ends with these two calls-to-action, in
        this order: first something like "If you're from {name}, comment 'I
        love my country' below!", then "Follow for more stories from around the
        world." Both are REQUIRED. IMPORTANT: the combined narration must total
        between {min_words} and {max_words} spoken words -- count them and do
        not go under {min_words}; if you are short, add more short beats with
        specific, accurate detail rather than padding. Output ONLY the JSON
        object.
    """).strip()

    user_message = f"Country: {name}\nGeneral context: {country['specialty']}"

    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=openai_cfg.get("max_tokens", 700),
        temperature=openai_cfg.get("temperature", 0.7),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    hook = (data.get("hook") or "").strip() or f"THE FLAG OF {name.upper()}"
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


def _gpt_script_stats(country: dict, openai_cfg: dict, min_words: int, max_words: int):
    """Return (hook, narration, segments) for the "stats" sub-type. Narration
    is restricted to numbers already present in country["facts"] -- the
    prompt is explicitly forbidden from citing anything not supplied, the
    same trick geography uses to keep physical-geography claims verifiable.
    "visual" holds the stat callout text to burn over the map highlight."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot call GPT.")

    client = OpenAI(api_key=api_key)
    name = country["name"]
    facts_block = "\n".join(f"- {f}" for f in country.get("facts", []))

    system_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts "by the numbers" narration about
        {name} for a stats-explainer series. This is shown over a highlighted
        map of {name} (with short text callouts naming each stat as it comes
        up), not video footage. Write ONLY the spoken narration -- no stage
        directions, no titles. The video runs 45-70 seconds read at a natural
        pace.

        Open with a hook sentence, then turn the facts supplied below into 3-5
        punchy "did you know" beats. Close with a brief line tying it back to
        {name}, then speak this call-to-action naturally: "If you're from
        {name}, comment 'I love my country' below! Follow for more stories from
        around the world."

        STRICT RULES (non-negotiable):
        - You may ONLY cite a number, percentage, or statistic that is
          EXPLICITLY present in the facts supplied below. NEVER invent, round
          differently, estimate, or infer a new statistic that isn't already
          stated there.
        - If the supplied facts don't contain enough numbers to fill the video,
          rephrase and elaborate on the numbers you DO have rather than
          inventing new ones.
        - Stay strictly about the numbers/facts supplied -- do not introduce
          unrelated claims.

        Tone: energetic and punchy, like a great "did you know" stats video --
        not a dry recitation. No bullet points or headers -- pure flowing prose
        only.

        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70
            characters) -- burned onto the video as text, so it must stand
            alone without the narration.
          - "segments": an array of 10 to 14 SHORT objects (short beats -- one
            sentence, or even half a sentence, each). Each object has:
              - "text": a short beat of the narration.
              - "visual": a SHORT on-screen callout (1-5 words) stating the
                specific number/stat this beat is about, taken directly from
                the supplied facts -- e.g. "80% Sahara", "170,000 Bunkers" --
                use an empty string "" for beats that aren't about one
                specific number.
        Concatenating every "text" in order must read as one smooth narration
        that starts with the hook and ends with these two calls-to-action, in
        this order: first something like "If you're from {name}, comment 'I
        love my country' below!", then "Follow for more stories from around the
        world." Both are REQUIRED. IMPORTANT: the combined narration must total
        between {min_words} and {max_words} spoken words -- count them and do
        not go under {min_words}. Output ONLY the JSON object.
    """).strip()

    user_message = textwrap.dedent(f"""
        Country: {name}

        Facts you may draw numbers from (do not use any number not present here):
        {facts_block}
    """).strip()

    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=openai_cfg.get("max_tokens", 700),
        temperature=openai_cfg.get("temperature", 0.7),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    hook = (data.get("hook") or "").strip() or f"{name.upper()} BY THE NUMBERS"
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


def _gpt_script(country, openai_cfg, subtype, min_words, max_words):
    if subtype == "flags":
        return _gpt_script_flags(country, openai_cfg, min_words, max_words)
    return _gpt_script_stats(country, openai_cfg, min_words, max_words)


# Caption / metadata writing is shared via captions_common.write_platform_captions,
# with per-day hashtag/description/tag variation applied there.


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate a world-data (flags/stats) country short script via OpenAI.")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--index", type=int, default=None,
                    help="Force a specific country index (0-based), overriding all rotation.")
    ap.add_argument("--slot", type=int, default=None, choices=[0, 1, 2],
                    help="Which of today's 3 videos this is (0, 1, or 2). "
                         "Combined with date rotation so each slot gets a distinct country.")
    ap.add_argument("--min-words", type=int, default=160,
                    help="Lower bound of the spoken-word target (daily rotation varies this).")
    ap.add_argument("--max-words", type=int, default=185,
                    help="Upper bound of the spoken-word target.")
    ap.add_argument("--variant-seed", type=int, default=None,
                    help="Seed for per-day caption variation (defaults to the day number).")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)

    countries = config["countries"]
    if not countries:
        raise SystemExit("No countries found in config.")

    # Offset by 1/6 of the list -- distinct from country=0, trending's 1/3,
    # hook's 1/2, and geography's 3/4 -- so same-day country collisions across
    # all five series stay unlikely.
    offset = len(countries) // 6

    if args.index is not None:
        idx = args.index % len(countries)
        cycle = 0
    elif args.slot is not None:
        day_number = (date.today() - date(2026, 7, 23)).days
        raw_index = day_number * 3 + args.slot + offset
        idx = raw_index % len(countries)
        cycle = raw_index // len(countries)
    else:
        day_number = (date.today() - date(2026, 7, 23)).days
        raw_index = day_number + offset
        idx = raw_index % len(countries)
        cycle = raw_index // len(countries)

    country = countries[idx]
    subtype = WORLDDATA_SUBTYPES[cycle % len(WORLDDATA_SUBTYPES)]

    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if OPENAI_AVAILABLE and api_key:
        print(f"[generate_worlddata_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} "
              f"(subtype={subtype!r}) ...")
        try:
            hook, narration, segments = _gpt_script(country, openai_cfg, subtype,
                                                     args.min_words, args.max_words)
            ok, issues = verify_narration(narration, country["name"], openai_cfg)
            if not ok:
                print(f"[generate_worlddata_script] fact-check flagged: {issues} — regenerating once")
                hook, narration, segments = _gpt_script(country, openai_cfg, subtype,
                                                        args.min_words, args.max_words)
                ok, issues = verify_narration(narration, country["name"], openai_cfg)
                if not ok:
                    print(f"[generate_worlddata_script] fact-check flagged again: {issues} — using fallback script")
                    hook, narration, segments = _fallback_script(country, subtype)
                    source = "fallback"
                else:
                    source = "openai"
            else:
                source = "openai"
        except Exception as exc:
            print(f"[generate_worlddata_script] OpenAI error: {exc} — using fallback script")
            hook, narration, segments = _fallback_script(country, subtype)
            source = "fallback"
    else:
        reason = "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set"
        print(f"[generate_worlddata_script] {reason} — using fallback script")
        hook, narration, segments = _fallback_script(country, subtype)
        source = "fallback"

    for seg in segments:
        seg["words"] = len(seg["text"].split())

    seed = args.variant_seed if args.variant_seed is not None else day_number_for()

    record = {
        "id": country["id"],
        "name": country["name"],
        "title": country["name"],
        "author": country["name"],
        "specialty": country["specialty"],
        "hook": hook,
        "footage_query": country.get("footage_query", ""),
        "intro_line": intro_line_for(country, cycle + idx),
        "iso2": country.get("iso2", ""),
        "lat": country.get("lat"),
        "lon": country.get("lon"),
        "text": f"{country['name']} — {hook}",
        "narration": narration,
        "segments": segments,
        "narration_source": source,
        "country_index": idx,
        "country_total": len(countries),
        "cycle": cycle,
        "subtype": subtype,
        "series": "worlddata",
        "variant_seed": seed,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    write_platform_captions("worlddata", country, hook, config, args.out, seed)

    words = len(narration.split())
    print(
        f"[generate_worlddata_script] country {idx + 1}/{len(countries)}: "
        f"{country['name']} (subtype={subtype!r}, hook={hook!r}, "
        f"{words} narration words, source={source})"
    )


if __name__ == "__main__":
    main()
