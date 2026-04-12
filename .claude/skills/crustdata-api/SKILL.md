---
name: crustdata-api
description: Crustdata API Reference -- all endpoints, parameters, credits, and rate limits. Auto-consult when working on enrichment, search, profile data, job listings, company data, watchers, or any Crustdata API integration.
---

# Crustdata API Reference

**Base URL:** `https://api.crustdata.com`
**Auth:** `Authorization: Bearer <token>` header on all requests
**Global policy:** No charges when APIs return no results.

---

## People APIs

### People Search (In-DB)
**Endpoint:** `POST /screener/persondb/search`
**Credits:** 3 per 100 results

| Param | Type | Required | Description |
|---|---|---|---|
| filters | object | Yes | Filter conditions using column/type/value keys. Supports AND/OR nesting. |
| sorts | array | No | Sort criteria [{column, order}] |
| cursor | string | No | Pagination cursor from previous response |
| limit | integer | No (default: 20, max: 1000) | Results per request |
| exclude_profiles | array | No | LinkedIn URLs to exclude (max 50,000, must be https://www.linkedin.com/in/slug format) |
| exclude_names | array | No | Names to exclude from results |

**Filter operators:** `[.]` substring, `(.)` fuzzy, `=` exact, `in` set membership, `not_in` exclude set, `>` `<` comparison, `geo_distance` radius search

**Key columns:** `current_employers.title`, `current_employers.name`, `current_employers.seniority_level` (Entry/Senior/Manager/Director/VP/CXO), `current_employers.company_headcount_range`, `region`, `skills`, `years_of_experience_raw`, `linkedin_profile_url`, `past_employers.name`, `past_employers.title`, `education_background.institute_name`

**Response:** Array of profiles with name, headline, region, location_details, skills, employers, education. Includes `next_cursor` for pagination.

**Notes:**
- Rate limit: 60 RPM
- `exclude_profiles` is a post-processing option (top-level param, NOT a filter condition). Max 50K URLs, 10MB payload. Best perf under 10K.
- Cursor tied to specific query -- invalid if filters/sorts change.

---

### People Search (Realtime)
**Endpoint:** `POST /screener/person/search`
**Credits:** 1 per profile returned (min 5 credits); 5 for preview

| Param | Type | Required | Description |
|---|---|---|---|
| filters | object | No | Filter conditions using filter_type/type/value keys |
| page | integer | No | Page 1-100 (mutually exclusive with limit) |
| limit | integer | No | Max results (sync max 25, async max 10,000) |
| preview | boolean | No | Get preview of profiles |
| background_job | boolean | No | Async processing (required when limit > 25) |
| job_id | string | No | Check status of background job |
| post_processing | object | No | Extra rules: strict_title_and_company_match, exclude_profiles, exclude_names |

**Valid filter_types:** CURRENT_COMPANY, PAST_COMPANY, CURRENT_TITLE, PAST_TITLE, FIRST_NAME, LAST_NAME, REGION, INDUSTRY, COMPANY_HEADQUARTERS, FUNCTION, SENIORITY_LEVEL, SCHOOL, KEYWORD, COMPANY_HEADCOUNT, RECENTLY_CHANGED_JOBS, POSTED_ON_LINKEDIN, YEARS_OF_EXPERIENCE

**Notes:**
- Rate limit: 15 RPM
- Latency: 10-30 seconds (realtime from LinkedIn)
- Max 3 concurrent background jobs
- fuzzy_match and strict_title_and_company_match are mutually exclusive

---

### People Enrichment
**Endpoint:** `GET /screener/person/enrich`
**Credits:** 3/profile (DB), 5/profile (realtime), +2 for business email, +2 for personal email, +2 for phone

| Param | Type | Required | Description |
|---|---|---|---|
| linkedin_profile_url | string | No | Comma-separated LinkedIn URLs (max 25) |
| business_email | string | No | Business email lookup |
| personal_email | string | No | Personal email reverse lookup (access controlled) |
| enrich_realtime | boolean | No (default: false) | Real-time enrichment from LinkedIn |
| fields | string | No | Fields to include (e.g., business_email, personal_contact_info.personal_emails) |
| preview | boolean | No (default: false) | Basic profile details only (access controlled) |
| force_fetch | boolean | No | Always hit LinkedIn, ignore cache. Requires enrich_realtime=true |

**Response:** Array of profile objects with name, location, title, headline, summary, skills, employers (current/past with dates), education, connections.

**Error codes:** PE01 (unavailable), PE02 (internal error), PE03 (not found, queued), PE04 (parsing error)

**Notes:**
- Rate limit: 15 RPM
- Only ONE identifier per request (linkedin_profile_url OR business_email OR personal_email)
- Max 25 profiles per batch
- DB latency: <10s. Realtime: longer.

---

## Company APIs

### Company Identification
**Endpoint:** `POST /screener/identify`
**Credits:** FREE

| Param | Type | Required | Description |
|---|---|---|---|
| query_company_name | string | No | Company name |
| query_company_website | string | No | Company domain |
| query_company_linkedin_url | string | No | LinkedIn company URL |
| query_company_crunchbase_url | string | No | Crunchbase URL |
| query_company_id | string | No | Crustdata company ID |
| exact_match | boolean | No (default: false) | Exact vs fuzzy matching |
| count | integer | No (default: 10, max: 25) | Max results |

**Notes:** Pass only ONE identifier. Rate limit: 30 RPM. Recommended: exact_match=true first, fall back to false.

---

### Company Enrichment
**Endpoint:** `GET /screener/company`
**Credits:** 1/company (DB), 5/company (realtime)

| Param | Type | Required | Description |
|---|---|---|---|
| company_domain | string | No | Comma-separated domains (max 25) |
| company_name | string | No | Comma-separated names (max 25) |
| company_linkedin_url | string | No | Comma-separated URLs (max 25) |
| company_id | integer | No | Comma-separated IDs (max 25) |
| fields | string | No | Enrichment fields to include (see below) |
| enrich_realtime | boolean | No | Real-time enrichment |
| exact_match | boolean | No | Exact matching |

**Available fields:** headcount, funding_and_investment, web_traffic, job_openings, glassdoor, g2, gartner, producthunt, linkedin_followers, news_articles, seo, competitors, taxonomy, founders, cxos, decision_makers, all_office_addresses, estimated_revenue_timeseries, markets

**Notes:** Rate limit: 30 RPM. Without `fields` param, only basic firmographics returned.

---

### Company Search (In-DB)
**Endpoint:** `POST /screener/companydb/search`
**Credits:** 1 per 100 results

| Param | Type | Required | Description |
|---|---|---|---|
| filters | object | Yes | Filter conditions (filter_type/type/value) |
| cursor | string | No | Pagination cursor |
| limit | integer | No (default: 20, max: 1000) | Results per request |
| sorts | array | No | Sort criteria |

**Key fields:** company_name, hq_country (ISO 3-alpha: USA, GBR, IND), year_founded, linkedin_industries, employee_metrics.latest_count, employee_metrics.growth_6m_percent, crunchbase_total_investment_usd, last_funding_round_type, estimated_revenue_lower_bound_usd

**Notes:** Rate limit: 60 RPM. hq_country uses ISO 3166-1 alpha-3 codes.

---

### Company Search (Realtime)
**Endpoint:** `POST /screener/company/search`
**Credits:** 1 per company returned (25 per page)

| Param | Type | Required | Description |
|---|---|---|---|
| filters | array | Yes | Array of {filter_type, type, value} objects |
| page | integer | No | Page 1-65 |

**Valid filter_types:** COMPANY_HEADCOUNT, REGION, INDUSTRY, NUM_OF_FOLLOWERS, FORTUNE, ACCOUNT_ACTIVITIES, JOB_OPPORTUNITIES, COMPANY_HEADCOUNT_GROWTH, ANNUAL_REVENUE, DEPARTMENT_HEADCOUNT, DEPARTMENT_HEADCOUNT_GROWTH, KEYWORD

**Notes:** Rate limit: 15 RPM. ANNUAL_REVENUE uses type "between" with {min, max} in millions USD + sub_filter "USD".

---

### LinkedIn Posts (Company)
**Endpoint:** `GET /screener/linkedin_posts/`
**Credits:** 1/post (default), 5/post (with reactors OR comments), 10/post (both)

| Param | Type | Required | Description |
|---|---|---|---|
| company_name | string | No | Company name (one identifier only) |
| company_domain | string | No | Company domain |
| company_id | string | No | Company ID |
| company_linkedin_url | string | No | Company LinkedIn URL |
| linkedin_post_url | string | No | Direct post URL |
| fields | string | No | Comma-separated: reactors, comments |
| page | integer | No (default: 1) | Page (up to 20) |
| limit | integer | No (default: 5) | Posts per page (1-100) |
| post_types | string | No | "original", "repost", or both |
| max_reactors | integer | No (default: 100) | Max reactors per post (1-5000) |
| max_comments | integer | No (default: 100) | Max comments per post (1-5000) |

**Notes:** Rate limit: 15 RPM. Latency: 30-60s (realtime). Reactors/comments <=100 returns enriched profiles; >100 returns basic data.

---

### LinkedIn Posts (Keyword Search)
**Endpoint:** `GET /screener/linkedin_posts/keyword_search`
**Credits:** Same as LinkedIn Posts

Search posts by keyword across all companies.

---

## Job APIs

### Job Search (New)
**Endpoint:** `POST /job/search`
**Required Header:** `x-api-version: 2025-11-01`
**Credits:** 0.03 per result

| Param | Type | Required | Description |
|---|---|---|---|
| filters | object | No | Filter using field/type/value keys |
| limit | integer | No (default: 20, max: 1000) | Results per request |
| cursor | string | No | Pagination cursor |
| sorts | array | No | Sort criteria |
| fields | array | No | Specific field paths to return |
| aggregations | array | No | count or group_by queries |

**Key fields:** job_details.title, job_details.category, job_details.workplace_type, company.basic_info.name, company.basic_info.crustdata_company_id, company.headcount.total, location.city/state/country, metadata.date_added

**Notes:** Use limit=0 with aggregations for counts without fetching. Cursor-based pagination.

---

### Live Job Search
**Endpoint:** `POST /job/professional_network/search/live`
**Required Header:** `x-api-version: 2025-11-01`
**Credits:** 2 per result

| Param | Type | Required | Description |
|---|---|---|---|
| crustdata_company_id | integer | Yes | Crustdata company ID |
| limit | integer | No (default: 100, max: 100) | Max jobs |
| fields | array | No | Field paths to return |

**Notes:** Real-time from LinkedIn for a specific company.

---

### Job Listings (Legacy)
**Endpoint:** `POST /data_lab/job_listings/Table/`
**Credits:** 1 per company; 5 for sync_from_source/background_task
**DEPRECATED** -- Use Job Search API instead.

---

## Watcher APIs

### Create Watch
**Endpoint:** `POST /watcher/watches`
**Credits:** FREE to create, 5 per notification

| Param | Type | Required | Description |
|---|---|---|---|
| event_type_slug | string | Yes | "job-posting-with-keyword-and-location", "company-watch-linkedin-posts", "linkedin-person-post-updates" |
| event_filters | array | Yes | Event-specific filters |
| lead_filters | array | No | Person criteria |
| account_filters | array | No | Company criteria |
| notification_endpoint | string | Yes | Webhook URL (HTTPS) |
| frequency | integer | No | Days between notifications |
| expiration_date | string | No | YYYY-MM-DD |

### Other Watcher Endpoints
- **Update:** `POST /watcher/watches/{watch_id}/update`
- **Get one:** `GET /watcher/watches/{watch_id}`
- **List all:** `GET /watcher/watches`

**Notes:** No historical data; tracking starts from creation with 1-day lookback. Webhook validation via HMAC-SHA256.

---

## Web APIs

### Web Search
**Endpoint:** `POST /screener/web-search`
**Credits:** 1 per 10 results (ceil)

| Param | Type | Required | Description |
|---|---|---|---|
| query | string | Yes | Search query (max 1000 chars) |
| geolocation | string | No | ISO 3166-1 alpha-2 country code |
| sources | array | No | "news", "web", "scholar-articles", "ai", "social" |
| site | string | No | Restrict to domain |
| numPages | integer | No (default: 1, max: 15) | Pages (web source only) |
| fetch_content | boolean | No | Fetch HTML of each result |

**Notes:** Rate limit: 10 RPM. numPages > 1 only works with "web" source.

### Web Fetch
**Endpoint:** `POST /screener/web-fetch`
**Credits:** 1 per URL

| Param | Type | Required | Description |
|---|---|---|---|
| urls | array | Yes | URLs to fetch (max 10, must include protocol) |

**Notes:** Rate limit: 10 RPM. Public pages only.

---

## Auxiliary APIs (ALL FREE)

### Remaining Credits
**Endpoint:** `GET /user/credits`
**Response:** `{"credits": 9406}`

### Filters Autocomplete (Realtime Search)
**Endpoint:** `POST /screener/filters/autocomplete`
For REGION, INDUSTRY, TITLE, SCHOOL values in realtime search APIs.

### PersonDB Field Autocomplete
**Endpoint:** `POST /screener/persondb/autocomplete`
Discover valid values for PersonDB search fields.

### CompanyDB Field Autocomplete
**Endpoint:** `POST /screener/companydb/autocomplete`
Discover valid values for CompanyDB search fields.

---

## Rate Limits Summary

| Endpoint | RPM |
|---|---|
| screener/persondb/search | 60 |
| screener/companydb/search | 60 |
| screener/persondb/autocomplete | 60 |
| screener/company | 30 |
| screener/identify | 30 |
| screener/person/search | 15 |
| screener/person/enrich | 15 |
| screener/company/search | 15 |
| screener/linkedin_posts | 15 |
| data_lab/job_listings | 15 |
| screener/web-search | 10 |
| screener/web-fetch | 10 |

Uses leaky bucket algorithm. Spread requests evenly -- batching causes 429s.

---

## Credit Costs Summary

| API | Credits |
|---|---|
| Company Identify | FREE |
| Company Enrich (DB/RT) | 1 / 5 |
| Company Search DB | 1 per 100 |
| Company Search RT | 1 per company |
| People Enrich (DB/RT) | 3 / 5 (+2 email, +2 phone) |
| People Search DB | 3 per 100 |
| People Search RT | 1 per profile (min 5) |
| Job Search | 0.03 per result |
| Live Job Search | 2 per result |
| LinkedIn Posts | 1-10 per post |
| Web Search | 1 per 10 results |
| Web Fetch | 1 per URL |
| Watcher Create | FREE (5 per notification) |
| All Autocomplete | FREE |
| Remaining Credits | FREE |
