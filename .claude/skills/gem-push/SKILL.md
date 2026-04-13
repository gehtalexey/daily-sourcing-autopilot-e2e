---
name: gem-push
description: Reference for GEM API v0 integration. Use when pushing candidates to GEM, debugging GEM errors, or setting up a new GEM project.
argument-hint: [position-id-or-question]
---

# GEM API v0 Complete Reference

## API Basics

- **Base URL:** `https://api.gem.com/v0`
- **Content-Type:** `application/json`
- **Auth:** `X-API-Key: <key>` header (NOT Bearer token). Integration partners also need `X-Application-Secret` header.
- **Rate limit:** 20 req/sec, burst capacity 500. Exceeding returns `429 Too Many Requests` or `Limit Exceeded`.
- **Config keys:** `gem_api_key`, `gem_project_id`, `gem_user_email` in config.json

## Pagination

All GET collection endpoints use `page` + `page_size` query params:
- `page`: 1-indexed (default 1)
- `page_size`: default 20, max 100
- Response includes `X-Pagination` header with JSON:

```json
{
  "total": 176,
  "total_pages": 18,
  "first_page": 1,
  "last_page": 18,
  "page": 2,
  "previous_page": 1,
  "next_page": 3
}
```

**IMPORTANT:** Do NOT use `limit`, `offset`, or `cursor` -- those are not GEM API params.

## Error Response Format

All errors return JSON:
```json
{
  "code": 400,
  "status": "Bad Request",
  "message": "Human-readable error message",
  "errors": { ... }
}
```

---

# 1. Users

### GET /v0/users -- List users

Returns all users on the team.

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| email | string (email) | No | Filter by email |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of User objects + `X-Pagination` header
```json
[{ "id": "ObjectID", "name": "string", "email": "string" }]
```

---

# 2. Candidates

### GET /v0/candidates -- List candidates

Returns all candidates on the team. Supports search/filter params.

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| created_after | integer (unix timestamp) | No | Only candidates created after this time |
| created_before | integer (unix timestamp) | No | Only candidates created before this time |
| updated_after | integer (unix timestamp) | No | Only candidates updated after this time |
| updated_before | integer (unix timestamp) | No | Only candidates updated before this time |
| sort | enum: asc, desc | No | Sort order by creation time |
| created_by | string (ObjectID) | No | Filter by user who added the candidate |
| email | string (email, max 255) | No | Candidates whose emails contain this value |
| linked_in_handle | string (1-255) | No | Filter by exact LinkedIn handle |
| candidate_ids | comma-separated ObjectIDs | No | Filter by specific IDs (max 20) |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of Candidate objects + `X-Pagination` header

### POST /v0/candidates -- Create a new candidate

**Required field:** `created_by` (user ObjectID). Auto-detected from `gem_user_email` in config.

**Request body (CandidateCreation):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| created_by | ObjectID | **Yes** | User creating the candidate |
| first_name | string (max 255, nullable) | No | |
| last_name | string (max 255, nullable) | No | |
| nickname | string (max 255, nullable) | No | Maps to `{{nickname}}` in email sequences |
| emails | array of Email (max 20) | No | `[{email_address, is_primary}]` |
| linked_in_handle | string (max 255, nullable) | No | Deduplication key -- if exists, returns 400 with duplicate info |
| title | string (max 255, nullable) | No | Current job title |
| company | string (max 255, nullable) | No | Current company |
| location | string (max 255, nullable) | No | |
| school | string (max 255, nullable) | No | |
| education_info | array of EducationInfo (nullable) | No | `[{school, parsed_university, parsed_school, start_date, end_date, field_of_study, parsed_major_1, parsed_major_2, degree}]` |
| work_info | array of WorkInfo (nullable) | No | `[{company, title, work_start_date, work_end_date, is_current}]` |
| profile_urls | array of URLs (nullable) | No | Auto-generates social profiles from URLs |
| phone_number | string (max 255, nullable) | No | |
| project_ids | array of ObjectIDs (max 20, nullable) | No | Add to these projects on creation |
| custom_fields | array of CustomFieldUpdate | No | `[{custom_field_id, value}]` |
| sourced_from | enum: SeekOut, hireEZ, Starcircle, Censia, Consider (nullable) | No | |
| autofill | boolean (default false) | No | Requires linked_in_handle. Fills missing fields automatically. |

**Response:** `201` -- Candidate object
**Errors:** `400` duplicate (if linked_in_handle exists), `422` validation

