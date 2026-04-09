"""
Pipeline DB Helpers — CLI interface for Supabase operations.

Called by the Claude Code scheduled agent via Bash.
All output JSON to stdout, logging to stderr.

Usage:
    python -m pipeline.db_helpers init <position_id>
    python -m pipeline.db_helpers exclude_urls <position_id>
    python -m pipeline.db_helpers save_candidates <position_id>   (reads JSON from stdin)
    python -m pipeline.db_helpers get_to_screen <position_id>
    python -m pipeline.db_helpers update_screening <position_id> <linkedin_url>  (reads JSON stdin)
    python -m pipeline.db_helpers get_qualified <position_id>
    python -m pipeline.db_helpers finalize <position_id> <run_id> <status>
"""

import sys
import json
from datetime import datetime

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_exclude_urls,
    create_pipeline_run,
    update_pipeline_run,
    upsert_pipeline_candidate,
    update_pipeline_candidate,
    get_pipeline_candidates,
    delete_pipeline_candidates,
    get_profile,
    get_profiles_batch,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    """Log to stderr so stdout stays clean for JSON."""
    print(f"[db_helpers] {msg}", file=sys.stderr)


def cmd_init(position_id: str):
    """Initialize a pipeline run. Returns position config + run_id."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    if not position.get('active', True):
        print(json.dumps({"error": f"Position '{position_id}' is not active"}))
        sys.exit(1)

    run = create_pipeline_run(client, position_id)
    run_id = run.get('id') if isinstance(run, dict) else None

    log(f"Created run {run_id} for position {position_id}")

    result = {
        "position_id": position_id,
        "run_id": run_id,
        "job_description": position.get("job_description", ""),
        "search_filters": position.get("search_filters"),
        "hm_notes": position.get("hm_notes", ""),
        "sender_info": position.get("sender_info", ""),
        "selling_points": position.get("selling_points", ""),
        "sheet_url": position.get("sheet_url"),
        "gem_project_id": position.get("gem_project_id"),
    }
    print(json.dumps(result))


def cmd_exclude_urls(position_id: str):
    """Get all LinkedIn URLs already sourced for this position."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    urls = get_pipeline_exclude_urls(client, position_id)
    log(f"Found {len(urls)} URLs to exclude for {position_id}")
    print(json.dumps(urls))


def cmd_save_candidates(position_id: str):
    """Save new candidates from stdin JSON. Expects list of {linkedin_url, ...}."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    candidates = json.loads(raw)

    if not isinstance(candidates, list):
        print(json.dumps({"error": "Expected JSON array"}))
        sys.exit(1)

    today = datetime.utcnow().strftime('%Y-%m-%d')
    saved = 0
    skipped = 0

    for c in candidates:
        url = c.get('linkedin_profile_url') or c.get('linkedin_url') or c.get('flagship_profile_url', '')
        url = normalize_linkedin_url(url)
        if not url:
            skipped += 1
            continue
        try:
            upsert_pipeline_candidate(client, position_id, url, source='crustdata_search', run_date=today)
            saved += 1
        except Exception as e:
            log(f"Error saving {url}: {e}")
            skipped += 1

    log(f"Saved {saved} candidates, skipped {skipped}")
    print(json.dumps({"saved": saved, "skipped": skipped}))


def cmd_get_to_screen(position_id: str):
    """Get candidates that need screening, with enriched profile data."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Get candidates without screening
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null'
    })

    if not candidates:
        log("No candidates to screen")
        print(json.dumps([]))
        return

    # Get their enriched profiles from profiles table
    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    profiles_map = get_profiles_batch(client, urls)

    # Merge candidate + profile data
    results = []
    for c in candidates:
        url = c.get('linkedin_url')
        profile = profiles_map.get(url, {})
        raw_data = profile.get('raw_data', {})

        if not raw_data:
            log(f"No raw_data for {url}, skipping")
            continue

        results.append({
            "linkedin_url": url,
            "name": raw_data.get('name', ''),
            "headline": raw_data.get('headline', ''),
            "location": raw_data.get('location', ''),
            "summary": raw_data.get('summary', ''),
            "skills": raw_data.get('skills', []),
            "current_employers": raw_data.get('current_employers', []),
            "past_employers": raw_data.get('past_employers', []),
            "education_background": raw_data.get('education_background', []),
            "all_employers": raw_data.get('all_employers', []),
            "all_titles": raw_data.get('all_titles', []),
            "all_schools": raw_data.get('all_schools', []),
            "num_of_connections": raw_data.get('num_of_connections', 0),
            "years_of_experience_raw": raw_data.get('years_of_experience_raw'),
        })

    log(f"Returning {len(results)} profiles for screening")
    print(json.dumps(results, default=str))


