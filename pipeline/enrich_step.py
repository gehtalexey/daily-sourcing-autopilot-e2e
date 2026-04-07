"""
Enrich Step — Crustdata batch enrichment + Supabase save.

Usage:
    python -m pipeline.enrich_step <position_id>

Enriches candidates' LinkedIn profiles via Crustdata API.
Skips profiles already enriched within the last 3 months.
Saves enriched data to the profiles table in Supabase.
Prints JSON stats to stdout.
"""

import sys
import json

from core.db import (
    get_supabase_client,
    get_pipeline_candidates,
    get_recently_enriched_urls,
    save_enriched_profile,
    ENRICHMENT_REFRESH_MONTHS,
)
from core.normalizers import normalize_linkedin_url
from integrations.crustdata import get_crustdata_client


def log(msg):
    print(f"[enrich] {msg}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.enrich_step <position_id>", file=sys.stderr)
        sys.exit(1)

    position_id = sys.argv[1]

    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    crustdata = get_crustdata_client()
    if not crustdata:
        print(json.dumps({"error": "Crustdata not configured"}))
        sys.exit(1)

    # Get candidates that haven't been screened yet (still in pipeline)
    candidates = get_pipeline_candidates(client, position_id, {
        'screening_result': 'is.null',
    })

    if not candidates:
        log("No candidates to enrich")
        print(json.dumps({"enriched_new": 0, "from_cache": 0, "credits_used": 0}))
        return

    # Extract LinkedIn URLs
    all_urls = []
    for c in candidates:
        url = normalize_linkedin_url(c.get('linkedin_url'))
        if url:
            all_urls.append(url)

    log(f"Found {len(all_urls)} URLs to check for enrichment")

    # Check which are already enriched recently
    recently_enriched = set(get_recently_enriched_urls(client, months=ENRICHMENT_REFRESH_MONTHS))
    urls_to_enrich = [u for u in all_urls if u not in recently_enriched]
    from_cache = len(all_urls) - len(urls_to_enrich)

    log(f"Already enriched (cache): {from_cache}")
    log(f"Need enrichment: {len(urls_to_enrich)}")

    if not urls_to_enrich:
        log("All profiles already enriched")
        print(json.dumps({"enriched_new": 0, "from_cache": from_cache, "credits_used": 0}))
        return

    # Enrich via Crustdata API
    def on_progress(current, total, batch):
        log(f"  Enriched {current}/{total}")

    log(f"Enriching {len(urls_to_enrich)} profiles via Crustdata...")
    results = crustdata.enrich_batch(
        urls_to_enrich,
        batch_size=25,
        delay=1.0,
        on_progress=on_progress,
    )

    # Save to Supabase
    enriched_count = 0
    failed_count = 0

    for result in results:
        if isinstance(result, dict) and result.get('error'):
            failed_count += 1
            log(f"  Error: {result.get('linkedin_url', '?')}: {result['error']}")
            continue

        # Extract LinkedIn URL from response
        linkedin_url = None
        if isinstance(result, dict):
            linkedin_url = (
                result.get('linkedin_flagship_url') or
                result.get('linkedin_profile_url') or
                result.get('linkedin_url')
            )

        if not linkedin_url:
            failed_count += 1
            continue

        try:
            save_enriched_profile(client, linkedin_url, result,
                                  original_url=result.get('linkedin_profile_url'))
            enriched_count += 1
        except Exception as e:
            log(f"  Save error for {linkedin_url}: {e}")
            failed_count += 1

    credits_used = enriched_count * 3  # 3 credits per profile

    stats = {
        "enriched_new": enriched_count,
        "from_cache": from_cache,
        "failed": failed_count,
        "credits_used": credits_used,
    }

    log(f"Enrichment complete: {enriched_count} new, {from_cache} cached, {failed_count} failed")
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
