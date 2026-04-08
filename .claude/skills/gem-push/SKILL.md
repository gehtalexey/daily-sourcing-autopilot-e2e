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
1. Extract `existing_id` from error response
2. Add to project: PUT `/v0/projects/{project_id}/candidates` with `{"candidate_ids": [id], "user_id": "..."}`
3. Update fields: PUT `/v0/candidates/{id}` with profile + email + custom fields

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
| Email opener | custom_fields (email opener) | PUT /candidates |
| Score | custom_fields (score) | PUT /candidates |
| Screening notes | custom_fields (reason) | PUT /candidates |

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| 403 Forbidden | Wrong auth header or expired key | Use X-API-Key, not Bearer |
| 422 created_by missing | POST without created_by | Add created_by user ID |
| 422 Unknown field | Sending fields API doesn't accept | Remove headline, notes, reason from payload |
| 400 Duplicate | Candidate already exists | Use add-to-project + update flow |
| 400 Permission | User can't access project | Check gem_user_email matches project owner |

## Running the GEM Step

```bash
python -m pipeline.gem_step <position_id>
```

For CSV export (optional, for email tokens):
```bash
python -m pipeline.gem_csv_export <position_id>
```
