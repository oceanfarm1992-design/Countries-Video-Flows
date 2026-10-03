#!/usr/bin/env python3
"""
Proposes updates to config/rankings_static.json from the same Wikipedia pages the
curated snapshot was originally read from (each block's source_url). It never
publishes anything itself: the monthly workflow pushes the result to a branch and
opens an issue so a person reviews every new number before it can reach a video.

Each source is parsed from the page's rendered HTML table (rowspans expanded, so
tied countries keep their shared value). Numbers are copied exactly as written on
the page, never computed. A source whose table can't be found or fails a sanity
check is reported as FAILED and its block is left untouched.

Global Firepower (military_strength) is not covered: its only source is a
commercial website, so it stays a manual refresh.

Usage:
    python scripts/refresh_rankings_static.py --report build/refresh_report.md
    python scripts/refresh_rankings_static.py --only hdi,passport_index --dry-run
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date
from html.parser import HTMLParser

import requests

STATIC_PATH = "config/rankings_static.json"
COUNTRIES_PATH = "config/countries.json"
API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "Countries-Video-Flows rankings refresh (https://github.com/oceanfarm1992-design/Countries-Video-Flows)"}
TOP_N = 20
MIN_MATCHED = 10
# A new top value outside this ratio of the old one means the wrong column was read.
MAX_TOP_RATIO = 1.5

# find: regex a table's composite header must match. heading: optional regex for the
# section heading above it. value: regex picking the value column; if it captures a
# year, the column with the newest year wins. year: where the edition year comes from.
SOURCES = {
    "hdi": {"page": "List of countries by Human Development Index", "find": r"^HDI value",
            "value": r"^HDI value", "year": "header_max", "edition": "{y} data"},
    "world_happiness": {"page": "World Happiness Report", "find": r"^Life evaluation$",
                        "value": r"^Life evaluation$", "year": "heading", "edition": "{y} report"},
    "democracy_index": {"page": "The Economist Democracy Index", "heading": r"List by country",
                        "find": r"^\d{4}$", "value": r"^(\d{4})$", "year": "value", "edition": "{y} edition"},
    "corruption_index": {"page": "List of countries by Corruption Perceptions Index",
                         "find": r"Nation or Territory", "value": r"^(\d{4}) Score$",
                         "year": "value", "edition": "{y} edition"},
    "global_innovation_index": {"page": "Global Innovation Index", "heading": r"(?i)ranking",
                                "find": r"^Score$", "value": r"^Score$", "year": "heading",
                                "edition": "{y} edition"},
    "pisa_science": {"page": "Programme for International Student Assessment", "find": r"^Science \d{4} Score$",
                     "value": r"^Science (\d{4}) Score$", "year": "value", "edition": "{y} assessment"},
    "passport_index": {"page": "Henley Passport Index", "find": r"^Visa-free destinations$",
                       "value": r"^Visa-free destinations$", "year": "heading", "edition": "{y} ranking"},
}

# Wikipedia spellings that differ from config/countries.json, normalized (see _norm).
ALIASES = {
    "czech republic": "czechia", "turkey": "turkiye", "turkiye": "turkiye",
    "ivory coast": "cote divoire", "cape verde": "cabo verde", "swaziland": "eswatini",
    "burma": "myanmar", "east timor": "timor leste", "united states of america": "united states",
    "korea": "south korea", "republic of korea": "south korea", "korea republic of": "south korea",
    "democratic republic of the congo": "democratic republic of the congo",
    "dr congo": "democratic republic of the congo", "congo kinshasa": "democratic republic of the congo",
    "republic of the congo": "republic of the congo", "congo brazzaville": "republic of the congo",
    "the gambia": "gambia", "the bahamas": "bahamas", "macedonia": "north macedonia",
    "vatican": "vatican city", "holy see": "vatican city", "palestine": "palestine",
    "state of palestine": "palestine", "chinese taipei": "taiwan", "russian federation": "russia",
}


class _TableParser(HTMLParser):
    """Collects every top-level table as rows of {text, th, colspan, rowspan}
    cells, with the nearest preceding h2-h4 heading. Tables nested inside a
    cell are flattened into that cell's text."""

    def __init__(self):
        super().__init__()
        self.tables, self.stack = [], []
        self.row = self.cell = None
        self.skip = self.nested = 0
        self.heading, self.in_heading = "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cell is not None and tag == "table":
            self.nested += 1
            return
        if self.nested:
            if tag in ("td", "th", "br"):
                self.cell["text"] += " "
            return
        if tag in ("h2", "h3", "h4") and not self.stack:
            self.in_heading, self.heading = True, ""
        elif tag == "table":
            self.stack.append({"rows": [], "heading": " ".join(self.heading.split())})
        elif tag == "tr" and self.stack:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"text": "", "th": tag == "th",
                         "colspan": _span(a.get("colspan")), "rowspan": _span(a.get("rowspan"))}
        elif tag in ("sup", "style") and self.cell is not None:
            self.skip += 1
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += " "

    def handle_endtag(self, tag):
        if self.nested:
            if tag == "table":
                self.nested -= 1
            return
        if tag in ("h2", "h3", "h4"):
            self.in_heading = False
        elif tag in ("sup", "style") and self.skip:
            self.skip -= 1
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.cell["text"] = " ".join(self.cell["text"].replace("\xad", "").split())
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None and self.stack:
            self.stack[-1]["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop())

    def handle_data(self, data):
        if self.in_heading:
            self.heading += data
        if self.cell is not None and not self.skip:
            self.cell["text"] += data


def _span(raw):
    digits = "".join(ch for ch in (raw or "1") if ch.isdigit())
    return max(1, int(digits or 1))


def _grid(raw_rows):
    """Expand rowspan/colspan. Returns [(cells_text, is_header_row)]."""
    grid, pending = [], {}
    for raw in raw_rows:
        texts, ths, col, cells = [], [], 0, list(raw)
        while cells or col in pending:
            if col in pending:
                text, th, left = pending[col]
                texts.append(text)
                ths.append(th)
                if left > 1:
                    pending[col] = (text, th, left - 1)
                else:
                    del pending[col]
                col += 1
                continue
            c = cells.pop(0)
            for _ in range(c["colspan"]):
                texts.append(c["text"])
                ths.append(c["th"])
                if c["rowspan"] > 1:
                    pending[col] = (c["text"], c["th"], c["rowspan"] - 1)
                col += 1
        grid.append((texts, bool(ths) and all(ths)))
    return grid


def fetch_tables(page):
    r = requests.get(API, headers=HEADERS, timeout=30, params={
        "action": "parse", "page": page, "prop": "text|revid", "format": "json",
        "formatversion": 2, "redirects": 1})
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("info", "MediaWiki API error"))
    parser = _TableParser()
    parser.feed(data["parse"]["text"])
    tables = []
    for t in parser.tables:
        grid = _grid(t["rows"])
        n_head = 0
        while n_head < len(grid) and grid[n_head][1]:
            n_head += 1
        width = max((len(r) for r, _ in grid), default=0)
        header = []
        for col in range(width):
            parts = []
            for texts, _ in grid[:n_head]:
                if col < len(texts) and texts[col] and (not parts or parts[-1] != texts[col]):
                    parts.append(texts[col])
            header.append(" ".join(parts))
        tables.append({"heading": t["heading"], "header": header, "rows": [r for r, _ in grid[n_head:]]})
    return tables, data["parse"]["revid"]


