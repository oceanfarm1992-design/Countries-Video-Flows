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
import glob
import json
import os
import re
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


def build_audio_filter(has_music, duration, music_vol, voice_idx=1, music_idx=2):
    """Audio graph: clean up the TTS voice (denoise + high-pass + loudness-normalize),
    and if a music track is present, duck it low and mix it under the voice.

    voice_idx/music_idx are the ffmpeg input indices — they shift because the per-segment
    montage adds one video input per segment ahead of the audio inputs.

    afftdn removes the faint hiss/"old radio" noise between words; loudnorm gives a
    consistent, clear speech level."""
    voice = f"[{voice_idx}:a]afftdn=nr=12,highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11"
    if not has_music:
        return voice + "[aout]"
    fade_out = max(0.0, duration - 2.0)
    return (
        voice + "[va];"
        f"[{music_idx}:a]volume={music_vol},afade=t=in:st=0:d=1.5,"
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
    """Escape characters special to ffmpeg's drawtext text= option.

    An ASCII apostrophe is the nasty one: our text is wrapped in single quotes inside a
    single -filter_complex argument, and there is no reliable backslash escape for a '
    there — it terminates the quote and corrupts the whole filtergraph (this crashed
    countries like Romania's "EUROPE'S ..." hook). Swapping it for a typographic
    apostrophe (’) sidesteps the quoting entirely and still reads correctly on screen."""
    return (text.replace("\\", "\\\\")
                .replace("'", "’")
                .replace(":", "\\:")
                .replace("%", "\\%"))


def fontfile_escape(path):
    """Escape a fontfile= path for ffmpeg's filtergraph syntax. Colons separate filter
    options, so a Windows drive letter (C:/...) must be escaped; Linux paths have no
    colon so this is a no-op there."""
    return path.replace("\\", "/").replace(":", "\\:")


def build_video_filter(pieces, hook, cta, captions_path, duration):
    """Build the video filtergraph for a montage of per-segment pieces (videos AND photos).

    Inputs 0..N-1 are the pieces in order. A video piece is trimmed to its beat's spoken
    duration; a photo piece gets a slow Ken Burns zoom (zoompan) over that duration so it
    feels alive rather than static. All are concatenated so the imagery changes in step
    with the narration. Hook card, lower-third captions and the end CTA are burned on top.

    `pieces` is a list of {"type": "video"|"photo", "dur": seconds} in input order."""
    hook_e = drawtext_escape(hook)
    cta_e = drawtext_escape(cta)
    font_bold = fontfile_escape(FONT_BOLD)
    subs = captions_path.replace("\\", "/")
    hook_end = 4.0
    cta_start = max(0.0, duration - 4.0)

    # Lower-third captions: Alignment=2 (bottom-centre); MarginV is measured up from the
    # bottom in libass's 288px canvas (~6.67x -> real 1920). MarginV=90 lands them around
    # 69% down the frame — off the subject's face (the old MarginV=144 sat dead-centre),
    # and clear of the CTA card at the very bottom.
    caption_style = (
        "FontName=DejaVu Sans,Fontsize=18,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
        "Alignment=2,MarginV=90"
    )

    parts = []
    labels = []
    for i, p in enumerate(pieces):
        d = p["dur"]
        if p["type"] == "photo":
            frames = max(2, int(round(d * 30)))
            # pre-scale to 1.5x for zoom headroom, then a slow push-in Ken Burns
            parts.append(
                f"[{i}:v]scale=1620:2880:force_original_aspect_ratio=increase,"
                f"crop=1620:2880,setsar=1,"
                f"zoompan=z='min(zoom+0.0009,1.25)':d={frames}:fps=30:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,"
                f"trim=duration={d:.3f},setpts=PTS-STARTPTS,format=yuv420p[c{i}]"
            )
        else:
            parts.append(
                f"[{i}:v]trim=duration={d:.3f},setpts=PTS-STARTPTS,"
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,setsar=1,fps=30,format=yuv420p[c{i}]"
            )
        labels.append(f"[c{i}]")
    parts.append("".join(labels) + f"concat=n={len(pieces)}:v=1:a=0[base]")

    # hook title card (top third), bold, semi-transparent box
    parts.append(
        f"[base]drawtext=fontfile={font_bold}:text='{hook_e}':"
        "fontcolor=white:fontsize=54:line_spacing=8:"
        "box=1:boxcolor=black@0.5:boxborderw=24:"
        f"x=(w-text_w)/2:y=h*0.14:enable='between(t,0,{hook_end})'[v1]"
    )
    # burned-in lower-third captions
    parts.append(f"[v1]subtitles='{subs}':force_style='{caption_style}'[v2]")
    # end-card CTA / watermark (bottom)
    parts.append(
        f"[v2]drawtext=fontfile={font_bold}:text='{cta_e}':"
        "fontcolor=white:fontsize=44:"
        "box=1:boxcolor=black@0.55:boxborderw=20:"
        f"x=(w-text_w)/2:y=h*0.86:enable='gte(t,{cta_start:.2f})'[vout]"
    )
    return ";".join(parts)


def find_segment_pieces(build_dir):
    """Return the per-segment media pieces as [(path, type), ...] in order.

    Prefers build/footage.json (which records each piece's type: video or photo); falls
    back to globbing footage_clip*.mp4/.jpg if the manifest is missing."""
    manifest = os.path.join(build_dir, "footage.json")
    if os.path.exists(manifest):
        try:
            with open(manifest, encoding="utf-8") as f:
                clips = json.load(f).get("clips", [])
            pieces = []
            for c in clips:
                p = c.get("path")
                if p:
                    pieces.append((os.path.join(build_dir, p), c.get("type", "video")))
            if pieces:
                return pieces
        except Exception:  # noqa: BLE001 — fall back to globbing
            pass

    found = glob.glob(os.path.join(build_dir, "footage_clip*.mp4")) + \
        glob.glob(os.path.join(build_dir, "footage_clip*.jpg"))

    def idx(path):
        m = re.search(r"footage_clip(\d+)\.", path)
        return int(m.group(1)) if m else 0

    return [(p, "photo" if p.endswith(".jpg") else "video") for p in sorted(found, key=idx)]


def segment_durations(script, total):
    """Split `total` seconds across the narration segments in proportion to how many
    words each one has, so each clip is on screen for exactly as long as its words are
    spoken."""
    segs = script.get("segments") or []
    words = [max(1, s.get("words", len(s.get("text", "").split()))) for s in segs]
    tw = sum(words) or 1
    return [total * w / tw for w in words]


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
    ap.add_argument("--intro", default="build/intro.mp4",
                    help="Optional globe-zoom intro to crossfade in front of the montage. "
                         "Ignored if the file doesn't exist.")
    ap.add_argument("--intro-xfade", type=float, default=0.8,
                    help="Crossfade duration (s) between the intro and the montage.")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)["video"]

    hook = script.get("hook", "STAY STRONG")
    cta = cfg.get("cta_text", "Follow for daily wisdom")

    audio_dur = ffprobe_duration(args.audio)
    # Footage should track the voice exactly, so the total is the voice length (+ a small
    # tail so the last word/CTA has room to breathe), clamped to the configured bounds.
    duration = max(cfg["min_seconds"], min(audio_dur + 0.4, cfg["max_seconds"]))
    print(f"[assemble_video] voice {audio_dur:.1f}s -> target duration {duration:.1f}s")

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

    # Per-segment montage: one media piece (video or photo) per narration beat, each shown
    # for the time its words take to speak. Falls back to the single looped footage.mp4 if
    # no per-segment pieces are present (older layout).
    found_pieces = find_segment_pieces(os.path.dirname(args.out) or ".")
    segs = script.get("segments") or []
    if found_pieces and segs:
        durs = segment_durations(script, duration)
        # map piece i to beat i; if fewer pieces than beats (some failed), cycle through
        # what we have so every beat still gets a visual
        piece_inputs = [found_pieces[i % len(found_pieces)] for i in range(len(durs))]
        pieces = [{"type": t, "dur": durs[i]} for i, (_, t) in enumerate(piece_inputs)]
        n_photo = sum(1 for p in pieces if p["type"] == "photo")
        print(f"[assemble_video] {len(pieces)} synced pieces "
              f"({len(pieces) - n_photo} video, {n_photo} photo)")
    else:
        # legacy single-clip path
        durs = [duration]
        piece_inputs = [(args.footage, "video")]
        pieces = [{"type": "video", "dur": duration}]
        print("[assemble_video] no per-segment pieces — single looped footage")

    video_fc = build_video_filter(pieces, hook, cta, args.captions, duration)
    voice_idx = len(piece_inputs)
    music_idx = voice_idx + 1
    audio_fc = build_audio_filter(bool(music_path), duration, args.music_volume,
                                  voice_idx, music_idx)
    filter_complex = video_fc + ";" + audio_fc

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    use_intro = bool(args.intro) and os.path.exists(args.intro)
    # When an intro will be crossfaded in front, render the montage to a temp file first.
    montage_out = (os.path.splitext(args.out)[0] + ".montage.mp4") if use_intro else args.out

    cmd = ["ffmpeg", "-y"]
    for path, ptype in piece_inputs:
        if ptype == "photo":
            cmd += ["-loop", "1", "-i", path]          # still image, framed by zoompan
        else:
            cmd += ["-stream_loop", "-1", "-i", path]  # video loops to fill its slot
    cmd += ["-i", args.audio]                            # voice (input voice_idx)
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]  # music (input music_idx)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        montage_out,
    ]
    print("[assemble_video] running ffmpeg...")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("ERROR: ffmpeg assembly failed.", file=sys.stderr)
        sys.exit(proc.returncode)

    if use_intro:
        intro_voice = os.path.join(os.path.dirname(args.out) or ".", "intro_voice.wav")
        prepend_intro(args.intro, montage_out, args.out, args.intro_xfade,
                      intro_voice if os.path.exists(intro_voice) else None)
        os.remove(montage_out)
    print(f"[assemble_video] wrote {args.out}")