def cmd_update_screening(position_id: str, linkedin_url: str):
    """Update screening results for a candidate. Reads JSON from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    data = json.loads(raw)

    updates = {}
    if 'score' in data:
        updates['screening_score'] = data['score']
    if 'result' in data:
        updates['screening_result'] = data['result']
    if 'notes' in data:
        updates['screening_notes'] = data['notes']
    if 'opener' in data:
        updates['email_opener'] = data['opener']

    update_pipeline_candidate(client, position_id, linkedin_url, updates)

    # Dual-write to shared screening_results table
    try:
        from core.db import insert_screening_result, compute_jd_hash, get_pipeline_position
        position = get_pipeline_position(client, position_id)
        jd_text = (position or {}).get('hm_notes') or (position or {}).get('job_description') or ''
        jd_hash = compute_jd_hash(jd_text)
        insert_screening_result(
            client, linkedin_url, source_project='autopilot', jd_hash=jd_hash,
            score=data.get('score'), result=data.get('result'),
            notes=data.get('notes'), opener=data.get('opener'),
            position_id=position_id, jd_title=position_id,
        )
    except Exception as e:
        log(f"Warning: screening_results write failed: {e}")

    log(f"Updated screening for {linkedin_url}: {data.get('result')}")
    print(json.dumps({"ok": True}))


def cmd_get_qualified(position_id: str):
    """Get qualified candidates that need email lookup."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
        'personal_email': 'is.null',
    })

    log(f"Found {len(candidates)} qualified candidates needing email")
    print(json.dumps(candidates, default=str))


def cmd_finalize(position_id: str, run_id: str, status: str = 'completed'):
    """Finalize a pipeline run with aggregated stats."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Aggregate stats
    all_candidates = get_pipeline_candidates(client, position_id)
    today = datetime.utcnow().strftime('%Y-%m-%d')
    today_candidates = [c for c in all_candidates if c.get('search_run_date') == today]

    stats = {
        "searched_today": len(today_candidates),
        "total_candidates": len(all_candidates),
        "screened": len([c for c in all_candidates if c.get('screening_result')]),
        "qualified": len([c for c in all_candidates if c.get('screening_result') == 'qualified']),
        "not_qualified": len([c for c in all_candidates if c.get('screening_result') == 'not_qualified']),
        "with_email": len([c for c in all_candidates if c.get('personal_email')]),
        "pushed_to_gem": len([c for c in all_candidates if c.get('gem_pushed')]),
    }

    update_pipeline_run(client, run_id, status, stats=stats)
    log(f"Finalized run {run_id}: {status}")
    print(json.dumps(stats))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.db_helpers <command> <position_id> [args...]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'init':
        cmd_init(position_id)
    elif command == 'exclude_urls':
        cmd_exclude_urls(position_id)
    elif command == 'save_candidates':
        cmd_save_candidates(position_id)
    elif command == 'get_to_screen':
        cmd_get_to_screen(position_id)
    elif command == 'update_screening':
        if len(sys.argv) < 4:
            print("Usage: ... update_screening <position_id> <linkedin_url>", file=sys.stderr)
            sys.exit(1)
        cmd_update_screening(position_id, sys.argv[3])
    elif command == 'get_qualified':
        cmd_get_qualified(position_id)
    elif command == 'finalize':
        if len(sys.argv) < 4:
            print("Usage: ... finalize <position_id> <run_id> [status]", file=sys.stderr)
            sys.exit(1)
        status = sys.argv[4] if len(sys.argv) > 4 else 'completed'
        cmd_finalize(position_id, sys.argv[3], status)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
