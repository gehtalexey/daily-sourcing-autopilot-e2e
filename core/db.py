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
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path

from .normalizers import normalize_linkedin_url, pick_current_employer, _parse_start_date_sort_key

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
        """Select rows from a table, paginating past PostgREST's default 1000-row cap.

        PostgREST enforces a server-side max-rows (typically 1000) that silently
        truncates responses regardless of the `limit` URL param. This method uses
        the `Range` header to paginate until we've fetched `limit` rows or the
        server returns fewer rows than requested (end of table).
        """
        params = {'select': columns}
        if filters:
            for key, value in filters.items():
                params[key] = value

        url = f"{self.url}/rest/v1/{table}"
        PAGE_SIZE = 1000
        results = []
        offset = 0
        while offset < limit:
            page_end = min(offset + PAGE_SIZE, limit) - 1
            headers = dict(self.headers)
            headers['Range-Unit'] = 'items'
            headers['Range'] = f'{offset}-{page_end}'
            response = requests.get(url, headers=headers, params=params, timeout=60)
            response.raise_for_status()
            if not response.text:
                break
            rows = response.json()
            if not rows:
                break
            results.extend(rows)
            # If the server returned fewer rows than we asked for, we're done.
            if len(rows) < (page_end - offset + 1):
                break
            offset += PAGE_SIZE
        return results

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

    def upsert_batch(self, table: str, rows: list, on_conflict: str = None) -> list:
        """Upsert multiple rows in a single request (much faster than individual upserts)."""
        if not rows:
            return []
        headers = self.headers.copy()
        if on_conflict:
            headers['Prefer'] = f'resolution=merge-duplicates,return=representation'
        url = f"{self.url}/rest/v1/{table}"
        params = {}
        if on_conflict:
            params['on_conflict'] = on_conflict
        json_str = json.dumps(rows, allow_nan=True)
        json_str = json_str.replace(': NaN', ': null').replace(':NaN', ':null')
        json_str = json_str.replace(': Infinity', ': null').replace(':Infinity', ':null')
        json_str = json_str.replace(': -Infinity', ': null').replace(':-Infinity', ':null')
        response = requests.post(url, headers=headers, params=params, data=json_str, timeout=60)
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
#
# CROSS-PROJECT CONTRACT: the functions below MUST write the `profiles` table
# byte-for-byte identically to SourcingX. They are a direct port of SourcingX's
# db.py — _get_existing_original_urls, _prepare_profile_row,
# _bulk_fetch_existing_original_urls, save_enriched_profile,
# save_enriched_profiles_bulk. Do NOT diverge them. If SourcingX changes how it
# writes profiles, mirror the change here (and in smartlead-sourcing-autopilot).
# See CLAUDE.md "Shared Supabase database — write/read consistency".

def _get_existing_original_urls(client: SupabaseClient, linkedin_url: str) -> list:
    """Get existing original_urls array for a profile (for append operation).

    Returns empty list if profile doesn't exist or has no original_urls.
    Backward compatible: Works before and after original_urls migration.
    """
    try:
        # Try with original_urls array first (post-migration)
        try:
            result = client.select(
                'profiles',
                'original_urls,original_url',
                filters={'linkedin_url': f'eq.{linkedin_url}'},
                limit=1
            )
        except Exception:
            # Fall back to original_url only (pre-migration)
            result = client.select(
                'profiles',
                'original_url',
                filters={'linkedin_url': f'eq.{linkedin_url}'},
                limit=1
            )

        if result and len(result) > 0:
            profile = result[0]
            # Get from array first, fall back to single field
            urls = profile.get('original_urls') or []
            if not urls and profile.get('original_url'):
                urls = [profile['original_url']]
            return urls if isinstance(urls, list) else []
    except Exception:
        pass
    return []


