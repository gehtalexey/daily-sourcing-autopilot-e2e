"""
URL and Field Normalizers for Claude Terminal Sourcing Agent

Handles LinkedIn URL normalization and data cleaning.
Compatible with linkedin-enricher web app normalizers.
"""

import re
import math
from typing import Optional, Any


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def is_nan_or_none(value: Any) -> bool:
    """Check if value is NaN, None, empty, or pandas NA."""
    if value is None:
        return True

    # Check for pandas NA type
    if type(value).__name__ == 'NAType':
        return True

    # Try pandas isna
    try:
        import pandas as pd
        if pd.isna(value):
            return True
    except (ImportError, TypeError, ValueError):
        pass

    # Check float NaN/Inf
    if isinstance(value, float):
        try:
            if math.isnan(value) or math.isinf(value):
                return True
        except (TypeError, ValueError):
            pass

    # Check empty string
    if isinstance(value, str) and value.strip() == '':
        return True

    return False


def clean_value(value: Any) -> Any:
    """Clean a single value - convert NaN/None to None, strip strings."""
    if is_nan_or_none(value):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def clean_dict(data: dict, keep_none: bool = False) -> dict:
    """Clean all values in a dict."""
    cleaned = {}
    for key, value in data.items():
        if isinstance(value, dict):
            cleaned[key] = clean_dict(value, keep_none)
        elif isinstance(value, list):
            cleaned[key] = [clean_value(v) for v in value if not is_nan_or_none(v)]
        else:
            clean_val = clean_value(value)
            if keep_none or clean_val is not None:
                cleaned[key] = clean_val
    return cleaned


def get_first_valid(data: dict, field_names: list) -> Any:
    """Get first non-null value from a list of possible field names."""
    for field in field_names:
        value = data.get(field)
        if not is_nan_or_none(value):
            return clean_value(value)
    return None


# ============================================================================
# URL NORMALIZATION
# ============================================================================

def normalize_linkedin_url(url: str) -> Optional[str]:
    """
    Normalize LinkedIn URL to canonical format for consistent matching.

    Transformations:
    - Add https:// if missing
    - Remove query parameters
    - Remove trailing slashes
    - Convert to lowercase
    - Validate it's a regular profile URL

    Returns None if URL is invalid.
    """
    if is_nan_or_none(url):
        return None

    url = str(url).strip()
    if not url:
        return None

    # Add protocol if missing
    if url.startswith('www.'):
        url = 'https://' + url
    elif not url.startswith('http'):
        url = 'https://' + url

    # Remove query parameters
    if '?' in url:
        url = url.split('?')[0]

    # Normalize www prefix
    url = url.replace('://linkedin.com', '://www.linkedin.com')

    # Remove trailing slashes
    url = url.rstrip('/')

    # Convert to lowercase
    url = url.lower()

    # Validate it's a LinkedIn profile URL
    if 'linkedin.com' not in url:
        return None

    # Reject Sales Navigator URLs
    if '/sales/' in url:
        return None

    # Should contain /in/ for personal profiles
    if '/in/' not in url:
        return None

    return url


def extract_linkedin_url(data: dict, url_fields: list = None) -> Optional[str]:
    """
    Extract and normalize LinkedIn URL from data.

    Args:
        data: Dict containing profile data
        url_fields: List of field names to try (in order)

    Returns:
        Normalized LinkedIn URL or None
    """
    if url_fields is None:
        url_fields = ['linkedin_url', 'defaultProfileUrl', 'profileUrl',
                      'linkedInProfileUrl', 'public_url', 'profileLink']

    for field in url_fields:
        url = data.get(field)
        if url:
            normalized = normalize_linkedin_url(url)
            if normalized:
                return normalized

    # Fallback: construct from publicIdentifier
    for field in ['publicIdentifier', 'public_identifier']:
        public_id = data.get(field)
        if public_id and public_id != 'null' and not is_nan_or_none(public_id):
            constructed = f"https://www.linkedin.com/in/{public_id}"
            return normalize_linkedin_url(constructed)

    return None


# ============================================================================
# DURATION PARSING
# ============================================================================

def parse_duration(duration_str: Any) -> Optional[float]:
    """
    Parse duration text to numeric years.

    Examples:
    - "8 months" → 0.67
    - "2 years" → 2.0
    - "1 year 6 months" → 1.5
    """
    if is_nan_or_none(duration_str):
        return None

    # Already numeric
    if isinstance(duration_str, (int, float)):
        return float(duration_str) if not math.isnan(duration_str) else None

    duration_str = str(duration_str).lower().strip()
    if not duration_str:
        return None

    # Try direct float conversion first
    try:
        return float(duration_str)
    except (ValueError, TypeError):
        pass

    # Parse text like "8 months", "2 years"
    years = 0
    months = 0

    year_match = re.search(r'(\d+(?:\.\d+)?)\s*year', duration_str)
    month_match = re.search(r'(\d+(?:\.\d+)?)\s*month', duration_str)

    if year_match:
        years = float(year_match.group(1))
    if month_match:
        months = float(month_match.group(1))

    if years or months:
        return round(years + months / 12, 2)

    return None


