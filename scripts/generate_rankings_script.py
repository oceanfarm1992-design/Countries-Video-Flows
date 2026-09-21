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

from captions_common import write_platform_captions
from daily_variation import day_number_for
from fact_check import verify_narration
from fetch_rankings_stats import format_value, get_ranked, load_metrics, year_range_label

# How many data-sparse metrics to skip forward past before giving up (see the
# fallback loop in main()) -- comfortably more than expected, since most
# metrics comfortably clear min_entries (see config/rankings_metrics.json).
MAX_METRIC_ATTEMPTS = 5

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass


def pick_metric_id(metrics_cfg, day_number):
    metrics = metrics_cfg["metrics"]
    return metrics[day_number % len(metrics)]["id"]


def title_name(metric):
    """A standalone noun phrase naming this ranking -- for the hook/title/
    hashtag ONLY. Distinct from metric['label'] (the raw index name, e.g.
    "Corruption Perceptions Index") for metrics whose literal index name
    reads backwards as a title otherwise (e.g. a CPI countdown with the
    cleanest country at #1, titled literally, reads as "most corrupt"). See
    config/rankings_metrics.json's _comment.

    NOT for mid-sentence grammar ("a {label} of {value}", "by {label}") --
    display_label phrases like "Least Corrupt Countries" or "Strongest
    Militaries" are written to stand alone as a title, and don't fit that
    slot ("a Least Corrupt Countries of 78/100" is not a sentence). Use
    metric['label'] there instead, same as before display_label existed."""
    return metric.get("display_label", metric["label"])


def title_phrase(metric, n):
    """The phrase that follows "TOP {n}" in the hook/title. Defaults to
    "COUNTRIES BY {raw label}" (existing behavior for every metric without an
    override); a metric with display_label supplies its own complete phrase
    instead (see title_name)."""
    if "display_label" in metric:
        return title_name(metric).upper()
    return f"COUNTRIES BY {metric['label'].upper()}"


# ---------------------------------------------------------------------------
# Fallback: template narration built ONLY from the fetched/curated numbers
# ---------------------------------------------------------------------------

RANK_PHRASES = [
    "Coming in at number {rank} is {country}, posting a {label} of {value}.",
    "Number {rank}: {country}, with a {label} of {value}.",
    "At number {rank}, {country} lands here with a {label} of {value}.",
]
TIED_RANK_PHRASES = [
    "Tied at number {rank} is {country}, with a {label} of {value}.",
    "Also at number {rank}: {country}, with a {label} of {value}.",
]


