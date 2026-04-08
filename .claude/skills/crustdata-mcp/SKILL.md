---
name: crustdata-mcp
description: Crustdata MCP search and enrich flow for the daily sourcing pipeline. Covers smart search with progress tracking, dedup, cursor pagination, qualification feedback, and enrichment. Use when searching for candidates or enriching profiles.
argument-hint: [position-id-or-question]
---

# Crustdata MCP — Search & Enrich Flow

## Overview

The pipeline uses two Crustdata MCP tools:
1. **`crustdata_people_search_db`** — Find candidates by title, seniority, location, skills
2. **`crustdata_people_enrich`** — Enrich LinkedIn profiles with full data

Both are called by Claude Code directly (not Python API). Python helpers handle DB I/O.

## Credits
- Search: **3 credits per call** (up to 100 results)
- Enrich: **2-5 credits per profile**
- Budget: optimize for ~50 qualified candidates/day

---

## SEARCH FLOW

### Step 1: Get Config
```bash
python -m pipeline.search_step get_config <position_id>
```

Returns:
```json
{
  "searches": [
    {
      "name": "devops_leads",
      "filters": {"op":"and", "conditions":[...]},
      "progress": {
        "last_cursor": "base64...",
        "total_found": 45,
        "qualified": 28,
        "qual_rate": 0.62,
        "exhausted": false,
        "last_run": "2026-04-08"
      }
    },
    ...
  ],
  "exhausted_searches": ["old_variant_name"],
  "target_qualified": 50,
  "exclude_urls": [...],
  "exclude_count": 120
}
```

**Smart ordering:** Searches are sorted by priority:
1. New searches (no data yet) — need initial data
2. High qual_rate searches — produce best candidates
3. Exhausted searches — skipped entirely

### Step 2: Build Filters from Intent & Run MCP

Each search has an `intent` field (natural language description). The agent reads the intent + JD + hm_notes and constructs the MCP filters dynamically.

**Example intent:** `"Senior SRE engineers in Israel who could step up to a DevOps leadership role"`

**Agent thinks:** This means:
- Title contains "SRE" or "Site Reliability"
- Seniority: Senior or Manager (could step up)
- Region: Israel
- Maybe skills include "Kubernetes", "Terraform"

**Agent constructs:**
```
crustdata_people_search_db(
  filters = {
    "op": "and",
    "conditions": [
      {"column": "current_employers.title", "type": "[.]", "value": "SRE"},
      {"column": "current_employers.seniority_level", "type": "in", "value": ["Senior", "Manager"]},
      {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
    ]
  },
  limit = 100,
  format = "json",
  compact = false    ← IMPORTANT: returns flagship_profile_url
)
```

**Why intent-based (not pre-defined filters):**
- Agent can evolve the intent based on screening feedback
- Same intent can produce different filters as the agent learns (e.g., adds a skill filter)
- Agent can reword/refine intent without breaking anything
- More natural — agent thinks like a recruiter, not a query builder

**If a search still has `filters` (legacy format)**, use them directly. Intent-based is preferred for new searches.

**Response includes:**
- `profiles` — array of candidate profiles
- `total_count` — total matching the filters
- `next_cursor` — cursor for next page (null if last page)
- Each profile has `flagship_profile_url` (clean URL) when compact=false

### Step 3: Save Candidates
```bash
echo '<JSON profiles array>' | python -m pipeline.search_step save_candidates <position_id> <search_name>
```

- Deduplicates against `exclude_urls`
- Tags each candidate with `source: "crustdata_search:<search_name>"` for qual_rate tracking
- Returns: `{"saved": 32, "skipped": 68, "search_name": "devops_leads"}`

### Step 4: Stop Criteria

Stop searching when:
- Total new candidates saved today >= `daily_search_limit` (default 500)
- OR all active searches return mostly duplicates (>90% skipped)
- OR all active search intents have been tried this run

**Config fields:**
- `target_qualified`: 50 — how many qualified we want per day
- `daily_search_limit`: 500 — max new candidates to search per day (across all variants)
- Credits: 500 candidates = ~5 search calls = ~15 credits

### Step 5: Update Qualification Rates (after screening)
```bash
python -m pipeline.search_step update_qual_rates <position_id>
```

Run this AFTER the screening step. It recalculates `qual_rate` per search variant from actual screening results, so the next day's search prioritizes high-performing filters.

### Step 6: Agent Creates New Filters (full autonomy)

When the agent notices patterns in qualified candidates, it creates a new search variant with an intent:

```bash
echo '{"intent": "Platform engineers and infrastructure leads in Israel with Kubernetes expertise, targeting companies with 200-5000 employees"}' | python -m pipeline.search_step add_search <position_id> platform_engineers
```

