#!/usr/bin/env python3
"""
Stage 1 (countries variant): pick today's country and use OpenAI to generate
a ~40-50 second narration script about its specialties.

Rotation: countries cycle by date (3/day via --slot) so each country gets its own
video before the list repeats. When a country comes back around on a later pass
through the list, the narration is steered toward a different ANGLE (history, food,
wildlife, etc.) instead of retelling the same facts — see ANGLES below. Pass --index
to force a specific country (bypasses angle rotation, always tells the cycle-0 story).

Requires:
    OPENAI_API_KEY environment variable (GitHub Secret: OPENAI_API_KEY)

Output:
    build/script.json   { id, name, text, hook, narration, footage_query }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_country_script.py
    python scripts/generate_country_script.py --index 5
    python scripts/generate_country_script.py --config config/countries.json --out build
"""
import argparse
import json
import os
import random
import re
import textwrap
import unicodedata
from datetime import date

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

# Every time a country comes back around in the rotation (every len(countries)/3 days
# at 3 videos/day), the narration should take a different angle instead of retelling
# the same story — that's what made repeats feel boring. The rotation math already
# tells us deterministically which pass through the full list this is (raw_index //
# len(countries)), so we rotate through these fixed angles with zero extra state.
ANGLES = [
    "geography, landscapes, and natural wonders",
    "history and ancient civilizations",
    "culture, traditions, and daily life",
    "food, cuisine, and local flavors",
    "records, superlatives, and surprising trivia",
    "wildlife and nature",
]

# Once all 6 angles have been used, angles start repeating (cycle 6 -> angle 0 again,
# etc.) — that's fine, but the CONTENT within a repeated angle shouldn't be. SUBFOCI
# gives each angle a few narrower slices; which one is used shifts by "lap" (how many
# full passes through the 6 angles have happened), so the 2nd time a country gets the
# "history" angle it zooms into a different slice of history than the 1st time. This
# pushes real content convergence out to years rather than months, with no extra state.
SUBFOCI = {
    0: [  # geography, landscapes, and natural wonders
        "iconic landmarks and natural wonders",
        "climate, terrain, and physical geography",
        "rivers, coastlines, and bodies of water",
        "cities, regions, and how the landscape shaped where people settled",
    ],
    1: [  # history and ancient civilizations
        "ancient origins and early civilizations",
        "medieval or feudal-era history and famous rulers",
        "colonial era, independence, or nation-building history",
        "20th-century transformation and how the country got here today",
    ],
    2: [  # culture, traditions, and daily life
        "festivals, celebrations, and public traditions",
        "art, music, dance, and creative traditions",
        "religion, spirituality, and belief systems",
        "family life, social customs, and everyday etiquette",
    ],
    3: [  # food, cuisine, and local flavors
        "iconic national dishes and street food",
        "unique ingredients and how they're grown or sourced",
        "drinking traditions and beverages",
        "food festivals, markets, and dining culture",
    ],
    4: [  # records, superlatives, and surprising trivia
        "world records and superlatives",
        "strange laws, customs, or little-known trivia",
        "inventions, firsts, and things this country pioneered",
        "surprising numbers and statistics",
    ],
    5: [  # wildlife and nature
        "unique or endemic animal species",
        "national parks, reserves, and protected wilderness",
        "marine life and coastal ecosystems",
        "conservation stories and comeback species",
    ],
}


# ---------------------------------------------------------------------------
# Fallback: build a simple narration without GPT (used when no API key / lib)
# ---------------------------------------------------------------------------

def _fallback_script(country: dict):
    """No-GPT fallback: build (narration, segments) from the curated facts. Each fact
    becomes one segment; its visual query is the country's base footage_query so the
    footage stage still has something on-theme to fetch per segment."""
    name = country["name"]
    specialty = country["specialty"]
    facts = country.get("facts", [])
    base_visual = country.get("footage_query", f"{name} landscape cinematic")

    segments = [{"text": f"{name} — {specialty}.", "visual": base_visual}]
    for fact in facts:
        segments.append({"text": fact + ".", "visual": base_visual})
    segments.append({
        "text": f"There is no place on Earth quite like {name}. "
                f"If you're from {name}, comment 'I love my country' below! "
                "Follow for more stories from around the world.",
        "visual": base_visual,
    })
    narration = " ".join(s["text"] for s in segments)
    return narration, segments