### GET /v0/candidates/{candidate_id} -- Get candidate by ID

**Path params:** `candidate_id` (string, required)
**Response:** `200` -- Candidate object

### PUT /v0/candidates/{candidate_id} -- Update a candidate

Only included fields are modified.

**Path params:** `candidate_id` (string, required)

**Request body (CandidateUpdate):**
| Field | Type | Description |
|-------|------|-------------|
| first_name | string (max 255, nullable) | |
| last_name | string (max 255, nullable) | |
| nickname | string (max 255, nullable) | |
| emails | array of Email (max 20) | Replaces all emails |
| title | string (max 255, nullable) | |
| company | string (max 255, nullable) | |
| location | string (max 255, nullable) | |
| school | string (max 255, nullable) | |
| profile_urls | array of URLs | Replaces all social profiles |
| phone_number | string (max 255, nullable) | |
| due_date | DueDate (nullable) | `{date, user_id, note (max 2000)}` |
| custom_fields | array of CustomFieldUpdate | `[{custom_field_id, value}]` |

**Response:** `200` -- Updated Candidate object

### DELETE /v0/candidates/{candidate_id} -- Delete a candidate

**Path params:** `candidate_id` (string, required)

**Request body (CandidateDelete):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| on_behalf_of_user_id | ObjectID | **Yes** | User performing deletion |
| permanently_remove_contact_info | boolean (default false) | No | Prevent contact info from being re-added |

**Response:** `204` No Content

### Candidate Object Fields

```json
{
  "id": "ObjectID (read-only)",
  "created_at": "unix timestamp",
  "created_by": "ObjectID",
  "last_updated_at": "unix timestamp (nullable)",
  "first_name": "string (max 255, nullable)",
  "last_name": "string (max 255, nullable)",
  "nickname": "string (max 255, nullable)",
  "weblink": "string",
  "emails": [{"email_address": "string (max 255)", "is_primary": false}],
  "phone_number": "string (max 255, nullable)",
  "location": "string (max 255, nullable)",
  "linked_in_handle": "string (max 255, nullable)",
  "profiles": [{"network": "string", "url": "string", "username": "string"}],
  "company": "string (max 255, nullable)",
  "title": "string (max 255, nullable)",
  "school": "string (max 255, nullable)",
  "education_info": [{"school", "parsed_university", "parsed_school", "start_date", "end_date", "field_of_study", "parsed_major_1", "parsed_major_2", "degree"}],
  "work_info": [{"company", "title", "work_start_date", "work_end_date", "is_current"}],
  "custom_fields": [{"id", "name", "scope", "project_id", "value", "value_type", "value_option_ids", "custom_field_category (deprecated)", "custom_field_value (deprecated)"}],
  "due_date": {"date": "yyyy-mm-dd", "user_id": "ObjectID", "note": "string (max 2000)"},
  "project_ids": ["ObjectID (max 20)"],
  "sourced_from": "enum (nullable)",
  "gem_source": "enum (nullable)",
  "candidate_greenhouse_id": "string (nullable)"
}
```

---

# 3. Candidate Events (DEPRECATED)

### GET /v0/candidates/{candidate_id}/events -- List candidate events (DEPRECATED)

**Known issue:** Returns `403 "Events are deprecated"` in practice.

**Query params:** `created_after`, `created_before`, `sort`, `page`, `page_size`
**Response:** `200` -- Array of CandidateEvent objects

### POST /v0/candidates/{candidate_id}/events -- Create candidate event

Only `manual_touchpoints` type events can be created.

**Request body:**
| Field | Type | Required |
|-------|------|----------|
| timestamp | integer (unix) | **Yes** |
| user_id | ObjectID | **Yes** |
| on_behalf_of_user_id | ObjectID (nullable) | No |
| project_id | ObjectID | **Yes** |
| sequence_id | ObjectID | **Yes** |
| type | enum: sequences, sequence_replies, manual_touchpoints | **Yes** |
| subtype | enum: first_outreach, follow_up, reply | **Yes** |
| contact_medium | enum: inmail, phone_call, text_message, email, meeting, li_connect_request | **Yes** |
| reply_status | enum: interested, not_interested, later (nullable) | No |

**Response:** `201` -- CandidateEvent object

### GET /v0/candidates/{candidate_id}/events/{event_id} -- Get event by ID

**Response:** `200` -- CandidateEvent object

### DELETE /v0/candidates/{candidate_id}/events/{event_id} -- Delete event

Only `manual_touchpoints` events can be deleted.
**Response:** `204` No Content

