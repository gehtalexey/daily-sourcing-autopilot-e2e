"""
GEM Integration

Handles pushing candidates to GEM (gem.com) ATS/CRM.
"""

import json
import requests
from typing import Optional, List
from pathlib import Path


class GemClient:
    """GEM API client for candidate management."""

    BASE_URL = 'https://api.gem.com/v1'

    def __init__(self, api_key: str, default_project_id: str = None):
        self.api_key = api_key
        self.default_project_id = default_project_id
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request."""
        url = f"{self.BASE_URL}/{endpoint}"
        kwargs.setdefault('headers', self.headers)
        kwargs.setdefault('timeout', 30)
        return requests.request(method, url, **kwargs)

    # =========================================================================
    # Projects
    # =========================================================================

    def list_projects(self) -> list:
        """List all projects."""
        try:
            response = self._request('GET', 'projects')
            if response.status_code == 200:
                return response.json().get('data', [])
        except Exception as e:
            print(f"[GEM] Error listing projects: {e}")
        return []

    def get_project(self, project_id: str) -> Optional[dict]:
        """Get project details."""
        try:
            response = self._request('GET', f'projects/{project_id}')
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None

    # =========================================================================
    # Candidates
    # =========================================================================

    def create_candidate(self, project_id: str, candidate_data: dict) -> dict:
        """
        Create a candidate in a project.

        Args:
            project_id: GEM project ID
            candidate_data: dict with candidate info:
                - first_name: str
                - last_name: str
                - email: str (optional)
                - linkedin_url: str
                - headline: str (optional)
                - location: str (optional)
                - current_company: str (optional)
                - current_title: str (optional)
                - notes: str (optional)
                - tags: list[str] (optional)

        Returns:
            Created candidate data or error dict
        """
        try:
            payload = {
                'first_name': candidate_data.get('first_name'),
                'last_name': candidate_data.get('last_name'),
                'email': candidate_data.get('email'),
                'linkedin_url': candidate_data.get('linkedin_url'),
                'headline': candidate_data.get('headline'),
                'location': candidate_data.get('location'),
                'current_company': candidate_data.get('current_company'),
                'current_title': candidate_data.get('current_title'),
            }

            # Remove None values
            payload = {k: v for k, v in payload.items() if v is not None}

            # Add notes if provided
            if candidate_data.get('notes'):
                payload['notes'] = candidate_data['notes']

            # Add tags if provided
            if candidate_data.get('tags'):
                payload['tags'] = candidate_data['tags']

            response = self._request(
                'POST',
                f'projects/{project_id}/candidates',
                json=payload
            )

            if response.status_code in (200, 201):
                return {
                    'success': True,
                    'candidate': response.json(),
                }
            else:
                return {
                    'success': False,
                    'error': f"API error {response.status_code}: {response.text}"
                }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def create_candidates_batch(self, project_id: str, candidates: List[dict],
                                 on_progress: callable = None) -> dict:
        """
        Create multiple candidates.

        Args:
            project_id: GEM project ID
            candidates: List of candidate dicts
            on_progress: Callback(current, total, result)

        Returns:
            dict with 'created', 'failed', 'results'
        """
        results = []
        created = 0
        failed = 0
        total = len(candidates)

        for i, candidate in enumerate(candidates):
            result = self.create_candidate(project_id, candidate)
            results.append(result)

            if result.get('success'):
                created += 1
            else:
                failed += 1

            if on_progress:
                on_progress(i + 1, total, result)

        return {
            'created': created,
            'failed': failed,
            'total': total,
            'results': results,
        }

    def search_candidates(self, project_id: str, query: str = None,
                          email: str = None, linkedin_url: str = None) -> list:
        """Search for candidates in a project."""
        try:
            params = {}
            if query:
                params['q'] = query
            if email:
                params['email'] = email
            if linkedin_url:
                params['linkedin_url'] = linkedin_url

            response = self._request(
                'GET',
                f'projects/{project_id}/candidates',
                params=params
            )

            if response.status_code == 200:
                return response.json().get('data', [])
        except Exception:
            pass
        return []

    def candidate_exists(self, project_id: str, linkedin_url: str) -> bool:
        """Check if a candidate already exists in the project."""
        results = self.search_candidates(project_id, linkedin_url=linkedin_url)
        return len(results) > 0

    # =========================================================================
    # Helpers
    # =========================================================================

    def format_candidate_for_gem(self, profile: dict, screening_result: dict = None) -> dict:
        """
        Format a profile dict for GEM candidate creation.

        Args:
            profile: Enriched profile from Supabase
            screening_result: Optional screening data to include in notes

        Returns:
            dict ready for create_candidate()
        """
        raw_data = profile.get('raw_data') or {}

        # Build notes from screening results
        notes = []
        if screening_result:
            notes.append(f"AI Score: {screening_result.get('screening_score')}/10")
            notes.append(f"Fit Level: {screening_result.get('screening_fit_level')}")
            if screening_result.get('screening_summary'):
                notes.append(f"\nSummary: {screening_result.get('screening_summary')}")

        # Build tags from fit level
        tags = []
        fit_level = profile.get('screening_fit_level') or screening_result.get('screening_fit_level')
        if fit_level:
            tags.append(fit_level.replace(' ', '-').lower())

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
            'tags': tags if tags else None,
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