# Spoken intro line over the globe zoom (so the opening isn't silent). Ties the words to
# the rotating-globe visual — the camera spins in and settles on the country — so the
# first thing viewers hear matches what they're watching. Rotated for variety.
INTRO_TEMPLATES = [
    "As the Earth spins, the world arrives in {name}.",
    "The globe turns, and it stops right here — {name}.",
    "Watch the world turn... it lands on {name}.",
    "As the planet spins, we touch down in {name}.",
    "The Earth keeps turning, and today it brings us to {name}.",
]


def intro_line_for(country, seed):
    return INTRO_TEMPLATES[seed % len(INTRO_TEMPLATES)].format(name=country["name"])


# ---------------------------------------------------------------------------
# OpenAI narration
# ---------------------------------------------------------------------------

def _gpt_script(country: dict, openai_cfg: dict, angle: str, subfocus: str, cycle: int):
    """Return (narration, segments). segments is a list of
    {"text": <spoken sentence(s)>, "visual": <stock-footage search query>} so the
    assemble stage can show a clip that MATCHES what's being said at that moment
    (e.g. narration mentions islands -> an island clip plays over those words)."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot call GPT.")

    client = OpenAI(api_key=api_key)
    facts_block = "\n".join(f"- {f}" for f in country.get("facts", []))

    if cycle == 0:
        angle_instruction = "weave in the facts below in a flowing, evocative way"
    else:
        angle_instruction = (
            f"this is telling #{cycle + 1} of {country['name']} in an ongoing daily "
            f"series, so do NOT retell the same story as earlier episodes — focus "
            f"specifically on {angle}, and within that, zoom in on {subfocus}. Treat the "
            f"facts below as background context only; draw mainly on OTHER genuinely "
            f"interesting, verifiable facts about {country['name']}'s {subfocus} that are "
            f"not in that list (stay accurate, never invent statistics or events), and "
            f"only reuse a listed fact if it's a strong fit for this angle"
        )

    base_prompt = openai_cfg["narration_prompt"].format(
        country_name=country["name"],
        angle_instruction=angle_instruction,
    )

    # Layer the segmentation contract on top of the existing narration prompt.
    system_prompt = base_prompt + " " + textwrap.dedent(f"""
        Return your answer as a JSON object with one key, "segments", whose value is an
        array of 12 to 16 SHORT objects (short beats — one sentence, or even half a
        sentence, each — so the video can cut to fresh imagery often and stay punchy).
        Each object has:
          - "text": a short beat of the narration (this is what the voiceover reads here).
          - "visual": a short English stock-media search query (2-5 words) describing ONE
            concrete, filmable subject that MATCHES this beat, so a clip or photo of
            exactly that can be shown while these words are spoken. Name the specific
            subject — a named mountain, city, dish, animal, landmark, or activity — not an
            abstract idea. Every visual should be about {country['name']} unless the
            subject is inherently generic.
        Concatenating every "text" in order must read as one smooth narration that starts
        with a strong hook and ends with these two calls-to-action, in this order: first
        something like "If you're from {country['name']}, comment 'I love my country'
        below!", then "Follow for more stories from around the world." Both of these are
        REQUIRED — do not drop the "I love my country" comment prompt even though it comes
        before the "Follow for more" line. IMPORTANT: the combined narration must total
        between 160 and 185 spoken words — count them and do not go under 160; if you are
        short, add more short beats with specific, accurate detail rather than padding.
        Output ONLY the JSON object.
    """).strip()

    user_message = textwrap.dedent(f"""
        Country: {country['name']}
        Specialty: {country['specialty']}

        Background facts (see system instructions for how to use these this time):
        {facts_block}
    """).strip()

    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=openai_cfg.get("max_tokens", 700),
        temperature=openai_cfg.get("temperature", 0.8),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual or country["footage_query"]})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")
    narration = " ".join(s["text"] for s in segments)
    return narration, segments


# ---------------------------------------------------------------------------
# Caption / metadata helpers
# ---------------------------------------------------------------------------

def _country_hashtag(name: str) -> str:
    """Build a country-specific hashtag: accent-stripped, lowercase, letters only.
    e.g. "São Tomé and Príncipe" -> "#saotomeandprincipe", "Japan" -> "#japan"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "#" + re.sub(r"[^a-z]", "", ascii_name.lower())


