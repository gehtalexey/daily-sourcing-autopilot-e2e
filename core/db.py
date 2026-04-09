"""
Supabase Database Module for Claude Terminal Sourcing Agent

Connects to the SAME Supabase instance as linkedin-enricher web app,
enabling shared data between both tools.

Uses REST API directly - no supabase package required.
"""

import os
import json
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from .normalizers import normalize_linkedin_url

# Refresh threshold for re-enriching stale profiles
ENRICHMENT_REFRESH_MONTHS = 3


class SupabaseClient:
    """Simple Supabase REST API client."""

    def __init__(self, url: str, key: str):
        self.url = url.rstrip('/')
        self.key = key
        self.headers = {
            'apikey': key,
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Prefer': 'return=representation'
        }

    def _request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None) -> dict:
        """Make a request to Supabase REST API."""
        url = f"{self.url}/rest/v1/{endpoint}"
        response = requests.request(
            method,
            url,
            headers=self.headers,
            params=params,
            json=json_data,
            timeout=30
        )
        response.raise_for_status()
        if response.text:
            return response.json()
        return {}

    def select(self, table: str, columns: str = '*', filters: dict = None, limit: int = 50000) -> list:
        """Select rows from a table."""
        params = {'select': columns}
        if filters:
            for key, value in filters.items():
                params[key] = value
        params['limit'] = limit
        return self._request('GET', table, params=params)

    def insert(self, table: str, data: dict) -> list:
        """Insert a row into a table."""
        return self._request('POST', table, json_data=data)

    def upsert(self, table: str, data: dict, on_conflict: str = None) -> list:
        """Upsert (insert or update) a row."""
        headers = self.headers.copy()
        if on_conflict:
            headers['Prefer'] = f'resolution=merge-duplicates,return=representation'
        url = f"{self.url}/rest/v1/{table}"
        params = {}
        if on_conflict:
            params['on_conflict'] = on_conflict
        # Pre-serialize JSON to handle NaN values
        json_str = json.dumps(data, allow_nan=True)
        json_str = json_str.replace(': NaN', ': null').replace(':NaN', ':null')
        json_str = json_str.replace(': Infinity', ': null').replace(':Infinity', ':null')
        json_str = json_str.replace(': -Infinity', ': null').replace(':-Infinity', ':null')
        response = requests.post(url, headers=headers, params=params, data=json_str, timeout=30)
        if response.status_code >= 400:
            error_msg = f"{response.status_code}: {response.text}"
            raise requests.HTTPError(error_msg)
        if response.text:
            return response.json()
        return []

    def update(self, table: str, data: dict, filters: dict) -> list:
        """Update rows matching filters."""
        params = {}
        for key, value in filters.items():
            params[key] = f'eq.{value}'
        return self._request('PATCH', table, params=params, json_data=data)

    def delete(self, table: str, filters: dict) -> list:
        """Delete rows matching filters."""
        params = {}
        for key, value in filters.items():
            params[key] = f'eq.{value}'
        return self._request('DELETE', table, params=params)

    def count(self, table: str, filters: dict = None) -> int:
        """Count rows in a table."""
        headers = self.headers.copy()
        headers['Prefer'] = 'count=exact'
        headers['Range-Unit'] = 'items'
        url = f"{self.url}/rest/v1/{table}"
        params = {'select': 'id'}
        if filters:
            for key, value in filters.items():
                params[key] = value
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        content_range = response.headers.get('Content-Range', '*/0')
        total = content_range.split('/')[-1]
        return int(total) if total != '*' else 0


def get_supabase_client() -> Optional[SupabaseClient]:
    """Get Supabase client from config.json or environment."""
    url = None
    key = None

    # Try config.json first (local development)
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                url = config.get('supabase_url')
                key = config.get('supabase_key')
    except Exception:
        pass

    # Fall back to environment variables
    if not url:
        url = os.environ.get('SUPABASE_URL')
    if not key:
        key = os.environ.get('SUPABASE_KEY')

    if url and key:
        return SupabaseClient(url, key)
    return None


# ============================================================================
# PROFILE OPERATIONS
# ============================================================================

