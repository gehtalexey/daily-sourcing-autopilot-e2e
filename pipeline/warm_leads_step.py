"""
GEM Warm Leads — Pull candidates from a shared GEM "warm leads" project.

These are people who replied YES to outreach for other positions. They are warm
leads with high response likelihood and should be sourced FIRST, before talent
pool or external search.

This is a GLOBAL project shared across all positions.

Usage:
    python -m pipeline.warm_leads_step search <position_id>
        -> Lists candidates from the GEM warm leads project
        -> Deduplicates against existing pipeline_candidates
        -> Output: {candidates: [{name, linkedin_url, email}], count: N}

    python -m pipeline.warm_leads_step add <position_id>
        -> Reads JSON array of LinkedIn URLs from stdin
        -> Upserts into pipeline_candidates with source='gem_warm_leads'
        -> Output: {added: N, already_in_pipeline: N}
"""

import sys
import json
from datetime import datetime, timezone
from pathlib import Path

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
    upsert_pipeline_candidate,
)
from core.normalizers import normalize_linkedin_url
from integrations.gem import get_gem_client


def log(msg):
    print(f"[warm_leads] {msg}", file=sys.stderr)


def _load_config() -> dict:
    """Load config.json from project root."""
    config_path = Path(__file__).parent.parent / 'config.json'
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def cmd_search(position_id: str):
    """List candidates from the GEM warm leads project, deduped against this position's pipeline."""
    config = _load_config()
    project_id = config.get('gem_warm_leads_project_id', '')

    if not project_id:
        log("No gem_warm_leads_project_id configured, skipping")
        print(json.dumps({"candidates": [], "count": 0, "skipped": "not_configured"}))
        return

    # Check if position has skip_warm_leads flag
    client = get_supabase_client()
    if not client:
        log("Supabase not configured")
        print(json.dumps({"candidates": [], "count": 0, "error": "supabase_not_configured"}))
        return

    position = get_pipeline_position(client, position_id)
    if not position:
        log(f"Position '{position_id}' not found")
        print(json.dumps({"candidates": [], "count": 0, "error": "position_not_found"}))
        return

    search_filters = position.get('search_filters') or {}
    if isinstance(search_filters, str):
        try:
            search_filters = json.loads(search_filters)
        except (json.JSONDecodeError, TypeError):
            search_filters = {}

    if search_filters.get('skip_warm_leads'):
        log(f"Position '{position_id}' has skip_warm_leads=true, skipping")
        print(json.dumps({"candidates": [], "count": 0, "skipped": "skip_warm_leads"}))
        return

    # Get GEM client
    gem = get_gem_client()
    if not gem:
        log("GEM client not available")
        print(json.dumps({"candidates": [], "count": 0, "error": "gem_not_available"}))
        return

    # Fetch candidates from the warm leads project
    try:
        raw_candidates = gem.list_project_candidates(project_id)
    except Exception as e:
        log(f"GEM API error: {e}")
        print(json.dumps({"candidates": [], "count": 0, "error": str(e)}))
        return

    log(f"Fetched {len(raw_candidates)} candidates from GEM warm leads project")

    # Get existing pipeline candidates for dedup
    existing = get_pipeline_candidates(client, position_id, {})
    existing_urls = set()
    for c in existing:
        url = c.get('linkedin_url')
        if url:
            normalized = normalize_linkedin_url(url)
            if normalized:
                existing_urls.add(normalized)

    # Extract and dedup candidates
    candidates = []
    for c in raw_candidates:
        # Build linkedin_url from linked_in_handle
        handle = c.get('linked_in_handle', '')
        if handle:
            linkedin_url = f"https://www.linkedin.com/in/{handle}"
        else:
            continue  # No LinkedIn handle, skip

        normalized_url = normalize_linkedin_url(linkedin_url)
        if not normalized_url:
            continue

        if normalized_url in existing_urls:
            continue

        # Extract email
        email = ''
        emails = c.get('emails') or []
        for e in emails:
            if isinstance(e, dict):
                addr = e.get('email_address', '')
                if addr:
                    email = addr
                    if e.get('is_primary'):
                        break  # Use primary email
            elif isinstance(e, str):
                email = e

        name_parts = []
        if c.get('first_name'):
            name_parts.append(c['first_name'])
        if c.get('last_name'):
            name_parts.append(c['last_name'])
        name = ' '.join(name_parts) or c.get('name', '')

        candidates.append({
            'name': name,
            'linkedin_url': normalized_url,
            'email': email,
        })

        # Also add to existing_urls to avoid dupes within the batch
        existing_urls.add(normalized_url)

    log(f"Found {len(candidates)} new warm leads (after dedup)")
    print(json.dumps({
        "candidates": candidates,
        "count": len(candidates),
        "total_in_project": len(raw_candidates),
    }))


def cmd_add(position_id: str):
    """Add warm lead URLs to pipeline. Reads JSON array of URLs from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    urls = json.loads(raw)
    if not isinstance(urls, list):
        urls = [urls]

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    added = 0
    already = 0

    for url in urls:
        normalized = normalize_linkedin_url(url)
        if not normalized:
            continue

        try:
            upsert_pipeline_candidate(client, position_id, normalized, {
                'source': 'gem_warm_leads',
                'search_run_date': today,
            })
            added += 1
        except Exception:
            already += 1

    log(f"Added {added} from warm leads, {already} already in pipeline")
    print(json.dumps({"added": added, "already_in_pipeline": already}))


def main():
    if len(sys.argv) < 3:
        print("Usage:", file=sys.stderr)
        print("  python -m pipeline.warm_leads_step search <position_id>", file=sys.stderr)
        print("  python -m pipeline.warm_leads_step add <position_id>  (reads URLs from stdin)", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'search':
        cmd_search(position_id)
    elif command == 'add':
        cmd_add(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
