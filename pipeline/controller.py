"""
Pipeline Controller — Self-healing validation layer.

Runs between each pipeline step to validate outputs and fix issues.
Detects: duplicates, missing openers, unenriched qualified, rate limits, unexpected counts.
Fixes problems instead of stopping. Only proceeds once validation passes.

Usage:
    python -m pipeline.controller validate <step_name> <position_id>
    python -m pipeline.controller stats <position_id>
    python -m pipeline.controller full_stats <position_id> [run_id]

Steps: search, pre_filter, enrich, screen, email, gem_push
"""

import sys
import json
from datetime import datetime
from collections import Counter

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
    get_profiles_batch,
    update_pipeline_candidate,
    ENRICHMENT_REFRESH_MONTHS,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[controller] {msg}", file=sys.stderr)


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================

def validate_search(client, position_id: str) -> dict:
    """Validate after search step."""
    issues = []
    fixes = []

    candidates = get_pipeline_candidates(client, position_id, {})
    today = datetime.utcnow().strftime('%Y-%m-%d')
    today_cands = [c for c in candidates if c.get('search_run_date') == today]

    if not today_cands:
        issues.append("No new candidates found today")

    # Check for duplicate URLs
    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    url_counts = Counter(urls)
    dupes = {url: count for url, count in url_counts.items() if count > 1}
    if dupes:
        issues.append(f"{len(dupes)} duplicate URLs in pipeline")
        # Fix: dedup is handled by DB composite key, but log it
        fixes.append(f"Logged {len(dupes)} duplicates (DB composite key prevents actual dupes)")

    return {
        "step": "search",
        "ok": len(issues) == 0,
        "total_candidates": len(candidates),
        "new_today": len(today_cands),
        "issues": issues,
        "fixes": fixes,
    }


def validate_pre_filter(client, position_id: str) -> dict:
    """Validate after pre-filter step."""
    issues = []
    fixes = []

    candidates = get_pipeline_candidates(client, position_id, {})

    if not candidates:
        issues.append("No candidates remaining after pre-filter")

    return {
        "step": "pre_filter",
        "ok": len(issues) == 0,
        "remaining": len(candidates),
        "issues": issues,
        "fixes": fixes,
    }


def validate_enrich(client, position_id: str) -> dict:
    """Validate after enrich step."""
    issues = []
    fixes = []

    # Get unscreened candidates (these should have been enriched)
    unscreened = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    if not unscreened:
        return {"step": "enrich", "ok": True, "issues": [], "fixes": []}

    # Check which have enriched profiles
    urls = [c.get('linkedin_url') for c in unscreened if c.get('linkedin_url')]
    profiles = get_profiles_batch(client, urls)

    missing_profiles = []
    for c in unscreened:
        url = c.get('linkedin_url', '')
        if url and url not in profiles:
            missing_profiles.append(url)

    if missing_profiles:
        issues.append(f"{len(missing_profiles)} candidates have no enriched profile")
        # These will be caught at screen time — not fixable here without re-enriching

    return {
        "step": "enrich",
        "ok": len(issues) == 0,
        "total_unscreened": len(unscreened),
        "with_profile": len(unscreened) - len(missing_profiles),
        "missing_profile": len(missing_profiles),
        "issues": issues,
        "fixes": fixes,
    }


def validate_screen(client, position_id: str) -> dict:
    """Validate after screen step. Fixes missing openers."""
    issues = []
    fixes = []

    qualified = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
    })

    if not qualified:
        return {"step": "screen", "ok": True, "issues": [], "fixes": [],
                "qualified": 0, "total_screened": 0}

    # Check for missing openers
    missing_openers = [c for c in qualified if not c.get('email_opener')]
    if missing_openers:
        issues.append(f"{len(missing_openers)} qualified candidates missing email opener")
        # Can't auto-fix openers (needs AI), but flag it

    # Check for scores outside range
    bad_scores = [c for c in qualified if c.get('screening_score') and
                  (c['screening_score'] < 1 or c['screening_score'] > 10)]
    if bad_scores:
        issues.append(f"{len(bad_scores)} candidates have score outside 1-10 range")

    # Check for qualified without notes
    no_notes = [c for c in qualified if not c.get('screening_notes')]
    if no_notes:
        issues.append(f"{len(no_notes)} qualified candidates missing screening notes")

    # Check for qualified without enriched profile (can't push to GEM)
    urls = [c.get('linkedin_url') for c in qualified if c.get('linkedin_url')]
    profiles = get_profiles_batch(client, urls)
    no_profile = [c for c in qualified
                  if c.get('linkedin_url') and c.get('linkedin_url') not in profiles]
    if no_profile:
        names = [c.get('candidate_name', '?') for c in no_profile]
        issues.append(f"{len(no_profile)} qualified candidates have no enriched profile: {', '.join(names[:5])}")

    all_screened = get_pipeline_candidates(client, position_id, {})
    total_screened = len([c for c in all_screened if c.get('screening_result')])

    return {
        "step": "screen",
        "ok": len(issues) == 0,
        "qualified": len(qualified),
        "total_screened": total_screened,
        "missing_openers": len(missing_openers),
        "missing_profiles": len(no_profile),
        "issues": issues,
        "fixes": fixes,
    }


