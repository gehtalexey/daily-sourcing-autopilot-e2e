---
name: ai-prescreen
description: Intelligent pre-screen of search results before enrichment. Reviews title, company, headline, education against JD + hm_notes to reject clearly irrelevant candidates and save enrich credits. Use after Google Sheet filtering, before enrich step.
argument-hint: [position-id]
---

# AI Pre-Screen -- Think Before You Spend

You are the cost-saving gate between search and enrichment. Every candidate you reject here saves 3 Crustdata credits. Every candidate you wrongly reject is a missed opportunity. Think carefully.

## Your Job

Review each candidate using ONLY the data available from search results:
- Name
- Current title
- Current company
- Headline
- Education

Cross-reference against the **JD** and **hm_notes** (provided in the output). Make a binary decision: **KEEP** or **REJECT**.

## Get Candidates
```bash
python -m pipeline.pre_filter_step get_for_review <position_id>
```

Returns JSON with `candidates` array + `job_description` + `hm_notes`.

## Decision Framework

### REJECT -- be confident, not aggressive

Only reject when you are **clearly certain** the candidate is not relevant. This is NOT full screening -- you don't have work history, skills, or summary yet. You're catching obvious mismatches.

**Reject when:**

1. **Wrong function entirely**
   - Title is sales, marketing, recruiter, product manager, customer success, account executive, business development
   - Example: "VP Customer Success & Professional Services at JFrog" -- has DevOps in career but current role is CS, not DevOps

2. **Wrong seniority direction**
   - Hiring for TL/Manager but candidate is intern, junior, or entry-level
   - Hiring for IC but candidate is C-level at a large company (unlikely to step down)
   - BUT: "Senior" IC applying for TL role = KEEP (could step up)

3. **Company type contradicts hm_notes**
   - hm_notes says "product company DNA" → reject heavy legacy telco (Bezeq, Pelephone, Partner, Cellcom, HOT)
   - hm_notes says "no consulting" → reject Develeap, Tikal, Sela, Matrix, Ness, Accenture, Deloitte
   - hm_notes says "no legacy enterprise" → reject if BOTH title AND company scream old-school IT (e.g., "IT Operations Manager at Bezeq")
   - BUT: someone at Check Point or NICE doing actual DevOps = KEEP (large companies have modern teams)

4. **Location mismatch** (if detectable)
   - Region clearly says another country and role requires Israel
   - BUT: "Frankfurt Rhine-Main Metropolitan Area" for Israel role = reject. "Israel" region = keep.

5. **Title is unrelated keyword match**
   - "DevOps Recruiter", "DevOps Trainer", "DevOps Sales Engineer"
   - The search matched a keyword but the actual role is different

### KEEP -- when in doubt, keep

**Always keep when:**

1. **Title makes sense for the role** even if not exact match
   - Derive adjacent titles from the JD. Examples:
   - Hiring DevOps TL → keep "SRE Manager", "Platform Lead", "Infrastructure Director"
   - Hiring VP Marketing → keep "Head of Marketing", "CMO", "Director Demand Gen", "SVP Growth"
   - These are adjacent roles worth evaluating in full screening

2. **Company is a strong product company** relevant to the role
   - Check hm_notes for target company types (e.g., "B2B SaaS", "fintech startup", "Israeli tech")
   - Even if title is slightly off, strong companies hire strong people

3. **Title is borderline** -- could go either way
   - When unsure, the full enrichment + screening will decide → KEEP
   - Example: "Head of Growth" for a VP Marketing search -- could be relevant

4. **Education or headline suggests relevance** even if title is vague
   - Headline mentions domain-relevant keywords from the JD
   - Education from a top program relevant to the role

5. **Company size is small** and title seems inflated
   - "CTO" at 5-person startup who does DevOps → KEEP, screen later
   - Don't reject based on title inflation -- that's for full screening

## How to Process

1. Read the JD + hm_notes to understand what matters
2. Go through each candidate one by one
3. For each, ask yourself: **"Is there any realistic chance this person fits?"**
   - If yes → KEEP
   - If clearly no → REJECT with one-word reason
4. Collect rejected URLs
5. Log your decisions

### Output Format

Log each decision:
```
[prescreen] KEEP: Chen Levi Elenberg | Director of Hosting and DevOps @ Elementor
[prescreen] KEEP: Ido Goldberg | Head of DevOps @ Wandz.ai
[prescreen] REJECT (wrong function): Ori Asias | VP Customer Success @ JFrog
[prescreen] REJECT (legacy enterprise): Moshe Cohen | IT Operations Manager @ Bezeq
[prescreen] REJECT (consulting): Yael Levy | DevOps Consultant @ Develeap
[prescreen] REJECT (wrong level): David Chen | Junior DevOps @ Startup
[prescreen] REJECT (location): Dirk Radde | Tech Lead DevOps @ Devoteam (Frankfurt)
```

Then remove rejected:
```bash
echo '["url1", "url2", ...]' | python -m pipeline.pre_filter_step remove_irrelevant <position_id>
```

## Rejection Categories

| Category | What it means | Example |
|----------|--------------|---------|
| wrong function | Role is not DevOps/infra/platform at all | VP Sales, Product Manager, Recruiter |
| wrong level | Too junior or too senior for the role | Intern, Junior, CEO of large company |
| legacy enterprise | Old-fashion company per hm_notes | Bezeq, Pelephone, Partner, Cellcom, ECI |
| consulting | Consulting/outsourcing per hm_notes | Develeap, Tikal, Sela, Matrix, Ness |
| location | Not in required location | Based in Germany for Israel role |
| unrelated | Keyword match but unrelated role | "DevOps Recruiter", "Cloud Sales" |

## Quality Rules

1. **Never reject more than 40%** in pre-screen. If you're rejecting more, your search filters are too broad -- flag this for the agent to adjust search intents, don't over-filter here.

2. **Track reject rate by category.** If most rejects are "wrong function", the search title filter is too loose. If most are "legacy enterprise", the search needs company filters.

3. **Don't do full screening here.** You don't have skills, work history, or summary. A candidate with a good title at a good company should ALWAYS pass pre-screen, even if they might fail full screening later.

4. **Don't reject based on name or education.** Names and schools are never grounds for rejection. Education is a positive signal only.

5. **Log the count:** `[prescreen] Result: X kept, Y rejected (Z% reject rate)`

## Israeli Market Notes

- **IDF companies** (Elbit Systems, Rafael, IAI) -- reject only if hm_notes says so. Some have modern DevOps teams.
- **Banks** (Leumi, Hapoalim, Discount) -- usually legacy IT, but fintech divisions can be modern. When in doubt, keep.
- **Telecom** (Bezeq, Partner, Cellcom, HOT) -- typically legacy. Reject if hm_notes mentions product company preference.
- **Government/public sector** -- usually reject for startup roles.
- **Big tech Israel offices** (Google, Meta, Amazon, Apple, Microsoft) -- always keep regardless of exact title.
