import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_tts as g  # noqa: E402


def test_long_text_is_packed_into_sentence_groups_under_the_limit():
    text = " ".join(f"By 20{i:02d}, it was one point six eight trillion dollars." for i in range(20))
    chunks = g._split_for_styletts2(text, g.VOICECLONE_MAX_CHARS)
    assert len(chunks) > 1 and all(len(c) <= g.VOICECLONE_MAX_CHARS for c in chunks)
    assert " ".join(chunks) == text


def test_parts_are_joined_with_a_short_pause(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(a, np.ones(1000, dtype="float32") * 0.1, 24000)
    sf.write(b, np.ones(500, dtype="float32") * 0.1, 24000)
    out = tmp_path / "out.wav"
    g._join_wavs([str(a), str(b)], str(out))
    data, sr = sf.read(out)
    assert sr == 24000 and len(data) == 1000 + int(24000 * g.JOIN_GAP_SECONDS) + 500
    assert not a.exists() and not b.exists()


def test_short_fallback_limit_is_unchanged():
    text = "One. " * 100
    assert all(len(c) <= g.MAX_CHARS_PER_STYLETTS2_CALL for c in g._split_for_styletts2(text))
