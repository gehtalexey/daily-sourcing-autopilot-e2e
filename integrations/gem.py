"""
GEM Integration

Handles pushing candidates to GEM (gem.com) ATS/CRM.
Uses GEM API v0: https://api.gem.com/v0/reference
Auth: X-API-Key header
"""

import json
import requests
from typing import Optional, List
from pathlib import Path


class GemClient:
    """GEM API v0 client for candidate management."""

    BASE_URL = 'https://api.gem.com/v0'

    def __init__(self, api_key: str, default_project_id: str = None, created_by: str = None):
        self.api_key = api_key
        self.default_project_id = default_project_id
        self.created_by = created_by
        self.headers = {
            'X-API-Key': api_key,
            'Content-Type': 'application/json',
        }

        # Auto-detect created_by if not provided
        if not self.created_by:
            self.created_by = self._get_current_user_id()

    def _get_current_user_id(self) -> Optional[str]:
        """Get the user ID associated with this API key.
        Falls back to searching by config email or first admin user."""
        try:
            # Try to find the API key owner by checking users
            resp = self._request('GET', 'users', params={'limit': 50})
            if resp.status_code == 200:
                users = resp.json()
                # Try to find by config email
                try:
                    config_path = Path(__file__).parent.parent / 'config.json'
                    if config_path.exists():
                        import json as _json
                        config = _json.load(open(config_path))
                        owner_email = config.get('gem_user_email', '')
                        for u in users:
                            if u.get('email') == owner_email:
                                return u.get('id')
                except Exception:
                    pass
                # Fallback: return first user
                if users:
                    return users[0].get('id')
        except Exception:
            pass
        return None

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request."""
        url = f"{self.BASE_URL}/{endpoint}"
        kwargs.setdefault('headers', self.headers)
        kwargs.setdefault('timeout', 30)
        return requests.request(method, url, **kwargs)

    # =========================================================================
    # Candidates
    # =========================================================================

    def create_candidate(self, project_id: str, candidate_data: dict) -> dict:
        """
        Create a candidate and add to a project.

        GEM v0 API: POST /v0/candidates
        Fields: first_name, last_name, emails, linked_in_handle, location, notes, project_ids
        """
        try:
            # Build emails array
            emails = []
            if candidate_data.get('email'):
                emails.append({
                    'email_address': candidate_data['email'],
                    'is_primary': True,
                })

            # Extract LinkedIn handle from URL
            linkedin_handle = ''
            linkedin_url = candidate_data.get('linkedin_url', '')
            if '/in/' in linkedin_url:
                linkedin_handle = linkedin_url.split('/in/')[-1].strip('/')

            payload = {
                'first_name': candidate_data.get('first_name', ''),
                'last_name': candidate_data.get('last_name', ''),
                'emails': emails,
                'linked_in_handle': linkedin_handle,
                'location': candidate_data.get('location', ''),
                'company': candidate_data.get('current_company', ''),
                'title': candidate_data.get('current_title', ''),
                'created_by': self.created_by,
            }

            # Add to project
            if project_id:
                payload['project_ids'] = [project_id]

            # Remove empty string values (but keep created_by and lists)
            payload = {k: v for k, v in payload.items()
                       if v != '' and v is not None and v != []}

            response = self._request('POST', 'candidates', json=payload)

            if response.status_code in (200, 201):
                return {
                    'success': True,
                    'candidate': response.json(),
                }
            elif response.status_code == 400:
                # Handle duplicate — add existing candidate to project
                error_data = response.json()
                dup = error_data.get('errors', {}).get('duplicate_candidate', {})
                existing_id = dup.get('id')
                if existing_id and project_id:
                    add_result = self._add_to_project(existing_id, project_id)
                    if add_result.get('success'):
                        return add_result
                    # Permission error on add-to-project — still treat as success
                    # so we can proceed to update fields on the existing candidate
                    return {
                        'success': True,
                        'duplicate': True,
                        'candidate_id': existing_id,
                        'add_to_project_error': add_result.get('error'),
                    }
                return {
                    'success': False,
                    'error': f"Duplicate: {error_data.get('message', '')}"
                }
            else:
                return {
                    'success': False,
                    'error': f"API error {response.status_code}: {response.text[:200]}"
                }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _add_to_project(self, candidate_id: str, project_id: str) -> dict:
        """Add an existing candidate to a project via PUT /projects/{id}/candidates."""
        try:
            payload = {'candidate_ids': [candidate_id]}

            # Try without user_id first (works for candidates owned by other users)
            resp = self._request('PUT', f'projects/{project_id}/candidates', json=payload)

            # If 400 permission error without user_id, retry with user_id
            if resp.status_code == 400 and self.created_by:
                payload['user_id'] = self.created_by
                resp = self._request('PUT', f'projects/{project_id}/candidates', json=payload)

            if resp.status_code in (200, 201, 204):
                return {'success': True, 'added_to_project': True, 'candidate_id': candidate_id}
            else:
                return {'success': False, 'error': f'Failed to add to project: {resp.status_code}: {resp.text[:200]}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def remove_candidates_from_project(self, project_id: str, candidate_ids: list) -> dict:
        """Remove candidates from a project via DELETE /projects/{id}/candidates.

        Args:
            project_id: GEM project ID
            candidate_ids: list of GEM candidate IDs to remove

        Returns:
            dict with success flag, removed count, and errors list.

        Note: do NOT pass user_id in the body — GEM API rejects DELETE with
        user_id in body with a 400 permission error. Body-only works.
        Also: candidate_ids MUST already be members of this project;
        passing candidates that aren't in the project returns 400.
        """
        if not candidate_ids:
            return {'success': True, 'removed': 0, 'errors': []}

        BATCH_SIZE = 50
        removed = 0
        errors = []
        for i in range(0, len(candidate_ids), BATCH_SIZE):
            batch = candidate_ids[i:i + BATCH_SIZE]
            payload = {'candidate_ids': batch}
            try:
                resp = self._request('DELETE', f'projects/{project_id}/candidates', json=payload)
                if resp.status_code in (200, 204):
                    removed += len(batch)
                else:
                    errors.append(f'batch {i}-{i+len(batch)-1}: {resp.status_code}: {resp.text[:200]}')
            except Exception as e:
                errors.append(f'batch {i}-{i+len(batch)-1}: {e}')
        return {'success': len(errors) == 0, 'removed': removed, 'errors': errors}

    def get_candidate_project_ids(self, candidate_id: str) -> list:
        """Return list of GEM project IDs the candidate currently belongs to."""
        try:
            resp = self._request('GET', f'candidates/{candidate_id}')
            if resp.status_code == 200:
                return resp.json().get('project_ids', []) or []
        except Exception:
            pass
        return []

    def update_candidate(self, candidate_id: str, candidate_data: dict = None,
                          email: str = None, custom_fields: list = None) -> dict:
        """Update candidate profile fields, email, and custom fields.

        Args:
            candidate_id: GEM candidate ID
            candidate_data: dict with profile fields (first_name, last_name, title, company, location)
            email: personal email to set as primary
            custom_fields: list of {custom_field_id, value} dicts
        """
        try:
            payload = {}

            # Main profile fields
            if candidate_data:
                for field in ['first_name', 'last_name', 'title', 'company', 'location', 'school', 'nickname']:
                    val = candidate_data.get(field)
                    if val:
                        payload[field] = val

            if email:
                payload['emails'] = [{'email_address': email, 'is_primary': True}]
            if custom_fields:
                payload['custom_fields'] = custom_fields

            if not payload:
                return {'success': True}

            resp = self._request('PUT', f'candidates/{candidate_id}', json=payload)
            if resp.status_code == 200:
                return {'success': True, 'candidate': resp.json()}
            else:
                return {'success': False, 'error': f'Update failed: {resp.status_code}: {resp.text[:200]}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_or_create_custom_fields(self, project_id: str) -> dict:
        """Get or create project-scoped custom fields. Returns {name: id} mapping."""
        # Project-scoped custom fields for GEM email tokens
        required_fields = ['email opener', 'score', 'reason']

        # Get existing custom fields for this project
        try:
            resp = self._request('GET', 'custom_fields')
            existing = resp.json() if resp.status_code == 200 else []
        except Exception:
            existing = []

        field_map = {}
        for f in existing:
            if f.get('project_id') == project_id and f.get('name') in required_fields:
                field_map[f['name']] = f['id']

        # Create missing fields
        for name in required_fields:
            if name not in field_map:
                try:
                    resp = self._request('POST', 'custom_fields', json={
                        'name': name,
                        'value_type': 'text',
                        'scope': 'project',
                        'project_id': project_id,
                    })
                    if resp.status_code in (200, 201):
                        field_map[name] = resp.json()['id']
                except Exception:
                    pass

        return field_map

    def list_project_candidates(self, project_id: str, page_size: int = 100) -> list:
        """List all candidates in a GEM project with full profile data.

        Uses GET /v0/projects/{project_id}/candidates (returns candidate_ids),
        then fetches full candidate profiles in batches via GET /v0/candidates/{id}.
        """
        import json as _json

        # Step 1: Get all candidate IDs from the project
        all_ids = []
        page = 1
        while True:
            resp = self._request('GET', f'projects/{project_id}/candidates', params={
                'page': page,
                'page_size': page_size,
            })
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for entry in batch:
                cid = entry.get('candidate_id')
                if cid:
                    all_ids.append(cid)

            # Check pagination header for next page
            pagination = resp.headers.get('X-Pagination', '{}')
            try:
                pag = _json.loads(pagination)
                if page >= pag.get('total_pages', 1):
                    break
            except Exception:
                if len(batch) < page_size:
                    break
            page += 1

        # Step 2: Fetch full candidate profiles by ID
        all_candidates = []
        for cid in all_ids:
            try:
                resp = self._request('GET', f'candidates/{cid}')
                if resp.status_code == 200:
                    all_candidates.append(resp.json())
            except Exception:
                continue

        return all_candidates

    def candidate_exists(self, project_id: str, linkedin_url: str) -> bool:
        """Check if a candidate already exists by LinkedIn handle."""
        linkedin_handle = ''
        if '/in/' in linkedin_url:
            linkedin_handle = linkedin_url.split('/in/')[-1].strip('/')

        if not linkedin_handle:
            return False

        try:
            response = self._request(
                'GET', 'candidates',
                params={'linked_in_handle': linkedin_handle, 'limit': 1}
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    # Check if already in this project
                    candidate = data[0]
                    project_ids = candidate.get('project_ids', [])
                    return project_id in project_ids
            return False
        except Exception:
            return False

    def get_candidate_id_by_linkedin(self, linkedin_url: str) -> Optional[str]:
        """Look up a GEM candidate_id by LinkedIn URL.

        Returns None if no candidate found or an error occurred.
        """
        linkedin_handle = ''
        if '/in/' in linkedin_url:
            linkedin_handle = linkedin_url.split('/in/')[-1].strip('/')

        if not linkedin_handle:
            return None

        try:
            response = self._request(
                'GET', 'candidates',
                params={'linked_in_handle': linkedin_handle, 'limit': 1}
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get('id')
            return None
        except Exception:
            return None

    def format_candidate_for_gem(self, profile: dict, screening_result: dict = None) -> dict:
        """Format a profile dict for GEM candidate creation."""
        raw_data = profile.get('raw_data') or {}

        notes = []
        if screening_result:
            notes.append(f"AI Score: {screening_result.get('screening_score')}/10")
            notes.append(f"Fit Level: {screening_result.get('screening_fit_level')}")
            if screening_result.get('screening_summary'):
                notes.append(f"\nSummary: {screening_result.get('screening_summary')}")

        return {
            'first_name': raw_data.get('first_name') or profile.get('first_name'),
            'last_name': raw_data.get('last_name') or profile.get('last_name'),
            'email': profile.get('email'),
            'linkedin_url': profile.get('linkedin_url'),
            'headline': raw_data.get('headline'),
            'location': raw_data.get('location'),
            'current_company': profile.get('current_company'),
            'current_title': profile.get('current_title'),
            'notes': '\n'.join(notes) if notes else None,
        }


def get_gem_client() -> Optional[GemClient]:
    """Get GEM client from config."""
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                api_key = config.get('gem_api_key')
                project_id = config.get('gem_project_id')
                if api_key and not api_key.startswith('YOUR_'):
                    return GemClient(api_key, project_id)
    except Exception as e:
        print(f"[GEM] Failed to initialize: {e}")
    return None
