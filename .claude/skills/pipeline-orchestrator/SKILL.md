---
name: pipeline-orchestrator
description: Master guide for running the daily sourcing pipeline end-to-end. Use when executing the scheduled daily task or running the pipeline manually.
argument-hint: [position-id or --all]
---

# Daily Sourcing Pipeline Orchestrator

Run the complete sourcing pipeline for one or all active positions. This is what the scheduled task executes daily at 9 AM.

## Two Orchestrators -- DO NOT mix them

1. **This skill** (Claude-driven): Claude reads this, executes each step, does AI screening. Used by the daily scheduled task.
2. **run_pipeline.py** (Python-only): Runs mechanical steps only (search, pre-filter, email, gem, finalize, slack). Does NOT screen. Legacy -- do not use while the scheduled task is active.

**Rule: Never run `python run_pipeline.py` while the scheduled task is running or vice versa.** They will create duplicate candidates and waste credits. Use only ONE orchestrator at a time.

## Step Types

| Step | Executed by | AI needed? |
|------|------------|-----------|
| Preflight | Python | No |
| Talent pool search | Python | No |
| Init | Python | No |
| Search | Claude (MCP) | Yes -- builds filters from intent |
| Pre-filter (Sheets) | Python | No |
| AI Pre-screen | Claude | Yes -- reviews candidates against JD |
| Enrich | Python (direct API) | No |
| Screen | Claude | Yes -- scores, qualifies, writes openers |
| Email (SalesQL) | Python | No |
| GEM push | Python | No |
| Finalize | Python | No |
| Slack report | Python | No |

## Required Reading (BEFORE running the pipeline)

You MUST read these skills before starting:
1. **This file** -- pipeline steps and flow
2. `.claude/skills/screening/SKILL.md` -- screening rubric, scoring, opener rules
3. `.claude/skills/pipeline-outreach/SKILL.md` -- email opener quality rules, NEVER list, variety angles
4. **Position-specific skill** (MANDATORY): `.claude/skills/screening-<position-id>/SKILL.md`
   - This file MUST exist for every position. It is generated during position setup from a calibration review with the hiring manager.
   - **If this file does NOT exist, do NOT run the screening step.** Send a Slack error alert and STOP: `python -m pipeline.slack_step error <position_id> "Screening" "No position-specific screening skill found. Run calibration review first."`
   - If it exists, read it BEFORE screening any candidates
   - Position-specific rules take PRECEDENCE over the general screening skill when they conflict
   - The general screening skill is role-agnostic — it provides the framework. The position-specific skill provides the actual rules for what qualifies a candidate for THIS role.

Do NOT generate openers without reading the outreach skill first. Bad openers waste candidates.

## Working Directory

```bash
cd "C:/Users/admin/Projects Built with Claude/daily-sourcing-autopilot-e2e"
```

## Full Pipeline

### Step 0a: Slack Start Notification (run FIRST)
```bash
python -m pipeline.slack_step start <position_id>
```
Sends a ":rocket: Pipeline started" notification to Slack so the team knows a run is in progress.

### Pre-flight Check
```bash
python -m pipeline.db_helpers preflight
```
Verifies all integrations: Supabase, Crustdata API, GEM API, Google Sheets credentials.
**If any check fails, STOP and report the error. Do NOT continue with a broken integration.**

### Step -1: GEM Warm Leads (highest priority source)

```bash
python -m pipeline.warm_leads_step search <position_id>
```

Pulls candidates from the GEM warm leads project (people who replied YES to outreach for other positions). These are warm leads with high response likelihood. This is a GLOBAL project shared across all positions.

If found, add ALL to pipeline:
```bash
python -m pipeline.warm_leads_step search <position_id> 2>/dev/null | python -c "
import json, sys
data = json.load(sys.stdin)
urls = [c['linkedin_url'] for c in data['candidates']]
print(json.dumps(urls))
" | python -m pipeline.warm_leads_step add <position_id>
```

These candidates are NOT enriched. They go through the FULL pipeline: pre-filter, AI pre-screen, enrich, screen, final review, GEM push.

Skip if: no `gem_warm_leads_project_id` in config, or position has `skip_warm_leads: true` in search_filters.
If GEM API fails: log warning, continue (non-blocking).

