#!/usr/bin/env python3
"""
Small helpers shared across pipeline stages that would otherwise need to
duplicate the same math and risk drifting out of sync with each other.
"""


def segment_durations(script, total):
    """Split `total` seconds across the narration segments in proportion to how many
    words each one has, so each clip is on screen for exactly as long as its words are
    spoken."""
    segs = script.get("segments") or []
    words = [max(1, s.get("words", len(s.get("text", "").split()))) for s in segs]
    tw = sum(words) or 1
    return [total * w / tw for w in words]