# ============================================================================
# NAME PARSING
# ============================================================================

def parse_full_name(full_name: str) -> tuple[Optional[str], Optional[str]]:
    """Split full name into first and last name."""
    if is_nan_or_none(full_name):
        return None, None

    full_name = str(full_name).strip()
    if not full_name:
        return None, None

    parts = full_name.split(' ', 1)
    first_name = parts[0] if parts else None
    last_name = parts[1] if len(parts) > 1 else None

    return first_name, last_name


# ============================================================================
# PROFILE EXTRACTION HELPERS
# ============================================================================

def extract_display_fields(raw_data: dict) -> dict:
    """Extract display fields from Crustdata raw response.

    Args:
        raw_data: The raw Crustdata API response

    Returns:
        Dict with normalized display fields
    """
    cd = raw_data or {}

    # Current employer (first in list)
    current_employers = cd.get('current_employers') or []
    emp = current_employers[0] if current_employers else {}

    # Name
    name = cd.get('name', '')
    first_name = cd.get('first_name', '')
    last_name = cd.get('last_name', '')
    if not first_name and name:
        parts = name.split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

    # Education - get first school
    all_schools = cd.get('all_schools') or []
    education = all_schools[0] if all_schools else ''

    return {
        'name': name,
        'first_name': first_name,
        'last_name': last_name,
        'headline': cd.get('headline', ''),
        'location': cd.get('location', ''),
        'summary': cd.get('summary', ''),
        'current_title': emp.get('employee_title') or emp.get('title') or '',
        'current_company': emp.get('employer_name') or emp.get('company_name') or '',
        'all_schools': all_schools,
        'all_employers': cd.get('all_employers') or [],
        'all_titles': cd.get('all_titles') or [],
        'past_employers': cd.get('past_employers') or [],
        'current_employers': current_employers,
        'education_background': cd.get('education_background') or [],
        'skills': cd.get('skills') or [],
        'education': education,
        'skills_str': ', '.join(cd.get('skills') or [])[:200],
        'connections': cd.get('num_of_connections') or cd.get('connections_count') or 0,
        'followers': cd.get('followers_count') or 0,
        'profile_picture': cd.get('profile_pic_url') or cd.get('profile_picture_url') or '',
    }


def extract_for_screening(raw_data: dict) -> dict:
    """Extract fields needed for AI screening."""
    display = extract_display_fields(raw_data)

    return {
        'name': display['name'],
        'headline': display['headline'],
        'location': display['location'],
        'summary': display['summary'],
        'current_title': display['current_title'],
        'current_company': display['current_company'],
        'all_employers': display['all_employers'],
        'all_titles': display['all_titles'],
        'all_schools': display['all_schools'],
        'skills': display['skills'],
        'past_employers': display['past_employers'],
        'current_employers': display['current_employers'],
        'education_background': display['education_background'],
        'connections': display['connections'],
    }


def format_profile_for_screening(profile: dict) -> str:
    """Format a profile dict into a readable string for AI screening."""
    raw_data = profile.get('raw_data') or {}
    fields = extract_for_screening(raw_data)

    lines = []
    lines.append(f"Name: {fields['name']}")
    lines.append(f"Headline: {fields['headline']}")
    lines.append(f"Location: {fields['location']}")

    if fields['current_title'] or fields['current_company']:
        lines.append(f"Current Role: {fields['current_title']} at {fields['current_company']}")

    if fields['summary']:
        lines.append(f"\nSummary:\n{fields['summary']}")

    if fields['all_employers']:
        lines.append(f"\nPast Employers: {', '.join(fields['all_employers'][:10])}")

    if fields['all_titles']:
        lines.append(f"Past Titles: {', '.join(fields['all_titles'][:10])}")

    if fields['all_schools']:
        lines.append(f"Education: {', '.join(fields['all_schools'])}")

    if fields['skills']:
        lines.append(f"Skills: {', '.join(fields['skills'][:20])}")

    # Work history detail
    if fields['past_employers']:
        lines.append("\nWork History:")
        for emp in fields['past_employers'][:5]:
            if isinstance(emp, dict):
                title = emp.get('employee_title') or emp.get('title') or ''
                company = emp.get('employer_name') or emp.get('company_name') or ''
                if title and company:
                    lines.append(f"  - {title} at {company}")

    return '\n'.join(lines)