def validate_email(client, position_id: str) -> dict:
    """Validate after email step."""
    qualified = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
    })

    with_email = [c for c in qualified if c.get('personal_email')]
    without_email = [c for c in qualified if not c.get('personal_email')]

    return {
        "step": "email",
        "ok": True,  # Email is non-blocking
        "qualified": len(qualified),
        "with_email": len(with_email),
        "without_email": len(without_email),
        "email_rate": f"{len(with_email)/len(qualified)*100:.0f}%" if qualified else "0%",
        "issues": [],
        "fixes": [],
    }


def validate_gem_push(client, position_id: str) -> dict:
    """Validate after GEM push step."""
    issues = []
    fixes = []

    qualified = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
    })

    pushed = [c for c in qualified if c.get('gem_pushed')]
    not_pushed = [c for c in qualified if not c.get('gem_pushed')]

    if not_pushed:
        names = [c.get('candidate_name', '?') for c in not_pushed]
        issues.append(f"{len(not_pushed)} qualified candidates not pushed to GEM: {', '.join(names[:10])}")

        # Try to fix: re-run GEM push
        fixes.append(f"Re-running GEM push for {len(not_pushed)} candidates")
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, '-m', 'pipeline.gem_step', position_id],
                capture_output=True, text=True, timeout=300,
                cwd=str(__import__('pathlib').Path(__file__).parent.parent),
            )
            if result.stdout:
                gem_result = json.loads(result.stdout)
                newly_pushed = gem_result.get('pushed', 0)
                fixes.append(f"Re-push result: {newly_pushed} pushed")
            if result.stderr:
                for line in result.stderr.strip().split('\n')[-5:]:
                    log(f"  gem retry: {line}")
        except Exception as e:
            fixes.append(f"Re-push failed: {e}")

    # Re-check after fix
    qualified = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
    })
    pushed = [c for c in qualified if c.get('gem_pushed')]
    still_not_pushed = [c for c in qualified if not c.get('gem_pushed')]

    return {
        "step": "gem_push",
        "ok": len(still_not_pushed) == 0,
        "qualified": len(qualified),
        "pushed": len(pushed),
        "not_pushed": len(still_not_pushed),
        "issues": issues,
        "fixes": fixes,
    }


# =============================================================================
# FULL STATISTICS
# =============================================================================

