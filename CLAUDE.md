# AGENTS.md

Single source of truth for AI agents (Claude Code today; Codex CLI in future) working on this repository.

## Project

`daily-sourcing-autopilot-e2e` is a Python pipeline that runs once a day and:

1. Searches Crustdata for new candidate profiles for each active position.
2. Pre-filters them against a Google Sheet of past candidates and blacklisted companies.
3. Enriches the surviving profiles via Crustdata.
4. Has Claude screen each profile (binary GO/NO GO) using position-specific rules.
5. Looks up personal emails (GEM first, SalesQL second).
6. Pushes qualified candidates with email to GEM (the ATS).
7. Finalizes stats and posts a Slack summary.

The mechanical (non-AI) steps live in `pipeline/` and are orchestrated by `run_pipeline.py`. Screening itself is performed by Claude Code in the scheduled task — not by the script.

## Repository map

| Path | What lives there |
|---|---|
| `run_pipeline.py` | Subprocess orchestrator. Invokes each pipeline step as `python -m pipeline.<step>` and parses JSON stdout. |
| `pipeline/` | One module per step (search, pre_filter, enrich, email, gem, finalize, slack, …). Each prints JSON to stdout. |
| `integrations/` | API clients: `crustdata.py`, `gem.py`, `salesql.py`. |
| `core/` | Supabase client, schema helpers, `normalizers.py` (LinkedIn URL normalization). |
| `.claude/skills/` | Claude Code skills (15 of them) — domain knowledge, screening rubrics, runbooks. Each has its own `SKILL.md`. |
| `tests/` | Pytest tripwires: contract tests for the JSON stdin/stdout protocol + integration client tests. |

## Hard rules (do not violate)

These are extracted from existing skill files. If two rules ever conflict, defer to the skill file cited as the source.

1. **Never run `python run_pipeline.py` while the scheduled Claude task is running.** Two orchestrators cannot run simultaneously. Source: `.claude/skills/pipeline-orchestrator/SKILL.md`.
2. **Position-specific screening skill is mandatory.** If `.claude/skills/screening-<position-id>/SKILL.md` does not exist for a position, halt the pipeline and send a Slack error — do not screen. Source: `.claude/skills/pipeline-orchestrator/SKILL.md` and `.claude/skills/screening/SKILL.md`.
3. **Pass `exclude_profiles` on every Crustdata search.** Without it, daily searches return the same profiles repeatedly, wasting credits and corrupting dedup. Source: `.claude/skills/crustdata-mcp/SKILL.md`.
4. **Screening is binary GO / NO GO with evidence-based verification.** No probabilistic "maybe" outputs. The non-invention rule applies: never invent facts not present in the candidate's profile. Source: `.claude/skills/screening/SKILL.md`.
5. **Pipeline steps communicate via JSON on stdin/stdout.** Step modules in `pipeline/` must not print anything to stdout other than a single JSON document. Logs go to stderr. Source: architectural invariant of `run_pipeline.py:run_step`.

## Reviewer roles

- **Claude Code = builder.** Plans, edits files, runs tests, commits.
- **Codex CLI = pessimist auditor.** *(Deferred — not yet wired up. When activated, see `docs/codex-audit-workflow.md`.)*
- A single agent must not both implement and audit the same change.

### Codex persona block (used verbatim by the future audit script)

> Act as a pessimistic reliability/security reviewer. Flag race conditions, silent error swallowing, missing retries on network calls, JSON contract violations between pipeline steps, secrets in logs, and any change that bypasses position-specific screening rules.

## Development conventions

- Python 3.11. Runtime deps in `requirements.txt`; dev/test deps in `requirements-dev.txt`.
- Run `pytest -q` before declaring a code change done. Tests are silent tripwires — they should stay green.
- Auto-formatting via `ruff format` runs on commit through pre-commit. It never blocks commits; it just keeps diffs clean.
- No `mypy`, no commit-blocking lint, no CI by design — kept lightweight for a non-technical owner.
