"""Unit tests for `run_pipeline.run_step`.

`run_step` is the joint that every pipeline step travels through. Its
contract: take a step name and args, run `python -m pipeline.<step>` as a
subprocess, and return either the parsed JSON stdout or a structured error
dict. Anything else broken here breaks the whole pipeline silently.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

import run_pipeline


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def test_parses_json_stdout(mocker):
    mocker.patch("subprocess.run", return_value=_completed(stdout='{"saved": 3}'))

    result = run_pipeline.run_step("search_step", ["pos-1"])

    assert result == {"saved": 3}


def test_strips_whitespace_around_json(mocker):
    mocker.patch("subprocess.run", return_value=_completed(stdout='\n  {"ok": true}\n'))

    assert run_pipeline.run_step("any", []) == {"ok": True}


def test_invalid_json_returns_error_dict(mocker):
    mocker.patch("subprocess.run", return_value=_completed(stdout="not json at all"))

    result = run_pipeline.run_step("any", [])

    assert "error" in result
    assert "Invalid JSON" in result["error"]


def test_empty_stdout_with_zero_exit_returns_empty_dict(mocker):
    mocker.patch("subprocess.run", return_value=_completed(stdout="", returncode=0))

    assert run_pipeline.run_step("any", []) == {}


def test_empty_stdout_with_nonzero_exit_returns_error(mocker):
    mocker.patch("subprocess.run", return_value=_completed(stdout="", returncode=1))

    result = run_pipeline.run_step("any", [])

    assert result == {"error": "any exited with code 1"}


def test_timeout_returns_error_dict(mocker):
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=600),
    )

    result = run_pipeline.run_step("enrich_step", ["pos-1"])

    assert result == {"error": "enrich_step timed out"}


def test_generic_exception_returns_error_dict(mocker):
    mocker.patch("subprocess.run", side_effect=RuntimeError("boom"))

    result = run_pipeline.run_step("any", [])

    assert result["error"].startswith("any failed:")
    assert "boom" in result["error"]


def test_invokes_python_dash_m_with_module_path(mocker):
    spy = mocker.patch("subprocess.run", return_value=_completed(stdout="{}"))

    run_pipeline.run_step("search_step", ["pos-1"])

    cmd = spy.call_args.args[0] if spy.call_args.args else spy.call_args.kwargs["args"]
    assert cmd[1:] == ["-m", "pipeline.search_step", "pos-1"]


def test_applies_600_second_timeout(mocker):
    spy = mocker.patch("subprocess.run", return_value=_completed(stdout="{}"))

    run_pipeline.run_step("enrich_step", ["pos-1"])

    assert spy.call_args.kwargs["timeout"] == 600


def test_passes_stdin_data_to_subprocess(mocker):
    spy = mocker.patch("subprocess.run", return_value=_completed(stdout="{}"))
    payload = json.dumps({"position_id": "pos-1"})

    run_pipeline.run_step("slack_step", ["pos-1"], stdin_data=payload)

    assert spy.call_args.kwargs["input"] == payload
