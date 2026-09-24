# Countries Video Flows

A fully automated pipeline that builds vertical (9:16) country-themed Shorts and posts
them to **YouTube**, **Facebook** and **TikTok** every day. Everything runs on GitHub
Actions. Narration uses the channel owner's cloned voice (StyleTTS2).

## Series

| Workflow | Series | Posts/day |
|---|---|---|
| `daily-rotating-short.yml` | Rotates 5 single-country series: `country`, `hook`, `trending`, `geography`, `worlddata` (2 per day, pairs set by `scripts/daily_variation.py`) | 2 |
| `daily-comparison-short.yml` | Country vs country, real World Bank stats | 2 |
| `daily-rankings-short.yml` | Top-8 countdown of one metric (live World Bank data + curated indices in `config/rankings_static.json`) | 1 |
| `ci.yml` | Installs pinned deps and runs `tests/`; gates Dependabot auto-merge | - |

## Schedule

Each video workflow ticks hourly, but GitHub drops or delays many scheduled runs, so the
gate step posts a slot once its target hour has passed and it isn't posted yet. Each slot
has its own target-hour lane (Dubai time): comparison A 04-07, rankings 08-11,
comparison B 12-14, rotating A 15-18, rotating B 19-22. `scripts/post_gate.py` also
requires at least 100 minutes between any two posts, waits while another video workflow
is mid-build, and blocks posting from 23:50 to 01:00 Dubai.

## Pipeline

1. `generate_*_script.py`: narration and on-screen beats. GPT-4o-mini writes it,
   `fact_check.py` reviews it (against the real data for comparison/rankings), and a
   template built only from fetched numbers is used if the check fails.
2. `fetch_footage.py` (Pexels, then Pixabay, then generated animation) or a series
   renderer (`render_geography_map.py`, `render_worlddata_graphics.py`,
   `render_comparison_graphics.py`, `render_rankings_graphics.py`).
3. `generate_tts.py`: StyleTTS2 cloned voice, falling back to Kokoro, then Piper.
4. `generate_captions.py`, `generate_music.py`, `generate_intro.py`, `assemble_video.py`.
5. The video is uploaded as a GitHub Release asset (its public URL is what Buffer posts
   from, so the repo must stay public), then `post_buffer.py` (Facebook + TikTok) and
   `post_youtube.py`. If any platform fails, the run is marked failed.
6. A row is appended to `logs/history_<series>.csv`:
   date, title, identifier, Buffer outcome, YouTube outcome.

Numbers are never invented: every figure comes from a live fetch or a curated snapshot
with a named source. Ties use competition ranking, and stale live data is dropped.

## Required secrets

| Secret | Used for |
|---|---|
| `OPENAI_API_KEY` | Narration, fact-check, captions |
| `VOICE_REPO_PAT` | Fetching the private voice reference sample |
| `BUFFER_API_KEY`, `BUFFER_ORG_ID`, `BUFFER_FACEBOOK_CHANNEL_ID`, `BUFFER_TIKTOK_CHANNEL_ID` | Facebook + TikTok posting |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | YouTube upload |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Stock footage (optional) |
| `NEWS_API_KEY` | Headlines for the trending series |

## Manual runs

Every workflow has `workflow_dispatch`. `dry_run` defaults to **true** (builds the video
and uploads it as an artifact, posts nothing). `force_run` skips the hour gate. The
rotating workflow also accepts `series` and `country_index`; comparison accepts
`index_a`/`index_b`; rankings accepts `metric_id`.

## Local development

```bash
pip install torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt pytest
python -m pytest tests          # offline smoke tests
python scripts/fetch_rankings_stats.py hdi --top 8
python scripts/daily_variation.py   # upcoming rotating slots
```

Output of a local build lands in `build/` (`build/final.mp4`).

## Maintenance

- `config/rankings_static.json` is curated by hand. Re-pull the published indices yearly;
  runs log a warning once `_last_refreshed` is over 400 days old.
- `config/rankings_metrics.json` and `config/countries.json` drive date-based rotations,
  so only append to them. Appending still moves the hook and geography offsets by one and
  reshuffles the comparison pairs (the schedules depend on the list length); inserting or
  reordering shifts every series much more.
- `scripts/build_geo_assets.py` regenerates the geography map masks offline whenever a
  country is added.
