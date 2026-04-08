"""
Pre-Filter Step — Filter candidates against Google Sheet exclusion lists.

Usage:
    python -m pipeline.pre_filter_step <position_id>

Filters:
  - Past Candidates: matched by full name
  - Blacklist: matched by company name (normalized)
  - Not Relevant Companies: matched by company name (normalized)

Reads candidates from pipeline_candidates table.
Deletes filtered-out candidates.
Prints JSON stats to stdout.
"""

import sys
import json
import re
from pathlib import Path

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
    delete_pipeline_candidates,
    get_profiles_batch,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[pre_filter] {msg}", file=sys.stderr)


def normalize_company(name):
    """Normalize company name for comparison."""
    if not name or not str(name).strip():
        return ''
    name = str(name).lower().strip()
    for suffix in [' ltd', ' inc', ' corp', ' llc', ' limited', ' israel', ' il',
                   ' technologies', ' tech', ' software', ' solutions', ' group']:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def matches_company_list(company, company_list):
    """Check if company matches any in the list."""
    if not company or not str(company).strip():
        return False
    company_norm = normalize_company(company)
    if not company_norm:
        return False
    for c in company_list:
        c_norm = normalize_company(c)
        if not c_norm:
            continue
        if company_norm == c_norm:
            return True
        if len(c_norm) >= 4 and len(company_norm) >= 4:
            if company_norm.startswith(c_norm) or c_norm.startswith(company_norm):
                return True
    return False


