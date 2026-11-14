import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generate_tts import PRONUNCIATION_FIXUPS, apply_pronunciation_fixups  # noqa: E402
from spoken_text import to_spoken  # noqa: E402


def test_hard_names_are_respelled():
    out = apply_pronunciation_fixups("Czechia beats Gabon. Lah is in Ladakh, near Tawang.")
    assert "Chekia" in out and "Gabohn" in out and "Lah-dahk" in out and "Tah-wang" in out


def test_whole_words_only():
    assert apply_pronunciation_fixups("Gabonese food") == "Gabonese food"


def test_respellings_survive_the_spoken_text_pass():
    for fixed in PRONUNCIATION_FIXUPS.values():
        assert to_spoken(fixed) == fixed, fixed


def test_a_respelling_never_contains_its_own_original():
    for wrong, fixed in PRONUNCIATION_FIXUPS.items():
        assert wrong not in fixed
