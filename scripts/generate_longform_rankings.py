#!/usr/bin/env python3
"""
Long-form format A: "Top 20 countries by <metric>, explained" (8+ minute 16:9 video).

Same data rules as the rankings Shorts: every number comes from get_ranked() (live
World Bank data or the curated indices) or from real World Bank yearly readings
(get_series), never from the model. GPT only phrases and interprets the numbers it
is given; the whole script is then fact-checked against those same lines, and a
template built purely from the data is used if GPT fails or is rejected.

Metric choice: walks the metric registry after the last long-form metric, skipping
any metric a rankings Short covered in the past week.

Output: <out>/script.json (segments with visual + chapter), <out>/caption_facebook.txt

Usage:
    python scripts/generate_longform_rankings.py
    python scripts/generate_longform_rankings.py --metric-id gdp_per_capita
"""
import argparse
import csv
import json
import os
import re
import textwrap
from datetime import date, datetime, timedelta

from fact_check import verify_narration
from fetch_rankings_stats import format_value, get_ranked, get_series, load_metrics, year_range_label
from generate_rankings_script import title_name

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

TOP_N = 20
MIN_ENTRIES = 15
TREND_YEARS = 10
# Measured on the cloned voice (2026-10-03 dry run, 22 segments, max error 4.7 s):
# ~0.40 s per plain word and ~0.81 s per DIGIT -- numbers are read out in full, so
# word counts badly underestimate number-heavy narration. Aim past 8:00 with margin.
SECONDS_PER_WORD = 0.40
SECONDS_PER_DIGIT = 0.81
MIN_EST_SECONDS = 540
LONG_HISTORY = "logs/history_longform.csv"
SHORTS_HISTORY = "logs/history_rankings.csv"
SHORTS_RECENT_DAYS = 7
# Units where "X% of the leader's figure" is meaningful (amounts, not rates or scores).
RATIO_UNITS = {"usd", "usd_big", "count", "km2", "tonnes"}
# Units whose change is better stated in points than as a percentage.
POINT_UNITS = {"pct", "years"}


