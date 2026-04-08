---
name: pipeline-orchestrator
description: Master guide for running the daily sourcing pipeline end-to-end. Use when executing the scheduled daily task or running the pipeline manually.
argument-hint: [position-id or --all]
---

# Daily Sourcing Pipeline Orchestrator

Run the complete sourcing pipeline for one or all active positions. This is what the scheduled task executes daily at 9 AM.

## Working Directory

```bash
cd "C:/Users/gehta/OneDrive/Desktop/Claude Code Projects/daily-sourcing-autopilot-e2e"
```

## Full Pipeline (9 Steps)

### Step 1: Init
```bash
python -m pipeline.db_helpers init <position_id>
```
**Output:** JSON with `run_id`, `job_description`, `search_filters`, `hm_notes`, `selling_points`, `gem_project_id`, `sheet_url`

Save `run_id` — needed for finalize step.

### Step 2: Search (Crustdata MCP)
```bash
python -m pipeline.search_step get_config <position_id>
```
Returns `searches` array (tiered filters) + `exclude_urls` + `target_qualified`.

**For each search round** (stop when you have enough candidates):
1. Call `crustdata_people_search_db` MCP with the round's filters, `limit: 100`, `format: "json"`, `compact: true`
2. Save results:
```bash
echo '<JSON profiles array>' | python -m pipeline.search_step save_candidates <position_id>
```
3. Math: need ~(target_qualified / 0.6) total candidates. For 50 qualified, aim for ~85 new.

**Credits:** 3 per search call (100 results max). Run only as many rounds as needed.

### Step 3: Pre-filter
```bash
python -m pipeline.pre_filter_step <position_id>
```
Filters against Google Sheets (past candidates, blacklist, not-relevant companies). Auto-deletes filtered candidates from DB.

### Step 4: Enrich (Crustdata MCP)
```bash
python -m pipeline.enrich_step get_urls <position_id>
```
Returns `urls_to_enrich` array.

Enrich via MCP in batches of **up to 25** (comma-separated):
```
crustdata_people_enrich(linkedin_profile_url="url1,url2,...url25")
```

Save each batch:
```bash
echo '<JSON profiles array>' | python -m pipeline.enrich_step save_profiles <position_id>
```

**Important:** MCP enrich returns `linkedin_flagship_url` (clean URL). The save step automatically updates `pipeline_candidates` with the clean URL.

### Step 5: Screen (Claude does this)
```bash
python -m pipeline.screen_step get_profiles <position_id>
```
Returns array of `{linkedin_url, name, profile_text}`.

**Use the `/screening` skill** for evaluation guidance. For each profile:
- Read profile_text against the job_description + hm_notes
- Score 1-10, result: qualified (6+) / not_qualified (<6)
- Write notes (2-3 sentences) and email opener (1-2 sentences using selling_points)

Save each:
```bash
echo '{"score":7,"result":"qualified","notes":"...","opener":"..."}' | python -m pipeline.screen_step save_result <position_id> <linkedin_url>
```

### Step 6: Email (SalesQL)
```bash
python -m pipeline.email_step <position_id>
```
Auto-finds personal emails for qualified candidates. ~80-90% hit rate.

### Step 7: GEM Push
```bash
python -m pipeline.gem_step <position_id>
```
Pushes qualified candidates with email to GEM. Updates ALL fields: name, title, company, location, email, custom fields (email opener, score, reason).

### Step 8: Finalize
```bash
python -m pipeline.finalize_step <position_id> <run_id> completed
```
Aggregates stats and updates `pipeline_runs`.

### Step 9: Slack
```bash
echo '<stats JSON>' | python -m pipeline.slack_step <position_id>
```
Sends Block Kit summary to `#terminal-sourcin-agent`.

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

| Step | If it fails... |
|------|---------------|
| Search | Log error, continue — may have candidates from previous runs |
| Pre-filter | Log, continue — candidates stay unfiltered |
| Enrich | Log, continue — unenriched candidates skip screening |
| Screen | **Critical** — if Claude fails, no new qualified candidates. Retry. |
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
