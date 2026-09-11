#!/usr/bin/env python3
"""
Stage 3: generate a voiceover WAV from the script text.

Engines, tried in order (first available wins unless --engine forces one):
  1. Kokoro-82M (https://huggingface.co/hexgrad/Kokoro-82M) — the production voice
     for both series. Open-weights neural TTS, runs fully offline once its checkpoint
     is cached, no API key or per-run cost. Voice fixed to am_fenrir (deep male) —
     same voice verified in local testing.
  2. Piper (https://github.com/rhasspy/piper) — fast, fully-offline neural TTS,
     no API key needed. Voice models are downloaded once from HuggingFace and
     cached in ./voices/. Fallback if Kokoro fails to load.
  3. espeak-ng — apt-installable on Ubuntu runners, always works, worst quality.
     Last-resort so the daily pipeline never fails outright.

OpenAI TTS (gpt-4o-mini-tts) is still supported via --engine openai for manual use,
but is no longer in the default auto chain.

Output: build/voice.wav

Usage:
    python scripts/generate_tts.py
    python scripts/generate_tts.py --config config/countries.json
    python scripts/generate_tts.py --engine kokoro --voice am_fenrir
    python scripts/generate_tts.py --engine piper --voice en_US-ryan-high
    python scripts/generate_tts.py --engine espeak
"""
import argparse
import json
import os
import subprocess
import sys

import requests

OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass

_KOKORO_PIPELINE = None
_KOKORO_LANG = None

# HuggingFace raw file base for piper voices.
# Path layout: <lang>/<lang_region>/<name>/<quality>/<voice>.onnx[.json]
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_PATHS = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-amy-medium": "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high.onnx",
}

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}


# --------------------------------------------------------------------------- OpenAI
def run_openai_tts(text, out_wav, model, voice, instructions=None):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    if not OPENAI_AVAILABLE:
        raise RuntimeError("openai package not installed.")

    client = OpenAI(api_key=api_key)
    kwargs = dict(model=model, voice=voice, input=text, response_format="wav")
    if instructions:
        kwargs["instructions"] = instructions
    print(f"[generate_tts] calling OpenAI TTS ({model}, voice={voice}) ...")
    with client.audio.speech.with_streaming_response.create(**kwargs) as response:
        response.stream_to_file(out_wav)


# ---------------------------------------------------------------------------- Kokoro
def _get_kokoro_pipeline(lang_code):
    """Load Kokoro-82M once and reuse it across the narration + intro-line calls
    in the same run — construction downloads/loads the model and is by far the
    slowest part (10s+), so paying that cost twice per video would be wasteful."""
    global _KOKORO_PIPELINE, _KOKORO_LANG
    if _KOKORO_PIPELINE is None or _KOKORO_LANG != lang_code:
        from kokoro import KPipeline
        print("[generate_tts] loading Kokoro-82M ...")
        _KOKORO_PIPELINE = KPipeline(lang_code=lang_code)
        _KOKORO_LANG = lang_code
    return _KOKORO_PIPELINE


def run_kokoro(text, out_wav, voice, lang_code="a"):
    import numpy as np
    import soundfile as sf

    pipeline = _get_kokoro_pipeline(lang_code)
    print(f"[generate_tts] calling Kokoro TTS (voice={voice}) ...")
    chunks = [audio for _, _, audio in pipeline(text, voice=voice)]
    full = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    sf.write(out_wav, full, 24000)


