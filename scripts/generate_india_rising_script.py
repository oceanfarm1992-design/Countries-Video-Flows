#!/usr/bin/env python3
"""
Stage 1 of the India channel's "India Rising" Short: one development measure, year by year,
as an animated chart (render_india_rising.py). For viewers worldwide: how fast India is changing.

Every number comes from the World Bank (live, or the weekly saved history when the site is
down). The narration is a template filled with those numbers only, never a model's memory,
so nothing unverified is said. Measures rotate one per Rising video.

Output: build/script.json (segments + the series to draw), build/script.txt, build/caption_*.txt

Usage:
    python scripts/generate_india_rising_script.py [--metric electricity] [--rotation 3]
"""
import argparse
import json
import os
from datetime import date

import requests

import save_history_snapshot as hist
from captions_common import write_platform_captions

EPOCH = date(2026, 11, 15)
START_YEAR = 2000
HEADERS = {"User-Agent": "yt-shorts-generator/1.0 (india rising)"}
LIVE = "https://api.worldbank.org/v2/country/IN;1W/indicator/{code}?format=json&per_page=200&date={start}:{end}"
CTA = "Follow to watch India rise."

# id, World Bank code, chart title, unit, the direction that is progress, the opening line
METRICS = [
    {"id": "electricity", "broll": ["India power lines", "India solar farm", "Mumbai city lights night", "India village electricity"], "code": "EG.ELC.ACCS.ZS", "title": "Indians with electricity", "unit": "pct",
     "better": "up", "line": "In {y}, {v} of Indians had electricity."},
    {"id": "gdp", "broll": ["India port container ship", "India factory", "Mumbai skyline", "India highway traffic", "India metro train"], "code": "NY.GDP.MKTP.CD", "title": "India's economy (GDP)", "unit": "usd_big",
     "better": "up", "line": "In {y}, India's economy was worth {v}.", "rank": True},
    {"id": "internet", "broll": ["India people smartphone", "Bangalore office", "India students laptop", "India street"], "code": "IT.NET.USER.ZS", "title": "Indians using the internet", "unit": "pct",
     "better": "up", "line": "In {y}, only {v} of Indians used the internet."},
    {"id": "life", "broll": ["India hospital", "India family", "India elderly", "India yoga"], "code": "SP.DYN.LE00.IN", "title": "Life expectancy in India", "unit": "years",
     "better": "up", "line": "In {y}, Indians lived {v} on average."},
    {"id": "poverty", "broll": ["India village", "India market", "India farmers", "Mumbai skyline"], "code": "SI.POV.DDAY", "title": "Indians in extreme poverty", "unit": "pct",
     "better": "down", "line": "In {y}, {v} of Indians lived in extreme poverty."},
    {"id": "income", "broll": ["India shopping mall", "Mumbai skyline", "India market", "India office"], "code": "NY.GNP.PCAP.CD", "title": "Income per person in India", "unit": "usd",
     "better": "up", "line": "In {y}, the average Indian earned {v} a year."},
    {"id": "infant", "broll": ["India hospital", "India mother baby", "India village"], "code": "SP.DYN.IMRT.IN", "title": "Infant deaths per 1,000 births", "unit": "per1000",
     "better": "down", "line": "In {y}, {v} babies in every thousand died before turning one."},
    {"id": "mobile", "broll": ["India people smartphone", "India street", "India train passengers"], "code": "IT.CEL.SETS.P2", "title": "Mobile phones per 100 Indians", "unit": "per100",
     "better": "up", "line": "In {y}, there were {v} mobile subscriptions for every hundred Indians."},
    {"id": "water", "broll": ["India water tap", "India river", "India village well"], "code": "SH.H2O.BASW.ZS", "title": "Indians with safe drinking water", "unit": "pct",
     "better": "up", "line": "In {y}, {v} of Indians had basic drinking water."},
]


def spoken(unit, v):
    """How the voice says a value (spoken_text.to_spoken turns $ and digits into words)."""
    if unit == "pct":
        if 99.5 <= v < 100:
            return "nearly 100 percent"
        return f"{v:.0f} percent" if v >= 10 else f"{v:.1f} percent"
    if unit == "usd_big":
        return f"${v / 1e12:.2f} trillion" if v >= 1e12 else f"${v / 1e9:.0f} billion"
    if unit == "usd":
        return f"${v:,.0f}"
    if unit == "years":
        return f"{v:.0f} years"
    return f"{v:.0f}"


def shown(unit, v):
    """How the chart writes a value."""
    if unit == "pct":
        if 99.5 <= v < 100:
            return f"{v:.1f}%"
        return f"{v:.0f}%" if v >= 10 else f"{v:.1f}%"
    if unit == "usd_big":
        return f"${v / 1e12:.2f}T" if v >= 1e12 else f"${v / 1e9:.0f}B"
    if unit == "usd":
        return f"${v:,.0f}"
    if unit == "years":
        return f"{v:.1f} yrs"
    return f"{v:.0f}"


def fetch_live(code):
    """{iso2: [(year, value)]} for India and the World average, or None if the site fails."""
    try:
        resp = requests.get(LIVE.format(code=code, start=START_YEAR, end=date.today().year),
                            headers=HEADERS, timeout=40)
        resp.raise_for_status()
        series = hist.parse(resp.json(), {"IN", "1W"})
        return {k: [tuple(p) for p in v] for k, v in series.items()} or None
    except (requests.RequestException, ValueError):
        return None


