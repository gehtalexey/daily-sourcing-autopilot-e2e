"""
Email Step — Find personal emails for qualified candidates.

Usage:
    python -m pipeline.email_step <position_id>

Flow:
  1. Check GEM for existing personal emails (free, no credits)
  2. Use SalesQL for remaining candidates (costs credits)

Only looks for personal emails (Gmail, Yahoo, etc.) — not work emails.
Updates pipeline_candidates.personal_email.
Prints JSON stats to stdout.
"""

import sys
import json
from pathlib import Path

from core.db import (
    get_supabase_client,
    get_pipeline_candidates,
    update_pipeline_candidate,
)
from integrations.salesql import get_salesql_client


# Common personal email domains
PERSONAL_DOMAINS = {
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'live.com',
    'icloud.com', 'me.com', 'mac.com', 'aol.com', 'mail.com',
    'protonmail.com', 'proton.me', 'yandex.com', 'gmx.com',
    'yahoo.co.il', 'walla.co.il', 'walla.com', '012.net.il',
    'netvision.net.il', 'bezeqint.net',
}


def is_personal_email(email: str) -> bool:
    """Check if an email is personal (not work/corporate)."""
    if not email or '@' not in email:
        return False
    domain = email.split('@')[1].lower()
    return domain in PERSONAL_DOMAINS


def log(msg):
    print(f"[email] {msg}", file=sys.stderr)


def check_gem_emails(candidates: list) -> dict:
    """Check GEM for existing personal emails. Returns {linkedin_url: email}."""
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        config = json.load(open(config_path))
        api_key = config.get('gem_api_key')
        if not api_key:
            return {}
    except Exception:
        return {}

    import requests
    headers = {'X-API-Key': api_key}
    found = {}

    for c in candidates:
        url = c.get('linkedin_url', '')
        if not url or '/in/' not in url:
            continue

        # Normalize URL first to strip query params, trailing slashes, etc.
        from core.normalizers import normalize_linkedin_url
        normalized = normalize_linkedin_url(url)
        if not normalized or '/in/' not in normalized:
            continue

        handle = normalized.split('/in/')[-1].strip('/')
        if not handle:
            continue

        try:
            resp = requests.get(
                'https://api.gem.com/v0/candidates',
                headers=headers,
                params={'linked_in_handle': handle, 'limit': 1},
                timeout=15,
            )
            if resp.status_code == 200:
                results = resp.json()
                if results:
                    emails = results[0].get('emails', [])
                    for e in emails:
                        addr = e.get('email_address', '')
                        if is_personal_email(addr):
                            found[url] = addr
                            break
        except Exception:
            continue

    return found


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.email_step <position_id>", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Get qualified candidates without personal email
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
        'personal_email': 'is.null',
    })

    if not candidates:
        log("No qualified candidates needing email lookup")
        print(json.dumps({"looked_up": 0, "from_gem": 0, "from_salesql": 0, "not_found": 0}))
        return

    log(f"{len(candidates)} qualified candidates need email...")

    # Step 1: Check GEM first (free)
    log("Checking GEM for existing personal emails...")
    gem_emails = check_gem_emails(candidates)

    from_gem = 0
    for c in candidates:
        url = c.get('linkedin_url')
        if url and url in gem_emails:
            update_pipeline_candidate(client, position_id, url, {
                'personal_email': gem_emails[url],
            })
            from_gem += 1
            log(f"  GEM: {url} -> {gem_emails[url]}")

    log(f"Found {from_gem} emails from GEM")

    # Step 2: SalesQL for remaining (costs credits)
    remaining = [c for c in candidates
                 if c.get('linkedin_url') and c.get('linkedin_url') not in gem_emails]

    from_salesql = 0
    not_found = 0

    if remaining:
        salesql = get_salesql_client()
        if not salesql:
            log("SalesQL not configured, skipping remaining lookups")
        else:
            urls = [c.get('linkedin_url') for c in remaining]
            log(f"Looking up {len(urls)} via SalesQL...")

            def on_progress(current, total, result):
                email = result.get('email', 'none')
                log(f"  SalesQL [{current}/{total}] {result.get('linkedin_url', '?')}: {email}")

            results = salesql.find_emails_batch(urls, delay=1.0, on_progress=on_progress)

            for result in results:
                url = result.get('linkedin_url')
                if not url:
                    continue

                if result.get('success') and result.get('email'):
                    update_pipeline_candidate(client, position_id, url, {
                        'personal_email': result['email'],
                    })
                    from_salesql += 1
                else:
                    not_found += 1

    total_found = from_gem + from_salesql
    total_looked = len(candidates)
    hit_rate = f"{total_found / total_looked * 100:.0f}%" if total_looked else "0%"

    stats = {
        "looked_up": total_looked,
        "from_gem": from_gem,
        "from_salesql": from_salesql,
        "salesql_calls": len(remaining) if remaining else 0,
        "not_found": not_found,
        "total_found": total_found,
        "hit_rate": hit_rate,
    }

    log(f"Email: {total_found}/{total_looked} found ({hit_rate}) — {from_gem} GEM, {from_salesql} SalesQL")
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
