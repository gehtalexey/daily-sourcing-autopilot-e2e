# Crustdata API Reference

Reference guide for Crustdata API integration in SourcingX. Auto-consult when working on enrichment, search, profile data issues, or API integration.

---

## People Database Search API

Search 100M+ professionals by name, company, title, location, skills, experience, and more.

### Endpoint
```
POST https://api.crustdata.com/screener/persondb/search
```

### Authentication
```
Authorization: Token {api_key}
Content-Type: application/json
```

### Cost
- **3 credits per 100 results**
- Up to 1000 results per request with cursor pagination

### Request Body
```json
{
  "filters": {
    "op": "and",
    "conditions": [
      {"column": "current_employers.title", "type": "[.]", "value": "Engineer"},
      {"column": "region", "type": "[.]", "value": "Israel"}
    ]
  },
  "limit": 100,
  "cursor": null,
  "sorts": [{"column": "years_of_experience_raw", "order": "desc"}]
}
```

### Filter Structure

**Single filter:**
```json
{"column": "name", "type": "[.]", "value": "Chris"}
```

**Multiple filters (AND):**
```json
{
  "op": "and",
  "conditions": [
    {"column": "current_employers.name", "type": "[.]", "value": "Google"},
    {"column": "current_employers.title", "type": "[.]", "value": "Engineer"}
  ]
}
```

**Multiple filters (OR):**
```json
{
  "op": "or",
  "conditions": [
    {"column": "headline", "type": "[.]", "value": "backend"},
    {"column": "headline", "type": "[.]", "value": "fullstack"}
  ]
}
```

### Filter Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `[.]` | Substring match (contains) | `{"type": "[.]", "value": "Engineer"}` |
| `(.)` | Fuzzy match (typo-tolerant) | `{"type": "(.)"}` |
| `=` | Exact match | `{"type": "=", "value": true}` |
| `!=` | Not equal | `{"type": "!="}` |
| `in` | Set membership (value MUST be array) | `{"type": "in", "value": ["VP", "Director"]}` |
| `not_in` | Not in set | `{"type": "not_in", "value": [...]}` |
| `>` | Greater than | `{"type": ">", "value": 5}` |
| `<` | Less than | `{"type": "<", "value": 10}` |

**IMPORTANT:** `>=` and `<=` are NOT supported. Use `>` with value-1 or `<` with value+1 instead.

### Valid Column Names

**Person fields:**
| Column | Type | Description |
|--------|------|-------------|
| `name` | string | Full name |
| `first_name` | string | First name |
| `last_name` | string | Last name |
| `region` | string | Location (e.g., "Israel", "Tel Aviv District") |
| `headline` | string | LinkedIn headline |
| `summary` | string | Profile summary/about |
| `skills` | array | Skills list |
| `languages` | array | Languages |
| `years_of_experience_raw` | number | Total years of experience |
| `num_of_connections` | number | LinkedIn connections |
| `recently_changed_jobs` | boolean | Changed jobs in last 90 days |

**Current employer fields (prefix: `current_employers.`):**
| Column | Type | Description |
|--------|------|-------------|
| `current_employers.name` | string | Company name |
| `current_employers.title` | string | Job title |
| `current_employers.seniority_level` | string | Seniority (see values below) |
| `current_employers.company_headcount_range` | string | Company size (see values below) |
| `current_employers.company_industries` | array | Industry list |
| `current_employers.company_hq_location` | string | Company HQ location |
| `current_employers.company_headquarters_country` | string | Country code (e.g., "ISR", "USA") |
| `current_employers.years_at_company_raw` | number | Years at current company |
| `current_employers.business_email_verified` | boolean | Has verified business email |

**Past employer fields (prefix: `past_employers.`):**
Same structure as current_employers.

**Education fields (prefix: `education_background.`):**
| Column | Type | Description |
|--------|------|-------------|
| `education_background.institute_name` | string | School/university name |
| `education_background.degree_name` | string | Degree name |
| `education_background.field_of_study` | string | Field of study |

