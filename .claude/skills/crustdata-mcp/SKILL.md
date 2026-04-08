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

### Step 2: Run Each Search via MCP

For each active search in the `searches` array:

```
crustdata_people_search_db(
  filters = <the search's "filters" object>,
  limit = 100,
  format = "json",
  compact = false    ← IMPORTANT: returns flagship_profile_url
)
```

**If the search has `progress.last_cursor`** — the search was partially paginated. Pass the cursor to continue:
```
crustdata_people_search_db(
  filters = <filters>,
  limit = 100,
  format = "json",
  compact = false,
  cursor = "<progress.last_cursor>"
)
```

**Response includes:**
- `profiles` — array of candidate profiles
- `total_count` — total matching the filters
- `next_cursor` — cursor for next page (null if exhausted)
- Each profile has `flagship_profile_url` (clean URL) when compact=false

### Step 3: Save Candidates
```bash
echo '<JSON profiles array>' | python -m pipeline.search_step save_candidates <position_id> <search_name>
```

- Deduplicates against `exclude_urls`
- Tags each candidate with `source: "crustdata_search:<search_name>"` for qual_rate tracking
- Returns: `{"saved": 32, "skipped": 68, "search_name": "devops_leads"}`

### Step 4: Save Progress
```bash
echo '{"next_cursor":"base64...","new_saved":32}' | python -m pipeline.search_step save_progress <position_id> <search_name>
```

- Stores cursor so tomorrow's run continues where today left off
- If `new_saved=0` and no `next_cursor` → marks search as exhausted
- Updates `last_run` date

### Step 5: Stop Criteria

Stop searching when:
- Total new candidates saved today >= `target_qualified / 0.6` (~85 for target 50)
- OR all active searches are exhausted
- OR credit budget exceeded

### Step 6: Update Qualification Rates (after screening)
```bash
python -m pipeline.search_step update_qual_rates <position_id>
```

Run this AFTER the screening step. It recalculates `qual_rate` per search variant from actual screening results, so the next day's search prioritizes high-performing filters.

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

The agent should follow this loop each day:

1. **Get config** → see which searches are active, which are exhausted
2. **Run active searches in priority order** (best qual_rate first)
3. **Use cursor** to continue from yesterday's position
4. **Save candidates + progress** after each search
5. **Stop when enough candidates** or all exhausted
6. **After screening**, update qual_rates so tomorrow is smarter
7. **If all searches exhausted** → log a message suggesting new filter variants

This creates a self-improving loop where the agent:
- Never re-searches the same candidates (cursor + dedup)
- Prioritizes filters that actually produce good candidates
- Automatically detects when a filter pool is exhausted
- Gets smarter each day from screening feedback