def load_google_sheets(sheet_url, config):
    """Load exclusion lists from Google Sheets."""
    if not HAS_GSPREAD:
        log("gspread not installed, skipping Google Sheets filters")
        return set(), [], []

    filter_config = config.get('filter_sheets', {})

    # Parse sheet URL to ID
    sheet_id = None
    if sheet_url:
        match = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_url)
        if match:
            sheet_id = match.group(1)
        elif re.match(r'^[a-zA-Z0-9-_]{20,}$', sheet_url):
            sheet_id = sheet_url
    if not sheet_id:
        sheet_id = filter_config.get('spreadsheet_id')

    if not sheet_id:
        log("No spreadsheet ID configured")
        return set(), [], []

    creds_file = config.get('google_credentials_file', 'google_credentials.json')
    creds_path = Path(__file__).parent.parent / creds_file
    if not creds_path.exists():
        log(f"Credentials file not found: {creds_path}")
        return set(), [], []

    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly',
                   'https://www.googleapis.com/auth/drive.readonly']
        creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(sheet_id)
    except Exception as e:
        log(f"Failed to open Google Sheet: {e}")
        return set(), [], []

    # Past Candidates — match by full name
    past_names = set()
    past_sheet = filter_config.get('past_candidates')
    if past_sheet:
        try:
            ws = spreadsheet.worksheet(past_sheet)
            for row in ws.get_all_values()[1:]:
                for cell in row:
                    if cell and cell.strip():
                        past_names.add(str(cell).lower().strip())
            log(f"  Past candidates: {len(past_names)} names")
        except Exception as e:
            log(f"  Warning: Could not load past candidates: {e}")

    # Blacklist
    blacklist = []
    bl_sheet = filter_config.get('blacklist')
    if bl_sheet:
        try:
            ws = spreadsheet.worksheet(bl_sheet)
            for row in ws.get_all_values()[1:]:
                for cell in row:
                    if cell and cell.strip():
                        blacklist.append(str(cell).strip())
            blacklist = list(set(blacklist))
            log(f"  Blacklist: {len(blacklist)} companies")
        except Exception as e:
            log(f"  Warning: Could not load blacklist: {e}")

    # Not Relevant Companies
    not_relevant = []
    nr_sheet = filter_config.get('not_relevant_companies') or filter_config.get('not_relevant')
    if nr_sheet:
        try:
            ws = spreadsheet.worksheet(nr_sheet)
            for row in ws.get_all_values()[1:]:
                for cell in row:
                    if cell and cell.strip():
                        not_relevant.append(cell.strip())
            not_relevant = list(set(not_relevant))
            log(f"  Not relevant: {len(not_relevant)} companies")
        except Exception as e:
            log(f"  Warning: Could not load not relevant companies: {e}")

    return past_names, blacklist, not_relevant


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.pre_filter_step <position_id>", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Load config
    config_path = Path(__file__).parent.parent / 'config.json'
    config = json.load(open(config_path)) if config_path.exists() else {}

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    # Get today's candidates (not yet screened = freshly added)
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    total_input = len(candidates)
    log(f"Pre-filtering {total_input} candidates for {position_id}")

    if total_input == 0:
        print(json.dumps({"filtered_out": 0, "remaining": 0, "by_category": {}}))
        return

    # Load Google Sheets
    log("Loading Google Sheets exclusion lists...")
    past_names, blacklist, not_relevant = load_google_sheets(
        position.get('sheet_url'), config
    )

    # Also try loading enriched profiles as fallback for name/company
    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    profiles_map = get_profiles_batch(client, urls)

    # Apply filters
    to_remove = {
        'past_candidates': [],
        'blacklist': [],
        'not_relevant': [],
    }

    for c in candidates:
        url = c.get('linkedin_url')

        # Get name/company from pipeline_candidates fields (set by search_step)
        # Fall back to enriched profile data if available
        profile = profiles_map.get(url, {})
        raw_data = profile.get('raw_data', {})

        # Name: prefer candidate_name (from search), fallback to enriched
        name = (c.get('candidate_name') or raw_data.get('name', '')).lower().strip()
        # Also try first+last from enriched data
        first = raw_data.get('first_name', '').lower().strip()
        last = raw_data.get('last_name', '').lower().strip()
        full_name = f"{first} {last}".strip()

        if name and name in past_names:
            to_remove['past_candidates'].append(url)
            log(f"  PAST: {name}")
            continue
        if full_name and full_name in past_names:
            to_remove['past_candidates'].append(url)
            log(f"  PAST: {full_name}")
            continue

        # Company: prefer current_company (from search), fallback to enriched
        company = c.get('current_company') or ''
        if not company:
            current_employers = raw_data.get('current_employers', [])
            if current_employers and isinstance(current_employers, list):
                emp = current_employers[0] if current_employers else {}
                if isinstance(emp, dict):
                    company = emp.get('employer_name', '') or emp.get('company_name', '')

        if matches_company_list(company, blacklist):
            to_remove['blacklist'].append(url)
            log(f"  BLACKLIST: {name} @ {company}")
            continue

        if matches_company_list(company, not_relevant):
            to_remove['not_relevant'].append(url)
            log(f"  NOT RELEVANT: {name} @ {company}")
            continue

    # Delete filtered-out candidates
    all_removed_urls = []
    for category, urls_list in to_remove.items():
        all_removed_urls.extend(urls_list)

    if all_removed_urls:
        deleted = delete_pipeline_candidates(client, position_id, all_removed_urls)
        log(f"Deleted {deleted} filtered-out candidates")

    total_removed = len(all_removed_urls)
    remaining = total_input - total_removed

    stats = {
        "filtered_out": total_removed,
        "remaining": remaining,
        "by_category": {
            "past_candidates": len(to_remove['past_candidates']),
            "blacklist": len(to_remove['blacklist']),
            "not_relevant": len(to_remove['not_relevant']),
        }
    }

    log(f"Pre-filter: {total_input} -> {remaining} ({total_removed} removed)")
    print(json.dumps(stats))


def cmd_get_titles(position_id: str):
    """Output candidates with title/headline for Claude to review relevance.
    Run after sheet-based pre-filter, before enrich."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    results = []
    for c in candidates:
        results.append({
            'linkedin_url': c.get('linkedin_url'),
            'name': c.get('candidate_name', ''),
            'title': c.get('current_title', ''),
            'company': c.get('current_company', ''),
            'headline': c.get('headline', ''),
        })

    log(f"{len(results)} candidates for title review")
    print(json.dumps(results))


def cmd_remove_irrelevant(position_id: str):
    """Remove candidates with irrelevant titles. Reads JSON array of LinkedIn URLs from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    urls = json.loads(raw)

    if not isinstance(urls, list):
        print(json.dumps({"error": "Expected JSON array of LinkedIn URLs"}))
        sys.exit(1)

    if urls:
        deleted = delete_pipeline_candidates(client, position_id, urls)
        log(f"Removed {deleted} irrelevant titles")
        print(json.dumps({"removed": deleted}))
    else:
        print(json.dumps({"removed": 0}))


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'get_titles':
        cmd_get_titles(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == 'remove_irrelevant':
        cmd_remove_irrelevant(sys.argv[2])
    else:
        main()