def save_enriched_profile(client: SupabaseClient, linkedin_url: str, crustdata_response: dict, original_url: str = None) -> dict:
    """Save a Crustdata-enriched profile to the database.

    Args:
        client: SupabaseClient instance
        linkedin_url: The LinkedIn URL (used as primary key)
        crustdata_response: Raw response from Crustdata API
        original_url: The original input URL (for matching with loaded data)

    Returns:
        The saved profile record
    """
    linkedin_url = normalize_linkedin_url(linkedin_url)
    if not linkedin_url:
        raise ValueError("Valid linkedin_url is required")

    original_url = normalize_linkedin_url(original_url) if original_url else None

    cd = crustdata_response or {}

    # Extract title/company for indexed filtering
    current_title = None
    current_company = None

    current_employers = cd.get('current_employers') or []
    if current_employers and isinstance(current_employers, list):
        emp = current_employers[0] if current_employers else {}
        if isinstance(emp, dict):
            current_title = emp.get('employee_title') or emp.get('title')
            current_company = emp.get('employer_name') or emp.get('company_name')

    # Fallback: extract from headline
    if not current_title or not current_company:
        headline = cd.get('headline', '')
        if headline and ' at ' in headline:
            parts = headline.split(' at ', 1)
            if not current_title:
                current_title = parts[0].strip()
            if not current_company and len(parts) > 1:
                current_company = parts[1].split('/')[0].strip()

    # Pre-flattened arrays from Crustdata
    all_employers = cd.get('all_employers') or []
    all_titles = cd.get('all_titles') or []
    all_schools = cd.get('all_schools') or []
    skills = cd.get('skills') or []

    # Ensure they're lists of strings
    all_employers = [str(x) for x in all_employers if x] if isinstance(all_employers, list) else []
    all_titles = [str(x) for x in all_titles if x] if isinstance(all_titles, list) else []
    all_schools = [str(x) for x in all_schools if x] if isinstance(all_schools, list) else []
    skills = [str(x) for x in skills if x] if isinstance(skills, list) else []

    # Extract name (for SourcingX shared DB compatibility)
    name = cd.get('name', '')
    if not name:
        first = cd.get('first_name', '')
        last = cd.get('last_name', '')
        name = f"{first} {last}".strip() if first or last else None

    data = {
        'linkedin_url': linkedin_url,
        'original_url': original_url,
        'raw_data': crustdata_response,
        'name': name,
        'current_title': current_title,
        'current_company': current_company,
        'all_employers': all_employers if all_employers else None,
        'all_titles': all_titles if all_titles else None,
        'all_schools': all_schools if all_schools else None,
        'skills': skills if skills else None,
        'enrichment_status': 'enriched',
        'enriched_at': datetime.utcnow().isoformat(),
    }

    # Remove None values
    data = {k: v for k, v in data.items() if v is not None}

    result = client.upsert('profiles', data, on_conflict='linkedin_url')
    return result[0] if result else None


def compute_jd_hash(jd_text: str) -> str:
    """Compute a stable hash from JD text for screening dedup."""
    return hashlib.sha256((jd_text or '')[:500].encode()).hexdigest()


def insert_screening_result(client: SupabaseClient, linkedin_url: str, source_project: str,
                             jd_hash: str, score: int = None, fit_level: str = None,
                             result: str = None, summary: str = None, reasoning: str = None,
                             notes: str = None, opener: str = None, jd_title: str = None,
                             position_id: str = None, ai_model: str = None) -> dict:
    """Insert screening result into the shared screening_results table.

    Uses upsert on (linkedin_url, jd_hash, source_project) so re-screening
    the same profile for the same JD overwrites the previous result.
    """
    linkedin_url = normalize_linkedin_url(linkedin_url)

    data = {
        'linkedin_url': linkedin_url,
        'source_project': source_project,
        'jd_hash': jd_hash,
        'jd_title': jd_title,
        'position_id': position_id,
        'screening_score': score,
        'screening_fit_level': fit_level,
        'screening_result': result,
        'screening_summary': summary,
        'screening_reasoning': reasoning,
        'screening_notes': notes,
        'email_opener': opener,
        'ai_model': ai_model,
        'screened_at': datetime.utcnow().isoformat(),
    }
    data = {k: v for k, v in data.items() if v is not None}

    try:
        return client.upsert('screening_results', data,
                              on_conflict='linkedin_url,jd_hash,source_project')
    except Exception as e:
        print(f"[db] Warning: failed to write screening_results: {e}", file=__import__('sys').stderr)
        return None


