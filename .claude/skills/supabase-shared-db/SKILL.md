---
name: supabase-shared-db
description: Shared Supabase database reference. Use when reading/writing profiles, debugging enrichment dedup, or checking cross-project compatibility. Critical for avoiding data overwrites between daily-sourcing-autopilot and SourcingX.
argument-hint: [table-name-or-question]
---

# Shared Supabase Database Reference

## CRITICAL: This DB is Shared Across Projects

The same Supabase instance is used by multiple projects:
- **daily-sourcing-autopilot-e2e** (this project) — automated pipeline
- **SourcingX** — interactive sourcing app (Streamlit)

Both projects read and write to the **same `profiles` table**. Upserts use `linkedin_url` as the conflict key with `merge-duplicates`. Last write wins per field. This means:

**If both projects enrich the same LinkedIn profile, the last upsert overwrites shared fields.**

## URL Normalization — KNOWN DIVERGENCE

### SourcingX normalizer
- Lowercases the ENTIRE URL including slug
- `https://www.linkedin.com/in/ACoAAAbCdEf` → `https://www.linkedin.com/in/acoaaabcdef`

### Autopilot normalizer
- Preserves case for obfuscated slugs (ACoAAA... pattern)
- `https://www.linkedin.com/in/ACoAAAbCdEf` → `https://www.linkedin.com/in/ACoAAAbCdEf`
- Only lowercases normal profile slugs

**Impact:** If a profile is first enriched via SourcingX (lowercase slug) and then autopilot tries to enrich the same profile with case-preserved slug, they create TWO rows in `profiles` with different `linkedin_url` values pointing to the same person.

**Fix needed:** Align normalizers. Either both preserve case or both lowercase. Since SourcingX has been running longer and has more data, the autopilot normalizer should be updated to match SourcingX (lowercase everything).

## Shared Table: `profiles`

**Primary Key:** `linkedin_url` (TEXT, UNIQUE)
**Conflict resolution:** `ON CONFLICT (linkedin_url) DO UPDATE` (merge-duplicates)

### Full Column List

| Column | Type | Written by Autopilot | Written by SourcingX | Notes |
|--------|------|---------------------|---------------------|-------|
| `id` | UUID PK | NO | YES (auto) | SourcingX schema has this |
| `linkedin_url` | TEXT UNIQUE | YES | YES | **Canonical key** — must match normalizer |
| `raw_data` | JSONB | YES | YES | Full Crustdata response. **Last write wins.** |
| `name` | TEXT | NO | YES | SourcingX extracts from raw_data |
| `location` | TEXT | NO | YES | SourcingX extracts from raw_data |
| `current_title` | TEXT | YES | YES | Both extract from raw_data |
| `current_company` | TEXT | YES | YES | Both extract from raw_data |
| `all_employers` | TEXT[] | YES | YES | GIN indexed |
| `all_titles` | TEXT[] | YES | YES | GIN indexed |
| `all_schools` | TEXT[] | YES | YES | GIN indexed |
| `skills` | TEXT[] | YES | YES | GIN indexed |
| `screening_score` | INTEGER | YES | YES | **CONFLICT RISK** — different screening per position |
| `screening_fit_level` | TEXT | YES | YES | **CONFLICT RISK** |
| `screening_summary` | TEXT | YES | YES | **CONFLICT RISK** |
| `screening_reasoning` | TEXT | YES | YES | **CONFLICT RISK** |
| `email` | TEXT | NO | YES | SourcingX stores email here |
| `email_source` | TEXT | NO | YES | 'salesql', 'crustdata', 'manual' |
| `original_url` | TEXT | YES | YES | Last input URL |
| `original_urls` | TEXT[] | NO | YES | Multi-source tracking (SourcingX only) |
| `status` | TEXT | NO | YES | 'enriched'/'screened'/'contacted'/'archived' |
| `enrichment_status` | TEXT | YES | YES | 'enriched' or 'not_found' |
| `enrichment_attempted_at` | TIMESTAMPTZ | NO | YES | SourcingX tracks this |
| `enriched_at` | TIMESTAMPTZ | YES | YES | Both set on enrichment |
| `screened_at` | TIMESTAMPTZ | YES | YES | Both set on screening |
| `contacted_at` | TIMESTAMPTZ | NO | YES | SourcingX only |
| `created_at` | TIMESTAMPTZ | NO | YES | Auto by SourcingX |
| `updated_at` | TIMESTAMPTZ | NO | YES | Auto by SourcingX |

