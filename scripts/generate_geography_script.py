#!/usr/bin/env python3
"""
Stage 1 (geography variant): a 4th series alongside generate_country_script.py,
generate_hook_script.py, and generate_trending_script.py, on the same channels.
Instead of documentary facts, a curiosity hook, or news, this writes a short
"why does {country} have the geography it has" narration — real, verifiable
physical geography (borders, mountains, coastline, climate) — rendered over an
animated map instead of stock footage (see scripts/render_geography_map.py,
which stands in for fetch_footage.py for this series only).

Physical-geography facts (why a mountain range/coastline/border is where it
is) are lower hallucination-risk than curiosity-hook trivia: they're stable,
well-documented, non-negotiable geography, not obscure or invented trivia.

segments[].visual is repurposed here (same key, same schema every other series
uses) to hold the on-screen MAP LABEL for that beat (e.g. "Himalayas", "Bay of
Bengal") rather than a stock-media search query, since this series never
searches stock footage at all.

Every downstream stage consumes build/script.json + the caption files purely
by key/filename, so this writes the exact same shape the other generators do.

Rotation: same day+slot math as the other three generators, offset by
3/4 of the country list (distinct from country=0, trending's 1/3, and hook's
1/2) so same-day country collisions across all four series stay unlikely.

Requires:
    OPENAI_API_KEY environment variable (GitHub Secret: OPENAI_API_KEY)

Output:
    build/script.json   { id, name, text, hook, narration, segments, ... }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_geography_script.py
    python scripts/generate_geography_script.py --index 5
    python scripts/generate_geography_script.py --config config/countries.json --out build
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

# Physical-geography angles, cycling by "cycle" (how many full passes through
# the country list have happened) the same way the other generators rotate.
GEOGRAPHY_ANGLES = [
    {
        "id": "borders",
        "desc": "why {name}'s borders are where they are — the mountains, rivers, "
                "deserts, or seas that act as natural barriers and shaped where the "
                "country's edges ended up",
        "fallback_hook": "WHY {NAME}'S BORDERS ARE WHERE THEY ARE",
    },
    {
        "id": "climate",
        "desc": "how {name}'s climate is shaped by its physical geography — elevation, "
                "ocean currents, monsoon patterns, or latitude — and why different "
                "parts of the country can feel like different worlds",
        "fallback_hook": "WHY {NAME}'S CLIMATE IS SHAPED THE WAY IT IS",
    },
    {
        "id": "defining_feature",
        "desc": "the one physical feature — a mountain range, river system, "
                "peninsula, or desert — that most defines {name} and its geography",
        "fallback_hook": "THE ONE FEATURE THAT DEFINES {NAME}'S GEOGRAPHY",
    },
    {
        "id": "coastline_history",
        "desc": "how {name}'s coastline, ports, or access (or lack of access) to the "
                "sea shaped its history, trade, and development",
        "fallback_hook": "HOW {NAME}'S COASTLINE SHAPED ITS ENTIRE HISTORY",
    },
]


# ---------------------------------------------------------------------------
# Fallback: build a simple geography narration without GPT (used when no key)
# ---------------------------------------------------------------------------

def _fallback_script(country: dict, angle: dict):
    """No-GPT fallback: a templated geography opener + the curated facts. Mirrors
    the other generators' _fallback_script so the pipeline never hard-fails when
    OPENAI_API_KEY is temporarily unavailable. Every segment's label is just the
    country name -- safe, always-correct default when there's no GPT-identified
    specific feature to name."""
    name = country["name"]
    specialty = country["specialty"]
    facts = country.get("facts", [])

    hook = angle["fallback_hook"].format(NAME=name.upper())
    opener = f"Here's the geography behind {name}: {specialty}."

    segments = [{"text": opener, "visual": name}]
    for fact in facts:
        segments.append({"text": fact + ".", "visual": name})
    segments.append({
        "text": f"That's the land that shaped {name}. "
                f"If you're from {name}, comment 'I love my country' below! "
                "Follow for more stories from around the world.",
        "visual": name,
    })
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# OpenAI narration
# ---------------------------------------------------------------------------

def _gpt_script(country: dict, openai_cfg: dict, angle: dict,
                min_words: int = 160, max_words: int = 185):
    """Return (hook, narration, segments) -- same segments contract as the other
    generators, except "visual" holds a MAP LABEL (a place/feature name to burn
    onto the animated map) instead of a stock-footage search query.

    min_words/max_words set the spoken-length target so the daily rotation can vary
    video duration (see daily_variation.py's WORD_BANDS)."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot call GPT.")

    client = OpenAI(api_key=api_key)
    name = country["name"]
    facts_block = "\n".join(f"- {f}" for f in country.get("facts", []))
    angle_desc = angle["desc"].format(name=name)

    base_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts narration about {name}'s PHYSICAL
        GEOGRAPHY for a "geography explainer" series -- real, verifiable facts about
        why the country's borders, mountains, coastline, or climate are what they
        are. This is shown over an animated map (the country highlighted, with text
        labels naming features as they come up), not video footage. Write ONLY the
        spoken narration -- no stage directions, no titles. The video runs 45-70
        seconds read at a natural pace.

        This episode's angle: {angle_desc}.

        Open with a hook sentence about this specific angle, then explain 3-5
        genuinely interesting, VERIFIABLE geographic facts that support it -- real
        mountain ranges, rivers, coastlines, or borders with their actual names,
        stay accurate, never invent a place, statistic, or event. Close with a brief
        line tying it back to {name}, then speak this call-to-action naturally: "If
        you're from {name}, comment 'I love my country' below! Follow for more
        stories from around the world."

        STRICT RULES (non-negotiable):
        - Every named place (mountain, river, sea, region, city) must be real and
          actually located where you say it is -- this narration is read directly
          against a map, so a wrong or invented location is immediately visible.
        - Never invent statistics, records, or events.
        - Stay about physical geography (terrain, climate, water, borders) -- not
          culture, cuisine, or ethnicity.

        Tone: like a great geography explainer video -- curious and clear, not a
        dry textbook. No bullet points or headers -- pure flowing prose only.
    """).strip()

    system_prompt = base_prompt + " " + textwrap.dedent("""
        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70 characters)
            capturing this episode's angle -- this is burned onto the video as text,
            so it must stand alone without the narration.
          - "segments": an array of 10 to 14 SHORT objects (short beats -- one
            sentence, or even half a sentence, each). Each object has:
              - "text": a short beat of the narration (what the voiceover reads here).
              - "visual": a SHORT on-screen map label (1-4 words) naming the specific
                real place or feature this beat is about -- e.g. "Himalayas", "Bay of
                Bengal", "Andes Mountains" -- NOT a stock-footage search query (no
                footage is used in this series). Use "{name}" itself for beats that
                are general/not about one specific named feature.
        Concatenating every "text" in order must read as one smooth narration that
        starts with the hook and ends with these two calls-to-action, in this order:
        first something like "If you're from {name}, comment 'I love my country'
        below!", then "Follow for more stories from around the world." Both are
        REQUIRED. IMPORTANT: the combined narration must total between {min_words}
        and {max_words} spoken words -- count them and do not go under {min_words};
        if you are short, add more short beats with specific, accurate detail rather
        than padding. Output ONLY the JSON object.
    """.format(name=name, min_words=min_words, max_words=max_words)).strip()

    user_message = textwrap.dedent(f"""
        Country: {name}
        Specialty: {country['specialty']}

        Background facts (use only if genuinely relevant to this episode's angle --
        prefer other accurate, verifiable geography facts about {name} that fit the
        angle better):
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
    hook = (data.get("hook") or "").strip() or angle["fallback_hook"].format(NAME=name.upper())
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual or name})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# Caption / metadata writing is shared via captions_common.write_platform_captions,
# with per-day hashtag/description/tag variation applied there.


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate a geography-explainer country short script via OpenAI.")
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

    # Offset by 3/4 of the list -- distinct from country=0, trending's 1/3, and
    # hook's 1/2 -- so same-day country collisions across all four series stay
    # unlikely.
    offset = (3 * len(countries)) // 4

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
    angle = GEOGRAPHY_ANGLES[cycle % len(GEOGRAPHY_ANGLES)]

    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if OPENAI_AVAILABLE and api_key:
        print(f"[generate_geography_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} "
              f"(angle={angle['id']!r}) ...")
        try:
            hook, narration, segments = _gpt_script(country, openai_cfg, angle,
                                                     args.min_words, args.max_words)
            ok, issues = verify_narration(narration, country["name"], openai_cfg)
            if not ok:
                print(f"[generate_geography_script] fact-check flagged: {issues} — regenerating once")
                hook, narration, segments = _gpt_script(country, openai_cfg, angle,
                                                        args.min_words, args.max_words)
                ok, issues = verify_narration(narration, country["name"], openai_cfg)
                if not ok:
                    print(f"[generate_geography_script] fact-check flagged again: {issues} — using fallback script")
                    hook, narration, segments = _fallback_script(country, angle)
                    source = "fallback"
                else:
                    source = "openai"
            else:
                source = "openai"
        except Exception as exc:
            print(f"[generate_geography_script] OpenAI error: {exc} — using fallback script")
            hook, narration, segments = _fallback_script(country, angle)
            source = "fallback"
    else:
        reason = "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set"
        print(f"[generate_geography_script] {reason} — using fallback script")
        hook, narration, segments = _fallback_script(country, angle)
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
        "angle": angle["id"],
        "series": "geography",
        "variant_seed": seed,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    write_platform_captions("geography", country, hook, config, args.out, seed)

    words = len(narration.split())
    print(
        f"[generate_geography_script] country {idx + 1}/{len(countries)}: "
        f"{country['name']} (angle={angle['id']!r}, hook={hook!r}, "
        f"{words} narration words, source={source})"
    )


if __name__ == "__main__":
    main()