# ----------------------------------------------------------------------------- Piper
def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return
    print(f"[generate_tts] downloading {url}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def ensure_voice(voice, voices_dir):
    if voice not in VOICE_PATHS:
        raise SystemExit(f"Unknown voice '{voice}'. Known: {list(VOICE_PATHS)}")
    os.makedirs(voices_dir, exist_ok=True)
    onnx_rel = VOICE_PATHS[voice]
    onnx_path = os.path.join(voices_dir, os.path.basename(onnx_rel))
    json_path = onnx_path + ".json"
    download(f"{HF_BASE}/{onnx_rel}", onnx_path)
    download(f"{HF_BASE}/{onnx_rel}.json", json_path)
    return onnx_path


def run_piper(text, onnx_path, out_wav, length_scale=1.0, sentence_silence=0.3):
    cmd = [
        "piper", "--model", onnx_path, "--output_file", out_wav,
        "--length_scale", str(length_scale),
        "--sentence_silence", str(sentence_silence),
    ]
    print(f"[generate_tts] running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"piper failed with code {proc.returncode}")


# ---------------------------------------------------------------------------- espeak
def run_espeak(text, out_wav):
    cmd = ["espeak-ng", "-v", "en-us+m3", "-s", "135", "-p", "45", "-g", "6",
           "-w", out_wav, text]
    print("[generate_tts] fallback espeak-ng")
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"espeak-ng failed with code {proc.returncode}")


def synth(text, out, engines, openai_model, openai_voice, instructions,
          piper_voice, voices_dir, kokoro_voice, kokoro_lang):
    """Synthesize `text` to `out`, trying each engine in order until one succeeds."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    last_error = None
    for engine in engines:
        try:
            if engine == "kokoro":
                run_kokoro(text, out, kokoro_voice, kokoro_lang)
            elif engine == "openai":
                run_openai_tts(text, out, openai_model, openai_voice, instructions)
            elif engine == "piper":
                onnx_path = ensure_voice(piper_voice, voices_dir)
                run_piper(text, onnx_path, out)
            elif engine == "espeak":
                run_espeak(text, out)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                print(f"[generate_tts] wrote {out} (engine={engine})")
                return
        except Exception as exc:  # noqa: BLE001 — try the next engine
            last_error = exc
            print(f"[generate_tts] {engine} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(f"All TTS engines failed. Last error: {last_error}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.txt")
    ap.add_argument("--out", default="build/voice.wav")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--engine", choices=["auto", "kokoro", "openai", "piper", "espeak"], default="auto",
                    help="auto tries Kokoro, then Piper, then espeak-ng.")
    ap.add_argument("--voice", default=None,
                    help="Override the Kokoro voice name (config's tts.kokoro_voice otherwise).")
    ap.add_argument("--voices-dir", default="voices")
    ap.add_argument("--fallback", choices=["espeak"], default=None,
                    help="Deprecated alias for --engine espeak.")
    ap.add_argument("--intro-json", default="build/script.json",
                    help="If it has an 'intro_line', also render it to --intro-out "
                         "(the spoken opening over the globe zoom).")
    ap.add_argument("--intro-out", default="build/intro_voice.wav")
    args = ap.parse_args()

    if args.fallback == "espeak":
        args.engine = "espeak"

    with open(args.script, encoding="utf-8") as f:
        text = f.read().strip()

    tts_cfg = {}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            tts_cfg = json.load(f).get("tts", {})

    kokoro_voice = args.voice or tts_cfg.get("kokoro_voice", "am_fenrir")
    kokoro_lang = tts_cfg.get("kokoro_lang", "a")
    piper_voice = tts_cfg.get("piper_voice", "en_US-amy-medium")
    openai_model = tts_cfg.get("openai_model", "gpt-4o-mini-tts")
    openai_voice = tts_cfg.get("openai_voice", "marin")
    instructions = tts_cfg.get("instructions")
    engines = [args.engine] if args.engine != "auto" else ["kokoro", "piper", "espeak"]

    common = dict(engines=engines, openai_model=openai_model, openai_voice=openai_voice,
                  instructions=instructions, piper_voice=piper_voice, voices_dir=args.voices_dir,
                  kokoro_voice=kokoro_voice, kokoro_lang=kokoro_lang)

    # main narration
    synth(text, args.out, **common)

    # intro announcement (best-effort; a failure here shouldn't break the run)
    if args.intro_json and os.path.exists(args.intro_json):
        try:
            with open(args.intro_json, encoding="utf-8") as f:
                intro_line = (json.load(f).get("intro_line") or "").strip()
        except Exception:  # noqa: BLE001
            intro_line = ""
        if intro_line:
            try:
                synth(intro_line, args.intro_out, **common)
            except SystemExit as exc:
                print(f"[generate_tts] intro line TTS failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
