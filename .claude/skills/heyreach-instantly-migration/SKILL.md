---
name: heyreach-instantly-migration
description: Migration plan from GEM to Heyreach (LinkedIn outreach) + Instantly (email outreach). Comprehensive API reference, no-dashboard agent feasibility, multi-client productization analysis, implementation steps, and field mapping.
argument-hint: [question-or-step]
---

# Migration Plan: GEM → Heyreach + Instantly

## Why We're Migrating

GEM's API cannot check if a candidate is already in a sequence (events endpoint returns 403 "deprecated", sequences endpoint is POST-only, candidate object has no sequence fields). This blocks our core need: deduplicating candidates across sourcing runs before outreach.

**Heyreach** handles LinkedIn outreach with automatic cross-campaign duplicate prevention (matched by profileUrl).
**Instantly** handles email outreach with explicit lead search (`POST /api/v2/leads/list`) and `skip_if_in_workspace` / `skip_if_in_campaign` / `skip_if_in_list` flags.

Both tools also have a **native bidirectional integration** -- leads can flow between them automatically (e.g., LinkedIn non-responders → email campaign, and vice versa). Reply on either channel pauses outreach on both.

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
| `integrations/gem.py` | GemClient class -- create/update candidates, custom fields, duplicate check |
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
                                                              ├── Heyreach (LinkedIn -- ALL qualified)
                                                              └── Instantly (Email -- qualified WITH email)
```

### Key Change: Push ALL Qualified to Heyreach (No Email Required)
- Heyreach only needs: `firstName`, `lastName`, `profileUrl` (LinkedIn URL)
- Email is NOT required for LinkedIn outreach
- This means ALL 50 qualified candidates get pushed, not just the 4 with email

### Instantly Gets Candidates WITH Email
- Instantly requires `email` field
- Only qualified candidates with `personal_email` go to Instantly
- Custom variables carry the personalization (opener, score, notes)

### CRITICAL: Push to Campaign, Not Just List
- **AddLeadsToCampaign/V2** → starts outreach sequences automatically
- **AddLeadsToList/V2** → only stages leads, does NOT start outreach
- The pipeline MUST push to campaigns for sequences to activate
- Use lists only for staging/organizing leads before adding to campaigns

### Native Integration Handles Cross-Channel
- Heyreach → Instantly: LinkedIn non-responders auto-added to email campaign
- Instantly → Heyreach: Email non-responders get LinkedIn follow-up
- Reply on either channel → pause on both

---

## API Reference: Heyreach

### Auth & Basics
- **Base URL:** `https://api.heyreach.io`
- **Auth:** `X-API-KEY: <key>` header on every request
- **API keys never expire** (can be deleted/deactivated)
- **Rate Limit:** 300 requests/minute (all endpoints share the limit, 429 on exceed)
- **Test:** `GET /api/public/auth/CheckApiKey` → 200 if valid

---

### Campaign Management (9 endpoints)

#### List All Campaigns
```
POST /api/public/campaign/GetAllCampaigns
```
Body: `{ "offset": 0, "limit": 100 }`
Returns paginated list of campaigns with IDs, names, statuses.

#### Get Campaign by ID
```
GET /api/public/campaign/GetById?campaignId=<id>
```
Returns campaign details including status, sequence steps, linked senders.

#### Resume / Pause Campaign
```
POST /api/public/campaign/Resume
POST /api/public/campaign/Pause
```
Body: `{ "campaignId": 12345 }`

#### Add Leads to Campaign (V1)
```
POST /api/public/campaign/AddLeadsToCampaign
```
Adds leads and **starts outreach sequences**. Same body as AddLeadsToCampaignV2.

#### Add Leads to Campaign V2 (PREFERRED -- use this)
```
POST /api/public/campaign/AddLeadsToCampaignV2
```
Same as V1 but returns counts: `{ addedLeadsCount, updatedLeadsCount, failedLeadsCount }`.