### Step 0: Talent Pool Search — ALWAYS RUN FIRST

```bash
python -m pipeline.talent_pool search <position_id>
```

Scans ALL enriched profiles in Supabase (22K+) for candidates matching this position's JD. Returns matches scored by title + skill overlap. These profiles are **FREE** (already enriched, no Crustdata credits) and go straight to screening.

**ALWAYS run this step on every run**, not just for new positions. The talent pool grows as other positions enrich new profiles — there may be new matches since last run.

If matches found, add ALL of them to the pipeline:
```bash
python -m pipeline.talent_pool search <position_id> 2>/dev/null | python -c "
import json, sys
data = json.load(sys.stdin)
urls = [m['linkedin_url'] for m in data['matches']]
print(json.dumps(urls))
" | python -m pipeline.talent_pool add <position_id>
```

**Drain the internal DB fully.** These candidates are already enriched — they skip search AND enrichment, going straight to screening. This is the cheapest and fastest source of candidates. The pre-filter and screening steps will handle quality.

### Step 0b: Pre-filter ALL candidates (MANDATORY — runs EVERY time)
```bash
python -m pipeline.pre_filter_step <position_id>
```

**This ALWAYS runs after talent pool add, BEFORE checking backlog.** It filters talent pool candidates AND any previously unfiltered candidates against Google Sheet exclusion lists (past candidates, blacklist, not-relevant companies). Without this, talent pool candidates who were already contacted or from blacklisted companies would enter the pipeline.

**If this step fails → send Slack error + STOP.**

### Step 0c: AI Pre-screen ALL candidates (MANDATORY — runs EVERY time)

```bash
python -m pipeline.pre_filter_step get_for_review <position_id>
```

**This ALWAYS runs after Google Sheet filtering, BEFORE full screening.** It's a fast, cheap review of title + company + headline to reject obvious mismatches. This is critical for talent pool candidates — the keyword matching is broad and returns many irrelevant profiles (e.g., Marketing Directors at law firms, hospitality groups, defense companies).

**Review each candidate and REJECT if:**
- Wrong function entirely (not marketing for a marketing role, not engineering for an eng role)
- Wrong seniority (Manager/Director when hiring VP, or CMO at Fortune 500 when hiring startup VP)
- Wrong company type (non-tech for a tech role, government, banks, traditional industries per hm_notes)
- Sub-function specialist when the JD requires full-function ownership (e.g., "VP Brand Marketing" for a "VP Marketing" role)
- Wrong location (not in the required city/country)
- Title contains a keyword but role is unrelated (e.g., "VP Marketing Solutions" = sales, not marketing)

**This step is a MAJOR time saver.** A talent pool of 500 candidates can be reduced to 50-100 relevant ones in minutes, saving hours of full-profile screening on obviously wrong candidates.

```bash
echo '["url1", "url2", ...]' | python -m pipeline.pre_filter_step remove_irrelevant <position_id>
```

### Step 0d: Check Backlog (decides whether to search externally)
```bash
python -m pipeline.screen_step summary <position_id>
```

**Read the `pending` count from the summary output.** This now includes talent pool candidates (minus filtered AND pre-screened ones).

- **If `pending >= 100`:** SKIP external search (Steps 1-3b). Go straight to Step 4 (Enrich) then Step 5 (Screen). There are already enough unscreened candidates.
- **If `pending < 100`:** Run search (Steps 1-3b) to refill the pool, then enrich and screen.

**Why:** Searching adds 200-500 new candidates per run. If the screening loop can only process ~400 per run, and the search keeps adding more, the backlog grows forever. Drain first, refill later.

### Step 1: Init
```bash
python -m pipeline.db_helpers init <position_id>
```
Save `run_id`, `job_description`, `hm_notes`, `selling_points`.

### Step 2: Search (Crustdata MCP)
```bash
python -m pipeline.search_step get_config <position_id>
```
The config now includes `target_companies` -- a list of priority companies from the Google Sheet.

**Search order (STRICT -- follow this priority):**

1. **Client wanted companies** (if present) -- the client's OWN target list. Highest priority. These are specific companies the client wants to poach from. Search these FIRST.
2. **Tech alerts / layoffs** (if present) -- companies with recent layoffs. Candidates may be actively looking. High response rate.
3. **Target companies** (if present) -- pre-vetted product companies from the master list. Good quality but broader.
4. **Target universities** (if present) -- graduates from top schools. Use as a supplementary filter.
5. **Regular search intents** -- the tiered filters built from the JD. Run these LAST, after all priority lists.

