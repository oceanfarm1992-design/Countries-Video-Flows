#!/usr/bin/env python3
"""
Stage 1 of the India channel's "Untold India" Short: pick a real, lesser-known place from
config/india_places.json and write a hype narration that opens "This is not <somewhere
else>. This is India." over drone footage of the place.

Facts: the narration may use ONLY the place's curated facts (each one checked against
Wikipedia by scripts/verify_india_facts.py). The AI writes the wording, a grounded
fact-check compares it with those facts, and a template built from the facts alone is
the fallback -- so nothing unverified is ever said on air.

Output (same shape as the other series, so footage / voice / captions / assembly just work):
    build/script.json, build/script.txt, build/caption_*.txt, build/yt_title.txt

Usage:
    python scripts/generate_india_untold_script.py [--place gurez] [--index 3]
"""
import argparse
import json
import os
import textwrap
from datetime import date

import ai_client
from captions_common import write_platform_captions
from fact_check import verify_narration

EPOCH = date(2026, 11, 15)  # the India channel's day 0
CTA = "Follow for the India you never knew."


def pick_place(places, day_number, index=None, place_id=None):
    """(position, place). Forced by id or index, else one place per day through the list."""
    if place_id:
        for i, p in enumerate(places):
            if p["id"] == place_id:
                return i, p
        raise SystemExit(f"unknown place id {place_id!r}")
    i = (index if index is not None else day_number) % len(places)
    return i, places[i]


def hook_text(place):
    """The first-second card: the 'not <elsewhere>' twist."""
    return f"THIS IS NOT {place['compare'].upper()}. THIS IS INDIA."


def opening_line(place):
    return f"This is not {place['compare']}. This is India."


def title_hook(place):
    """Goes into the YouTube title, so it names the place (the card itself cannot)."""
    return f"This is not {place['compare']} - this is {place['name']}, India"


def _visuals(place):
    """Search queries to cycle through: the place's own drone queries first."""
    return [*place["footage"], f"{place['name']} landscape", f"{place['region']} India aerial"]


def fallback_script(place):
    """(narration, segments) from the verified facts only."""
    visuals = _visuals(place)
    lines = [
        opening_line(place),
        f"This is {place['name']}, in {place['region']}.",
        *[f"{f['text']}." for f in place["facts"]],
        f"Many would guess {place['compare']}. They would be wrong.",
        "Imagine standing right here.",
        CTA,
    ]
    segments = [{"text": t, "visual": visuals[i % len(visuals)]} for i, t in enumerate(lines)]
    return " ".join(s["text"] for s in segments), segments


def _gpt_script(place, openai_cfg, min_words, max_words):
    client = ai_client.client()
    facts = "\n".join(f"- {f['text']}" for f in place["facts"])
    system_prompt = textwrap.dedent(f"""
        You write the narration of a 30-45 second vertical video for an India travel
        channel. The tone is awe, pride and hype -- always positive. The pictures are real
        drone footage of {place['name']} ({place['region']}, India).

        Return a JSON object with one key, "segments": 9 to 12 SHORT beats (one sentence or
        less each). Each beat has "text" (what the voice says) and "visual" (a 2-5 word
        stock-footage search, e.g. "{place['name']} drone aerial"; always name the place,
        its region or India).

        Rules, all strict:
        - Beat 1 text is exactly: "{opening_line(place)}"
        - Beat 2 names the place and its region.
        - Use ONLY the facts given below for any number, date, record or claim. Never add
          another figure, ranking, date or history. You may add feeling and awe, not facts.
        - Say numbers the way they are given (e.g. "2,400 metres"); never use symbols like
          $, %, ~, or abbreviations.
        - The last beat is exactly: "{CTA}"
        - Total spoken length {min_words} to {max_words} words. Output ONLY the JSON.
    """).strip()
    user_message = f"Place: {place['name']}, {place['region']}\nFacts:\n{facts}"
    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_message}],
        max_tokens=openai_cfg.get("max_tokens", 700),
        temperature=openai_cfg.get("temperature", 0.8),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    fallback_visual = _visuals(place)[0]
    segments = [{"text": (s.get("text") or "").strip(),
                 "visual": (s.get("visual") or "").strip() or fallback_visual}
                for s in data.get("segments", []) if (s.get("text") or "").strip()]
    if not segments:
        raise RuntimeError("the AI returned no usable beats")
    return " ".join(s["text"] for s in segments), segments


def build_script(place, openai_cfg, min_words, max_words):
    """(narration, segments, source). AI wording checked against the facts, else the template."""
    if not ai_client.available():
        return (*fallback_script(place), "fallback")
    reference = [f["text"] for f in place["facts"]] + [f"{place['name']} is in {place['region']}, India"]
    try:
        for attempt in (1, 2):
            narration, segments = _gpt_script(place, openai_cfg, min_words, max_words)
            ok, issues = verify_narration(narration, place["name"], openai_cfg,
                                          reference=reference, strict=True)
            if ok:
                return narration, segments, "openai"
            print(f"[india_untold] fact-check flagged (try {attempt}): {issues}")
    except Exception as exc:  # noqa: BLE001 -- any AI trouble falls back to the template
        print(f"[india_untold] AI error: {type(exc).__name__}: {exc}")
    return (*fallback_script(place), "fallback")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="config/india.json")
    ap.add_argument("--places", default="config/india_places.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--place", default=None, help="force a place id")
    ap.add_argument("--index", type=int, default=None, help="force a 0-based place index")
    ap.add_argument("--skip", type=int, default=0,
                    help="move N places past today's (when today's has too little confirmed footage)")
    ap.add_argument("--variant-seed", type=int, default=None)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)
    with open(args.places, encoding="utf-8") as fh:
        places = json.load(fh)["places"]

    day_number = (date.today() - EPOCH).days
    idx, place = pick_place(places, day_number + args.skip, args.index, args.place)
    video = config.get("video", {})
    narration, segments, source = build_script(
        place, config.get("openai", {}), video.get("min_words", 80), video.get("max_words", 115))
    for seg in segments:
        seg["words"] = len(seg["text"].split())

    record = {
        "id": place["id"], "name": place["name"], "title": place["name"], "author": place["name"],
        "series": "india_untold",
        "specialty": f"{place['region']}, India",
        "hook": hook_text(place),
        "footage_query": place["footage"][0],
        "match_terms": place["match"],
        "iso2": "IN", "lat": place["lat"], "lon": place["lon"],
        "text": f"{place['name']} - {place['region']}",
        "narration": narration, "segments": segments, "narration_source": source,
        "place_index": idx, "place_total": len(places),
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    seed = args.variant_seed if args.variant_seed is not None else day_number
    caption_subject = {"name": place["name"], "specialty": f"{place['region']}, India",
                       "facts": [f["text"] for f in place["facts"]]}
    write_platform_captions("india_untold", caption_subject, title_hook(place),
                            config, args.out, seed)
    print(f"[india_untold] place {idx + 1}/{len(places)}: {place['name']} "
          f"({len(narration.split())} words, source={source})")


if __name__ == "__main__":
    main()