# ============================================================================
# QUERY OPERATIONS
# ============================================================================

def get_profile(client: SupabaseClient, linkedin_url: str) -> Optional[dict]:
    """Get a single profile by LinkedIn URL."""
    linkedin_url = normalize_linkedin_url(linkedin_url)
    result = client.select('profiles', '*', {'linkedin_url': f'eq.{linkedin_url}'})
    return result[0] if result else None


def get_profiles_batch(client: SupabaseClient, linkedin_urls: list[str]) -> dict:
    """Get multiple profiles by LinkedIn URLs in one query.

    Returns dict mapping linkedin_url -> profile dict.
    Much faster than calling get_profile() multiple times.
    """
    if not linkedin_urls:
        return {}

    # Normalize URLs
    normalized = [normalize_linkedin_url(url) for url in linkedin_urls if url]
    normalized = [u for u in normalized if u]

    if not normalized:
        return {}

    # Supabase IN query using 'in' filter — batch to avoid URL length limits
    # Format: linkedin_url=in.(url1,url2,url3)
    BATCH_SIZE = 50
    profiles_map = {}
    for i in range(0, len(normalized), BATCH_SIZE):
        batch = normalized[i:i + BATCH_SIZE]
        url_list = ','.join(f'"{u}"' for u in batch)
        result = client.select('profiles', '*', {'linkedin_url': f'in.({url_list})'}, limit=len(batch))
        for p in result:
            url = p.get('linkedin_url')
            if url:
                profiles_map[url] = p

    return profiles_map


def get_profiles_needing_enrichment(client: SupabaseClient, urls: list[str]) -> list[str]:
    """Given a list of URLs, return those that need enrichment.

    A URL needs enrichment if:
    - It's not in the database, OR
    - It was enriched more than ENRICHMENT_REFRESH_MONTHS ago
    """
    if not urls:
        return []

    # Get recently enriched URLs
    recently_enriched = set(get_recently_enriched_urls(client, months=ENRICHMENT_REFRESH_MONTHS))

    # Return URLs not in the recently enriched set
    needs_enrichment = []
    for url in urls:
        normalized = normalize_linkedin_url(url)
        if normalized and normalized not in recently_enriched:
            needs_enrichment.append(url)

    return needs_enrichment


def get_profiles_needing_screening(client: SupabaseClient, limit: int = 100) -> list:
    """Get enriched profiles that haven't been screened yet.
    Uses latest_screening view to check across all projects."""
    # Get all enriched profiles and filter out those already screened
    enriched = client.select('profiles', 'linkedin_url', {'enrichment_status': 'eq.enriched'}, limit=50000)
    if not enriched:
        return []
    screened = client.select('latest_screening', 'linkedin_url', limit=50000)
    screened_urls = {r['linkedin_url'] for r in screened}
    need_screening = [r['linkedin_url'] for r in enriched if r['linkedin_url'] not in screened_urls]
    if not need_screening:
        return []
    # Fetch full profiles for unscreened URLs (up to limit)
    url_list = ','.join(need_screening[:limit])
    return client.select('profiles', '*', {'linkedin_url': f'in.({url_list})'}, limit=limit)


def get_profiles_by_status(client: SupabaseClient, status: str, limit: int = 1000) -> list:
    """Get profiles by pipeline status."""
    return client.select('profiles', '*', {'enrichment_status': f'eq.{status}'}, limit=limit)


def get_profiles_by_fit_level(client: SupabaseClient, fit_level: str, limit: int = 1000) -> list:
    """Get profiles by screening fit level from latest_screening view."""
    return client.select('latest_screening', '*', {'screening_fit_level': f'eq.{fit_level}'}, limit=limit)


def get_all_profiles(client: SupabaseClient, limit: int = 10000) -> list:
    """Get all profiles."""
    return client.select('profiles', '*', limit=limit)


def get_enriched_urls(client: SupabaseClient) -> set:
    """Get all LinkedIn URLs that have been enriched."""
    result = client.select('profiles', 'linkedin_url', limit=50000)
    urls = set()
    for p in result:
        url = p.get('linkedin_url')
        if url:
            urls.add(normalize_linkedin_url(url))
    return urls


