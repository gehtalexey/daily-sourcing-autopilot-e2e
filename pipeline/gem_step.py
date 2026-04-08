"""
GEM Step — Push qualified candidates to GEM ATS.

Usage:
    python -m pipeline.gem_step <position_id>

Pushes qualified candidates (with email) to GEM project.
Maps ALL enriched profile fields including custom email openers.
Checks for duplicates before creating.
Updates pipeline_candidates.gem_pushed.
Prints JSON stats to stdout.
"""

import sys
import json
from datetime import datetime

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
    update_pipeline_candidate,
    get_profiles_batch,
)
from integrations.gem import get_gem_client


def log(msg):
    print(f"[gem] {msg}", file=sys.stderr)


def format_candidate(profile_raw: dict, candidate: dict, position_id: str) -> dict:
    """Format candidate data for GEM API with ALL fields properly mapped.

    Args:
        profile_raw: Raw Crustdata enrichment data from profiles.raw_data
        candidate: Pipeline candidate record from pipeline_candidates
        position_id: Position ID for tagging

    Returns:
        dict ready for GEM create_candidate()
    """
    raw = profile_raw or {}

    # --- Name ---
    first_name = raw.get('first_name') or ''
    last_name = raw.get('last_name') or ''
    if not first_name and raw.get('name'):
        parts = raw['name'].split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

    # --- Current role ---
    current_title = ''
    current_company = ''
    current_employers = raw.get('current_employers') or []
    if current_employers and isinstance(current_employers, list):
        emp = current_employers[0] if current_employers else {}
        if isinstance(emp, dict):
            current_title = (
                emp.get('employee_title') or
                emp.get('title') or
                ''
            )
            current_company = (
                emp.get('employer_name') or
                emp.get('company_name') or
                emp.get('name') or
                ''
            )

    # Fallback from headline
    if (not current_title or not current_company) and raw.get('headline'):
        headline = raw['headline']
        if ' at ' in headline:
            parts = headline.split(' at ', 1)
            if not current_title:
                current_title = parts[0].strip()
            if not current_company and len(parts) > 1:
                current_company = parts[1].split('/')[0].split('|')[0].strip()

    # --- Location ---
    location = raw.get('location') or raw.get('region') or ''

    # --- Build notes section ---
    notes_parts = []

    # Screening info
    score = candidate.get('screening_score')
    result = candidate.get('screening_result')
    if score is not None:
        notes_parts.append(f"AI Score: {score}/10 ({result})")

    if candidate.get('screening_notes'):
        notes_parts.append(f"\nScreening Notes:\n{candidate['screening_notes']}")

    # Email opener — prominently placed for recruiter use
    if candidate.get('email_opener'):
        notes_parts.append(f"\n--- Personalized Email Opener ---\n{candidate['email_opener']}")

    # Work history summary
    all_employers = raw.get('all_employers') or []
    all_titles = raw.get('all_titles') or []
    if all_employers:
        notes_parts.append(f"\nPast Companies: {', '.join(all_employers[:8])}")
    if all_titles:
        notes_parts.append(f"Past Titles: {', '.join(all_titles[:8])}")

    # Education
    all_schools = raw.get('all_schools') or []
    if all_schools:
        notes_parts.append(f"Education: {', '.join(all_schools[:5])}")

    # Skills
    skills = raw.get('skills') or []
    if skills:
        notes_parts.append(f"Skills: {', '.join(skills[:15])}")

    # Source metadata
    notes_parts.append(f"\nSource: Autopilot ({position_id}) | {datetime.utcnow().strftime('%Y-%m-%d')}")

    # --- Tags ---
    tags = ['autopilot', position_id]
    if result:
        tags.append(result.replace(' ', '-').lower())
    if score and score >= 8:
        tags.append('strong-fit')

    return {
        'first_name': first_name,
        'last_name': last_name,
        'email': candidate.get('personal_email'),
        'linkedin_url': candidate.get('linkedin_url'),
        'headline': raw.get('headline') or '',
        'location': location,
        'current_company': current_company,
        'current_title': current_title,
        'notes': '\n'.join(notes_parts),
        'tags': tags,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.gem_step <position_id>", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    gem = get_gem_client()
    if not gem:
        log("GEM not configured, skipping push")
        print(json.dumps({"error": "GEM not configured", "pushed": 0}))
        return

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    project_id = position.get('gem_project_id') or gem.default_project_id
    if not project_id:
        log("No GEM project ID configured")
        print(json.dumps({"error": "No GEM project ID", "pushed": 0}))
        return

    # Get qualified candidates with email, not yet pushed
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
        'gem_pushed': 'eq.false',
    })

    # Filter to those with email
    candidates = [c for c in candidates if c.get('personal_email')]

    if not candidates:
        log("No candidates ready for GEM push")
        print(json.dumps({"pushed": 0, "duplicates": 0, "errors": 0}))
        return

    log(f"Pushing {len(candidates)} candidates to GEM project {project_id}")

    # Get or create custom fields for this project
    field_map = gem.get_or_create_custom_fields(project_id)
    log(f"Custom fields: {list(field_map.keys())}")

    # Load enriched profiles
    urls = [c.get('linkedin_url') for c in candidates if c.get('linkedin_url')]
    profiles_map = get_profiles_batch(client, urls)

    pushed = 0
    duplicates = 0
    errors = 0
    pushed_names = []

    for c in candidates:
        url = c.get('linkedin_url')
        profile = profiles_map.get(url, {})
        raw_data = profile.get('raw_data', {})
        name = raw_data.get('name') or url

        # Format with all fields mapped
        candidate_data = format_candidate(raw_data, c, position_id)

        # Validate required fields
        if not candidate_data.get('first_name'):
            log(f"  SKIP (no name): {url}")
            errors += 1
            continue

        # Check for duplicate in GEM
        if gem.candidate_exists(project_id, url):
            log(f"  EXISTS: {name} — adding to project + updating fields")
            duplicates += 1
        else:
            # Create new candidate
            result = gem.create_candidate(project_id, candidate_data)
            if not result.get('success'):
                errors += 1
                log(f"  ERROR: {name}: {result.get('error')}")
                continue

        # Get candidate ID for updating custom fields
        candidate_id = None
        linkedin_handle = url.split('/in/')[-1].strip('/') if '/in/' in url else ''
        if linkedin_handle:
            try:
                resp = gem._request('GET', 'candidates', params={'linked_in_handle': linkedin_handle, 'limit': 1})
                if resp.status_code == 200:
                    found = resp.json()
                    if found:
                        candidate_id = found[0].get('id')
            except Exception:
                pass

        if candidate_id:
            # Build custom field values
            custom_fields = []
            if field_map.get('Personal Email') and c.get('personal_email'):
                custom_fields.append({'custom_field_id': field_map['Personal Email'], 'value': c['personal_email']})
            if field_map.get('Email Opener') and c.get('email_opener'):
                custom_fields.append({'custom_field_id': field_map['Email Opener'], 'value': c['email_opener']})
            if field_map.get('Fit Level') and c.get('screening_score') is not None:
                score = c['screening_score']
                fit = 'Strong Fit' if score >= 8 else 'Good Fit' if score >= 6 else 'Partial Fit'
                custom_fields.append({'custom_field_id': field_map['Fit Level'], 'value': f'{fit} ({score}/10)'})
            if field_map.get('Screening Notes') and c.get('screening_notes'):
                custom_fields.append({'custom_field_id': field_map['Screening Notes'], 'value': c['screening_notes']})

            # Update candidate with email + custom fields
            gem.update_candidate(candidate_id, email=c.get('personal_email'), custom_fields=custom_fields)

        update_pipeline_candidate(client, position_id, url, {
            'gem_pushed': True,
            'gem_pushed_at': datetime.utcnow().isoformat(),
        })
        pushed += 1
        pushed_names.append(name)
        log(f"  PUSHED: {name} | {candidate_data.get('current_title', '')} at {candidate_data.get('current_company', '')}")

    stats = {
        "pushed": pushed,
        "duplicates": duplicates,
        "errors": errors,
        "pushed_names": pushed_names[:20],
    }

    log(f"GEM push: {pushed} pushed, {duplicates} duplicates, {errors} errors")
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