```json
{
  "campaignId": 12345,
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
  ]
}
```

**Required fields:** `firstName`, `lastName`, `profileUrl`
**Optional:** `location`, `companyName`, `position`, `emailAddress`, `summary`, `about`, `customUserFields`

**Response:**
```json
{ "addedLeadsCount": 5, "updatedLeadsCount": 2, "failedLeadsCount": 0 }
```

**Duplicate behavior:** Matched by `profileUrl`. Existing leads get updated (reflected in `updatedLeadsCount`).

#### Stop Lead in Campaign
```
POST /api/public/campaign/StopLeadInCampaign
```
Stops outreach for a specific lead in a campaign.

#### Get Leads from Campaign
```
POST /api/public/campaign/GetLeadsFromCampaign
```
Body: `{ "campaignId": 12345, "offset": 0, "limit": 100 }`
Returns paginated leads with their campaign status.

#### Get Campaigns for Lead
```
POST /api/public/campaign/GetCampaignsForLead
```
Find all campaigns a specific lead is part of. Useful for cross-campaign dedup.

---

### List Management (10 endpoints)

Lists are for organizing/staging leads. Adding to a list does NOT start outreach.

#### Create Empty List
```
POST /api/public/list/CreateEmptyList
```
Body: `{ "name": "My List", "type": "USER_LIST" }` (or `"COMPANY_LIST"`)
Response: `{ "id": 123, "name": "My List", "count": 0, "listType": "USER_LIST", ... }`

#### Get All Lists / Get List by ID
```
POST /api/public/list/GetAll    → body: { "offset": 0, "limit": 100 }
GET  /api/public/list/GetById?listId=<id>
```

#### Add Leads to List V2
```
POST /api/public/list/AddLeadsToListV2
```
Same lead body format as AddLeadsToCampaignV2 but with `listId` instead of `campaignId`.
Up to 100 leads per request.
Response: `{ "addedLeadsCount": 10, "updatedLeadsCount": 1, "failedLeadsCount": 0 }`

#### Get Leads from List
```
POST /api/public/list/GetLeadsFromList
```
Body: `{ "listId": 123, "offset": 0, "limit": 1000 }` -- up to 1000 per request.

#### Get Lists for Lead
```
POST /api/public/list/GetListsForLead
```
Body: `{ "email": "", "linkedinId": "", "profileUrl": "https://www.linkedin.com/in/john-doe/", "offset": 0, "limit": 100 }`
Lookup by email, linkedinId, OR profileUrl. Returns list IDs and names.

#### Delete Leads from List
```
DELETE /api/public/list/DeleteLeadsFromList
DELETE /api/public/list/DeleteLeadsFromListByProfileUrl
```
Delete by lead IDs or by LinkedIn profile URLs. Returns `notFoundInList` array.

#### Get Companies from List
```
POST /api/public/list/GetCompaniesFromList
```
Body: `{ "listId": 123, "offset": 0, "keyword": "HeyReach", "limit": 10 }`

---

### Inbox & Conversations (4 endpoints) -- No-Dashboard Critical

These enable an AI agent to read and respond to LinkedIn conversations without opening HeyReach dashboard.

#### Get Conversations
```
POST /api/public/inbox/GetConversationsV2
```
Retrieve LinkedIn conversations with advanced filtering (by sender, status, date range).

#### Get Chatroom
```
GET /api/public/inbox/GetChatroom?chatroomId=<id>
```
Get a specific conversation thread with all messages.

#### Send Message
```
POST /api/public/inbox/SendMessage
```
Send a LinkedIn message in an existing conversation. Enables agent-driven reply workflows.

#### Set Seen Status
```
POST /api/public/inbox/SetSeenStatus
```
Mark messages as read.

---

### Lead Management (4 endpoints)

#### Get Lead
```
POST /api/public/lead/GetLead
```
Get lead details by profileUrl. Check if lead exists and get current status across campaigns.