### Seniority Levels
```
CXO, Vice President, Director, Manager, Senior, Entry, Training, Owner / Partner
```

### Company Headcount Ranges
```
1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+
```

### Response Schema
```json
{
  "profiles": [...],
  "total_count": 12345,
  "next_cursor": "base64_encoded_cursor"
}
```

### Profile Object (Search Result)
```json
{
  "person_id": 12345,
  "name": "John Doe",
  "first_name": "John",
  "last_name": "Doe",
  "region": "Tel Aviv District, Israel",
  "headline": "Senior Backend Engineer at Google",
  "summary": "...",
  "skills": ["Python", "AWS", "Kubernetes"],
  "languages": ["English", "Hebrew"],
  "linkedin_profile_url": "https://www.linkedin.com/in/ACoAAA...",
  "flagship_profile_url": "https://www.linkedin.com/in/johndoe",
  "years_of_experience_raw": 8,
  "recently_changed_jobs": false,
  "current_employers": [...],
  "past_employers": [...],
  "education_background": [...],
  "last_updated": "2026-02-18T21:02:49"
}
```

### Example: Search Backend Engineers in Israel
```python
import requests
import json

with open('config.json') as f:
    config = json.load(f)

body = {
    "filters": {
        "op": "and",
        "conditions": [
            {"column": "current_employers.title", "type": "[.]", "value": "Backend"},
            {"column": "region", "type": "[.]", "value": "Israel"},
            {"column": "years_of_experience_raw", "type": ">", "value": 2}
        ]
    },
    "limit": 100
}

response = requests.post(
    "https://api.crustdata.com/screener/persondb/search",
    json=body,
    headers={
        "Authorization": f"Token {config['api_key']}",
        "Content-Type": "application/json"
    },
    timeout=60
)

data = response.json()
print(f"Found {data['total_count']} profiles")
for profile in data['profiles']:
    print(f"- {profile['name']}: {profile['headline']}")
```

### Pagination
```python
cursor = None
all_profiles = []

while True:
    body = {"filters": filters, "limit": 100, "cursor": cursor}
    response = requests.post(endpoint, json=body, headers=headers)
    data = response.json()

    all_profiles.extend(data['profiles'])
    cursor = data.get('next_cursor')

    if not cursor:
        break
```

---

## Person Enrichment API

Enrich LinkedIn profile URLs with full profile data.

### Endpoint
```
GET https://api.crustdata.com/screener/person/enrich
```

### Authentication
```
Authorization: Token {api_key}
```

### Request Parameters
| Parameter | Type | Description |
|-----------|------|-------------|
| `linkedin_profile_url` | string | Single URL or comma-separated batch (up to 25) |

### Cost
- **3 credits per profile** enriched
- Batch up to 25 profiles per request
- Rate limit: ~500 profiles/day (depends on plan)

### Example Request
```python
import requests

response = requests.get(
    'https://api.crustdata.com/screener/person/enrich',
    params={'linkedin_profile_url': 'https://www.linkedin.com/in/username/'},
    headers={'Authorization': f'Token {api_key}'},
    timeout=120
)
data = response.json()[0]  # Returns array even for single profile
```

### Response Schema

#### Identity Fields
| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Full name |
| `email` | string/null | Email (often null) |
| `title` | string | Current job title |
| `headline` | string | LinkedIn headline |
| `summary` | string | Profile summary (often truncated with "...") |
| `location` | string | Location (e.g., "Tel Aviv District, Israel") |
| `person_id` | int | Crustdata internal ID |

#### URLs
| Field | Type | Description |
|-------|------|-------------|
| `linkedin_profile_url` | string | Encoded LinkedIn URL (URN format) |
| `linkedin_flagship_url` | string | Clean LinkedIn URL (use this one) |
| `profile_picture_url` | string | LinkedIn CDN photo URL (expires) |
| `profile_picture_permalink` | string | Crustdata S3 permanent photo URL |

