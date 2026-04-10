"""
Search Step — Helpers for Crustdata MCP-driven candidate search.

The actual search is done by Claude Code using the crustdata_people_search_db
MCP tool. This module provides helpers to get filters, save results,
track qualification rates, and manage filter variants.

Sub-commands:
    python -m pipeline.search_step get_config <position_id>
        → Outputs JSON with smart-ordered searches, exclude URLs, and qual stats

    python -m pipeline.search_step save_candidates <position_id> [search_name]
        → Reads JSON from stdin (Crustdata MCP search results)
        → Saves new candidates, deduplicates against all previously sourced
        → Output: {saved, skipped, search_name}

    python -m pipeline.search_step update_qual_rates <position_id>
        → Recalculates qualification rates per search variant from screening results

    python -m pipeline.search_step add_search <position_id> <search_name>
        → Reads JSON filters from stdin, adds a new search variant

    python -m pipeline.search_step retire_search <position_id> <search_name>
        → Marks a search variant as retired (won't run again)
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
    update_pipeline_candidate,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[search] {msg}", file=sys.stderr)


def _update_search_filters(client, position_id: str, search_filters: dict):
    """Write search_filters back to pipeline_positions."""
    import requests as http_req
    url = f"{client.url}/rest/v1/pipeline_positions"
    params = {'position_id': f'eq.{position_id}'}
    http_req.patch(url, headers=client.headers, params=params,
                   json={'search_filters': search_filters}, timeout=30)


def _get_google_sheet(config: dict):
    """Get an authorized Google Sheet connection. Returns (spreadsheet, None) or (None, error)."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        from pathlib import Path

        filter_config = config.get('filter_sheets', {})
        sheet_id = filter_config.get('spreadsheet_id')
        if not sheet_id:
            return None, "No spreadsheet_id configured"

        creds_path = Path(__file__).parent.parent / config.get('google_credentials_file', 'google_credentials.json')
        if not creds_path.exists():
            return None, f"Credentials file not found: {creds_path}"

        creds = Credentials.from_service_account_file(str(creds_path),
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly',
                    'https://www.googleapis.com/auth/drive.readonly'])
        gc = gspread.authorize(creds)
        return gc.open_by_key(sheet_id), None
    except Exception as e:
        return None, str(e)


def _load_sheet_values(spreadsheet, sheet_name: str) -> list:
    """Load all non-empty cell values from a sheet tab. Returns sorted unique list."""
    try:
        ws = spreadsheet.worksheet(sheet_name)
        values = set()
        for row in ws.get_all_values()[1:]:
            for cell in row:
                if cell and cell.strip():
                    values.add(cell.strip())
        return sorted(values)
    except Exception:
        return []


def _load_search_priority_lists(position: dict) -> dict:
    """Load priority lists from Google Sheet based on position's search_priorities config.

    Returns dict with keys: target_companies, target_universities, tech_alerts, client_wanted_companies.
    Each value is a list of strings (company names or university names).
    """
    result = {
        'target_companies': [],
        'target_universities': [],
        'tech_alerts': [],
        'client_wanted_companies': [],
    }

    try:
        import json as _json
        from pathlib import Path

        config_path = Path(__file__).parent.parent / 'config.json'
        config = _json.load(open(config_path))

        spreadsheet, error = _get_google_sheet(config)
        if not spreadsheet:
            log(f"  Warning: Could not open Google Sheet: {error}")
            return result

        # Check which priorities are enabled
        search_filters = position.get('search_filters') or {}
        priorities = search_filters.get('search_priorities', {})

        # Default: target_companies enabled if no priorities configured
        if not priorities:
            priorities = {'target_companies': True}

        filter_config = config.get('filter_sheets', {})

        if priorities.get('target_companies'):
            sheet_name = filter_config.get('target_companies', 'Target Companies')
            result['target_companies'] = _load_sheet_values(spreadsheet, sheet_name)
            log(f"  Target companies: {len(result['target_companies'])} loaded")

        if priorities.get('target_universities'):
            sheet_name = filter_config.get('universities', 'Universities')
            result['target_universities'] = _load_sheet_values(spreadsheet, sheet_name)
            log(f"  Target universities: {len(result['target_universities'])} loaded")

        if priorities.get('tech_alerts'):
            sheet_name = filter_config.get('tech_alerts', 'Tech Alerts')
            result['tech_alerts'] = _load_sheet_values(spreadsheet, sheet_name)
            log(f"  Tech alerts (layoffs): {len(result['tech_alerts'])} loaded")

        if priorities.get('client_wanted_companies'):
            sheet_name = filter_config.get('client_wanted_companies', 'Client specific wanted companies')
            result['client_wanted_companies'] = _load_sheet_values(spreadsheet, sheet_name)
            log(f"  Client wanted companies: {len(result['client_wanted_companies'])} loaded")

    except Exception as e:
        log(f"  Warning: Could not load search priority lists: {e}")

    return result


