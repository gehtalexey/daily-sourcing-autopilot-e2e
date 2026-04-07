"""
Daily Sourcing Autopilot — Main Pipeline Orchestrator

Usage:
    python run_pipeline.py <position_id>
    python run_pipeline.py --all          # Run for all active positions

Runs the MECHANICAL pipeline steps (search, pre-filter, enrich, email, gem, finalize, slack).
Screening is handled by Claude Code itself in the scheduled task — not by this script.

Pipeline steps (in order):
  1. Search    — Find new candidates via Crustdata
  2. Pre-filter — Remove past candidates, blacklisted companies
  3. Enrich    — Enrich LinkedIn profiles via Crustdata
  [screening is done by Claude Code in the scheduled task]
  4. Email     — Find personal emails via SalesQL
  5. GEM       — Push qualified candidates to GEM ATS
  6. Finalize  — Aggregate stats, update run record
  7. Slack     — Send daily summary to Slack
"""

import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

from core.db import (
    get_supabase_client,
    get_active_pipeline_positions,
    get_pipeline_position,
    create_pipeline_run,
    update_pipeline_run,
)


PYTHON = sys.executable


def log(msg):
    print(f"[pipeline] {msg}", file=sys.stderr)


def run_step(step_name: str, args: list, stdin_data: str = None) -> dict:
    """Run a pipeline step as a subprocess. Returns parsed JSON stdout."""
    module = f"pipeline.{step_name}"
    cmd = [PYTHON, '-m', module] + args

    log(f"--- Running {step_name} {' '.join(args)} ---")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=stdin_data,
            timeout=600,
            cwd=str(Path(__file__).parent),
        )

        if result.stderr:
            for line in result.stderr.strip().split('\n'):
                print(f"  {line}", file=sys.stderr)

        if result.stdout.strip():
            try:
                return json.loads(result.stdout.strip())
            except json.JSONDecodeError:
                log(f"  Warning: Could not parse JSON: {result.stdout[:200]}")
                return {"error": f"Invalid JSON from {step_name}"}

        if result.returncode != 0:
            return {"error": f"{step_name} exited with code {result.returncode}"}

        return {}

    except subprocess.TimeoutExpired:
        return {"error": f"{step_name} timed out"}
    except Exception as e:
        return {"error": f"{step_name} failed: {str(e)}"}


def run_mechanical_steps(position_id: str, skip_screening: bool = True) -> dict:
    """Run the mechanical (non-AI) pipeline steps for a position."""
    log(f"========== Starting pipeline for {position_id} ==========")

    client = get_supabase_client()
    if not client:
        return {"error": "Supabase not configured"}

    position = get_pipeline_position(client, position_id)
    if not position:
        return {"error": f"Position '{position_id}' not found"}

    if not position.get('active', True):
        return {"skipped": True, "reason": "inactive"}

    run = create_pipeline_run(client, position_id)
    run_id = run.get('id') if isinstance(run, dict) else None

    all_stats = {"position_id": position_id, "run_id": run_id}

    try:
        # Search
        all_stats['search'] = run_step('search_step', [position_id])

        # Pre-filter
        all_stats['pre_filter'] = run_step('pre_filter_step', [position_id])

        # Enrich
        all_stats['enrich'] = run_step('enrich_step', [position_id])

        # Email (for previously qualified candidates)
        all_stats['email'] = run_step('email_step', [position_id])

        # GEM push (for previously qualified with email)
        all_stats['gem'] = run_step('gem_step', [position_id])

        # Finalize
        finalize = run_step('finalize_step', [position_id, run_id, 'completed'])
        all_stats.update(finalize)

        # Slack
        all_stats['slack'] = run_step('slack_step', [position_id],
                                       stdin_data=json.dumps(all_stats))

    except Exception as e:
        all_stats['error'] = str(e)
        if run_id:
            try:
                update_pipeline_run(client, run_id, 'failed', stats=all_stats, error=str(e))
            except Exception:
                pass

    log(f"========== Pipeline done for {position_id} ==========")
    return all_stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_pipeline.py <position_id>", file=sys.stderr)
        print("       python run_pipeline.py --all", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == '--all':
        client = get_supabase_client()
        if not client:
            print(json.dumps({"error": "Supabase not configured"}))
            sys.exit(1)

        positions = get_active_pipeline_positions(client)
        if not positions:
            print(json.dumps({"error": "No active positions"}))
            return

        all_results = {}
        for pos in positions:
            pid = pos.get('position_id')
            if pid:
                all_results[pid] = run_mechanical_steps(pid)

        print(json.dumps(all_results, indent=2, default=str))
    else:
        result = run_mechanical_steps(arg)
        print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()