def _write_platform_captions(country: dict, narration: str, config: dict, out_dir: str):
    hashtags = config.get("hashtags", {})
    name = country["name"]
    specialty = country["specialty"]
    hook = country.get("hook_text", specialty)
    facts = country.get("facts", [])
    ctag = _country_hashtag(name)          # e.g. "#japan"
    first_fact = facts[0] if facts else specialty

    # YouTube title: hook text in title case is punchy and keyword-rich.
    # Python's str.title() capitalises the letter after an apostrophe ("World'S"),
    # so we fix that back to lower after the call.
    hook_title = re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), hook.title()).rstrip(".")
    # Reserve 8 chars for " #Shorts" suffix; hard limit 100.
    max_hook = 91
    if len(hook_title) > max_hook:
        hook_title = hook_title[:max_hook].rsplit(" ", 1)[0] + "…"
    yt_title = f"{hook_title} #Shorts"

    # YouTube description: keyword-rich above the fold, fact teaser, CTA.
    yt_desc = (
        f"{name} 🌍 {specialty}\n\n"
        f"Did you know? {first_fact}\n\n"
        "Subscribe for a new country every day! 🌏\n\n"
        f"{ctag} {hashtags.get('youtube', '')}"
    )

    # Facebook: conversational opener + engagement question to drive comments.
    fb_cap = (
        f"🌍 {name} — {specialty}\n\n"
        f"Which fact surprised you the most? Drop it in the comments! 👇\n\n"
        f"{ctag} {hashtags.get('facebook', '')}"
    )

    # TikTok: snappy hook + country tag + discovery tags.
    tt_cap = (
        f"{name} is truly incredible! 🌍\n\n"
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
    ap = argparse.ArgumentParser(description="Generate a country short script via OpenAI.")
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

    if args.index is not None:
        idx = args.index % len(countries)
        cycle = 0  # manual override has no meaningful "pass through the list" number
    elif args.slot is not None:
        # 3 videos/day: day N covers countries [3N, 3N+1, 3N+2] in the list,
        # so the full rotation completes every len(countries)/3 days with no repeats.
        day_number = (date.today() - date(2026, 7, 23)).days
        raw_index = day_number * 3 + args.slot
        idx = raw_index % len(countries)
        cycle = raw_index // len(countries)
    else:
        # Fallback: single video/day, date-based rotation
        day_number = (date.today() - date(2026, 7, 23)).days
        idx = day_number % len(countries)
        cycle = day_number // len(countries)

    country = countries[idx]
    angle_index = cycle % len(ANGLES)
    angle = ANGLES[angle_index]
    lap = cycle // len(ANGLES)  # how many full passes through the angle list so far
    subfoci = SUBFOCI[angle_index]
    subfocus = subfoci[lap % len(subfoci)]

    # Generate narration
    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if OPENAI_AVAILABLE and api_key:
        print(f"[generate_country_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} "
              f"(cycle {cycle + 1}, angle={angle!r}, subfocus={subfocus!r}) ...")
        try:
            narration, segments = _gpt_script(country, openai_cfg, angle, subfocus, cycle)
            source = "openai"
        except Exception as exc:
            print(f"[generate_country_script] OpenAI error: {exc} — using fallback script")
            narration, segments = _fallback_script(country)
            source = "fallback"
    else:
        reason = "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set"
        print(f"[generate_country_script] {reason} — using fallback script")
        narration, segments = _fallback_script(country)
        source = "fallback"

    # Precompute each segment's word count so the footage/assemble stages can time each
    # clip to how long its words take to speak (footage stays in sync with the voice).
    for seg in segments:
        seg["words"] = len(seg["text"].split())

    # Build output record (compatible with the rest of the pipeline)
    record = {
        "id": country["id"],
        "name": country["name"],
        "title": country["name"],          # pipeline compatibility alias
        "author": country["name"],         # pipeline compatibility alias
        "specialty": country["specialty"],
        "hook": country["hook_text"],
        "footage_query": country["footage_query"],
        # spoken over the globe intro so the opening isn't silent
        "intro_line": intro_line_for(country, cycle + idx),
        # geo fields drive the globe-zoom intro (scripts/generate_intro.py)
        "iso2": country.get("iso2", ""),
        "lat": country.get("lat"),
        "lon": country.get("lon"),
        "text": f"{country['name']} — {country['specialty']}",
        "narration": narration,
        "segments": segments,
        "narration_source": source,
        "country_index": idx,
        "country_total": len(countries),
        "cycle": cycle,
        "angle": angle,
        "subfocus": subfocus,
        "lap": lap,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    _write_platform_captions(country, narration, config, args.out)

    words = len(narration.split())
    print(
        f"[generate_country_script] country {idx + 1}/{len(countries)}: "
        f"{country['name']} (cycle {cycle + 1}, angle={angle!r}, subfocus={subfocus!r}, "
        f"{words} narration words, source={source})"
    )


if __name__ == "__main__":
    main()