### GET /v0/candidates/events -- List all candidate events (DEPRECATED)

**Query params:** `created_after`, `created_before`, `sort`, `page`, `page_size`
**Response:** `200` -- Array of CandidateEvent objects

---

# 4. Candidate Notes

### GET /v0/candidates/{candidate_id}/notes -- List notes for a candidate

**Path params:** `candidate_id` (required)
**Query params:** `created_after`, `created_before`, `sort`, `page`, `page_size`
**Response:** `200` -- Array of Note objects + `X-Pagination` header

### POST /v0/notes -- Create a note

**Request body:**
| Field | Type | Required |
|-------|------|----------|
| candidate_id | ObjectID | **Yes** |
| user_id | ObjectID | **Yes** |
| content | string (max 10000) | **Yes** |
| is_private | boolean (default false) | No |

**Response:** `201` -- Note object

### GET /v0/notes/{note_id} -- Get note by ID

**Response:** `200` -- Note object `{id, candidate_id, user_id, timestamp, is_private, content}`

### DELETE /v0/notes/{note_id} -- Delete a note

**Response:** `204` No Content

---

# 5. Candidate Uploaded Resumes

### GET /v0/candidates/{candidate_id}/uploaded_resumes -- List resumes

**Query params:** `created_after`, `created_before`, `sort`, `page`, `page_size`
**Response:** `200` -- Array of UploadedResume objects `{id, candidate_id, created_at, user_id, filename, download_url}`

### POST /v0/candidates/{candidate_id}/uploaded_resumes/{user_id} -- Upload resume

Both `candidate_id` and `user_id` are path params.

**Content-Type:** `multipart/form-data`
**Request body:** `resume_file` (binary, required). Allowed: .pdf, .doc, .docx. Max 10MB.
**Response:** `201` -- UploadedResume object

---

# 6. Custom Fields

### GET /v0/custom_fields -- List custom fields

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| created_after | integer (unix) | No | |
| created_before | integer (unix) | No | |
| sort | enum: asc, desc | No | |
| project_id | ObjectID (nullable) | No | Filter by project (for project-scoped fields) |
| scope | enum: team, project | No | |
| is_hidden | boolean | No | |
| name | string | No | Filter by name (trimmed) |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of CustomField objects + `X-Pagination` header

### POST /v0/custom_fields -- Create a custom field

**Request body (CustomFieldCreation):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string (1-50) | **Yes** | Unique within scope. Trimmed. |
| value_type | enum: date, text, single_select, multi_select | **Yes** | |
| scope | enum: team, project | **Yes** | |
| project_id | ObjectID (nullable) | Yes if scope=project | |
| option_values | array of strings (1-50 items, each 1-50 chars) | Yes if single/multi_select | |

**Response:** `201` -- CustomField object
**Deduplication:** Returns `400` with existing field info if name already exists in scope.

### GET /v0/custom_fields/{custom_field_id} -- Get custom field by ID

**Response:** `200` -- CustomField object

### PATCH /v0/custom_fields/{custom_field_id} -- Modify a custom field

Custom fields cannot be deleted, only hidden.

**Request body (CustomFieldUpdate):**
| Field | Type | Description |
|-------|------|-------------|
| name | string (1-50) | Rename (must be unique in scope) |
| is_hidden | boolean | Hide/unhide |

**Response:** `200` -- Updated CustomField object

### CustomField Object

```json
{
  "id": "ObjectID",
  "created_at": "unix timestamp",
  "name": "string",
  "value_type": "date | text | single_select | multi_select",
  "scope": "team | project",
  "project_id": "ObjectID (if project scope)",
  "is_hidden": false,
  "options": [{"id": "ObjectID", "value": "string", "is_hidden": false}]
}
```

---

# 7. Custom Field Options

### GET /v0/custom_fields/{custom_field_id}/options -- List options

For single_select/multi_select fields.

**Query params:** `value` (string), `is_hidden` (boolean), `page`, `page_size`
**Response:** `200` -- Array of `{id, value, is_hidden}` + `X-Pagination` header

### POST /v0/custom_fields/{custom_field_id}/options -- Add options

**Request body:**
```json
{ "option_values": ["Option A", "Option B"] }
```
Array of strings, min 1 item, each 1-50 chars.

**Response:** `201` -- Array of created option objects
**Deduplication:** Returns `400` if option values already exist.

### GET /v0/custom_fields/{custom_field_id}/options/{option_id} -- Get option

