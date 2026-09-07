#!/usr/bin/env python3
"""
Stage 5: assemble the final 1080x1920 vertical short with ffmpeg.

Pipeline (single ffmpeg invocation with -filter_complex):
  1. Take the archive.org footage, scale-to-cover and crop to 1080x1920 (9:16),
     normalize to 30fps / yuv420p. Footage is looped (-stream_loop -1) and the
     output is cut to the voiceover length, so short clips still fill the video.
  2. Burn in a hook title card (drawtext) for the first few seconds.
  3. Burn in the animated captions from the SRT (subtitles filter).
  4. Burn in a small end-card CTA/watermark for the last few seconds.
  5. Mux with the TTS voiceover; drop the original footage audio.

ffmpeg is preinstalled on GitHub Actions Ubuntu runners.

# VERIFY: font paths. On ubuntu-latest the DejaVu fonts live at
#   /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
#   /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
# If drawtext errors with "Cannot find a valid font", run `fc-list | grep -i dejavu`
# in CI to find the real path, or `sudo apt-get install -y fonts-dejavu-core`.

Output: build/final.mp4

Usage:
    python scripts/assemble_video.py
    python scripts/assemble_video.py --footage build/footage.mp4 --audio build/voice.wav \
        --captions build/captions.srt --script build/script.json --out build/final.mp4
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

def _find_font(candidates):
    """First existing path from candidates, or the first candidate (let ffmpeg error
    with a clear message) if none exist. Lets this run on the Ubuntu CI runner (where
    the DejaVu path is always first and always exists) and also on a local Windows/macOS
    dev machine for quick manual testing, without changing CI behavior.

    A Windows absolute path (C:/...) breaks ffmpeg's filtergraph parser — the drive-letter
    colon can't be escaped there (unlike drawtext's text= option, backslash-escaping a
    colon inside fontfile= isn't honored by this parser). So when we fall back to a
    Windows font, copy it once into a relative-path cache dir and use that instead. This
    branch never runs in CI, where the first (Linux, no drive letter) candidate matches."""
    for path in candidates:
        if os.path.exists(path):
            if ":" in path:
                cache_dir = ".font_cache"
                os.makedirs(cache_dir, exist_ok=True)
                cached = os.path.join(cache_dir, os.path.basename(path))
                if not os.path.exists(cached):
                    shutil.copyfile(path, cached)
                return cached
            return path
    return candidates[0]


FONT_REGULAR = _find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
])
FONT_BOLD = _find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
])

MUSIC_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")


def pick_music(music_dir):
    """Return a background-music file from music_dir, rotated by date, or None if the
    folder is missing/empty. Music is optional — no file just means no background bed."""
    if not music_dir or not os.path.isdir(music_dir):
        return None
    tracks = sorted(f for f in os.listdir(music_dir)
                    if f.lower().endswith(MUSIC_EXTS))
    if not tracks:
        return None
    idx = datetime.date.today().timetuple().tm_yday % len(tracks)
    return os.path.join(music_dir, tracks[idx])


def build_audio_filter(has_music, duration, music_vol):
    """Audio graph: clean up the TTS voice (denoise + high-pass + loudness-normalize),
    and if a music track is present, duck it low and mix it under the voice.

    afftdn removes the faint hiss/"old radio" noise between words; loudnorm gives a
    consistent, clear speech level."""
    voice = "[1:a]afftdn=nr=12,highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11"
    if not has_music:
        return voice + "[aout]"
    fade_out = max(0.0, duration - 2.0)
    return (
        voice + "[va];"
        f"[2:a]volume={music_vol},afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={fade_out:.2f}:d=2[mus];"
        # duration=longest so the music plays the FULL video length (incl. the tail
        # after the voice ends) — duration=first cut the audio off at the voice length,
        # leaving a silent tail and an effectively inaudible bed. normalize=0 keeps the
        # voice at full level instead of amix halving both inputs. The outer -t caps it.
        "[va][mus]amix=inputs=2:duration=longest:normalize=0[aout]"
    )


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}: {out.stderr}")
    return float(out.stdout.strip())


def drawtext_escape(text):
    """Escape characters special to ffmpeg's drawtext text= option."""
    return (text.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\\'")
                .replace("%", "\\%"))


