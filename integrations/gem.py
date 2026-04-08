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
                    return self._add_to_project(existing_id, project_id)
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
            if self.created_by:
                payload['user_id'] = self.created_by

            resp = self._request('PUT', f'projects/{project_id}/candidates', json=payload)

            if resp.status_code in (200, 201, 204):
                return {'success': True, 'added_to_project': True, 'candidate_id': candidate_id}
            else:
                return {'success': False, 'error': f'Failed to add to project: {resp.status_code}: {resp.text[:200]}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def update_candidate(self, candidate_id: str, email: str = None,
                          custom_fields: list = None) -> dict:
        """Update candidate email and custom fields after push."""
        try:
            payload = {}
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
        required_fields = ['Email Opener', 'Fit Level', 'Screening Notes', 'Personal Email']

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
