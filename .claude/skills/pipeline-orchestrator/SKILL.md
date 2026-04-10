---
name: pipeline-orchestrator
description: Master guide for running the daily sourcing pipeline end-to-end. Use when executing the scheduled daily task or running the pipeline manually.
argument-hint: [position-id or --all]
---

# Daily Sourcing Pipeline Orchestrator

Run the complete sourcing pipeline for one or all active positions. This is what the scheduled task executes daily at 9 AM.

## Required Reading (BEFORE running the pipeline)

You MUST read these skills before starting:
1. **This file** — pipeline steps and flow
2. `.claude/skills/screening/SKILL.md` — screening rubric, scoring, opener rules
3. `.claude/skills/pipeline-outreach/SKILL.md` — email opener quality rules, NEVER list, variety angles

Do NOT generate openers without reading the outreach skill first. Bad openers waste candidates.

## Working Directory

```bash
cd "C:/Users/admin/Desktop/Claude Projects/daily-sourcing-autopilot-e2e"
```

## Full Pipeline

### Step 0: Talent Pool Search (for new positions)
```bash
python -m pipeline.talent_pool search <position_id>
```
Scans existing enriched profiles in Supabase for candidates matching this position's JD. Returns matches scored by title + skill overlap. Skip this step for positions that already have 50+ candidates.

If matches found, add them to the pipeline:
```bash
echo '["url1", "url2", ...]' | python -m pipeline.talent_pool add <position_id>
```
These candidates skip the search step (already in DB) and go straight to screening.

### Step 1: Init
```bash
python -m pipeline.db_helpers init <position_id>
```
Save `run_id`, `job_description`, `hm_notes`, `selling_points`.

### Step 2: Search (Crustdata MCP)
```bash
python -m pipeline.search_step get_config <position_id>
```
For each active search intent, build MCP filters and call `crustdata_people_search_db` with:
- `limit: 100`
- `format: "json"`
- `compact: true`
- `fields: "name,headline,linkedin_profile_url,region,current_employers.name,current_employers.title,current_employers.seniority_level,current_employers.company_headcount_range,education_background.institute_name"`

The `fields` param keeps response small enough for 100 results per call. Save with search_name tag. Stop at `daily_search_limit` (500).

See `/crustdata-mcp` skill for details.

### Step 3: Pre-filter (Google Sheets)
```bash
python -m pipeline.pre_filter_step <position_id>
```
Filters against Google Sheets: past candidates (name), blacklist (company), not-relevant companies.

### Step 3b: AI Pre-Screen (Claude thinks — saves enrich credits)
```bash
python -m pipeline.pre_filter_step get_for_review <position_id>
```
Returns ALL candidates with name, title, company, headline, education + the JD and hm_notes.

**YOU review each candidate** using ALL available data against the JD + hm_notes. Think carefully about each one:

**REJECT if any of these:**
- Wrong function entirely (sales, marketing, recruiter, product manager, customer success)
- Wrong level (intern, junior when hiring for TL/manager)
- Company is heavy legacy enterprise/telco that contradicts hm_notes (e.g., "must be product company DNA")
- Consulting/outsourcing company that's a dealbreaker in hm_notes
- Title that just happens to contain a keyword but is unrelated (e.g., "DevOps Recruiter", "VP DevOps Sales")
- Location mismatch if detectable from data

**KEEP if:**
- Title and company make sense for the role
- Even if borderline — keep for enrichment, the full screen will decide

Collect rejected LinkedIn URLs and remove:
```bash
echo '["url1", "url2", ...]' | python -m pipeline.pre_filter_step remove_irrelevant <position_id>
```

**This is a cost-saving step.** Every candidate removed here saves 3 Crustdata enrich credits. Be decisive but not overly aggressive — when in doubt, keep.

### Step 4: Enrich (Direct API — no MCP needed)

```bash
python -m pipeline.enrich_step enrich <position_id>
```

This runs enrichment entirely in Python via the Crustdata REST API. No MCP calls needed — much faster, no timeouts. It:
- Gets all unscreened candidate URLs
- Checks cache (skips recently enriched profiles)
- Enriches in batches of 25 via direct API
- Saves all profiles to the DB
- Respects daily cap (400/day)

Output: `{enriched, from_cache, saved, failed, remaining_cap}`

**Fallback:** If the direct API fails, you can still enrich via MCP:
```bash
python -m pipeline.enrich_step get_urls <position_id>
# Then call crustdata_people_enrich MCP tool with the URLs
echo '<JSON profiles>' | python -m pipeline.enrich_step save_profiles <position_id>
```

