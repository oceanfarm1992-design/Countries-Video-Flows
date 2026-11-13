import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pipeline_common import CUT_LEAD_SECONDS, segment_durations  # noqa: E402

SCRIPT = {"segments": [{"text": "Let's dive in."},
                       {"text": "At number 8, Saudi Arabia with $83.2B."},
                       {"text": "Comment below!"}]}


def _write_times(tmp_path, starts):
    words = [w for s in SCRIPT["segments"] for w in s["text"].split()]
    path = tmp_path / "word_times.json"
    path.write_text(json.dumps({"words": [{"word": w, "start": t, "end": t + 0.2}
                                          for w, t in zip(words, starts)]}))
    return str(path)


def test_pictures_change_when_the_first_word_of_each_segment_is_spoken(tmp_path):
    # Arrange: segment 2 starts at 1.6 s, segment 3 at 7.0 s
    starts = [0.0, 0.4, 0.8, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6, 4.0, 7.0, 7.4]
    path = _write_times(tmp_path, starts)

    # Act
    durs = segment_durations(SCRIPT, 9.0, word_times_path=path)

    # Assert
    assert abs(durs[0] - (1.6 - CUT_LEAD_SECONDS)) < 1e-9
    assert abs(sum(durs[:2]) - (7.0 - CUT_LEAD_SECONDS)) < 1e-9
    assert abs(sum(durs) - 9.0) < 1e-9


def test_timings_for_a_different_script_are_ignored(tmp_path):
    path = tmp_path / "word_times.json"
    path.write_text(json.dumps({"words": [{"word": "other", "start": 0, "end": 1}]}))
    durs = segment_durations(SCRIPT, 9.0, word_times_path=str(path))
    assert abs(sum(durs) - 9.0) < 1e-9


def test_without_timings_numbers_count_by_spoken_length(tmp_path):
    # "$83.2B" is one written word but a long spoken phrase: the number-heavy segment
    # must get far more than its written share (6 of 12 words = half).
    durs = segment_durations(SCRIPT, 12.0, word_times_path=str(tmp_path / "missing.json"))
    assert durs[1] / sum(durs) > 0.6
    assert abs(sum(durs) - 12.0) < 1e-9
