# Kahwa News – automated newsroom & publisher

Fully unattended pipeline for the **Kahwa News** Facebook Page, running free on GitHub Actions.

1. `newsroom` (every 2h): reads 8 Arabic world-news outlets, clusters the same story, ranks it (outlet count, freshness, early-warning signals), keeps only stories confirmed by ≥2 outlets, filters restricted subjects, has an AI write an original Arabic post (copy-check, no links), verifies every claim against the source texts, renders a branded 1080×1350 card, queues it.
2. `publish` (every 20 min): posts the next due item with 40–60 min human-like spacing, failover Zernio → Buffer → Make, no duplicates, pauses everything 48h on any Facebook restriction (never retries those).
3. `reel` (12:41 and 18:41 Algeria): picks the top verified story, an AI writes a reel script (`story.json`, every number checked against the sources), fetches openly licensed archive footage/photos (Wikimedia Commons, NASA; PD/CC0/CC BY/CC BY-SA only, credited and labelled «لقطات أرشيفية»/«صورة أرشيفية», graphic-only fallback), renders it with HyperFrames 0.8.214 (style A explainer, style B if urgent) over our own music, verifies every on-screen line and the caption claim by claim, QA's it, hosts the MP4 and queues it as kind `reel`. While `PUBLISHING_ENABLED` is not `true` it only renders and uploads the MP4 as a workflow artifact.
4. `watchdog` (every 2h): opens a GitHub issue (= email) if nothing was published for hours or a provider lost authorization.

Kill switch: repository variable `PUBLISHING_ENABLED` (must be `true`) or a file `state/PAUSE`.
Hard content rules enforced in code: never Morocco / UAE / Algeria / Israel (incl. Arabic prefixed forms), no sports, no engagement bait, no AI-disclosure line, no AI images, no AI voice (reels are voice-free).

## Repository variables (Settings → Secrets and variables → Actions → Variables)

| Variable | Values | Effect |
| --- | --- | --- |
| `PUBLISHING_ENABLED` | `false` (now) / `true` | Master switch. Anything but `true` = render/test only, nothing is published. |
| `MUSIC_SOURCE` | `library` (default) / `synth` | One-line music switch for reels. `library` = our 16 ACE-Step 1.5 tracks in `assets/music`; `synth` = the code-synthesised bed in `kahwa_publisher/reels/audio.py` (no model, no samples). Unset/unknown = `library`. If the library is missing the renderer falls back to `synth` by itself. |
| `DAILY_CAP`, `ZERNIO_FB_ACCOUNT_ID`, `BUFFER_*` | | publisher settings (unchanged). |

Environment knobs (workflow `env:` or local): `SIGNALS=off` disables the early-warning ranking; `ZERNIO_STAGE_VIDEO=false` stops re-hosting reels on Zernio storage; `REELS_MEDIA_BASE` overrides the public MP4 base URL.

## Music (reels)

- `assets/music/*.mp3` + `music.json`: 16 original instrumental tracks (4 moods × 4: serious, calm, tension, light), generated for Kahwa News with ACE-Step 1.5 (MIT code + weights; model card: "You can strictly use the generated music for commercial purposes"), rendered on CPU in Actions run 40795738899. Not registered with any Content ID system. Provenance (seed, caption, model, runtime commit) per track in `music.json`.
- Mastering (`tools/music/process.py`): start/end trim to the first/last non-silent 50 ms window, 40 ms fade-in, 3 s fade-out, two-pass loudnorm to −16 LUFS, **true peak ≤ −2.0 dBTP measured on the final MP3** (the encode is repeated with the excess removed because LAME adds overshoot). QA rejects clipping, silence gaps, odd spectra and anything the ACE-Step "understand" pass hears as sung lyrics.
- Mood rules (`render.pick_music`): tragic story → `calm` (quietest, −21 LUFS bed); style B (urgent) → `tension`; otherwise the story's mood (`serious` / `light` / `calm`). Track choice inside a mood is a stable hash of the story id. The final reel mix is normalised to −14 LUFS.
- The generator workflow (`music-library.yml`) was removed after use (2026-10-29). To regenerate, restore it from git history (commit before `cc0c483`) and run `tools/music/gen.py` → `tools/music/process.py <raw_dir>`.

## Reel hosting (why GitHub Pages + Zernio storage)

