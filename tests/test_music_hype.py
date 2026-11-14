import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_music_hype as hype  # noqa: E402


def test_loop_is_a_whole_number_of_bars_and_in_range():
    samples = hype.synth(8, 62, seed=7)
    bar = 4 * 60.0 / hype.BPM
    assert abs(len(samples) / hype.SR - 8 * bar) < 0.01
    assert 0.5 < np.max(np.abs(samples)) <= 0.9


def test_track_is_never_silent_and_is_deterministic():
    a, b = hype.synth(4, 64, seed=7), hype.synth(4, 64, seed=7)
    assert np.array_equal(a, b)
    bar = int(4 * 60.0 / hype.BPM * hype.SR)
    assert all(np.abs(a[i * bar:(i + 1) * bar]).mean() > 0.01 for i in range(4))
