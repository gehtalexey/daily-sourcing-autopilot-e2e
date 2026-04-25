"""Pipeline-step JSON contract tests.

Every module under ``pipeline/`` that the orchestrator runs as a subprocess must
satisfy: **stdout is either empty or a single valid JSON document**. Anything
else (extra prints, stack traces leaking to stdout, partial JSON) breaks
``run_pipeline.run_step`` silently — the most likely place a regression slips
into production.

These tests invoke each step exactly the way ``run_pipeline.py`` does (via
``python -m pipeline.<step>``) with no Supabase / GEM / Slack credentials, so
each step is forced down its "not configured" branch. We then assert stdout
parses cleanly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CREDENTIAL_ENV_VARS = (
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "CRUSTDATA_API_KEY",
    "GEM_API_KEY",
    "SALESQL_API_KEY",
    "SLACK_BOT_TOKEN",
)


def _clean_env() -> dict[str, str]:
    """Copy of the current env with credential vars stripped."""
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV_VARS}
    return env


def _run_step(
    module: str, args: list[str], stdin: str | None = None
) -> subprocess.CompletedProcess:
    """Mirror ``run_pipeline.run_step`` — same invocation shape, no creds."""
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        input=stdin,
        cwd=str(REPO_ROOT),
        timeout=30,
        env=_clean_env(),
    )


def _assert_stdout_is_empty_or_json(result: subprocess.CompletedProcess) -> None:
    out = result.stdout.strip()
    if not out:
        return
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Step stdout is neither empty nor valid JSON.\n"
            f"  stdout (first 300 chars): {out[:300]!r}\n"
            f"  stderr (first 300 chars): {result.stderr[:300]!r}\n"
            f"  json error: {e}"
        )
    assert isinstance(parsed, (dict, list)), (
        f"Top-level JSON must be object or array, got {type(parsed).__name__}"
    )


# Steps invoked by run_pipeline.run_mechanical_steps, with the exact args used
# in run_pipeline.py:104-125.
ORCHESTRATED_STEPS = [
    pytest.param("pipeline.search_step", ["test-position"], None, id="search"),
    pytest.param("pipeline.pre_filter_step", ["test-position"], None, id="pre_filter"),
    pytest.param("pipeline.enrich_step", ["test-position"], None, id="enrich"),
    pytest.param("pipeline.email_step", ["test-position"], None, id="email"),
    pytest.param("pipeline.gem_step", ["test-position"], None, id="gem"),
    pytest.param(
        "pipeline.finalize_step",
        ["test-position", "test-run-id", "completed"],
        None,
        id="finalize",
    ),
    pytest.param(
        "pipeline.slack_step",
        ["test-position"],
        json.dumps({"position_id": "test-position"}),
        id="slack",
    ),
]


@pytest.mark.parametrize("module,args,stdin", ORCHESTRATED_STEPS)
def test_orchestrated_step_stdout_is_json_or_empty(module, args, stdin):
    """Each step the daily orchestrator invokes must obey the JSON-or-empty contract."""
    result = _run_step(module, args, stdin=stdin)
    _assert_stdout_is_empty_or_json(result)


# Auxiliary steps run by Claude Code skills (not by run_pipeline.py) — same
# contract applies because they're consumed the same way.
AUX_STEPS = [
    pytest.param("pipeline.feedback_step", ["analyze", "test-position"], None, id="feedback"),
    pytest.param("pipeline.screen_step", ["get_profiles", "test-position"], None, id="screen"),
    pytest.param("pipeline.warm_leads_step", ["search", "test-position"], None, id="warm_leads"),
    pytest.param("pipeline.credits", ["today", "test-position"], None, id="credits"),
]


@pytest.mark.parametrize("module,args,stdin", AUX_STEPS)
def test_aux_step_stdout_is_json_or_empty(module, args, stdin):
    """Auxiliary steps invoked by skills must also obey the JSON-or-empty contract."""
    result = _run_step(module, args, stdin=stdin)
    _assert_stdout_is_empty_or_json(result)


def test_finalize_step_emits_supabase_error_when_unconfigured():
    """Sanity check: when Supabase is unconfigured, finalize_step emits the
    documented error JSON (not just any JSON)."""
    result = _run_step(
        "pipeline.finalize_step",
        ["test-position", "test-run-id", "completed"],
    )
    payload = json.loads(result.stdout.strip())
    assert payload == {"error": "Supabase not configured"}


def test_slack_step_emits_slack_error_when_unconfigured():
    """slack_step short-circuits on missing Slack config and must emit JSON."""
    result = _run_step(
        "pipeline.slack_step",
        ["test-position"],
        stdin=json.dumps({"position_id": "test-position"}),
    )
    payload = json.loads(result.stdout.strip())
    assert payload == {"error": "Slack not configured"}
