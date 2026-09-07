#!/usr/bin/env python3
"""
Synthesize an upbeat, cheerful background music loop from scratch with numpy — no audio
files, no licensing concerns.

The old version was a calm ambient drone (detuned sine chords + pink noise), which suited
meditative content but felt flat under bright travel videos. This version builds an actual
happy little track:

  - Chord progression: I–V–vi–IV, the classic "feel-good pop" progression, in a major key.
  - A warm sustained pad holds each chord.
  - A bright plucked arpeggio dances over the top — this is the catchy, attention-holding
    part that makes people keep watching.
  - A soft bassline on the chord roots gives it foundation.
  - A gentle kick pulse (four-on-the-floor) plus a soft hi-hat give it light rhythm so it
    reads as MUSIC, not ambience — without being an aggressive EDM beat.

The loop is a whole number of bars so it repeats seamlessly (the assemble stage loops it
to the video length and ducks it under the voice), so this file has NO internal fades.
The key rotates by date for day-to-day variety.

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
BPM = 112  # upbeat but relaxed

# Major keys to rotate through (root MIDI note), kept in a comfortable mid register.
KEY_ROOTS = [60, 62, 64, 65, 67]  # C4, D4, E4, F4, G4

# I–V–vi–IV: (semitone offset from key root, chord quality). The happiest 4 chords in pop.
PROGRESSION = [(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")]
TRIADS = {"maj": [0, 4, 7], "min": [0, 3, 7]}


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


def pluck(freq, dur, decay=0.30):
    """Bright plucked note: a few harmonics under a fast-attack exponential decay —
    marimba/music-box-ish, the catchy lead voice."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t / decay)
    a = max(1, int(0.005 * SR))  # 5ms attack to kill the click
    env[:a] *= np.linspace(0, 1, a)
    wave = (np.sin(2 * np.pi * freq * t)
            + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
            + 0.18 * np.sin(2 * np.pi * 3 * freq * t)
            + 0.08 * np.sin(2 * np.pi * 4 * freq * t))
    return wave * env


def pad(freq, dur):
    """Warm sustained tone with a soft attack/release (the harmonic bed)."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    wave = (0.6 * np.sin(2 * np.pi * freq * t)
            + 0.25 * np.sin(2 * np.pi * 2 * freq * t)
            + 0.1 * np.sin(2 * np.pi * 3 * freq * t))
    env = np.ones(n)
    a = int(0.12 * SR)
    r = int(0.20 * SR)
    if a + r < n:
        env[:a] = np.linspace(0, 1, a)
        env[n - r:] = np.linspace(1, 0, r)
    return wave * env


def bass(freq, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    wave = 0.8 * np.sin(2 * np.pi * freq * t) + 0.15 * np.sin(2 * np.pi * 2 * freq * t)
    env = np.ones(n)
    a = int(0.02 * SR)
    r = int(0.05 * SR)
    if a + r < n:
        env[:a] = np.linspace(0, 1, a)
        env[n - r:] = np.linspace(1, 0, r)
    return wave * env


def kick():
    """Soft round kick: pitch drops fast, quick decay. Felt more than heard."""
    n = int(0.20 * SR)
    t = np.arange(n) / SR
    freq_env = 45 + (120 - 45) * np.exp(-t / 0.03)
    phase = 2 * np.pi * np.cumsum(freq_env) / SR
    return np.sin(phase) * np.exp(-t / 0.11)


def hat(rng):
    """Short soft noise tick for lift on the off-beats."""
    n = int(0.045 * SR)
    noise = rng.uniform(-1, 1, n)
    noise = np.diff(noise, prepend=0.0)  # crude high-pass -> brighter
    env = np.exp(-np.arange(n) / SR / 0.02)
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

        # pad: whole-bar chord
        for m in triad:
            _place(buf, bar_start, pad(midi_freq(m), bar_len), gain=0.11)
        # bass: root an octave down, whole bar
        _place(buf, bar_start, bass(midi_freq(chord_root - 12), bar_len), gain=0.22)

        # arpeggio: 8 eighth-notes across the bar, up-and-over the chord tones
        arp_midis = [triad[0], triad[1], triad[2], triad[0] + 12,
                     triad[1], triad[2], triad[0] + 12, triad[2]]
        eighth = beat / 2
        for i, m in enumerate(arp_midis):
            start = bar_start + int(round(i * eighth * SR))
            _place(buf, start, pluck(midi_freq(m + 12), eighth * 1.6), gain=0.30)

        # kick on every beat; hat on every off-beat
        for beat_i in range(4):
            k_start = bar_start + int(round(beat_i * beat * SR))
            _place(buf, k_start, kick(), gain=0.55)
            h_start = bar_start + int(round((beat_i + 0.5) * beat * SR))
            _place(buf, h_start, hat(rng), gain=0.10)

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

    print(f"[generate_music] {bars} bars @ {BPM} BPM, key root MIDI {key_root} "
          f"(~{bars * bar_len:.1f}s upbeat loop)")
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
