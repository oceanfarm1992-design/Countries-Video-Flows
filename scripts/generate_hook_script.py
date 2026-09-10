#!/usr/bin/env python3
"""
Stage 1 (hook variant): a second, independent series alongside
generate_country_script.py, on the same channels. Where that script writes a
documentary-style "here's what makes X special" narration, this one writes a
punchier, curiosity-driven "hook" narration (a secret place, a seasonal
transformation, a border oddity, a myth to bust, ...). Deliberately excludes
any hook that names another country as inferior/losing — see HOOK_ANGLES.

Every downstream stage (fetch_footage.py, generate_tts.py,
generate_captions.py, generate_intro.py, assemble_video.py, post_buffer.py,
post_youtube.py) consumes build/script.json + the caption files purely by
key/filename, so this writes the exact same shape generate_country_script.py
does and the rest of the pipeline runs completely unmodified.

Rotation: same day+slot math as generate_country_script.py, offset by half
the country list so the two series don't cover the same country on the same
day/slot. Angle cycles through HOOK_ANGLES by "cycle" (how many full passes
through the country list have happened), same mechanism as that script's
ANGLES rotation.

Requires:
    OPENAI_API_KEY environment variable (GitHub Secret: OPENAI_API_KEY)

Output:
    build/script.json   { id, name, text, hook, narration, footage_query, ... }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_hook_script.py
    python scripts/generate_hook_script.py --index 5
    python scripts/generate_hook_script.py --config config/countries.json --out build
"""
import argparse
import json
import os
import re
import textwrap
from datetime import date

from generate_country_script import intro_line_for, _country_hashtag

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

# Curiosity-gap angles, plus one "diplomatic pride" angle (world_best) that
# praises the country on its own merits WITHOUT naming another country as a
# loser. Deliberately excludes rivalry-style hooks ("why nobody likes X",
# "X beats Y at everything") — those need a human reviewing every line before
# it posts, and this pipeline has none. See the STRICT RULES in
# _gpt_system_prompt for how this is enforced on the model itself.
HOOK_ANGLES = [
    {
        "id": "secret_place",
        "desc": "a real, verifiable secret, hidden, or little-known place in {name} "
                "that most outsiders have never heard of",
        "fallback_hook": "THE HIDDEN PLACE IN {NAME} NO MAP SHOWS",
    },
    {
        "id": "seasonal_transform",
        "desc": "a dramatic seasonal or cyclical transformation in {name} that changes "
                "how it looks or functions for part of the year",
        "fallback_hook": "WHY {NAME} COMPLETELY TRANSFORMS EVERY YEAR",
    },
    {
        "id": "geo_political_quirk",
        "desc": "an unusual geographic or political quirk in {name} — two capitals, "
                "an exclave, a strange border, an unusual time zone or calendar — "
                "that surprises people",
        "fallback_hook": "THE STRANGE QUIRK MOST PEOPLE DON'T KNOW ABOUT {NAME}",
    },
    {
        "id": "hidden_reason",
        "desc": "the real, verifiable reason behind something {name} is known for "
                "that most people get wrong",
        "fallback_hook": "THE REAL STORY BEHIND {NAME}'S BIGGEST CLAIM TO FAME",
    },
    {
        "id": "border_reality",
        "desc": "what actually happens when you travel to or cross into {name} — "
                "visa rules, checkpoints, or unusual travel realities",
        "fallback_hook": "WHAT REALLY HAPPENS WHEN YOU CROSS INTO {NAME}",
    },
    {
        "id": "myth_bust",
        "desc": "the single biggest misconception outsiders have about {name}",
        "fallback_hook": "THE BIGGEST MISCONCEPTION ABOUT {NAME}",
    },
    {
        "id": "world_best",
        "desc": "the one specific thing {name} does better than anywhere else on "
                "Earth, backed by a real, verifiable record or achievement",
        "fallback_hook": "THE ONE THING {NAME} DOES BETTER THAN ANYWHERE ON EARTH",
    },
]


# ---------------------------------------------------------------------------
# Fallback: build a simple hook narration without GPT (used when no API key)
# ---------------------------------------------------------------------------

def _fallback_script(country: dict, angle: dict):
    """No-GPT fallback: a templated hook opener + the curated facts. Mirrors
    generate_country_script.py's _fallback_script so the pipeline never hard-fails
    when OPENAI_API_KEY is temporarily unavailable."""
    name = country["name"]
    specialty = country["specialty"]
    facts = country.get("facts", [])
    base_visual = country.get("footage_query", f"{name} landscape cinematic")

    hook = angle["fallback_hook"].format(NAME=name.upper())
    opener = f"Here's something about {name} most people don't know: {specialty}."

    segments = [{"text": opener, "visual": base_visual}]
    for fact in facts:
        segments.append({"text": fact + ".", "visual": base_visual})
    segments.append({
        "text": f"That's {name} for you. "
                f"If you're from {name}, comment 'I love my country' below! "
                "Follow for more stories from around the world.",
        "visual": base_visual,
    })
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# OpenAI narration
# ---------------------------------------------------------------------------

