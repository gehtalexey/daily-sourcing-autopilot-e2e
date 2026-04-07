"""
Crustdata Integration

Handles LinkedIn profile enrichment via Crustdata API.
"""

import json
import time
import requests
from typing import Optional, Callable
from pathlib import Path


class CrustdataClient:
    """Crustdata API client for profile enrichment."""

    BASE_URL = 'https://api.crustdata.com'

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            'Authorization': f'Token {api_key}',
            'Content-Type': 'application/json',
        }

    def enrich_profile(self, linkedin_url: str) -> dict:
        """
        Enrich a single LinkedIn profile.

        Args:
            linkedin_url: LinkedIn profile URL

        Returns:
            Enriched profile data or error dict
        """
        try:
            response = requests.get(
                f'{self.BASE_URL}/screener/person/enrich',
                params={'linkedin_profile_url': linkedin_url},
                headers=self.headers,
                timeout=120
            )

            if response.status_code == 200:
                data = response.json()
                # API returns list for single URL
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                return data
            else:
                return {
                    'linkedin_url': linkedin_url,
                    'error': f"API error {response.status_code}: {response.text}"
                }

        except Exception as e:
            return {'linkedin_url': linkedin_url, 'error': str(e)}

    def enrich_batch(self, linkedin_urls: list[str], batch_size: int = 10,
                     delay: float = 2.0, on_progress: Callable = None) -> list[dict]:
        """
        Enrich multiple profiles in batches.

        Args:
            linkedin_urls: List of LinkedIn URLs
            batch_size: Profiles per API call (max 25)
            delay: Seconds between batches (rate limiting)
            on_progress: Callback(current, total, batch_result) for progress

        Returns:
            List of enriched profiles
        """
        all_results = []
        total = len(linkedin_urls)

        for i in range(0, total, batch_size):
            batch = linkedin_urls[i:i + batch_size]
            batch_str = ','.join(batch)

            try:
                response = requests.get(
                    f'{self.BASE_URL}/screener/person/enrich',
                    params={'linkedin_profile_url': batch_str},
                    headers=self.headers,
                    timeout=120
                )

                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        all_results.extend(data)
                    else:
                        all_results.append(data)
                else:
                    # Add error entries for failed batch
                    for url in batch:
                        all_results.append({
                            'linkedin_url': url,
                            'error': f"API error {response.status_code}"
                        })

            except Exception as e:
                for url in batch:
                    all_results.append({'linkedin_url': url, 'error': str(e)})

            if on_progress:
                on_progress(min(i + batch_size, total), total, all_results[-len(batch):])

            # Rate limiting
            if i + batch_size < total:
                time.sleep(delay)

        return all_results

    def search_people(self, filters: list[dict], page: int = 1,
                       exclude_urls: list[str] = None) -> dict:
        """
        Search for people using Crustdata People Search DB.

        Args:
            filters: List of filter dicts with 'column', 'type', 'value' keys.
                     Multiple filters should be wrapped in {'op': 'and', 'conditions': [...]}.
            page: Page number (1-indexed), not used for DB search (uses limit/cursor).
            exclude_urls: LinkedIn URLs to exclude from results.

        Returns:
            dict with 'profiles' list and metadata
        """
        try:
            payload = {
                'dataset': 'people',
                'filters': filters,
                'limit': 100,
                'offset': (page - 1) * 100,
            }

            if exclude_urls:
                # Add NOT IN filter for already-sourced URLs
                exclude_filter = {
                    'column': 'linkedin_profile_url',
                    'type': 'not_in',
                    'value': exclude_urls[:500],  # API limit
                }
                if isinstance(filters, dict) and filters.get('op'):
                    filters['conditions'].append(exclude_filter)
                elif isinstance(filters, list):
                    all_conditions = filters + [exclude_filter]
                    payload['filters'] = {'op': 'and', 'conditions': all_conditions}
                else:
                    payload['filters'] = {'op': 'and', 'conditions': [filters, exclude_filter]}

            response = requests.post(
                f'{self.BASE_URL}/screener/person/search',
                headers=self.headers,
                json=payload,
                timeout=120,
            )

            if response.status_code == 200:
                data = response.json()
                profiles = data if isinstance(data, list) else data.get('profiles', data.get('data', []))
                return {
                    'profiles': profiles if isinstance(profiles, list) else [],
                    'total': len(profiles) if isinstance(profiles, list) else 0,
                }
            else:
                return {
                    'profiles': [],
                    'total': 0,
                    'error': f"API error {response.status_code}: {response.text[:200]}"
                }

        except Exception as e:
            return {'profiles': [], 'total': 0, 'error': str(e)}

    def get_credits_balance(self) -> Optional[int]:
        """Get remaining API credits (if supported by API)."""
        # Note: Crustdata may not have a credits endpoint
        # This is a placeholder for future implementation
        return None


def get_crustdata_client() -> Optional[CrustdataClient]:
    """Get Crustdata client from config."""
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                # Try both key names for compatibility
                api_key = config.get('crustdata_api_key') or config.get('api_key')
                if api_key and not api_key.startswith('YOUR_'):
                    return CrustdataClient(api_key)
    except Exception as e:
        print(f"[Crustdata] Failed to initialize: {e}")
    return None