The `add_search` command accepts either:
- `{"intent": "natural language description"}` — agent builds filters at runtime
- `{"filters": {...}}` — legacy structured filters (backward compatible)

Retire a low-performing one:
```bash
python -m pipeline.search_step retire_search <position_id> cloud_engineers
```

**When to create new filters:**
- A title/skill pattern appears frequently in qualified candidates but isn't in current filters
- Current filters produce <30% qual_rate after 50+ screened
- All current filters return mostly duplicates (>80% skipped)

**When to retire a filter:**
- Qual_rate < 20% after 30+ screened
- Returns 0 new candidates (all deduped) for 3+ consecutive runs

---

## ENRICH FLOW

### Step 1: Get URLs to Enrich
```bash
python -m pipeline.enrich_step get_urls <position_id>
```

Returns URLs not enriched in last 3 months.

### Step 2: Enrich via MCP (batches of 25)
```
crustdata_people_enrich(
  linkedin_profile_url = "url1,url2,...url25"
)
```

Returns full profiles with `linkedin_flagship_url`.

### Step 3: Save Enriched Profiles
```bash
echo '<JSON profiles array>' | python -m pipeline.enrich_step save_profiles <position_id>
```

Saves to `profiles` table + updates `pipeline_candidates` with flagship URLs.

---

## SEARCH vs ENRICH: What Each Returns

| Field | Search (compact=false) | Enrich |
|-------|----------------------|--------|
| flagship_profile_url | YES | YES (as linkedin_flagship_url) |
| name, headline, region | YES | YES |
| skills | YES | YES |
| current_employers (full) | YES | YES |
| past_employers (full) | YES (compact=false) | YES |
| summary | YES (compact=false) | YES |
| education_background | YES | YES |
| work descriptions | YES (compact=false) | YES |
| profile_picture_url | YES (compact=false) | YES |
| total_count + cursor | YES | NO |

**Key insight:** With `compact=false`, search returns almost as much data as enrich — including flagship URLs and work descriptions. This means we may be able to screen directly from search results for a quick first pass.

---

## FILTER SYNTAX

### Operators
| Type | Meaning | Use for |
|------|---------|---------|
| `[.]` | Substring match | Titles, company names |
| `(.)` | Fuzzy match | Typo-tolerant name search |
| `=` | Exact match | Boolean fields |
| `in` | Set membership | Seniority levels (MUST be array) |
| `not_in` | Exclude set | Exclude specific values |
| `>`, `<` | Comparison | Years of experience |
| `geo_distance` | Radius search | Location filtering |

### Key Columns
- `current_employers.title` — job title (substring)
- `current_employers.seniority_level` — "Entry", "Senior", "Manager", "Director", "Vice President", "CXO"
- `current_employers.name` — company name
- `current_employers.company_headcount_range` — "11-50", "51-200", "201-500", etc.
- `region` — location (supports geo_distance)
- `skills` — skills array
- `years_of_experience_raw` — numeric years

### Location Patterns
```json
// Israel
{"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}

// US metro
{"column": "region", "type": "geo_distance", "value": {"location": "San Francisco", "distance": 30, "unit": "mi"}}

// Country
{"column": "region", "type": "[.]", "value": "Germany"}
```

---

## DAILY AGENT BEHAVIOR

The agent follows this loop each day:

1. **Get config** → see which searches are active, their qual_rates
2. **Run active searches in priority order** (new first for exploration, then best qual_rate)
3. **Dedup is automatic** — exclude_urls grows daily, same candidates never re-saved regardless of filter changes
4. **Save candidates** tagged with search_name for tracking
5. **Stop when enough new candidates** or all searches return mostly dupes
6. **After screening**, run `update_qual_rates` → next day is smarter
7. **Analyze patterns** in qualified candidates → create new filter variants
8. **Retire underperformers** (<20% qual after 30+ screened)

### Self-Improving Loop

```
Day 1: Run initial filters → Screen → Learn qual_rates
Day 2: Prioritize best filters → Most results are deduped → Still finds new ones
Day 3: Notice pattern (many qualified have "Platform" title) → Create new filter
Day 4: New "Platform" filter runs first (explore) → Great results → High qual_rate
Day 5: "Platform" filter now prioritized → Old low-performer retired
...
Day N: Agent has evolved filters far beyond the original set
```

### Dedup Mechanics

The only dedup mechanism is **exclude_urls** — the list of ALL LinkedIn URLs already in `pipeline_candidates` for this position. This works because:
- It's URL-based, not page/cursor-based
- It survives filter changes — same person found via different filters gets skipped
- It grows monotonically — once sourced, never re-sourced
- It's checked at save time, not search time — so we see the full search pool size

### Why No Cursors

Cursors are tied to a specific query. When the agent adjusts filters (which it should!), old cursors become invalid. Exclude_urls is the correct dedup approach because it's query-independent.