**Response:** `200` -- `{id, value, is_hidden}`

### PATCH /v0/custom_fields/{custom_field_id}/options/{option_id} -- Modify option

Options cannot be deleted, only hidden.

**Request body:** `{ "is_hidden": true }`
**Response:** `200` -- Updated option object

---

# 8. Projects

### GET /v0/projects -- List projects

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| created_after | integer (unix) | No | |
| created_before | integer (unix) | No | |
| sort | enum: asc, desc | No | |
| user_id | ObjectID | No | Projects owned by this user |
| readable_by | ObjectID | No | Projects this user can read |
| writable_by | ObjectID | No | Projects this user can write to |
| is_archived | boolean | No | |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of Project objects + `X-Pagination` header

### POST /v0/projects -- Create a project

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| user_id | ObjectID | **Yes** | Project owner |
| name | string (max 255) | **Yes** | |
| privacy_type | enum: confidential, personal, shared | No | Default: personal |
| description | string (max 2000, nullable) | No | |

**Response:** `201` -- Project object

### GET /v0/projects/{project_id} -- Get project by ID

**Response:** `200` -- Project object

### PATCH /v0/projects/{project_id} -- Modify a project

**Request body (ProjectUpdate):**
| Field | Type | Description |
|-------|------|-------------|
| user_id | ObjectID | Change owner |
| name | string (max 255) | |
| privacy_type | enum: confidential, personal, shared | |
| description | string (max 2000, nullable) | |
| is_archived | boolean | |

**Response:** `200` -- Updated Project object

### Project Object

```json
{
  "id": "ObjectID (read-only)",
  "created_at": "unix timestamp (read-only)",
  "user_id": "ObjectID (owner)",
  "name": "string (max 255)",
  "privacy_type": "confidential | personal | shared",
  "description": "string (max 2000, nullable)",
  "is_archived": false,
  "project_fields": [{"id", "name", "value", "value_type", "value_option_ids"}],
  "context": "string (read-only, summary of top project fields)"
}
```

---

# 9. Project Candidates Membership

### GET /v0/projects/{project_id}/candidates -- List candidates in a project

**IMPORTANT:** Use this endpoint, NOT `GET /v0/candidates?project_id=...`

Returns `[{candidate_id, added_at}]` -- only IDs, not full profiles. Fetch full profiles via `GET /v0/candidates/{candidate_id}`.

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| added_after | integer (unix) | No | |
| added_before | integer (unix) | No | |
| sort | enum: asc, desc | No | |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of `{candidate_id, added_at}` + `X-Pagination` header

### PUT /v0/projects/{project_id}/candidates -- Add candidates to a project

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| candidate_ids | array of ObjectIDs (1-1000) | **Yes** | |
| user_id | ObjectID | No | User performing action. Must have write access. |

**Response:** `204` No Content
**Errors:** `400` if candidates already in project (returns candidate_ids in error)

### DELETE /v0/projects/{project_id}/candidates -- Remove candidates from a project

**Request body:** Same as PUT -- `{candidate_ids: [...], user_id (optional)}`
**Response:** `204` No Content
**Errors:** `400` if candidates not in project

### GET /v0/project_candidate_membership_log -- Fetch membership log

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| changed_after | integer (unix) | No | |
| changed_before | integer (unix) | No | |
| project_id | string | No | At least one of project_id or candidate_id required |
| candidate_id | string | No | |
| sort | enum: asc, desc | No | |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of `{candidate_id, project_id, action, timestamp}` + `X-Pagination` header

---

# 10. Project Fields

### GET /v0/project_fields -- List project fields

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| created_after | integer (unix) | No | |
| created_before | integer (unix) | No | |
| sort | enum: asc, desc | No | |
| is_hidden | boolean | No | |
| is_required | boolean | No | |
| name | string | No | |
| field_type | enum: text, single_select, multi_select | No | |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of ProjectField objects `{id, name, field_type, user_id, options, is_required, is_hidden}` + `X-Pagination` header

### POST /v0/project_fields -- Create project field

**Request body:**
| Field | Type | Required |
|-------|------|----------|
| name | string (1-255) | **Yes** |
| field_type | enum: text, single_select, multi_select | **Yes** |
| options | array of strings (min 1, each 1-255) | Yes if single/multi_select |
| is_required | boolean (nullable) | No |

**Response:** `201` -- ProjectField object

### GET /v0/project_fields/{project_field_id} -- Get project field by ID

**Response:** `200` -- ProjectField object

