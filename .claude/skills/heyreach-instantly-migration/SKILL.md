---
name: heyreach-instantly-migration
description: Migration plan from GEM to Heyreach (LinkedIn outreach) + Instantly (email outreach). Full API reference, architecture, implementation steps, and field mapping.
argument-hint: [question-or-step]
---

# Migration Plan: GEM → Heyreach + Instantly

## Why We're Migrating

GEM's API cannot check if a candidate is already in a sequence (events endpoint returns 403 "deprecated", sequences endpoint is POST-only, candidate object has no sequence fields). This blocks our core need: deduplicating candidates across sourcing runs before outreach.

**Heyreach** handles LinkedIn outreach with automatic cross-campaign duplicate prevention.
**Instantly** handles email outreach with explicit lead search (`GET /api/v2/lead/search`) and `skip_if_in_workspace` / `skip_if_in_campaign` flags.

Both tools also have a **native bidirectional integration** — leads can flow between them automatically (e.g., LinkedIn non-responders → email campaign, and vice versa). Reply on either channel pauses outreach on both.

---

## Current Architecture (GEM)

### Pipeline Flow
```
Search (Crustdata) → Pre-filter → Enrich → Screen → Email (SalesQL) → GEM Push → Slack
```

### GEM Push Step (`pipeline/gem_step.py`)
- Filters: `screening_result='qualified'` AND `gem_pushed=false` AND `personal_email` is set
- **Problem:** Only candidates WITH email get pushed (line 179). Today's run: 50 qualified, only 4 pushed (8%)
- Creates candidate in GEM, adds to project, sets custom fields (email opener, score, reason)
- Uses `nickname` field for `{{nickname}}` email token (255 char limit)

### Files That Touch GEM
| File | What it does |
|------|-------------|
| `integrations/gem.py` | GemClient class — create/update candidates, custom fields, duplicate check |
| `pipeline/gem_step.py` | Push qualified candidates to GEM project |
| `pipeline/gem_csv_export.py` | CSV export for manual GEM import |
| `pipeline/email_step.py` | Checks GEM for existing personal emails before SalesQL |
| `run_pipeline.py` | Calls gem_step in pipeline sequence |
| `.claude/skills/gem-push/SKILL.md` | GEM reference skill |

---

## New Architecture (Heyreach + Instantly)

### New Pipeline Flow
```
Search → Pre-filter → Enrich → Screen → Email (SalesQL) → Outreach Push → Slack
                                                              ├── Heyreach (LinkedIn — ALL qualified)
                                                              └── Instantly (Email — qualified WITH email)
```

### Key Change: Push ALL Qualified to Heyreach (No Email Required)
- Heyreach only needs: `firstName`, `lastName`, `profileUrl` (LinkedIn URL)
- Email is NOT required for LinkedIn outreach
- This means ALL 50 qualified candidates get pushed, not just the 4 with email

### Instantly Gets Candidates WITH Email
- Instantly requires `email` field
- Only qualified candidates with `personal_email` go to Instantly
- Custom variables carry the personalization (opener, score, notes)

### Native Integration Handles Cross-Channel
- Heyreach → Instantly: LinkedIn non-responders auto-added to email campaign
- Instantly → Heyreach: Email non-responders get LinkedIn follow-up
- Reply on either channel → pause on both

---

## API Reference: Heyreach

### Auth & Basics
- **Base URL:** `https://api.heyreach.io`
- **Auth:** `X-API-KEY: <key>` header
- **Rate Limit:** 300 requests/minute
- **Test:** `GET /api/public/auth/CheckApiKey`

### Add Leads to Campaign/List
```
POST /api/public/list/AddLeadsToListV2
```
```json
{
  "leads": [
    {
      "firstName": "John",
      "lastName": "Doe",
      "profileUrl": "https://www.linkedin.com/in/john-doe",
      "location": "San Francisco, CA",
      "companyName": "Acme Corp",
      "position": "VP Engineering",
      "emailAddress": "john@gmail.com",
      "summary": "10 years in cloud infrastructure",
      "about": "AI Score: 9/10 (qualified)",
      "customUserFields": [
        {"name": "email_opener", "value": "Your work scaling Acme's platform..."},
        {"name": "score", "value": "Strong Fit (9/10)"},
        {"name": "reason", "value": "Deep Kubernetes + AWS experience"}
      ]
    }
  ],
  "listId": 12345
}
```

**Required fields:** `firstName`, `lastName`, `profileUrl`
**Optional:** `location`, `companyName`, `position`, `emailAddress`, `summary`, `about`, `customUserFields`

**Response:**
```json
{
  "addedLeadsCount": 5,
  "updatedLeadsCount": 2,
  "failedLeadsCount": 0
}
```

**Duplicate behavior:** Matched by `profileUrl`. Existing leads get updated (reflected in `updatedLeadsCount`). Cross-campaign contacts automatically skipped.

