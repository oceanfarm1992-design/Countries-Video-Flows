#!/usr/bin/env python3
"""
Finishing pass on the assembled video, the last step before posting (every series):

  - progress bar: a thin accent bar across the top that fills as the video plays, so
    the viewer always sees how little is left (Shorts only)
  - loop ending: the last half-second dissolves into the video's own first frame, so
    when Shorts replays it the loop is seamless and the replay feels intentional
  - platform loudness: the whole mix normalised to -14 LUFS / -1 dBTP, YouTube's
    playback target, so no video plays quieter than the one before it in the feed

Never fatal: if anything fails, the unfinished video is kept and posted as before.

Usage:
    python scripts/finish_video.py --video build/final.mp4
    python scripts/finish_video.py --video build/long/final.mp4 --long   # loudness only
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from pipeline_common import ffprobe_duration

BAR_H = 10
BAR_RGB = "0xF0BE3C"     # motion_kit ACCENT
LOOP_SECONDS = 0.45
TARGET = "I=-14:TP=-1:LRA=11"


def loudness_filter(video):
    """Two-pass loudnorm: measure the mix first, then apply one linear correction --
    lands on -14 LUFS (one pass was ~1 LU short) and doesn't pump the dynamics."""
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-vn", "-af",
                            f"loudnorm={TARGET}:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, check=True).stderr
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    return (f"loudnorm={TARGET}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
            f"offset={m['target_offset']}:linear=true")


def finish(video, out, long_form=False):
    dur = ffprobe_duration(video)
    loud = loudness_filter(video)
    if long_form:
        inputs = ["-i", video]
        graph = f"[0:a]{loud}[a]"
        vmap, vcodec = ["-map", "0:v"], ["-c:v", "copy"]
    else:
        first = os.path.splitext(out)[0] + "_first.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-frames:v", "1", first], check=True)
        loop_at = max(0.0, dur - LOOP_SECONDS)
        inputs = ["-i", video, "-f", "lavfi", "-i", f"color=c={BAR_RGB}:s=1080x{BAR_H}:r=30",
                  "-loop", "1", "-i", first]
        graph = (
            # dark track under the bar so it reads on bright footage too
            f"[0:v]drawbox=x=0:y=0:w=iw:h={BAR_H}:color=black@0.45:t=fill[tr];"
            f"[tr][1:v]overlay=x='-w+w*t/{dur:.3f}':y=0:eval=frame:shortest=1[pb];"
            f"[2:v]format=rgba,fade=t=in:st={loop_at:.3f}:d={LOOP_SECONDS}:alpha=1,"
            f"trim=duration={dur:.3f}[lp];"
            "[pb][lp]overlay=0:0:shortest=1,format=yuv420p[v];"
            f"[0:a]{loud},afade=t=out:st={max(0.0, dur - 0.3):.3f}:d=0.3[a]"
        )
        vmap = ["-map", "[v]"]
        vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, *vmap, "-map", "[a]",
           "-t", f"{dur:.3f}", *vcodec, "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
           "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    if abs(ffprobe_duration(out) - dur) > 0.5:
        raise RuntimeError(f"finished video is {ffprobe_duration(out):.2f}s, expected {dur:.2f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", default="build/final.mp4")
    ap.add_argument("--long", action="store_true", help="Long-form: loudness only (YouTube draws its own bar)")
    args = ap.parse_args()
    tmp = os.path.splitext(args.video)[0] + "_finished.mp4"
    try:
        finish(args.video, tmp, args.long)
        shutil.move(tmp, args.video)
        print(f"[finish_video] finished {args.video} "
              f"({'loudness' if args.long else 'progress bar, loop ending, loudness'})")
    except Exception as exc:  # noqa: BLE001 -- polish must never cost the day's post
        print(f"::warning::finishing pass failed ({exc}) -- posting the unfinished video")
        if os.path.exists(tmp):
            os.remove(tmp)
    finally:
        first = os.path.splitext(tmp)[0] + "_first.png"  # finish() writes it next to tmp
        if os.path.exists(first):
            os.remove(first)
    return 0


if __name__ == "__main__":
    sys.exit(main())
