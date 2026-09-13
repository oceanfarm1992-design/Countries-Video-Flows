#!/usr/bin/env python3
"""
Shared helper: a second, independent OpenAI call that reviews a generated narration
for factual accuracy before it's used, so a single hallucinated GPT-4o-mini call
doesn't ship straight to a public, unattended channel.

Used by both generate_country_script.py and generate_hook_script.py. The caller
decides what to do on failure (their fallback path differs) — this module only
answers the yes/no question "does this narration contain a likely-false claim?".

Deliberately narrow: it only flags claims the checker is confident are FALSE or
fabricated (wrong number, invented event, wrong country), not stylistic looseness
or things it's merely unsure about — otherwise almost any narration would get
flagged and the checker would defeat the point of using GPT at all.

If the check call itself fails (network error, no API key), that is NOT treated
as a failed check — it returns (True, []) so a checker outage never blocks the
pipeline. Only a genuine "fail" verdict from a completed review counts.
"""
import json
import os
import textwrap

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass


def verify_narration(narration: str, country_name: str, openai_cfg: dict):
    """Return (passed: bool, issues: list[str])."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not (OPENAI_AVAILABLE and api_key):
        return True, []

    system_prompt = textwrap.dedent(f"""
        You are a careful fact-checker reviewing a short spoken video narration about
        {country_name} for factual accuracy before it is published. You are NOT
        reviewing style, tone, grammar, or phrasing — only whether the CLAIMS made
        are true.

        Flag a claim only if you are confident it is FALSE, fabricated, or describes
        a statistic, record, date, or event that does not exist or is materially
        wrong (wrong country, wrong number, an invented "fact"). Do NOT flag a claim
        just because it is imprecise, a reasonable simplification, or something you
        are merely unsure about — only flag things you have good reason to believe
        are actually wrong.

        Respond with ONLY a JSON object:
          {{"verdict": "pass" or "fail", "issues": ["<claim>: <why it's wrong>", ...]}}
        "issues" must be empty if verdict is "pass".
    """).strip()

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=openai_cfg.get("fact_check_model", openai_cfg.get("model", "gpt-4o-mini")),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": narration},
            ],
            max_tokens=400,
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        verdict = (data.get("verdict") or "pass").strip().lower()
        issues = [str(i) for i in (data.get("issues") or [])]
        return verdict != "fail", issues
    except Exception as exc:  # noqa: BLE001 — a checker outage should never block the pipeline
        print(f"[fact_check] verification call failed ({type(exc).__name__}: {exc}) — treating as pass")
        return True, []
