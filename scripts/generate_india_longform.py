#!/usr/bin/env python3
"""
Weekly long-form for the India channel: "India in 10 Charts". One theme a week (economy, people
and health, technology and energy), about ten World Bank measures, each told year by year: the
start, every five years, today, the biggest jump, any dip, the world average and where China
and the United States stand. Every sentence is built from the data -- no model writes facts.

Data: the saved World Bank history (config/wb_history, refreshed weekly), so an outage cannot
stop the video. A measure with too few Indian readings is skipped.

Output: build/long/script.json  {title, description_head, proof, hashtags, segments:[{text,
chapter, kind, chart?, broll?}]}  -- the shape longform_tts.py and assemble_long_video.py use.

Usage:
    python scripts/generate_india_longform.py [--theme economy] [--out build/long]
"""
import argparse
import json
import os
from datetime import date

import save_history_snapshot as hist
from generate_india_rising_script import shown, spoken

EPOCH = date(2026, 11, 15)
START_YEAR = 2000
COUNTRIES = {"CN": "China", "US": "the United States"}

# measure: (World Bank code, chart title, unit, progress direction, opening line, footage searches)
M = {
    "gdp": ("NY.GDP.MKTP.CD", "Size of the economy (GDP)", "usd_big", "up",
            "In {y}, India's economy was worth {v}.", ["India port container ship", "Mumbai skyline"]),
    "gdp_pc": ("NY.GDP.PCAP.CD", "GDP per person", "usd", "up",
               "In {y}, India produced {v} for every person.", ["India market", "India shopping mall"]),
    "income": ("NY.GNP.PCAP.CD", "Income per person", "usd", "up",
               "In {y}, the average Indian earned {v} a year.", ["India office", "India street"]),
    "exports": ("NE.EXP.GNFS.CD", "Exports of goods and services", "usd_big", "up",
                "In {y}, India sold {v} to the world.", ["India port container ship", "India cargo"]),
    "reserves": ("FI.RES.TOTL.CD", "Foreign exchange reserves", "usd_big", "up",
                 "In {y}, India held {v} in reserves.", ["Mumbai skyline", "India bank"]),
    "fdi": ("BX.KLT.DINV.CD.WD", "Foreign investment coming in", "usd_big", "up",
            "In {y}, {v} of foreign investment came into India.", ["India factory", "Bangalore office"]),
    "remit": ("BX.TRF.PWKR.CD.DT", "Money sent home by Indians abroad", "usd_big", "up",
              "In {y}, Indians abroad sent home {v}.", ["India family", "India airport"]),
    "gdp_ppp": ("NY.GDP.MKTP.PP.CD", "Economy size adjusted for prices (PPP)", "usd_big", "up",
                "In {y}, adjusted for what money buys locally, India's economy was {v}.", ["India highway traffic", "India metro train"]),
    "rnd": ("GB.XPD.RSDV.GD.ZS", "Research spending (% of GDP)", "pct", "up",
            "In {y}, India spent {v} of its economy on research.", ["India laboratory", "Bangalore office"]),
    "life": ("SP.DYN.LE00.IN", "Life expectancy", "years", "up",
             "In {y}, Indians lived {v} on average.", ["India family", "India elderly"]),
    "infant": ("SP.DYN.IMRT.IN", "Infant deaths per 1,000 births", "per1000", "down",
               "In {y}, {v} babies in every thousand died before turning one.", ["India hospital", "India mother baby"]),
    "water": ("SH.H2O.BASW.ZS", "People with basic drinking water", "pct", "up",
              "In {y}, {v} of Indians had basic drinking water.", ["India water tap", "India village"]),
    "literacy": ("SE.ADT.LITR.ZS", "Adult literacy", "pct", "up",
                 "In {y}, {v} of Indian adults could read and write.", ["India school", "India students"]),
    "poverty": ("SI.POV.DDAY", "Extreme poverty", "pct", "down",
                "In {y}, {v} of Indians lived in extreme poverty.", ["India village", "India market"]),
    "urban": ("SP.URB.TOTL.IN.ZS", "People living in cities", "pct", "up",
              "In {y}, {v} of Indians lived in cities.", ["Mumbai skyline", "Delhi traffic"]),
    "health_spend": ("SH.XPD.CHEX.PC.CD", "Health spending per person", "usd", "up",
                     "In {y}, India spent {v} per person on health.", ["India hospital", "India doctor"]),
    "population": ("SP.POP.TOTL", "Population", "people", "neutral",
                   "In {y}, India had {v} people.", ["India crowd", "Mumbai street"]),
    "electricity": ("EG.ELC.ACCS.ZS", "Access to electricity", "pct", "up",
                    "In {y}, {v} of Indians had electricity.", ["India power lines", "India solar farm"]),
    "internet": ("IT.NET.USER.ZS", "Internet users", "pct", "up",
                 "In {y}, {v} of Indians used the internet.", ["India people smartphone", "Bangalore office"]),
    "mobile": ("IT.CEL.SETS.P2", "Mobile subscriptions per 100 people", "per100", "up",
               "In {y}, there were {v} mobile subscriptions for every hundred Indians.", ["India people smartphone", "India train passengers"]),
    "renewable": ("EG.FEC.RNEW.ZS", "Renewable share of energy use", "pct", "neutral",
                  "In {y}, renewables made up {v} of the energy Indians used.", ["India solar farm", "India wind turbines"]),
    "growth": ("NY.GDP.MKTP.KD.ZG", "Economic growth per year", "pct", "neutral",
               "In {y}, India's economy grew {v}.", ["India factory", "India highway traffic"]),
    "inflation": ("FP.CPI.TOTL.ZG", "Inflation", "pct", "neutral",
                  "In {y}, prices in India rose {v}.", ["India market", "India shopping mall"]),
    "fertility": ("SP.DYN.TFRT.IN", "Children per woman", "decimal", "neutral",
                  "In {y}, an Indian woman had {v} children on average.", ["India family", "India school"]),
    "elderly": ("SP.POP.65UP.TO.ZS", "People aged 65 and over", "pct", "neutral",
                "In {y}, {v} of Indians were 65 or older.", ["India elderly", "India family"]),
    "co2": ("EN.GHG.CO2.PC.CE.AR5", "Carbon dioxide per person (tonnes)", "decimal", "neutral",
            "In {y}, each Indian produced {v} tonnes of carbon dioxide.", ["India power plant", "India solar farm"]),
}
THEMES = {
    "economy": ("India's Economy", ["gdp", "gdp_ppp", "growth", "gdp_pc", "income", "exports", "reserves",
                                    "fdi", "remit", "inflation", "rnd", "urban"]),
    "people": ("India's People and Health", ["population", "life", "infant", "water", "literacy", "poverty",
                                              "fertility", "elderly", "urban", "health_spend", "electricity",
                                              "income"]),
    "technology": ("India's Technology and Energy", ["electricity", "internet", "mobile", "renewable", "rnd",
                                                     "co2", "exports", "gdp_pc", "gdp_ppp", "urban", "water",
                                                     "fdi"]),
}
THEME_ORDER = ["economy", "people", "technology"]