def cmd_get_config(position_id: str):
    """Get search config with smart ordering based on qual rates."""
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
    log(f"Loaded {len(exclude_urls)} exclude URLs (dedup pool)")

    # Parse search config
    searches = []
    target_qualified = 50
    daily_search_limit = 500

    if 'searches' in search_filters:
        searches = search_filters['searches']
        target_qualified = search_filters.get('target_qualified', 50)
        daily_search_limit = search_filters.get('daily_search_limit', 500)
    else:
        searches = [{"name": "default", "filters": search_filters}]

    # Split active vs retired
    active_searches = []
    retired_searches = []

    for s in searches:
        stats = s.get('stats', {})
        if stats.get('retired'):
            retired_searches.append(s['name'])
            log(f"  RETIRED: {s['name']}")
        else:
            qual_rate = stats.get('qual_rate')
            rate_str = f" ({qual_rate:.0%} qual)" if qual_rate is not None else " (no data)"
            log(f"  ACTIVE: {s['name']}{rate_str}")
            active_searches.append(s)

    # Sort: new searches first (explore), then by qual_rate descending (exploit)
    def sort_key(s):
        stats = s.get('stats', {})
        rate = stats.get('qual_rate')
        if rate is None:
            return (0, 0)  # New = explore first
        return (1, -rate)  # Then best qual rate

    active_searches.sort(key=sort_key)

    if not active_searches:
        log("All searches retired! Agent should create new variants.")

    # Load priority search lists from Google Sheet
    priority_lists = _load_search_priority_lists(position)

    result = {
        "position_id": position_id,
        "job_description": position.get('job_description', ''),
        "hm_notes": position.get('hm_notes', ''),
        "searches": active_searches,
        "retired": retired_searches,
        "target_qualified": target_qualified,
        "daily_search_limit": daily_search_limit,
        "exclude_urls": exclude_urls,
        "exclude_count": len(exclude_urls),
    }

    # Add priority lists (only non-empty ones)
    for key, values in priority_lists.items():
        if values:
            result[key] = values
            result[f"{key}_count"] = len(values)

    print(json.dumps(result))