def _fallback_script(metric, ranked):
    """Countdown from the weakest of the top N down to #1, one sentence per
    row, rotating through a small set of phrase templates for rhythm. Ties
    are spoken as ties (never invented into a false ordering -- see
    fetch_rankings_stats._assign_competition_ranks). Unlike comparison's
    fallback (deliberately terse, since that series' cap is only 35-60s),
    rankings targets 58-78s over N+2 beats, so each row needs to carry more
    words on its own -- the template phrasing below, plus real country names
    and values, comfortably reaches the video-config word floor without
    padding with anything not grounded in the fetched/curated numbers."""
    n = len(ranked)
    # Mid-sentence grammar always uses the raw index name (metric['label'])
    # -- title_phrase's display_label overrides are written to stand alone
    # as a title and don't fit "a ___ of {value}" or "by ___" (see
    # title_name's docstring).
    raw_label = metric["label"]
    hook = f"TOP {n} {title_phrase(metric, n)}"
    intro_text = (
        f"Today we're counting down the top {n} countries in the world "
        f"by {raw_label.lower()}, straight from real data."
    )
    if metric.get("note"):
        intro_text += f" One note before we start: {metric['note']}."
    segments = [{"text": intro_text, "visual": ""}]

    # `ranked` is sorted best-first -- speak it in REVERSE so the countdown
    # goes from the weakest of the top N down to the true #1.
    for row in reversed(ranked):
        val = format_value(metric["unit"], row["value"])
        phrases = TIED_RANK_PHRASES if row["tied"] else RANK_PHRASES
        phrase = phrases[hash(row["iso2"]) % len(phrases)]
        text = phrase.format(rank=row["rank"], country=row["country_name"],
                              label=raw_label, value=val)
        if row.get("more_tied_beyond"):
            # Showing only top_n of a wider tie without saying so would
            # itself misrepresent how many countries share this value (see
            # get_ranked's docstring) -- this is the only row that carries
            # the count, so it's spoken exactly once, right where the tie
            # first comes up in the countdown.
            count = row["more_tied_beyond"]
            plural = "country" if count == 1 else "countries"
            text += f" {count} more {plural} also tie at this exact value, just outside our top {n}."
        segments.append({"text": text, "visual": row["iso2"]})

    segments.append({
        "text": (
            f"That's our top {n} for {raw_label.lower()}. Where does "
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
    # `label` (raw index name) is used for mid-sentence grammar ("counting
    # down ... by {label}"); `title` (display_label override when present)
    # is used only where the prompt asks GPT to produce a standalone title-
    # like line -- see title_name's docstring for why these must stay
    # separate ("a Least Corrupt Countries of 78/100" is not a sentence).
    label = metric["label"]
    title = title_name(metric)
    # Presented in countdown order (weakest of the top N first, true #1 last)
    # to match the narration order the prompt below asks for. "visual" for
    # each rank beat must be the country's ISO2 code -- rank numbers alone
    # aren't a unique identifier when the source publishes a tie.
    rows_block = "\n".join(
        f"- ISO2 {row['iso2']}, Rank {row['rank']}{' (TIED)' if row['tied'] else ''}: "
        f"{row['country_name']} -- {format_value(metric['unit'], row['value'])} "
        f"({row['year_or_edition']})"
        + (f" [NOTE: {row['more_tied_beyond']} more countries also share this exact value, "
           f"just outside the top {n} -- mention this once, in this row's beat]"
           if row.get("more_tied_beyond") else "")
        for row in reversed(ranked)
    )
    tone = metric.get("tone", "energetic")
    tone_line = (
        "Tone: energetic countdown reveal, like a great \"top 10\" video -- not a "
        "dry recitation."
        if tone == "energetic" else
        "Tone: clear and matter-of-fact -- this metric is not a \"win\", so avoid "
        "celebratory framing (no \"congrats\", no \"amazing\") even though it's "
        "still an engaging countdown."
    )
    note_line = f"\nIMPORTANT CONTEXT: {metric['note']}. Work this into your opening line " \
                f"in your own words, since without it the ranking can be misread.\n" \
                if metric.get("note") else ""

    system_prompt = textwrap.dedent(f"""
        You are writing a short YouTube Shorts narration counting down the top
        {n} countries by {label} for a country-rankings series. This is shown
        over an on-screen COUNTDOWN LEADERBOARD (not video footage), so the
        narration should read out and react to each row as it appears on
        screen. Write ONLY the spoken narration -- no stage directions, no
        titles. The video runs 58-78 seconds read at a natural pace, so keep
        it tight and punchy.
        {note_line}
        Open with a hook sentence framing this as a "{title}" top {n}
        countdown, then go through the supplied rows ONE AT A TIME in the
        order given, stating each country's rank and a brief, honest
        reaction. If a row is marked (TIED), say so explicitly ("tied at
        number X") rather than implying it beat or lost to its neighbor.
        Close with a brief line, then speak this call-to-action naturally:
        "Where does your country rank? Comment below! Follow for more
        rankings."

        STRICT RULES (non-negotiable):
        - You may ONLY cite a number, rank, or tie status that is EXPLICITLY
          present in the list supplied below, exactly as given. NEVER invent,
          reorder, round differently, estimate, or infer a new statistic,
          rank, or ordering between two tied countries.
        - Do not introduce any statistic, record, or claim not present below.
        - Stay strictly about the numbers supplied -- no unrelated history,
          culture, or politics.

        {tone_line} No bullet points or headers -- pure flowing prose only.

        Return your answer as a JSON object with two keys:
          - "hook": a short, punchy, ALL-CAPS title-card line (under 70
            characters) framing this as a "{title}" ranking -- burned onto
            the video as text, so it must stand alone without the narration.
          - "segments": an array of {n + 2} SHORT objects: one for the
            opening hook line, one per row IN THE ORDER SUPPLIED, and one for
            the closing call-to-action. Each object has:
              - "text": a short beat of the narration.
              - "visual": for a row beat, the EXACT ISO2 code from that row
                (e.g. "US"); empty string "" for the opening and closing
                beats ONLY -- every row must get exactly one beat and no
                other beat may carry an ISO2 code.
        Concatenating every "text" in order must read as one smooth narration
        that starts with the hook and ends with the call-to-action above
        (both sentences, in that order). IMPORTANT: the combined narration
        must total between {min_words} and {max_words} spoken words -- count
        them; if short, add a touch more reaction to each row rather than
        inventing new figures. Output ONLY the JSON object.
    """).strip()

    user_message = textwrap.dedent(f"""
        Ranking: Top {n} countries by {label}

        Rows you may cite (do not use any number, country, rank, or tie
        status not present here, and do not reorder them):
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
    hook = (data.get("hook") or "").strip() or f"TOP {n} {title_phrase(metric, n)}"
    segments = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        visual = (seg.get("visual") or "").strip().upper()
        if text:
            segments.append({"text": text, "visual": visual})
    if not segments:
        raise RuntimeError("GPT returned no usable segments.")

    # render_rankings_graphics.py trusts each row beat's "visual" to identify
    # exactly one row in `ranked`, and trusts the FIRST and LAST segments to
    # be the intro/outro (mode is inferred from segment position, not just
    # content -- see that script's main()). Neither fact_check.verify_narration
    # nor the JSON parsing above catches a malformed shape here (they only
    # judge factual/textual correctness), so validate explicitly and raise to
    # trigger the same retry-then-fallback ladder the caller already applies
    # to other GPT failure modes. A prior version of this check only verified
    # the multiset of rank NUMBERS matched 1..n, which (a) breaks once ranks
    # can repeat for ties and (b) didn't catch GPT inserting an extra non-row
    # beat mid-video, which silently reveals the full leaderboard early and
    # spoils the rest of the countdown.
    expected_isos = {row["iso2"] for row in ranked}
    row_visuals = [s["visual"] for s in segments if s["visual"]]
    ok = (
        len(segments) == n + 2
        and segments[0]["visual"] == ""
        and segments[-1]["visual"] == ""
        and sorted(row_visuals) == sorted(expected_isos)
        and len(row_visuals) == len(set(row_visuals))
    )
    if not ok:
        raise RuntimeError(
            f"GPT returned a malformed segment shape (expected {n + 2} segments, "
            f"first/last with no ISO2, and exactly one beat per row): "
            f"got {len(segments)} segments, visuals={[s['visual'] for s in segments]!r}")

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
        # A metric can be too data-sparse on a given day -- its live source
        # temporarily returns less coverage than usual, or (now) the max-age
        # filter in get_ranked() drops enough stale readings to fall short --
        # walk forward through the fixed rotation (deterministic per day,
        # same fallback every time) rather than fail the day's post, same
        # pattern generate_comparison_script.py uses for data-sparse pairs.
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
            ok, issues = verify_narration(narration, metric["label"], openai_cfg, reference=ranked)
            if not ok:
                print(f"[generate_rankings_script] fact-check flagged: {issues} -- regenerating once")
                hook, narration, segments = _gpt_script(metric, ranked, openai_cfg, min_words, max_words)
                ok, issues = verify_narration(narration, metric["label"], openai_cfg, reference=ranked)
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
    # Same title-vs-grammar split as the hook (see title_name's docstring):
    # a display_label override already reads as a complete title on its own
    # ("Top 8 Least Corrupt Countries"), so it replaces "Countries by {label}"
    # wholesale rather than being spliced into that phrase.
    n = len(ranked)
    title_suffix = title_name(metric) if "display_label" in metric else f"Countries by {metric['label']}"

    record = {
        "id": f"rankings_{metric['id']}_{day_number}",
        "name": f"Top {n} {title_suffix}",
        "title": f"Top {n} {title_suffix}",
        "hook": hook,
        "narration": narration,
        "segments": segments,
        "narration_source": source,
        "series": "rankings",
        "metric": metric,
        "ranked": ranked,
        "year_range": year_range_label(ranked),
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
    # just a short noun phrase (not a full sentence) because _country_hashtag()
    # in captions_common.py strips it down to a single run-on hashtag (e.g.
    # "#leastcorruptcountries") -- feeding it a whole sentence would instead
    # produce a garbled run-on. hook_title already carries the "Top N
    # Countries by X" framing for the caption body (see write_platform_
    # captions' `lead` for non-country series), so nothing is lost.
    synthetic_country = {
        "name": title_name(metric),
        "specialty": f"The world's top {n} countries by {metric['label'].lower()}",
        "facts": [
            f"#{row['rank']}{' (tied)' if row['tied'] else ''} {row['country_name']}: "
            f"{format_value(metric['unit'], row['value'])}"
            for row in ranked
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
