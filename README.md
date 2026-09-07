# yt-shorts-generator

A **zero-cost, fully automated daily pipeline** that builds one ~30-50s vertical (9:16)
motivational short from **public-domain sources** and posts it to **Instagram Reels,
Facebook, and YouTube Shorts**. Everything runs on the **GitHub Actions free
tier** — no paid infrastructure, no YouTube ripping.

Posting is handed off to **Zapier via a shared Google Sheet** rather than calling each
platform's API with tokens directly. The pipeline appends one row per video to a Google
Sheet; a free Zapier "New Spreadsheet Row" trigger (one Zap per platform) picks it up and
posts using Zapier's own already-verified connections. This sidesteps the token churn that
kept breaking direct posting (Meta long-lived token re-exchange, TikTok's rotating refresh
token, YouTube OAuth verification). **TikTok is not posted** — its app isn't audited and
there's no free Zapier TikTok posting integration.

## How it works

Each day a GitHub Actions cron job runs these stages in order:

| Stage | Script | What it does |
|-------|--------|--------------|
| 1 | `fetch_script_text.py` | Picks a public-domain excerpt (Marcus Aurelius / Emerson / Seneca) from `config/sources.json`, rotating by date. Wraps it with a short spoken intro + reflective outro so the narration runs ~40s (not an abrupt ~20s). Also writes per-platform caption files. |
| 2 | `fetch_footage.py` | Fetches a **theme-matched, HD** B-roll clip. Tries **Pexels → Pixabay**, searching by the quote's `footage_query` so the footage is relevant. If no suitable clip is found, falls back to `generate_animation.py` — a **generated cinematic gradient** (always on-tone, never random). archive.org NASA footage is still available but off by default. |
| 3 | `generate_tts.py` | Generates the voiceover with **Piper TTS** (offline, no API key), default voice `en_US-amy-medium` (natural). Falls back to `espeak-ng` if Piper fails. |
| 4 | `generate_captions.py` | Builds a burned-in `.srt` from the known script text + measured audio duration (no transcription needed). |
| 5a | `generate_music.py` | Synthesizes a soft **ambient music pad** with ffmpeg (`build/music.mp3`) — no assets needed. Skipped in favour of real tracks if you drop any in `assets/music/`. |
| 5b | `assemble_video.py` | ffmpeg: crop/pad footage to 1080x1920, burn in **centre-screen** captions + a hook title card + end-card CTA; **denoise + loudness-normalize** the voice, and mix the **background music** under it. |
| 6 | `post_sheet.py` | Appends one row (`title \| description \| hashtags \| caption \| video_url \| category`) to the shared Google Sheet. Zapier posts to Instagram / Facebook / YouTube from there. |
| 7 | workflow step | Appends a row to `logs/history.csv` and commits it back. |

The workflow is `.github/workflows/daily-short.yml`. It runs **daily at 14:00 UTC**
(`build_and_post`) and is also runnable on demand via **workflow_dispatch**. There is no
longer a token-refresh job — posting auth lives in Zapier, not in this repo.

> The direct-API posters (`post_meta.py`, `post_tiktok.py`, `post_youtube.py`,
> `refresh_meta_token.py`) are kept in `scripts/` for reference but are **no longer wired
> into the workflow**. Re-wire `post_tiktok.py` once the TikTok app passes audit.

### One-time setup (no Google Cloud needed)

After this ~15-minute setup the pipeline is **fully autonomous** — no tokens to refresh,
nothing to touch daily.

1. Create a Google Sheet with a header row: `title | description | hashtags | caption | video_url | category`.
2. **Deploy the sheet web app (no Cloud project, no key file):** in that sheet, open
   **Extensions → Apps Script**, paste `scripts/sheet_webhook.gs`, then
   **Deploy → New deployment → Web app** (Execute as: *Me*, Who has access: *Anyone*).
   Copy the resulting `…/exec` URL into the `SHEET_WEBHOOK_URL` secret. Optionally set a
   random token in both the script and the `SHEET_WEBHOOK_TOKEN` secret.
3. In Zapier, create one Zap per platform: trigger **Google Sheets → New Spreadsheet Row**
   on this sheet; action **Instagram / Facebook / YouTube → post video**, mapping the
   `video_url` and `caption` columns. Zapier's free tier (~100 tasks/month) covers a
   once-daily post to three platforms (~90/month).

