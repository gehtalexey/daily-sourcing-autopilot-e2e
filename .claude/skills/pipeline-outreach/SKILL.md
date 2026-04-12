---
name: pipeline-outreach
description: Generate email subject line + personalized opener for qualified pipeline candidates. Uses JD context, screening results, and selling points. Called after screening, output goes to GEM (subject → email subject, opener → nickname field for {{nickname}} token).
argument-hint: [candidate-profile + screening-result]
---

# Pipeline Outreach -- Subject Line + Personalized Opener

Generate a subject line and opener for each qualified candidate. This runs AFTER screening, so you have:
- The candidate's full enriched profile
- The screening notes (why they qualified)
- The JD, hm_notes, and selling_points from the position config
- The sender_info (who the email is from)

## Output Format

For each qualified candidate, output:
```
Subject: [subject line]
Opener: [personal opener sentence]
```

These map to GEM fields:
- **Subject** → email sequence subject line
- **Opener** → `nickname` field → `{{nickname}}` token in email body

## Subject Line Rules

### Length & Tone
- Under 10 words
- Casual, curious -- like a peer-to-peer message
- Should make them want to open the email
- Israeli market: informal is fine, no corporate stiffness

### Priority order (pick the BEST one that fits):
1. **Previous company → current company transition** (only if different and notable)
2. **Notable school** (Technion, TAU, Hebrew U, BGU)
3. **Interesting title** (Team Lead, Staff, Founding, Architect)
4. **Previous company** (any, if different from current)
5. **Interesting skill** (Rust, Go, K8s, Kafka, Scala)
6. **Generic from current company** (fallback only)

### Good Subject Lines
```
Snyk to Wilco?
Google to Foretellix?
Technion grad building at Monday?
Still hands-on as lead?
Staff engineer at Wiz?
K8s at scale?
Go and microservices at Melio?
```

### NEVER Use
- Em dashes (--)
- "Exciting opportunity"
- "Are you open to..."
- More than 10 words
- Company name of the HIRING company in the subject

### VALIDATION
Before outputting a subject line, check:
- [ ] No em dashes
- [ ] Under 10 words
- [ ] Prev company ≠ current company (if using transition format)
- [ ] Same parent company caught (Google/Waze, Unity/ironSource, SAP/Gigya)
- [ ] Company name cleaned (strip Ltd, Inc, Corp, Technologies, Labs, .io)

## Opener Rules

### What It Is
1-2 sentences that go in the email body as the first personalized paragraph. Must feel like a human recruiter wrote it after actually reading their profile.

### Context Awareness
You have THREE data sources -- use them all:

1. **Profile data** → reference a specific detail (company move, skill, team size, project)
2. **Screening notes** → you know WHY they're qualified, reference their strongest signal
3. **Selling points** → weave in what makes THIS role compelling for THIS person

### Structure
```
[Specific observation about THEIR background] + [Connection to OUR opportunity/challenge]
```

### Good Opener Examples (adapt to role type)

**Technical role: DevOps lead who grew team 2→12. Selling point: reliability.**
```
Growing a team from 2 to 12 at Tipalti while hitting scale tells me you know how to build. We need exactly that kind of builder for our platform group.
```

**Marketing role: VP who scaled demand gen at a fintech. Selling point: CEO access.**
```
Taking demand gen from zero to $15M pipeline at Lemonade tells me you know how to build a growth engine. Our CEO is looking for exactly that to lead marketing at Obligo.
```

**Leadership role: Director who built a function from scratch. Selling point: ownership.**
```
Building the entire marketing function at Cedar from the ground up is rare. We need someone who can do the same for Obligo as we scale into enterprise.
```

### NEVER Use (instant disqualify)
- "I noticed your profile"
- "I came across your background"
- "I was impressed by"
- "I hope this message finds you well"
- "Your profile really stood out"
- "We have an exciting opportunity"
- "Reaching out because"
- Any opener that could apply to anyone (not specific to THIS person)
- Em dashes (--)

### Opener Variety
Rotate through these angles -- don't repeat the same pattern for consecutive candidates:

**Company transition:** "Your move from [A] to [B] tells me you thrive in [trait]. We're building that at [company]."
**Specific skill:** "Scaling [tech] at [company] is no joke. We're solving the same problem at [company]."
**Team growth:** "Growing a team from [N] to [M] while keeping quality is rare. That's exactly what we need."
**Career arc:** "From [early role] to [current role] in [N] years shows you ship."
**Community/OSS:** "Maintaining [project] while leading [team] shows the kind of craft we value."
**Education:** "Technion is no joke. Curious how you apply that rigor day-to-day at [company]."
**Military:** "8200 sets a high bar. We need that kind of thinking for our [challenge]."

### Sender Tone Matching
- **From CTO/VP R&D**: peer tone -- "We're building X, curious if..." 
- **From recruiter**: professional but warm -- "Your background in X caught my eye..."
- **From hiring manager**: direct -- "I'm building the [team] and your [skill] is what I need."

## Pipeline Integration

In the pipeline, this skill is called after screening for each qualified candidate:

1. Screening saves `score`, `result`, `notes` (no opener)
2. Then for each qualified candidate, generate subject + opener
3. Save opener to pipeline_candidates.email_opener via:
```bash
echo '{"opener": "<the opener text>"}' | python -m pipeline.screen_step save_result <position_id> <linkedin_url>
```
4. GEM push puts opener into nickname field → available as `{{nickname}}` in email sequences
