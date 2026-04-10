---
name: position-setup
description: Set up a new position in the sourcing pipeline. Guides through JD, search filters, Google Sheet, and GEM project. Use when user says "new position", "add role", or "set up pipeline for".
argument-hint: [job-description-or-url]
---

# Position Setup Guide

Set up a new position in the daily sourcing pipeline. This creates a record in `pipeline_positions` and validates all integrations.

## Information Needed

Ask the user for:

1. **Job Description** -- full text or URL to job posting
2. **Google Sheet URL** -- exclusion lists (past candidates, blacklist, not-relevant companies)
3. **GEM Project URL** -- e.g. `https://www.gem.com/projects/Project-Name--UHJvamVjdDo...` (extract project ID from after `--`)
4. **Hiring Manager Notes** (optional) -- preferences, dealbreakers
5. **Selling Points** (optional) -- what makes the role/company attractive
6. **Sender Info** (optional) -- who the outreach email is from

## Step 1: Parse the JD

Read the JD carefully and extract:
- **Position ID** -- kebab-case slug (e.g., `autofleet-devops-tl`, `wiz-backend-senior`)
- **Role title and level** -- e.g. "DevOps Team Lead" (Manager level)
- **Location** -- Israel, US, remote, etc.
- **Years of experience** -- e.g. 6+

## Step 2: Intake Interview -- Ask the User

After reading the JD, present your understanding and ask these questions using `AskUserQuestion`. This is critical for calibrating the search and screening.

### Question 1: Must-Have Requirements
"Based on the JD, here's what I identified as must-haves. Please confirm or adjust:"
- List 3-5 hard requirements you extracted (e.g., "K8s in production", "2+ years leading a team", "based in Israel")
- Options: "Looks right" / "Let me adjust"

### Question 2: Nice-to-Have vs Must-Have Clarification
"Which of these are nice-to-have vs dealbreaker?"
- List 3-5 items from the JD that are ambiguous (e.g., "GCP experience", "Terraform certification", "specific industry background")
- For each, ask: must-have or nice-to-have?

### Question 3: Dealbreakers
"Are there any automatic disqualifiers not mentioned in the JD?"
- Options: "No specific dealbreakers" / "Yes, let me list them"
- Examples: "No consulting backgrounds", "Must have managed 5+ people", "No candidates from competitor X"

### Question 4: Ideal Candidate Profile
"What does the perfect candidate look like? Describe in 1-2 sentences."
- This helps calibrate the email opener tone and screening notes
- Options: "The JD covers it" / "Let me describe"

### Question 5: Company Selling Points
"What should I highlight when reaching out to candidates?"
- Ask about: team culture, tech challenges, growth opportunity, comp range, remote policy
- Options: "I'll provide selling points" / "Use what's in the JD"

### Question 6: Outreach Sender
"Who is sending the outreach emails?"
- Name, title, and email (e.g., "Yoav Ben Arie, VP R&D, yoav@autofleet.io")
- This affects tone -- CTO vs recruiter emails land differently

### Question 7: Search Priorities
"The Google Sheet has several data sources we can use to prioritize the search. Which do you want to use?"

Present these as multi-select options:

1. **Target Companies** (1000+ pre-vetted product companies)
   - Search candidates currently working at these companies first
   - Best for: roles where company pedigree matters (e.g., product company DNA required)

2. **Target Universities** (top CS/engineering schools)
   - Prioritize candidates from specific universities (Technion, TAU, Hebrew U, etc.)
   - Best for: roles where education background is a strong signal

3. **Tech Alerts / Layoffs** (62 companies with recent layoffs)
   - Search candidates from companies with recent layoffs -- they may be actively looking
   - Best for: faster pipeline fill, candidates more likely to respond

4. **Client-Specific Wanted Companies** (custom per client)
   - Use the client's own list of target companies they want to hire from
   - Best for: targeted poaching from specific competitors or admired companies

Store the selections in `search_filters.search_priorities`:
```json
{
  "search_priorities": {
    "target_companies": true,
    "target_universities": false,
    "tech_alerts": true,
    "client_wanted_companies": false
  }
}
```

The search step will load the selected lists from Google Sheets and pass them to the agent for building prioritized search filters.

### How to Use the Answers

Store the responses in `pipeline_positions`:
- **Must-haves + dealbreakers** → `hm_notes` (used by screening skill to score candidates)
- **Selling points** → `selling_points` (used by screening skill to write email openers)
- **Sender info** → `sender_info` (used for email sequence setup)
- **Nice-to-haves** → append to `hm_notes` as "Nice-to-have: ..."

Example `hm_notes` after intake:
```
MUST HAVE:
- 6+ years DevOps, 2+ years team lead
- Deep Kubernetes (not just "used it once")
- Israel-based (hybrid Tel Aviv)

NICE TO HAVE:
- GCP (preferred over AWS)
- Terraform at scale
- 8200/Mamram background

DEALBREAKERS:
- No consulting-only backgrounds (Develeap, Tikal)
- No split-focus founders with active side businesses

IDEAL PROFILE:
Someone who's been a hands-on DevOps lead at a 200-500 person company,
owns the platform roadmap, and has grown a team from 3 to 8+.
```

## Step 3: Build Search Filters

Use the `/search-strategy` skill to generate tiered search filters. The output is a JSON object for `pipeline_positions.search_filters`.

## Step 4: Verify Google Sheet

```bash
cd "C:/Users/admin/Desktop/Claude Projects/daily-sourcing-autopilot-e2e"
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
Priority search sheets (optional, selected during setup): `Target Companies`, `Universities`, `Tech Alerts`, `Client specific wanted companies`

## Step 5: Extract GEM Project ID

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

## Step 6: Insert Position

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

## Step 7: Validate

Run a quick test:
```bash
# Test init
python -m pipeline.db_helpers init <position-id>

# Test search config
python -m pipeline.search_step get_config <position-id>

# Test sheet access
python -m pipeline.pre_filter_step <position-id>
```

## Step 8: First Run

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