### What Autopilot Writes to `profiles`

**On enrichment** (`save_enriched_profile`):
```python
{
    'linkedin_url': normalized_url,
    'original_url': original_input_url,
    'raw_data': crustdata_response,       # FULL OVERWRITE
    'current_title': extracted,
    'current_company': extracted,
    'all_employers': [...],
    'all_titles': [...],
    'all_schools': [...],
    'skills': [...],
    'enrichment_status': 'enriched',
    'enriched_at': now(),
}
```

**On screening** (`update_profile_screening`):
```python
{
    'linkedin_url': normalized_url,
    'screening_score': 7,
    'screening_fit_level': 'Good Fit',
    'screening_summary': '...',
    'screening_reasoning': '...',
    'screened_at': now(),
    'enrichment_status': 'screened',
}
```

### What Autopilot Does NOT Write (safe from overwrite)
- `name` — won't be cleared if SourcingX set it
- `email`, `email_source` — autopilot stores emails in `pipeline_candidates.personal_email` instead
- `original_urls` array — autopilot doesn't touch it
- `status` — autopilot doesn't write this field
- `contacted_at` — autopilot doesn't write this

### Screening Conflict Risk

**Problem:** Both projects screen profiles against DIFFERENT job descriptions. Profile A might be "Strong Fit (9/10)" for a DevOps TL role (autopilot) but "Not a Fit (3/10)" for a Frontend Lead role (SourcingX). The `screening_*` fields on `profiles` table hold only ONE screening result.

**Current behavior:** Last screening wins. If SourcingX re-screens a profile that autopilot already screened, the autopilot score is overwritten.

**Mitigation:** Autopilot stores its own screening results in `pipeline_candidates` table (per-position). The `profiles.screening_*` fields are a convenience copy, not the source of truth for autopilot. But SourcingX reads `profiles.screening_*` directly.

**Future fix:** Consider adding `screened_for_position` or moving to a separate `screening_results` table with `(linkedin_url, position_id)` composite key.

## Autopilot-Only Tables

These tables are NOT used by SourcingX:

### `pipeline_candidates`
**PK:** Composite `(position_id, linkedin_url)`
- Per-position candidate tracking
- Stores screening results, email, GEM push status per position
- Safe from cross-project conflicts

### `pipeline_positions`
**PK:** `position_id`
- Position config (JD, search filters, hm_notes)
- Only autopilot reads/writes

### `pipeline_runs`
**PK:** `id` (auto)
- Run audit log
- Only autopilot reads/writes

## SourcingX-Only Tables

### `api_usage_logs`
- Tracks API costs (Crustdata, OpenAI, SalesQL credits)
- Autopilot does not write here (but could/should for cost tracking)

### `schema_migrations`
- Tracks applied SQL migrations
- SourcingX manages schema evolution

## Enrichment Cache — Shared Behavior

Both projects check if a profile was recently enriched before re-enriching:
- **Autopilot:** `ENRICHMENT_REFRESH_MONTHS = 3` — skips if enriched within 3 months
- **SourcingX:** Similar logic via `enrichment_attempted_at` timestamp

This means: if SourcingX enriches a profile today, autopilot won't re-enrich it for 3 months (and vice versa). This is GOOD — saves Crustdata credits.

## Safe Operations Checklist

### Safe (no cross-project conflict):
- Reading any table
- Writing to `pipeline_candidates`, `pipeline_positions`, `pipeline_runs`
- Enriching a profile that neither project has enriched recently
- Setting `email` on `pipeline_candidates.personal_email` (autopilot's own field)

### Caution (potential overwrite):
- Enriching a profile that SourcingX already enriched → `raw_data` gets refreshed (usually fine, newer data is better)
- Screening a profile → overwrites `profiles.screening_*` fields from any previous screening by either project

### Dangerous (avoid):
- Changing URL normalization without migrating existing data
- Writing `enrichment_status: 'screened'` when SourcingX expects `status: 'screened'` (different field names for similar concept)
- Deleting profiles from the table (affects both projects)

## Config

Both projects use the same Supabase credentials:
```json
{
    "supabase_url": "https://xxx.supabase.co",
    "supabase_key": "eyJ..."
}
```

Stored in `config.json` (gitignored) or environment variables `SUPABASE_URL` / `SUPABASE_KEY`.
