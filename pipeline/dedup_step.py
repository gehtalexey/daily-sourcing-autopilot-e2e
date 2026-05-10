"""
Dedup Step — Merge same-person duplicate rows in pipeline_candidates.

Background:
    Crustdata search sometimes returns the same human under two different
    LinkedIn URL forms — a flagship URL like /in/john-doe and an obfuscated
    URL like /in/ACoAA....  The (position_id, linkedin_url) unique constraint
    treats them as different rows, so the same candidate ends up duplicated.

    Worse, when enrichment resolves an obfuscated URL to its flagship form
    and tries to PATCH the candidate row, the existing flagship-URL row in
    pipeline_candidates causes a 409 conflict — leaving the obfuscated row
    orphaned (no enriched profile, no screening).

This module finds those pairs, picks a winner by state, copies any
fields the winner is missing from the loser, deletes the loser, and writes
an audit row to pipeline_dedup_merges.

Sub-commands:
    python -m pipeline.dedup_step find [position_id]
        -> Lists duplicate pairs without changing data.
        -> Output: {pairs: [...], total: N}

    python -m pipeline.dedup_step merge [position_id] [--dry-run]
        -> Merges the duplicates.  Without --dry-run actually deletes losers.
        -> Output: {merged: N, dry_run: bool, errors: [...]}

If position_id is omitted, the command runs across all active positions.
"""

import sys
import json
from datetime import datetime, timezone

import requests

from core.db import (
    get_supabase_client,
    SupabaseClient,
)


def log(msg: str) -> None:
    print(f"[dedup] {msg}", file=sys.stderr)


# Fields we copy from loser -> winner if the winner's value is null/empty.
# Order doesn't matter; all are independent text/number/json columns.
MERGE_FIELDS = (
    'candidate_name',
    'current_company',
    'current_title',
    'headline',
    'education',
    'personal_email',
    'email_opener',
    'screening_score',
    'screening_result',
    'screening_notes',
    'screening_detail',
    'screened_at',
    'gem_pushed',
    'gem_pushed_at',
    'enrich_failed_at',
    'hm_feedback',
    'hm_rejected_at',
    'hm_rejection_reason',
)


def _state_score(row: dict) -> int:
    """Score a candidate row by how much progress it represents.

    Higher = more progressed = better winner.
    """
    if row.get('gem_pushed'):
        return 5
    result = (row.get('screening_result') or '').strip().lower()
    if result == 'qualified':
        return 4
    if result == 'not_qualified':
        return 3
    if row.get('enrich_failed_at'):
        # Failed enrichment is worse than unscreened -- it's a dead end.
        return 1
    if row.get('screening_result') is None:
        return 2
    # Unknown state — treat as fresh
    return 2


def _state_label(row: dict) -> str:
    if row.get('gem_pushed'):
        return 'qualified+pushed'
    result = (row.get('screening_result') or '').strip().lower() or 'unscreened'
    if row.get('enrich_failed_at') and result == 'unscreened':
        return 'enrich_failed'
    return result


def _is_obfuscated_url(url: str) -> bool:
    """Heuristic: LinkedIn obfuscated URLs are like /in/ACoAA<chars>."""
    if not url:
        return True
    return '/in/ACoA' in url or '/in/acoa' in url


def _pick_winner(row_a: dict, row_b: dict) -> tuple[dict, dict]:
    """Return (winner, loser).  Higher state score wins; ties go to flagship URL,
    then to whichever row was created later."""
    score_a = _state_score(row_a)
    score_b = _state_score(row_b)
    if score_a != score_b:
        return (row_a, row_b) if score_a > score_b else (row_b, row_a)

    # Tie on state -> prefer flagship URL form
    a_obf = _is_obfuscated_url(row_a.get('linkedin_url'))
    b_obf = _is_obfuscated_url(row_b.get('linkedin_url'))
    if a_obf != b_obf:
        return (row_b, row_a) if a_obf else (row_a, row_b)

    # Tie on URL form -> keep newer
    a_created = row_a.get('created_at') or ''
    b_created = row_b.get('created_at') or ''
    return (row_a, row_b) if a_created >= b_created else (row_b, row_a)


def _build_pair_index(client: SupabaseClient, position_filter: str | None) -> list[dict]:
    """Find duplicate pairs by joining pipeline_candidates with profiles via
    profiles.original_url -> profiles.linkedin_url.

    Returns a list of dicts: {position_id, obfuscated_url, flagship_url}
    only for pairs where BOTH rows exist in pipeline_candidates for the
    same position.
    """
    # Pull profiles that have a different original_url vs linkedin_url
    # (these are the ones where Crustdata resolved an obfuscated -> flagship).
    profile_filters = {
        'select': 'linkedin_url,original_url',
        # original_url is set + differs from linkedin_url
        'original_url': 'not.is.null',
    }
    profiles = client.select(
        'profiles',
        'linkedin_url,original_url',
        {'original_url': 'not.is.null'},
        limit=500000,
    )

    # Map: original_url -> flagship_url (only where they actually differ)
    obf_to_flag = {}
    for p in profiles:
        obf = p.get('original_url')
        flag = p.get('linkedin_url')
        if obf and flag and obf != flag:
            obf_to_flag[obf] = flag

    if not obf_to_flag:
        return []

    # Now load candidates for this position (or all) and find pairs
    cand_filters = {}
    if position_filter:
        cand_filters['position_id'] = f'eq.{position_filter}'

    candidates = client.select(
        'pipeline_candidates',
        '*',
        cand_filters,
        limit=500000,
    )

    # Index by (position_id, linkedin_url) -> row
    by_key = {}
    for c in candidates:
        key = (c.get('position_id'), c.get('linkedin_url'))
        by_key[key] = c

    pairs = []
    seen_pair_keys = set()
    for c in candidates:
        pid = c.get('position_id')
        url = c.get('linkedin_url')
        flag = obf_to_flag.get(url)
        if not flag or flag == url:
            continue
        twin = by_key.get((pid, flag))
        if not twin:
            continue
        # Avoid double-listing each pair
        pair_key = tuple(sorted([url, flag])) + (pid,)
        if pair_key in seen_pair_keys:
            continue
        seen_pair_keys.add(pair_key)
        pairs.append({
            'position_id': pid,
            'obfuscated': c,
            'flagship': twin,
        })
    return pairs


