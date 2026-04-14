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
from datetime import datetime, timezone

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


def cmd_get_profiles(position_id: str, batch_size: int = 50):
    """Get profiles that need screening, formatted for Claude.

    Returns at most batch_size profiles per call to avoid context overflow.
    Call repeatedly until empty array returned.
    """
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

    total_unscreened = len(candidates)
    log(f"{total_unscreened} total unscreened")

    # Scan through ALL candidates in chunks to find batch_size with enriched profiles.
    # Do NOT slice to batch_size first -- unenriched candidates cluster at the front
    # (obfuscated URLs that failed enrichment), causing early false-empty returns.
    LOOKUP_CHUNK = 100
    results = []
    scanned = 0

    for chunk_start in range(0, len(candidates), LOOKUP_CHUNK):
        chunk = candidates[chunk_start:chunk_start + LOOKUP_CHUNK]
        urls = [c.get('linkedin_url') for c in chunk if c.get('linkedin_url')]
        profiles_map = get_profiles_batch(client, urls)
        scanned += len(chunk)

        for c in chunk:
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

            if len(results) >= batch_size:
                break

        if len(results) >= batch_size:
            break

    log(f"Returning {len(results)} profiles for screening (scanned {scanned}/{total_unscreened})")
    print(json.dumps(results, default=str))


def cmd_save_result(position_id: str, linkedin_url: str):
    """Save screening result for a candidate. Reads JSON from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"ERROR: Invalid JSON from stdin: {e}")
        log(f"  Raw input: {raw[:500]}")
        print(json.dumps({"error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    # Validate required fields so partial/invalid writes fail loudly instead
    # of silently leaving candidates in a half-updated state.
    has_decision = 'decision' in data
    has_result = 'result' in data
    if not has_decision and not has_result:
        log(f"ERROR: missing required field 'result' or 'decision' in {linkedin_url}")
        print(json.dumps({"error": "missing required field: result or decision"}))
        sys.exit(1)

    # Validate score/confidence range (1-10).
    raw_score = data.get('confidence') if has_decision else data.get('score')
    if raw_score is not None:
        try:
            score_int = int(raw_score)
        except (TypeError, ValueError):
            log(f"ERROR: score/confidence must be int, got {raw_score!r}")
            print(json.dumps({"error": f"score must be int 1-10, got {raw_score!r}"}))
            sys.exit(1)
        if score_int < 1 or score_int > 10:
            log(f"ERROR: score/confidence out of range (1-10): {score_int}")
            print(json.dumps({"error": f"score out of range 1-10: {score_int}"}))
            sys.exit(1)

    # Verify candidate exists in this position's pool before writing.
    # Prevents silent no-op updates + orphan screening_results entries when
    # a subagent passes a URL that doesn't belong to this position.
    existing = get_pipeline_candidates(client, position_id, {
        'linkedin_url': f'eq.{linkedin_url}',
    })
    if not existing:
        log(f"ERROR: candidate {linkedin_url} not found in {position_id}")
        print(json.dumps({"error": f"candidate not found: {linkedin_url}"}))
        sys.exit(1)

    updates = {}

    # Support both old format (score/result) and new format (decision/confidence)
    if has_decision:
        # New GO/NO GO format
        decision = data['decision'].strip().upper()
        updates['screening_result'] = 'qualified' if decision == 'GO' else 'not_qualified'
        if 'confidence' in data:
            updates['screening_score'] = int(data['confidence'])
    else:
        # Legacy format — backward compatible
        if 'score' in data:
            updates['screening_score'] = int(data['score'])
        if 'result' in data:
            updates['screening_result'] = data['result'].strip().lower()

    if 'notes' in data:
        updates['screening_notes'] = data['notes']
    if 'opener' in data:
        updates['email_opener'] = data['opener']

    # Store structured screening detail (new format fields)
    detail_fields = ['must_haves', 'career_trajectory', 'tenure_verified',
                     'tenure_detail', 'company_verified', 'company_note',
                     'hard_filters_passed', 'rejection_reason', 'dealbreakers_checked']
    screening_detail = {k: data[k] for k in detail_fields if k in data}
    if screening_detail:
        updates['screening_detail'] = json.dumps(screening_detail)

    updates['screened_at'] = datetime.now(timezone.utc).isoformat()

    update_pipeline_candidate(client, position_id, linkedin_url, updates)

    # Dual-write to shared screening_results table (with retry)
    from core.db import insert_screening_result, compute_jd_hash, get_pipeline_position
    import time
    position = get_pipeline_position(client, position_id)
    jd_text = (position or {}).get('hm_notes') or (position or {}).get('job_description') or ''
    jd_hash = compute_jd_hash(jd_text)
    for attempt in range(3):
        try:
            insert_screening_result(
                client, linkedin_url, source_project='autopilot', jd_hash=jd_hash,
                score=data.get('score'), result=data.get('result'),
                notes=data.get('notes'), opener=data.get('opener'),
                position_id=position_id, jd_title=position_id,
            )
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                log(f"ERROR: screening_results write failed after 3 attempts: {e}")

    log(f"Saved: {linkedin_url} -> {data.get('result')} ({data.get('score')}/10)")
    print(json.dumps({"ok": True}))


def cmd_summary(position_id: str):
    """Get screening stats for this position."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    all_candidates = get_pipeline_candidates(client, position_id)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Count today's screening by screened_at timestamp
    today_screened = [c for c in all_candidates
                      if c.get('screened_at') and str(c['screened_at'])[:10] == today]
    today_qualified = [c for c in today_screened if c.get('screening_result') == 'qualified']

    # Pending = unscreened AND not failed enrichment (actual work remaining)
    pending = [c for c in all_candidates
               if not c.get('screening_result') and not c.get('enrich_failed_at')]

    stats = {
        "total": len(all_candidates),
        "screened": len([c for c in all_candidates if c.get('screening_result')]),
        "qualified": len([c for c in all_candidates if c.get('screening_result') == 'qualified']),
        "not_qualified": len([c for c in all_candidates if c.get('screening_result') == 'not_qualified']),
        "pending": len(pending),
        "today_screened": len(today_screened),
        "today_qualified": len(today_qualified),
    }

    print(json.dumps(stats))


def cmd_get_qualified(position_id: str):
    """Get all qualified candidates with full profiles for final review."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
        'gem_pushed': 'eq.false',
    })

    if not candidates:
        log("No qualified candidates pending GEM push")
        print(json.dumps([]))
        return

    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    profiles_map = get_profiles_batch(client, urls)

    results = []
    for c in candidates:
        url = c.get('linkedin_url')
        profile = profiles_map.get(url, {})

        profile_text = ''
        if profile.get('raw_data'):
            profile_text = format_profile_for_screening(profile)

        results.append({
            "linkedin_url": url,
            "name": (profile.get('raw_data') or {}).get('name', c.get('candidate_name', '')),
            "screening_score": c.get('screening_score'),
            "screening_notes": c.get('screening_notes', ''),
            "email_opener": c.get('email_opener', ''),
            "profile_text": profile_text,
        })

    log(f"Returning {len(results)} qualified candidates for final review")
    print(json.dumps(results, default=str))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.screen_step <command> <position_id> [linkedin_url]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_profiles':
        cmd_get_profiles(position_id)
    elif command == 'get_qualified':
        cmd_get_qualified(position_id)
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