#### Lead Tags
```
POST /api/public/lead/AddTags
POST /api/public/lead/GetTags
POST /api/public/lead/ReplaceTags
```
Tag leads for categorization and filtering.

---

### LinkedIn Account Management (2 endpoints)

```
POST /api/public/linkedinaccount/GetAll    → list all connected LinkedIn sender accounts
GET  /api/public/linkedinaccount/GetById   → get specific sender account details
```

---

### Stats (1 endpoint)

```
POST /api/public/stats/GetOverallStats
```
```json
{
  "accountIds": [1234],
  "campaignIds": [],
  "startDate": "2024-12-17T00:00:00.000Z",
  "endDate": "2024-12-19T23:59:59.999Z"
}
```

**Response includes daily breakdowns:**
- `profileViews`, `postLikes`, `follows`
- `messagesSent`, `totalMessageStarted`, `totalMessageReplies`
- `inmailMessagesSent`, `totalInmailStarted`, `totalInmailReplies`
- `connectionsSent`, `connectionsAccepted`
- `messageReplyRate`, `inMailReplyRate`, `connectionAcceptanceRate`

Plus `overallStats` aggregate across the date range.

---

### Webhooks (20+ events)

Events available for subscription:
- Connection request sent / accepted
- Message sent / reply received
- InMail sent / reply received
- Campaign completed
- Lead status changes

Webhook management via the PublicWebhooks endpoints.

### Custom Fields Rules
- Field names: alphanumeric + underscores only (spaces auto-convert to `_`)
- Must EXACTLY match variable names in Heyreach sequences
- Used in connection notes and messages as `{field_name}`
- Mismatch → fallback message used instead

---

## API Reference: Instantly (v2 only -- v1 deprecated Jan 19, 2026)

### Auth & Basics
- **Base URL:** `https://api.instantly.ai`
- **Auth:** `Authorization: Bearer <api-key>` (must be v2 API key)
- **Rate Limit:** 100 req/sec, 6000 req/min (shared across workspace, all API keys)
- **Scopes:** Granular v2 scopes -- `leads:create`, `leads:read`, `leads:all`, `campaigns:read`, `campaigns:all`, `all:create`, `all:read`, `all:all`, etc.
- **Max batch:** 1000 leads per bulk request
- **Full docs:** https://developer.instantly.ai/
- **OpenAPI spec:** https://api.instantly.ai/openapi/api_v2.json

---

### Lead Management (8+ endpoints)

