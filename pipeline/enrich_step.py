"""
Enrich Step — Helpers for Crustdata MCP-driven profile enrichment.

The actual enrichment is done by Claude Code using the crustdata_people_enrich
MCP tool. This module provides helpers to get URLs and save results.

Sub-commands:
    python -m pipeline.enrich_step get_urls <position_id>
        → Outputs JSON: {urls_to_enrich: [...], from_cache: N}

    python -m pipeline.enrich_step save_profiles <position_id>
        → Reads JSON array from stdin (Crustdata MCP enrich results)
        → Saves enriched profiles to the profiles table
        → Output: {saved, failed}
"""

import sys
import json
from datetime import datetime

from core.db import (
    get_supabase_client,
    get_pipeline_candidates,
    get_recently_enriched_urls,
    save_enriched_profile,
    update_pipeline_candidate,
    ENRICHMENT_REFRESH_MONTHS,
)
from core.normalizers import normalize_linkedin_url


def log(msg):
    print(f"[enrich] {msg}", file=sys.stderr)


def cmd_get_urls(position_id: str):
    """Get LinkedIn URLs that need enrichment for Claude to enrich via MCP."""
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    # Get candidates not yet screened
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    if not candidates:
        log("No candidates to enrich")
        print(json.dumps({"urls_to_enrich": [], "from_cache": 0}))
        return

    # Get raw URLs (preserve original case for obfuscated LinkedIn IDs)
    all_urls = []
    for c in candidates:
        url = c.get('linkedin_url', '')
        if url:
            all_urls.append(url)

    log(f"Found {len(all_urls)} candidate URLs")

    # Check which are already enriched recently (normalize for comparison)
    recently_enriched = set(get_recently_enriched_urls(client, months=ENRICHMENT_REFRESH_MONTHS))

    urls_to_enrich = []
    from_cache = 0
    for url in all_urls:
        normalized = normalize_linkedin_url(url)
        if normalized and normalized in recently_enriched:
            from_cache += 1
        else:
            urls_to_enrich.append(url)

    log(f"Need enrichment: {len(urls_to_enrich)}, from cache: {from_cache}")

    result = {
        "urls_to_enrich": urls_to_enrich,
        "from_cache": from_cache,
        "total": len(all_urls),
    }
    print(json.dumps(result))


def cmd_save_profiles(position_id: str):
    """Save MCP-enriched profiles to the profiles table. Reads JSON from stdin.

    Expects array of profile objects from crustdata_people_enrich MCP.
    Each profile should have linkedin_flagship_url or linkedin_profile_url.
    """
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    raw = sys.stdin.read()
    data = json.loads(raw)

    # Handle wrapped response
    profiles = data
    if isinstance(data, dict):
        profiles = data.get('profiles', data.get('data', [data]))
    if not isinstance(profiles, list):
        profiles = [profiles]

    saved = 0
    failed = 0

    for profile in profiles:
        if not isinstance(profile, dict):
            failed += 1
            continue

        if profile.get('error'):
            log(f"  Error profile: {profile.get('error')}")
            failed += 1
            continue

        # Use flagship URL as the canonical URL (clean /in/username format)
        linkedin_url = (
            profile.get('linkedin_flagship_url') or
            profile.get('linkedin_profile_url') or
            profile.get('linkedin_url')
        )

        if not linkedin_url:
            failed += 1
            continue

        # The obfuscated URL (ACoAAA...) was stored in pipeline_candidates during search
        original_url = profile.get('linkedin_profile_url') or linkedin_url
        flagship_url = profile.get('linkedin_flagship_url')

        # Use flagship (clean) URL as canonical
        canonical_url = flagship_url or linkedin_url

        try:
            save_enriched_profile(client, canonical_url, profile, original_url=original_url)

            # Update pipeline_candidates: replace obfuscated URL with flagship URL
            if flagship_url and original_url and flagship_url != original_url:
                normalized_original = normalize_linkedin_url(original_url)
                # Try matching by the normalized (lowercased) obfuscated URL
                try:
                    # Direct SQL update since the stored URL was lowercased by normalizer
                    url = f"{client.url}/rest/v1/pipeline_candidates"
                    params = {
                        'position_id': f'eq.{position_id}',
                        'linkedin_url': f'eq.{normalized_original}',
                    }
                    import requests as http_req
                    resp = http_req.patch(url, headers=client.headers,
                                          params=params,
                                          json={'linkedin_url': normalize_linkedin_url(flagship_url)},
                                          timeout=30)
                except Exception:
                    pass  # Non-fatal — candidate still findable

            saved += 1
            name = profile.get('name', canonical_url)
            log(f"  Saved: {name} -> {canonical_url}")
        except Exception as e:
            log(f"  Save error for {canonical_url}: {e}")
            failed += 1

    stats = {
        "saved": saved,
        "failed": failed,
    }
    log(f"Enrichment save: {saved} saved, {failed} failed")
    print(json.dumps(stats))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.enrich_step <command> <position_id>", file=sys.stderr)
        print("Commands: get_urls, save_profiles", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_urls':
        cmd_get_urls(position_id)
    elif command == 'save_profiles':
        cmd_save_profiles(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