def get_recently_enriched_urls(client: SupabaseClient, months: int = 6) -> list:
    """Get LinkedIn URLs enriched within the last N months.
    Returns both linkedin_url and original_url for better matching."""
    cutoff_date = (datetime.utcnow() - timedelta(days=months * 30)).isoformat()

    # Paginate to get all results
    all_results = []
    offset = 0
    page_size = 1000
    while True:
        filters = {'enriched_at': f'gte.{cutoff_date}', 'offset': str(offset)}
        result = client.select('profiles', 'linkedin_url,original_url', filters, limit=page_size)
        if not result:
            break
        all_results.extend(result)
        if len(result) < page_size:
            break
        offset += page_size

    urls = []
    for p in all_results:
        if p.get('linkedin_url'):
            urls.append(normalize_linkedin_url(p['linkedin_url']))
        if p.get('original_url') and p.get('original_url') != p.get('linkedin_url'):
            urls.append(normalize_linkedin_url(p['original_url']))
    return urls


def get_dedup_stats(client: SupabaseClient) -> dict:
    """Get stats about profiles in database for dedup preview."""
    total = client.count('profiles')
    cutoff_date = (datetime.utcnow() - timedelta(days=ENRICHMENT_REFRESH_MONTHS * 30)).isoformat()
    recently_enriched = client.count('profiles', {'enriched_at': f'gte.{cutoff_date}'})

    return {
        'total_profiles': total,
        'recently_enriched': recently_enriched,
        'will_skip': recently_enriched,
    }


# ============================================================================
# SCREENING PROMPTS (from database)
# ============================================================================

def get_screening_prompts(client: SupabaseClient) -> list:
    """Get all screening prompts from the database."""
    try:
        result = client.select('screening_prompts', '*', limit=100)
        return result if result else []
    except Exception as e:
        print(f"[DB] Failed to get screening prompts: {e}")
        return []


def get_default_screening_prompt(client: SupabaseClient) -> Optional[dict]:
    """Get the default screening prompt (is_default=true)."""
    try:
        result = client.select('screening_prompts', '*', {'is_default': 'eq.true'}, limit=1)
        if result:
            return result[0]
    except Exception as e:
        print(f"[DB] Failed to get default prompt: {e}")
    return None


def match_prompt_by_keywords(client: SupabaseClient, text: str) -> Optional[dict]:
    """Find the best matching prompt based on keywords in the text."""
    try:
        prompts = get_screening_prompts(client)
        if not prompts:
            return None

        text_lower = text.lower()
        best_match = None
        best_score = 0

        for prompt in prompts:
            keywords = prompt.get('keywords', [])
            if not keywords:
                continue
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            if score > best_score:
                best_score = score
                best_match = prompt

        if best_score >= 2:
            return best_match

        return get_default_screening_prompt(client)
    except Exception as e:
        print(f"[DB] Failed to match prompt: {e}")
        return None


# ============================================================================
# UTILITY
# ============================================================================

def check_connection(client: SupabaseClient) -> bool:
    """Check if Supabase connection is working."""
    if not client:
        return False
    try:
        client.select('profiles', 'linkedin_url', limit=1)
        return True
    except Exception:
        return False


def get_pipeline_stats(client: SupabaseClient) -> dict:
    """Get pipeline statistics."""
    stats = {
        'total': client.count('profiles'),
        'enriched': client.count('profiles', {'enrichment_status': 'eq.enriched'}),
        'screened': client.count('profiles', {'enrichment_status': 'eq.screened'}),
    }

    # Count by fit level
    for fit in ['Strong Fit', 'Good Fit', 'Partial Fit', 'Not a Fit']:
        stats[fit.lower().replace(' ', '_')] = client.count('profiles', {'screening_fit_level': f'eq.{fit}'})

    return stats


# ============================================================================
# PIPELINE OPERATIONS (pipeline_positions, pipeline_candidates, pipeline_runs)
# ============================================================================

def get_pipeline_position(client: SupabaseClient, position_id: str) -> Optional[dict]:
    """Get a position config by ID."""
    result = client.select('pipeline_positions', '*', {'position_id': f'eq.{position_id}'}, limit=1)
    return result[0] if result else None