#### Add Leads in Bulk (PRIMARY for pipeline)
```
POST /api/v2/leads/add
```
```json
{
  "campaign_id": "uuid-of-campaign",
  "skip_if_in_workspace": true,
  "skip_if_in_campaign": true,
  "skip_if_in_list": true,
  "verify_leads_on_import": false,
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

Can also use `list_id` instead of `campaign_id` to add to a lead list.

**Required:** `email` (for campaigns), `email` or `first_name`+`last_name` (for lists)
**Optional:** `first_name`, `last_name`, `company_name`, `job_title`, `phone`, `website`, `personalization`, `lt_interest_status`, `assigned_to`, `custom_variables`
**Custom variables:** key-value pairs, values must be string/number/boolean/null (no objects/arrays). Used in email templates as `{{variable_name}}`.

**Response:**
```json
{
  "status": "success",
  "total_sent": 10,
  "leads_uploaded": 8,
  "in_blocklist": 0,
  "duplicated_leads": 1,
  "skipped_count": 1,
  "invalid_email_count": 0,
  "incomplete_count": 0,
  "duplicate_email_count": 0,
  "remaining_in_plan": 4500,
  "created_leads": [
    { "id": "uuid", "index": 0, "email": "john@gmail.com", "first_name": "John", "last_name": "Doe" }
  ]
}
```

#### Create Single Lead
```
POST /api/v2/leads
```
Same fields as bulk add but for a single lead. Also supports skip flags.

#### Get Lead by ID
```
GET /api/v2/leads/{id}
```
Returns full lead object: status, engagement metrics (email_open_count, email_reply_count, email_click_count), timestamps, verification_status, enrichment_status, custom payload.

#### Search/Filter Leads (REPLACES deprecated v1 GET /api/v1/lead/get)
```
POST /api/v2/leads/list
```
```json
{
  "search": "john@gmail.com",
  "campaign": "uuid",
  "list_id": "uuid",
  "filter": "FILTER_VAL_CONTACTED",
  "contacts": ["john@gmail.com"],
  "limit": 100,
  "starting_after": "cursor"
}
```
Rich filtering by status, campaign, list, enrichment status, email contacts.
Response: `{ "items": [...], "next_starting_after": "cursor" }`

#### Update / Delete / Move / Merge
```
PATCH  /api/v2/leads/{id}         → update lead fields
DELETE /api/v2/leads/{id}         → delete single lead
POST   /api/v2/leads/delete       → bulk delete leads
POST   /api/v2/leads/move         → move leads between campaigns/lists
POST   /api/v2/leads/merge        → merge two lead records
```

---

### Duplicate Prevention Flags
| Flag | Behavior |
|------|----------|
| `skip_if_in_workspace` | Skip if lead exists ANYWHERE in workspace (strongest) |
| `skip_if_in_campaign` | Skip if lead exists in ANY campaign |
| `skip_if_in_list` | Skip if lead exists in ANY list |
| `verify_leads_on_import` | Trigger background email verification on import |
| `blocklist_id` | Check against specific blocklist (workspace default if omitted) |

---

### Campaign Management (10+ endpoints)

#### List Campaigns
```
GET /api/v2/campaigns?status=1&search=<name>&limit=50&starting_after=<uuid>
```
Status codes: 0=Draft, 1=Active, 2=Paused, 3=Completed, 4=Running Subsequences, -1=Unhealthy, -2=Bounce Protect, -99=Suspended
Response: `{ "items": [...], "next_starting_after": "uuid" }`

#### Get / Create / Update / Delete Campaign
```
GET    /api/v2/campaigns/{id}
POST   /api/v2/campaigns
PATCH  /api/v2/campaigns/{id}
DELETE /api/v2/campaigns/{id}
```

#### Start/Resume and Pause Campaign
```
POST /api/v2/campaigns/{id}/activate
POST /api/v2/campaigns/{id}/pause
```

#### Search Campaigns by Lead Email (REPLACES deprecated v1 searchbycontact)
```
GET /api/v2/campaigns/search-by-contact?search=john@gmail.com
```
Returns campaigns containing that lead. Use for cross-campaign dedup.

#### Campaign Analytics
```
GET /api/v2/campaigns/{id}/analytics
GET /api/v2/campaigns/{id}/steps-analytics
```

---

### Email Management (6 endpoints) -- No-Dashboard Critical

These enable an AI agent to read and respond to emails without opening the Instantly dashboard.

```
GET  /api/v2/emails                            → list emails with filtering
GET  /api/v2/emails/{id}                       → get specific email content
POST /api/v2/emails/{id}/reply                 → reply to an email
POST /api/v2/emails/{id}/forward               → forward an email
GET  /api/v2/emails/unread/count               → count unread emails
PATCH /api/v2/emails/thread/{threadId}/read    → mark thread as read
```

---

### Lead Lists
```
POST /api/v2/lead-lists          → create lead list
GET  /api/v2/lead-lists          → list all lead lists
GET  /api/v2/lead-lists/{id}     → get list details + verification stats
```

---

### Block List
```
POST /api/v2/block-list-entries       → add single entry
POST /api/v2/block-list-entries/bulk  → bulk add entries
GET  /api/v2/block-list-entries       → list entries
```
Use `blocklist_id` in leads/add to auto-filter against a specific blocklist.

---

### Email Verification
```
POST /api/v2/email-verifications           → submit email for verification
GET  /api/v2/email-verifications/{id}      → check verification status
```

---

### Campaign Subsequences (multi-step flows)
```
POST   /api/v2/campaign-subsequences              → create subsequence
GET    /api/v2/campaign-subsequences               → list subsequences
GET    /api/v2/campaign-subsequences/{id}          → get details
PATCH  /api/v2/campaign-subsequences/{id}          → update
DELETE /api/v2/campaign-subsequences/{id}          → delete
POST   /api/v2/campaign-subsequences/{id}/pause    → pause
POST   /api/v2/campaign-subsequences/{id}/resume   → resume
```

---

### Webhooks
```
POST /api/v2/webhooks                → create webhook subscription
GET  /api/v2/webhooks                → list webhooks
GET  /api/v2/webhooks/{id}           → get webhook details
PATCH /api/v2/webhooks/{id}          → update
DELETE /api/v2/webhooks/{id}         → delete
GET  /api/v2/webhooks/event-types    → list available event types
POST /api/v2/webhooks/{id}/test      → send test event
POST /api/v2/webhooks/{id}/resume    → resume paused webhook
```

**Event types:** `email_sent`, `email_opened`, `email_link_clicked`, `reply_received`, `email_bounced`, `lead_interested`, `lead_not_interested`, `lead_meeting_booked`, `campaign_completed`

---

### Custom Tags
```
POST   /api/v2/custom-tags                        → create tag
GET    /api/v2/custom-tags                         → list tags
POST   /api/v2/custom-tags/assign                  → assign/unassign tags to resources
```

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

## No-Dashboard Agent Feasibility

### Verdict: YES -- for daily operations

Both APIs are comprehensive enough for an AI agent to handle all recurring outreach tasks. Only one-time setup tasks require the dashboard.

### What the Agent CAN Do (Daily Operations via API)

**Heyreach (LinkedIn):**
- Push qualified candidates to campaigns → `AddLeadsToCampaignV2`
- Monitor campaign stats → `GetOverallStats`
- Read LinkedIn replies/conversations → `GetConversationsV2`, `GetChatroom`
- Send follow-up messages → `SendMessage`
- Check which campaigns a lead is in → `GetCampaignsForLead`
- Stop outreach for specific leads → `StopLeadInCampaign`
- Pause/resume campaigns → `Pause`, `Resume`

**Instantly (Email):**
- Push qualified candidates with email → `POST /api/v2/leads/add`
- Monitor campaign analytics → `GET /api/v2/campaigns/{id}/analytics`
- Read email replies → `GET /api/v2/emails`, `GET /api/v2/emails/{id}`
- Reply to emails → `POST /api/v2/emails/{id}/reply`
- Forward emails → `POST /api/v2/emails/{id}/forward`
- Check unread count → `GET /api/v2/emails/unread/count`
- Search if lead already in workspace → `POST /api/v2/leads/list`
- Update lead interest status → `PATCH /api/v2/leads/{id}`
- Manage block lists → `POST /api/v2/block-list-entries`
- Verify emails before sending → `POST /api/v2/email-verifications`

**Both platforms -- webhook-driven events:**
- Reply received → notify user in Slack
- Email bounced → flag lead, try alternate email
- Meeting booked → celebrate in Slack, update CRM
- Campaign completed → trigger next sequence or report

### What Requires Dashboard (One-Time Setup)
- Creating campaign sequences/templates (message steps, delays, conditions)
- Connecting LinkedIn sender accounts (OAuth/cookie auth)
- Connecting email sending accounts (SMTP/OAuth)
- Email warmup configuration
- Setting up the Heyreach ↔ Instantly native integration
- Creating custom fields/variables for the first time

### Agent Daily Workflow
```
1. Pipeline outputs qualified candidates
2. Agent pushes ALL qualified → Heyreach campaign (AddLeadsToCampaignV2)
3. Agent pushes qualified WITH email → Instantly campaign (POST /api/v2/leads/add)
4. Webhooks fire on events → agent processes in real-time:
   - reply_received → notify user, draft response suggestion
   - email_bounced → flag lead, try alternate
   - lead_interested → escalate to user
   - meeting_booked → notify + update tracking