def say(unit, v):
    if v < 0:
        return "minus " + say(unit, -v)
    if unit == "decimal":
        return f"{v:.1f}"
    if unit == "people":
        return f"{v / 1e9:.2f} billion" if v >= 1e9 else f"{v / 1e6:.0f} million"
    if unit == "per1000":
        return f"{v:.0f}"
    return spoken(unit, v)


def show(unit, v):
    if unit == "decimal":
        return f"{v:.1f}"
    if unit == "people":
        return f"{v / 1e9:.2f}B" if v >= 1e9 else f"{v / 1e6:.0f}M"
    return shown(unit, v)


def latest(points, upto=None):
    pts = [p for p in points if upto is None or p[0] <= upto]
    return pts[-1] if pts else None


def change_phrase(unit, direction, v0, v1, y0):
    if unit in ("pct",):
        d = v1 - v0
        return f"That is {abs(d):.0f} percentage points {'more' if d > 0 else 'less'} than in {y0}."
    if v0 > 0 and v1 / v0 >= 1.5:
        return f"That is {v1 / v0:.1f} times the level of {y0}."
    if v0 > 0 and v1 / v0 <= 0.67:
        return f"That is less than {'half' if v1 / v0 < 0.5 else 'two thirds'} of the level in {y0}."
    return f"In {y0} it was {say(unit, v0)}."