### Step 4b: Validate enrichment
```bash
python -m pipeline.controller validate enrich <position_id>
```
Checks all unscreened candidates have enriched profiles. Flags missing ones.

### Step 5: Screen

```bash
python -m pipeline.screen_step get_profiles <position_id>
```
Use `/screening` skill. Score, qualify, write notes + opener. Save each result.

Check progress:
```bash
python -m pipeline.screen_step summary <position_id>
```
If `qualified >= 50` → move to email step.
If more candidates need enriching → go back to Step 4.

### Step 5b: Validate screening
```bash
python -m pipeline.controller validate screen <position_id>
```
Checks: all qualified have openers, scores in range, notes present, enriched profiles exist.
**If issues found:** fix them before proceeding (e.g., generate missing openers, re-enrich missing profiles).

### Step 6: Update Qual Rates
```bash
python -m pipeline.search_step update_qual_rates <position_id>
```
Feedback loop — next day's search will prioritize better filters.

### Step 7: Email (SalesQL)
```bash
python -m pipeline.email_step <position_id>
```
Finds personal emails for qualified candidates. ~80-90% hit rate.

### Step 8: GEM Push
```bash
python -m pipeline.gem_step <position_id>
```
Pushes ALL qualified candidates to GEM (email optional). Updates ALL fields: name, title, company, location, email, nickname (opener), custom fields (score, reason).

### Step 8b: Validate GEM Push
```bash
python -m pipeline.controller validate gem_push <position_id>
```
Checks ALL qualified are pushed. **If any are missing, automatically re-runs GEM push.**

### Step 9: Finalize
```bash
python -m pipeline.finalize_step <position_id> <run_id> completed
```
Aggregates stats and updates `pipeline_runs`.

### Step 10: Slack (Detailed Report)
```bash
python -m pipeline.slack_step <position_id> <run_id>
```
Sends detailed Block Kit report with:
- Today's numbers (searched, qualified, rejected, by search variant)
- All-time totals (sourced, qualified, with email, pushed to GEM, pending)
- Qualification rates by search variant
- Data quality issues (missing openers, unpushed candidates)

### Step 11: Full Stats (optional — for debugging)
```bash
python -m pipeline.controller full_stats <position_id> <run_id>
```
Prints comprehensive JSON with all pipeline statistics.

Build stats JSON by aggregating results from all steps:
```json
{
  "searched_today": N, "total_candidates": N,
  "qualified": N, "not_qualified": N,
  "with_email": N, "pushed_to_gem": N,
  "search": {"found": N, "saved": N},
  "pre_filter": {"filtered_out": N},
  "enrich": {"enriched_new": N, "from_cache": N},
  "screen": {"qualified": N, "not_qualified": N},
  "email": {"found": N, "looked_up": N},
  "gem": {"pushed": N, "duplicates": N}
}
```

## Error Handling

## CRITICAL: Never Skip Steps

**NEVER skip a pipeline step because of a missing config or credentials error.** If a step fails due to missing configuration (Google credentials, API keys, config.json), that is a BLOCKING error — stop the pipeline and report the issue. Do not silently continue with unfiltered/unenriched/unscreened candidates.

The only steps that can be skipped on failure are Finalize and Slack (reporting steps). All data-processing steps (Search, Pre-filter, Enrich, Screen, Email, GEM push) are mandatory.

| Step | If it fails... |
|------|---------------|
| Search | **Stop** — no candidates to process |
| Pre-filter | **Stop** — unfiltered candidates waste enrich credits |
| Enrich | **Stop** — can't screen without enriched profiles |
| Screen | **Stop + Retry** — if Claude fails, retry once. No new qualified without screening. |
| Email | Log, continue — candidates just won't have email for GEM |
| GEM | Log, continue — candidates stay in DB for next run |
| Finalize | Non-fatal — stats won't be recorded but pipeline still worked |
| Slack | Non-fatal — no notification but pipeline completed |

## URL Handling

Crustdata search returns **obfuscated URLs** (ACoAAA...) which are case-sensitive. The normalizer preserves case for these. During enrichment, MCP returns **flagship URLs** (/in/real-name) which replace the obfuscated ones in pipeline_candidates.

## Running for All Positions

For automated daily runs, loop through active positions:
```bash
python -m pipeline.db_helpers init <pos1>
# ... run all steps for pos1 ...
python -m pipeline.db_helpers init <pos2>
# ... run all steps for pos2 ...
```

Or use `python run_pipeline.py --all` for the mechanical steps (excludes screening).