### Get All Campaigns
```
GET /api/public/campaign/GetAllCampaigns
```
Returns: campaign ID, name, status, pagination

### Get Lead Details
```
GET /api/public/lead/GetLead?profileUrl=<linkedin_url>
```
Check if lead exists and get current status.

### Custom Fields Rules
- Field names: alphanumeric + underscores only (spaces auto-convert to `_`)
- Must EXACTLY match variable names in Heyreach sequences
- Used in connection notes and messages as `{field_name}`
- Mismatch → fallback message used instead

### Webhooks (20+ events)
- Connection request sent/accepted
- Message sent/reply received
- InMail sent/reply received
- Campaign completed

---

## API Reference: Instantly

### Auth & Basics
- **Base URL:** `https://api.instantly.ai`
- **Auth:** `Authorization: Bearer <api-key>` (v2 key, NOT v1)
- **Rate Limit:** 100 req/sec, 6000 req/min
- **Max batch:** 1000 leads per request

### Add Leads in Bulk
```
POST /api/v2/leads/add
```
```json
{
  "campaign_id": "uuid-of-campaign",
  "skip_if_in_workspace": true,
  "skip_if_in_campaign": true,
  "leads": [
    {
      "email": "john@gmail.com",
      "first_name": "John",
      "last_name": "Doe",
      "company_name": "Acme Corp",
      "job_title": "VP Engineering",
      "website": "acme.com",
      "personalization": "Your work scaling Acme's platform caught my eye",
      "custom_variables": {
        "linkedin_url": "https://www.linkedin.com/in/john-doe",
        "email_opener": "Your work scaling Acme's platform...",
        "score": "Strong Fit (9/10)",
        "reason": "Deep Kubernetes + AWS experience",
        "position_id": "autofleet-devops-tl"
      }
    }
  ]
}
```

**Required:** `email`
**Optional:** `first_name`, `last_name`, `company_name`, `job_title`, `phone`, `website`, `personalization`, `custom_variables`

**Custom variables:** key-value pairs, values must be string/number/boolean/null (no objects/arrays). Used in email templates as `{{variable_name}}`.

**Response:**
```json
{
  "status": "success",
  "leads_uploaded": 10,
  "duplicated_leads": 2,
  "skipped_count": 1,
  "invalid_email_count": 0
}
```

### Check if Lead Exists (THE KEY FEATURE)
```
POST /api/v2/campaign/searchbycontact
```
Find which campaigns contain a specific lead email.

```
GET /api/v1/lead/get?email=john@gmail.com&campaign_id=<optional>
```
Leave `campaign_id` blank to search ALL campaigns. Returns empty array if not found.

### Duplicate Prevention Flags
| Flag | Behavior |
|------|----------|
| `skip_if_in_workspace` | Skip if lead exists ANYWHERE in workspace (strongest) |
| `skip_if_in_campaign` | Skip if lead exists in ANY campaign |
| `skip_if_in_list` | Skip if lead exists in ANY list |

### List Campaigns
```
GET /api/v2/campaigns?status=1&search=<name>&limit=50
```

### Webhooks
Events: `email_sent`, `email_opened`, `email_link_clicked`, `reply_received`, `email_bounced`, `lead_interested`, `lead_not_interested`, `lead_meeting_booked`, `campaign_completed`

---

## Heyreach ↔ Instantly Native Integration

### Setup
1. Generate API keys in both platforms
2. In Heyreach: Integrations → Instantly → paste Instantly API key → Connect
3. In Instantly: Settings → Integrations → paste Heyreach API key → Connect
4. Pre-create destination campaigns/lists in Instantly BEFORE they appear in Heyreach dropdowns

### Automatic Routing
- **Heyreach → Instantly:** In Heyreach sequence, add "Add to Instantly" action. Trigger: no reply after X days on LinkedIn
- **Instantly → Heyreach:** In Instantly workflows, trigger on campaign completion or non-response → add to Heyreach campaign
- **Reply sync:** Reply on either channel → outreach paused on BOTH platforms automatically

### Data Flow Requirements
- Leads MUST have email to enter Instantly campaigns
- Leads MUST have LinkedIn URL to enter Heyreach campaigns
- Custom emails take priority over enriched emails

---

## Implementation Plan

### Phase 1: New Integration Clients

**File: `integrations/heyreach.py`** (NEW)
```
class HeyreachClient:
    BASE_URL = 'https://api.heyreach.io'
    
    Methods:
    - __init__(api_key, default_list_id)
    - _request(method, endpoint, **kwargs)
    - add_leads_to_list(list_id, leads) → {added, updated, failed}
    - get_campaigns() → list of campaigns
    - get_lead(profile_url) → lead details or None
    - lead_exists(profile_url) → bool
```

