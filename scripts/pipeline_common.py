#!/usr/bin/env python3
"""
Small helpers shared across pipeline stages that would otherwise need to
duplicate the same math and risk drifting out of sync with each other.
"""
import re
import subprocess

# Crossfade duration (seconds) between the globe-zoom intro and the main montage.
# Shared by generate_intro.py (which sizes the intro's own video length around it)
# and assemble_video.py (which performs the actual crossfade) so the two can never
# silently drift apart and reintroduce an audio/video sync bug.
INTRO_XFADE_SECONDS = 0.8


def segment_durations(script, total):
    """Split `total` seconds across the narration segments in proportion to how many
    words each one has, so each clip is on screen for exactly as long as its words are
    spoken."""
    segs = script.get("segments") or []
    words = [max(1, s.get("words", len(s.get("text", "").split()))) for s in segs]
    tw = sum(words) or 1
    return [total * w / tw for w in words]


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