5. On demand: user asks "show me unread replies" → agent reads inbox
6. User approves response → agent sends via API
7. Daily: agent pulls stats from both platforms → Slack report
```

---

## External Agent / Multi-Client Feasibility

### Architecture: One Agent, Many Clients

The pipeline can be productized because:
- **Separate API keys per client** for both Heyreach and Instantly
- **Rate limits are per-API-key** = natural resource isolation
- **Instantly has workspace group management** for agency/multi-client setups
- Pipeline is already parameterized per position (search filters, screening criteria, campaign IDs)

### Per-Client Config Template
```json
{
  "client_id": "acme-corp",
  "client_name": "Acme Corp",
  "heyreach_api_key": "...",
  "instantly_api_key": "...",
  "slack_channel": "#acme-sourcing",
  "positions": [
    {
      "position_id": "acme-devops-tl",
      "heyreach_campaign_id": 12345,
      "instantly_campaign_id": "uuid-...",
      "google_sheet_id": "...",
      "jd_path": "positions/acme-devops-tl/jd.md",
      "search_filters": { ... },
      "daily_search_limit": 500,
      "daily_enrich_limit": 400
    }
  ]
}
```

### Multi-Tenant Considerations
- Each client's data is isolated in their own Heyreach/Instantly workspace
- Webhook endpoints can include client_id for routing: `https://agent.example.com/webhooks/{client_id}`
- Supabase shared DB already supports multi-position; extend to multi-client with a `client_id` column
- Crustdata credits are shared -- need per-client credit tracking or separate Crustdata accounts

