#!/usr/bin/env python3
"""
Automatic quality gate on the finished video, run after the finishing pass and
before anything is posted. Measured with ffmpeg's own detectors, no API cost.

BLOCKS posting (exit 1) -- a video nobody should see:
  - wrong frame size, no audio track, or near-silent audio
  - more than BLACK_LIMIT seconds of black screen

WARNS (run-page annotation, still posts) -- worth a look:
  - a frame frozen for FREEZE_WARN+ seconds (a stuck clip)
  - a silence of SILENCE_WARN+ seconds inside the video
  - loudness far from the -14 LUFS target

Usage:
    python scripts/qa_video.py --video build/final.mp4
    python scripts/qa_video.py --video build/long/final.mp4 --long
"""
import argparse
import json
import re
import subprocess
import sys

BLACK_LIMIT = 1.5
FREEZE_WARN = 6.0
SILENCE_WARN = 2.5
LOUDNESS_RANGE = (-17.0, -11.0)


def probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", video],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def detectors(video):
    """One decode pass with every detector; returns ffmpeg's log."""
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", video,
         # pix_th=0.03: truly black only -- the default (0.10) counted the dark navy design
         # backgrounds (#101828, rgb 13,17,30) as black and could block a good video
         "-vf", "blackdetect=d=0.5:pic_th=0.97:pix_th=0.03,freezedetect=n=0.002:d=" + str(FREEZE_WARN),
         "-af", f"silencedetect=noise=-45dB:d={SILENCE_WARN},ebur128=framelog=quiet",
         "-f", "null", "-"], capture_output=True, text=True).stderr


def check(video, long_form=False):
    """Returns (blocking_problems, warnings)."""
    info = probe(video)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    block, warn = [], []
    want = (1920, 1080) if long_form else (1080, 1920)
    if v is None or (v["width"], v["height"]) != want:
        block.append(f"frame size {v and (v['width'], v['height'])}, expected {want}")
    if a is None:
        block.append("no audio track")
    log = detectors(video)
    runs = [float(x) for x in re.findall(r"black_duration:([\d.]+)", log)]
    if runs and max(runs) > BLACK_LIMIT:  # one long blackout, not brief dips added up
        block.append(f"{max(runs):.1f}s of black screen")
    for start, length in zip(re.findall(r"freeze_start: ([\d.]+)", log), re.findall(r"freeze_duration: ([\d.]+)", log)):
        warn.append(f"frame frozen for {float(length):.1f}s at {float(start):.1f}s")
    for start, end in zip(re.findall(r"silence_start: ([\d.]+)", log), re.findall(r"silence_end: ([\d.]+)", log)):
        if float(end) < dur - 0.5:  # a quiet tail is fine
            warn.append(f"{float(end) - float(start):.1f}s of silence at {float(start):.1f}s")
    loud = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    if loud:
        lufs = float(loud[-1])
        if lufs < -40:
            block.append(f"audio is near-silent ({lufs} LUFS)")
        elif not LOUDNESS_RANGE[0] <= lufs <= LOUDNESS_RANGE[1]:
            warn.append(f"loudness {lufs} LUFS is far from the -14 target")
    return block, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", default="build/final.mp4")
    ap.add_argument("--long", action="store_true")
    args = ap.parse_args()
    block, warn = check(args.video, args.long)
    for w in warn:
        print(f"::warning::QA: {w}")
    for b in block:
        print(f"::error::QA: {b}")
    print(f"[qa_video] {len(block)} blocking problem(s), {len(warn)} warning(s)")
    return 1 if block else 0


if __name__ == "__main__":
    sys.exit(main())