- `raw.githubusercontent.com` serves MP4 as `application/octet-stream`; GitHub Release assets answer with a redirect to `release-assets.githubusercontent.com` (also octet-stream). Buffer's API has no upload endpoint and requires a *public, direct (no redirect), HTTPS, stable* file URL (developers.buffer.com/guides/hosting-media); Zernio infers the media type from the URL / a HEAD request and rejects generic types (docs.zernio.com/guides/media-uploads).
- Decision: branch `reels-media` is published with **GitHub Pages** → `https://kahouanews.github.io/kahwa-news-publisher/<file>.mp4`, a direct 200 with `Content-Type: video/mp4`. `tools/host_reel.sh` force-pushes an orphan commit (no history growth), keeps 5 days of files, and refuses to continue until Pages serves the exact file as `video/mp4`.
- At publish time Zernio additionally re-hosts the MP4 on its own storage (`POST /v1/media/presign` + `PUT`, `providers.Zernio.stage_video`), so Zernio never depends on our host's headers. If staging fails it falls back to the Pages URL. Buffer uses the Pages URL.
- `reel.yml` → *Run workflow* with `dry_run` + `host_test` hosts the test reel as `test-<file>.mp4`, prints its headers and probes Zernio staging (temporary storage, auto-deleted after 7 days; no post is created).

## Fixed named series (topic lanes)

| Series | Where | Tag | Caption line 1 |
| --- | --- | --- | --- |
| «فهم الخبر» | reel style A (explainer) | red kicker on the poster frame | `فهم الخبر \| <subject>` |
| «العالم في 30 ثانية» | reel style B (urgent) | in the «عاجل» band, above the date | `العالم في 30 ثانية \| <subject>` |
| «بالأرقام» | numbers card (2–4 verified figures) | card kicker | `بالأرقام \| <subject>` |

The subject line is plain and factual (who/what/where), never a question or a teaser; it goes through claim verification like every other line. Posts are never deleted (Meta's generative retrieval keeps surfacing new posts after the first hour).

## Early-warning signals (`kahwa_publisher/signals.py`)

Ranking only — signals never replace verification: a story is still written only when ≥2 of our outlets carry it (the tier-1 single-outlet exception is judged on the feeds-only score, so a boost can never let a single-source story through), and the banned-country / sports / restricted filters run first.

- **GDELT DOC 2.0** (`api.gdeltproject.org/api/v2/doc/doc`, no key, 15-min index, one request per 5 s): Arabic coverage of the last 3 h. Each cluster gets +0.5 per other news domain already running it (max +2.5). Stories seen on ≥4 other domains but on none of our feeds are logged as a *watch list* and never written.
- **Wikipedia pageviews** (`ar.wikipedia.org/w/api.php?prop=pageviews`, 50 titles per request, no key): names in the cluster's headlines that are real Arabic Wikipedia articles; spike = latest complete day vs the 21-day median (≥200 views and ≥2×) → +log2(ratio) (max +2). Data lags 1–2 days, so it lifts developing stories rather than breaking ones.
- Every call is best-effort with timeouts; a down or rate-limited source just means boost 0. The log line `{"signals": …}` shows base score, boosts and the watch list each run.

## Second verification layer (`kahwa_publisher/verify.py`, fail closed)

Loki / OpenFactVerification-style, using the same free Gemini models:
1. **Decompose** the generated post into atomic claims (each with a check-worthiness flag).
2. **Verify** each claim against the numbered source texts only; a "supported" verdict must quote its evidence verbatim, and code checks the quote really is in that source (normalised tokens, clitics stripped) and that every figure in the claim appears in the sources.
3. **Repair** once: unsupported/contradicted claims are dropped or rewritten from supported facts only, then the whole text is decomposed and verified again. Still unsupported → the story is dropped.
4. Card text (headline, title, subline, facts, numbers, quote) is verified separately without repair; reels verify every on-screen line plus the caption.
Any LLM/parse error, an empty claim list, or a remaining unsupported claim = nothing is queued. The repaired text re-passes every hard gate (banned countries, sports, bait, 7-gram copy check, structure).

## Fallback writer (if Gemini fails)

See "Fallback writer decision" below. Current behaviour: skip-on-failure — if every configured LLM fails, the run writes nothing and the next scheduled run tries again (the queue keeps the page supplied for a few hours).

## Testing without publishing

- `newsroom.yml` → *Run workflow* (`dry_run` default **true**): writes posts + cards + full verification reports to the artifact `newsroom-dry-<run id>`; `queue/`, `state/`, `media/` untouched.
- `reel.yml` → *Run workflow* (`dry_run` default **true**, optional `host_test`): renders + QA + verification, artifact `reel-<run id>`; nothing queued.
- `llm-fallback-bench.yml` (manual, one-off): measures open Arabic models on CPU with the real newsroom prompt.
- Local: `python -m pytest tests`; `python -m kahwa_publisher.newsroom --dry --out /tmp/kahwa-dry`.