**Priority lists** (check config output for each -- skip if empty):

| Priority | Config key | What it is | How to use in search |
|---|---|---|---|
| 1 (highest) | `client_wanted_companies` | Client's specific target list | `CURRENT_COMPANY` filter -- poaching from specific companies |
| 2 | `tech_alerts` | Companies with recent layoffs | `CURRENT_COMPANY` or `PAST_COMPANY` filter -- candidates who may be looking |
| 3 | `target_companies` | Pre-vetted product companies | `CURRENT_COMPANY` filter -- candidates working at these companies |
| 4 | `target_universities` | Top CS/engineering schools | `SCHOOL` filter -- graduates from these universities |

For each priority list, combine with role-specific title keywords from the JD + location. Split company lists into batches of 20-25 (Crustdata filter limit). Example:
```json
{
  "op": "and",
  "conditions": [
    {"column": "current_employers.name", "type": "in", "value": ["Wiz", "Snyk", "Monday.com", ...]},
    {"column": "current_employers.title", "type": "[.]", "value": "devops"},
    {"column": "region", "type": "[.]", "value": "Israel"}
  ]
}
```

Save each priority search with its source name: `search_name: "target_companies"`, `"tech_alerts"`, etc.

For each search, call `crustdata_people_search_db` with:
- `limit: 100`
- `format: "json"`
- `compact: true`
- `fields: "name,headline,linkedin_profile_url,region,current_employers.name,current_employers.title,current_employers.seniority_level,current_employers.company_headcount_range,education_background.institute_name"`

**CRITICAL: Exclude already-sourced URLs from search results.**
The config returns `exclude_urls` -- a list of ALL LinkedIn URLs already in the pipeline for this position. Pass them as the `exclude_profiles` parameter on EVERY `crustdata_people_search_db` call:

```
crustdata_people_search_db(
  filters = { ... your title/location/seniority filters ... },
  exclude_profiles = ["https://www.linkedin.com/in/john-doe", "https://www.linkedin.com/in/jane-smith", ...],
  limit = 100,
  format = "json",
  compact = true
)
```

**`exclude_profiles` is a top-level parameter, NOT a filter condition.** Do NOT put it inside `filters.conditions`. It is a post-processing option that Crustdata applies server-side after the query runs.

- Maximum: 50,000 URLs per request (10MB payload limit)
- URLs must be in format `https://www.linkedin.com/in/{slug}`
- For best performance, keep under 10,000 URLs

**Why:** Without this, Crustdata returns candidates we already have, wasting search result slots. With 600+ existing candidates, a 100-result search could return 80+ duplicates.

Stop at `daily_search_limit` (500).

See `/crustdata-mcp` skill for details.

### Step 3: Pre-filter (Google Sheets) — MANDATORY, NEVER SKIP

```bash
python -m pipeline.pre_filter_step <position_id>
```

**THIS STEP IS MANDATORY.** It filters candidates against three Google Sheet exclusion lists:
1. **Past Candidates** — people already contacted in previous runs (matched by name)
2. **Blacklist** — companies we must never source from (matched by company name)
3. **Not Relevant Companies** — companies that don't fit the role (e.g., consulting, defense, legacy)

**If this step fails (Google credentials error, sheet not found, etc.) → STOP THE PIPELINE.**
Do NOT continue to enrichment or screening without filtering. Reasons:
- Without past-candidate filtering, you will re-source people already in GEM → wasted outreach, damages employer brand
- Without blacklist filtering, you will enrich and screen dealbreaker companies → wastes Crustdata credits
- Without not-relevant filtering, you will screen candidates the HM will instantly reject → wastes screening time

**Expected output:** JSON with `{"filtered_out": N, "remaining": N, "by_reason": {...}}`
If `filtered_out == 0` and you have 100+ candidates, something may be wrong — verify the Google Sheet is accessible.

### Step 3b: AI Pre-Screen (Claude thinks -- saves enrich credits)
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
- Even if borderline -- keep for enrichment, the full screen will decide

