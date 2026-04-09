---
name: gem-push
description: Reference for GEM API v0 integration. Use when pushing candidates to GEM, debugging GEM errors, or setting up a new GEM project.
argument-hint: [position-id-or-question]
---

# GEM ATS Integration Reference

## API Basics

- **Base URL:** `https://api.gem.com/v0`
- **Auth:** `X-API-Key: <key>` header (NOT Bearer token)
- **Rate limit:** 20 req/sec, burst 500
- **Config keys:** `gem_api_key`, `gem_project_id`, `gem_user_email` in config.json

## Key Endpoints

| Action | Method | Endpoint |
|--------|--------|----------|
| List candidates | GET | `/v0/candidates` |
| Create candidate | POST | `/v0/candidates` |
| Update candidate | PUT | `/v0/candidates/{id}` |
| Add to project | PUT | `/v0/projects/{project_id}/candidates` |
| List users | GET | `/v0/users` |
| List custom fields | GET | `/v0/custom_fields` |
| Create custom field | POST | `/v0/custom_fields` |

## Known API Limitations

### No Sequence Membership Check
There is NO way to check if a candidate is currently in a GEM sequence via API:
- `GET /candidates/{id}/events` → **403 "Events are deprecated"**
- `GET /projects/{id}/sequences` → **405 Method Not Allowed** (POST only — create, not list)
- Candidate object has `project_ids` but NO `sequence_ids` field

The only thing you can check is **project membership** via `candidate_exists()`.

### No Sequence Listing
`GET /projects/{id}/sequences` is not supported. You can only `POST` to create sequences.

## Creating a Candidate

POST requires `created_by` (user ID). Auto-detected from `gem_user_email` in config.

```json
{
  "first_name": "Chen",
  "last_name": "Levi Elenberg",
  "emails": [{"email_address": "chen@gmail.com", "is_primary": true}],
  "linked_in_handle": "chen-levi-elenberg",
  "title": "Director of Hosting and DevOps",
  "company": "Elementor",
  "location": "Israel",
  "project_ids": ["UHJvamVjdDo..."],
  "created_by": "dXNlcnM6..."
}
```

**Email is optional.** Candidates without email can be pushed — emails can be found inside GEM later.

**NOT supported on create:** `headline`, `notes`, `reason`, `extra1-3`

## Updating a Candidate

PUT `/v0/candidates/{id}` — updates profile fields + custom fields:

```json
{
  "first_name": "Chen",
  "last_name": "Levi Elenberg",
  "title": "Director of Hosting and DevOps",
  "company": "Elementor",
  "location": "Israel",
  "emails": [{"email_address": "chen@gmail.com", "is_primary": true}],
  "custom_fields": [
    {"custom_field_id": "Q3VzdG9t...", "value": "Your journey from..."}
  ]
}
```

## Handling Duplicates

When POST returns 400 with `duplicate_candidate`:

```json
{
  "code": 400,
  "errors": {
    "duplicate_candidate": {
      "id": "Y2FuZGlkYXRlczoxODQ5NzA3Nw==",
      "li_handle": "omer-palombo-23307354"
    }
  },
  "message": "Candidate with LinkedIn handle \"omer-palombo-23307354\" already exists."
}
```

**Flow:**
1. Extract `existing_id` from error response
2. Add to project: PUT `/v0/projects/{project_id}/candidates`
3. Update fields: PUT `/v0/candidates/{id}` with profile + email + custom fields
4. If add-to-project fails with permission error, still proceed to update fields

### CRITICAL: user_id Permission Bug on Add-to-Project

When adding an existing candidate (owned by another GEM user) to your project:

- **WITH `user_id`** → `400 "User does not have the permission to perform the action."`
- **WITHOUT `user_id`** → `204 Success`

**Fix:** `_add_to_project()` tries WITHOUT `user_id` first. If that fails, retries WITH `user_id` as fallback.

```python
# WRONG — fails for candidates owned by other users
payload = {'candidate_ids': [cid], 'user_id': self.created_by}

# RIGHT — try without user_id first
payload = {'candidate_ids': [cid]}
```

Also: if `_add_to_project` still fails but the candidate already exists, the push should still proceed (mark success, continue to update fields). The candidate might already be in the project — GEM returns `400 "Candidates with the ids are already in the project"` in that case.

