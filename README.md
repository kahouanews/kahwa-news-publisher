# Kahwa News – automated newsroom & publisher

Fully unattended pipeline for the **Kahwa News** Facebook Page, running free on GitHub Actions.

1. `newsroom` (every 2h): reads 8 Arabic world-news outlets, clusters the same story, keeps only stories confirmed by ≥2 outlets, filters restricted subjects, has an AI write an original Arabic post (copy-check, no links), renders a branded 1080×1350 card, queues it.
2. `publish` (every 20 min): posts the next due item with 40–60 min human-like spacing, failover Zernio → Buffer → Make, no duplicates, pauses everything 48h on any Facebook restriction (never retries those).
3. `reel` (12:41 and 18:41 Algeria): picks the top verified story, an AI writes a reel script (`story.json`, every number checked against the sources), fetches openly licensed archive footage/photos (Wikimedia Commons, NASA; PD/CC0/CC BY/CC BY-SA only, credited and labelled «لقطات أرشيفية»/«صورة أرشيفية», graphic-only fallback), renders it with HyperFrames 0.8.214 (style A explainer, style B if urgent) over our own music library (`assets/music`, generated with ACE-Step 1.5), QA's it and queues it as kind `reel`. While `PUBLISHING_ENABLED` is not `true` it only renders and uploads the MP4 as a workflow artifact.
4. `watchdog` (every 2h): opens a GitHub issue (= email) if nothing was published for hours or a provider lost authorization.

Kill switch: repository variable `PUBLISHING_ENABLED` (must be `true`) or a file `state/PAUSE`.