Collect rejected LinkedIn URLs and remove:
```bash
echo '["url1", "url2", ...]' | python -m pipeline.pre_filter_step remove_irrelevant <position_id>
```

**This is a cost-saving step.** Every candidate removed here saves 3 Crustdata enrich credits. Be decisive but not overly aggressive -- when in doubt, keep.

### Step 4: Enrich (only for candidates that need it)

**Talent pool candidates are ALREADY enriched** — they came from the profiles table. The enrich step automatically skips them (cache hit). Only candidates from external Crustdata search need enrichment.

If ALL candidates came from the talent pool (no external search was run), you can **skip this step entirely** — go straight to Step 5 (Screen).

If external search WAS run, enrich the new candidates:
```bash
python -m pipeline.enrich_step enrich <position_id>
```

This runs enrichment entirely in Python via the Crustdata REST API. No MCP calls needed -- much faster, no timeouts. It:
- Gets all unscreened candidate URLs
- Checks cache (skips recently enriched profiles — including talent pool candidates)
- Enriches in batches of 25 via direct API
- Saves all profiles to the DB
- Respects daily cap (400/day per position)

Output: `{enriched, from_cache, saved, failed, remaining_cap}`

**Fallback:** If the direct API fails, you can still enrich via MCP:
```bash
python -m pipeline.enrich_step get_urls <position_id>
# Then call crustdata_people_enrich MCP tool with the URLs
echo '<JSON profiles>' | python -m pipeline.enrich_step save_profiles <position_id>
```

### Step 4b: Validate enrichment (skip if no external search)
```bash
python -m pipeline.controller validate enrich <position_id>
```
Checks all unscreened candidates have enriched profiles. Flags missing ones.

### Step 5: Screen (LOOP until target met)

**This step MUST loop. Do NOT proceed to email/GEM until enough candidates are qualified.**

**Target: 40+ NEW qualified candidates per run.** If the run produces fewer than 40 qualified, keep screening.

**Loop logic:**
```
Before starting the loop:
  - Run: python -m pipeline.screen_step summary <position_id>
  - Record the current `qualified` count as BASELINE_QUALIFIED
  - Set MY_QUALIFIED = 0

REPEAT:
  1. Get next batch: python -m pipeline.screen_step get_profiles <position_id>
     (returns up to 50 unscreened profiles per call)
  2. If empty array returned → all candidates screened, exit loop
  3. Screen each profile using the screening skill. Save each result immediately.
     Count how many YOU qualified in this batch → add to MY_QUALIFIED
  4. If MY_QUALIFIED >= 40 → exit loop, proceed to email
  5. Check: python -m pipeline.screen_step summary <position_id>
     Read `pending` from output.
  6. If pending == 0 → exit loop (nothing left to screen)
  7. Otherwise → go back to step 1 (get next batch)
```

**CRITICAL: Track YOUR OWN qualified count (MY_QUALIFIED), do NOT use `today_qualified` from summary.**
`today_qualified` includes candidates screened by humans or previous runs today. If someone manually screened 50 candidates before your run, `today_qualified` would be 50 before you screen anyone — and you'd incorrectly skip screening.

**MY_QUALIFIED** is the count of candidates YOU qualified in THIS run. Start at 0, increment each time you save a "qualified" result. Only exit the loop when MY_QUALIFIED >= 40 or pending == 0 or get_profiles returns [].