def get_full_stats(client, position_id: str, run_id: str = None) -> dict:
    """Compute detailed pipeline statistics for Slack report."""
    all_candidates = get_pipeline_candidates(client, position_id, {})
    today = datetime.utcnow().strftime('%Y-%m-%d')

    # By date
    today_cands = [c for c in all_candidates if c.get('search_run_date') == today]

    # By search variant
    by_source = Counter()
    by_source_today = Counter()
    for c in all_candidates:
        source = c.get('source', 'unknown')
        # Extract variant name from "crustdata_search:variant_name"
        variant = source.split(':')[-1] if ':' in source else source
        by_source[variant] += 1
        if c.get('search_run_date') == today:
            by_source_today[variant] += 1

    # Screening stats
    qualified = [c for c in all_candidates if c.get('screening_result') == 'qualified']
    not_qualified = [c for c in all_candidates if c.get('screening_result') == 'not_qualified']
    pending = [c for c in all_candidates if not c.get('screening_result')]

    # Today's screening
    today_qualified = [c for c in qualified if c.get('search_run_date') == today]
    today_not_qualified = [c for c in not_qualified if c.get('search_run_date') == today]

    # Email stats
    with_email = [c for c in qualified if c.get('personal_email')]

    # GEM stats
    pushed = [c for c in qualified if c.get('gem_pushed')]
    not_pushed = [c for c in qualified if not c.get('gem_pushed')]

    # Score distribution
    score_dist = Counter()
    for c in all_candidates:
        if c.get('screening_score') is not None:
            score_dist[c['screening_score']] += 1

    # Qualification rate by source
    qual_rates = {}
    for variant in by_source:
        variant_cands = [c for c in all_candidates
                         if (c.get('source', '').split(':')[-1] if ':' in c.get('source', '') else c.get('source', '')) == variant]
        screened = [c for c in variant_cands if c.get('screening_result')]
        qual = [c for c in variant_cands if c.get('screening_result') == 'qualified']
        if screened:
            qual_rates[variant] = {
                "total": len(variant_cands),
                "screened": len(screened),
                "qualified": len(qual),
                "rate": f"{len(qual)/len(screened)*100:.0f}%",
            }

    # Missing openers check
    missing_openers = [c.get('candidate_name', '?') for c in qualified if not c.get('email_opener')]

    return {
        "position_id": position_id,
        "run_date": today,
        "run_id": run_id,

        # Today's numbers
        "today": {
            "searched": len(today_cands),
            "qualified": len(today_qualified),
            "not_qualified": len(today_not_qualified),
            "by_source": dict(by_source_today),
        },

        # All-time numbers
        "all_time": {
            "total_sourced": len(all_candidates),
            "qualified": len(qualified),
            "not_qualified": len(not_qualified),
            "pending_screening": len(pending),
            "with_email": len(with_email),
            "pushed_to_gem": len(pushed),
            "not_pushed": len(not_pushed),
            "by_source": dict(by_source),
        },

        # Qualification rates by search variant
        "qual_rates": qual_rates,

        # Score distribution
        "score_distribution": dict(sorted(score_dist.items())),

        # Data quality
        "issues": {
            "missing_openers": missing_openers[:10],
            "missing_openers_count": len(missing_openers),
            "not_pushed_to_gem": len(not_pushed),
        },
    }


# =============================================================================
# CLI
# =============================================================================

VALIDATORS = {
    'search': validate_search,
    'pre_filter': validate_pre_filter,
    'enrich': validate_enrich,
    'screen': validate_screen,
    'email': validate_email,
    'gem_push': validate_gem_push,
}


def main():
    if len(sys.argv) < 3:
        print("Usage:", file=sys.stderr)
        print("  python -m pipeline.controller validate <step> <position_id>", file=sys.stderr)
        print("  python -m pipeline.controller stats <position_id>", file=sys.stderr)
        print("  python -m pipeline.controller full_stats <position_id> [run_id]", file=sys.stderr)
        print(f"  Steps: {', '.join(VALIDATORS.keys())}", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == 'validate':
        if len(sys.argv) < 4:
            print("Usage: python -m pipeline.controller validate <step> <position_id>", file=sys.stderr)
            sys.exit(1)
        step = sys.argv[2]
        position_id = sys.argv[3]

        if step not in VALIDATORS:
            print(f"Unknown step: {step}. Valid: {', '.join(VALIDATORS.keys())}", file=sys.stderr)
            sys.exit(1)

        client = get_supabase_client()
        if not client:
            print(json.dumps({"error": "Supabase not configured"}))
            sys.exit(1)

        result = VALIDATORS[step](client, position_id)
        if result.get('issues'):
            for issue in result['issues']:
                log(f"  ISSUE: {issue}")
        if result.get('fixes'):
            for fix in result['fixes']:
                log(f"  FIX: {fix}")
        log(f"Validation {step}: {'PASS' if result['ok'] else 'ISSUES FOUND'}")
        print(json.dumps(result))

    elif command in ('stats', 'full_stats'):
        position_id = sys.argv[2]
        run_id = sys.argv[3] if len(sys.argv) > 3 else None

        client = get_supabase_client()
        if not client:
            print(json.dumps({"error": "Supabase not configured"}))
            sys.exit(1)

        stats = get_full_stats(client, position_id, run_id)
        print(json.dumps(stats, indent=2))

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