### PATCH /v0/project_fields/{project_field_id} -- Update project field

Project fields cannot be deleted, only hidden.

**Request body:**
| Field | Type |
|-------|------|
| name | string (1-255, must be unique across team) |
| is_required | boolean |
| is_hidden | boolean |

**Response:** `200` -- Updated ProjectField object

---

# 11. Project Field Options

### GET /v0/project_fields/{project_field_id}/options -- List options

For single_select/multi_select project fields.

**Query params:** `value` (string), `is_hidden` (boolean), `page`, `page_size`
**Response:** `200` -- Array of `{id, value, is_hidden}` + `X-Pagination` header

### POST /v0/project_fields/{project_field_id}/options -- Create options

**Request body:** `{ "options": ["Option A", "Option B"] }` (min 1 item, each 1-255 chars)
**Response:** `201` -- Array of created option objects

### GET /v0/project_fields/{project_field_id}/options/{option_id} -- Get option

**Response:** `200` -- `{id, value, is_hidden}`

### PATCH /v0/project_fields/{project_field_id}/options/{option_id} -- Update option

Options cannot be deleted, only hidden.

**Request body:** `{ "is_hidden": true }`
**Response:** `200` -- Updated option object

---

# 12. Project Field Options Associations

### GET /v0/projects/{project_id}/project_field_options -- List field option associations

**Response:** `200` -- Array of `{project_field_id, option_id, text, field_type, is_hidden, is_required}`

### POST /v0/projects/{project_id}/project_field_options -- Manage field option associations

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| project_field_id | ObjectID | **Yes** | |
| operation | enum: add, remove | **Yes** | |
| options | array of ObjectIDs | For add on single/multi_select, for remove on multi_select | |
| text | string (min 1) | For add on text fields | |

**Response:** Success (varies)

---

# 13. Sequences

### GET /v0/sequences -- List sequences

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| created_after | integer (unix) | No | |
| created_before | integer (unix) | No | |
| sort | enum: asc, desc | No | |
| user_id | ObjectID | No | Filter by sequence owner |
| page | integer (min 1) | No | Default 1 |
| page_size | integer (1-100) | No | Default 20 |

**Response:** `200` -- Array of Sequence objects + `X-Pagination` header
```json
[{ "id": "ObjectID", "created_at": "unix timestamp", "name": "string (max 255)", "user_id": "ObjectID" }]
```

### GET /v0/sequences/{sequence_id} -- Get sequence by ID

**Response:** `200` -- Sequence object

---

# 14. Data Export

### GET /v0/data_export -- Get most recent data export

**Response:** `200` -- DataExport object `{id, created_at, files: [{name, download_url}]}`

Available file names: event_log, projects, project_fields, custom_project_fields, candidate_custom_field_data, notes, candidate_info, candidate_predicted_demographic_info, user_info, custom_field_metadata, job_openings, jobs, offers, applications, application_custom_fields, application_question_answers, application_stages, applications_jobs, approvals, candidate_custom_fields, candidate_email_addresses, candidate_phone_numbers, candidates, departments, educations, eeoc_responses, employments, hiring_team, interviewers, interviews, job_custom_fields, job_post_locations, job_post_questions, job_posts, jobs_departments, jobs_offices, jobs_interviews, jobs_stages, job_snapshots, offer_custom_fields, offices, opening_custom_fields, openings, referrers, rejection_reasons, scheduled_interviews, scorecard_question_answers, scorecard_questions, scorecards, sources, user_candidate_links, users, stages, stage_snapshots.

---

# Known API Limitations

### No Sequence Membership Check
There is NO way to check if a candidate is currently in a GEM sequence via API:
- `GET /candidates/{id}/events` -- **403 "Events are deprecated"**
- `GET /projects/{id}/sequences` -- **405 Method Not Allowed** (POST only)
- Candidate object has `project_ids` but NO `sequence_ids` field

The only thing you can check is **project membership** via `candidate_exists()`.

### No Sequence Listing Per Project
`GET /v0/sequences` lists all sequences globally (filterable by user_id), but there is no endpoint to list sequences within a specific project.

---

# Pipeline Integration Reference

## Creating a Candidate

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

**Email is optional.** Candidates without email can be pushed -- emails can be found inside GEM later.

## Updating a Candidate

PUT `/v0/candidates/{id}` -- updates profile fields + custom fields:

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

- **WITH `user_id`** -- `400 "User does not have the permission to perform the action."`
- **WITHOUT `user_id`** -- `204 Success`