def _prepare_profile_row(linkedin_url: str, crustdata_response: dict, original_url: str = None,
                         existing_original_urls: list = None, email: str = None, email_source: str = None) -> dict:
    """Prepare a profile dict for DB upsert (pure function, no DB calls).

    Extracts indexed fields from the Crustdata response and builds the row dict.
    """
    cd = crustdata_response or {}

    # Extract name for indexed column
    name = cd.get('name') or ''
    if not name:
        first_name = cd.get('first_name') or ''
        last_name = cd.get('last_name') or ''
        name = f"{first_name} {last_name}".strip()

    # Extract location
    location = cd.get('location') or ''

    # Extract only title/company for indexed filtering
    current_title = None
    current_company = None

    # Try current_employers first (Crustdata format) — pick most recent
    emp = pick_current_employer(cd.get('current_employers'))
    current_start_date = None
    current_years_at_company = None
    if emp:
        current_title = emp.get('employee_title') or emp.get('title')
        current_company = emp.get('employer_name') or emp.get('company_name')
        # Tenure-at-current-company: persist alongside the indexed fields so
        # Filter+ can read it without raw_data. Parsed via the same helper the
        # normalizer + backfill use, so all three paths produce identical
        # values.
        raw_start = emp.get('start_date')
        if raw_start is not None and str(raw_start).strip():
            from datetime import datetime as _dt
            parseable, dt = _parse_start_date_sort_key(raw_start)
            if parseable:
                current_start_date = dt.isoformat()
                current_years_at_company = round((_dt.now() - dt).days / 365.25, 1)
            else:
                # Sentinel: row processed, value unknowable. Filter+ treats
                # this the same as NULL (does not drop the row).
                current_years_at_company = -1.0
    else:
        # No current employer at all — same sentinel so we don't keep
        # re-checking via the IS NULL backfill path.
        current_years_at_company = -1.0

    # Fallback: extract from headline (e.g., "CEO at Company")
    if not current_title or not current_company:
        headline = cd.get('headline', '')
        if headline and ' at ' in headline:
            parts = headline.split(' at ', 1)
            if not current_title:
                current_title = parts[0].strip()
            if not current_company and len(parts) > 1:
                current_company = parts[1].split('/')[0].strip()

    # Extract pre-flattened arrays from Crustdata (already provided by API)
    all_employers = cd.get('all_employers') or []
    all_titles = cd.get('all_titles') or []
    all_schools = cd.get('all_schools') or []
    skills = cd.get('skills') or []

    # Ensure they're lists of strings
    all_employers = [str(x) for x in all_employers if x] if isinstance(all_employers, list) else []
    all_titles = [str(x) for x in all_titles if x] if isinstance(all_titles, list) else []
    all_schools = [str(x) for x in all_schools if x] if isinstance(all_schools, list) else []
    skills = [str(x) for x in skills if x] if isinstance(skills, list) else []

    now = datetime.utcnow().isoformat()

    # Build original_urls array (merge existing + new)
    original_urls = list(existing_original_urls or [])
    if original_url and original_url not in original_urls:
        original_urls.append(original_url)

    # Data to save - indexed fields + raw_data for everything else
    data = {
        'linkedin_url': linkedin_url,
        'original_url': original_url,  # Latest URL (backward compatibility)
        'raw_data': crustdata_response,
        'name': name if name else None,
        'location': location if location else None,
        'current_title': current_title,
        'current_company': current_company,
        'all_employers': all_employers if all_employers else None,
        'all_titles': all_titles if all_titles else None,
        'all_schools': all_schools if all_schools else None,
        'skills': skills if skills else None,
        # NOTE: `status` column was dropped from profiles in the shared-DB migration.
        # Use enrichment_status instead — writing `status` causes a 400 and silently
        # fails the bulk upsert.
        'enriched_at': now,
        'enrichment_status': 'enriched',
        'enrichment_attempted_at': now,
        'current_start_date': current_start_date,
        'current_years_at_company': current_years_at_company,
    }

    # Include email if provided
    if email:
        data['email'] = email
        if email_source:
            data['email_source'] = email_source

    # Include original_urls array
    if original_urls:
        data['original_urls'] = original_urls

    # Remove None values
    data = {k: v for k, v in data.items() if v is not None}

    return data


def save_enriched_profile(client: SupabaseClient, linkedin_url: str, crustdata_response: dict, original_url: str = None) -> dict:
    """Save a Crustdata-enriched profile to the database.

    Simplified approach: Store raw_data as-is, extract only title/company for indexing.
    All other fields are extracted at display time from raw_data.

    Multi-source support: Appends to original_urls array instead of overwriting,
    allowing tracking of all input URLs from different sources.

    Args:
        client: SupabaseClient instance
        linkedin_url: The LinkedIn URL (used as primary key, typically from Crustdata)
        crustdata_response: Raw response from Crustdata API
        original_url: The original input URL (for matching with loaded data)

    Returns:
        The saved profile record
    """
    linkedin_url = normalize_linkedin_url(linkedin_url)
    if not linkedin_url:
        raise ValueError("Valid linkedin_url is required")

    original_url = normalize_linkedin_url(original_url) if original_url else None

    # Multi-source support: Get existing URLs and append (deduplicated)
    existing_urls = _get_existing_original_urls(client, linkedin_url)

    data = _prepare_profile_row(linkedin_url, crustdata_response, original_url, existing_urls)

    # Try to save with original_urls array (post-migration)
    if 'original_urls' in data:
        try:
            result = client.upsert('profiles', data, on_conflict='linkedin_url')
            return result[0] if result else None
        except Exception:
            # Column doesn't exist yet (pre-migration), fall through to save without it
            data.pop('original_urls', None)

    # Save without original_urls array (backward compatible)
    result = client.upsert('profiles', data, on_conflict='linkedin_url')
    return result[0] if result else None