---

## Implementation Plan

### Phase 1: New Integration Clients

**File: `integrations/heyreach.py`** (NEW)
```
class HeyreachClient:
    BASE_URL = 'https://api.heyreach.io'
    
    Methods:
    - __init__(api_key, default_campaign_id)
    - _request(method, endpoint, **kwargs)
    - add_leads_to_campaign(campaign_id, leads) → {added, updated, failed}
    - add_leads_to_list(list_id, leads) → {added, updated, failed}
    - get_campaigns() → list of campaigns
    - get_lead(profile_url) → lead details or None
    - lead_exists(profile_url) → bool
    - get_campaigns_for_lead(profile_url) → list of campaigns
    - get_conversations(filters) → conversations
    - get_chatroom(chatroom_id) → messages
    - send_message(chatroom_id, message) → result
    - get_stats(account_ids, campaign_ids, start, end) → stats
    - pause_campaign(campaign_id) → result
    - resume_campaign(campaign_id) → result
```

**File: `integrations/instantly.py`** (NEW)
```
class InstantlyClient:
    BASE_URL = 'https://api.instantly.ai'
    
    Methods:
    - __init__(api_key, default_campaign_id)
    - _request(method, endpoint, **kwargs)
    - add_leads(campaign_id, leads, skip_if_in_workspace=True) → {uploaded, duplicated, skipped}
    - search_leads(search, campaign_id=None) → list of leads
    - lead_exists(email) → bool
    - list_campaigns(status=None) → list of campaigns
    - search_campaigns_by_email(email) → campaigns containing lead
    - list_emails(filters) → emails
    - get_email(email_id) → email content
    - reply_to_email(email_id, body) → result
    - get_unread_count() → int
    - get_campaign_analytics(campaign_id) → analytics
    - create_lead_list(name) → list
    - add_blocklist_entry(entry) → result
    - verify_email(email) → verification result
```

### Phase 2: New Outreach Push Step