def prepend_intro(intro, montage, out, xfade, intro_voice=None):
    """Crossfade the globe intro into the montage. The montage's audio is delayed so the
    main voice starts once the intro finishes; if an intro voiceover is given ("Today we
    travel to X"), it plays over the globe so the opening isn't silent."""
    intro_dur = ffprobe_duration(intro)
    voffset = max(0.0, intro_dur - xfade)     # when the video crossfade begins
    delay_ms = int(intro_dur * 1000)          # main voice/music start after the intro

    inputs = ["-i", intro, "-i", montage]
    if intro_voice:
        inputs += ["-i", intro_voice]

    vfc = (
        f"[0:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p,settb=AVTB[iv];"
        f"[1:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p,settb=AVTB[mv];"
        f"[iv][mv]xfade=transition=fade:duration={xfade}:offset={voffset:.3f}[v]"
    )
    if intro_voice:
        # intro VO starts ~0.3s in (over the globe), montage audio delayed to the intro end
        afc = (
            f"[2:a]adelay=300|300,loudnorm=I=-16:TP=-1.5:LRA=11[iva];"
            f"[1:a]adelay={delay_ms}|{delay_ms}[mva];"
            f"[iva][mva]amix=inputs=2:duration=longest:normalize=0[a]"
        )
    else:
        afc = f"[1:a]adelay={delay_ms}|{delay_ms}[a]"

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", vfc + ";" + afc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", out,
    ]
    print(f"[assemble_video] crossfading intro ({intro_dur:.1f}s"
          f"{', with voiceover' if intro_voice else ''}) into montage...")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise SystemExit(f"intro crossfade failed ({proc.returncode})")


if __name__ == "__main__":
    main()