**Fix:** `_add_to_project()` tries WITHOUT `user_id` first. If that fails, retries WITH `user_id` as fallback.

```python
# WRONG -- fails for candidates owned by other users
payload = {'candidate_ids': [cid], 'user_id': self.created_by}

# RIGHT -- try without user_id first
payload = {'candidate_ids': [cid]}
```

Also: if `_add_to_project` still fails but the candidate already exists, the push should still proceed (mark success, continue to update fields). The candidate might already be in the project -- GEM returns `400 "Candidates with the ids are already in the project"` in that case.

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

### Custom Field Value Types
- `text`: value is a string
- `date`: value is a string `yyyy-mm-dd`
- `single_select`: value is a string (selected option ID)
- `multi_select`: value is an array of strings (selected option IDs)
- Set `value` to `null` to clear text/date/single_select. Set to `[]` to clear multi_select.

## Nickname Field for Email Tokens

- `nickname` field on candidate maps to `{{nickname}}` token in GEM email sequences
- **255 character limit** -- truncate if longer
- Used for personalized email opener in outreach sequences
- Set via PUT `/v0/candidates/{id}` with `"nickname": "Your work on..."`

## Email Tokens ({{reason}}, {{extra1}}, etc.)

**Cannot be set via API.** These are separate from custom fields. Workaround:
- Generate CSV via `python -m pipeline.gem_csv_export <position_id>`
- Upload to GEM: Projects -> Options -> Import CSV
- Map: Reason -> {{reason}}, Extra 1 -> {{extra1}}, etc.

## Field Mapping (Pipeline -> GEM)

| Pipeline Data | GEM Field | API Path |
|--------------|-----------|----------|
| First name | first_name | PUT /candidates |
| Last name | last_name | PUT /candidates |
| Personal email | emails[0].email_address | PUT /candidates |
| Current title | title | PUT /candidates |
| Current company | company | PUT /candidates |
| Location | location | PUT /candidates |
| Education/school | school | PUT /candidates |
| LinkedIn URL -> handle | linked_in_handle | POST /candidates |
| Email opener | nickname (255 char max) | PUT /candidates |
| Email opener | custom_fields (email opener) | PUT /candidates |
| Score | custom_fields (score) | PUT /candidates |
| Screening notes | custom_fields (reason) | PUT /candidates |

## Pipeline Behavior

### Who Gets Pushed
ALL qualified candidates get pushed -- email is NOT required. Candidates without email can have emails found inside GEM later.

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
  +-- 201 Created -> success
  +-- 400 Duplicate -> extract existing_id
       +-- Add to project (PUT /projects/{id}/candidates) -- no user_id
       |    +-- 204 -> success
       |    +-- 400 "already in project" -> already there, success
       |    +-- 400 permission -> still proceed (treat as success)
       +-- Update fields + custom fields (PUT /candidates/{id})
```

## Removing Candidates from a Project

DELETE `/v0/projects/{project_id}/candidates` with body:
```json
{"candidate_ids": ["Y2FuZGlkYXRlczox..."]}
```
Returns 204 on success. Use when re-screening disqualifies previously pushed candidates.

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| 403 Forbidden | Wrong auth header or expired key | Use X-API-Key, not Bearer |
| 403 "Events are deprecated" | Calling GET /candidates/{id}/events | This endpoint no longer works -- no alternative |
| 405 Method Not Allowed | GET on /projects/{id}/sequences | Sequences endpoint only supports GET /v0/sequences (global) |
| 422 created_by missing | POST without created_by | Add created_by user ID |
| 422 Unknown field | Sending fields API doesn't accept | Remove headline, notes, reason from payload |
| 400 Duplicate | Candidate already exists (by linked_in_handle) | Use add-to-project + update flow |
| 400 Permission (add-to-project) | Sending `user_id` for candidate owned by another user | Remove `user_id` from payload |
| 400 "already in project" | Candidate is already in this project | Not an error -- skip and continue |
| 400 "custom field name exists" | Creating duplicate custom field | Extract existing field from error response |
| 429 Too Many Requests | Rate limit exceeded (20/sec or 500 burst) | Back off and retry |

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
| `integrations/gem.py` | GemClient class -- create/update candidates, custom fields, duplicate handling |
| `pipeline/gem_step.py` | Push qualified candidates to GEM project |
| `pipeline/gem_csv_export.py` | CSV export for manual GEM import (email tokens) |
| `pipeline/email_step.py` | Checks GEM for existing personal emails before SalesQL |
