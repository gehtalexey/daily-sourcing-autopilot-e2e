"""
GEM Step — Push qualified candidates to GEM ATS.

Usage:
    python -m pipeline.gem_step <position_id>

Pushes qualified candidates to GEM project (email optional — can enrich in GEM later).
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

    # Get qualified candidates not yet pushed (email optional — can find in GEM later)
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'eq.qualified',
        'gem_pushed': 'eq.false',
    })

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

    # Block candidates missing email opener — they need openers before push
    missing_openers = [c for c in candidates if not c.get('email_opener')]
    if missing_openers:
        names = [c.get('candidate_name') or c.get('linkedin_url') for c in missing_openers]
        log(f"  BLOCKED: {len(missing_openers)} candidates missing email opener — skipping: {', '.join(names[:10])}")
        candidates = [c for c in candidates if c.get('email_opener')]
        if not candidates:
            log("No candidates with openers to push")
            print(json.dumps({"pushed": 0, "blocked_no_opener": len(missing_openers)}))
            return

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
        # Use flagship URL from enriched profile (not obfuscated search URL)
        candidate_id = None
        flagship_url = raw_data.get('linkedin_flagship_url') or raw_data.get('flagship_profile_url') or url
        linkedin_handle = flagship_url.split('/in/')[-1].strip('/') if '/in/' in flagship_url else ''
        if linkedin_handle:
            try:
                resp = gem._request('GET', 'candidates', params={'linked_in_handle': linkedin_handle, 'limit': 1})
                if resp.status_code == 200:
                    found = resp.json()
                    if found:
                        candidate_id = found[0].get('id')
                    else:
                        log(f"  WARN: Could not find {name} in GEM by handle '{linkedin_handle}' — custom fields/nickname won't be set")
            except Exception as e:
                log(f"  WARN: GEM lookup failed for {name}: {e}")

        if candidate_id:
            # Build custom field values for GEM:
            # email opener = personalized opener line
            # score = fit level + numeric score
            # reason = screening notes (why they fit)
            custom_fields = []
            if field_map.get('email opener') and c.get('email_opener'):
                custom_fields.append({'custom_field_id': field_map['email opener'], 'value': c['email_opener']})
            if field_map.get('score') and c.get('screening_score') is not None:
                score = c['screening_score']
                fit = 'Strong Fit' if score >= 8 else 'Good Fit' if score >= 6 else 'Partial Fit'
                custom_fields.append({'custom_field_id': field_map['score'], 'value': f'{fit} ({score}/10)'})
            if field_map.get('reason') and c.get('screening_notes'):
                custom_fields.append({'custom_field_id': field_map['reason'], 'value': c['screening_notes']})

            # Build main profile fields for update
            # nickname = email opener (used as {{nickname}} token in GEM sequences)
            # GEM nickname field has 255 char limit — truncate if needed
            opener = c.get('email_opener') or ''
            if len(opener) > 255:
                opener = opener[:252] + '...'
            profile_update = {
                'first_name': candidate_data.get('first_name'),
                'last_name': candidate_data.get('last_name'),
                'title': candidate_data.get('current_title'),
                'company': candidate_data.get('current_company'),
                'location': candidate_data.get('location'),
                'nickname': opener or None,
            }

            # Update candidate with profile fields + email + custom fields
            gem.update_candidate(candidate_id, candidate_data=profile_update,
                                  email=c.get('personal_email'), custom_fields=custom_fields)

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