`pending` from summary excludes candidates with failed enrichment (they can't be screened).

**CRITICAL: Do NOT stop after one batch.** The screening step returns 50 profiles at a time. At typical 10-20% qualification rates, you need to screen 200-400 profiles to get 40 qualified. This means 4-8 loops through get_profiles.

If all candidates are screened and qualified < 40, that's OK -- it means the search didn't find enough. Log it and proceed. But do NOT stop early when there are still unscreened candidates in the pipeline.

**If more candidates need enriching** (get_profiles returns profiles without enriched data) → go back to Step 4 to enrich, then resume screening.

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
Feedback loop -- next day's search will prioritize better filters.

### Step 7: Email (SalesQL)
```bash
python -m pipeline.email_step <position_id>
```
Finds personal emails for qualified candidates. ~80-90% hit rate.

### Step 7b: Final Review (MANDATORY quality gate before GEM push)

**This is the last line of defense. Every candidate that reaches GEM must pass this review.**

Before pushing to GEM, re-read EVERY qualified candidate's full enriched profile and verify they truly match the role. This catches screening drift, mistakes, and edge cases that slipped through.

**How to run:**
```bash
python -m pipeline.screen_step get_qualified <position_id>
```
This returns all qualified candidates with their full enriched profiles and screening results.

For EACH qualified candidate, verify ALL of the following:

1. **Role fit check:** Does this person actually match the role?
   - Apply the position-specific screening skill rules (`.claude/skills/screening-<position-id>/SKILL.md`) — these are calibrated from hiring manager feedback and take PRECEDENCE over generic rules.
   - If no position-specific skill exists, use the general screening skill rules.

2. **Must-have check:** Re-verify each must-have from hm_notes against ACTUAL profile data (skills list, work history). Do NOT assume skills from company name.

3. **Dealbreaker check:** Re-check company history against dealbreakers (consulting, banks, defense, legacy). Check ALL employers, not just current.

4. **Seniority check:** Confirm they're not overkill (VP/Director managing managers for an IC/TL role) and not too junior.

5. **Notes consistency:** Read the screening_notes. If the notes flag serious concerns ("no React verified", "pure backend", "consulting background") but the result is "qualified", that's a contradiction -- **FAIL**.

6. **Opener quality:** Confirm the opener is under 250 chars, specific to this person, and follows the outreach skill rules.

**If a candidate FAILS any check:**
```bash
echo '{"score": 5, "result": "not_qualified", "notes": "FINAL REVIEW REJECT: <reason>"}' | python -m pipeline.screen_step save_result <position_id> <linkedin_url>
```

**Log format:**
```
[final_review] PASS: Lior Rabinovich (7/10) -- Senior Full Stack Developer at VERITI, all must-haves verified
[final_review] FAIL: Yael Fisher (7/10) -- Senior Frontend Engineer at Gong, pure frontend title, downgrading
[final_review] Summary: 37 reviewed, 35 passed, 2 rejected
```

**This step is NOT optional.** Do not skip it. Do not batch-approve. Read each profile individually. A single bad candidate in GEM wastes an outreach slot and damages employer brand.

### Step 8: GEM Push
```bash
python -m pipeline.gem_step <position_id>
```
Pushes ALL qualified candidates to GEM (email optional). Updates ALL fields: name, title, company, location, email, nickname (opener), custom fields (score, reason).
**Only candidates that passed the Final Review will be qualified at this point.**

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

### Step 11: Full Stats (optional -- for debugging)
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

### CRITICAL: Send Slack Error Alert on ANY Pipeline Failure

**If any step fails and you must stop the pipeline, ALWAYS send a Slack error notification BEFORE stopping:**

```bash
python -m pipeline.slack_step error <position_id> "<step_name>" "<error_message>"
```

Example:
```bash
python -m pipeline.slack_step error autofleet-fullstack-senior "Pre-filter" "Google credentials expired, cannot access sheet"
```

**This is NOT optional.** The team must be notified when the pipeline fails so they can fix the issue before the next run. Silent failures waste an entire day of sourcing.

### Never Skip Steps

**NEVER skip a pipeline step because of a missing config or credentials error.** If a step fails due to missing configuration (Google credentials, API keys, config.json), that is a BLOCKING error -- send Slack error alert, then stop the pipeline.

The only steps that can be skipped on failure are Finalize and Slack (reporting steps). All data-processing steps (Search, Pre-filter, Enrich, Screen, Email, GEM push) are mandatory.

| Step | If it fails... |
|------|---------------|
| Preflight | **Slack error + Stop** -- integrations broken |
| Search | **Slack error + Stop** -- no candidates to process |
| Pre-filter | **Slack error + Stop** -- unfiltered candidates waste enrich credits |
| Enrich | **Slack error + Stop** -- can't screen without enriched profiles |
| Screen | **Slack error + Stop + Retry** -- if Claude fails, retry once. If still fails, send error. |
| Email | Log, continue -- candidates just won't have email for GEM |
| GEM | Log, continue -- candidates stay in DB for next run |
| Finalize | Non-fatal -- stats won't be recorded but pipeline still worked |
| Slack | Non-fatal -- no notification but pipeline completed |

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
