"""
Screen Step — Output profiles for Claude Code to screen, then save results.

This step is designed to be called by Claude Code (not standalone).

Sub-commands:
    python -m pipeline.screen_step get_profiles <position_id>
        → Outputs JSON array of profiles needing screening

    python -m pipeline.screen_step save_result <position_id> <linkedin_url>
        → Reads JSON from stdin: {"score": 7, "result": "qualified", "notes": "...", "opener": "..."}
        → Updates pipeline_candidates with screening results

    python -m pipeline.screen_step summary <position_id>
        → Outputs screening stats for this position
"""

import sys
import json

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
    update_pipeline_candidate,
    get_profiles_batch,
)
from core.normalizers import format_profile_for_screening


def log(msg):
    print(f"[screen] {msg}", file=sys.stderr)


def cmd_get_profiles(position_id: str):
    """Get profiles that need screening, formatted for Claude."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    if not candidates:
        log("No candidates to screen")
        print(json.dumps([]))
        return

    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    profiles_map = get_profiles_batch(client, urls)

    results = []
    for c in candidates:
        url = c.get('linkedin_url')
        profile = profiles_map.get(url, {})

        if not profile.get('raw_data'):
            continue

        profile_text = format_profile_for_screening(profile)
        raw_data = profile.get('raw_data', {})

        results.append({
            "linkedin_url": url,
            "name": raw_data.get('name', ''),
            "profile_text": profile_text,
        })

    log(f"Returning {len(results)} profiles for screening")
    print(json.dumps(results, default=str))


def cmd_save_result(position_id: str, linkedin_url: str):
    """Save screening result for a candidate. Reads JSON from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    data = json.loads(raw)

    updates = {}
    if 'score' in data:
        updates['screening_score'] = int(data['score'])
    if 'result' in data:
        updates['screening_result'] = data['result']
    if 'notes' in data:
        updates['screening_notes'] = data['notes']
    if 'opener' in data:
        updates['email_opener'] = data['opener']

    update_pipeline_candidate(client, position_id, linkedin_url, updates)
    log(f"Saved: {linkedin_url} -> {data.get('result')} ({data.get('score')}/10)")
    print(json.dumps({"ok": True}))


def cmd_summary(position_id: str):
    """Get screening stats for this position."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    all_candidates = get_pipeline_candidates(client, position_id)

    stats = {
        "total": len(all_candidates),
        "screened": len([c for c in all_candidates if c.get('screening_result')]),
        "qualified": len([c for c in all_candidates if c.get('screening_result') == 'qualified']),
        "not_qualified": len([c for c in all_candidates if c.get('screening_result') == 'not_qualified']),
        "pending": len([c for c in all_candidates if not c.get('screening_result')]),
    }

    print(json.dumps(stats))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.screen_step <command> <position_id> [linkedin_url]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_profiles':
        cmd_get_profiles(position_id)
    elif command == 'save_result':
        if len(sys.argv) < 4:
            print("Usage: ... save_result <position_id> <linkedin_url>", file=sys.stderr)
            sys.exit(1)
        cmd_save_result(position_id, sys.argv[3])
    elif command == 'summary':
        cmd_summary(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