## Content sourcing

- **Text:** Project Gutenberg public-domain excerpts, curated in `config/sources.json`.
- **Video:** theme-matched HD stock from **Pexels** or **Pixabay** (free licenses, free
  commercial use), searched by each quote's `footage_query`. When no suitable stock clip
  is found, `generate_animation.py` renders an on-tone **animated gradient** background
  (ffmpeg, no assets/network) instead of dropping in a random/irrelevant clip — this is
  the guaranteed fallback and never fails. archive.org **NASA** public-domain footage is
  still available (`--source archive`, or add `"archive"` back to `footage.source_order`)
  but is off by default. The `prelinger` collection was removed (off-tone, low-res).
- **Voice:** Piper TTS — open-source, offline, CI-friendly.
- **No copyrighted material is downloaded or reused.** (Pexels/Pixabay clips are free-to-
  use under their own licenses; NASA footage is public domain.)

## Required GitHub Secrets

Create these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `SHEET_WEBHOOK_URL` | `post_sheet.py` | Apps Script web app URL (…/exec) that appends the row to the sheet. See `scripts/sheet_webhook.gs`. No Google Cloud needed. |
| `SHEET_WEBHOOK_TOKEN` | `post_sheet.py` | *Optional.* Shared secret; must match the token in `sheet_webhook.gs` so only your pipeline can write. |
| `PEXELS_API_KEY` | `fetch_footage.py` | *Optional.* Free key from https://www.pexels.com/api/ for HD theme-matched footage (tried first). |
| `PIXABAY_API_KEY` | `fetch_footage.py` | *Optional.* Free key from https://pixabay.com/api/docs/ (tried second). |

Footage degrades gracefully: with **no** stock key set, `fetch_footage.py` falls back to
free archive.org NASA footage automatically. Set at least one stock key for the best
quality + relevance.

`GITHUB_TOKEN` (built-in) is used to create the release asset and commit the log — no
setup needed. The old per-platform token secrets (`META_*`, `TIKTOK_*`, `YOUTUBE_*`,
`GH_PAT`) are **no longer used** and can be deleted once you've confirmed the Zapier path
works.

## Posting notes

- **Public video URL still required.** Zapier's Instagram/Facebook/YouTube actions post
  from the `video_url` column, so the workflow still uploads `final.mp4` as a GitHub
  **Release asset** and writes that public URL into the sheet row. **This only works if
  the repo is PUBLIC**; for a private repo, host the mp4 elsewhere and set the URL there.
- **No tokens in the repo.** All platform authentication now lives inside the Zaps
  (Zapier's own connections), which is why the token-refresh job and all `META_*` /
  `TIKTOK_*` / `YOUTUBE_*` secrets are gone.
- **TikTok** is not posted — its app isn't audited and Zapier has no free TikTok
  content-posting integration. `scripts/post_tiktok.py` is retained for when that changes.

## Running / debugging locally

Every stage is independently runnable. Typical local dry-run:

```bash
pip install -r requirements.txt
sudo apt-get install -y ffmpeg fonts-dejavu-core espeak-ng

python scripts/fetch_script_text.py
python scripts/fetch_footage.py          # PEXELS_API_KEY/PIXABAY_API_KEY optional; falls back to a generated animation
# python scripts/fetch_footage.py --source animate   # force the generated gradient background
python scripts/generate_tts.py          # or: --fallback espeak
python scripts/generate_captions.py
python scripts/generate_music.py       # ambient pad -> build/music.mp3 (or drop tracks in assets/music/)
python scripts/assemble_video.py
# -> build/final.mp4
```

Posting now goes through `post_sheet.py` → Apps Script web app → Google Sheet → Zapier;
set `SHEET_WEBHOOK_URL` to test it. The old direct-API posters are unwired (see note above).

## Limitations / things to verify

- **Repo must be public** for the release-asset public-URL (which Zapier posts from) to work.
- **TikTok is not posted** — its app isn't audited and Zapier has no free TikTok posting
  integration. `scripts/post_tiktok.py` is retained for when that changes.
- CLI flags / paths marked with `# VERIFY:` comments (Piper CLI flags such as
  `--length_scale`/`--sentence_silence`, ffmpeg font paths) should be confirmed against the
  versions actually installed on the runner.
- The pipeline is intentionally simple (one JSON config, no framework) — it's a personal
  hobby pipeline, not enterprise software.