def _values_in(series_all, year):
    vals = {}
    for iso2, pts in series_all.items():
        if iso2 == hist.WORLD:
            continue
        v = dict(pts).get(year)
        if v is not None:
            vals[iso2] = v
    return vals


def ranked_count(series_all, year):
    return len(_values_in(series_all, year)) or None


def rank_in(series_all, year, direction):
    """India's rank that year (1 = highest; lowest for 'down' measures), or None when too few
    countries report it or the measure has no better/worse direction."""
    if direction == "neutral":
        return None
    vals = _values_in(series_all, year)
    if "IN" not in vals or len(vals) < 120:
        return None
    me = vals["IN"]
    better = sum(1 for v in vals.values() if (v > me if direction == "up" else v < me))
    return 1 + better


def decade_phrase(unit, india):
    """How much it moved in the 2000s and in the 2010s (nearest readings to 2000/2010/2020)."""
    def near(y):
        pt = min(india, key=lambda p: abs(p[0] - y))
        return pt if abs(pt[0] - y) <= 2 else None
    a, b, c = near(2000), near(2010), near(2020)
    if not (a and b and c):
        return ""

    def move(p, q):
        d = q[1] - p[1]
        amount = f"{abs(d):.1f} percentage points" if unit == "pct" else say(unit, abs(d))
        return f"{'rose' if d > 0 else 'fell'} by {amount}" if d else "held steady"
    return (f"From {a[0]} to {b[0]} it {move(a, b)}; from {b[0]} to {c[0]} it {move(b, c)}.")


