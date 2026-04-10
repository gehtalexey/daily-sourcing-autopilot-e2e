"""
Credit Tracking — Track API credits spent per pipeline run.

Usage:
    python -m pipeline.credits log <position_id> <provider> <operation> <credits> [details]
    python -m pipeline.credits today <position_id>
    python -m pipeline.credits total <position_id>

Providers: crustdata, salesql, gem
Operations: search, enrich, email_lookup, gem_push

Logs to the api_usage_logs table in Supabase.
"""

import sys
import json
from datetime import datetime, timezone

from core.db import get_supabase_client


def log(msg):
    print(f"[credits] {msg}", file=sys.stderr)


def cmd_log(position_id: str, provider: str, operation: str, credits: float, details: str = None):
    """Log credit usage for an API call."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        return

    data = {
        'provider': provider,
        'operation': operation,
        'credits_used': credits,
        'metadata': {
            'position_id': position_id,
            'details': details,
        },
    }

    try:
        client.insert('api_usage_logs', data)
        log(f"{provider}/{operation}: {credits} credits ({details or ''})")
        print(json.dumps({"ok": True, "credits": credits}))
    except Exception as e:
        log(f"Error logging credits: {e}")
        print(json.dumps({"error": str(e)}))


def cmd_today(position_id: str):
    """Get credits spent today for a position, broken down by provider."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        return

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    cutoff = f"{today}T00:00:00"

    try:
        rows = client.select('api_usage_logs', 'provider,operation,credits_used,metadata',
                             {'created_at': f'gte.{cutoff}'}, limit=10000)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return

    # Filter to this position and aggregate
    by_provider = {}
    total = 0

    for row in rows:
        meta = row.get('metadata') or {}
        if meta.get('position_id') != position_id:
            continue

        provider = row.get('provider', 'unknown')
        credits = float(row.get('credits_used', 0))

        if provider not in by_provider:
            by_provider[provider] = {'credits': 0, 'calls': 0}
        by_provider[provider]['credits'] += credits
        by_provider[provider]['calls'] += 1
        total += credits

    result = {
        "date": today,
        "position_id": position_id,
        "total_credits": round(total, 1),
        "by_provider": by_provider,
    }

    log(f"Today: {total:.0f} credits for {position_id}")
    for p, s in by_provider.items():
        log(f"  {p}: {s['credits']:.0f} credits ({s['calls']} calls)")

    print(json.dumps(result))


def cmd_total(position_id: str):
    """Get all-time credits spent for a position."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        return

    try:
        rows = client.select('api_usage_logs', 'provider,credits_used,metadata',
                             limit=50000)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return

    by_provider = {}
    total = 0

    for row in rows:
        meta = row.get('metadata') or {}
        if meta.get('position_id') != position_id:
            continue

        provider = row.get('provider', 'unknown')
        credits = float(row.get('credits_used', 0))

        if provider not in by_provider:
            by_provider[provider] = 0
        by_provider[provider] += credits
        total += credits

    result = {
        "position_id": position_id,
        "total_credits": round(total, 1),
        "by_provider": {k: round(v, 1) for k, v in by_provider.items()},
    }

    log(f"Total: {total:.0f} credits for {position_id}")
    print(json.dumps(result))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.credits <command> <position_id> [args]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'log':
        if len(sys.argv) < 5:
            print("Usage: ... log <position_id> <provider> <operation> <credits> [details]",
                  file=sys.stderr)
            sys.exit(1)
        details = sys.argv[6] if len(sys.argv) > 6 else None
        cmd_log(position_id, sys.argv[3], sys.argv[4], float(sys.argv[5]), details)
    elif command == 'today':
        cmd_today(position_id)
    elif command == 'total':
        cmd_total(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