def fontfile_escape(path):
    """Escape a fontfile= path for ffmpeg's filtergraph syntax. Colons separate filter
    options, so a Windows drive letter (C:/...) must be escaped; Linux paths have no
    colon so this is a no-op there."""
    return path.replace("\\", "/").replace(":", "\\:")


def build_filter_complex(hook, cta, captions_path, duration):
    hook_e = drawtext_escape(hook)
    cta_e = drawtext_escape(cta)
    font_bold = fontfile_escape(FONT_BOLD)
    # subtitles filter path: colons/backslashes would need escaping on Windows,
    # but CI runs on Linux with a simple relative path.
    subs = captions_path.replace("\\", "/")
    hook_end = 4.0
    cta_start = max(0.0, duration - 4.0)

    # Alignment=2 is bottom-centre; MarginV is the gap from the bottom, measured in
    # libass's default 288px canvas (then scaled to the real 1920 height, ~6.67x). So
    # MarginV=144 (= half of 288) lands the captions in the VERTICAL MIDDLE of the frame.
    # The old value 280 pushed them ~6.67x past that, jamming them at the very top.
    caption_style = (
        "FontName=DejaVu Sans,Fontsize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        "Alignment=2,MarginV=144"
    )
    # VERIFY: force_style keys are ASS style names (case-sensitive-ish). If captions
    # look unstyled, check the ffmpeg build supports libass (`ffmpeg -filters | grep subtitles`).

    parts = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,fps=30,format=yuv420p[base]",

        # hook title card (top third), bold, semi-transparent box
        f"[base]drawtext=fontfile={font_bold}:text='{hook_e}':"
        "fontcolor=white:fontsize=54:line_spacing=8:"
        "box=1:boxcolor=black@0.5:boxborderw=24:"
        f"x=(w-text_w)/2:y=h*0.14:enable='between(t,0,{hook_end})'[v1]",

        # burned-in captions
        f"[v1]subtitles='{subs}':force_style='{caption_style}'[v2]",

        # end-card CTA / watermark (bottom)
        f"[v2]drawtext=fontfile={font_bold}:text='{cta_e}':"
        "fontcolor=white:fontsize=44:"
        "box=1:boxcolor=black@0.55:boxborderw=20:"
        f"x=(w-text_w)/2:y=h*0.86:enable='gte(t,{cta_start:.2f})'[vout]",
    ]
    return ";".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--footage", default="build/footage.mp4")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--captions", default="build/captions.srt")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--out", default="build/final.mp4")
    ap.add_argument("--music", default="build/music.mp3",
                    help="Explicit music file (e.g. the generated ambient pad); takes "
                         "precedence over --music-dir when it exists.")
    ap.add_argument("--music-dir", default="assets/music",
                    help="Folder of background-music tracks (optional; picks one by date).")
    ap.add_argument("--music-volume", type=float, default=0.30,
                    help="Background music level, 0..1 (voice stays at full).")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)["video"]

    hook = script.get("hook", "STAY STRONG")
    cta = cfg.get("cta_text", "Follow for daily wisdom")

    duration = ffprobe_duration(args.audio)
    # clamp to configured bounds (in case an excerpt is unusually long/short)
    duration = max(cfg["min_seconds"], min(duration + 0.6, cfg["max_seconds"]))
    print(f"[assemble_video] target duration {duration:.1f}s")

    # Prefer a real track dropped in --music-dir; otherwise use the generated ambient
    # pad (--music, built by generate_music.py). So adding real music later just works.
    folder_track = pick_music(args.music_dir)
    if folder_track:
        music_path = folder_track
    elif args.music and os.path.exists(args.music):
        music_path = args.music
    else:
        music_path = None
    if music_path:
        print(f"[assemble_video] background music: {music_path}")
    else:
        print(f"[assemble_video] no music found in {args.music_dir!r} — voice only")

    video_fc = build_filter_complex(hook, cta, args.captions, duration)
    audio_fc = build_audio_filter(bool(music_path), duration, args.music_volume)
    filter_complex = video_fc + ";" + audio_fc

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", args.footage,   # input 0: loop footage to cover audio
        "-i", args.audio,                            # input 1: TTS voice
    ]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]  # input 2: looped background music
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        args.out,
    ]
    print("[assemble_video] running ffmpeg...")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("ERROR: ffmpeg assembly failed.", file=sys.stderr)
        sys.exit(proc.returncode)
    print(f"[assemble_video] wrote {args.out}")


if __name__ == "__main__":
    main()
