"""
Finalize Step — Complete a pipeline run with aggregated stats.

Usage:
    python -m pipeline.finalize_step <position_id> <run_id> [status]

Aggregates stats from pipeline_candidates for this position.
Updates pipeline_runs with final status and stats.
Prints summary JSON for Claude to send via Slack.
"""

import sys
import json
from datetime import datetime

from core.db import (
    get_supabase_client,
    get_pipeline_candidates,
    update_pipeline_run,
)


def log(msg):
    print(f"[finalize] {msg}", file=sys.stderr)


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.finalize_step <position_id> <run_id> [status]", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]
    run_id = sys.argv[2]
    status = sys.argv[3] if len(sys.argv) > 3 else 'completed'

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Aggregate stats from all candidates for this position
    all_candidates = get_pipeline_candidates(client, position_id)
    today = datetime.utcnow().strftime('%Y-%m-%d')
    today_candidates = [c for c in all_candidates if c.get('search_run_date') == today]

    stats = {
        "position_id": position_id,
        "run_date": today,
        "status": status,
        "searched_today": len(today_candidates),
        "total_candidates": len(all_candidates),
        "qualified": len([c for c in all_candidates if c.get('screening_result') == 'qualified']),
        "not_qualified": len([c for c in all_candidates if c.get('screening_result') == 'not_qualified']),
        "pending_screening": len([c for c in all_candidates if not c.get('screening_result')]),
        "with_email": len([c for c in all_candidates if c.get('personal_email')]),
        "pushed_to_gem": len([c for c in all_candidates if c.get('gem_pushed')]),
    }

    # Update the run
    update_pipeline_run(client, run_id, status, stats=stats)

    log(f"Run {run_id} finalized: {status}")
    log(f"  Today: {stats['searched_today']} new | Total: {stats['total_candidates']}")
    log(f"  Qualified: {stats['qualified']} | Not qualified: {stats['not_qualified']}")
    log(f"  With email: {stats['with_email']} | Pushed to GEM: {stats['pushed_to_gem']}")

    print(json.dumps(stats))


if __name__ == '__main__':
    main()
