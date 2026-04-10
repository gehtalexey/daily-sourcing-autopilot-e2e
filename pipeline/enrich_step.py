"""
Enrich Step — Profile enrichment via Crustdata API.

Sub-commands:
    python -m pipeline.enrich_step get_urls <position_id>
        → Outputs JSON: {urls_to_enrich: [...], from_cache: N}

    python -m pipeline.enrich_step enrich <position_id>
        → Gets URLs, enriches via direct API, saves to DB. No MCP needed.
        → Output: {enriched, from_cache, failed, saved}

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
    get_pipeline_position,
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
    """Get LinkedIn URLs that need enrichment."""
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

    # Config: batch size and daily cap
    position = get_pipeline_position(client, position_id)
    batch_size = 100
    daily_cap = 400
    if position:
        sf = position.get('search_filters') or {}
        batch_size = sf.get('enrich_batch_size', 100)
        daily_cap = sf.get('daily_enrich_cap', 400)

    # Check how many we already enriched today (against daily cap)
    today = datetime.utcnow().strftime('%Y-%m-%d')
    cutoff = f"{today}T00:00:00"
    try:
        today_enriched = client.count('profiles', {'enriched_at': f'gte.{cutoff}'})
    except Exception:
        today_enriched = 0

    remaining_cap = max(0, daily_cap - today_enriched)

    if remaining_cap == 0:
        log(f"Daily enrich cap reached ({daily_cap}). No more enrichments today.")
        print(json.dumps({
            "urls_to_enrich": [],
            "from_cache": from_cache,
            "total_pending": len(urls_to_enrich),
            "daily_cap_reached": True,
            "enriched_today": today_enriched,
        }))
        return

    # Apply batch size and remaining cap
    effective_limit = min(batch_size, remaining_cap, len(urls_to_enrich))
    if effective_limit < len(urls_to_enrich):
        log(f"Returning {effective_limit} of {len(urls_to_enrich)} pending "
            f"(batch: {batch_size}, cap remaining: {remaining_cap})")
        urls_to_enrich = urls_to_enrich[:effective_limit]

    log(f"To enrich: {len(urls_to_enrich)}, from cache: {from_cache}, "
        f"enriched today: {today_enriched}/{daily_cap}")

    result = {
        "urls_to_enrich": urls_to_enrich,
        "from_cache": from_cache,
        "total_pending": len(all_urls) - from_cache,
        "enriched_today": today_enriched,
        "daily_cap": daily_cap,
        "remaining_cap": remaining_cap - len(urls_to_enrich),
    }
    print(json.dumps(result))


def _save_profile(client, position_id: str, profile: dict) -> bool:
    """Save a single enriched profile. Returns True on success."""
    if not isinstance(profile, dict) or profile.get('error'):
        return False

    linkedin_url = (
        profile.get('linkedin_flagship_url') or
        profile.get('linkedin_profile_url') or
        profile.get('linkedin_url')
    )
    if not linkedin_url:
        return False

    original_url = profile.get('linkedin_profile_url') or linkedin_url
    flagship_url = profile.get('linkedin_flagship_url')
    canonical_url = flagship_url or linkedin_url

    try:
        save_enriched_profile(client, canonical_url, profile, original_url=original_url)

        # Update pipeline_candidates: replace obfuscated URL with flagship URL
        # Update ALL positions (not just current) to prevent cross-position URL mismatch
        if flagship_url and original_url and flagship_url != original_url:
            normalized_original = normalize_linkedin_url(original_url)
            normalized_flagship = normalize_linkedin_url(flagship_url)
            if normalized_original and normalized_flagship and normalized_original != normalized_flagship:
                try:
                    import requests as http_req
                    url = f"{client.url}/rest/v1/pipeline_candidates"
                    # No position_id filter -- update across ALL positions
                    params = {
                        'linkedin_url': f'eq.{normalized_original}',
                    }
                    http_req.patch(url, headers=client.headers,
                                   params=params,
                                   json={'linkedin_url': normalized_flagship},
                                   timeout=30)
                except Exception:
                    pass  # Non-fatal

        return True
    except Exception as e:
        log(f"  Save error for {canonical_url}: {e}")
        return False


def cmd_enrich(position_id: str):
    """Full enrichment: get URLs, enrich via direct API, save to DB.

    This replaces the MCP-based enrichment flow. Runs entirely in Python
    without needing Claude to call MCP tools — faster and no timeouts.
    """
    from integrations.crustdata import get_crustdata_client

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    crustdata = get_crustdata_client()
    if not crustdata:
        print(json.dumps({"error": "Crustdata not configured"}))
        sys.exit(1)

    # Get candidates not yet screened
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    if not candidates:
        log("No candidates to enrich")
        print(json.dumps({"enriched": 0, "from_cache": 0, "failed": 0, "saved": 0}))
        return

    all_urls = [c.get('linkedin_url', '') for c in candidates if c.get('linkedin_url')]
    log(f"Found {len(all_urls)} candidate URLs")

    # Check cache
    recently_enriched = set(get_recently_enriched_urls(client, months=ENRICHMENT_REFRESH_MONTHS))
    urls_to_enrich = []
    from_cache = 0
    for url in all_urls:
        normalized = normalize_linkedin_url(url)
        if normalized and normalized in recently_enriched:
            from_cache += 1
        else:
            urls_to_enrich.append(url)

    # Apply daily cap
    position = get_pipeline_position(client, position_id)
    daily_cap = 400
    if position:
        sf = position.get('search_filters') or {}
        daily_cap = sf.get('daily_enrich_cap', 400)

    today = datetime.utcnow().strftime('%Y-%m-%d')
    cutoff = f"{today}T00:00:00"
    try:
        today_enriched = client.count('profiles', {'enriched_at': f'gte.{cutoff}'})
    except Exception:
        today_enriched = 0

    remaining_cap = max(0, daily_cap - today_enriched)
    if remaining_cap == 0:
        log(f"Daily enrich cap reached ({daily_cap})")
        print(json.dumps({
            "enriched": 0, "from_cache": from_cache, "failed": 0, "saved": 0,
            "daily_cap_reached": True, "enriched_today": today_enriched,
        }))
        return

    urls_to_enrich = urls_to_enrich[:remaining_cap]
    log(f"Enriching {len(urls_to_enrich)} profiles (cache: {from_cache}, today: {today_enriched}/{daily_cap})")

    if not urls_to_enrich:
        print(json.dumps({"enriched": 0, "from_cache": from_cache, "failed": 0, "saved": 0}))
        return

    # Enrich via direct API — batch_size=25 (Crustdata max), 1 sec delay
    def on_progress(current, total, batch_result):
        log(f"  Enriched {current}/{total}")

    profiles = crustdata.enrich_batch(
        urls_to_enrich, batch_size=25, delay=1.0, on_progress=on_progress
    )

    # Save results
    saved = 0
    failed = 0
    for profile in profiles:
        if profile.get('error'):
            error_code = profile.get('error_code', '')
            if error_code == 'PE03':
                log(f"  Not found: {profile.get('linkedin_profile_url', '?')}")
            else:
                log(f"  Error: {profile.get('error', '')[:100]}")
            failed += 1
            continue

        if _save_profile(client, position_id, profile):
            name = profile.get('name', '?')
            saved += 1
        else:
            failed += 1

    log(f"Enrichment complete: {saved} saved, {failed} failed, {from_cache} cached")

    print(json.dumps({
        "enriched": saved + failed,
        "from_cache": from_cache,
        "saved": saved,
        "failed": failed,
        "enriched_today": today_enriched + saved,
        "daily_cap": daily_cap,
        "remaining_cap": remaining_cap - len(urls_to_enrich),
    }))


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
        if _save_profile(client, position_id, profile):
            canonical_url = (
                profile.get('linkedin_flagship_url') or
                profile.get('linkedin_profile_url') or
                profile.get('linkedin_url', '?')
            )
            name = profile.get('name', canonical_url)
            log(f"  Saved: {name} -> {canonical_url}")
            saved += 1
        else:
            if isinstance(profile, dict) and profile.get('error'):
                log(f"  Error profile: {profile.get('error')}")
            failed += 1

    stats = {"saved": saved, "failed": failed}
    log(f"Enrichment save: {saved} saved, {failed} failed")
    print(json.dumps(stats))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.enrich_step <command> <position_id>", file=sys.stderr)
        print("Commands: get_urls, enrich, save_profiles", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    position_id = sys.argv[2]

    if command == 'get_urls':
        cmd_get_urls(position_id)
    elif command == 'enrich':
        cmd_enrich(position_id)
    elif command == 'save_profiles':
        cmd_save_profiles(position_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
