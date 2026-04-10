---
name: supabase-shared-db
description: Shared Supabase database reference. Use when reading/writing profiles, debugging enrichment dedup, or checking cross-project compatibility. Critical for avoiding data overwrites between daily-sourcing-autopilot and SourcingX.
argument-hint: [table-name-or-question]
---

# Shared Supabase Database Reference

## CRITICAL: This DB is Shared Across Projects

The same Supabase instance is used by multiple projects:
- **daily-sourcing-autopilot-e2e** (this project) -- automated pipeline
- **SourcingX** -- interactive sourcing app (Streamlit)

Both projects read and write to the **same `profiles` table**. Upserts use `linkedin_url` as the conflict key with `merge-duplicates`. Last write wins per field. This means:

**If both projects enrich the same LinkedIn profile, the last upsert overwrites shared fields.**

## URL Normalization -- ALIGNED (both projects)

Both projects now use identical normalization logic:
- **Normal slugs** (`/in/john-doe`): lowercase everything
- **Obfuscated slugs** (`/in/ACoAAAbCdEf`): preserve slug case, lowercase domain only

Obfuscated LinkedIn URLs (ACoAAA... pattern) are case-sensitive internal IDs. Lowercasing them breaks Crustdata enrichment lookups. Both normalizers now detect the `ACo` prefix and preserve case.

**Result:** `https://www.linkedin.com/in/ACoAAAbCdEf` → `https://www.linkedin.com/in/ACoAAAbCdEf` (case preserved)

## Shared Table: `profiles`

**Primary Key:** `linkedin_url` (TEXT, UNIQUE)
**Conflict resolution:** `ON CONFLICT (linkedin_url) DO UPDATE` (merge-duplicates)

### Full Column List

| Column | Type | Written by Autopilot | Written by SourcingX | Notes |
|--------|------|---------------------|---------------------|-------|
| `id` | UUID PK | NO | YES (auto) | SourcingX schema has this |
| `linkedin_url` | TEXT UNIQUE | YES | YES | **Canonical key** -- must match normalizer |
| `raw_data` | JSONB | YES | YES | Full Crustdata response. **Last write wins.** |
| `name` | TEXT | NO | YES | SourcingX extracts from raw_data |
| `location` | TEXT | NO | YES | SourcingX extracts from raw_data |
| `current_title` | TEXT | YES | YES | Both extract from raw_data |
| `current_company` | TEXT | YES | YES | Both extract from raw_data |
| `all_employers` | TEXT[] | YES | YES | GIN indexed |
| `all_titles` | TEXT[] | YES | YES | GIN indexed |
| `all_schools` | TEXT[] | YES | YES | GIN indexed |
| `skills` | TEXT[] | YES | YES | GIN indexed |
| `screening_score` | INTEGER | NO (use screening_results) | NO (use screening_results) | Moved to screening_results table |
| `screening_fit_level` | TEXT | NO | NO | Moved to screening_results table |
| `screening_summary` | TEXT | NO | NO | Moved to screening_results table |
| `screening_reasoning` | TEXT | NO | NO | Moved to screening_results table |
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
- `name` -- won't be cleared if SourcingX set it
- `email`, `email_source` -- autopilot stores emails in `pipeline_candidates.personal_email` instead
- `original_urls` array -- autopilot doesn't touch it
- `status` -- autopilot doesn't write this field
- `contacted_at` -- autopilot doesn't write this

### Screening Results -- Isolated via `screening_results` Table

**RESOLVED:** Screening conflicts are now handled by a dedicated `screening_results` table.

Both projects write to `screening_results` instead of `profiles.screening_*`. Each screening is keyed by `(linkedin_url, jd_hash, source_project)`, so the same profile can be screened for different JDs without overwriting.

A `latest_screening` view provides the most recent screening per profile for backward compatibility.

See "Shared Table: screening_results" section below for full schema.

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

## Enrichment Cache -- Shared Behavior

Both projects check if a profile was recently enriched before re-enriching:
- **Autopilot:** `ENRICHMENT_REFRESH_MONTHS = 3` -- skips if enriched within 3 months
- **SourcingX:** Similar logic via `enrichment_attempted_at` timestamp

This means: if SourcingX enriches a profile today, autopilot won't re-enrich it for 3 months (and vice versa). This is GOOD -- saves Crustdata credits.

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

## Shared Table: `screening_results`

**Primary Key:** `id` (UUID, auto-generated)
**Dedup Key:** UNIQUE `(linkedin_url, jd_hash, source_project)`

| Column | Type | Purpose |
|--------|------|---------|
| `linkedin_url` | TEXT NOT NULL | Who was screened |
| `source_project` | TEXT NOT NULL | 'autopilot' or 'sourcingx' |
| `position_id` | TEXT | FK to pipeline_positions (autopilot only, NULL for SourcingX) |
| `jd_hash` | TEXT NOT NULL | SHA256 of first 500 chars of JD -- dedup key |
| `jd_title` | TEXT | Human-readable JD title |
| `screening_score` | INTEGER | 1-10 |
| `screening_fit_level` | TEXT | 'Strong Fit', 'Good Fit', 'Partial Fit', 'Not a Fit' |
| `screening_result` | TEXT | 'qualified', 'not_qualified' (autopilot convention) |
| `screening_summary` | TEXT | AI screening summary |
| `screening_reasoning` | TEXT | AI screening reasoning |
| `screening_notes` | TEXT | Autopilot screening notes |
| `email_opener` | TEXT | Personalized opener |
| `ai_model` | TEXT | Model used for screening |
| `screened_at` | TIMESTAMPTZ | When screening happened |

### Writing
```python
from core.db import insert_screening_result, compute_jd_hash
jd_hash = compute_jd_hash(jd_text)
insert_screening_result(client, url, 'autopilot', jd_hash, score=7, result='qualified', ...)
```

### Reading (latest per profile)
```python
# Use the latest_screening view
results = client.select('latest_screening', '*', {'screening_fit_level': 'eq.Strong Fit'})
```

### How jd_hash works
`compute_jd_hash(jd_text)` → `hashlib.sha256(jd_text[:500].encode()).hexdigest()`
Same JD always produces same hash. Different JDs produce different hashes. This allows multiple screenings of the same profile for different JDs.

## Config

Both projects use the same Supabase credentials:
```json
{
    "supabase_url": "https://xxx.supabase.co",
    "supabase_key": "eyJ..."
}
```

Stored in `config.json` (gitignored) or environment variables `SUPABASE_URL` / `SUPABASE_KEY`.
