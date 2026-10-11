#!/usr/bin/env python3
"""
Sound-effects track for footage-based Shorts: a soft whoosh at every scene cut,
timed to the same segment durations assemble_video.py cuts the montage on.

Output: build/sfx.wav (mixed in by assemble_video.py --sfx)

Usage:
    python scripts/make_transition_sfx.py --config config/countries.json
"""
import argparse
import json

import numpy as np

import motion_kit as mk
from pipeline_common import segment_durations
from render_geography_map import resolve_duration


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/countries.json")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--out", default="build/sfx.wav")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        video_cfg = json.load(fh)["video"]
    duration, _ = resolve_duration(script, video_cfg, args.audio)
    durs = segment_durations(script, duration) if script.get("segments") else [duration]
    whoosh = mk.sfx_whoosh(0.55) * 0.55
    events, t = [], 0.0
    for d in durs[:-1]:
        t += d
        events.append((max(0.0, t - 0.28), whoosh))  # peaks right on the cut
    events.append((max(0.0, duration - 3.9), mk.sfx_pop() * 0.8))  # end card lands
    mk.write_sfx_track(args.out, duration, events)
    print(f"[make_transition_sfx] {len(events)} effects over {duration:.1f}s -> {args.out}")


if __name__ == "__main__":
    np.random.seed(0)
    main()
