"""
Feedback Step — Record and analyze HM feedback on screening decisions.

Enables a feedback loop: HM rejections feed back into position-specific screening skills
so the AI avoids repeating the same mistakes.

Sub-commands:
    python -m pipeline.feedback_step record <position_id> <linkedin_url>
        → Reads JSON from stdin: {"action": "rejected", "reason": "too junior, only 1 year as TL"}
        → Updates pipeline_candidates with HM feedback

    python -m pipeline.feedback_step get_rejections <position_id>
        → Outputs all candidates where AI said GO but HM rejected
        → Used by screening skill Phase 0 to load past mistakes

    python -m pipeline.feedback_step analyze <position_id>
        → Groups HM rejections by pattern and outputs structured report
"""

import sys
import json
from datetime import datetime, timezone

from core.db import get_supabase_client, get_pipeline_candidates, update_pipeline_candidate


def log(msg):
    print(f"[feedback] {msg}", file=sys.stderr)


def cmd_record(position_id: str, linkedin_url: str):
    """Record HM feedback for a candidate. Reads JSON from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"ERROR: Invalid JSON: {e}")
        print(json.dumps({"error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    action = data.get('action', '').strip().lower()
    reason = data.get('reason', '')

    updates = {
        'hm_feedback': action,
        'hm_rejection_reason': reason if action == 'rejected' else None,
    }
    if action == 'rejected':
        updates['hm_rejected_at'] = datetime.now(timezone.utc).isoformat()
        # Also flip screening result so they don't appear in qualified lists
        updates['screening_result'] = 'not_qualified'

    update_pipeline_candidate(client, position_id, linkedin_url, updates)
    log(f"Recorded HM {action}: {linkedin_url} — {reason}")
    print(json.dumps({"ok": True}))


def cmd_get_rejections(position_id: str):
    """Get all candidates where AI qualified but HM rejected."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    candidates = get_pipeline_candidates(client, position_id, {
        'hm_feedback': 'eq.rejected',
    })

    if not candidates:
        log("No HM rejections found")
        print(json.dumps([]))
        return

    results = []
    for c in candidates:
        results.append({
            "linkedin_url": c.get('linkedin_url'),
            "name": c.get('candidate_name', ''),
            "ai_score": c.get('screening_score'),
            "ai_notes": c.get('screening_notes', ''),
            "hm_reason": c.get('hm_rejection_reason', ''),
            "hm_rejected_at": str(c.get('hm_rejected_at', '')),
        })

    log(f"Found {len(results)} HM rejections")
    print(json.dumps(results, default=str))


def cmd_analyze(position_id: str):
    """Analyze rejection patterns and output structured report."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    candidates = get_pipeline_candidates(client, position_id, {
        'hm_feedback': 'eq.rejected',
    })

    if not candidates:
        log("No rejections to analyze")
        print(json.dumps({"patterns": [], "total": 0}))
        return

    # Group by rejection reason keywords
    patterns = {}
    for c in candidates:
        reason = (c.get('hm_rejection_reason') or '').lower()
        # Categorize by common keywords
        category = 'other'
        if any(w in reason for w in ['junior', 'experience', 'years', 'tenure']):
            category = 'insufficient_experience'
        elif any(w in reason for w in ['senior', 'overkill', 'overqualified']):
            category = 'too_senior'
        elif any(w in reason for w in ['skill', 'stack', 'tech', 'frontend', 'backend']):
            category = 'wrong_skills'
        elif any(w in reason for w in ['company', 'consulting', 'outsourcing', 'agency']):
            category = 'wrong_company_type'
        elif any(w in reason for w in ['title', 'function', 'role']):
            category = 'wrong_function'
        elif any(w in reason for w in ['domain', 'industry']):
            category = 'wrong_domain'

        if category not in patterns:
            patterns[category] = []
        patterns[category].append({
            "name": c.get('candidate_name', ''),
            "reason": c.get('hm_rejection_reason', ''),
            "ai_score": c.get('screening_score'),
        })

    report = {
        "total_rejections": len(candidates),
        "patterns": {k: {"count": len(v), "examples": v[:3]} for k, v in patterns.items()},
    }

    log(f"Analysis: {len(candidates)} rejections across {len(patterns)} patterns")
    print(json.dumps(report, default=str))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.feedback_step <command> <position_id> [linkedin_url]",
              file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'record':
        if len(sys.argv) < 4:
            print("Usage: ... record <position_id> <linkedin_url>", file=sys.stderr)
            sys.exit(1)
        cmd_record(position_id, sys.argv[3])
    elif command == 'get_rejections':
        cmd_get_rejections(position_id)
    elif command == 'analyze':
        cmd_analyze(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