def chart_text(n, mid, series_all):
    """Narration for one chart, using only numbers from the series."""
    code, title, unit, direction, line, _ = M[mid]
    india = [p for p in series_all.get("IN", []) if p[0] >= START_YEAR]
    (y0, v0), (y1, v1) = india[0], india[-1]
    parts = [f"Chart {n}: {title}.", line.format(y=y0, v=say(unit, v0))]
    for y in range(((y0 // 5) + 1) * 5, y1, 5):
        pt = latest(india, y)
        if pt and pt[0] > y0 and y1 - pt[0] >= 3:
            parts.append(f"By {pt[0]}, {say(unit, pt[1])}.")
    parts.append(f"And by {y1}, {say(unit, v1)}.")
    if not (direction == "neutral" and unit == "pct"):  # a rate (growth, inflation): no "times", no decade sums
        parts.append(change_phrase(unit, direction, v0, v1, y0))
        parts.append(decade_phrase(unit, india))
    steps = [(b[0], b[1] - a[1]) for a, b in zip(india, india[1:]) if b[0] - a[0] == 1]
    if steps and direction != "neutral":
        best = max(steps, key=lambda s: s[1] if direction == "up" else -s[1])
        if (best[1] > 0) == (direction == "up") and abs(best[1]) > 0:
            parts.append(f"The biggest one-year {'jump' if direction == 'up' else 'fall'} came in {best[0]}.")
        dips = [y for y, d in steps if (d < 0) == (direction == "up") and d != 0]
        if dips and len(dips) <= 3:
            before = dict(india).get(dips[-1] - 1)
            recovered = before is not None and ((v1 > before) == (direction == "up")) and v1 != before
            years = " and ".join(map(str, dips)) if len(dips) < 3 else f"{dips[0]}, {dips[1]} and {dips[2]}"
            parts.append(f"It went the wrong way only in {years}" + (", and then recovered." if recovered else "."))
    if unit in ("usd", "usd_big") and v0 > 0 and v1 > 0 and y1 > y0:
        cagr = (v1 / v0) ** (1 / (y1 - y0)) - 1
        parts.append(f"That is an average rise of {cagr * 100:.1f} percent a year.")
    r0, r1, total = rank_in(series_all, y0, direction), rank_in(series_all, y1, direction), ranked_count(series_all, y1)
    if r0 and r1 and total and not (unit == "pct" and v1 >= 99.5):
        parts.append(f"Among {total} countries, India ranked number {r0} in {y0} and number {r1} in {y1}.")
    world = latest(series_all.get(hist.WORLD, []))
    if world and unit not in ("usd_big", "people"):
        parts.append(f"The world average is {say(unit, world[1])}.")
    others = [(COUNTRIES[c], latest(series_all.get(c, []))) for c in COUNTRIES]
    others = [(name, pt) for name, pt in others if pt]
    if len(others) == 2:
        parts.append(f"{others[0][0]} is at {say(unit, others[0][1][1])}, "
                     f"and {others[1][0]} at {say(unit, others[1][1][1])}.")
    chart = {"title": title, "unit": unit, "code": code, "india": india,
             "world": [p for p in series_all.get(hist.WORLD, []) if p[0] >= START_YEAR],
             "labels": {str(y): show(unit, v) for y, v in india}}
    return " ".join(p for p in parts if p), chart


def build(theme_id):
    name, measures = THEMES[theme_id]
    segments, proof = [], []
    n = 0
    for mid in measures:
        series = hist.load(M[mid][0])
        if len([p for p in series.get("IN", []) if p[0] >= START_YEAR]) < 6:
            print(f"[india_long] skipped {mid}: too few readings")
            continue
        n += 1
        text, chart = chart_text(n, mid, series)
        segments.append({"kind": "chart", "chapter": f"{n}. {chart['title']}", "text": text,
                         "chart": chart, "broll": M[mid][5]})
        proof.append(f"{n}. {chart['title']} (World Bank {chart['code']}): "
                     + ", ".join(f"{y} {chart['labels'][str(y)]}" for y, _ in chart["india"][::5] + [chart["india"][-1]]))
    intro = (f"India in {n} charts: {name}. Every number in this video comes from the World Bank, "
             f"year by year, from {START_YEAR} to today. Let's see how India has changed.")
    outro = ("That was India in numbers. All the figures and their sources are in the description. "
             "Subscribe for a new set of charts every week.")
    segments = ([{"kind": "intro", "chapter": "Intro", "text": intro, "broll": ["India aerial", "Mumbai skyline"]}]
                + segments + [{"kind": "outro", "chapter": "Outro", "text": outro, "broll": ["India sunset", "India landscape"]}])
    return {
        "title": f"India in {n} Charts: {name} ({START_YEAR} to Today)",
        "description_head": (f"{name}: how India changed since {START_YEAR}, in {n} animated charts. "
                             f"Every figure is from the World Bank."),
        "proof": "The data in this video\n" + "\n".join(proof)
                 + "\n\nSource: World Bank, World Development Indicators (https://data.worldbank.org/country/india)",
        "hashtags": "#India #IndiaRising #economy #data #worldbank #development",
        "theme": theme_id, "segments": segments,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--theme", choices=list(THEMES), default=None)
    ap.add_argument("--out", default="build/long")
    args = ap.parse_args()
    week = max(0, (date.today() - EPOCH).days // 7)
    theme = args.theme or THEME_ORDER[week % len(THEME_ORDER)]
    script = build(theme)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as fh:
        json.dump(script, fh, indent=1, ensure_ascii=False)
    words = sum(len(s["text"].split()) for s in script["segments"])
    print(f"[india_long] {script['title']}: {len(script['segments'])} segments, {words} words")


if __name__ == "__main__":
    main()
