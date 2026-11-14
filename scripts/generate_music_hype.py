#!/usr/bin/env python3
"""
Original "hype" background track for the India channel, synthesised from scratch with numpy
(no samples, no licensing). The style target is modern trap-pop drive: a 96 BPM groove with a
deep gliding 808, a rolling 16th hi-hat, claps on 2 and 4, tabla hits for an Indian colour,
a warm major-key pad and a bansuri-like flute playing an original four-bar riff. Energy stays
steady like a loop bed, with a riser into every 8th bar so the cuts feel lifted.

The tempo, groove density and low-end weight were taken from a style reference; the notes,
chords, rhythm patterns and sounds here are written fresh and share nothing with it.

The loop is a whole number of bars (16 = 40 s) so assemble_video.py can repeat it seamlessly
and duck it under the voice. Output: build/music.mp3

Usage:
    python scripts/generate_music_hype.py [--out build/music.mp3] [--bars 16] [--key 0]
"""
import argparse
import datetime
import os
import subprocess
import sys

import numpy as np

from generate_music import SR, _place, midi_freq, write_wav

BPM = 96
# Tonic rotates by date for variety: D, E, G, A (all sit well for an 808 and a flute).
KEY_ROOTS = [62, 64, 67, 69]
# Uplifting I - V - vi - IV, as (bass offset from the tonic, chord quality).
PROGRESSION = [(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")]
TRIADS = {"maj": [0, 4, 7], "min": [0, 3, 7]}

# Original 4-bar flute riff on the major pentatonic: (start in 8ths, semitones above the
# tonic, length in 8ths) per bar.
RIFF = [
    [(0, 7, 2), (2, 9, 1), (3, 7, 1), (4, 4, 3)],
    [(0, 9, 2), (2, 7, 2), (4, 4, 2), (6, 2, 2)],
    [(0, 4, 2), (2, 7, 1), (3, 9, 1), (4, 12, 3), (7, 9, 1)],
    [(0, 9, 2), (2, 7, 2), (4, 4, 2), (6, 7, 2)],
]

# 808 hits per bar in 16ths: (position, length in 16ths, glide up a fourth into it)
BASS_HITS = [(0, 5, False), (6, 2, False), (10, 3, True), (14, 2, False)]
KICKS = [0, 10]
CLAPS = [4, 12]
TABLA = [2, 5, 13]
GHOST_NOISE_SEED = 7


def _env(n, attack, release):
    env = np.ones(n)
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    env[:a] = np.linspace(0, 1, a)
    if r < n:
        env[n - r:] = np.linspace(1, 0, r)
    return env


def bass808(freq, dur, glide=False):
    """Deep sine 808 with a slow decay and a short slide in from below."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = np.full(n, freq)
    if glide:
        f = freq * (1 + (2 ** (-5 / 12) - 1) * np.exp(-t / 0.05))  # slides up from a fourth below
    phase = 2 * np.pi * np.cumsum(f) / SR
    wave = np.tanh(1.8 * np.sin(phase)) * np.exp(-t / (dur * 0.55))
    return wave * _env(n, 0.004, 0.03)


def kick():
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(48 + 130 * np.exp(-t / 0.02)) / SR
    return np.sin(phase) * np.exp(-t / 0.1)


def clap(rng):
    """Three quick noise bursts and a short tail."""
    out = np.zeros(int(0.25 * SR))
    for off in (0.0, 0.011, 0.022):
        n = int(0.08 * SR)
        burst = np.diff(rng.uniform(-1, 1, n), prepend=0.0) * np.exp(-np.arange(n) / SR / 0.02)
        _place(out, int(off * SR), burst, 0.55)
    n = len(out)
    tail = np.diff(rng.uniform(-1, 1, n), prepend=0.0) * np.exp(-np.arange(n) / SR / 0.06)
    return out + tail * 0.35


def hat(rng, open_hat=False, decay=None):
    n = int((0.14 if open_hat else 0.035) * SR)
    d = decay or (0.05 if open_hat else 0.008)
    noise = np.diff(np.diff(rng.uniform(-1, 1, n), prepend=0.0), prepend=0.0)  # bright
    return noise * np.exp(-np.arange(n) / SR / d)


def tabla(freq):
    """Tuned skin hit: a pitch that drops fast, a woody click, a short ring."""
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(freq * (1 + 0.9 * np.exp(-t / 0.018))) / SR
    body = np.sin(phase) * np.exp(-t / 0.09)
    click = np.sin(2 * np.pi * 1800 * t) * np.exp(-t / 0.004) * 0.4
    return body + click


def flute(freq, dur, rng):
    """Bansuri-like: soft attack, gentle vibrato, odd-leaning harmonics and a little breath."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1, t / 0.25)
    phase = 2 * np.pi * np.cumsum(freq * vib) / SR
    tone = np.sin(phase) + 0.3 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)
    breath = np.diff(rng.uniform(-1, 1, n), prepend=0.0) * 0.05 * np.exp(-t / 0.15)
    return (tone + breath) * _env(n, 0.035, 0.09)


def pad(freq, dur):
    """Warm sustained chord tone, slightly detuned pair."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    wave = (np.sin(2 * np.pi * freq * t) + np.sin(2 * np.pi * freq * 1.003 * t)
            + 0.25 * np.sin(2 * np.pi * 2 * freq * t))
    return wave * _env(n, 0.4, 0.5)


def riser(seconds, rng):
    """Rising filtered noise into the downbeat, then a short impact."""
    n = int(seconds * SR)
    noise = np.diff(rng.uniform(-1, 1, n), prepend=0.0)
    swell = np.linspace(0, 1, n) ** 2.5
    return noise * swell


def synth(bars, tonic, seed):
    rng = np.random.default_rng(seed)
    beat = 60.0 / BPM
    bar_len = 4 * beat
    sixteenth, eighth = beat / 4, beat / 2
    buf = np.zeros(int(round(bars * bar_len * SR)))
    at = lambda bar, seconds: int(round((bar * bar_len + seconds) * SR))  # noqa: E731

    for b in range(bars):
        bass_off, quality = PROGRESSION[b % len(PROGRESSION)]
        triad = [tonic + bass_off + iv for iv in TRIADS[quality]]

        for m in triad:                                    # pad, one long chord per bar
            _place(buf, at(b, 0), pad(midi_freq(m), bar_len * 1.02), 0.035)
        for pos, length, glide in BASS_HITS:               # 808 line
            _place(buf, at(b, pos * sixteenth),
                   bass808(midi_freq(tonic - 24 + bass_off), length * sixteenth * 1.1, glide), 0.5)
        for pos in KICKS:
            _place(buf, at(b, pos * sixteenth), kick(), 0.5)
        for pos in CLAPS:
            _place(buf, at(b, pos * sixteenth), clap(rng), 0.42)
        for pos in TABLA:
            _place(buf, at(b, pos * sixteenth), tabla(midi_freq(tonic - 12 + (0 if pos != 13 else 7))), 0.22)

        rolling = b % 4 == 3                               # hat roll on the last beat of every 4th bar
        for i in range(16):
            vel = (1.0, 0.45, 0.7, 0.45)[i % 4]
            if i == 14:
                _place(buf, at(b, i * sixteenth), hat(rng, open_hat=True), 0.26)
            elif rolling and i >= 12:
                for j in range(2):
                    _place(buf, at(b, (i + j / 2) * sixteenth), hat(rng), 0.2 * (1 + (i - 12) / 6))
            else:
                _place(buf, at(b, i * sixteenth), hat(rng), 0.2 * vel)

        if b >= 2:                                         # flute riff enters on bar 3, up an octave in the 2nd half
            octave = 12 if (b // 8) % 2 else 0
            for start, semis, length in RIFF[b % 4]:
                _place(buf, at(b, start * eighth),
                       flute(midi_freq(tonic + 12 + semis + octave), length * eighth * 1.05, rng), 0.15)

        if b % 8 == 7:                                     # riser into bar 8 / the loop point
            _place(buf, at(b, 2 * beat), riser(2 * beat, rng), 0.16)
        if b % 8 == 0 and b > 0:                           # impact on the downbeat
            _place(buf, at(b, 0), hat(rng, open_hat=True, decay=0.25) * 2.2, 0.2)

    buf = np.tanh(0.9 * buf)                               # glue + loudness
    return buf / (np.max(np.abs(buf)) or 1.0) * 0.89


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build/music.mp3")
    ap.add_argument("--bars", type=int, default=16, help="whole bars (16 = 40 s)")
    ap.add_argument("--key", type=int, default=None, help="force a key index")
    args = ap.parse_args()

    day = datetime.date.today().timetuple().tm_yday
    tonic = KEY_ROOTS[(args.key if args.key is not None else day) % len(KEY_ROOTS)]
    print(f"[generate_music_hype] {args.bars} bars @ {BPM} BPM, tonic MIDI {tonic}")
    samples = synth(args.bars, tonic, seed=GHOST_NOISE_SEED)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    tmp = args.out + ".tmp.wav"
    write_wav(tmp, samples)
    proc = subprocess.run(["ffmpeg", "-y", "-i", tmp, "-c:a", "libmp3lame", "-q:a", "4",
                           "-ar", str(SR), "-ac", "1", args.out], capture_output=True)
    os.remove(tmp)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-2000:])
        raise SystemExit(f"ffmpeg mp3 encode failed ({proc.returncode})")
    print(f"[generate_music_hype] wrote {args.out}")


if __name__ == "__main__":
    main()
