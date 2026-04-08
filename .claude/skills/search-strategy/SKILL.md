---
name: search-strategy
description: Build Crustdata search filters from a job description. Generates tiered search strategy (tight to loose) for the daily sourcing pipeline. Use when setting up a new position or optimizing search results.
argument-hint: [job-description-or-position-id]
---

# Search Strategy Builder

You are a sourcing strategist. Translate a job description into tiered Crustdata People Search DB filters that find the right talent pool.

## Goal

Generate a `search_filters` JSON object for `pipeline_positions.search_filters` with:
- Multiple search rounds ordered tight → loose
- Target: enough candidates to yield ~50 qualified per day
- Cost-efficient: each search call costs 3 Crustdata credits for up to 100 results

## Output Format

```json
{
  "target_qualified": 50,
  "searches": [
    {"name": "exact_match", "filters": {...}},
    {"name": "expanded_titles", "filters": {...}},
    {"name": "adjacent_roles", "filters": {...}}
  ]
}
```

## Step 1: Analyze the JD

Extract:
1. **Core titles** — what is the role called? (e.g., "DevOps Team Lead")
2. **Adjacent titles** — what similar roles exist? (e.g., "SRE Lead", "Platform Engineering Manager")
3. **Seniority level** — IC, Senior, TL, Manager, Director?
4. **Must-have tech** — what stack is required? (e.g., Kubernetes, Terraform)
5. **Location** — where is the role based?
6. **Industry** — any domain preference?

## Step 2: Build Tiered Searches

### Tier 1: Exact Match (tightest)
- Exact title match + required seniority + location
- Example: "DevOps" title + Manager/Director + Israel

### Tier 2: Title Expansion
- Broader title variations + same seniority + location
- Add synonyms: DevOps → SRE, Site Reliability, Platform Engineering

### Tier 3: Adjacent Roles
- Related roles that could transition
- Example: Infrastructure Engineer, Cloud Engineer, K8s specialist

### Tier 4: Loosened Seniority (if needed)
- Drop seniority filter or include "Senior" (could step up to TL)
- Only use if tiers 1-3 don't yield enough candidates

### Tier 5: Skill-Based (if needed)
- Search by skills instead of title
- Example: K8s + Terraform + leadership skills in region

## Crustdata Filter Syntax

### Single filter:
```json
{"column": "current_employers.title", "type": "[.]", "value": "DevOps"}
```

### Multiple filters (AND):
```json
{
  "op": "and",
  "conditions": [
    {"column": "current_employers.title", "type": "[.]", "value": "DevOps"},
    {"column": "current_employers.seniority_level", "type": "in", "value": ["Manager", "Director"]},
    {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
  ]
}
```

### Operators
| Type | Meaning | Example |
|------|---------|---------|
| `[.]` | Substring match | Title contains "DevOps" |
| `(.)` | Fuzzy match | Typo-tolerant |
| `=` | Exact match | Country = "USA" |
| `in` | Set membership | Seniority in ["Manager", "Director"] |
| `not_in` | Exclude set | Title not_in ["Intern"] |
| `>`, `<`, `>=`, `<=` | Comparison | Years > 5 |
| `geo_distance` | Radius search | Within 50mi of Israel |

### Key Columns

**Person:**
- `region` — location (supports geo_distance)
- `skills` — skill array
- `years_of_experience_raw` — numeric

**Current employer:**
- `current_employers.title` — job title
- `current_employers.name` — company name
- `current_employers.seniority_level` — values: "Entry", "Senior", "Manager", "Director", "Vice President", "CXO", "Owner / Partner"
- `current_employers.company_headcount_range` — "11-50", "51-200", "201-500", etc.
- `current_employers.company_industries` — industry array

**Education:**
- `education_background.institute_name` — school name

## Title Expansion Map