def _bulk_fetch_existing_original_urls(client: SupabaseClient, linkedin_urls: list) -> dict:
    """Bulk-fetch existing original_urls for multiple profiles in one query.

    Returns:
        Dict mapping linkedin_url -> list of existing original_urls
    """
    if not linkedin_urls:
        return {}

    result_map = {}
    batch_size = 50  # Supabase URL length limits

    for i in range(0, len(linkedin_urls), batch_size):
        batch = linkedin_urls[i:i+batch_size]
        url_list = ','.join(batch)
        try:
            # Try with original_urls column (post-migration)
            try:
                results = client.select(
                    'profiles',
                    'linkedin_url,original_urls,original_url',
                    filters={'linkedin_url': f'in.({url_list})'},
                    limit=batch_size
                )
            except Exception:
                # Fall back to original_url only (pre-migration)
                results = client.select(
                    'profiles',
                    'linkedin_url,original_url',
                    filters={'linkedin_url': f'in.({url_list})'},
                    limit=batch_size
                )

            for profile in (results or []):
                url = profile.get('linkedin_url', '')
                urls = profile.get('original_urls') or []
                if not urls and profile.get('original_url'):
                    urls = [profile['original_url']]
                result_map[url] = urls if isinstance(urls, list) else []
        except Exception:
            pass

    return result_map


def save_enriched_profiles_bulk(client: SupabaseClient, profiles: list,
                                original_url_map: dict = None,
                                email_map: dict = None,
                                batch_size: int = 100) -> dict:
    """Save multiple enriched profiles using batch upsert (much faster than individual saves).

    Replaces the old pattern of N individual save_enriched_profile() calls with:
    1. One bulk SELECT to fetch existing original_urls
    2. In-memory row preparation
    3. Batch upserts of 100 rows each

    For 2000 profiles: ~60 HTTP calls instead of ~6000.
    """
    if not profiles:
        return {'saved': 0, 'errors': 0, 'error_messages': []}

    original_url_map = original_url_map or {}
    email_map = email_map or {}
    stats = {'saved': 0, 'errors': 0, 'error_messages': []}

    # Step 1: Collect all linkedin_urls and normalize
    url_profile_pairs = []
    for profile in profiles:
        linkedin_url = profile.get('linkedin_flagship_url') or profile.get('linkedin_url')
        if not linkedin_url:
            continue
        norm_url = normalize_linkedin_url(linkedin_url)
        if norm_url:
            url_profile_pairs.append((norm_url, profile))

    all_urls = [url for url, _ in url_profile_pairs]

    # Step 2: Bulk-fetch existing original_urls (one query per 50 URLs)
    existing_urls_map = _bulk_fetch_existing_original_urls(client, all_urls)

    # Step 3: Prepare all rows in-memory
    rows = []
    has_original_urls_column = True  # Optimistic; will retry without if needed
    for norm_url, profile in url_profile_pairs:
        original_url = original_url_map.get(norm_url)
        existing_urls = existing_urls_map.get(norm_url, [])

        # Check email map for this profile
        email = email_map.get(norm_url)
        if not email and original_url:
            email = email_map.get(normalize_linkedin_url(original_url))

        try:
            row = _prepare_profile_row(
                norm_url, profile, original_url, existing_urls,
                email=email, email_source='csv' if email else None
            )
            rows.append(row)
        except Exception as e:
            stats['errors'] += 1
            stats['error_messages'].append(f"{norm_url}: prepare failed: {str(e)[:100]}")

    # Step 4: Batch upsert in chunks
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        try:
            if has_original_urls_column:
                client.upsert_batch('profiles', batch, on_conflict='linkedin_url')
            else:
                # Strip original_urls from all rows
                clean_batch = [{k: v for k, v in row.items() if k != 'original_urls'} for row in batch]
                client.upsert_batch('profiles', clean_batch, on_conflict='linkedin_url')
            stats['saved'] += len(batch)
        except Exception as e:
            error_str = str(e)
            # If original_urls column doesn't exist, retry this batch without it
            if has_original_urls_column and ('original_urls' in error_str or '42703' in error_str):
                has_original_urls_column = False
                try:
                    clean_batch = [{k: v for k, v in row.items() if k != 'original_urls'} for row in batch]
                    client.upsert_batch('profiles', clean_batch, on_conflict='linkedin_url')
                    stats['saved'] += len(batch)
                    continue
                except Exception as e2:
                    error_str = str(e2)

            # Batch failed — fall back to individual saves for this batch
            print(f"[DB] Batch upsert failed ({error_str[:100]}), falling back to individual saves")
            for row in batch:
                try:
                    client.upsert('profiles', row, on_conflict='linkedin_url')
                    stats['saved'] += 1
                except Exception as row_e:
                    stats['errors'] += 1
                    url = row.get('linkedin_url', 'unknown')
                    stats['error_messages'].append(f"{url}: {str(row_e)[:100]}")

    return stats


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
        'screened_at': datetime.now(timezone.utc).isoformat(),
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
    Returns both linkedin_url and original_url for better matching.

    Pagination is handled transparently by client.select() via Range headers.
    """
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()
    all_results = client.select(
        'profiles', 'linkedin_url,original_url',
        {'enriched_at': f'gte.{cutoff_date}'},
        limit=500000,
    )

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
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=ENRICHMENT_REFRESH_MONTHS * 30)).isoformat()
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

    Pagination is handled transparently by client.select() via Range headers.
    """
    result = client.select(
        'pipeline_candidates', 'linkedin_url',
        {'position_id': f'eq.{position_id}'},
        limit=100000,
    )
    return [row['linkedin_url'] for row in result if row.get('linkedin_url')]