#### Employment Data
| Field | Type | Description |
|-------|------|-------------|
| `current_employers` | array | Current positions |
| `past_employers` | array | Past positions |
| `all_employers` | array[string] | Flat list of company names |
| `all_employers_company_id` | array[int] | LinkedIn company IDs |
| `all_titles` | array[string] | Flat list of all job titles |

#### Employer Object Structure
```json
{
  "employer_name": "Meta",
  "employer_linkedin_id": "10667",
  "employer_logo_url": "https://...",
  "employer_company_id": [6033736],
  "employer_linkedin_description": "Company description...",
  "employer_company_website_domain": ["metacareers.com"],
  "domains": ["facebook.com", "meta.com", "instagram.com"],
  "employee_title": "Production Engineer",
  "employee_description": "",
  "employee_location": "",
  "employee_position_id": 0,
  "start_date": "2021-06-01T00:00:00",
  "end_date": null
}
```

#### Education Data
| Field | Type | Description |
|-------|------|-------------|
| `education_background` | array | Education history |
| `all_schools` | array[string] | Flat list of school names |
| `all_degrees` | array[string] | Flat list of degree names |

#### Skills & Languages
| Field | Type | Description |
|-------|------|-------------|
| `skills` | array[string] | All listed skills (can be 50+) |
| `languages` | array[string] | Languages (e.g., ["English", "Hebrew"]) |

#### Social Profiles
| Field | Type | Description |
|-------|------|-------------|
| `github_profiles` | array | Matched GitHub accounts |
| `twitter_handle` | string | Twitter/X handle |

#### Metadata
| Field | Type | Description |
|-------|------|-------------|
| `last_updated` | string | When Crustdata last scraped LinkedIn |
| `enriched_realtime` | bool | Whether this was a real-time scrape |
| `num_of_connections` | int | LinkedIn connections (often 0) |

---

## Data Quality Issues

### Empty `past_employers`
Sometimes Crustdata returns profiles with empty `past_employers` even when LinkedIn has full history. This is a **Crustdata scraping issue**, not fixable by re-enriching.

**Signs of incomplete data:**
- `past_employers: []` with person who graduated years ago
- `all_employers` only contains current employer
- `all_titles` only contains current title

### URL Mismatch
Crustdata may return a different `linkedin_flagship_url` than the input URL:
- Input: `linkedin.com/in/yoav-derman-365736152`
- Output: `linkedin.com/in/yderman`

**Solution:** Store both `linkedin_url` (canonical) and `original_url` (input).

### Stale Data
`last_updated` shows when Crustdata last scraped. If old (months), data may be stale. `enriched_realtime: true` means fresh scrape was triggered.

---

## SourcingX Integration

### Code Locations
| File | Purpose |
|------|---------|
| `crustdata_search.py` | People database search API client |
| `normalizers.py` | `normalize_crustdata_profile()` - main normalizer |
| `dashboard.py` | `enrich_batch()` - enrichment API call logic |
| `enrich.py` | Standalone CLI enrichment tool |
| `db.py` | Profile storage in Supabase |

### Storage (normalizers.py)
We normalize and store:
- Identity: `name`, `headline`, `location`, `summary`
- Current job: `current_title`, `current_company`
- Arrays: `skills`, `all_employers`, `all_titles`, `all_schools`
- Raw JSON: `raw_data` column in Supabase

### NOT Currently Stored
- `github_profiles` - available but not extracted
- `twitter_handle` - available but not extracted
- `employer_company_id` - available but not indexed

---

## Sources

- [Crustdata API Docs](https://docs.crustdata.com/)
- [People Data API](https://docs.crustdata.com/docs/discover/people-data-api/)
- [People Search API via Filters](https://docs.crustdata.com/docs/discover/people-search-api-via-filters/)
