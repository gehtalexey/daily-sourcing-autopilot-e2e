"""
Search Step — Helpers for Crustdata MCP-driven candidate search.

The actual search is done by Claude Code using the crustdata_people_search_db
MCP tool. This module provides helpers to get filters and save results.

Sub-commands:
    python -m pipeline.search_step get_config <position_id>
        → Outputs JSON: {search_filters, exclude_urls, position_id}

    python -m pipeline.search_step save_candidates <position_id>
        → Reads JSON array from stdin (Crustdata MCP search results)
        → Saves new candidates to pipeline_candidates
        → Output: {saved, skipped}
"""

import sys
import json
from datetime import datetime

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_exclude_urls,
    upsert_pipeline_candidate,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[search] {msg}", file=sys.stderr)


def cmd_get_config(position_id: str):
    """Get search config: filters + exclude URLs for Claude to use with MCP."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    search_filters = position.get('search_filters')
    if not search_filters:
        print(json.dumps({"error": "No search_filters configured for this position"}))
        sys.exit(1)

    exclude_urls = get_pipeline_exclude_urls(client, position_id)
    log(f"Loaded {len(exclude_urls)} exclude URLs for {position_id}")

    # Support tiered search format: {searches: [...], target_qualified: N}
    # or legacy flat format: {op: "and", conditions: [...]}
    searches = []
    target_qualified = 50

    if 'searches' in search_filters:
        # Tiered format
        searches = search_filters['searches']
        target_qualified = search_filters.get('target_qualified', 50)
    else:
        # Legacy flat format — single search
        searches = [{"name": "default", "filters": search_filters}]

    result = {
        "position_id": position_id,
        "searches": searches,
        "target_qualified": target_qualified,
        "exclude_urls": exclude_urls,
        "exclude_count": len(exclude_urls),
    }
    print(json.dumps(result))


def cmd_save_candidates(position_id: str):
    """Save candidates from MCP search results (stdin JSON array).

    Expects array of objects with linkedin_profile_url or linkedin_url.
    These come directly from crustdata_people_search_db MCP results.
    """
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    candidates = json.loads(raw)

    if not isinstance(candidates, list):
        # Handle single object or wrapped response
        if isinstance(candidates, dict):
            candidates = candidates.get('profiles', candidates.get('data', [candidates]))

    # Load exclude URLs for dedup
    exclude_urls = set(get_pipeline_exclude_urls(client, position_id))

    today = datetime.utcnow().strftime('%Y-%m-%d')
    saved = 0
    skipped = 0

    for c in candidates:
        # Prefer linkedin_flagship_url (clean /in/username format)
        # over linkedin_profile_url (obfuscated ACoAAA... format)
        url = (
            c.get('linkedin_flagship_url') or
            c.get('linkedin_profile_url') or
            c.get('linkedin_url') or
            ''
        )
        # Normalize if it's a clean URL, otherwise keep as-is (obfuscated IDs are case-sensitive)
        normalized = normalize_linkedin_url(url)
        url = normalized or url.strip()
        if not url:
            skipped += 1
            continue

        if url in exclude_urls:
            skipped += 1
            continue

        try:
            upsert_pipeline_candidate(
                client, position_id, url,
                source='crustdata_search',
                run_date=today,
            )
            saved += 1
            exclude_urls.add(url)
        except Exception as e:
            log(f"Error saving {url}: {e}")
            skipped += 1

    log(f"Saved {saved} candidates, skipped {skipped}")
    print(json.dumps({"saved": saved, "skipped": skipped}))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.search_step <command> <position_id>", file=sys.stderr)
        print("Commands: get_config, save_candidates", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_config':
        cmd_get_config(position_id)
    elif command == 'save_candidates':
        cmd_save_candidates(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
