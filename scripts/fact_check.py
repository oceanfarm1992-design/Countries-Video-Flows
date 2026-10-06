#!/usr/bin/env python3
"""
Shared helper: a second, independent OpenAI call that reviews a generated narration
for factual accuracy before it's used, so a single hallucinated GPT-4o-mini call
doesn't ship straight to a public, unattended channel.

Used by generate_country_script.py, generate_hook_script.py, and the two
numeric series (generate_comparison_script.py, generate_rankings_script.py).
The caller decides what to do on failure (their fallback path differs) — this
module only answers the yes/no question "does this narration contain a
likely-false claim?".

Deliberately narrow: it only flags claims the checker is confident are FALSE or
fabricated (wrong number, invented event, wrong country), not stylistic looseness
or things it's merely unsure about — otherwise almost any narration would get
flagged and the checker would defeat the point of using GPT at all.

Two review modes:
  - No `reference`: general-knowledge review (country/hook series) — the
    checker judges claims against what it already knows.
  - With `reference`: grounded review (comparison/rankings) — the checker is
    given the EXACT rows the narration is supposed to cite and told to check
    against THOSE, not its own memory. This matters for anything the model's
    training data won't have precisely right (a specific year's GDP figure,
    an exact index score) — judging a live pipeline's current numbers against
    a language model's approximate recollection of "typical" values produces
    false positives on entirely correct data, discarding real GPT narration
    and silently falling back to the template every time. Confirmed live:
    the rankings series' first production run had two correct World Bank FDI
    figures (from the same day's actual fetch) rejected as "incorrect" by an
    ungrounded check.

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


def _reference_block(reference):
    return "\n".join(f"- {line}" for line in reference)


_STRICT_RULE = """
            ALSO flag any number, date, rank, record, or specific factual claim about a
            country (its history, geography, economy, policies or events) that is NOT
            stated in, or directly calculable from, the source data below -- even if it
            sounds plausible or true. General interpretive language ("a steep climb",
            "a close race", "a remarkable lead") is fine and must not be flagged.
"""


def verify_narration(narration: str, country_name: str, openai_cfg: dict, reference=None,
                     strict=False):
    """Return (passed: bool, issues: list[str]).

    `reference`, when given, is the list of display-formatted source lines
    the narration is meant to cite (the same values GPT was given) — the checker is told
    to validate numeric claims against THIS, not its own training-data
    recollection, since the latter produces false positives on correct but
    unfamiliar-to-the-model figures (see module docstring).

    `strict` (with `reference`) also flags claims the data doesn't SUPPORT, not just
    ones that contradict it: long-form narration has room to pad with invented
    context, which a contradiction-only check lets through."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not (OPENAI_AVAILABLE and api_key):
        return True, []

    if reference:
        system_prompt = textwrap.dedent(f"""
            You are a careful fact-checker reviewing a short spoken video narration
            about {country_name} for factual accuracy before it is published. You are
            NOT reviewing style, tone, grammar, or phrasing — only whether the CLAIMS
            made are true.

            You are given the EXACT source data the narration is supposed to cite
            below. This is the ground truth for this review — it may include figures
            that look surprising or differ from what you'd expect from general
            knowledge; that is expected and NOT an error, since these come from a
            live data fetch more current or more precise than your training data.

            Flag a claim ONLY if it contradicts a number, rank, tie, or country name
            that IS present in the source data below (e.g. it states a different
            value, attributes a figure to the wrong country, states a rank order
            that isn't in the data, or claims a tie that isn't marked as one — or
            vice versa). Do NOT flag a claim because the number seems unusual,
            because it differs from what you'd expect from general knowledge, or
            because you are merely unsure — your own general knowledge is NOT the
            standard here, the source data below is.

            Rounding and abbreviation are CORRECT, not errors: "$57,223" or "$57.2K"
            for $57,222.71, "84 years" for 84.1 yrs, "1.4 billion" for 1.41B, "about a
            third" for 33.6%. Flag a number only if it is still wrong after rounding.
            {_STRICT_RULE if strict else ""}
            SOURCE DATA (the only standard for this review):
            {_reference_block(reference)}

            Respond with ONLY a JSON object:
              {{"verdict": "pass" or "fail", "issues": ["<claim>: <why it contradicts the source data above>", ...]}}
            "issues" must be empty if verdict is "pass".
        """).strip()
    else:
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
            # A long narration with several issues overran 400 tokens and came back as
            # truncated JSON; strict (long-form) reviews get room to answer in full.
            max_tokens=1500 if strict else 400,
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        verdict = (data.get("verdict") or "pass").strip().lower()
        issues = [str(i) for i in (data.get("issues") or [])]
        return verdict != "fail", issues
    except Exception as exc:  # noqa: BLE001 — a checker outage should never block the pipeline
        if strict:
            # Long narration has room to invent; an unchecked one must not ship. The
            # caller falls back to its data-only template instead.
            print(f"[fact_check] verification call failed ({type(exc).__name__}: {exc}) — "
                  f"strict review, treating as FAIL")
            return False, [f"fact-check unavailable ({type(exc).__name__})"]
        print(f"[fact_check] verification call failed ({type(exc).__name__}: {exc}) — treating as pass")
        return True, []
