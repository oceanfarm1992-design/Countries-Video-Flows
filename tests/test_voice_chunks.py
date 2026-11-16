import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_tts as g  # noqa: E402


def test_groups_stay_under_the_phonetic_limit_and_keep_every_word():
    text = " ".join(f"By 20{i:02d}, it rose by three hundred and fourteen billion dollars." for i in range(12))
    groups = g._pack_for_voiceclone(text)
    assert len(groups) > 1 and all(g._ipa_len(c) <= g.VOICECLONE_MAX_IPA for c in groups)
    assert " ".join(groups) == text


def test_short_text_stays_in_one_piece():
    assert g._pack_for_voiceclone("This is not Bali. This is India.") == ["This is not Bali. This is India."]


def test_an_over_long_sentence_splits_at_its_commas():
    measure = len  # one symbol per character, to make the limit easy to reason about
    groups = g._pack_for_voiceclone("aaaa, bbbb, cccc, dddd.", limit=12, measure=measure)
    assert all(len(c) <= 12 for c in groups) and " ".join(groups) == "aaaa, bbbb, cccc, dddd."


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