## Custom Fields

Our pipeline uses **project-scoped** custom fields (visible in table columns):

| Field Name | Contains | Maps to |
|------------|----------|---------|
| email opener | Personalized 1-2 sentence opener | Recruiter outreach |
| score | "Strong Fit (9/10)" | Fit assessment |
| reason | Screening notes | Why they fit/don't |

### Auto-creation
The `get_or_create_custom_fields()` method auto-creates these if missing for a project:
- Scope: `project`
- Type: `text`
- Linked to specific `project_id`

### Custom Field IDs
IDs are base64-encoded and project-specific. Always look up via GET `/v0/custom_fields` first.

## Nickname Field for Email Tokens

- `nickname` field on candidate maps to `{{nickname}}` token in GEM email sequences
- **255 character limit** — truncate if longer
- Used for personalized email opener in outreach sequences
- Set via PUT `/v0/candidates/{id}` with `"nickname": "Your work on..."`

## Email Tokens ({{reason}}, {{extra1}}, etc.)

**Cannot be set via API.** These are separate from custom fields. Workaround:
- Generate CSV via `python -m pipeline.gem_csv_export <position_id>`
- Upload to GEM: Projects → Options → Import CSV
- Map: Reason → {{reason}}, Extra 1 → {{extra1}}, etc.

## Field Mapping (Pipeline → GEM)

| Pipeline Data | GEM Field | API Path |
|--------------|-----------|----------|
| First name | first_name | PUT /candidates |
| Last name | last_name | PUT /candidates |
| Personal email | emails[0].email_address | PUT /candidates |
| Current title | title | PUT /candidates |
| Current company | company | PUT /candidates |
| Location | location | PUT /candidates |
| LinkedIn URL → handle | linked_in_handle | POST /candidates |
| Email opener | nickname (255 char max) | PUT /candidates |
| Email opener | custom_fields (email opener) | PUT /candidates |
| Score | custom_fields (score) | PUT /candidates |
| Screening notes | custom_fields (reason) | PUT /candidates |

## Pipeline Behavior

### Who Gets Pushed
ALL qualified candidates get pushed — email is NOT required. Candidates without email can have emails found inside GEM later.

### Query
```python
candidates = get_pipeline_candidates(client, position_id, {
    'screening_result': 'eq.qualified',
    'gem_pushed': 'eq.false',
})
```

### Duplicate Handling Flow
```
Create candidate (POST /candidates)
  ├── 201 Created → success
  └── 400 Duplicate → extract existing_id
       ├── Add to project (PUT /projects/{id}/candidates) — no user_id
       │    ├── 204 → success
       │    ├── 400 "already in project" → already there, success
       │    └── 400 permission → still proceed (treat as success)
       └── Update fields + custom fields (PUT /candidates/{id})
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| 403 Forbidden | Wrong auth header or expired key | Use X-API-Key, not Bearer |
| 403 "Events are deprecated" | Calling GET /candidates/{id}/events | This endpoint no longer works — no alternative |
| 405 Method Not Allowed | GET on /projects/{id}/sequences | Sequences endpoint only supports POST (create) |
| 422 created_by missing | POST without created_by | Add created_by user ID |
| 422 Unknown field | Sending fields API doesn't accept | Remove headline, notes, reason from payload |
| 400 Duplicate | Candidate already exists | Use add-to-project + update flow |
| 400 Permission (add-to-project) | Sending `user_id` for candidate owned by another user | Remove `user_id` from payload |
| 400 "already in project" | Candidate is already in this project | Not an error — skip and continue |

## Running the GEM Step

```bash
python -m pipeline.gem_step <position_id>
```

For CSV export (optional, for email tokens):
```bash
python -m pipeline.gem_csv_export <position_id>
```

## Key Files

| File | Purpose |
|------|---------|
| `integrations/gem.py` | GemClient class — create/update candidates, custom fields, duplicate handling |
| `pipeline/gem_step.py` | Push qualified candidates to GEM project |
| `pipeline/gem_csv_export.py` | CSV export for manual GEM import (email tokens) |
| `pipeline/email_step.py` | Checks GEM for existing personal emails before SalesQL |
