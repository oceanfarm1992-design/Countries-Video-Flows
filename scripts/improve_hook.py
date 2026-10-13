#!/usr/bin/env python3
"""
Best-of-several opening hook for the single-country Shorts. The hook is the line on
screen in the video's first second (hook_card.py) and the YouTube title, so it decides
whether a viewer keeps watching.

  1. GPT writes 3 alternative hooks from the finished narration -- curiosity-led
     (a question or a surprising angle), short, no clickbait lies
  2. every candidate must stay inside the narration's facts: a number that isn't in
     the narration, another country's name, or an over-long line disqualifies it
  3. a second, separate GPT call judges the original + the valid alternatives and
     picks the one most likely to stop a scroll while staying accurate

The original hook is kept on any failure, so this can only ever help.

Writes build/script.json (hook, hook_original) and build/yt_title.txt.
"""
import argparse
import json
import os
import re

from captions_common import _title_from_hook

MAX_CHARS = 60
OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass


def _ask(client, model, system, user, max_tokens=300, temperature=0.9):
    r = client.chat.completions.create(
        model=model, max_tokens=max_tokens, temperature=temperature,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return json.loads(r.choices[0].message.content)


def valid(hook, narration, country, other_countries):
    """A candidate may only use what the narration says."""
    if not hook or len(hook) > MAX_CHARS:
        return False
    narr_digits = set(re.findall(r"\d[\d,.]*", narration))
    if any(n not in narr_digits for n in re.findall(r"\d[\d,.]*", hook)):
        return False
    low = hook.lower()
    return not any(o.lower() in low for o in other_countries if o.lower() not in country.lower())


def best_hook(script, countries, model):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    country, narration, original = script.get("name", ""), script["narration"], script.get("hook", "")
    others = [c["name"] for c in countries if c["name"] != country and len(c["name"]) > 3]
    gen = _ask(client, model,
               "You write the on-screen opening line of YouTube Shorts about countries. The line "
               "appears in the first second and must make a scrolling viewer stop. Use curiosity: a "
               "question, a surprising contrast, or an intriguing claim -- but ONLY facts stated in the "
               "narration you are given, no new numbers, no exaggeration, nothing misleading. "
               f"Max {MAX_CHARS} characters. ALL CAPS. Respond as JSON: {{\"hooks\": [3 strings]}}.",
               f"Country: {country}\nNarration: {narration}")
    candidates = [original] + [h.strip().upper() for h in gen.get("hooks", []) if isinstance(h, str)]
    candidates = [h for i, h in enumerate(candidates) if i == 0 or valid(h, narration, country, others)]
    if len(candidates) == 1:
        return original, "no valid alternatives"
    listing = "\n".join(f"{i}. {h}" for i, h in enumerate(candidates))
    pick = _ask(client, model,
                "You are a YouTube Shorts editor choosing the opening on-screen line. Pick the line most "
                "likely to stop a viewer scrolling past, that is also fully supported by the narration "
                "(reject anything misleading). Respond as JSON: {\"best\": <index>, \"why\": \"...\"}.",
                f"Narration: {narration}\n\nCandidates:\n{listing}", max_tokens=150, temperature=0)
    idx = pick.get("best")
    if not isinstance(idx, int) or not 0 <= idx < len(candidates):
        return original, "judge gave no valid pick"
    return candidates[idx], pick.get("why", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build")
    args = ap.parse_args()
    if not (OPENAI_AVAILABLE and os.environ.get("OPENAI_API_KEY", "").strip()):
        print("[improve_hook] no OpenAI -- keeping the original hook")
        return
    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    original = script.get("hook", "")
    try:
        hook, why = best_hook(script, cfg["countries"], cfg.get("openai", {}).get("model", "gpt-4o-mini"))
    except Exception as exc:  # noqa: BLE001 -- the original hook is always a safe answer
        print(f"[improve_hook] kept the original hook ({type(exc).__name__}: {exc})")
        return
    if hook == original:
        print(f"[improve_hook] original hook kept: {original!r} ({why})")
        return
    script["hook_original"], script["hook"] = original, hook
    with open(args.script, "w", encoding="utf-8") as fh:
        json.dump(script, fh, indent=2, ensure_ascii=False)
    title = _title_from_hook(hook)
    with open(os.path.join(args.out, "yt_title.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{title} #Shorts")
    print(f"[improve_hook] {original!r} -> {hook!r} ({why})")


if __name__ == "__main__":
    main()