| Role Family | Search Terms |
|-------------|-------------|
| DevOps | DevOps, SRE, Site Reliability, Platform Engineer, Infrastructure |
| Backend | Backend, Server, API, Microservices |
| Frontend | Frontend, Front-end, UI Engineer, React, Web |
| Fullstack | Full Stack, Fullstack, Full-Stack |
| Data | Data Engineer, Data Platform, Analytics Engineer, ETL |
| ML/AI | Machine Learning, ML Engineer, AI Engineer, Deep Learning |
| Security | Security Engineer, AppSec, InfoSec, Cyber |
| Mobile | iOS, Android, Mobile, React Native, Flutter |
| QA | QA, SDET, Test Engineer, Quality, Automation Engineer |
| Management | Engineering Manager, R&D Manager, VP Engineering, CTO |

## Seniority Mapping

| Role Level | Crustdata Seniority Values |
|------------|---------------------------|
| Junior/Mid IC | ["Entry", "Senior"] |
| Senior IC | ["Senior"] |
| Team Lead | ["Senior", "Manager"] |
| Manager | ["Manager", "Director"] |
| Director+ | ["Director", "Vice President", "CXO"] |

## Location Patterns

### Israel
```json
{"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
```

### US (specific metro)
```json
{"column": "region", "type": "geo_distance", "value": {"location": "San Francisco", "distance": 30, "unit": "mi"}}
```

### US (nationwide)
```json
{"column": "region", "type": "[.]", "value": "United States"}
```

### Europe (specific country)
```json
{"column": "region", "type": "[.]", "value": "Germany"}
```

## Credit Math

- Each `crustdata_people_search_db` call: **3 credits for up to 100 results**
- Target: 50 qualified candidates
- Qualification rate: ~60% (from testing)
- Need: ~85 total candidates
- Plan: 1-3 search rounds = 3-9 credits

## Example: DevOps Team Lead, Israel

```json
{
  "target_qualified": 50,
  "searches": [
    {
      "name": "devops_leads",
      "filters": {
        "op": "and",
        "conditions": [
          {"column": "current_employers.title", "type": "[.]", "value": "DevOps"},
          {"column": "current_employers.seniority_level", "type": "in", "value": ["Manager", "Director"]},
          {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
        ]
      }
    },
    {
      "name": "devops_senior",
      "filters": {
        "op": "and",
        "conditions": [
          {"column": "current_employers.title", "type": "[.]", "value": "DevOps"},
          {"column": "current_employers.seniority_level", "type": "in", "value": ["Senior", "Manager", "Director"]},
          {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
        ]
      }
    },
    {
      "name": "sre_leads",
      "filters": {
        "op": "and",
        "conditions": [
          {"column": "current_employers.title", "type": "[.]", "value": "SRE"},
          {"column": "current_employers.seniority_level", "type": "in", "value": ["Senior", "Manager", "Director"]},
          {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
        ]
      }
    },
    {
      "name": "platform_infra",
      "filters": {
        "op": "and",
        "conditions": [
          {"column": "current_employers.title", "type": "[.]", "value": "Platform"},
          {"column": "current_employers.seniority_level", "type": "in", "value": ["Senior", "Manager", "Director"]},
          {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
        ]
      }
    },
    {
      "name": "infrastructure_cloud",
      "filters": {
        "op": "and",
        "conditions": [
          {"column": "current_employers.title", "type": "[.]", "value": "Infrastructure"},
          {"column": "current_employers.seniority_level", "type": "in", "value": ["Senior", "Manager", "Director"]},
          {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
        ]
      }
    },
    {
      "name": "cloud_engineers",
      "filters": {
        "op": "and",
        "conditions": [
          {"column": "current_employers.title", "type": "[.]", "value": "Cloud"},
          {"column": "current_employers.seniority_level", "type": "in", "value": ["Senior", "Manager", "Director"]},
          {"column": "region", "type": "geo_distance", "value": {"location": "Israel", "distance": 50, "unit": "mi"}}
        ]
      }
    }
  ]
}
```
