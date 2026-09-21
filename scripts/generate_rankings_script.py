#!/usr/bin/env python3
"""
Stage 1 (rankings variant): a 7th series, alongside country/hook/trending/
geography/worlddata/comparison -- one Top-N countdown video per statistical
metric (GDP, HDI, Democracy Index, ...), NOT one video per thematic chapter.
See config/rankings_metrics.json for the full metric registry and
scripts/fetch_rankings_stats.py for how each metric's numbers are sourced
(live World Bank API or a curated static snapshot) -- numbers are NEVER
invented, same rule every other series in this project already follows.

Rotation: one metric posts per day, walking a fixed order through the metric
registry (metrics_cfg["metrics"], index = day_number % len(metrics)) so a
re-run of the same day always regenerates the same metric. Own dedicated
1-video/day schedule (.github/workflows/daily-rankings-short.yml), separate
from both the 5-day single-country rotation and the comparison series.

Requires:
    OPENAI_API_KEY environment variable (optional -- falls back to a template
    narration built only from the fetched/curated numbers if unset or if
    GPT/fact-check both fail).

Output:
    build/script.json   { id, name, hook, narration, segments, metric,
                           ranked, series="rankings", ... }
    build/script.txt    raw narration text for TTS
    build/caption_meta.txt / caption_tiktok.txt / caption_youtube.txt / yt_title.txt

Usage:
    python scripts/generate_rankings_script.py
    python scripts/generate_rankings_script.py --metric-id hdi
"""
import argparse
import json
import os
import textwrap
from datetime import date

from captions_common import write_platform_captions
from daily_variation import day_number_for
from fact_check import verify_narration
from fetch_rankings_stats import format_value, get_ranked, load_metrics

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

EPOCH = date(2026, 7, 23)  # same pipeline epoch as daily_variation.py

# How many data-sparse metrics to skip forward past before giving up (see the
# fallback loop in main()) -- comfortably more than expected, since most
# metrics comfortably clear min_entries (see config/rankings_metrics.json).
MAX_METRIC_ATTEMPTS = 5


def pick_metric_id(metrics_cfg, day_number):
    metrics = metrics_cfg["metrics"]
    return metrics[day_number % len(metrics)]["id"]


# ---------------------------------------------------------------------------
# Fallback: template narration built ONLY from the fetched/curated numbers
# ---------------------------------------------------------------------------

RANK_PHRASES = [
    "Coming in at number {rank} is {country}, posting a {label} of {value}.",
    "Number {rank}: {country}, with a {label} of {value}.",
    "At number {rank}, {country} lands here with a {label} of {value}.",
]


def _fallback_script(metric, ranked):
    """Countdown from #N down to #1, one sentence per rank, rotating through a
    small set of phrase templates for rhythm. Unlike comparison's fallback
    (deliberately terse, since that series' cap is only 35-60s), rankings
    targets 58-78s over N+2 beats, so each rank line needs to carry more
    words on its own -- the template phrasing below, plus real country names
    and values, comfortably reaches the video-config word floor without
    padding with anything not grounded in the fetched/curated numbers."""
    n = len(ranked)
    hook = f"TOP {n} COUNTRIES BY {metric['label'].upper()}"
    segments = [{
        "text": (
            f"Today we're counting down the top {n} countries in the world "
            f"by {metric['label'].lower()}, straight from real data."
        ),
        "visual": "",
    }]
    # `ranked` is sorted best-first (ranked[0] is the true #1), so its true
    # rank is simply its 1-based position -- iterate in REVERSE to speak the
    # countdown from #N (weakest of the top N) down to #1 (the actual best).
    for i, row in enumerate(reversed(ranked)):
        rank = n - i
        val = format_value(metric["unit"], row["value"])
        phrase = RANK_PHRASES[i % len(RANK_PHRASES)]
        text = phrase.format(rank=rank, country=row["country_name"],
                              label=metric["label"], value=val)
        segments.append({"text": text, "visual": str(rank)})
    segments.append({
        "text": (
            f"That's our top {n} for {metric['label'].lower()}. Where does "
            f"your country rank? Comment below and follow for more rankings."
        ),
        "visual": "",
    })
    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# OpenAI narration -- STRICTLY limited to the supplied ranked numbers
# ---------------------------------------------------------------------------

