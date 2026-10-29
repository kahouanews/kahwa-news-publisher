"""Retired 2026-10-29: the old Pillow slideshow reel (rejected by the user).

Reels are now rendered by kahwa_publisher.reels (HyperFrames v2 renderer, style A explainer / style B breaking)
from .github/workflows/reel.yml. Kept as a stub so nothing can silently fall back to the old path.
"""


def render_reel(*_a, **_k):
    raise RuntimeError("old Pillow reel path is disabled; use kahwa_publisher.reels.make")
