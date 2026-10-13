#!/usr/bin/env python3
"""
Word-by-word highlight captions (the style most top Shorts use), written as an ASS
subtitle file that ffmpeg's libass burns in -- no extra tools.

Same word timings and the same 3-word groups as captions.srt, so the timing is
exactly what the SRT already proved. On top of that:
  - the word being spoken turns yellow and pops slightly bigger
  - numbers ($40,430 / 61.7% / 9/100) stay yellow the whole time -- they are the
    facts the viewer came for
  - each group pops in (scales up from 88%) instead of just appearing

Layout: 1080x1920 canvas, bottom-centred inside the caption band (y 1100-1360) the
renderers keep clear; long groups wrap onto a second line upward.
"""
import re

PLAY_W, PLAY_H = 1080, 1920
BAND_BOTTOM = 1330          # baseline of the bottom caption line
FONT = "DejaVu Sans"        # preinstalled on the Ubuntu runners
FONT_SIZE = 86
ACTIVE = "&H003CBEF0&"      # ASS colours are &HAABBGGRR -- accent yellow (240, 190, 60)
WHITE = "&H00FFFFFF&"
POP_IN = r"{\fscx88\fscy88\t(0,90,\fscx100\fscy100)}"
ACTIVE_ON = r"{\c" + ACTIVE + r"\fscx112\fscy112}"
NUMBER_ON = r"{\c" + ACTIVE + "}"
RESET = r"{\r}"

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{FONT},{FONT_SIZE},{WHITE},{WHITE},&H00000000&,&H96000000&,-1,0,0,0,100,100,0,0,1,6,3,2,70,70,{PLAY_H - BAND_BOTTOM},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds):
    """ASS timestamp H:MM:SS.cc"""
    cs = max(0, int(round(seconds * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(word):
    # braces start override blocks and a backslash starts a tag
    return word.replace("\\", "/").replace("{", "(").replace("}", ")")


def _is_number(word):
    return bool(re.search(r"\d", word))


def _line(group, active):
    parts = []
    for i, w in enumerate(group):
        text = _escape(w["word"].strip())
        if i == active:
            parts.append(ACTIVE_ON + text + RESET)
        elif _is_number(text):
            parts.append(NUMBER_ON + text + RESET)
        else:
            parts.append(text)
    return " ".join(parts)


def build_ass(word_times, words_per_cue=3):
    """word_times: [{word, start, end}] in order. Returns the .ass file text."""
    events = []
    words = [w for w in word_times if w.get("word", "").strip()]
    for g in range(0, len(words), words_per_cue):
        group = words[g:g + words_per_cue]
        group_end = group[-1]["end"]
        for i, w in enumerate(group):
            start = w["start"]
            end = group[i + 1]["start"] if i + 1 < len(group) else group_end
            if end <= start:
                end = start + 0.05
            text = (POP_IN if i == 0 else "") + _line(group, i)
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Word,,0,0,0,,{text}")
    return HEADER + "\n".join(events) + "\n"


def even_word_times(words, total_seconds):
    """Fallback timings when Whisper is unavailable: words spread evenly."""
    per = total_seconds / len(words) if words else 0.0
    return [{"word": w, "start": i * per, "end": (i + 1) * per} for i, w in enumerate(words)]
