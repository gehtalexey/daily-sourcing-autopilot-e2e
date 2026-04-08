"""
GEM CSV Export — Generate CSV for GEM import with custom token fields.

Usage:
    python -m pipeline.gem_csv_export <position_id> [output_path]

Generates a CSV with columns that map to GEM's import fields:
- LinkedIn URL, First Name, Last Name, Email, Title, Company, Location
- Reason (= email opener for {{reason}} token)
- Extra 1 (= score for {{extra1}} token)
- Extra 2 (= screening notes for {{extra2}} token)
- Extra 3 (= empty, reserved)

The CSV can be uploaded to GEM via Projects > Options > Import CSV.
Map "Reason" to {{reason}}, "Extra 1" to {{extra1}}, etc.
"""

import sys
import csv
import json
import io
from pathlib import Path

from core.db import (
    get_supabase_client,
    get_pipeline_candidates,
    get_profiles_batch,
)


def log(msg):
    print(f"[gem_csv] {msg}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.gem_csv_export <position_id> [output_path]", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else f"gem_import_{position_id}.csv"

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Get qualified candidates
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
    })

    if not candidates:
        log("No qualified candidates")
        print(json.dumps({"error": "No qualified candidates", "count": 0}))
        return

    # Load enriched profiles
    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    profiles_map = get_profiles_batch(client, urls)

    # Build CSV
    rows = []
    for c in candidates:
        url = c.get('linkedin_url', '')
        profile = profiles_map.get(url, {})
        raw = profile.get('raw_data', {})

        # Current role
        current_title = ''
        current_company = ''
        employers = raw.get('current_employers') or []
        if employers and isinstance(employers[0], dict):
            current_title = employers[0].get('employee_title') or employers[0].get('title') or ''
            current_company = employers[0].get('employer_name') or employers[0].get('company_name') or ''

        score = c.get('screening_score')
        fit = 'Strong Fit' if score and score >= 8 else 'Good Fit' if score and score >= 6 else 'Partial Fit'

        # Name — fallback to splitting full name
        first_name = raw.get('first_name', '')
        last_name = raw.get('last_name', '')
        if not first_name and raw.get('name'):
            parts = raw['name'].split(' ', 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ''

        rows.append({
            'LinkedIn URL': url,
            'First Name': first_name,
            'Last Name': last_name,
            'Email': c.get('personal_email', ''),
            'Title': current_title,
            'Company': current_company,
            'Location': raw.get('location', ''),
            'Reason': c.get('email_opener', ''),
            'Extra 1': f'{fit} ({score}/10)' if score else '',
            'Extra 2': c.get('screening_notes', ''),
            'Extra 3': '',
        })

    # Write CSV
    output = Path(output_path)
    with open(output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    log(f"Exported {len(rows)} candidates to {output}")
    print(json.dumps({"exported": len(rows), "path": str(output)}))


if __name__ == '__main__':
    main()
