import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from spoken_text import to_spoken  # noqa: E402


@pytest.mark.parametrize("written, spoken", [
    ("Its budget is $997 billion.", "Its budget is 997 billion dollars."),
    ("GDP per person is $1,362.", "GDP per person is 1,362 dollars."),
    ("It spends $997.3B a year", "It spends 997.3 billion dollars a year"),
    ("It costs $2.5 trillion.", "It costs 2.5 trillion dollars."),
    ("Income of US$45,210 per person", "Income of 45,210 dollars per person"),
    ("only $1 a day", "only 1 dollar a day"),
    ("a ticket at $2.50", "a ticket at 2 dollars 50 cents"),
    ("€3bn of aid", "3 billion euros of aid"),
    ("£12 million", "12 million pounds"),
    ("45,000 USD", "45,000 US dollars"),
    ("from 2020-2024", "from 2020 to 2024"),
    ("in 2024–25", "in 2024 to 25"),
    ("about 1/3 of people", "about one third of people"),
    ("~5% growth", "about 5% growth"),
    ("25°C in summer", "25 degrees Celsius in summer"),
    ("5x more", "5 times more"),
    ("#1 is Monaco", "number 1 is Monaco"),
    ("1.41B people", "1.41 billion people"),
    ("12.4 Mt CO₂e", "12.4 million tonnes of C O two"),
    ("PM2.5 of 41 µg/m³", "PM2.5 of 41 micrograms per cubic metre"),
    ("41.2 per 100k", "41.2 per 100,000 people"),
    ("BIGGEST MILITARY BUDGETS – TOP 8 COUNTDOWN", "Biggest Military Budgets, Top 8 Countdown"),
    ("TOP 8 GDP GIANTS OF THE WORLD. Number 8 is Spain.", "Top 8 GDP Giants Of The World. Number 8 is Spain."),
    ("eighty‑three point two", "eighty-three point two"),
])
def test_to_spoken_rewrites_forms_the_voice_cannot_read(written, spoken):
    assert to_spoken(written) == spoken


def test_to_spoken_leaves_plain_text_alone():
    text = "Number eight is South Korea, with 3.4% of GDP."
    assert to_spoken(text) == text


def test_negative_numbers_are_not_ranges():
    assert to_spoken("growth of -3.2%") == "growth of -3.2%"


def test_short_capitalised_acronyms_stay_letters():
    assert to_spoken("The GDP of the UK grew.") == "The GDP of the UK grew."
