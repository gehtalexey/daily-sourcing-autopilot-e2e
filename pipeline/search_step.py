"""
Search Step — Helpers for Crustdata MCP-driven candidate search.

The actual search is done by Claude Code using the crustdata_people_search_db
MCP tool. This module provides helpers to get filters, save results, and
track search progress across daily runs.

Sub-commands:
    python -m pipeline.search_step get_config <position_id>
        → Outputs JSON with smart-ordered searches, exclude URLs, and progress

    python -m pipeline.search_step save_candidates <position_id> [search_name]
        → Reads JSON from stdin (Crustdata MCP search results)
        → Saves new candidates to pipeline_candidates
        → Output: {saved, skipped, search_name}

    python -m pipeline.search_step save_progress <position_id> <search_name>
        → Reads JSON from stdin: {next_cursor, total_found, new_saved}
        → Updates progress for this search variant in pipeline_positions

    python -m pipeline.search_step update_qual_rates <position_id>
        → Recalculates qualification rates per search variant from screening results
"""

import sys
import json
from datetime import datetime

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_exclude_urls,
    get_pipeline_candidates,
    upsert_pipeline_candidate,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[search] {msg}", file=sys.stderr)


def cmd_get_config(position_id: str):
    """Get search config with smart ordering based on progress and qual rates."""
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

    # Parse search config
    searches = []
    target_qualified = 50

    if 'searches' in search_filters:
        searches = search_filters['searches']
        target_qualified = search_filters.get('target_qualified', 50)
    else:
        searches = [{"name": "default", "filters": search_filters}]

    # Smart ordering: skip exhausted, prioritize by qual_rate (high first)
    active_searches = []
    exhausted_searches = []

    for s in searches:
        progress = s.get('progress', {})
        if progress.get('exhausted'):
            exhausted_searches.append(s)
            log(f"  SKIP (exhausted): {s['name']} — {progress.get('total_found', 0)} found, "
                f"{progress.get('qual_rate', 0):.0%} qual rate")
        else:
            active_searches.append(s)
            cursor_info = f" (cursor: resuming)" if progress.get('last_cursor') else " (fresh)"
            log(f"  ACTIVE: {s['name']}{cursor_info}")

    # Sort active by qual_rate descending (best performing first)
    # New searches (no qual_rate) go first to gather data
    def sort_key(s):
        p = s.get('progress', {})
        rate = p.get('qual_rate')
        if rate is None:
            return (0, 0)  # New searches first
        return (1, -rate)  # Then by qual rate descending

    active_searches.sort(key=sort_key)

    if not active_searches:
        log("All searches exhausted! Consider adding new filter variants.")

    result = {
        "position_id": position_id,
        "searches": active_searches,
        "exhausted_searches": [s['name'] for s in exhausted_searches],
        "target_qualified": target_qualified,
        "exclude_urls": exclude_urls,
        "exclude_count": len(exclude_urls),
    }
    print(json.dumps(result))


