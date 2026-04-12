---
name: screening-autofleet-devops-tl
description: Position-specific screening rules for autofleet-devops-tl. Generated from calibration review with hiring manager on 2026-04-12. Load BEFORE the general screening skill. These rules OVERRIDE the general screening skill when they conflict.
---

# Position-Specific Screening: autofleet-devops-tl

**Generated from calibration review on 2026-04-12. 10 profiles reviewed with hiring manager.**

## How to Use This File

Read this BEFORE screening any candidate for autofleet-devops-tl. Apply these rules IN ADDITION TO the general screening skill. When these rules conflict with the general screening skill, THESE RULES WIN.

## Calibrated Must-Haves (user-validated)

1. **2+ years of leadership experience** -- STRICTLY enforced. Calculate actual months from work history dates. 9 months as TL does NOT qualify even at a great company. Sum ALL leadership roles (TL, Lead, Manager, Group Lead, Director). Must be >= 24 months total.
2. **DevOps/SRE/Platform career track** -- current or recent role must be DevOps, SRE, Platform, or Infrastructure
3. **Israel-based**
4. **Product company DNA** -- at least one recognized product company in career history

## Calibrated Dealbreakers (user-validated)

- Consulting/outsourcing companies as current employer
- Pure overkill: Director/VP at large company managing managers, with only leadership/strategy skills and NO hands-on DevOps tools visible (e.g., Oded Baruch pattern)
- Less than 2 years leadership tenure -- this is a HARD requirement, not soft

## Leadership Tenure Rules -- STRICTLY ENFORCED

**This was the #1 mistake in calibration. Do NOT relax this rule.**

- Calculate leadership months from ACTUAL start_date and end_date in work history
- For current roles: end_date is null → use today's date (2026-04-12)
- Sum ALL roles with leadership titles: TL, Team Lead, Lead, Manager, Head, Group Lead, Director
- Total must be >= 24 months
- Example REJECT: Arnold Yahad -- DevOps SRE TL at Dynamic Yield from Jul 2025 (9 months). Was Senior IC for 1.8yr before. 9 months < 24 months = NOT QUALIFIED despite great company.
- Example QUALIFY: Danniel Shalev -- DevOps TL at Skai + DevOps Manager at UVeye = 8+ years leadership total.
- **Log the calculation every time:** `[screen] Leadership tenure: TL at X (date-date, Nmo) + Manager at Y (date-date, Nmo) = Nmo total. Requires 24mo. MET/NOT MET.`

## Overkill Rules -- NUANCED

**Director level is NOT an automatic reject for DevOps TL roles.**

- **QUALIFY Directors if:** they grew within one company (Eng→TL→Director trajectory), OR they come from a small/mid company where Director = hands-on TL, OR everything else fits strongly (user said "worth trying")
- **REJECT Directors if:** they have ONLY leadership/strategy skills (Zero-Trust, GRC, AI Strategy), NO hands-on DevOps tools (no K8s, Terraform, cloud), AND are at a large enterprise (1000+ people)
- Examples from calibration:
  - Avi Juran (Director at ActiveFence): QUALIFIED -- strong fit otherwise, FinOps, IaC, product companies
  - Shiran Itzhaki (Director at WSC Sports): QUALIFIED -- grew from Engineer→Director at same company
  - Oded Baruch (Director at Proofpoint): REJECTED -- only strategy skills, no hands-on tools
  - Koby Sayag (Director at Cato): REJECTED -- legacy skills (HTML, SQL, Windows Server), no modern DevOps

## Thin Profiles -- DON'T AUTO-REJECT

**Crustdata enrichment can miss skills. A thin profile is NOT a bad profile.**

- If title says DevOps TL/Manager AND they have 2+ years in that role AND the company is a real product company → QUALIFY with a note "thin profile -- verify stack in call"
- Example from calibration:
  - Racheli Lotvin (zero skills listed, CyberArk): QUALIFIED at 8 -- title progression HPE→CyberArk is strong enough
  - Andrey Bistrinin (zero skills listed, Anzu.io): QUALIFIED at 6 -- 3yr TL + 5yr DevOps, LinkedIn shows relevant skills even though Crustdata missed them
  - Yehuda Levi (thin skills, Taboola): QUALIFIED at 6 -- SRE TL at Taboola, missing Terraform/cloud but company and seniority enough

## Company Signals (from calibration review)

**For DevOps roles, companies don't need to be "wow" -- relevant is enough.**

**Positive (good fit):**
- Tier 1: Cato Networks, CyberArk, Taboola, Dynamic Yield, ironSource, Wiz, Monday.com, Check Point
- Tier 2: UVeye, Skai, ActiveFence, WSC Sports, Anzu.io, Imperva, Tufin
- General: any product company building real software where DevOps owns infrastructure

**Early career telco/legacy is OK if recent career is product:**
- Danniel Shalev started at Bezeq (telco) but last decade at Skai + UVeye → QUALIFIED
- What matters is the RECENT trajectory, not where they started

**Reject if:**
- Overkill Director at large enterprise with only strategy skills
- Pure consulting/outsourcing as current role

## Scoring Calibration (from real examples)

**What an 8 looks like:**
- Racheli Lotvin: DevOps Platform Group Manager at CyberArk. Clear HPE→CyberArk leadership ladder. Tier-1 company.

**What a 7 looks like:**
- Chen Anidam: Principal SRE TL at Cato Networks. Multiple TL stints, good career stability. Companies relevant for DevOps even if not "wow".
- Danniel Shalev: DevOps Manager at UVeye. 15yr DevOps, 8yr TL. K8s, Terraform, Pulumi. Early Bezeq OK.
- Avi Juran: Director Platform Eng at ActiveFence. Might be overkill but strong fit otherwise. Worth trying.

**What a 6 looks like:**
- Yehuda Levi: SRE TL at Taboola. Thin skills but right role at right company.
- Shiran Itzhaki: Director DevOps at WSC Sports. Full growth path. Director might be overkill but worth trying.
- Andrey Bistrinin: TL Infra & SRE at Anzu.io. Zero skills from Crustdata but 3yr TL tenure. Benefit of doubt.

**What gets REJECTED:**
- Arnold Yahad (I scored 6, user rejected): DevOps SRE TL at Dynamic Yield. Great company, right title. BUT only 9 months as TL. **Leadership tenure is a hard requirement -- 2+ years, no exceptions.**
- Oded Baruch (I scored 5, user agreed): Director at Proofpoint. Overkill + only strategy skills + no hands-on tools.
- Koby Sayag (I scored 5, user agreed): Director at Cato. Overkill + legacy skills (HTML, SQL, Windows Server).

## Key Takeaway for This Position

**The hiring manager values LEADERSHIP TENURE and CAREER RELEVANCE over tool-specific skills.**

Priority order when screening:
1. Do they have 2+ years of ACTUAL leadership? (most important -- strictly enforced)
2. Is their career DevOps/SRE/Platform focused?
3. Are they at a relevant product company? (doesn't need to be tier-1)
4. Seniority fit? (Director OK to try if grew organically, reject if overkill strategy-only)
5. Specific tools (K8s, Terraform, cloud)? (least important -- thin profiles OK if tenure and company are strong)
