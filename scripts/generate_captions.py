#!/usr/bin/env python3
"""
Stage 4: build burned-in captions from the TTS audio.

Primary (Whisper): call OpenAI Whisper with word-level timestamps on the actual
voice.wav — this gives the EXACT second each word is spoken so captions are
perfectly in sync even when TTS pace varies sentence-to-sentence.

Fallback (even distribution): if Whisper is unavailable (no API key, network
error), words are spread evenly across the voiceover duration. Accurate at the
start but drifts by 1-2s toward the end due to natural TTS rhythm variation.

Output: build/captions.srt

Usage:
    python scripts/generate_captions.py
    python scripts/generate_captions.py --audio build/voice.wav --words-per-cue 3
"""
import argparse
import json
import os
import subprocess

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass


def wav_duration_seconds(path):
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


def build_srt_from_word_timestamps(word_times, words_per_cue):
    """Build SRT from [{word, start, end}, ...] returned by Whisper."""
    cues = []
    i = 0
    while i < len(word_times):
        group = word_times[i:i + words_per_cue]
        start = group[0]["start"]
        end = group[-1]["end"]
        text = " ".join(w["word"].strip() for w in group)
        cues.append((start, end, text))
        i += words_per_cue
    lines = []
    for idx, (start, end, text) in enumerate(cues, 1):
        lines += [str(idx), f"{fmt_ts(start)} --> {fmt_ts(end)}", text, ""]
    return "\n".join(lines)


def build_srt_even(words, total_seconds, words_per_cue):
    """Fallback: distribute words evenly (less accurate than Whisper)."""
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
    for idx, (start, end, text) in enumerate(cues, 1):
        lines += [str(idx), f"{fmt_ts(start)} --> {fmt_ts(end)}", text, ""]
    return "\n".join(lines)


def whisper_word_timestamps(audio_path):
    """Call OpenAI Whisper with word-level timestamps. Returns [{word, start, end}]."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or not OPENAI_AVAILABLE:
        raise RuntimeError("OpenAI not available.")
    client = OpenAI(api_key=api_key)
    print("[generate_captions] calling Whisper for word timestamps...")
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = getattr(resp, "words", None) or []
    if not words:
        raise RuntimeError("Whisper returned no word timestamps.")
    return [{"word": w.word, "start": w.start, "end": w.end} for w in words]


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

    # --- Primary: Whisper word-level timestamps ---
    try:
        word_times = whisper_word_timestamps(args.audio)
        srt = build_srt_from_word_timestamps(word_times, args.words_per_cue)
        print(f"[generate_captions] wrote {args.out} via Whisper ({len(word_times)} words)")
        method = "whisper"
    except Exception as exc:
        print(f"[generate_captions] Whisper unavailable ({exc}), falling back to even distribution")
        duration = wav_duration_seconds(args.audio)
        if duration is None:
            cfg_path = args.config if os.path.exists(args.config) else "config/countries.json"
            with open(cfg_path, encoding="utf-8") as f:
                wps = json.load(f)["video"]["words_per_second"]
            duration = len(words) / wps
            print(f"[generate_captions] estimated {duration:.1f}s from {wps} words/sec")
        else:
            print(f"[generate_captions] voiceover duration {duration:.1f}s")
        srt = build_srt_even(words, duration, args.words_per_cue)
        method = "even"

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(srt)
    if method != "whisper":
        print(f"[generate_captions] wrote {args.out} ({len(words)} words)")


if __name__ == "__main__":
    main()
