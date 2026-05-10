"""Shared fixtures.

The pipeline reads config from `config.json` at the repo root and falls back to
`SUPABASE_URL` / `SUPABASE_KEY` env vars. Tests run with neither, which forces
each step into its "not configured" branch — exactly the path we want to assert
prints valid JSON.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def unconfigured_env(monkeypatch):
    """Strip any ambient credentials so step modules hit the not-configured path."""
    for var in (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "CRUSTDATA_API_KEY",
        "GEM_API_KEY",
        "SALESQL_API_KEY",
        "SLACK_BOT_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def _guard_against_real_config():
    """Hard fail if a real config.json appears during test runs — we never want
    tests to touch a live Supabase, GEM, or Slack workspace."""
    config_path = REPO_ROOT / "config.json"
    if config_path.exists():
        pytest.fail(
            f"Refusing to run tests with a real config.json at {config_path}. "
            "Move or rename it before running pytest."
        )
