# daily-sourcing-autopilot-e2e

Automated daily sourcing pipeline. Pushes qualified candidates into **GEM** (the
ATS/CRM) for email outreach. A sibling project, `smartlead-sourcing-autopilot`, is
a fork of this one that pushes to SmartLead instead.

## Pipeline

Search (Crustdata) → Pre-filter (Google Sheets) → AI Pre-screen → Enrich (Crustdata)
→ Screen (Claude) → Email lookup (SalesQL) → **GEM push** → Finalize → Slack.

`run_pipeline.py` runs the mechanical steps; the Claude-driven scheduled task does
screening. See `.claude/skills/pipeline-orchestrator` for the full flow.

## Shared Supabase database — write/read consistency (CRITICAL)

This project shares ONE Supabase database with two siblings:
- **SourcingX** — the interactive sourcing app. **Canonical reference for all DB access.**
- **smartlead-sourcing-autopilot** — sibling automated pipeline (pushes to SmartLead).

**The rule:** all three projects MUST write profiles to the `profiles` table the
exact same way, and search/read them the exact same way. If they drift apart, one
project silently corrupts or misreads another's data.

- The profile-saving code here — `core/db.py` (`save_enriched_profile`,
  `_prepare_profile_row`, `_bulk_fetch_existing_original_urls`,
  `save_enriched_profiles_bulk`, `SupabaseClient.upsert` / `upsert_batch`) and
  `core/normalizers.py` (`normalize_linkedin_url`, `pick_current_employer`,
  `_parse_start_date_sort_key`) — is a **direct port of SourcingX's** and must stay
  behaviourally byte-for-byte identical.
- **Before changing any database read or write code, check SourcingX and
  smartlead-sourcing-autopilot.** Mirror changes across all three. SourcingX wins ties.
- This covers: which columns are written, how each field is extracted from the
  Crustdata response, timestamp format, URL normalization, the `original_urls` array
  handling, and search/filter query patterns. Do not invent a new way to write a profile.

### position_id namespacing

`pipeline_candidates` has composite PK `(position_id, linkedin_url)` and is shared
with `smartlead-sourcing-autopilot`. Position IDs must stay distinct between the two
pipelines, or they will share candidate rows and push-status flags.
See `.claude/skills/supabase-shared-db`.

## GEM push

- `integrations/gem.py` — `GemClient` (GEM API v0).
- `pipeline/gem_step.py` — pushes qualified candidates to a GEM project.
- `config.json` needs `gem_api_key`; project IDs live on
  `pipeline_positions.gem_project_id`.
- DB columns this project uses: `gem_pushed`, `gem_pushed_at` on `pipeline_candidates`.

## Conventions

- Secrets live in `config.json` and `google_credentials.json` — both gitignored. Never commit them.
