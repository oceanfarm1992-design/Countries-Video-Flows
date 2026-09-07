#!/usr/bin/env python3
"""
Synthesize a soft ambient background pad with ffmpeg — no music files needed.

This is a fully-automated, zero-asset alternative to shipping licensed tracks. It builds
a calm drone from a chord of detuned sine tones plus a whisper of filtered pink noise for
air, then softens it (low-pass), adds slow movement (tremolo), a little space (echo), and
fades. The assemble stage mixes it low under the voice, so it reads as an atmospheric bed,
not a melody. The chord rotates by date for a little day-to-day variety.

Output: build/music.mp3  (assemble picks it up via --music)

Usage:
    python scripts/generate_music.py
    python scripts/generate_music.py --out build/music.mp3 --duration 24
"""
import argparse
import datetime
import os
import subprocess
import sys

# Calm, reflective chords (Hz). Each is a low root + a few notes above it.
CHORDS = [
    [110.00, 164.81, 220.00, 329.63],   # A minor-ish (A2 E3 A3 E4)
    [130.81, 196.00, 261.63, 392.00],   # C major (C3 G3 C4 G4)
    [146.83, 220.00, 293.66, 440.00],   # D (D3 A3 D4 A4)
    [ 98.00, 146.83, 196.00, 293.66],   # G (G2 D3 G3 D4)
]


def build_filter(freqs, duration):
    """Construct the ffmpeg -filter_complex string for the pad."""
    chains = []
    labels = []
    for i, f in enumerate(freqs):
        vol = 0.30 if i == 0 else 0.16          # root a touch louder than upper notes
        # a second, slightly detuned sine per note gives a warm chorus/beating effect
        chains.append(
            f"sine=frequency={f}:duration={duration}:sample_rate=44100,volume={vol}[a{i}]")
        chains.append(
            f"sine=frequency={f * 1.004:.3f}:duration={duration}:sample_rate=44100,"
            f"volume={vol * 0.8:.3f}[b{i}]")
        labels += [f"[a{i}]", f"[b{i}]"]
    # airy pad bed: pink noise, heavily low-passed and very quiet
    chains.append(
        f"anoisesrc=color=pink:duration={duration}:sample_rate=44100,"
        "lowpass=f=500,volume=0.05[bed]")
    labels.append("[bed]")

    fade_out = max(0.0, duration - 3.0)
    mix = (
        "".join(labels) + f"amix=inputs={len(labels)}:normalize=0,"
        "tremolo=f=0.12:d=0.35,"          # slow gentle pulsing
        "lowpass=f=1400,"                  # soften the sine edges
        "aecho=0.8:0.88:60|110:0.3|0.2,"   # a little space/reverb
        f"afade=t=in:d=3,afade=t=out:st={fade_out:.2f}:d=3,"
        # normalize to a consistent, known loudness so the assemble stage's fixed
        # duck (music-volume) lands the bed at a predictable level under the voice
        "loudnorm=I=-18:TP=-2:LRA=7[out]"
    )
    return ";".join(chains) + ";" + mix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/music.mp3")
    ap.add_argument("--duration", type=float, default=24.0,
                    help="Loop length in seconds (assemble loops it to the video length).")
    ap.add_argument("--chord", type=int, default=None,
                    help="Force a chord index instead of the date-based rotation.")
    args = ap.parse_args()

    if args.chord is not None:
        freqs = CHORDS[args.chord % len(CHORDS)]
    else:
        day = datetime.date.today().timetuple().tm_yday
        freqs = CHORDS[day % len(CHORDS)]

    filter_complex = build_filter(freqs, args.duration)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", f"{args.duration}",
        "-c:a", "libmp3lame", "-q:a", "4", "-ar", "44100", "-ac", "1",
        args.out,
    ]
    print(f"[generate_music] rendering {args.duration}s ambient pad, chord={freqs}")
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-2000:])
        raise SystemExit(f"ffmpeg music render failed ({proc.returncode})")
    print(f"[generate_music] wrote {args.out}")


if __name__ == "__main__":
    main()