**File: `integrations/instantly.py`** (NEW)
```
class InstantlyClient:
    BASE_URL = 'https://api.instantly.ai'
    
    Methods:
    - __init__(api_key, default_campaign_id)
    - _request(method, endpoint, **kwargs)
    - add_leads(campaign_id, leads, skip_if_in_workspace=True) → {uploaded, duplicated, skipped}
    - search_lead(email, campaign_id=None) → lead or None
    - lead_exists(email) → bool
    - list_campaigns() → list of campaigns
```

### Phase 2: New Outreach Push Step

**File: `pipeline/outreach_step.py`** (NEW — replaces `gem_step.py`)
```
Flow:
1. Get qualified candidates with gem_pushed=false (rename field to outreach_pushed later or reuse)
2. Load enriched profiles
3. Split candidates:
   a. ALL qualified → format for Heyreach (LinkedIn)
   b. Qualified WITH email → format for Instantly (email)
4. Push to Heyreach in batches
5. Push to Instantly in batches (with skip_if_in_workspace=true)
6. Mark outreach_pushed=true
7. Print stats
```

**Heyreach field mapping:**
| Pipeline Data | Heyreach Field |
|--------------|----------------|
| raw_data.first_name | firstName |
| raw_data.last_name | lastName |
| linkedin_url | profileUrl |
| raw_data.location | location |
| current_company | companyName |
| current_title | position |
| personal_email | emailAddress |
| email_opener | customUserFields[email_opener] |
| screening_score | customUserFields[score] |
| screening_notes | customUserFields[reason] |

**Instantly field mapping:**
| Pipeline Data | Instantly Field |
|--------------|-----------------|
| personal_email | email |
| raw_data.first_name | first_name |
| raw_data.last_name | last_name |
| current_company | company_name |
| current_title | job_title |
| linkedin_url | custom_variables.linkedin_url |
| email_opener | personalization + custom_variables.email_opener |
| screening_score | custom_variables.score |
| screening_notes | custom_variables.reason |
| position_id | custom_variables.position_id |

### Phase 3: Update Pipeline Orchestrator

**File: `run_pipeline.py`** — change `gem_step` → `outreach_step`

### Phase 4: Update Email Step

**File: `pipeline/email_step.py`** — remove GEM email check (`check_gem_emails` function). SalesQL becomes the only email source.

### Phase 5: Config Changes

**File: `config.json`** — add new keys:
```json
{
  "heyreach_api_key": "...",
  "heyreach_default_list_id": "...",
  "instantly_api_key": "...",
  "instantly_default_campaign_id": "...",
  "gem_api_key": "...(keep for transition, remove later)"
}
```

### Phase 6: Update Skills & Docs

- Replace `.claude/skills/gem-push/SKILL.md` with this skill
- Update `pipeline-orchestrator` skill to reference outreach_step

---

## Config per Position

Each position in the DB can override defaults:
```json
{
  "position_id": "autofleet-devops-tl",
  "heyreach_list_id": "12345",
  "instantly_campaign_id": "uuid-...",
  "gem_project_id": "(deprecated)"
}
```

---

## Verification Steps

1. **Test Heyreach client:**
   ```python
   from integrations.heyreach import get_heyreach_client
   client = get_heyreach_client()
   # Check API key
   # List campaigns
   # Add 1 test lead to a test list
   ```

2. **Test Instantly client:**
   ```python
   from integrations.instantly import get_instantly_client
   client = get_instantly_client()
   # Check API key
   # List campaigns
   # Add 1 test lead with custom variables
   # Search for that lead to verify
   ```

3. **Test outreach_step:**
   ```bash
   python -m pipeline.outreach_step <position_id>
   ```
   Verify: ALL qualified candidates pushed to Heyreach, only those with email to Instantly

4. **Test full pipeline:**
   ```bash
   python run_pipeline.py <position_id>
   ```
   Check Slack report shows Heyreach + Instantly stats instead of GEM

5. **Verify in Heyreach UI:** Leads appear with custom fields populated
6. **Verify in Instantly UI:** Leads appear with custom variables, `{{email_opener}}` renders correctly

---

## Migration Checklist

- [ ] Get Heyreach API key and create target list/campaign
- [ ] Get Instantly API v2 key (NOT v1) and create target campaign
- [ ] Set up native Heyreach ↔ Instantly integration in both UIs
- [ ] Build `integrations/heyreach.py`
- [ ] Build `integrations/instantly.py`
- [ ] Build `pipeline/outreach_step.py`
- [ ] Update `run_pipeline.py`
- [ ] Update `pipeline/email_step.py` (remove GEM email check)
- [ ] Update `config.json` with new keys
- [ ] Test with 1 position end-to-end
- [ ] Update Slack report to show Heyreach/Instantly stats
- [ ] Deprecate GEM files (keep but don't call)
