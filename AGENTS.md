# AGENTS.md · MateScope

> **English — single source of truth** · [中文镜像](AGENTS_zh.md)

Keep this file short: stable project context and working rules only. Put detailed architecture, implementation plans, and progress in dedicated documents when needed.

## Project context

MateScope is a self-hosted, mobile-first, responsive PWA for exploring TeslaMate vehicle data. It provides its own authentication and aims to cover most TeslaMate Grafana dashboard use cases with improved navigation, maps, and analysis.

Repository: [t-liu93/matescope](https://github.com/t-liu93/matescope). SSH remote: `git@github.com:t-liu93/matescope.git`.

- Frontend: React + TypeScript + Vite. Preferred libraries: Mantine, TanStack Query, Leaflet / React Leaflet, Recharts, and OpenAPI-generated client types.
- Backend direction: Python + FastAPI. Detailed architecture, dependency versions, storage choices, and directory structure remain open until documented during scaffolding.
- Deployment: an independent containerized app, integrated with an existing TeslaMate deployment through Docker networking and the owner's installation scripts.
- Historical data: backend-only access to TeslaMate PostgreSQL using a dedicated read-only account. Optional MQTT supplies the latest vehicle state. Keep MateScope-owned data separate.
- Initial scope: validate the scaffold and real data access before building the daily-use dashboard.

Start with the [documentation index](docs/README.md), [roadmap](docs/plan/roadmap.md), and relevant milestone. The roadmap owns progress; insights provide background rather than additional agent rules.

## Language and documentation

- Reply in the user's language. Tracked agent-authored project documentation, including README files, guides, and plans, must have English and Chinese versions. Local material in `development-notes/`, including step implementation/review/fix reports and milestone implementation reports, is written in Chinese only; no English mirror or language-pair link is required.
- English is the single source of truth. Keep the original English filename and insert `_zh` before the extension for its faithful Chinese mirror: `README.md` / `README_zh.md`, `AGENTS.md` / `AGENTS_zh.md`, or `plan.md` / `plan_zh.md`. If versions disagree, English takes precedence; translations must not introduce independent rules.
- Update each pair in the same change. Directly below each H1, link to the other language. Other documentation links should target the same language where a mirror exists.
- Do not assume planned files, commands, or features exist. Read the relevant implementation and available design documents before making changes.

- Put temporary implementation notes, per-task walkthroughs, review findings, validation reports, and other intermediate material prepared for the owner under the repository-root `development-notes/` directory. Ignore the entire directory in Git and Docker build contexts; never force-add, commit, or copy those notes into tracked documentation. These local reports serve both cold-start agents and the owner’s manual inspection.
- Keep tracked documentation focused on stable project information, agreed plans, and concise roadmap progress. A task completing or a note containing useful commands does not make it permanent documentation; promote material into tracked docs only when the user explicitly requests it. Put detailed per-task execution/review evidence in `development-notes/`, not README files or milestone plans.

## Working rules

- Implement the requested scope in small, reviewable changes. Preserve unrelated user work and avoid speculative abstractions or refactors.
- Resolve routine implementation choices autonomously; ask when a missing decision materially changes scope or behavior.
- Never commit secrets or real vehicle/location data. Use synthetic or sanitized fixtures. Do not expose database credentials to the frontend.
- Do not modify TeslaMate data or schema from the application. Database role setup is a separate, explicit deployment step. Do not modify a live deployment without authorization.
- Verify relevant behavior with the checks available in the repository. Add meaningful tests for risky logic; run a real image build when changing container packaging. Report what passed, what was not run, and any remaining limitations.
- Keep generated API types synchronized when the contract changes. Treat time zones, units, missing data, and measured versus estimated metrics explicitly.
- Keep commits scoped and use English Conventional Commits without AI attribution. Follow the commit authorization and history rules below.

## Git and commit lifecycle

- Solo development may proceed on `main`; branches and pull requests are optional unless requested. Never include unrelated changes in a commit.
- Outside orchestration, commit only when explicitly requested. Push, publish releases/tags, or rewrite already-pushed history only with explicit authorization covering that action.
- An explicit request to run orchestrated implementation authorizes local implementation commits, fixup commits, and per-step squash of unpublished commits within the requested scope. It does not by itself authorize pushing or publishing.
- **Implement:** after the step's required checks pass, create one implementation commit and record its SHA and preceding commit as the step's boundaries.
- **Rework:** commit each review-driven repair with `git commit --fixup=<implementation-sha>`. Keep fixes scoped to the same step; include any new validation in the report.
- **Wrap up:** after review and integration checks pass, combine the step's implementation and fixups into one commit before proceeding. For a step with a preceding commit, use `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <preceding-sha>`. For a root implementation commit, use `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash --root` only after verifying the affected history belongs to that step.
- Autosquash folds fixups into their targets; it does not combine multiple ordinary implementation commits. If a step has several such commits, explicitly arrange a non-interactive rebase todo to squash only that step's commits. Preserve commits belonging to other steps or contributors.
- Before rewriting, verify a clean worktree, exact commit boundaries, and that all affected commits are unpublished. Never automatically rewrite already-pushed history or force-push. If rewriting is not authorized, preserve history and use follow-up commits.
- At milestone completion, retain one commit per completed step, not a single squash of the entire milestone. Produce a Chinese-only milestone implementation report in `development-notes/`, covering completed and pending work, verification, limitations, and manual walkthrough steps with expected results. Never describe pending acceptance as completed.

## Agent orchestration

Single-agent work is the default. Start a delegated implementation/review loop only when the user explicitly requests orchestration or delegation. This section defines that workflow; it does not activate it automatically.

### Model defaults

| Role | Preferred model | Reasoning |
| --- | --- | --- |
| Orchestrator / reviewer | `gpt-6-astra` | `medium` |
| Implementer / fixer | `gpt-5.6-luna` | `medium` |

- These are adjustable project defaults, not claims of equivalent quality. Explicit user selections take precedence.
- Use Astra `high` for difficult architectural decisions, subtle correctness issues, or security-sensitive review when additional reasoning is warranted.
- Give Luna bounded tasks with concrete acceptance criteria. Escalate implementation/fixes to `gpt-5.6-terra` with `medium`, or Astra for harder work, when complexity or repeated failures warrants it. Route authentication boundaries, database permissions, and ambiguous statistical logic to a stronger model from the start when appropriate.
- Use actual harness model/effort settings, not role names in prompts. If unavailable, disclose the limitation and use a supported alternative consistent with the user's preferences. Do not claim to have changed the parent session's model unless the harness actually did so.

### Reports and cold-start handoff

- For every atomic step, save an implementation report after implementation, a review report after each review round, and a fix report after each repair round, before handing off. Use `development-notes/<milestone>/<step>/implementation-report.md`, `review-report-r<N>.md`, and `fix-report-r<N>.md`; write all contents in Chinese and retain each round. Save the milestone implementation report at `development-notes/<milestone>/implementation-report.md`.
- Reports are the sole acceptance and handoff record. Together with the corresponding code diff, they are the only task-specific inputs to cold-start reviewers and fixers; do not pass conversation history, oral summaries, or the orchestrator’s preferred verdict. Repository rules still apply, and agents must inspect relevant code and independently verify report claims. Put the agreed scope and acceptance criteria in the report so the next agent needs no prior conversation.
- An implementation report records the step ID, scope/exclusions, acceptance criteria, changes/files, preceding and implementation SHAs, exact diff boundaries, checks and outcomes, unrun checks, limitations, and manual verification. A review report identifies the reviewed SHAs and records each finding’s ID, priority, location, reproduction/evidence, expected behavior, and required correction, plus independent checks and the acceptance verdict; explicitly state when there are no actionable findings. A fix report maps each finding to its correction, fixup SHA/diff, validation, and unresolved items.
- A fresh reviewer receives the implementation report, any subsequent review/fix reports, and the cumulative step diff. A fresh fixer receives those reports, the latest actionable review report, and the corresponding cumulative/repair diffs. Missing or stale scope, evidence, or commit boundaries must be corrected in the reports before handoff. After squash, record the final SHA and old-to-new mapping so later acceptance uses the actual current diff.

### Delegated workflow

1. Define a bounded task, acceptance criteria, relevant files, and required checks. Parallelize only independent work with clear file ownership.
2. The implementer completes the task and required checks; create the implementation commit and Chinese implementation report before review. The orchestrator owns Git mutations when agents share a worktree; do not run concurrent commits or rebases there.
3. A cold-start reviewer uses the report-and-diff handoff above, inspects the code, independently verifies critical behavior, and writes a Chinese review report.
4. A cold-start fixer addresses the review report’s actionable findings, creates a fixup for the step’s implementation commit, and writes a Chinese fix report. A fresh reviewer reviews the revised reports and diff and records the next review round. Escalate after two unsuccessful attempts at the same issue rather than repeating unchanged instructions. After five repair rounds, stop the loop and report unresolved findings to the user.
5. Once no actionable findings remain, the orchestrator verifies integration, performs the authorized per-step squash, verifies the resulting diff and history, and updates the report with the final boundaries and acceptance result before advancing. Review supplements testing; it does not replace it.

## Maintaining this guide

Update both language versions when stable decisions change. Keep milestone status, transient research, machine-specific paths, and other repositories' instructions out of this guide. Keep temporary development commands and execution evidence in `development-notes/`; do not link stable guidance to ignored local files.
