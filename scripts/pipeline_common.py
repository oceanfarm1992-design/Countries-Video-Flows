#!/usr/bin/env python3
"""
Small helpers shared across pipeline stages that would otherwise need to
duplicate the same math and risk drifting out of sync with each other.
"""
import json
import re
import subprocess

# Crossfade duration (seconds) between the globe-zoom intro and the main montage.
# Shared by generate_intro.py (which sizes the intro's own video length around it)
# and assemble_video.py (which performs the actual crossfade) so the two can never
# silently drift apart and reintroduce an audio/video sync bug.
INTRO_XFADE_SECONDS = 0.8


# Written by generate_captions.py: when each script word is actually spoken (Whisper).
WORD_TIMES = "build/word_times.json"
# A picture change lands this much before its first word, so it never trails the voice.
CUT_LEAD_SECONDS = 0.15
# Series whose beats open with an animation before the subject appears get a longer
# lead, so the subject is on screen as it is named. Rankings / worst-of
# (render_rankings_motion.py): the camera flies ~1.3 s before the stat card drops in.
# Read from script.json's "series", so the renderer and the assembler (which re-cuts
# the same clips) always use the same cuts.
CUT_LEAD_BY_SERIES = {"rankings": 1.2}
# Spoken-length estimate when no word timings exist: seconds per plain word / per digit
# (numbers are read out in full -- "$83.2B" is ~6 spoken words). Measured on the own
# voice, 2026-11-13 (same model as generate_longform_rankings.py).
SECONDS_PER_WORD = 0.30
SECONDS_PER_DIGIT = 0.57


def _spoken_weight(text):
    from spoken_text import to_spoken  # local import: keeps this module dependency-light
    tokens = to_spoken(text).split()
    plain = sum(1 for t in tokens if not re.search(r"\d", t))
    digits = sum(len(re.sub(r"\D", "", t)) for t in tokens)
    return max(0.3, plain * SECONDS_PER_WORD + digits * SECONDS_PER_DIGIT)


def _timed_starts(segs, path):
    """Start second of each segment's first word, from the Whisper word timings, or None
    when there are none or they don't belong to these segments."""
    try:
        with open(path, encoding="utf-8") as fh:
            timed = json.load(fh)["words"]
    except (OSError, ValueError, KeyError):
        return None
    seg_words = [s.get("text", "").split() for s in segs]
    if [w["word"] for w in timed] != [w for ws in seg_words for w in ws] or not all(seg_words):
        return None
    starts, i = [], 0
    for ws in seg_words:
        starts.append(timed[i]["start"])
        i += len(ws)
    return starts


def segment_durations(script, total, word_times_path=WORD_TIMES):
    """Split `total` seconds across the narration segments so each picture is on screen
    exactly while its words are spoken.

    Uses the real word timings from generate_captions.py when they match the script:
    a segment's picture starts when its first word is heard (CUT_LEAD_SECONDS early, or
    the series' own lead from CUT_LEAD_BY_SERIES).
    Otherwise the split follows each segment's SPOKEN length -- not its written word
    count, which put pictures up to 3 s behind the voice in number-heavy Shorts
    ("$83.2B" is one written word but six spoken ones)."""
    segs = script.get("segments") or []
    if not segs:
        return []
    starts = _timed_starts(segs, word_times_path) if word_times_path else None
    if starts and len(segs) > 1:
        lead = CUT_LEAD_BY_SERIES.get(script.get("series"), CUT_LEAD_SECONDS)
        cuts = [0.0] + [max(0.0, t - lead) for t in starts[1:]] + [total]
        for k in range(1, len(cuts)):            # keep every segment at least 0.3 s
            cuts[k] = max(cuts[k], cuts[k - 1] + 0.3)
        scale = total / cuts[-1] if cuts[-1] > total else 1.0
        return [(b - a) * scale for a, b in zip(cuts, cuts[1:])]
    weights = [_spoken_weight(s.get("text", "")) if s.get("text") else max(1, s.get("words", 1)) * SECONDS_PER_WORD
               for s in segs]
    tw = sum(weights)
    return [total * w / tw for w in weights]


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}: {out.stderr}")
    return float(out.stdout.strip())


def speech_end_time(path, full_duration, noise_db=-35, min_silence=0.2, eof_tolerance=0.15):
    """Where the actual spoken audio in `path` ends, ignoring any trailing silence
    the TTS engine padded the file with.

    Short lines (e.g. the intro announcement) occasionally come back from the TTS
    engines with several extra seconds of trailing silence baked into the wav —
    the file's own duration then overstates how long the line actually takes to
    say. Detect it instead of trusting the raw duration: if the LAST silent
    stretch ffmpeg finds runs all the way to end-of-file, the line's real speech
    ends where that stretch starts; otherwise (no trailing silence, or the last
    gap is merely a mid-sentence pause) trust the full file length."""
    proc = subprocess.run(
        ["ffmpeg", "-i", path, "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", proc.stderr)]
    if starts and ends and abs(ends[-1] - full_duration) <= eof_tolerance:
        return starts[-1]
    return full_duration


def wrap_lines(text, max_chars):
    """Word-wrap `text` into lines of at most ~max_chars (a long word keeps its own line)."""
    lines, current, count = [], [], 0
    for w in text.split():
        space = 1 if current else 0
        if current and count + space + len(w) > max_chars:
            lines.append(" ".join(current))
            current, count = [w], len(w)
        else:
            current.append(w)
            count += space + len(w)
    if current:
        lines.append(" ".join(current))
    return lines


def stacked_drawtext(src, dst, lines, font, fontsize, y, style, enable=None, tag="tl"):
    """drawtext filters for a multi-line block: ONE filter per line, each centred, never a
    newline inside a text= value. ffmpeg 8 (GitHub's Ubuntu runners) draws the newline
    character itself as a missing-glyph box -- live videos showed "From here?[box]" in the
    end card on 2026-11-13. `lines` must already be drawtext-escaped; `y` is the top of
    the first line (an ffmpeg expression); `style` holds colour/box/border options. A box
    keeps its full side padding but only a small top/bottom one, and the line pitch matches
    the box height, so stacked boxes touch without overlapping (no darker seams)."""
    pitch = round(fontsize * 1.17) + 12
    filters, cur = [], src
    for i, line in enumerate(lines):
        out = dst if i == len(lines) - 1 else f"{tag}{i}"
        when = f":enable='{enable}'" if enable else ""
        filters.append(f"[{cur}]drawtext=fontfile={font}:text='{line}':fontsize={fontsize}:{style}:"
                       f"x=(w-text_w)/2:y={y}+{i * pitch}{when}[{out}]")
        cur = out
    return filters
