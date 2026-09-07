# Background music

Drop **royalty-free / open-license** music tracks in this folder (`.mp3`, `.m4a`, `.wav`,
`.ogg`, `.flac`). The assemble stage (`scripts/assemble_video.py`) picks one **by date**
(so it rotates), ducks it low (default 10% volume — see `--music-volume`), fades it in/out,
and mixes it **under** the voiceover. The voice stays at full, clear level.

**Optional:** if this folder is empty, videos are simply built **voice-only** — no error.

## Where to get free, license-safe tracks (no attribution needed)

- **Pixabay Music** — https://pixabay.com/music/ (Pixabay Content License, free for commercial
  use, no attribution). Download a few calm / ambient / cinematic tracks and drop them here.
- **YouTube Audio Library** — free tracks marked "No attribution required".
- **Free Music Archive** — https://freemusicarchive.org (filter to CC0 to avoid attribution).

If you use a **CC-BY** track (e.g. incompetech.com / Kevin MacLeod), you must add the
required attribution to the video description — prefer CC0 / Pixabay to keep it hands-off.

## Tips
- Pick **calm, low-key, instrumental** tracks — anything with vocals or a strong beat
  fights the spoken quote.
- 2–4 tracks is plenty; the pipeline rotates through them automatically.
- Length doesn't matter — the pipeline loops/cuts the track to the video length.
