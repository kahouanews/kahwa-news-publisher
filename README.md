# Kahwa News – automated newsroom & publisher

Fully unattended pipeline for the **Kahwa News** Facebook Page, running free on GitHub Actions.

1. `newsroom` (every 2h): reads 8 Arabic world-news outlets, clusters the same story, ranks it (outlet count, freshness, early-warning signals), keeps only stories confirmed by ≥2 outlets (internal check, never published), filters restricted subjects, has an AI write an original Arabic post (copy-check, no links) attributed to the facts' primary sources, verifies every claim and every attribution against the source texts, renders a branded 1080×1350 card, queues it.
2. `publish` (every 20 min): posts the next due item with 40–60 min human-like spacing, failover Zernio → Buffer → Make, no duplicates, pauses everything 48h on any Facebook restriction (never retries those).
3. `reel` (12:41 and 18:41 Algeria): picks the top verified story, an AI writes a reel script (`story.json`, every number checked against the sources), fetches openly licensed archive footage/photos (Wikimedia Commons, NASA; PD/CC0/CC BY/CC BY-SA only, credited and labelled «لقطات أرشيفية»/«صورة أرشيفية», graphic-only fallback), renders it with HyperFrames 0.8.214 (style A explainer, style B if urgent) over our own music, verifies every on-screen line and the caption claim by claim, QA's it, hosts the MP4 and queues it as kind `reel`. While `PUBLISHING_ENABLED` is not `true` it only renders and uploads the MP4 as a workflow artifact.
4. `watchdog` (every 2h): opens a GitHub issue (= email) if nothing was published for hours or a provider lost authorization.

Kill switch: repository variable `PUBLISHING_ENABLED` (must be `true`) or a file `state/PAUSE`.
Hard content rules enforced in code: never Morocco / UAE / Algeria / Israel in any text (incl. Arabic prefixed forms), no sports, no engagement bait, no AI-disclosure line, no AI images, no AI voice (reels are voice-free), never name a competing news outlet (primary-source attribution only, see below).

War / conflict news is allowed (owner decision 2026-10-29) in a calm, factual tone (`kahwa_publisher/safety.py`): no graphic / gore wording in anything we publish, graphic sources rejected at selection, a Gemini classifier rejects only graphic, propaganda, unverified, sports and banned-country-dependent stories (fail closed). War stories never get photos or footage: cards use a map / numbers / facts / text card, reels are graphic-only.

Maps (`kahwa_publisher/geo.py`, data built by `tools/maps/build_world.py` from Natural Earth admin-0 1:110m, public domain): a normal world map with every border and every country's Arabic name. Morocco, Algeria and the UAE are drawn and labelled like any country (map label layer only; the text ban above is unchanged). Western Sahara is a separate shape with its border drawn, labelled «الصحراء الغربية». Israel + Palestine are one undivided shape with no label or text. A map is never focused on Morocco, Algeria, the UAE, Israel or the Palestine/Israel shape.

## Source attribution (user-approved 2026-10-29, `kahwa_publisher/attribution.py`)