def _history_ids(path, since=None, fmt=None):
    """Identifiers from a history CSV, oldest first (optionally only since a date /
    only rows of one long-form format)."""
    ids = []
    if not os.path.exists(path):
        return ids
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 4:
                continue
            try:
                when = datetime.fromisoformat(row[0].replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if since and when < since:
                continue
            if fmt is not None:
                if row[1] == fmt:
                    ids.append(row[3])
            else:
                ids.append(row[2])
    return ids


def pick_metric(metrics_cfg, countries, forced=None):
    # Live (World Bank) metrics only: their yearly history gives every country a
    # decade trend, past rank and peak, which is what fills 8+ minutes with real
    # data. The curated indices have one edition each, so they stay in the Shorts.
    metrics = [m for m in metrics_cfg["metrics"] if m["source_type"] == "live"]
    if forced:
        order = [m for m in metrics if m["id"] == forced]
    else:
        used = _history_ids(LONG_HISTORY, fmt="A")
        start = next((i + 1 for i, m in enumerate(metrics) if used and m["id"] == used[-1]), 0)
        order = metrics[start:] + metrics[:start]
        recent = set(_history_ids(SHORTS_HISTORY, since=date.today() - timedelta(days=SHORTS_RECENT_DAYS)))
        order = [m for m in order if m["id"] not in recent] or order
    for metric in order:
        ranked = get_ranked(metric["id"], countries, top_n=TOP_N, metrics_cfg=metrics_cfg)
        if len(ranked) >= MIN_ENTRIES:
            return metric, ranked
        print(f"[longform_rankings] {metric['id']}: only {len(ranked)} entries, trying the next metric")
    raise SystemExit("No metric has enough data for a top-20 video.")


def _change_text(unit, start, end):
    (y0, v0), (y1, v1) = start, end
    when = "over the past decade" if y1 - y0 == TREND_YEARS else f"since {y0}"
    if unit in POINT_UNITS:
        delta = v1 - v0
        word = "points" if unit == "pct" else "years"
        direction = "up" if delta > 0 else "down"
        return f"{direction} {abs(delta):.1f} {word} {when}"
    if v0 == 0:
        return None
    pct = (v1 - v0) / abs(v0) * 100
    direction = "up" if pct > 0 else "down"
    return f"{direction} {abs(pct):.0f}% {when}"


def past_ranks(metric, series, year):
    """Competition ranks of every country with a real reading in `year`."""
    desc = metric["sort_direction"] == "desc"
    vals = sorted(((pts_by_year[year], iso) for iso, pts in series.items()
                   for pts_by_year in [dict(pts)] if year in pts_by_year), reverse=desc)
    ranks, prev, rank = {}, None, 0
    for i, (v, iso) in enumerate(vals):
        if v != prev:
            rank, prev = i + 1, v
        ranks[iso] = rank
    return ranks


def build_rows(metric, ranked, series):
    """Per-country facts, all computed from real data. Returned best-first.
    `series` should cover every country so past ranks are over the full field."""
    unit = metric["unit"]
    leader = ranked[0]
    latest_years = [int(r["year_or_edition"]) for r in ranked if str(r["year_or_edition"]).isdigit()]
    ref_year = max(latest_years) - TREND_YEARS if latest_years else None
    then_ranks = past_ranks(metric, series, ref_year) if ref_year else {}
    # A past rank is only comparable if roughly the same field of countries reported then.
    now_field = sum(1 for pts in series.values() if pts and pts[-1][0] >= max(latest_years, default=0) - 3)
    if len(then_ranks) < 0.8 * now_field:
        then_ranks = {}
    rows = []
    for i, r in enumerate(ranked):
        facts = []
        points = series.get(r["iso2"], [])
        if len(points) >= 2:
            latest_year = points[-1][0]
            window = [p for p in points if p[0] >= latest_year - TREND_YEARS]
            if len(window) >= 2 and window[0][0] < latest_year:
                change = _change_text(unit, window[0], window[-1])
                if change:
                    facts.append(change)
                best = (max if metric["sort_direction"] == "desc" else min)(window, key=lambda p: p[1])
                if best[0] != latest_year and abs(best[1] - window[-1][1]) > abs(window[-1][1]) * 0.10:
                    facts.append(f"its best reading in that span was {format_value(unit, best[1])} in {best[0]}")
        if r["iso2"] in then_ranks:
            moved = then_ranks[r["iso2"]] - r["rank"]
            places = "place" if abs(moved) == 1 else "places"
            move = (f"up {moved} {places}" if moved > 0 else f"down {-moved} {places}" if moved < 0
                    else "unchanged")
            facts.append(f"ranked number {then_ranks[r['iso2']]} ten years earlier, in {ref_year}, "
                         f"{move} since then")
        if i > 0 and unit in RATIO_UNITS and leader["value"] > 0 and metric["sort_direction"] == "desc":
            facts.append(f"{r['value'] / leader['value'] * 100:.0f}% of {leader['country_name']}'s figure")
        rows.append({
            "iso2": r["iso2"], "country": r["country_name"], "rank": r["rank"], "tied": r["tied"],
            "value": format_value(unit, r["value"]), "raw_value": r["value"],
            "year": str(r["year_or_edition"]), "facts": facts,
            "more_tied_beyond": r.get("more_tied_beyond"),
            "trend": [[y, v] for y, v in points if y >= (points[-1][0] - TREND_YEARS)] if points else [],
        })
    return rows


def reference_lines(rows):
    return [f"{r['country']}: rank {r['rank']}{' (tied)' if r['tied'] else ''}, {r['value']} ({r['year']})"
            + (f"; {'; '.join(r['facts'])}" if r["facts"] else "")
            + (f"; {r['more_tied_beyond']} more countries share this value just outside the top {TOP_N}"
               if r.get("more_tied_beyond") else "")
            for r in rows]


def spoken_label(label):
    """'GDP per Capita' -> 'GDP per capita' for mid-sentence use (keeps acronyms)."""
    return " ".join(w if w.isupper() or any(c.isdigit() for c in w) else w.lower() for w in label.split())


def _fact_sentence(fact, country):
    if fact.startswith(("up ", "down ")):
        return f"Its figure is {fact}."
    if fact.startswith("its best reading"):
        return f"Interestingly, {fact}, so the latest figure is not its peak."
    if fact.startswith("ranked number"):
        return f"For context, {country} {fact}."
    if fact.startswith("ahead of"):
        return f"That puts it {fact}."
    return f"That is {fact}."


def _fallback_script(metric, rows, source_label, as_of):
    """Narration built only from the rows' real numbers -- used when GPT is
    unavailable or rejected."""
    name, label = title_name(metric), spoken_label(metric["label"])
    n = len(rows)
    note = f" One thing to keep in mind: {metric['note']}." if metric.get("note") else ""
    segments = [{"visual": "intro", "text": (
        f"Welcome. Today we rank the top {n} countries in the world by {label}, using {source_label} "
        f"data as of {as_of}. We will count down from number {n} all the way to number one. For every "
        f"country you will see its exact figure, how far it sits from its neighbours in the ranking, and, "
        f"where the yearly records go back far enough, how its figure and its position have changed over "
        f"roughly the past decade.{note} When two countries report exactly the same value, they share the "
        f"same rank, just as the source reports it, and every number you hear comes straight from the "
        f"published data. Let's get started.")}]
    openers = ["At number {rank}", "Number {rank} on our list", "Coming in at number {rank}",
               "Next up, at number {rank}", "Holding number {rank}"]
    usual_year = max(set(r["year"] for r in rows), key=lambda y: sum(r["year"] == y for r in rows))
    for i, r in enumerate(reversed(rows)):
        rank_txt = f"{r['rank']}, tied," if r["tied"] else str(r["rank"])
        year_note = f", based on its {r['year']} figure" if r["year"] != usual_year else ""
        parts = [f"{openers[i % len(openers)].format(rank=rank_txt)} is {r['country']}, "
                 f"with a {label} of {r['value']}{year_note}."]
        parts += [_fact_sentence(f, r["country"]) for f in r["facts"]]
        if r["tied"]:
            parts.append(f"{r['country']} shares this exact figure with at least one other country on the list.")
        if r.get("more_tied_beyond"):
            parts.append(f"{r['more_tied_beyond']} more countries share this value just outside our top {n}.")
        segments.append({"visual": r["iso2"], "text": " ".join(parts)})
    top3 = "; ".join(f"number {r['rank']}, {r['country']}, with {r['value']}" for r in reversed(rows[:3]))
    segments.append({"visual": "outro", "text": (
        f"And that completes our countdown of {name}. To recap the top three: {top3}. "
        f"Every figure in this video comes from {source_label} data, as of {as_of}. Where does your country "
        f"rank? Tell us in the comments, and subscribe for a new full ranking every week.")})
    return segments


def _gpt_script(metric, rows, source_label, as_of, openai_cfg):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    n = len(rows)
    rows_block = "\n".join(f"- ISO2 {r['iso2']} | {line}"
                           for r, line in zip(reversed(rows), reversed(reference_lines(rows))))
    tone = metric.get("tone", "warm, curious documentary")
    system = textwrap.dedent(f"""
        You write the narration for a 10-minute YouTube video ranking the top {n} countries by
        {metric['label']} ({title_name(metric)}). Data source: {source_label}, as of {as_of}.
        {('Important framing: ' + metric['note'] + '.') if metric.get('note') else ''}
        Tone: {tone}. Spoken English, no headings, no emojis, no hashtags.

        HARD RULES:
        - Use ONLY the numbers, years, ranks and comparisons listed below. Never add any other
          number, date, record, statistic or claim about a country. You may interpret the given
          numbers (gaps, trends, what a change means) but not introduce new facts.
        - Ties: say "tied" for rows marked (tied). If a row mentions countries sharing the value
          outside the top {n}, mention that once in that row.
        - Count DOWN from rank {n} to rank 1, one segment per row, in the order given.

        Return JSON: {{"segments": [{{"visual": "intro", "text": "..."}},
          one {{"visual": "<ISO2>", "text": "..."}} per row in the order given,
          {{"visual": "outro", "text": "..."}}]}}
        Lengths: intro 120-160 words (what this ranking measures in general terms, the source,
        how the countdown works); each country 65-80 words; outro 70-100 words (recap the top 3,
        invite comments, ask viewers to subscribe for a new full ranking every week).

        ROWS (rank {n} first):
        {rows_block}
    """).strip()
    response = client.chat.completions.create(
        model=openai_cfg.get("model", "gpt-4o-mini"), temperature=0.7, max_tokens=4500,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": "Write the narration now."}])
    segments = json.loads(response.choices[0].message.content).get("segments") or []
    segments = [{"visual": str(s.get("visual", "")).strip(), "text": str(s.get("text", "")).strip()}
                for s in segments]
    expected = ["intro"] + [r["iso2"] for r in reversed(rows)] + ["outro"]
    if [s["visual"] for s in segments] != expected or not all(s["text"] for s in segments):
        raise RuntimeError("GPT segments don't match the expected countdown order")
    est = estimate_seconds(segments)
    if est < MIN_EST_SECONDS:
        raise RuntimeError(f"GPT narration too short (~{est / 60:.1f} min < {MIN_EST_SECONDS / 60:.0f} min)")
    return segments


def estimate_seconds(segments):
    total = 0.0
    for s in segments:
        tokens = s["text"].split()
        plain = sum(1 for t in tokens if not re.search(r"\d", t))
        digits = sum(len(re.sub(r"\D", "", t)) for t in tokens)
        total += plain * SECONDS_PER_WORD + digits * SECONDS_PER_DIGIT + 0.45
    return total


def attach_chapters(segments, rows):
    by_iso = {r["iso2"]: r for r in rows}
    for s in segments:
        if s["visual"] == "intro":
            s["chapter"] = "Intro"
        elif s["visual"] == "outro":
            s["chapter"] = "Recap"
        else:
            r = by_iso[s["visual"]]
            s["chapter"] = f"#{r['rank']} {r['country']}"
    return segments


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--out", default="build/long")
    ap.add_argument("--metric-id", default=None)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)
    countries = config["countries"]
    metrics_cfg = load_metrics()
    metric, ranked = pick_metric(metrics_cfg, countries, args.metric_id)
    series = get_series(metric["id"], [r["iso2"] for r in ranked], metrics_cfg)
    rows = build_rows(metric, ranked, series)
    source_label = ranked[0]["source_label"]
    as_of = year_range_label(ranked) or str(ranked[0]["year_or_edition"])

    source = "fallback"
    segments = None
    openai_cfg = config.get("openai", {})
    if OPENAI_AVAILABLE and os.environ.get("OPENAI_API_KEY", "").strip():
        for attempt in (1, 2):
            try:
                candidate = _gpt_script(metric, rows, source_label, as_of, openai_cfg)
            except Exception as exc:  # noqa: BLE001 -- any GPT problem falls back to the template
                print(f"[longform_rankings] GPT attempt {attempt} rejected: {exc}")
                continue
            narration = " ".join(s["text"] for s in candidate)
            ok, issues = verify_narration(narration, metric["label"], openai_cfg,
                                          reference=reference_lines(rows), strict=True)
            if ok:
                segments, source = candidate, "openai"
                break
            print(f"[longform_rankings] fact-check flagged attempt {attempt}: {issues}")
    if segments is None:
        segments = _fallback_script(metric, rows, source_label, as_of)
    segments = attach_chapters(segments, rows)
    est = estimate_seconds(segments)

    name = title_name(metric)
    head = (f"Top {len(rows)} {name}" if "display_label" in metric
            else f"Top {len(rows)} Countries by {metric['label']}")
    title = f"{head} ({as_of}) | Full Ranking Explained"
    if len(title) > 100:
        title = f"{head} ({as_of})"[:100]
    description_head = (
        f"Every country in the top {len(rows)} for {spoken_label(metric['label'])}, counted down from "
        f"#{len(rows)} to #1, with exact figures, gaps between neighbours and, where the data allows, "
        f"the change over the past decade.\n\nData: {source_label}, as of {as_of}.")

    os.makedirs(args.out, exist_ok=True)
    record = {
        "format": "A", "identifier": metric["id"], "metric_id": metric["id"], "unit": metric["unit"],
        "title": title, "slide_title": head, "description_head": description_head,
        "source_label": source_label, "as_of": as_of, "narration_source": source,
        "rows": rows, "segments": segments,
    }
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "caption_facebook.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{title}\n\n{description_head}\n\n#countries #ranking #geography #worldfacts")
    print(f"[longform_rankings] {metric['id']}: {len(rows)} countries, ~{est / 60:.1f} min "
          f"estimated, source={source}")


if __name__ == "__main__":
    main()
