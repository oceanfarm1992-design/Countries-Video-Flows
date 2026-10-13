#!/usr/bin/env python3
"""
AI "editor" review: a few frames of the finished video go to a vision model, which
flags what an editor would reject -- overlapping or cut-off text, unreadable text,
broken or glitched graphics, an empty or black frame, footage that looks wrong for
the topic. Advisory only: findings show on the run page and in build/vision_review.json;
it never blocks a post (a model's judgement is not reliable enough to cancel one).

Cost: FRAMES low-detail images per video on gpt-4o-mini -- a fraction of a cent.

Usage:
    python scripts/vision_check.py --video build/final.mp4 --topic "Top 8 Most Corrupt Countries"
"""
import argparse
import base64
import json
import os
import subprocess
import sys

from pipeline_common import ffprobe_duration

FRAMES = 6
OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    pass


def sample_frames(video, n=FRAMES, width=432):
    dur = ffprobe_duration(video)
    frames = []
    for k in range(n):
        t = 0.8 + (dur - 1.6) * k / max(1, n - 1)
        jpg = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                              "-vf", f"scale={width}:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"],
                             capture_output=True, check=True).stdout
        frames.append((t, base64.b64encode(jpg).decode()))
    return frames


def review(video, topic, model="gpt-4o-mini"):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    frames = sample_frames(video)
    content = [{"type": "text", "text":
                f"These are {len(frames)} frames, in order, from a short vertical video about: {topic}. "
                "Review them as a strict video editor. Report ONLY real defects a viewer would notice: "
                "text overlapping other text, text cut off at the frame edge, text too small or low-contrast "
                "to read, broken/garbled/glitched graphics, an empty or black frame, footage that clearly "
                "doesn't match the topic. Do not comment on style or taste. Respond as JSON: "
                "{\"score\": 1-10 (10 = broadcast quality), \"issues\": [{\"frame\": index, \"issue\": \"...\"}]}"}]
    for t, b64 in frames:
        content.append({"type": "text", "text": f"Frame {frames.index((t, b64))} (t={t:.1f}s):"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
    r = client.chat.completions.create(model=model, max_tokens=500, temperature=0,
                                       response_format={"type": "json_object"},
                                       messages=[{"role": "user", "content": content}])
    result = json.loads(r.choices[0].message.content)
    result["frame_times"] = [round(t, 1) for t, _ in frames]
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", default="build/final.mp4")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--out", default="build/vision_review.json")
    args = ap.parse_args()
    if not (OPENAI_AVAILABLE and os.environ.get("OPENAI_API_KEY", "").strip()):
        print("[vision_check] no OpenAI -- skipped")
        return 0
    topic = "a country video"
    if os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as fh:
            s = json.load(fh)
        topic = s.get("title") or s.get("name") or topic
    try:
        result = review(args.video, topic)
    except Exception as exc:  # noqa: BLE001 -- advisory; never blocks the post
        print(f"[vision_check] review unavailable ({type(exc).__name__}: {exc})")
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    times = result.get("frame_times", [])
    for item in result.get("issues", []):
        i = item.get("frame")
        at = f" at {times[i]}s" if isinstance(i, int) and 0 <= i < len(times) else ""
        print(f"::warning::AI editor{at}: {item.get('issue')}")
    print(f"[vision_check] score {result.get('score')}/10, {len(result.get('issues', []))} issue(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
