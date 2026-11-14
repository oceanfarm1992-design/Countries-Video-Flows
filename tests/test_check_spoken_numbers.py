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


def test_read_accuracy_treats_digits_and_words_alike():
    from check_spoken_numbers import read_accuracy
    acc, diffs = read_accuracy("It sits 2,400 metres up.", "It sits two thousand four hundred metres up".split())
    assert acc > 0.99 and not diffs


def test_read_accuracy_flags_a_misread_word():
    from check_spoken_numbers import MIN_READ_ACCURACY, read_accuracy
    script = "Number three is Spain with a budget of 28 billion dollars"
    heard = "Number free is Spain with a budget of 28 million dollars".split()
    acc, diffs = read_accuracy(script, heard)
    assert ("three", "free") in diffs and acc < 1.0
    assert read_accuracy(script, script.split())[0] > MIN_READ_ACCURACY
