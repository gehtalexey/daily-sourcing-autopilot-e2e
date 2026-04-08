"""
SalesQL Integration

Handles email finding via SalesQL API.
"""

import json
import time
import requests
from typing import Optional, Callable
from pathlib import Path


class SalesQLClient:
    """SalesQL API client for email finding."""

    BASE_URL = 'https://api-public.salesql.com/v1'

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            'Authorization': f'Bearer {api_key}',
        }

    def find_email(self, linkedin_url: str, personal_only: bool = True) -> dict:
        """
        Find email for a LinkedIn profile.

        Args:
            linkedin_url: LinkedIn profile URL
            personal_only: If True, only return Direct (personal) emails

        Returns:
            dict with 'email', 'success', etc. or 'error'
        """
        try:
            params = {'linkedin_url': linkedin_url}
            if personal_only:
                params['match_if_direct_email'] = 'true'

            response = requests.get(
                f'{self.BASE_URL}/persons/enrich/',
                params=params,
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                emails = data.get('emails', [])

                # Filter to direct/personal emails if requested
                if personal_only:
                    emails = [e for e in emails if e.get('type') == 'Direct']

                # Pick the best email
                email = emails[0].get('email') if emails else None

                return {
                    'linkedin_url': linkedin_url,
                    'email': email,
                    'all_emails': emails,
                    'success': bool(email),
                }
            elif response.status_code == 404:
                return {
                    'linkedin_url': linkedin_url,
                    'email': None,
                    'success': False,
                    'error': 'Not found'
                }
            elif response.status_code == 429:
                return {
                    'linkedin_url': linkedin_url,
                    'email': None,
                    'success': False,
                    'error': 'Rate limit exceeded'
                }
            else:
                return {
                    'linkedin_url': linkedin_url,
                    'success': False,
                    'error': f"API error {response.status_code}"
                }

        except Exception as e:
            return {
                'linkedin_url': linkedin_url,
                'success': False,
                'error': str(e)
            }

    def find_emails_batch(self, linkedin_urls: list[str], delay: float = 1.0,
                          on_progress: Callable = None) -> list[dict]:
        """
        Find emails for multiple profiles.

        Args:
            linkedin_urls: List of LinkedIn URLs
            delay: Seconds between requests (rate limiting)
            on_progress: Callback(current, total, result) for progress

        Returns:
            List of result dicts
        """
        results = []
        total = len(linkedin_urls)

        for i, url in enumerate(linkedin_urls):
            result = self.find_email(url)
            results.append(result)

            if on_progress:
                on_progress(i + 1, total, result)

            # Rate limiting
            if i + 1 < total:
                time.sleep(delay)

        return results

    def get_credits_balance(self) -> Optional[int]:
        """Get remaining API credits."""
        try:
            response = requests.get(
                f'{self.BASE_URL}/account/credits',
                headers=self.headers,
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('credits', data.get('balance'))
        except Exception:
            pass
        return None


def get_salesql_client() -> Optional[SalesQLClient]:
    """Get SalesQL client from config."""
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                api_key = config.get('salesql_api_key')
                if api_key and not api_key.startswith('YOUR_'):
                    return SalesQLClient(api_key)
    except Exception as e:
        print(f"[SalesQL] Failed to initialize: {e}")
    return None
