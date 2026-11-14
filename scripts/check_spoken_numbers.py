#!/usr/bin/env python3
"""
Does the voice say every amount's scale the way the script does?

The cloned voice occasionally blurs a "b" into an "m": a 2026-11-13 test said
"eighty four point one MILLION dollars" for a script line "$84.1B" (text and phonemes
were correct -- the synthesis slipped). On a statistics channel that is a factual error
on air. generate_captions.py saves what Whisper heard (build/transcript_words.json);
this compares the sequence of scale words (thousand / million / billion / trillion) in
the script's spoken form with the heard sequence. Exit 1 on a mismatch, so the
workflow re-records the voice (StyleTTS2 is stochastic -- a new take differs).

Only clear swaps count (script "billion" where "million" was heard). A scale word
Whisper dropped or added is not flagged -- that is usually Whisper writing "$84.1B"
or "84,100,000", not the voice misspeaking.

Usage:
    python scripts/check_spoken_numbers.py [--script build/script.txt]
                                           [--heard build/transcript_words.json]
"""
import argparse
import difflib
import json
import os
import re
import sys

from generate_captions import _pieces
from spoken_text import to_spoken

SCALES = {"thousand", "million", "billion", "trillion"}
SUFFIX = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion", "t": "trillion"}


def scale_words(words):
    """Scale words in order, from either side ('$84.1B' / '84.1bn' count too)."""
    found = []
    for w in words:
        t = w.lower().strip(".,!?;:'\"")
        if t in SCALES:
            found.append(t)
            continue
        m = re.fullmatch(r"\$?\d[\d,.]*(k|m|b|bn|t)", t)
        if m:
            found.append(SUFFIX[m.group(1)])
    return found


def mismatches(script_text, heard_words):
    said = scale_words(to_spoken(script_text).split())
    heard = scale_words(heard_words)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, said, heard, autojunk=False).get_opcodes():
        if tag == "replace":
            out += [f"script says '{a}', voice says '{b}'" for a, b in zip(said[i1:i2], heard[j1:j2])]
    return out


MIN_READ_ACCURACY = 0.90  # character similarity; below this the take is re-recorded
# Written out by the script but shown as a symbol by Whisper ("$25,216", "99.8%"), so never compared.
IGNORED = {"dollars", "dollar", "euros", "euro", "pounds", "pound", "cents", "cent", "percent", "and"}


def merge_split_numbers(words):
    """Whisper hands back "$25" and ",216" (or "99" and ".8%") as separate words; glue them
    back so a number is compared whole."""
    merged = []
    for w in words:
        if merged and w[:1] in ",." and merged[-1][-1:].isdigit():
            merged[-1] += w
        else:
            merged.append(w)
    return merged


def _flat(words):
    return [piece for w in words for piece in _pieces(w) if piece not in IGNORED]


def read_accuracy(script_text, heard_words):
    """(similarity 0..1, [(script words, heard words)] for every stretch that differs).
    Compared letter by letter with spaces removed, so "seven th" / "seventh" and
    "2,400" / "two thousand four hundred" agree."""
    said, heard = _flat(to_spoken(script_text).split()), _flat(merge_split_numbers(heard_words))
    if not said:
        return 1.0, []
    ratio = difflib.SequenceMatcher(None, "".join(said), "".join(heard), autojunk=False).ratio()
    sm = difflib.SequenceMatcher(None, said, heard, autojunk=False)
    diffs = [(" ".join(said[i1:i2]), " ".join(heard[j1:j2]))
             for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    return ratio, diffs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--script", default="build/script.txt")
    ap.add_argument("--heard", default="build/transcript_words.json")
    args = ap.parse_args()
    if not os.path.exists(args.heard):
        print("[check_spoken_numbers] no transcript (Whisper unavailable) -- skipped")
        return 0
    with open(args.script, encoding="utf-8") as fh:
        script_text = fh.read()
    with open(args.heard, encoding="utf-8") as fh:
        heard = [w["word"] for w in json.load(fh)["words"]]
    bad = mismatches(script_text, heard)
    accuracy, diffs = read_accuracy(script_text, heard)
    if diffs:
        print("[check_spoken_numbers] words that differ (script -> heard): "
              + "; ".join(f"'{a}' -> '{b}'" for a, b in diffs[:12]))
    if bad:
        print("[check_spoken_numbers] the voice misspoke: " + "; ".join(bad))
        return 1
    if accuracy < MIN_READ_ACCURACY:
        print(f"[check_spoken_numbers] the voice misread the script (only {accuracy:.0%} matches)")
        return 1
    print(f"[check_spoken_numbers] read-through matches the script {accuracy:.0%}")
    print(f"[check_spoken_numbers] every amount spoken as written "
          f"({len(scale_words(to_spoken(script_text).split()))} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