def _gpt_script(country: dict, openai_cfg: dict, angle: dict):
    """Return (hook, narration, segments) — same segments contract as
    generate_country_script.py's _gpt_script (each {"text", "visual"})."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot call GPT.")

    client = OpenAI(api_key=api_key)
    name = country["name"]
    facts_block = "\n".join(f"- {f}" for f in country.get("facts", []))
    angle_desc = angle["desc"].format(name=name)

    base_prompt = textwrap.dedent(f"""
        You are writing a short, punchy YouTube Shorts narration about {name} for a
        "hook" style series built on curiosity and gentle national pride — never on
        comparing or criticizing another country. Write ONLY the spoken narration — no
        stage directions, no titles. The video runs 45-70 seconds read at a natural pace.

        This episode's angle: {angle_desc}.

        Open with an attention-grabbing hook sentence about this specific angle — the
        kind of line that makes someone stop scrolling — then deliver 3-5 genuinely
        interesting, VERIFIABLE facts that support it (stay accurate, never invent
        statistics or events). Close with a brief line tying it back to why {name} is
        worth knowing, then speak this call-to-action naturally: "If you're from {name},
        comment 'I love my country' below! Follow for more stories from around the
        world."

        STRICT RULES (non-negotiable):
        - Never name or reference another country as worse, losing, or inferior — no
          comparison framed against a named country, ever.
        - Never generalize or stereotype the people of {name} or any other country —
          stay focused on places, history, records, and verifiable facts.
        - Never invent statistics, records, or events.

        Tone: energetic and curious, like a great short-form creator sharing something
        genuinely surprising — not a rivalry or "we're better" tone. No bullet points or
        headers — pure flowing prose only.
    """).strip()

    system_prompt = base_prompt + " " + textwrap.dedent("""
        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70 characters)
            capturing this episode's angle — this is burned onto the video as text, so
            it must stand alone without the narration.
          - "segments": an array of 12 to 16 SHORT objects (short beats — one sentence,
            or even half a sentence, each — so the video can cut to fresh imagery often
            and stay punchy). Each object has:
              - "text": a short beat of the narration (what the voiceover reads here).
              - "visual": a short English stock-media search query (2-5 words)
                describing ONE concrete, filmable subject that MATCHES this beat, so a
                clip or photo of exactly that can be shown while these words are
                spoken. Name the specific subject — a named place, landmark, animal, or
                activity — not an abstract idea.
        Concatenating every "text" in order must read as one smooth narration that
        starts with the hook and ends with these two calls-to-action, in this order:
        first something like "If you're from {name}, comment 'I love my country'
        below!", then "Follow for more stories from around the world." Both are
        REQUIRED — do not drop the "I love my country" comment prompt even though it
        comes before the "Follow for more" line. IMPORTANT: the combined narration must
        total between 160 and 185 spoken words — count them and do not go under 160; if
        you are short, add more short beats with specific, accurate detail rather than
        padding. Output ONLY the JSON object.
    """.format(name=name)).strip()

    user_message = textwrap.dedent(f"""
        Country: {name}
        Specialty: {country['specialty']}

        Background facts (use only if genuinely relevant to this episode's angle —
        prefer other accurate, verifiable facts about {name} that fit the angle better):
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
    hook = (data.get("hook") or "").strip() or angle["fallback_hook"].format(NAME=name.upper())
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
    """Same structure as generate_country_script.py's _write_platform_captions, but
    the YouTube title comes from THIS episode's generated hook (the whole point of
    the format) rather than the country's static curated tagline."""
    hashtags = config.get("hashtags", {})
    name = country["name"]
    facts = country.get("facts", [])
    ctag = _country_hashtag(name)
    first_fact = facts[0] if facts else country["specialty"]

    # Title-case the ALL-CAPS hook for a readable YouTube title (same apostrophe fix
    # as generate_country_script.py: str.title() capitalises the letter after an
    # apostrophe, e.g. "World'S" — put it back to lower).
    hook_title = re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), hook.title()).rstrip(".")
    max_hook = 91  # reserve 8 chars for " #Shorts" suffix; hard limit 100
    if len(hook_title) > max_hook:
        hook_title = hook_title[:max_hook].rsplit(" ", 1)[0] + "…"
    yt_title = f"{hook_title} #Shorts"

    yt_desc = (
        f"{name} 🌍 {hook_title}\n\n"
        f"Did you know? {first_fact}\n\n"
        "Subscribe for more hidden stories from around the world! 🌏\n\n"
        f"{ctag} {hashtags.get('youtube', '')}"
    )

    fb_cap = (
        f"🌍 {hook_title}\n\n"
        f"Did you know this about {name}? Drop a comment below! 👇\n\n"
        f"{ctag} {hashtags.get('facebook', '')}"
    )

    tt_cap = (
        f"{hook_title} 👀\n\n"
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
    ap = argparse.ArgumentParser(description="Generate a hook-style country short script via OpenAI.")
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

    # Offset by half the list so this series covers a different country than
    # generate_country_script.py on the same day/slot (same rotation formula there).
    offset = len(countries) // 2

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
    angle = HOOK_ANGLES[cycle % len(HOOK_ANGLES)]

    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if OPENAI_AVAILABLE and api_key:
        print(f"[generate_hook_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} "
              f"(angle={angle['id']!r}) ...")
        try:
            hook, narration, segments = _gpt_script(country, openai_cfg, angle)
            source = "openai"
        except Exception as exc:
            print(f"[generate_hook_script] OpenAI error: {exc} — using fallback script")
            hook, narration, segments = _fallback_script(country, angle)
            source = "fallback"
    else:
        reason = "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set"
        print(f"[generate_hook_script] {reason} — using fallback script")
        hook, narration, segments = _fallback_script(country, angle)
        source = "fallback"

    for seg in segments:
        seg["words"] = len(seg["text"].split())

    record = {
        "id": country["id"],
        "name": country["name"],
        "title": country["name"],
        "author": country["name"],
        "specialty": country["specialty"],
        "hook": hook,
        "footage_query": country["footage_query"],
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
        "series": "hook",
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    _write_platform_captions(country, hook, config, args.out)

    words = len(narration.split())
    print(
        f"[generate_hook_script] country {idx + 1}/{len(countries)}: "
        f"{country['name']} (angle={angle['id']!r}, hook={hook!r}, "
        f"{words} narration words, source={source})"
    )


if __name__ == "__main__":
    main()