def _norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    s = " ".join(w for w in s.split() if w != "the" or s.startswith("the gambia") or s.startswith("the bahamas"))
    s = " ".join(s.split())
    return ALIASES.get(s, s)


def _parse_number(text):
    t = text.replace(",", "").replace("−", "-").strip()
    return t if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def extract(key, src, countries):
    """Return (edition, entries, unmatched_names, revid) for one source."""
    tables, revid = fetch_tables(src["page"])
    table = None
    for t in tables:
        if src.get("heading") and not re.search(src["heading"], t["heading"]):
            continue
        if any(re.search(src["find"], h) for h in t["header"]):
            table = t
            break
    if table is None:
        raise RuntimeError("no table matched")

    header = table["header"]
    country_col = next((i for i, h in enumerate(header) if re.search(r"(?i)country|nation", h)), None)
    candidates = [(i, re.search(src["value"], h)) for i, h in enumerate(header)]
    candidates = [(i, m) for i, m in candidates if m]
    if country_col is None or not candidates:
        raise RuntimeError(f"country/value column not found in header {header}")
    if candidates[0][1].groups():
        value_col, m = max(candidates, key=lambda c: int(c[1].group(1)))
        value_year = int(m.group(1))
    else:
        value_col, value_year = candidates[0][0], None

    if src["year"] == "value":
        year = value_year
    elif src["year"] == "heading":
        years = re.findall(r"\b(19\d\d|20\d\d)\b", table["heading"])
        year = int(years[0]) if years else None
    else:
        year = max((int(y) for h in header for y in re.findall(r"\b(19\d\d|20\d\d)\b", h)), default=None)
    if year is None:
        raise RuntimeError("edition year not found")

    by_name = {_norm(c["name"]): c for c in countries}
    rows, unmatched = [], []
    for texts in table["rows"]:
        if max(country_col, value_col) >= len(texts):
            continue
        raw = _parse_number(texts[value_col])
        if raw is None:
            continue
        country = by_name.get(_norm(texts[country_col]))
        if country is None:
            unmatched.append((texts[country_col], raw))
            continue
        rows.append({"iso2": country["iso2"], "country_name": country["name"], "raw": raw})
    return src["edition"].format(y=year), rows, unmatched, revid


def top_entries(rows, lower_is_better=False):
    """Top TOP_N rows by value, plus any further rows tied with the last one."""
    seen, unique = set(), []
    for r in rows:
        if r["iso2"] not in seen:
            seen.add(r["iso2"])
            unique.append(r)
    unique.sort(key=lambda r: float(r["raw"]), reverse=not lower_is_better)
    if len(unique) <= TOP_N:
        return unique
    cutoff = float(unique[TOP_N - 1]["raw"])
    return [r for i, r in enumerate(unique) if i < TOP_N or float(r["raw"]) == cutoff]


