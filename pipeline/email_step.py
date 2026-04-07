"""
Email Step — SalesQL personal email lookup for qualified candidates.

Usage:
    python -m pipeline.email_step <position_id>

Finds personal emails via SalesQL for candidates marked as 'qualified'.
Updates pipeline_candidates.personal_email.
Prints JSON stats to stdout.
"""

import sys
import json

from core.db import (
    get_supabase_client,
    get_pipeline_candidates,
    update_pipeline_candidate,
)
from integrations.salesql import get_salesql_client


def log(msg):
    print(f"[email] {msg}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.email_step <position_id>", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    salesql = get_salesql_client()
    if not salesql:
        log("SalesQL not configured, skipping email lookup")
        print(json.dumps({"error": "SalesQL not configured", "found": 0, "not_found": 0}))
        return

    # Get qualified candidates without personal email
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
        'personal_email': 'is.null',
    })

    if not candidates:
        log("No qualified candidates needing email lookup")
        print(json.dumps({"looked_up": 0, "found": 0, "not_found": 0}))
        return

    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    log(f"Looking up emails for {len(urls)} qualified candidates...")

    def on_progress(current, total, result):
        email = result.get('email', 'none')
        log(f"  [{current}/{total}] {result.get('linkedin_url', '?')}: {email}")

    results = salesql.find_emails_batch(urls, delay=1.0, on_progress=on_progress)

    found = 0
    not_found = 0

    for result in results:
        url = result.get('linkedin_url')
        if not url:
            continue

        if result.get('success') and result.get('email'):
            update_pipeline_candidate(client, position_id, url, {
                'personal_email': result['email'],
            })
            found += 1
        else:
            not_found += 1

    hit_rate = f"{found / len(urls) * 100:.0f}%" if urls else "0%"

    stats = {
        "looked_up": len(urls),
        "found": found,
        "not_found": not_found,
        "hit_rate": hit_rate,
    }

    log(f"Email lookup: {found}/{len(urls)} found ({hit_rate})")
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
