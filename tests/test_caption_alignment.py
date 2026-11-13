import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generate_captions import align_script_words  # noqa: E402


def test_script_with_number_words_aligns_to_a_transcript_with_digits():
    # Arrange: the script spells the number out, the transcript writes digits
    script = "Saudi Arabia with eighty-three point two billion dollars.".split()
    heard = [("Saudi", 0.0), ("Arabia", 0.4), ("with", 0.8), ("83.2", 1.1), ("billion", 2.0), ("dollars.", 2.4)]
    times = [{"word": w, "start": t, "end": t + 0.3} for w, t in heard]

    # Act
    words, share = align_script_words(script, times)

    # Assert
    assert share > 0.9
    assert words[3]["start"] == 1.1          # "eighty-three" timed where "83.2" was heard
    assert words[-1]["start"] == 2.4


def test_script_with_digits_aligns_to_a_transcript_with_number_words():
    script = "India with $92.1 billion.".split()
    heard = [("India", 0.0), ("with", 0.5), ("ninety-two", 0.9), ("point", 1.4), ("one", 1.6), ("billion.", 1.9)]
    times = [{"word": w, "start": t, "end": t + 0.3} for w, t in heard]
    words, share = align_script_words(script, times)
    assert share > 0.9 and words[2]["start"] == 0.9