def cmd_save_candidates(position_id: str, search_name: str = None):
    """Save candidates from MCP search results. Dedup via exclude_urls."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    candidates = json.loads(raw)

    if not isinstance(candidates, list):
        if isinstance(candidates, dict):
            candidates = candidates.get('profiles', candidates.get('data', [candidates]))

    # All previously sourced URLs — this is the ONLY dedup mechanism
    exclude_urls = set(get_pipeline_exclude_urls(client, position_id))

    today = datetime.utcnow().strftime('%Y-%m-%d')
    saved = 0
    skipped = 0

    for c in candidates:
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

            # Store name/company/school from search data for pre-filtering
            name = c.get('name', '')
            company = ''
            title = ''
            employers = c.get('current_employers') or []
            if employers and isinstance(employers[0], dict):
                company = employers[0].get('name') or employers[0].get('company_name') or ''
                title = employers[0].get('title') or ''

            # Extract schools from education
            schools = []
            edu = c.get('education_background') or []
            for e in edu:
                if isinstance(e, dict) and e.get('institute_name'):
                    schools.append(e['institute_name'])

            updates = {
                'candidate_name': name,
                'current_company': company,
                'current_title': title,
                'headline': c.get('headline', ''),
            }
            if schools:
                updates['education'] = ', '.join(schools)

            if name or company:
                update_pipeline_candidate(client, position_id, url, updates)

            saved += 1
            exclude_urls.add(url)
        except Exception as e:
            log(f"Error saving {url}: {e}")
            skipped += 1

    log(f"Saved {saved}, skipped {skipped} (search: {search_name})")
    print(json.dumps({"saved": saved, "skipped": skipped, "search_name": search_name}))


def cmd_update_qual_rates(position_id: str):
    """Recalculate qualification rates per search variant from screening results."""
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

    # Count screening results per source tag
    all_candidates = get_pipeline_candidates(client, position_id)

    source_stats = {}
    for c in all_candidates:
        source = c.get('source', 'crustdata_search')
        variant = source.split(':', 1)[1] if ':' in source else 'default'

        if variant not in source_stats:
            source_stats[variant] = {'total': 0, 'screened': 0, 'qualified': 0}

        source_stats[variant]['total'] += 1
        if c.get('screening_result'):
            source_stats[variant]['screened'] += 1
            if c.get('screening_result') == 'qualified':
                source_stats[variant]['qualified'] += 1

    # Update stats on each search variant
    for s in searches:
        name = s.get('name', 'default')
        counts = source_stats.get(name, {})
        stats = s.get('stats', {})

        screened = counts.get('screened', 0)
        qualified = counts.get('qualified', 0)
        total = counts.get('total', 0)

        stats['total_sourced'] = total
        stats['screened'] = screened
        stats['qualified'] = qualified
        if screened > 0:
            stats['qual_rate'] = round(qualified / screened, 2)
        stats['last_updated'] = datetime.utcnow().strftime('%Y-%m-%d')

        s['stats'] = stats
        log(f"  {name}: {qualified}/{screened} qualified"
            f" ({stats.get('qual_rate', 0):.0%}), {total} total sourced")

    # Save back
    search_filters['searches'] = searches
    _update_search_filters(client, position_id, search_filters)

    log("Qualification rates updated")
    print(json.dumps(source_stats))


def cmd_add_search(position_id: str, search_name: str):
    """Add a new search variant. Reads JSON from stdin.

    Accepts either:
        {"intent": "natural language description"} — agent builds filters at runtime
        {"filters": {...}} — legacy structured filters
        {"intent": "...", "filters": {...}} — both (intent for context, filters as starting point)
    """
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    data = json.loads(raw)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    search_filters = position.get('search_filters', {})
    if 'searches' not in search_filters:
        search_filters = {'searches': [], 'target_qualified': 50}

    existing_names = [s['name'] for s in search_filters['searches']]
    if search_name in existing_names:
        print(json.dumps({"error": f"Search '{search_name}' already exists"}))
        sys.exit(1)

    # Build new search entry
    new_search = {
        "name": search_name,
        "stats": {"created": datetime.utcnow().strftime('%Y-%m-%d')},
    }

    if 'intent' in data:
        new_search['intent'] = data['intent']
    if 'filters' in data:
        new_search['filters'] = data['filters']

    if 'intent' not in data and 'filters' not in data:
        # Treat entire input as filters (legacy)
        new_search['filters'] = data

    search_filters['searches'].append(new_search)

    _update_search_filters(client, position_id, search_filters)

    log(f"Added search variant: {search_name}")
    print(json.dumps({"ok": True, "name": search_name, "total_searches": len(search_filters['searches'])}))


def cmd_retire_search(position_id: str, search_name: str):
    """Mark a search variant as retired."""
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

    found = False
    for s in searches:
        if s.get('name') == search_name:
            stats = s.get('stats', {})
            stats['retired'] = True
            stats['retired_at'] = datetime.utcnow().strftime('%Y-%m-%d')
            s['stats'] = stats
            found = True
            break

    if not found:
        print(json.dumps({"error": f"Search '{search_name}' not found"}))
        sys.exit(1)

    _update_search_filters(client, position_id, search_filters)

    log(f"Retired search variant: {search_name}")
    print(json.dumps({"ok": True, "retired": search_name}))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.search_step <command> <position_id> [args]", file=sys.stderr)
        print("Commands: get_config, save_candidates, update_qual_rates, add_search, retire_search",
              file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_config':
        cmd_get_config(position_id)
    elif command == 'save_candidates':
        search_name = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_save_candidates(position_id, search_name)
    elif command == 'update_qual_rates':
        cmd_update_qual_rates(position_id)
    elif command == 'add_search':
        if len(sys.argv) < 4:
            print("Usage: ... add_search <position_id> <search_name>", file=sys.stderr)
            sys.exit(1)
        cmd_add_search(position_id, sys.argv[3])
    elif command == 'retire_search':
        if len(sys.argv) < 4:
            print("Usage: ... retire_search <position_id> <search_name>", file=sys.stderr)
            sys.exit(1)
        cmd_retire_search(position_id, sys.argv[3])
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
