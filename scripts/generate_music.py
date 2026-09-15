#!/usr/bin/env python3
"""
Synthesize an energetic, "breaking news" style background music loop from scratch
with numpy — no audio files, no licensing concerns.

The previous version was a warm, sustained-pad pop track (I-V-vi-IV, happy major
key) — pleasant but too laid-back for the channel's pacing. This version goes for
urgency instead:

  - A pedal-tone bass: the bass note NEVER moves off the key root for the whole
    track (classic tension-building technique — think newsroom sting music), while
    a minor/diminished chord progression shifts in the register above it.
  - Punchy short stabs (fast attack, fast decay) on every beat instead of a
    sustained pad — reads as an alert, not a cozy background.
  - A fast 16th-note arpeggio layered on top for continuous forward motion —
    activity, not just volume, is what actually reads as "energetic".
  - A driving 16th-note percussive tick (a "newsroom clock" pulse) under a harder
    kick + bright snare backbeat, plus a kick "pickup" hit driving into each bar.
  - Faster tempo (146 BPM) than the old track's 112.

The loop is a whole number of bars so it repeats seamlessly (the assemble stage
loops it to the video length and ducks it under the voice), so this file has NO
internal fades. The pedal root rotates by date for day-to-day variety.

Output: build/music.mp3

Usage:
    python scripts/generate_music.py
    python scripts/generate_music.py --out build/music.mp3 --duration 24
    python scripts/generate_music.py --key 3            # force a key instead of by-date
"""
import argparse
import datetime
import os
import subprocess
import sys
import wave

import numpy as np

SR = 44100
BPM = 146  # driving, urgent

# Pedal roots to rotate through by date (MIDI note), kept in a comfortable
# low-mid register since the bass sits on this note for the whole track.
KEY_ROOTS = [57, 60, 62, 64, 55]  # A3, C4, D4, E4, G3

# Chord qualities stacked above the fixed pedal bass (offset from the pedal
# root, quality): i-iv-VII-v(dim) -- a tension-building minor movement. The
# BASS never moves off the pedal root; only these upper voicings shift.
PROGRESSION = [(0, "min"), (5, "min"), (10, "maj"), (7, "dim")]
TRIADS = {"min": [0, 3, 7], "maj": [0, 4, 7], "dim": [0, 3, 6]}


