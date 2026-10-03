#!/usr/bin/env python3
"""
Long-form voiceover: one WAV per narration segment, all from the SAME engine.

Per-segment audio gives exact segment timings (slides and chapter timestamps line
up with the words) instead of the Shorts' word-count estimate, which drifts ~7%
and would be several seconds off by the end of a 10-minute video.

generate_tts.synth() falls back to the next engine on any failure; per segment
that could switch voices mid-video. So the engine is locked: every segment is
tried with one engine, and if any segment fails, all segments are redone with
the next engine in the chain.

Output: <out-dir>/seg_000.wav ... and <out-dir>/timings.json
    {"engine": str, "segments": [{"wav": path, "speech": seconds, "dur": seconds}]}
"dur" is the slot length used for both audio and video: speech + GAP_SECONDS.

Usage:
    python scripts/longform_tts.py --script build/long/script.json --out-dir build/long
"""
import argparse
import json
import os
import sys

from generate_tts import apply_pronunciation_fixups, synth
from pipeline_common import ffprobe_duration, speech_end_time

ENGINE_CHAIN = ["styletts2", "kokoro", "piper", "espeak"]
GAP_SECONDS = 0.45


def _engine_options(config_path):
    tts = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            tts = json.load(fh).get("tts", {})
    return dict(openai_model=tts.get("openai_model", "gpt-4o-mini-tts"),
                openai_voice=tts.get("openai_voice", "marin"), instructions=tts.get("instructions"),
                piper_voice=tts.get("piper_voice", "en_US-amy-medium"), voices_dir="voices",
                kokoro_voice=tts.get("kokoro_voice", "am_fenrir"), kokoro_lang=tts.get("kokoro_lang", "a"))


def render_all(texts, out_dir, engine, options):
    """Synthesize every segment with one engine; raises SystemExit on any failure."""
    results = []
    for i, text in enumerate(texts):
        wav = os.path.join(out_dir, f"seg_{i:03d}.wav")
        synth(apply_pronunciation_fixups(text), wav, engines=[engine], **options)
        speech = speech_end_time(wav, ffprobe_duration(wav))
        results.append({"wav": wav, "speech": round(speech, 3), "dur": round(speech + GAP_SECONDS, 3)})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/long/script.json")
    ap.add_argument("--out-dir", default="build/long")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--engine", choices=["auto"] + ENGINE_CHAIN, default="auto")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        texts = [s["text"] for s in json.load(fh)["segments"]]
    os.makedirs(args.out_dir, exist_ok=True)
    options = _engine_options(args.config)
    chain = ENGINE_CHAIN if args.engine == "auto" else [args.engine]

    for engine in chain:
        try:
            segments = render_all(texts, args.out_dir, engine, options)
        except SystemExit as exc:
            print(f"[longform_tts] {engine} failed on a segment ({exc}); redoing all segments "
                  f"with the next engine so the voice never changes mid-video", file=sys.stderr)
            continue
        total = sum(s["dur"] for s in segments)
        with open(os.path.join(args.out_dir, "timings.json"), "w", encoding="utf-8") as fh:
            json.dump({"engine": engine, "segments": segments}, fh, indent=2)
        print(f"[longform_tts] {len(segments)} segments, {total / 60:.1f} min, engine={engine}")
        return
    raise SystemExit("[longform_tts] every TTS engine failed")


if __name__ == "__main__":
    main()