def _gpt_script(metric, ranked, openai_cfg, min_words, max_words):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set -- cannot call GPT.")

    client = OpenAI(api_key=api_key)
    n = len(ranked)
    # Presented in countdown order (rank N first, rank 1 last) to match the
    # narration order the prompt below asks for -- ranked[] itself is sorted
    # best-first, so its true rank is (index + 1); reversed(ranked) walks it
    # from the Nth-best (weakest of the top N) to the true #1.
    rows_block = "\n".join(
        f"- Rank {n - i}: {row['country_name']} -- "
        f"{format_value(metric['unit'], row['value'])} ({row['year_or_edition']})"
        for i, row in enumerate(reversed(ranked))
    )

    system_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts narration counting down the top
        {n} countries by {metric['label']} for a country-rankings series. This
        is shown over an on-screen COUNTDOWN LEADERBOARD (not video footage),
        so the narration should read out and react to each rank as it appears
        on screen, from #{n} down to #1. Write ONLY the spoken narration -- no
        stage directions, no titles. The video runs 58-78 seconds read at a
        natural pace, so keep it tight and punchy.

        Open with a hook sentence framing this as a "{metric['label']}" top
        {n} countdown, then go through the supplied ranks ONE AT A TIME in the
        order given (rank {n} first, rank 1 last), stating each country's
        number and a brief, honest reaction. Close with a brief line, then
        speak this call-to-action naturally: "Where does your country rank?
        Comment below! Follow for more rankings."

        STRICT RULES (non-negotiable):
        - You may ONLY cite a number that is EXPLICITLY present in the ranked
          list supplied below, using the value and rank as given. NEVER
          invent, reorder, round differently, estimate, or infer a new
          statistic or rank.
        - Do not introduce any statistic, record, or claim not present below.
        - Stay strictly about the numbers supplied -- no unrelated history,
          culture, or politics.

        Tone: energetic countdown reveal, like a great "top 10" video -- not a
        dry recitation. No bullet points or headers -- pure flowing prose only.

        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70
            characters) framing this as a "{metric['label']}" ranking --
            burned onto the video as text, so it must stand alone without the
            narration.
          - "segments": an array of {n + 2} SHORT objects (one for the opening
            hook line, one per rank IN COUNTDOWN ORDER from {n} down to 1, and
            one for the closing call-to-action). Each object has:
              - "text": a short beat of the narration.
              - "visual": for a rank beat, the EXACT rank NUMBER as a string
                (e.g. "8"); empty string "" for the opening and closing beats.
        Concatenating every "text" in order must read as one smooth narration
        that starts with the hook and ends with the call-to-action above
        (both sentences, in that order). IMPORTANT: the combined narration
        must total between {min_words} and {max_words} spoken words -- count
        them; if short, add a touch more reaction to each rank rather than
        inventing new figures. Output ONLY the JSON object.
    """).strip()

    user_message = textwrap.dedent(f"""
        Ranking: Top {n} countries by {metric['label']}

        Ranks you may cite (do not use any number, country, or rank not
        present here, and do not reorder them):
        {rows_block}
    """).strip()

    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=openai_cfg.get("max_tokens", 900),
        temperature=openai_cfg.get("temperature", 0.7),
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    hook = (data.get("hook") or "").strip() or f"TOP {n} COUNTRIES BY {metric['label'].upper()}"
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip()
        if text:
            segments.append({"text": text, "visual": visual})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")

    # render_rankings_graphics.py trusts each rank beat's "visual" as a
    # direct 1-based index into `ranked` (row = ranked[current_rank - 1]) --
    # an out-of-range or duplicated rank from GPT would either crash that
    # render step outright or silently mislabel a country on screen. Neither
    # fact_check.verify_narration nor the JSON parsing above catches this
    # (they only judge factual/textual correctness), so validate the rank
    # set explicitly and raise to trigger the same retry-then-fallback ladder
    # the caller already applies to other GPT failure modes.
    rank_visuals = [s["visual"] for s in segments if s["visual"]]
    if sorted(int(v) for v in rank_visuals if v.isdigit()) != list(range(1, n + 1)) \
            or len(rank_visuals) != n:
        raise RuntimeError(
            f"GPT returned malformed rank labels {rank_visuals!r}; expected exactly "
            f"one of each of 1..{n}.")

    narration = " ".join(s["text"] for s in segments)
    return hook, narration, segments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate a country-rankings countdown short script.")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--metrics-config", default="config/rankings_metrics.json")
    ap.add_argument("--video-config", default="config/rankings_video.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--metric-id", default=None, help="Force a metric (blank = auto date rotation).")
    ap.add_argument("--min-words", type=int, default=None,
                    help="Defaults to video-config's min_seconds * words_per_second.")
    ap.add_argument("--max-words", type=int, default=None,
                    help="Defaults to video-config's max_seconds * words_per_second.")
    ap.add_argument("--variant-seed", type=int, default=None)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)
    with open(args.video_config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]
    metrics_cfg = load_metrics(args.metrics_config)

    countries = config["countries"]
    day_number = day_number_for()
    wps = video_cfg.get("words_per_second", 2.3)
    min_words = args.min_words or round(video_cfg["min_seconds"] * wps)
    max_words = args.max_words or round(video_cfg["max_seconds"] * wps)
    min_entries = metrics_cfg.get("min_entries", 7)

    metrics = metrics_cfg["metrics"]
    ids_by_metric = {m["id"]: m for m in metrics}

    if args.metric_id:
        if args.metric_id not in ids_by_metric:
            raise SystemExit(f"Unknown --metric-id {args.metric_id!r}")
        metric = ids_by_metric[args.metric_id]
        print(f"[generate_rankings_script] fetching ranking for {metric['label']} ...")
        ranked = get_ranked(metric["id"], countries, metrics_cfg=metrics_cfg)
        if len(ranked) < min_entries:
            raise SystemExit(
                f"Only {len(ranked)} countries have data for {metric['label']} "
                f"(need >= {min_entries}).")
    else:
        # A metric can be too data-sparse on a given day only if its source
        # temporarily returns less coverage than usual -- walk forward
        # through the fixed rotation (deterministic per day, same fallback
        # every time) rather than fail the day's post, same pattern
        # generate_comparison_script.py uses for data-sparse pairs.
        metric = None
        ranked = None
        for attempt in range(MAX_METRIC_ATTEMPTS):
            candidate_id = pick_metric_id(metrics_cfg, day_number + attempt)
            candidate = ids_by_metric[candidate_id]
            print(f"[generate_rankings_script] fetching ranking for {candidate['label']} ...")
            candidate_ranked = get_ranked(candidate["id"], countries, metrics_cfg=metrics_cfg)
            if len(candidate_ranked) >= min_entries:
                metric, ranked = candidate, candidate_ranked
                if attempt:
                    print(f"[generate_rankings_script] skipped {attempt} data-sparse metric(s) "
                          f"before landing on {metric['label']}")
                break
            print(f"[generate_rankings_script] only {len(candidate_ranked)} countries for "
                  f"{candidate['label']} -- trying the next scheduled metric")
        if metric is None:
            raise SystemExit(
                f"Could not find a metric with >= {min_entries} countries in "
                f"{MAX_METRIC_ATTEMPTS} attempts starting from day {day_number}.")

    openai_cfg = config.get("openai", {})
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if OPENAI_AVAILABLE and api_key:
        print(f"[generate_rankings_script] calling OpenAI {openai_cfg.get('model', 'gpt-4o-mini')} ...")
        try:
            hook, narration, segments = _gpt_script(metric, ranked, openai_cfg, min_words, max_words)
            ok, issues = verify_narration(narration, metric["label"], openai_cfg)
            if not ok:
                print(f"[generate_rankings_script] fact-check flagged: {issues} -- regenerating once")
                hook, narration, segments = _gpt_script(metric, ranked, openai_cfg, min_words, max_words)
                ok, issues = verify_narration(narration, metric["label"], openai_cfg)
                if not ok:
                    print(f"[generate_rankings_script] fact-check flagged again: {issues} -- using fallback script")
                    hook, narration, segments = _fallback_script(metric, ranked)
                    source = "fallback"
                else:
                    source = "openai"
            else:
                source = "openai"
        except Exception as exc:
            print(f"[generate_rankings_script] OpenAI error: {exc} -- using fallback script")
            hook, narration, segments = _fallback_script(metric, ranked)
            source = "fallback"
    else:
        reason = "openai library not installed" if not OPENAI_AVAILABLE else "OPENAI_API_KEY not set"
        print(f"[generate_rankings_script] {reason} -- using fallback script")
        hook, narration, segments = _fallback_script(metric, ranked)
        source = "fallback"

    for seg in segments:
        seg["words"] = len(seg["text"].split())

    seed = args.variant_seed if args.variant_seed is not None else day_number

    record = {
        "id": f"rankings_{metric['id']}_{day_number}",
        "name": f"Top {len(ranked)} by {metric['label']}",
        "title": f"Top {len(ranked)} Countries by {metric['label']}",
        "hook": hook,
        "narration": narration,
        "segments": segments,
        "narration_source": source,
        "series": "rankings",
        "metric": metric,
        "ranked": ranked,
        "day_number": day_number,
        "variant_seed": seed,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(narration)

    # write_platform_captions expects a single "country" dict -- synthesize
    # one representing the ranking so the existing caption/hashtag machinery
    # (and its per-day variation) works unmodified. "name" deliberately holds
    # just the metric label (not the full "Top N by X" sentence) because
    # _country_hashtag() in captions_common.py strips it down to a single
    # run-on hashtag (e.g. "#humandevelopmentindex") -- feeding it a whole
    # sentence would instead produce a garbled "#topbyhumandevelopmentindex".
    # hook_title already carries the "Top N Countries by X" framing for the
    # caption body (see write_platform_captions' `lead` for non-country
    # series), so nothing is lost.
    n = len(ranked)
    synthetic_country = {
        "name": metric["label"],
        "specialty": f"The world's top {n} countries by {metric['label'].lower()}",
        "facts": [
            f"#{i + 1} {row['country_name']}: {format_value(metric['unit'], row['value'])}"
            for i, row in enumerate(ranked)
        ],
    }
    write_platform_captions("rankings", synthetic_country, hook, config, args.out, seed)

    words = len(narration.split())
    print(
        f"[generate_rankings_script] {metric['label']} ({metric['source_type']}): "
        f"hook={hook!r}, {len(ranked)} ranks, {words} narration words, source={source}"
    )


if __name__ == "__main__":
    main()
