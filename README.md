# Kahwa News – automated newsroom & publisher

Fully unattended pipeline for the **Kahwa News** Facebook Page, running free on GitHub Actions.

1. `newsroom` (every 2h): reads 8 Arabic world-news outlets, clusters the same story, keeps only stories confirmed by ≥2 outlets, filters restricted subjects, has an AI write an original Arabic post (copy-check, no links), renders a branded 1080×1350 card, queues it.
2. `publish` (every 20 min): posts the next due item with 40–60 min human-like spacing, failover Zernio → Buffer → Make, no duplicates, pauses everything 48h on any Facebook restriction (never retries those).
3. `watchdog` (every 2h): opens a GitHub issue (= email) if nothing was published for hours or a provider lost authorization.

Kill switch: repository variable `PUBLISHING_ENABLED` (must be `true`) or a file `state/PAUSE`.
