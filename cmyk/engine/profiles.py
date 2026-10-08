"""Printer / preflight profiles. Every threshold the engine uses comes from a profile."""
from __future__ import annotations

import json
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parents[2] / "profiles"
DEFAULT_PROFILE = "brochure"

# Fallback values so a hand-edited or partial profile never crashes the engine.
DEFAULTS = {
    "trim_mm": None,          # [w, h] expected trim size, or None to skip
    "bleed_mm": 3,
    "crop_marks": "ignore",   # required | forbidden | ignore
    "safe_margin_mm": 3,      # 0 disables the check
    "dpi_warn": 300,
    "dpi_fail": 150,
    "rgb": "fail",            # fail | warning | ignore
    "max_spot_colours": None, # None = no limit
    "overprint": "fail",      # fail | warning | ignore
    "fonts_embedded": "fail", # fail | warning | ignore
    "live_text": "ignore",    # review | ignore  (signage: type may need outlining)
    "manual_checks": [],
}


def _complete(p: dict) -> dict:
    out = dict(DEFAULTS)
    out.update(p)
    return out


def load_profiles() -> list[dict]:
    profiles = []
    for f in sorted(PROFILE_DIR.glob("*.json")):
        try:
            profiles.append(_complete(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return profiles


def load_profile(profile_id: str) -> dict:
    for p in load_profiles():
        if p["id"] == profile_id:
            return p
    raise KeyError(profile_id)


def merge_overrides(base: dict, overrides: dict | None) -> dict:
    """Per-job overrides never touch the saved profile file."""
    out = dict(base)
    for k, v in (overrides or {}).items():
        if k in DEFAULTS:
            out[k] = v
    return out
