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

## Language and documentation

- Reply in the user's language. All agent-authored project documentation, including README files, guides, plans, and reports, must have English and Chinese versions.
- English is the single source of truth. Keep the original English filename and insert `_zh` before the extension for its faithful Chinese mirror: `README.md` / `README_zh.md`, `AGENTS.md` / `AGENTS_zh.md`, or `plan.md` / `plan_zh.md`. If versions disagree, English takes precedence; translations must not introduce independent rules.
- Update each pair in the same change. Directly below each H1, link to the other language. Other documentation links should target the same language where a mirror exists.
- Do not assume planned files, commands, or features exist. Read the relevant implementation and available design documents before making changes.

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
- At milestone completion, retain one commit per completed step, not a single squash of the entire milestone. Report completed work, verification, and remaining manual walkthrough steps in bilingual documentation when producing a written report.

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

### Delegated workflow

1. Define a bounded task, acceptance criteria, relevant files, and required checks. Parallelize only independent work with clear file ownership.
2. The implementer completes that task, runs required checks, creates the implementation commit, and reports changes, checks, limitations, and the commit SHA. The orchestrator owns Git mutations when agents share a worktree; do not run concurrent commits or rebases there.
3. A fresh reviewer receives the requirements, diff, and factual implementation report, without the implementer's conversation or the orchestrator's preferred verdict. Review the code and independently verify critical behavior.
4. A fixer addresses actionable findings and creates a fixup for that step's implementation commit; a fresh reviewer reviews the revised result again. Escalate after two unsuccessful attempts at the same issue rather than repeating unchanged instructions. After five repair rounds, stop the loop and report unresolved findings to the user.
5. Once no actionable findings remain, the orchestrator verifies integration, performs the authorized per-step squash, verifies the resulting diff and history, and reports the result before advancing to the next task. Review supplements testing; it does not replace it.

## Maintaining this guide

Update both language versions when stable decisions change. Keep milestone status, transient research, machine-specific paths, and other repositories' instructions out of this guide. Add verified development commands when the scaffold exists.