def cmd_save_candidates(position_id: str, search_name: str = None):
    """Save candidates from MCP search results (stdin JSON array)."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    candidates = json.loads(raw)

    if not isinstance(candidates, list):
        if isinstance(candidates, dict):
            candidates = candidates.get('profiles', candidates.get('data', [candidates]))

    exclude_urls = set(get_pipeline_exclude_urls(client, position_id))

    today = datetime.utcnow().strftime('%Y-%m-%d')
    saved = 0
    skipped = 0

    for c in candidates:
        # Prefer flagship URL (compact=false returns it)
        url = (
            c.get('flagship_profile_url') or
            c.get('linkedin_flagship_url') or
            c.get('linkedin_profile_url') or
            c.get('linkedin_url') or
            ''
        )
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
                source=f'crustdata_search:{search_name}' if search_name else 'crustdata_search',
                run_date=today,
            )
            saved += 1
            exclude_urls.add(url)
        except Exception as e:
            log(f"Error saving {url}: {e}")
            skipped += 1

    log(f"Saved {saved} candidates, skipped {skipped}")
    print(json.dumps({"saved": saved, "skipped": skipped, "search_name": search_name}))


def cmd_save_progress(position_id: str, search_name: str):
    """Save search progress (cursor, counts) for a search variant.

    Reads JSON from stdin: {next_cursor, total_found, new_saved}
    Updates the progress field in pipeline_positions.search_filters.
    """
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    progress_data = json.loads(raw)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    search_filters = position.get('search_filters', {})
    searches = search_filters.get('searches', [])

    # Find and update the matching search variant
    updated = False
    for s in searches:
        if s.get('name') == search_name:
            progress = s.get('progress', {})

            # Update cursor
            next_cursor = progress_data.get('next_cursor')
            if next_cursor:
                progress['last_cursor'] = next_cursor
            elif progress_data.get('new_saved', 0) == 0:
                # No cursor and no new results = exhausted
                progress['exhausted'] = True

            # Accumulate counts
            progress['total_found'] = progress.get('total_found', 0) + progress_data.get('new_saved', 0)
            progress['last_run'] = datetime.utcnow().strftime('%Y-%m-%d')

            # Mark exhausted if explicitly set or no cursor returned
            if progress_data.get('exhausted'):
                progress['exhausted'] = True

            s['progress'] = progress
            updated = True
            break

    if not updated:
        log(f"Search variant '{search_name}' not found")
        print(json.dumps({"error": f"Search variant '{search_name}' not found"}))
        return

    # Save back to DB
    search_filters['searches'] = searches
    import requests as http_req
    url = f"{client.url}/rest/v1/pipeline_positions"
    params = {'position_id': f'eq.{position_id}'}
    http_req.patch(url, headers=client.headers, params=params,
                   json={'search_filters': search_filters}, timeout=30)

    log(f"Progress saved for {search_name}: {json.dumps(s.get('progress', {}))}")
    print(json.dumps({"ok": True, "progress": s.get('progress', {})}))


def cmd_update_qual_rates(position_id: str):
    """Recalculate qualification rates per search variant from actual screening results."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    search_filters = position.get('search_filters', {})
    searches = search_filters.get('searches', [])

    # Get all screened candidates with their source
    all_candidates = get_pipeline_candidates(client, position_id)

    # Count per source
    source_stats = {}
    for c in all_candidates:
        source = c.get('source', 'crustdata_search')
        # Source format: "crustdata_search:devops_leads"
        variant = source.split(':', 1)[1] if ':' in source else 'default'

        if variant not in source_stats:
            source_stats[variant] = {'total': 0, 'screened': 0, 'qualified': 0}

        source_stats[variant]['total'] += 1
        if c.get('screening_result'):
            source_stats[variant]['screened'] += 1
            if c.get('screening_result') == 'qualified':
                source_stats[variant]['qualified'] += 1

    # Update qual rates in search_filters
    for s in searches:
        name = s.get('name', 'default')
        stats = source_stats.get(name, {})
        progress = s.get('progress', {})

        screened = stats.get('screened', 0)
        qualified = stats.get('qualified', 0)

        if screened > 0:
            progress['qual_rate'] = round(qualified / screened, 2)
            progress['qualified'] = qualified
            progress['screened'] = screened
            s['progress'] = progress

            log(f"  {name}: {qualified}/{screened} qualified ({progress['qual_rate']:.0%})")
        else:
            log(f"  {name}: no screening data yet")

    # Save back
    search_filters['searches'] = searches
    import requests as http_req
    url = f"{client.url}/rest/v1/pipeline_positions"
    params = {'position_id': f'eq.{position_id}'}
    http_req.patch(url, headers=client.headers, params=params,
                   json={'search_filters': search_filters}, timeout=30)

    log("Qualification rates updated")
    print(json.dumps(source_stats))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.search_step <command> <position_id> [args]", file=sys.stderr)
        print("Commands: get_config, save_candidates, save_progress, update_qual_rates", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_config':
        cmd_get_config(position_id)
    elif command == 'save_candidates':
        search_name = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_save_candidates(position_id, search_name)
    elif command == 'save_progress':
        if len(sys.argv) < 4:
            print("Usage: ... save_progress <position_id> <search_name>", file=sys.stderr)
            sys.exit(1)
        cmd_save_progress(position_id, sys.argv[3])
    elif command == 'update_qual_rates':
        cmd_update_qual_rates(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