def cmd_find(position_id: str | None) -> None:
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    pairs = _build_pair_index(client, position_id)

    summary = []
    for p in pairs:
        winner, loser = _pick_winner(p['obfuscated'], p['flagship'])
        summary.append({
            'position_id': p['position_id'],
            'winner_url': winner.get('linkedin_url'),
            'winner_state': _state_label(winner),
            'loser_url': loser.get('linkedin_url'),
            'loser_state': _state_label(loser),
        })

    log(f"Found {len(pairs)} duplicate pair(s)"
        + (f" for {position_id}" if position_id else " across all positions"))
    print(json.dumps({"pairs": summary, "total": len(summary)}, default=str))


def _merge_fields(winner: dict, loser: dict) -> dict:
    """Return a dict of {field: value} to update the winner with, copying
    only fields where the winner's value is null/empty and the loser has
    a real value."""
    updates = {}
    for f in MERGE_FIELDS:
        w = winner.get(f)
        l = loser.get(f)
        if l is None or l == '':
            continue
        # Only fill when winner is missing it
        if w is None or w == '' or w is False:
            # Don't overwrite a real `false` for booleans like gem_pushed
            # only when it's explicitly None (a real `False` should stand).
            if f in ('gem_pushed',) and w is False:
                # Only promote True; don't demote
                if l is True:
                    updates[f] = True
                continue
            updates[f] = l
    return updates


def _delete_candidate(client: SupabaseClient, candidate_id: str) -> None:
    url = f"{client.url}/rest/v1/pipeline_candidates"
    headers = dict(client.headers)
    params = {'id': f'eq.{candidate_id}'}
    resp = requests.delete(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()


def _patch_candidate(client: SupabaseClient, candidate_id: str, patch: dict) -> None:
    url = f"{client.url}/rest/v1/pipeline_candidates"
    headers = dict(client.headers)
    params = {'id': f'eq.{candidate_id}'}
    resp = requests.patch(url, headers=headers, params=params, json=patch, timeout=30)
    resp.raise_for_status()


def _log_merge(
    client: SupabaseClient,
    position_id: str,
    winner_url: str,
    loser_url: str,
    winner_state: str,
    loser_state: str,
    fields_merged: dict,
    dry_run: bool,
    reason: str,
) -> None:
    payload = {
        'position_id': position_id,
        'winner_url': winner_url,
        'loser_url': loser_url,
        'winner_state': winner_state,
        'loser_state': loser_state,
        'fields_merged': fields_merged or {},
        'dry_run': dry_run,
        'reason': reason,
    }
    try:
        client.insert('pipeline_dedup_merges', payload)
    except Exception as e:
        log(f"  WARN: failed to write audit row: {e}")


def cmd_merge(position_id: str | None, dry_run: bool) -> None:
    client = get_supabase_client()
    if not client:
        print(json.dumps({"error": "Supabase not configured"}))
        sys.exit(1)

    pairs = _build_pair_index(client, position_id)
    log(f"{len(pairs)} pair(s) to process (dry_run={dry_run})")

    merged = 0
    errors = []

    for p in pairs:
        winner, loser = _pick_winner(p['obfuscated'], p['flagship'])
        winner_state = _state_label(winner)
        loser_state = _state_label(loser)
        field_updates = _merge_fields(winner, loser)

        log(f"  {p['position_id']}: keep {winner.get('linkedin_url')} "
            f"({winner_state}), drop {loser.get('linkedin_url')} "
            f"({loser_state}), copy {len(field_updates)} field(s)")

        if dry_run:
            _log_merge(
                client,
                p['position_id'],
                winner.get('linkedin_url') or '',
                loser.get('linkedin_url') or '',
                winner_state,
                loser_state,
                field_updates,
                dry_run=True,
                reason='flagship_obfuscated_pair',
            )
            merged += 1
            continue

        try:
            if field_updates:
                _patch_candidate(client, winner['id'], field_updates)
            _delete_candidate(client, loser['id'])
            _log_merge(
                client,
                p['position_id'],
                winner.get('linkedin_url') or '',
                loser.get('linkedin_url') or '',
                winner_state,
                loser_state,
                field_updates,
                dry_run=False,
                reason='flagship_obfuscated_pair',
            )
            merged += 1
        except Exception as e:
            err = {
                'position_id': p['position_id'],
                'winner_url': winner.get('linkedin_url'),
                'loser_url': loser.get('linkedin_url'),
                'error': str(e),
            }
            errors.append(err)
            log(f"  ERROR merging pair: {e}")

    log(f"Done: merged={merged}, errors={len(errors)}, dry_run={dry_run}")
    print(json.dumps({
        "merged": merged,
        "dry_run": dry_run,
        "errors": errors,
        "total_pairs": len(pairs),
    }, default=str))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    dry_run = '--dry-run' in args
    args = [a for a in args if not a.startswith('--')]
    position_id = args[0] if args else None

    if cmd == 'find':
        cmd_find(position_id)
    elif cmd == 'merge':
        cmd_merge(position_id, dry_run)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