**File: `pipeline/outreach_step.py`** (NEW -- replaces `gem_step.py`)
```
Flow:
1. Get qualified candidates with gem_pushed=false (reuse field or rename to outreach_pushed)
2. Load enriched profiles
3. Split candidates:
   a. ALL qualified → format for Heyreach (LinkedIn)
   b. Qualified WITH email → format for Instantly (email)
4. Push to Heyreach CAMPAIGN (AddLeadsToCampaignV2) in batches of 100
5. Push to Instantly CAMPAIGN (POST /api/v2/leads/add) in batches of 1000 (with skip_if_in_workspace=true)
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

**File: `run_pipeline.py`** -- change `gem_step` → `outreach_step`

### Phase 4: Update Email Step

**File: `pipeline/email_step.py`** -- remove GEM email check (`check_gem_emails` function). SalesQL becomes the only email source.

### Phase 5: Config Changes

**File: `config.json`** -- add new keys:
```json
{
  "heyreach_api_key": "...",
  "heyreach_default_campaign_id": "...",
  "instantly_api_key": "...",
  "instantly_default_campaign_id": "...",
  "gem_api_key": "...(keep for transition, remove later)"
}
```

### Phase 6: Update Skills & Docs

- Update `pipeline-orchestrator` skill to reference outreach_step
- Update `pipeline-outreach` skill: GEM fields → Heyreach customUserFields + Instantly custom_variables

### Phase 7: Webhook Listeners (NEW)

Set up webhook endpoints for both platforms:
1. Create HTTPS endpoint (Supabase edge function or external server)
2. Subscribe to Heyreach events: reply received, connection accepted, campaign completed
3. Subscribe to Instantly events: reply_received, email_bounced, lead_interested, meeting_booked
4. Route events to Slack notifications and database updates

### Phase 8: Inbox/Email Agent Commands (NEW)

Build chat commands for the AI agent:
- "show me unread replies" → pull from both Heyreach inbox and Instantly emails
- "reply to [name]" → draft response, user approves, send via API
- "show stats for [campaign/position]" → pull from both platforms, format report
- "pause campaign [name]" → pause on both platforms
- "who replied today" → filter conversations and emails by date

---

## Config per Position

Each position in the DB can override defaults:
```json
{
  "position_id": "autofleet-devops-tl",
  "heyreach_campaign_id": 12345,
  "instantly_campaign_id": "uuid-...",
  "webhook_endpoint": "https://agent.example.com/webhooks/autofleet-devops-tl",
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
   # Add 1 test lead to a test campaign (NOT list)
   # Verify lead appears in campaign
   ```

2. **Test Instantly client:**
   ```python
   from integrations.instantly import get_instantly_client
   client = get_instantly_client()
   # Check API key
   # List campaigns
   # Add 1 test lead with custom variables
   # Search for that lead to verify (POST /api/v2/leads/list)
   ```

3. **Test outreach_step:**
   ```bash
   python -m pipeline.outreach_step <position_id>
   ```
   Verify: ALL qualified candidates pushed to Heyreach campaign, only those with email to Instantly

4. **Test full pipeline:**
   ```bash
   python run_pipeline.py <position_id>
   ```
   Check Slack report shows Heyreach + Instantly stats instead of GEM

5. **Verify in Heyreach UI:** Leads appear in campaign (not just list) with custom fields populated
6. **Verify in Instantly UI:** Leads appear with custom variables, `{{email_opener}}` renders correctly
7. **Test inbox read:** Read conversations from Heyreach and emails from Instantly via API
8. **Test webhooks:** Trigger test events, verify Slack notifications arrive

---

## Migration Checklist

- [ ] Get Heyreach API key and create target campaign with sequence
- [ ] Get Instantly API v2 key (NOT v1 -- v1 deprecated) and create target campaign
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
- [ ] Set up webhook endpoints for both platforms
- [ ] Test inbox/email read/reply via API
- [ ] Verify AddLeadsToCampaignV2 (not just AddLeadsToListV2) works
- [ ] Verify v2 lead search (`POST /api/v2/leads/list`) replaces v1
- [ ] Test agent commands: unread replies, stats, pause campaign
