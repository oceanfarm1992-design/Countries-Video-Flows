#!/usr/bin/env python3
"""
Assemble a long-form 16:9 video from per-segment pieces and per-segment voice.

Each segment becomes its own 1920x1080 clip lasting exactly that segment's voice
slot (timings.json from longform_tts.py): the background footage (darkened) or a
still, with the segment's overlay on top and a short fade at each end. The clips
are joined, then the voice slots and a music bed are mixed underneath.

Also writes the YouTube description with chapter timestamps taken from the real
segment timings, and refuses (exit 1) any result shorter than --min-seconds so a
short video never uploads as "long-form".

Usage:
    python scripts/assemble_long_video.py --dir build/long
"""
import argparse
import json
import os
import subprocess
import sys

from pipeline_common import ffprobe_duration

W, H, FPS = 1920, 1080, 30
FADE = 0.35
MIN_SECONDS = 480


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])} ...")


def render_segment(out, overlay, background, dur):
    fade = (f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(0.0, dur - FADE):.3f}:d={FADE},"
            f"format=yuv420p")
    if background:
        inputs = ["-stream_loop", "-1", "-i", background, "-loop", "1", "-i", overlay]
        graph = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                 f"fps={FPS},eq=brightness=-0.06:saturation=0.9[b];"
                 f"[b][1:v]overlay=0:0:format=auto,{fade}[v]")
    else:
        # A sharp still: per-frame zoompan over 10+ minutes of stills took longer than
        # the whole rest of the build, and the footage segments already carry motion.
        inputs = ["-f", "lavfi", "-i", f"color=c=0x101828:s={W}x{H}:r={FPS}",
                  "-loop", "1", "-framerate", str(FPS), "-i", overlay]
        graph = f"[0:v][1:v]overlay=0:0:format=auto,{fade}[v]"
    # Capped bitrate: footage is a darkened backdrop, and the uncapped first dry run
    # came out at 557 MB for 18 minutes.
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-t", f"{dur:.3f}",
          "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
          "-maxrate", "2500k", "-bufsize", "5000k", "-r", str(FPS), out])


def build_voice(segments, out):
    """Each segment wav padded/trimmed to its exact slot, then joined."""
    inputs, parts = [], []
    for i, s in enumerate(segments):
        inputs += ["-i", s["wav"]]
        parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=mono,"
                     f"apad=whole_dur={s['dur']:.3f},atrim=0:{s['dur']:.3f}[a{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(segments))) + \
        f"concat=n={len(segments)}:v=0:a=1[out]"
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", out])


def chapter_lines(script_segments, timings):
    lines, t = [], 0.0
    for seg, tm in zip(script_segments, timings):
        m, s = divmod(int(t), 60)
        h, m = divmod(m, 60)
        stamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"{stamp} {seg['chapter']}")
        t += tm["dur"]
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="build/long")
    ap.add_argument("--music", default=None, help="Optional music bed (looped, mixed low)")
    ap.add_argument("--music-volume", type=float, default=0.08)
    ap.add_argument("--min-seconds", type=float, default=MIN_SECONDS)
    args = ap.parse_args()
    d = args.dir

    with open(os.path.join(d, "script.json"), encoding="utf-8") as fh:
        script = json.load(fh)
    with open(os.path.join(d, "timings.json"), encoding="utf-8") as fh:
        timings = json.load(fh)["segments"]
    with open(os.path.join(d, "pieces.json"), encoding="utf-8") as fh:
        pieces = json.load(fh)
    if not (len(script["segments"]) == len(timings) == len(pieces)):
        raise SystemExit("script, timings and pieces disagree on the number of segments")

    total = sum(t["dur"] for t in timings)
    if total < args.min_seconds:
        raise SystemExit(f"[assemble_long] narration is only {total:.0f}s (< {args.min_seconds:.0f}s) "
                         f"-- not uploading a short video as long-form")

    clips = []
    for i, (piece, tm) in enumerate(zip(pieces, timings)):
        clip = os.path.join(d, f"clip_{i:03d}.mp4")
        bg = os.path.join(d, piece["background"]) if piece.get("background") else None
        render_segment(clip, os.path.join(d, piece["overlay"]), bg, tm["dur"])
        clips.append(clip)
        print(f"[assemble_long] segment {i + 1}/{len(pieces)} ({tm['dur']:.1f}s, "
              f"{'footage' if bg else 'still'})")
    listing = os.path.join(d, "clips.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        fh.writelines(f"file '{os.path.basename(c)}'\n" for c in clips)
    video = os.path.join(d, "video_only.mp4")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", video])

    voice = os.path.join(d, "voice.wav")
    build_voice(timings, voice)

    final = os.path.join(d, "final.mp4")
    inputs = ["-i", video, "-i", voice]
    audio = "[1:a]afftdn=nr=12,highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11"
    if args.music and os.path.exists(args.music):
        inputs += ["-stream_loop", "-1", "-i", args.music]
        audio += (f"[va];[2:a]volume={args.music_volume},afade=t=in:d=2,"
                  f"afade=t=out:st={max(0.0, total - 3):.2f}:d=3[mus];"
                  "[va][mus]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        audio += "[aout]"
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", audio, "-map", "0:v", "-map", "[aout]",
          "-t", f"{total:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
          "-movflags", "+faststart", final])

    actual = ffprobe_duration(final)
    if actual < args.min_seconds:
        raise SystemExit(f"[assemble_long] final video is {actual:.0f}s (< {args.min_seconds:.0f}s)")
    description = (f"{script['description_head']}\n\nChapters\n" + "\n".join(chapter_lines(script["segments"], timings))
                   + "\n\nEvery figure is shown as published by the source, rounded for display."
                   + "\n\n#countries #ranking #worldfacts #geography")
    with open(os.path.join(d, "description.txt"), "w", encoding="utf-8") as fh:
        fh.write(description)
    with open(os.path.join(d, "title.txt"), "w", encoding="utf-8") as fh:
        fh.write(script["title"])
    print(f"[assemble_long] wrote {final} ({actual / 60:.1f} min, {len(clips)} segments)")


if __name__ == "__main__":
    main()
