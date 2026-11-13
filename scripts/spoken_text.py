#!/usr/bin/env python3
"""
Turn written narration into text a speech engine reads correctly.

StyleTTS2's phonemizer (gruut) silently drops tokens it can't verbalize: "$997
billion" is spoken as "... billion" (the amount vanishes), "US$45,000" as "us",
"25°C" as "erk", "2024–25" as nothing. Everything here rewrites such forms into
plain words and digits gruut reads well ("997 billion dollars"). Applied only to
what the voice hears -- captions are transcribed from the audio, titles and
on-screen text keep the written form.

Usage:
    from spoken_text import to_spoken
    to_spoken("It spends $997.3B a year")  # -> "It spends 997.3 billion dollars a year"
"""
import re

SCALES = r"(?:thousand|million|billion|trillion)"

# Display formats (from format_value) that would otherwise be read letter by letter.
UNITS = [
    (r"\bper 100k\b", "per 100,000 people"),   # before the generic k rule below
    (r"(\d)\s?T\b", r"\1 trillion"),
    (r"(\d)\s?(?:B|bn)\b", r"\1 billion"),
    (r"(\d)\s?(?:M|mn)\b", r"\1 million"),
    (r"(\d)\s?[Kk]\b", r"\1 thousand"),
    (r"\byrs\b", "years"),
    (r"/km²", " per square kilometre"),
    (r"\bper km²", "per square kilometre"),
    (r"km²", "square kilometres"),
    (r"(\d) t\b", r"\1 tonnes"),
    (r"µg/m³", "micrograms per cubic metre"),
    (r"\bMt\s?CO(?:2|₂)e?\b", "million tonnes of C O two"),
    (r"\btCO(?:2|₂)e?\b", "tonnes of C O two"),
    (r"(\d)\s?Mt\b", r"\1 million tonnes"),
    (r"\bCO(?:2|₂)\b", "C O two"),
    (r"(\d)\s?°C\b", r"\1 degrees Celsius"),
    (r"(\d)\s?°F\b", r"\1 degrees Fahrenheit"),
    (r"(\d)\s?°", r"\1 degrees"),
    (r"‰", " per thousand"),
]

CURRENCIES = {"$": ("dollar", "dollars"), "€": ("euro", "euros"),
              "£": ("pound", "pounds"), "¥": ("yen", "yen")}
CODES = {"USD": "US dollars", "EUR": "euros", "GBP": "pounds"}

FRACTIONS = {"1/2": "one half", "1/3": "one third", "2/3": "two thirds",
             "1/4": "one quarter", "3/4": "three quarters", "1/5": "one fifth", "1/10": "one tenth"}

# "$997 billion", "US$ 45,210", "$2.5T" (after UNITS), "€3bn"
_MONEY = re.compile(rf"(?:US|A|C|NZ|HK|S)?([$€£¥])\s?(\d[\d,]*(?:\.\d+)?)(\s{SCALES})?")
# "45,000 USD", "3 billion EUR"
_CODE = re.compile(rf"(\d[\d,]*(?:\.\d+)?(?:\s{SCALES})?)\s?({'|'.join(CODES)})\b")


def _money(m):
    symbol, amount, scale = m.group(1), m.group(2), m.group(3) or ""
    one, many = CURRENCIES[symbol]
    if not scale and "." in amount:
        whole, cents = amount.split(".", 1)
        cents = (cents + "0")[:2]
        if symbol in "$€" and cents != "00":
            word = one if whole == "1" else many
            return f"{whole} {word} {int(cents)} cents"
        amount = whole
    word = one if amount == "1" and not scale else many
    return f"{amount}{scale} {word}"


def to_spoken(text):
    for pattern, spoken in UNITS:
        text = re.sub(pattern, spoken, text)
    text = _MONEY.sub(_money, text)
    text = _CODE.sub(lambda m: f"{m.group(1)} {CODES[m.group(2)]}", text)
    # year and number ranges: "2020-2024", "2024–25", "5–10%" -> "... to ..."
    text = re.sub(r"(\d)\s?[–—-]\s?(\d)", r"\1 to \2", text)
    for frac, words in FRACTIONS.items():
        text = re.sub(rf"(?<![\d/]){re.escape(frac)}(?![\d/])", words, text)
    text = re.sub(r"(?<![\d/])(\d+)/(\d+)(?![\d/])", r"\1 out of \2", text)
    text = re.sub(r"[~≈]\s?(?=\d)", "about ", text)
    text = re.sub(r"(\d)x\b", r"\1 times", text)
    text = re.sub(r"#(\d+)", r"number \1", text)
    text = re.sub(r"\bNo\.\s?(\d)", r"number \1", text)
    text = re.sub(r"[–—]", ", ", text)              # a dash between words is a pause
    return re.sub(r"[ \t]{2,}", " ", text)
