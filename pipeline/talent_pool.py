"""
Talent Pool — Search existing enriched profiles before external Crustdata search.

Scans the shared Supabase profiles table for candidates that might fit a new
position, based on title keywords, skills, and company patterns from the JD.

Usage:
    python -m pipeline.talent_pool search <position_id>
        → Finds matching profiles from the DB that aren't in this position's pipeline
        → Output: {matches: [{name, url, title, company, skills_match, ...}], count: N}

    python -m pipeline.talent_pool add <position_id>
        → Reads JSON array of LinkedIn URLs from stdin
        → Adds them to pipeline_candidates for this position
        → Output: {added: N, already_in_pipeline: N}
"""

import sys
import json
import re
from datetime import datetime, timezone

from core.db import (
    get_supabase_client,
    get_pipeline_position,
    get_pipeline_candidates,
    upsert_pipeline_candidate,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[talent_pool] {msg}", file=sys.stderr)


def extract_keywords_from_jd(hm_notes: str) -> dict:
    """Extract searchable keywords from JD/hm_notes.

    Returns dict with:
        title_keywords: words to match against current_title
        skill_keywords: words to match against skills array
        company_exclude: companies to exclude (from dealbreakers)
    """
    if not hm_notes:
        return {'title_keywords': [], 'skill_keywords': [], 'company_exclude': []}

    text = hm_notes.lower()

    # Extract title keywords from must-haves and role description
    title_keywords = set()
    title_patterns = [
        r'devops', r'sre', r'site reliability', r'platform',
        r'infrastructure', r'cloud', r'team lead', r'manager',
        r'director', r'head of', r'tech lead', r'group lead',
    ]
    for pattern in title_patterns:
        if re.search(pattern, text):
            title_keywords.add(pattern)

    # Extract skill keywords from must-haves
    skill_patterns = [
        r'kubernetes', r'k8s', r'terraform', r'docker', r'ci/cd',
        r'aws', r'gcp', r'azure', r'jenkins', r'ansible', r'helm',
        r'argocd', r'gitops', r'prometheus', r'grafana', r'python',
        r'go\b', r'linux', r'observability', r'finops',
    ]
    skill_keywords = set()
    for pattern in skill_patterns:
        if re.search(pattern, text):
            skill_keywords.add(pattern.replace(r'\b', ''))

    # Extract excluded companies from dealbreakers
    company_exclude = set()
    exclude_patterns = [
        r'develeap', r'tikal', r'sela', r'matrix', r'ness',
        r'bezeq', r'pelephone', r'partner', r'cellcom',
        r'taldor',
    ]
    for pattern in exclude_patterns:
        if re.search(pattern, text):
            company_exclude.add(pattern)

    return {
        'title_keywords': list(title_keywords),
        'skill_keywords': list(skill_keywords),
        'company_exclude': list(company_exclude),
    }


def cmd_search(position_id: str):
    """Search existing profiles for candidates matching this position."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    position = get_pipeline_position(client, position_id)
    if not position:
        print(json.dumps({"error": f"Position '{position_id}' not found"}))
        sys.exit(1)

    hm_notes = position.get('hm_notes') or position.get('job_description') or ''
    keywords = extract_keywords_from_jd(hm_notes)

    log(f"Keywords: titles={keywords['title_keywords']}, skills={keywords['skill_keywords']}")

    # Get all enriched profiles from DB
    all_profiles = client.select('profiles', 'linkedin_url,current_title,current_company,all_employers,all_titles,skills,name',
                                  {'enrichment_status': 'eq.enriched'}, limit=50000)

    if not all_profiles:
        log("No enriched profiles in DB")
        print(json.dumps({"matches": [], "count": 0}))
        return

    log(f"Scanning {len(all_profiles)} enriched profiles")

    # Get URLs already in this position's pipeline
    existing = get_pipeline_candidates(client, position_id, {})
    existing_urls = {c.get('linkedin_url') for c in existing if c.get('linkedin_url')}

    matches = []
    for p in all_profiles:
        url = p.get('linkedin_url', '')
        if not url or url in existing_urls:
            continue

        title = (p.get('current_title') or '').lower()
        company = (p.get('current_company') or '').lower()
        skills = [s.lower() for s in (p.get('skills') or [])]
        all_titles_list = [t.lower() for t in (p.get('all_titles') or [])]
        all_employers_list = [e.lower() for e in (p.get('all_employers') or [])]

        # Check company exclude
        excluded = False
        for exc in keywords['company_exclude']:
            if exc in company:
                excluded = True
                break
        if excluded:
            continue

        # Score match
        title_score = 0
        for kw in keywords['title_keywords']:
            if kw in title:
                title_score += 2
            elif any(kw in t for t in all_titles_list):
                title_score += 1

        skill_score = 0
        for kw in keywords['skill_keywords']:
            if any(kw in s for s in skills):
                skill_score += 1

        total_score = title_score + skill_score

        # Minimum threshold: at least 1 title match + 2 skill matches
        if title_score >= 1 and skill_score >= 2:
            matches.append({
                'name': p.get('name', '?'),
                'linkedin_url': url,
                'current_title': p.get('current_title', ''),
                'current_company': p.get('current_company', ''),
                'match_score': total_score,
                'title_matches': title_score,
                'skill_matches': skill_score,
                'matched_skills': [kw for kw in keywords['skill_keywords'] if any(kw in s for s in skills)],
            })

    # Sort by match score
    matches.sort(key=lambda x: -x['match_score'])

    log(f"Found {len(matches)} potential matches from talent pool")
    for m in matches[:10]:
        log(f"  {(m['name'] or '?'):30s} | {(m['current_title'] or '')[:30]:30s} | score={m['match_score']} skills={m['matched_skills']}")

    print(json.dumps({
        "matches": matches[:100],  # Cap at 100
        "count": len(matches),
        "keywords": keywords,
        "total_scanned": len(all_profiles),
    }))


def cmd_add(position_id: str):
    """Add talent pool matches to pipeline. Reads JSON array of URLs from stdin."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    urls = json.loads(raw)
    if not isinstance(urls, list):
        urls = [urls]

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    added = 0
    already = 0

    for url in urls:
        normalized = normalize_linkedin_url(url)
        if not normalized:
            continue

        try:
            upsert_pipeline_candidate(client, position_id, normalized, {
                'source': 'talent_pool',
                'search_run_date': today,
            })
            added += 1
        except Exception:
            already += 1

    log(f"Added {added} from talent pool, {already} already in pipeline")
    print(json.dumps({"added": added, "already_in_pipeline": already}))


def main():
    if len(sys.argv) < 3:
        print("Usage:", file=sys.stderr)
        print("  python -m pipeline.talent_pool search <position_id>", file=sys.stderr)
        print("  python -m pipeline.talent_pool add <position_id>  (reads URLs from stdin)", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'search':
        cmd_search(position_id)
    elif command == 'add':
        cmd_add(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