def midi_freq(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def _place(buf, start, wave_arr, gain=1.0):
    """Add wave_arr into buf at sample offset `start`, clipping to buffer bounds."""
    end = start + len(wave_arr)
    if start >= len(buf):
        return
    if end > len(buf):
        wave_arr = wave_arr[: len(buf) - start]
        end = len(buf)
    buf[start:end] += wave_arr * gain


def stab(freq, dur, decay=0.14):
    """Punchy short chord hit: fast attack, fast decay, bright harmonics —
    reads as an alert stab, not a sustained cozy pad."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t / decay)
    a = max(1, int(0.003 * SR))
    env[:a] *= np.linspace(0, 1, a)
    wave = (np.sin(2 * np.pi * freq * t)
            + 0.5 * np.sin(2 * np.pi * 2 * freq * t)
            + 0.3 * np.sin(2 * np.pi * 3 * freq * t)
            + 0.15 * np.sin(2 * np.pi * 5 * freq * t))
    return wave * env


def arp(freq, dur, decay=0.10):
    """Fast, bright plucked note — a driving 16th-note arpeggio layer for
    continuous forward motion."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t / decay)
    a = max(1, int(0.002 * SR))
    env[:a] *= np.linspace(0, 1, a)
    wave = (np.sin(2 * np.pi * freq * t)
            + 0.4 * np.sin(2 * np.pi * 2 * freq * t)
            + 0.25 * np.sin(2 * np.pi * 3 * freq * t))
    return wave * env


def pedal_bass(freq, dur):
    """Held pedal tone: a driving 8th-note pulse on the SAME root note
    throughout the track — the constant repetition is what builds tension,
    not a sustained whole-note."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    wave = 0.85 * np.sin(2 * np.pi * freq * t) + 0.2 * np.sin(2 * np.pi * 2 * freq * t)
    env = np.ones(n)
    a = int(0.01 * SR)
    r = int(0.03 * SR)
    if a + r < n:
        env[:a] = np.linspace(0, 1, a)
        env[n - r:] = np.linspace(1, 0, r)
    return wave * env


def kick():
    """Punchy kick — harder/more present than a soft ambient thump."""
    n = int(0.18 * SR)
    t = np.arange(n) / SR
    freq_env = 50 + (150 - 50) * np.exp(-t / 0.022)
    phase = 2 * np.pi * np.cumsum(freq_env) / SR
    return np.sin(phase) * np.exp(-t / 0.09)


def snare(rng):
    """Bright filtered-noise backbeat hit — the newsroom-urgency snare."""
    n = int(0.12 * SR)
    noise = rng.uniform(-1, 1, n)
    noise = np.diff(noise, prepend=0.0)
    tone = 0.3 * np.sin(2 * np.pi * 200 * np.arange(n) / SR)
    env = np.exp(-np.arange(n) / SR / 0.045)
    return (noise * 0.8 + tone) * env


def tick(rng):
    """Fast, dry percussive tick — the "newsroom clock" 16th-note pulse."""
    n = int(0.025 * SR)
    noise = rng.uniform(-1, 1, n)
    env = np.exp(-np.arange(n) / SR / 0.005)
    return noise * env


def synth(bars, key_root, seed):
    rng = np.random.default_rng(seed)
    beat = 60.0 / BPM
    bar_len = 4 * beat
    total = int(round(bars * bar_len * SR))
    buf = np.zeros(total)

    for b in range(bars):
        degree, quality = PROGRESSION[b % len(PROGRESSION)]
        chord_root = key_root + degree
        triad = [chord_root + iv for iv in TRIADS[quality]]
        bar_start = int(round(b * bar_len * SR))

        # punchy stabs on all 4 beats, accented on 1 & 3
        for beat_i in range(4):
            s = bar_start + int(round(beat_i * beat * SR))
            gain = 0.24 if beat_i in (0, 2) else 0.15
            for m in triad:
                _place(buf, s, stab(midi_freq(m + 12), beat * 0.9), gain=gain)

        # pedal bass: driving 8th notes on the SAME root the whole track
        eighth = beat / 2
        for i in range(8):
            s = bar_start + int(round(i * eighth * SR))
            _place(buf, s, pedal_bass(midi_freq(key_root - 12), eighth * 0.95), gain=0.32)

        # fast 16th-note arpeggio over the chord tones — continuous motion
        sixteenth = beat / 4
        arp_pattern = [0, 1, 2, 1] * 4  # bounces across the triad, 16 hits/bar
        for i, ti in enumerate(arp_pattern):
            s = bar_start + int(round(i * sixteenth * SR))
            m = triad[ti] + 24
            _place(buf, s, arp(midi_freq(m), sixteenth * 1.3), gain=0.16)

        # kick on every beat + snare on 2 and 4 + a pickup kick into the next bar
        for beat_i in range(4):
            k = bar_start + int(round(beat_i * beat * SR))
            _place(buf, k, kick(), gain=0.72)
            if beat_i in (1, 3):
                sn = bar_start + int(round(beat_i * beat * SR))
                _place(buf, sn, snare(rng), gain=0.42)
        pickup = bar_start + int(round(3.5 * beat * SR))
        _place(buf, pickup, kick(), gain=0.5)

        # 16th-note ticking pulse — the urgent "newsroom clock"
        for i in range(16):
            s = bar_start + int(round(i * sixteenth * SR))
            _place(buf, s, tick(rng), gain=0.17)

    # peak-normalize with headroom (assemble ducks + fades this under the voice)
    peak = np.max(np.abs(buf)) or 1.0
    buf = buf / peak * 0.89
    return buf


def write_wav(path, samples):
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/music.mp3")
    ap.add_argument("--duration", type=float, default=24.0,
                    help="Approx loop length in seconds (rounded to whole bars; assemble "
                         "loops it to the video length).")
    ap.add_argument("--key", type=int, default=None,
                    help="Force a key index instead of the date-based rotation.")
    args = ap.parse_args()

    day = datetime.date.today().timetuple().tm_yday
    key_root = KEY_ROOTS[(args.key if args.key is not None else day) % len(KEY_ROOTS)]

    beat = 60.0 / BPM
    bar_len = 4 * beat
    # whole number of bars, and a multiple of the 4-chord progression for a clean loop
    bars = max(4, round(args.duration / bar_len / len(PROGRESSION)) * len(PROGRESSION))

    print(f"[generate_music] {bars} bars @ {BPM} BPM, pedal root MIDI {key_root} "
          f"(~{bars * bar_len:.1f}s energetic loop)")
    samples = synth(bars, key_root, seed=day)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    tmp_wav = args.out + ".tmp.wav"
    write_wav(tmp_wav, samples)

    cmd = ["ffmpeg", "-y", "-i", tmp_wav,
           "-c:a", "libmp3lame", "-q:a", "4", "-ar", str(SR), "-ac", "1", args.out]
    proc = subprocess.run(cmd, capture_output=True)
    os.remove(tmp_wav)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-2000:])
        raise SystemExit(f"ffmpeg mp3 encode failed ({proc.returncode})")
    print(f"[generate_music] wrote {args.out}")


if __name__ == "__main__":
    main()