We attribute (Berne Convention art. 10bis, Meta's originality signal "credit the source", reader trust) but never advertise competing channels.

- **Who is named:** the PRIMARY source of each fact. Ranking: official/primary body (ministry, police, civil defence, health authority, government, UN agency, court, a company's own statement, space agency, central bank, statistics office; named officials) > wire agency (AFP → «وكالة الصحافة الفرنسية», Reuters «رويترز», AP «أسوشيتد برس», dpa «الوكالة الألمانية», EFE, ANSA, Kyodo, Xinhua, TASS, Anadolu, Yonhap — when that agency is the origin) > generic «وكالات».
- **Never named:** competing TV channels / news sites / papers (Al Jazeera, Al Arabiya/Al Hadath, Sky News Arabia, BBC Arabic, France 24, CNN, Euronews, RT, CGTN, Asharq, TRT, DW, Independent Arabia, Al-Araby, Al Mayadeen, Bloomberg, NYT, …) — not in post text, card, reel, hashtags or the source line.
- **How it is enforced:**
  1. The writers never see outlet names: source texts are labelled `[م1]`, `[م2]` … (posts and reels). Prompts tell them to extract the primary attribution from the texts ("قالت وزارة الصحة …", "وفق وكالة الصحافة الفرنسية") and never to name an outlet.
  2. `sanitize()` turns "بحسب/وفق/نقلاً عن <outlet>" into "بحسب تقارير إعلامية".
  3. **Hard gate in code after generation** (`outlet_hits`): denylist of outlet names in Arabic + Latin, with every Arabic prefixed form (و/ف + ب/ل/ك + ال, لل: «للجزيرة», «وبسكاي نيوز», «لبي بي سي»), harakat/tatweel-proof. Words that are also ordinary Arabic (الجزيرة = island/peninsula, العربية = Arabic, الحدث, الحرة, الشرق …) only count in an attribution context ("وفق الجزيرة", "قناة العربية") or as a bare item of a source line. Runs on posts, card fields, card photo credits, reel story/caption/chips (`render.gate`, `story.build_story`, `make.verify_reel`) — any hit = the post/reel is dropped. Photos/footage credited to an outlet are skipped (`cards.find_photo`, `reels.media.acceptable`).
  4. `verify.py`: each in-text attribution ("قالت <جهة>", "وفق <جهة>") must be supported by the source text (code check + verifier prompt) — no invented «قالت وزارة …»; unsupported → repaired once, else dropped. Posts with numbers, casualty counts, accusations or contested claims must carry an in-text attribution (`require_attribution`).
- **Final line** (built by code, never by the model): «المصدر: <up to 3 primary sources>» or «المصدر: وكالات» when only outlets are known; archive-photo credits sit above it. No links, logos, handles (@) or tags.
- **Cards:** footer «المصدر: <primary>»; our own compilations (numbers / facts cards, maps) read «إعداد: قهوة نيوز · المصدر: …»; maps «خريطة: قهوة نيوز (بيانات Natural Earth)».
- **Reels:** every on-screen source chip, the lower third and the end card show primary names only (fallback «وكالات»); caption bullets carry «(وفق …)»; Commons/NASA image credits stay as their licences require.
- **Internal only:** the ≥2-outlet verification is unchanged and logged (`{"verified_by_outlets": …}` in the run log, `sources`/`verified_by_outlets` in queue items, `outlets` in the reel `summary.json`); providers never publish these fields.
- Series tags (فهم الخبر، العالم في 30 ثانية، بالأرقام) remain our own brand. Tests: `tests/test_attribution.py`.

## Self-healing (2026-10-31, `work/ci-fix-2026-10-31.md` in the project notes)

- **Layout heal** (`bulletin/make.heal_layout`): DOM QA failures no longer kill the bulletin. A broken scene gets fit level 1→2→3 (smaller type; level 3 hides map pills), re-checked by the same QA; still broken → that shot / segment / «باختصار» is dropped. Banned-name and map-rule hits are never healed (fail closed). DOM QA crashes retry once on a new port.
- **True-peak guard** (`reels/audio.py`): 4× oversampled limiter after loudnorm + final-file guard that trims AAC overshoot (`fix_true_peak`), reels and bulletins.
- **Degradation ladder** (`fallback.py`): video fails after verification → a card post from the same verified lines (skipped if the story is already covered). One "degraded" issue thread.
- **Idempotent slots**: `slot` on every queue item (`bulletin-YYYY-MM-DD`, `reel-YYYY-MM-DD-HH`); a slot already queued/done is skipped (safe for duplicate triggers; `workflow_dispatch` input `slot` for an external cron).
- **Re-run transient failures once** (`rerun.yml` + `tools/ci_doctor.py --rerun-check`): only qa-crash / timeout / network / git race / render crash; never layout, audio, content, code, quota or auth failures; `publish` is never re-run.
- **CI doctor** (watchdog): classifies every failed run, one issue per (workflow, cause), reports disabled workflows. No keepalive hack, no AI.
- **Publisher health** (`health.py`, watchdog): Zernio `/v1/accounts/health` → circuit opens on disconnect / expired token / 401 → dispatcher skips Zernio (Buffer → Make); issue 7 days before `tokenExpiresAt`; closes by itself when healthy. `KEY_EXPIRIES` (e.g. `BUFFER_API_KEY=2026-11-27`): expired publisher keys skipped, expiring ones demoted 3 days before.
- **Supply chain**: `ubuntu-24.04`, actions pinned to SHAs, `npm ci --ignore-scripts` from `kahwa_publisher/reels/npm-shrinkwrap.json`, pinned `requirements-ci.txt`; Dependabot weekly; `deps-automerge.yml` merges only after pytest + a dry fixture render in a no-secrets, read-only job. `ci.yml`: tests on push + nightly dry fixture renders.

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

## LLM layer (2026-10-29): per-task free-model router — `kahwa_publisher/llm.py`

Each task has its own chain (override per run with env `LLM_CHAIN_<TASK>`, comma list `provider:model`):

| task | chain |
|---|---|
| writer | `gemini-3.8-flash` → `gemini-3.6-flash` → `gemini-3.1-flash-lite` → `gemma-4-31b-it` → `gemini-3.5-flash-lite` |
| classifier | `gemma-4-26b-a4b-it` → `gemini-3.5-flash-lite` → `gemma-4-31b-it` → `gemini-3.1-flash-lite` |
| utility (reel line shortening) | `gemma-4-31b-it` → `gemma-4-26b-a4b-it` → `gemini-3.1-flash-lite` → `gemini-3.5-flash-lite` |
| factcheck (claim decomposition, verification, repair, CHECK pass) | Groq `qwen/qwen3.8-27b` → SambaNova `DeepSeek-V3.2` → `DeepSeek-V3.1` → OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` → `nvidia/nemotron-3.5-super-vl:free` → Groq `openai/gpt-oss-120b` → (Cloudflare, Google models) |

- **Family rule:** the fact-checker is never the writer's model family (Gemma = Google). No independent checker with a key + quota
  → the story is **held** (not queued, not marked seen) and the next run retries. Never published unverified. A preflight check holds
  the whole run before any quota is spent.
- Keys from env (GitHub secrets): `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `SAMBANOVA_API_KEY`,
  `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`; a provider without a key is skipped. SambaNova free key expires ~2027-01-29
  (watchdog opens an issue from 14 days before; repo variable `KEY_EXPIRIES`).
- Free limits used for budgeting: Gemini 3.8/3.6 Flash 20 RPD, 3.1 Flash-Lite 500 RPD, Gemma 4 14,400 RPD (~15K TPM); Groq qwen3.8-27b
  and gpt-oss-120b 1,000 RPD / 8K TPM / 200K TPD (qwen also 1K output tokens/min, so max_tokens shrinks and calls are paced);
  SambaNova DeepSeek 20 RPD each (16K context on free); OpenRouter :free 50 requests/day for the account. Unknown limits are learned
  from 429 details.
- Quota state: `state/llm_quota-newsroom.json` / `state/llm_quota-reel.json` (one file per workflow, committed by the bot, summed on
  read; days follow the provider reset clock, Gemini = midnight Pacific). Per-run cap = ceil(daily limit / `LLM_RUNS_PER_DAY` (11) ×
  `LLM_BURST` (1.5)).
- Circuit breaker: 404 / deprecated / "not available" → model dead 3 days (persisted); daily 429 → exhausted until reset (persisted);
  401/403 → provider off for the run; 3 transient failures → model rests for the run.
- Strict JSON (schema per prompt, one repair try on the same model) and a code-only Arabic quality check on writer / repair output
  before fact-check. Quota exhaustion never fails a workflow (exit 0, logged as `held`).
- `python -m kahwa_publisher.llm --list-models --status --probe gemini:gemma-4-31b-it,...` (runs on manual newsroom dispatches).

## Fallback writer history (superseded by the LLM router above)

- Order today: Gemini `gemini-3.8-flash` → `gemini-3.6-flash` → `gemini-flash-latest` → `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` → `gemini-flash-lite-latest` (all on the free tier, each with its own daily quota; `gemini-2.5-flash` was dropped: it now answers 404 "no longer available to new users") → Groq / OpenRouter (only if those secrets are ever added). Override with env `GEMINI_MODELS`. A per-run circuit breaker skips a model for the rest of the run after a daily-quota 429, a 4xx, or 3 transient failures in a row (each skip is logged as `llm_skip_model`), so a spent model no longer costs ~50 s of back-off per call. If every model fails, the run writes nothing and the next scheduled run tries again.
- Benchmarked (`llm-fallback-bench.yml`, run 40802385273, llama.cpp b11972 prebuilt CPU, GitHub runner 4 vCPU, real newsroom prompt for a live two-outlet story):

| Model (GGUF) | reasoning_effort | wall time / call | generation | result |
| --- | --- | --- | --- | --- |
| Jais-3-3.7B Q4_K_M (3.2 GB) | low | 72.6 s | 8.7 tok/s | valid JSON, but dropped the mandatory «المصادر:» line |
| Jais-3-3.7B Q4_K_M | medium | 740.8 s | 6.0 tok/s | invalid JSON (14K chars of reasoning) |
| Jais-3-0.9B Q8_0 (1.2 GB) | low | 38.4 s | 19.0 tok/s | invalid JSON |
| Jais-3-0.9B Q8_0 | medium | 757.4 s (hit 6,144 tokens) | 8.1 tok/s | invalid JSON |
| Gemini (reference, same prompt) | — | 142 s incl. quota back-off | — | valid, passes gates |

- Why not: IFM's own cards say to always use `reasoning_effort=high` with ≥32K output tokens — impossible on a CPU runner (medium already takes ~12 min per call); the only feasible setting (low) failed our structural gate on its first story, and the fail-closed verifier would then have to be the same small model (a post needs ~6 LLM calls ≈ 7+ minutes plus a 3.2 GB download per run). Licence is fine (Apache-2.0 on every model card; note the repos carry no LICENSE file). Publishing nothing beats publishing an unverified post. Re-run the bench when a stronger small Arabic model ships.

## Daily newscast «قهوة اليوم» (2026-10-29, `kahwa_publisher/bulletin/`, workflow `bulletin.yml`)
- Structure (owner-approved pilot v4): opener A «المدار» (`bulletin/opener/`, NASA Blue/Black Marble globe, plate cached) →
  «أبرز العناوين» with one headline per main segment in bulletin order (permanent gate `rundown.check_headlines`) → 3–5
  numbered segments, each opened by a bumper → «باختصار» → sign-off. Voiceless, no verification card, music = global identity
  (`assets/music/identity`, CC0 samples, own composition).
- Stories: `bulletin/writer.py` picks the day's stories (≥ 2 outlets each, newsroom legal/sports/banned filters, graphic gate,
  LLM classifier), writes each segment through the LLM router (task writer), drops any line with a number not in the sources,
  a banned name, a competing outlet or graphic wording, and verifies every on-screen line with a fact-checker of another model
  family (fail closed: the story is dropped; fewer than 3 verified stories → no bulletin that day, exit 0).
- Maps never centre on a no-focus shape (MAR/DZA/ARE/ISR/merged PSE) and zoom in until no labelled no-focus country is in
  frame when the story point allows it (Yemen maps show no «الإمارات»).
- QA before queueing: screen gate, DOM safe-zone QA (`bulletin/qa.mjs`: Meta 9:16 safe zone, action rail, overflow, overlaps,
  banned names, map-label rules), file QA (1080×1920, yuv420p, −14 LUFS, TP ≤ −1 dBTP, full decode), frame #0 = opener.
- Daily 06:30 UTC; dry/fixture runs upload the MP4 as an artifact only; a queue item (kind `video`) is written only on a
  scheduled run with `PUBLISHING_ENABLED=true`. Test: Actions → bulletin → Run workflow (dry_run, optional test_fixture
  `tests/fixtures/bulletin-2026-10-29.json`).

## Concept B stings (`kahwa_publisher/stings/`, rules in `stings/policy.py`)
- Edition sting «العالم في 30 ثانية» (3.56 s) opens the multi-headline edition reel (reel.yml slots 08:11/14:11 UTC, max 4/day):
  sting with real story lon/lat as dots (no labels, no borders) → 3 verified headlines → fade; body music = bars 1–4 of
  bed-breaking; ≤ 60 s.
- Alert sting «عاجل» (3.05 s) in front of a style-B reel only for top-tier stories (urgent, ≥ 2 outlets incl. a tier-1 outlet),
  max 1/day and 3/week, never within 3 h after an edition reel. Counts in `state/stings.json` (scheduled runs only).

## Testing without publishing

- `newsroom.yml` → *Run workflow* (`dry_run` default **true**): writes posts + cards + full verification reports to the artifact `newsroom-dry-<run id>`; `queue/`, `state/`, `media/` untouched.
- `reel.yml` → *Run workflow* (`dry_run` default **true**, optional `host_test`): renders + QA + verification, artifact `reel-<run id>`; nothing queued.
- `llm-fallback-bench.yml` (manual, one-off): measures open Arabic models on CPU with the real newsroom prompt.
- Local: `python -m pytest tests`; `python -m kahwa_publisher.newsroom --dry --out /tmp/kahwa-dry`.
