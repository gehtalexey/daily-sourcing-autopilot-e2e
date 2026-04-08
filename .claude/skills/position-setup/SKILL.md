---
name: position-setup
description: Set up a new position in the sourcing pipeline. Guides through JD, search filters, Google Sheet, and GEM project. Use when user says "new position", "add role", or "set up pipeline for".
argument-hint: [job-description-or-url]
---

# Position Setup Guide

Set up a new position in the daily sourcing pipeline. This creates a record in `pipeline_positions` and validates all integrations.

## Information Needed

Ask the user for:

1. **Job Description** — full text or URL to job posting
2. **Google Sheet URL** — exclusion lists (past candidates, blacklist, not-relevant companies)
3. **GEM Project URL** — e.g. `https://www.gem.com/projects/Project-Name--UHJvamVjdDo...` (extract project ID from after `--`)
4. **Hiring Manager Notes** (optional) — preferences, dealbreakers
5. **Selling Points** (optional) — what makes the role/company attractive
6. **Sender Info** (optional) — who the outreach email is from

## Step 1: Parse the JD

Extract key info:
- **Position ID** — kebab-case slug (e.g., `autofleet-devops-tl`, `wiz-backend-senior`)
- **Role title and level** — e.g. "DevOps Team Lead" (Manager level)
- **Location** — Israel, US, remote, etc.
- **Must-have tech** — K8s, Terraform, GCP, etc.
- **Years of experience** — e.g. 6+
- **Key responsibilities** — team management, platform work, etc.

## Step 2: Build Search Filters

Use the `/search-strategy` skill to generate tiered search filters. The output is a JSON object for `pipeline_positions.search_filters`.

## Step 3: Verify Google Sheet

```bash
cd "C:/Users/gehta/OneDrive/Desktop/Claude Code Projects/daily-sourcing-autopilot-e2e"
python -c "
import gspread
from google.oauth2.service_account import Credentials
creds = Credentials.from_service_account_file('google_credentials.json',
    scopes=['https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'])
gc = gspread.authorize(creds)
sheet = gc.open_by_key('<SHEET_ID>')
for ws in sheet.worksheets():
    print(f'{ws.title}: {len(ws.get_all_values())} rows')
"
```

Required sheets: `Past Candidates`, `Blacklist`, `NotRelevant Companies`
Optional: `Target Companies`, `Universities`

## Step 4: Extract GEM Project ID

From URL like `https://www.gem.com/projects/Project-Name--UHJvamVjdDoxNDMzMTk4`:
- Project ID = `UHJvamVjdDoxNDMzMTk4` (the base64 part after `--`)

Verify access:
```bash
python -c "
import requests, json
config = json.load(open('config.json'))
resp = requests.get('https://api.gem.com/v0/projects/<PROJECT_ID>',
    headers={'X-API-Key': config['gem_api_key']}, timeout=30)
print(resp.json().get('name', 'ERROR'))
"
```

## Step 5: Insert Position

```sql
INSERT INTO pipeline_positions (
    position_id, job_description, search_filters,
    sheet_url, gem_project_id,
    hm_notes, selling_points, sender_info, active
) VALUES (
    '<position-id>',
    '<job-description-text>',
    '<search-filters-json>'::jsonb,
    '<google-sheet-url>',
    '<gem-project-id>',
    '<hiring-manager-notes>',
    '<selling-points>',
    '<sender-info>',
    true
);
```

Use the Supabase MCP `execute_sql` tool with project_id `ciyyvbzblogtbwabhbmh`.

## Step 6: Validate

Run a quick test:
```bash
# Test init
python -m pipeline.db_helpers init <position-id>

# Test search config
python -m pipeline.search_step get_config <position-id>

# Test sheet access
python -m pipeline.pre_filter_step <position-id>
```

## Step 7: First Run

Run the full pipeline manually to verify:
1. Search → save candidates
2. Pre-filter
3. Enrich
4. Screen (Claude evaluates)
5. Email lookup
6. GEM push
7. Finalize + Slack

Use `/pipeline-orchestrator` for step-by-step commands.

## Position Checklist

- [ ] position_id created (kebab-case)
- [ ] job_description stored
- [ ] search_filters built (tiered, JSON)
- [ ] sheet_url set and accessible
- [ ] gem_project_id set and API accessible
- [ ] hm_notes added (optional)
- [ ] selling_points added (optional)
- [ ] active = true
- [ ] Test search returns results
- [ ] Test pre-filter runs without errors
- [ ] First full pipeline run completed
