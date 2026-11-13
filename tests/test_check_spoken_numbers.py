import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_spoken_numbers import mismatches  # noqa: E402


def test_billion_heard_as_million_is_flagged():
    # the real 2026-11-13 slip: script "$84.1B", voice "eighty four point one million"
    heard = "Ukraine with eighty four point one million dollars".split()
    assert mismatches("Ukraine with $84.1B.", heard) == ["script says 'billion', voice says 'million'"]


def test_amounts_spoken_as_written_pass_however_whisper_writes_them():
    heard = "Saudi Arabia with $83.2B Ukraine with 84.1 billion and Spain 9.5bn".split()
    assert mismatches("Saudi Arabia with $83.2B. Ukraine with $84.1B. Spain $9.5B.", heard) == []


def test_a_scale_word_whisper_dropped_is_not_a_misspoken_amount():
    heard = "China with 335,500,000,000 dollars and the US 954.4 billion".split()
    assert mismatches("China with $335.5B and the US $954.4B.", heard) == []