def create_pipeline_run(client: SupabaseClient, position_id: str) -> dict:
    """Create a new pipeline run. Returns the run record with id.

    Checks for existing 'running' run for this position to prevent concurrent execution.
    If a run is already in progress, returns it instead of creating a new one.
    """
    # Check for existing running run (prevents concurrent execution)
    existing = client.select('pipeline_runs', '*', {
        'position_id': f'eq.{position_id}',
        'status': 'eq.running',
    }, limit=1)
    if existing:
        run = existing[0]
        # Check if it's stale (started more than 6 hours ago -- likely crashed)
        started = run.get('started_at', '')
        if started:
            try:
                started_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - started_dt).total_seconds() / 3600
                if age_hours < 6:
                    print(f"[db] WARNING: Run already in progress for {position_id} "
                          f"(started {age_hours:.1f}h ago, id={run.get('id')})", file=__import__('sys').stderr)
                    return run
                else:
                    # Stale run -- mark as failed and create new
                    update_pipeline_run(client, run['id'], 'failed',
                                        error=f'Stale run detected (started {age_hours:.0f}h ago)')
                    print(f"[db] Stale run marked as failed: {run.get('id')}", file=__import__('sys').stderr)
            except Exception:
                pass  # Can't parse date, create new run

    data = {
        'position_id': position_id,
        'status': 'running',
        'started_at': datetime.now(timezone.utc).isoformat(),
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
        data['completed_at'] = datetime.now(timezone.utc).isoformat()
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
        'search_run_date': run_date or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
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
    """Delete candidates from pipeline (used by pre-filter).

    Batches URLs to avoid hitting HTTP request-line length limits
    (each LinkedIn URL is ~60+ chars; >150 URLs in one `in.()` filter
    can exceed typical 8KB request-line caps).
    """
    if not linkedin_urls:
        return 0
    url = f"{client.url}/rest/v1/pipeline_candidates"
    BATCH_SIZE = 100
    total_deleted = 0
    for i in range(0, len(linkedin_urls), BATCH_SIZE):
        batch = linkedin_urls[i:i + BATCH_SIZE]
        url_list = ','.join(f'"{u}"' for u in batch)
        params = {
            'position_id': f'eq.{position_id}',
            'linkedin_url': f'in.({url_list})',
        }
        response = requests.delete(url, headers=client.headers, params=params, timeout=30)
        response.raise_for_status()
        result = response.json() if response.text else []
        total_deleted += len(result)
    return total_deleted