def _entry_line(e):
    return (f'{{"iso2": {json.dumps(e["iso2"])}, "country_name": {json.dumps(e["country_name"], ensure_ascii=False)}, '
            f'"value": {e["raw"]}}}')


def apply_update(text, key, edition, entries):
    """Rewrite only `key`'s edition and entries in the file text, leaving every
    other byte as it was (the file keeps published precision such as 0.970)."""
    start = text.index(f'\n  "{key}": {{')
    end = text.index("\n  }", start)
    block = text[start:end]
    block = re.sub(r'("edition": )"[^"]*"', lambda m: m.group(1) + json.dumps(edition, ensure_ascii=False), block, count=1)
    head, rest = block.split('"entries": [', 1)
    tail = rest[rest.index("\n    ]"):]
    body = ",\n".join("      " + _entry_line(e) for e in entries)
    return text[:start] + head + '"entries": [\n' + body + tail + text[end:]


def set_last_refreshed(text, day):
    return re.sub(r'("_last_refreshed": )"[^"]*"', lambda m: f'{m.group(1)}"{day}"', text, count=1)


def diff_lines(old_entries, new_entries):
    old = {e["iso2"]: e for e in old_entries}
    new = {e["iso2"]: e for e in new_entries}
    lines = []
    for iso, e in new.items():
        if iso not in old:
            lines.append(f"| added | {e['country_name']} | | {e['raw']} |")
        elif float(old[iso]["value"]) != float(e["raw"]):
            lines.append(f"| changed | {e['country_name']} | {old[iso]['value']} | {e['raw']} |")
    for iso, e in old.items():
        if iso not in new:
            lines.append(f"| removed | {e['country_name']} | {e['value']} | |")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="Comma-separated static keys (default: all supported)")
    ap.add_argument("--report", default="build/refresh_report.md")
    ap.add_argument("--dry-run", action="store_true", help="Write the report but not the JSON file")
    args = ap.parse_args()

    with open(STATIC_PATH, encoding="utf-8", newline="") as fh:
        original = fh.read()
    static = json.loads(original)
    with open(COUNTRIES_PATH, encoding="utf-8") as fh:
        countries = json.load(fh)["countries"]

    keys = [k for k in (args.only.split(",") if args.only else SOURCES) if k]
    text = original
    report, changed, failed = [], [], []
    for key in keys:
        src, block = SOURCES[key], static[key]
        try:
            edition, rows, unmatched, revid = extract(key, src, countries)
            entries = top_entries(rows, lower_is_better=bool(block.get("lower_is_stronger")))
            if len(entries) < MIN_MATCHED:
                raise RuntimeError(f"only {len(entries)} countries matched")
            old_top, new_top = float(block["entries"][0]["value"]), float(entries[0]["raw"])
            if not (1 / MAX_TOP_RATIO <= new_top / old_top <= MAX_TOP_RATIO):
                raise RuntimeError(f"top value {new_top} is implausible next to the current {old_top}")
        except Exception as exc:  # noqa: BLE001 -- one broken page must not stop the others
            failed.append(key)
            report.append(f"### {key}: FAILED\n\n{type(exc).__name__}: {exc}\n")
            print(f"[refresh] {key}: FAILED ({exc})")
            continue

        changes = diff_lines(block["entries"], entries)
        source = f"https://en.wikipedia.org/w/index.php?oldid={revid}"
        if not changes:
            report.append(f"### {key}: unchanged ({block['edition']})\n")
            print(f"[refresh] {key}: unchanged")
            continue
        changed.append(key)
        # Same year as the current label: keep the curated, more descriptive one
        # ("2025 edition (2023 data)" rather than the generated "2023 data").
        if re.search(rf"\b{re.escape(edition.split()[0])}\b", block["edition"]):
            edition = block["edition"]
        text = apply_update(text, key, edition, entries)
        top_unmatched = [f"{n} ({v})" for n, v in unmatched
                         if float(v) >= float(entries[-1]["raw"]) or block.get("lower_is_stronger")][:8]
        report.append(
            f"### {key}: {block['edition']} -> {edition}\n\n"
            f"Read from [this exact page revision]({source}). Check against the primary source "
            f"({block['source']}) before merging.\n\n"
            "| | Country | Current | Proposed |\n|---|---|---|---|\n" + "\n".join(changes) + "\n"
            + (f"\nIn the source's top list but not in config/countries.json (skipped): "
               f"{', '.join(top_unmatched)}\n" if top_unmatched else ""))
        print(f"[refresh] {key}: {len(changes)} change(s), edition {edition}")

    if changed and not args.dry_run:
        text = set_last_refreshed(text, date.today().isoformat())
        json.loads(text)  # never write a file that doesn't parse
        with open(STATIC_PATH, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    summary = (f"**{len(changed)} index(es) with proposed updates, {len(failed)} failed, "
               f"{len(keys) - len(changed) - len(failed)} unchanged.** "
               "Global Firepower (military_strength) is not covered and stays a manual refresh.\n\n")
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(summary + "\n".join(report))
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as fh:
        fh.write(f"changed={'true' if changed else 'false'}\nfailed={'true' if failed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
