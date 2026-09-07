#!/usr/bin/env python3
"""
Stage 4: build burned-in captions from the KNOWN script text.

Because we authored the narration, we don't need speech-to-text. We take the total
voiceover duration (read via ffprobe) and distribute the words across it proportionally,
then group them into short caption cues (a few words each) for a punchy, readable,
"word-by-word-ish" look.

Output: build/captions.srt

Duration source:
  - Preferred: ffprobe on build/voice.wav. (The stdlib `wave` module trusts the WAV
    header's data-chunk size, which OpenAI's streamed TTS output does not set
    correctly — it reads back as tens of thousands of seconds. ffprobe decodes the
    actual audio instead, so it's correct for any encoder.)
  - Fallback: if ffprobe can't read the file, estimate from words_per_second in
    config/countries.json.

Usage:
    python scripts/generate_captions.py
    python scripts/generate_captions.py --audio build/voice.wav --words-per-cue 3
"""
import argparse
import json
import os
import subprocess


def wav_duration_seconds(path):
    """Return duration in seconds via ffprobe, or None if the file can't be read."""
    if not os.path.exists(path):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", path],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            return None
        return float(out.stdout.strip())
    except (OSError, ValueError):
        return None


def fmt_ts(seconds):
    """seconds -> SRT timestamp HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600 * 1000)
    m, ms = divmod(ms, 60 * 1000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words, total_seconds, words_per_cue):
    """Distribute words evenly across total_seconds, grouped into cues."""
    n = len(words)
    per_word = total_seconds / n if n else 0.0
    cues = []
    i = 0
    while i < n:
        group = words[i:i + words_per_cue]
        start = i * per_word
        end = (i + len(group)) * per_word
        cues.append((start, end, " ".join(group)))
        i += words_per_cue

    lines = []
    for idx, (start, end, text) in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.txt")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--out", default="build/captions.srt")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--words-per-cue", type=int, default=3)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        words = f.read().split()

    duration = wav_duration_seconds(args.audio)
    if duration is None:
        with open(args.config, encoding="utf-8") as f:
            wps = json.load(f)["video"]["words_per_second"]
        duration = len(words) / wps
        print(f"[generate_captions] WAV unreadable; estimated {duration:.1f}s "
              f"from {wps} words/sec")
    else:
        print(f"[generate_captions] voiceover duration {duration:.1f}s")

    srt = build_srt(words, duration, args.words_per_cue)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(srt)
    print(f"[generate_captions] wrote {args.out} ({len(words)} words)")


if __name__ == "__main__":
    main()