def get_active_pipeline_positions(client: SupabaseClient) -> list:
    """Get all active positions."""
    return client.select('pipeline_positions', '*', {'active': 'eq.true'}, limit=100)


def get_pipeline_exclude_urls(client: SupabaseClient, position_id: str) -> list:
    """Get all LinkedIn URLs already sourced for a position (for not_in exclude).
    Paginated to handle large lists."""
    all_urls = []
    offset = 0
    page_size = 1000
    while True:
        result = client.select(
            'pipeline_candidates', 'linkedin_url',
            {'position_id': f'eq.{position_id}', 'offset': str(offset)},
            limit=page_size
        )
        if not result:
            break
        for row in result:
            url = row.get('linkedin_url')
            if url:
                all_urls.append(url)
        if len(result) < page_size:
            break
        offset += page_size
    return all_urls


def create_pipeline_run(client: SupabaseClient, position_id: str) -> dict:
    """Create a new pipeline run. Returns the run record with id."""
    data = {
        'position_id': position_id,
        'status': 'running',
        'started_at': datetime.utcnow().isoformat(),
    }
    result = client.insert('pipeline_runs', data)
    return result[0] if isinstance(result, list) and result else result


def update_pipeline_run(client: SupabaseClient, run_id: str, status: str,
                        stats: dict = None, error: str = None) -> dict:
    """Update a pipeline run status and stats."""
    data = {'status': status}
    if stats:
        data['stats'] = stats
    if error:
        data['error_message'] = error
    if status in ('completed', 'failed'):
        data['completed_at'] = datetime.utcnow().isoformat()
    result = client.update('pipeline_runs', data, {'id': run_id})
    return result[0] if result else None


def upsert_pipeline_candidate(client: SupabaseClient, position_id: str,
                               linkedin_url: str, source: str = 'crustdata_search',
                               run_date: str = None) -> dict:
    """Insert a candidate for a position. Skips if already exists."""
    data = {
        'position_id': position_id,
        'linkedin_url': normalize_linkedin_url(linkedin_url) or linkedin_url,
        'source': source,
        'search_run_date': run_date or datetime.utcnow().strftime('%Y-%m-%d'),
    }
    try:
        result = client.upsert('pipeline_candidates', data, on_conflict='position_id,linkedin_url')
        return result[0] if isinstance(result, list) and result else result
    except Exception:
        # ON CONFLICT DO NOTHING equivalent — skip duplicates
        return {}


def update_pipeline_candidate(client: SupabaseClient, position_id: str,
                               linkedin_url: str, updates: dict) -> dict:
    """Update a pipeline candidate's fields."""
    linkedin_url = normalize_linkedin_url(linkedin_url) or linkedin_url
    # Need to filter by both position_id AND linkedin_url
    url = f"{client.url}/rest/v1/pipeline_candidates"
    params = {
        'position_id': f'eq.{position_id}',
        'linkedin_url': f'eq.{linkedin_url}',
    }
    response = requests.patch(url, headers=client.headers, params=params, json=updates, timeout=30)
    response.raise_for_status()
    if response.text:
        return response.json()
    return {}


def get_pipeline_candidates(client: SupabaseClient, position_id: str,
                             filters: dict = None, limit: int = 5000) -> list:
    """Get pipeline candidates for a position with optional filters."""
    query_filters = {'position_id': f'eq.{position_id}'}
    if filters:
        query_filters.update(filters)
    return client.select('pipeline_candidates', '*', query_filters, limit=limit)


def delete_pipeline_candidates(client: SupabaseClient, position_id: str,
                                linkedin_urls: list) -> int:
    """Delete candidates from pipeline (used by pre-filter)."""
    if not linkedin_urls:
        return 0
    url_list = ','.join(f'"{u}"' for u in linkedin_urls)
    url = f"{client.url}/rest/v1/pipeline_candidates"
    params = {
        'position_id': f'eq.{position_id}',
        'linkedin_url': f'in.({url_list})',
    }
    response = requests.delete(url, headers=client.headers, params=params, timeout=30)
    response.raise_for_status()
    result = response.json() if response.text else []
    return len(result)