def load_series(code):
    """(India points, World points, source note). Live first, saved history second."""
    live = fetch_live(code)
    if live and live.get("IN"):
        return live["IN"], live.get("1W", []), "live"
    saved = hist.load(code)
    return saved.get("IN", []), saved.get("1W", []), "saved"


def world_rank(code, year):
    """India's rank among countries in `year` from the saved history (None if unknown)."""
    saved = hist.load(code)
    values = {iso2: dict(pts).get(year) for iso2, pts in saved.items() if iso2 != hist.WORLD}
    values = {k: v for k, v in values.items() if v is not None}
    if "IN" not in values or len(values) < 100:
        return None
    return 1 + sum(1 for v in values.values() if v > values["IN"])


def pick_metric(rotation, metric_id=None):
    if metric_id:
        return next(m for m in METRICS if m["id"] == metric_id)
    return METRICS[rotation % len(METRICS)]


def facts_for(metric, india, world):
    """The numbers the narration and chart are allowed to use."""
    india = [(y, v) for y, v in india if y >= START_YEAR]
    if len(india) < 4:
        raise SystemExit(f"[india_rising] too few years of {metric['id']} for India ({len(india)})")
    (y0, v0), (y1, v1) = india[0], india[-1]
    ym, vm = min(india[1:-1], key=lambda p: abs(p[0] - (y0 + y1) / 2))
    facts = {"y0": y0, "v0": v0, "y1": y1, "v1": v1, "ym": ym, "vm": vm, "points": india,
             "world": [(y, v) for y, v in world if y >= START_YEAR]}
    w = dict(world)
    facts["w1"] = w.get(y1)
    if metric.get("rank"):
        facts["r0"], facts["r1"] = world_rank(metric["code"], y0), world_rank(metric["code"], y1)
    return facts


def narration(metric, f):
    """Segments: hook, the start, the climb, today, the world comparison, the call to action."""
    u = metric["unit"]
    up = metric["better"] == "up"
    segs = [{"text": f"Watch how India changed since {f['y0']}.", "visual": "chart_intro"},
            {"text": metric["line"].format(y=f["y0"], v=spoken(u, f["v0"])), "visual": "chart_start"},
            {"text": "Now watch the line, year by year.", "visual": "chart_grow"},
            {"text": f"By {f['ym']}, {spoken(u, f['vm'])}.", "visual": "chart_grow"},
            {"text": f"By {f['y1']}, it was {spoken(u, f['v1'])}.", "visual": "chart_end"}]
    if metric.get("rank") and f.get("r0") and f.get("r1") and f["r1"] < f["r0"]:
        segs.append({"text": f"India went from number {f['r0']} in the world to number {f['r1']}.",
                     "visual": "chart_end"})
    elif f.get("w1") is not None:
        ahead = (f["v1"] > f["w1"]) == up
        if ahead:
            segs.append({"text": f"The world average is {spoken(u, f['w1'])}. India is now ahead of it.",
                         "visual": "chart_world"})
        else:
            change = abs(f["v1"] - f["v0"])
            amount = f"{change:.0f} percentage points" if u == "pct" else spoken(u, change)
            segs.append({"text": f"The world average is {spoken(u, f['w1'])}, and India is closing the gap: "
                                 f"{'up' if f['v1'] > f['v0'] else 'down'} {amount} since {f['y0']}.",
                         "visual": "chart_world"})
    segs.append({"text": CTA, "visual": "chart_end"})
    for s in segs:
        s["words"] = len(s["text"].split())
    return segs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="config/india.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--metric", default=None, help="force a measure id")
    ap.add_argument("--rotation", type=int, default=None, help="rotation step (default: day number)")
    ap.add_argument("--variant-seed", type=int, default=None)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)
    day = (date.today() - EPOCH).days
    metric = pick_metric(args.rotation if args.rotation is not None else day, args.metric)
    india, world, source = load_series(metric["code"])
    f = facts_for(metric, india, world)
    segments = narration(metric, f)
    text = " ".join(s["text"] for s in segments)
    hook = f"INDIA {f['y0']} VS {f['y1']}: {metric['title'].upper()}"

    record = {
        "id": f"rising-{metric['id']}", "name": "India", "title": metric["title"], "author": "India",
        "series": "india_rising", "hook": hook, "narration": text, "segments": segments,
        "broll": metric["broll"],
        "narration_source": "template", "data_source": source, "iso2": "IN",
        "metric": {k: metric[k] for k in ("id", "code", "title", "unit", "better")},
        "chart": {"india": f["points"], "world": f["world"],
                  "labels": {str(y): shown(metric["unit"], v) for y, v in f["points"]}},
        "facts": {k: f[k] for k in ("y0", "v0", "ym", "vm", "y1", "v1", "w1") if k in f} | (
            {"r0": f.get("r0"), "r1": f.get("r1")} if metric.get("rank") else {}),
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)

    proof = [f"{y}: {shown(metric['unit'], v)}" for y, v in f["points"]]
    sources = {"lines": proof, "credit": f"Source: World Bank, indicator {metric['code']}",
               "link": f"https://data.worldbank.org/indicator/{metric['code']}?locations=IN"}
    subject = {"name": "India", "specialty": metric["title"], "facts": [text]}
    title = f"India {f['y0']} vs {f['y1']}: {metric['title']}"
    write_platform_captions("india_rising", subject, title, config, args.out,
                            args.variant_seed if args.variant_seed is not None else day, sources)
    print(f"[india_rising] {metric['id']}: {f['y0']} {shown(metric['unit'], f['v0'])} -> "
          f"{f['y1']} {shown(metric['unit'], f['v1'])} ({len(text.split())} words, data={source})")


if __name__ == "__main__":
    main()
