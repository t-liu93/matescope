# MateScope roadmap

> **English — single source of truth** · [中文](roadmap_zh.md)

This is the scope and milestone overview, not a frozen implementation specification. The table below is the authoritative progress record. Detail each milestone just before implementation using the [template](milestones/_TEMPLATE.md).

## Direction and decisions

Confirmed direction: a self-hosted, responsive React PWA with independent authentication, a Python/FastAPI backend, read-only TeslaMate history, optional MQTT, and an independently deployable container. The product aims to replace TeslaMate Grafana with a modern, informative interface that makes common information easy to reach on phones, tablets, and desktops. The owner will integrate deployment through their own installer. M0 established the data-access foundation; M1 designs the daily-use dashboard.

| Area | Current direction | Still to validate during scaffolding |
| --- | --- | --- |
| Frontend | React + TypeScript + Vite; prefer Mantine | Compatible versions, mobile shell |
| Data UI | Prefer TanStack Query, Leaflet/React Leaflet, Recharts | Trajectory/series budgets, touch interaction, tile provider |
| API | FastAPI, generated OpenAPI types and typed frontend client | Route layout, errors, code generation and drift check |
| Historical storage | TeslaMate PostgreSQL, dedicated read-only role, psycopg 3; synthetic PostgreSQL locally | Actual VPS schema compatibility and bounded queries |
| Application storage | Persistent SQLite with SQLAlchemy 2/Alembic; encrypted connection settings and CLI backup/restore | Migration, restart, and recovery verification |
| Setup | Bilingual web onboarding; single administrator; PostgreSQL/MQTT/SMTP settings | Resume/skip/edit behavior and connection tests |
| Tooling | Python 3.13, Node 22, uv, pnpm | Compatible pinned releases, lockfiles, reproducible checks |
| Packaging | One non-root image serving API and compiled frontend; local/production Compose | Health checks, isolated persistent data, proxy configuration |
| Delivery | GitHub Actions; version-tag releases to GHCR for amd64/arm64 | Publish tested artifacts; prereleases never move latest; VPS pull verification |

## Constraints

- Never migrate or write to TeslaMate tables from MateScope. Limit the role to required data; account setup is a separate deployment operation.
- Use synthetic fixtures for local development. Do not publish production data, open the production database port, or require live VPS access for normal tests.
- Protect vehicle-data endpoints with application authentication before real-data deployment. Distinguish invalid credentials, incompatible schemas, empty results, and temporary connection failures without exposing secrets.
- Compare metrics against upstream behavior and document intentional differences. Handle timezones, units, missing values, and estimation explicitly.
- Bound database work and client payloads. Keep UI resources and sensitive data caching policies separate.
- Keep documents bilingual and API contracts synchronized. Follow the [agent guide](../../AGENTS.md) for execution and Git rules.

## Milestones and progress

| Milestone | Outcome | Status |
| --- | --- | --- |
| [M0](milestones/M0.md) | Local Compose preview, web onboarding, read-only data, backup/restore, CI and dual-platform release, VPS acceptance | Complete: the owner confirmed the walkthrough and M0 closure. Local validation and image delivery are recorded; this closure does not assert that every originally planned manual test was individually performed. |
| Post-alpha enhancements | Password-manager hints, installable PWA, optional TOTP and recovery codes | Owner walkthrough completed and 2FA creation/use confirmed. `v0.1.0-alpha.2` published; main CI, release checks, native amd64/arm64 image validation, and anonymous pull passed. Offline vehicle data is hidden and cleared. |
| [M1](milestones/M1.md) | Concise overview, complete-history date picker, trip/charge summaries and charts, responsive navigation; reuse existing PWA foundations | Orchestrated implementation authorized for all 36 bounded steps: T01–T11 passed implementation, independent review, and integration checks; T12–T36 not started. PostgreSQL-only data in M1; continuous MQTT and advanced analysis excluded. Human acceptance, publication, and deployment remain pending. |
| M2 | Coverage inventory and prioritized statistics: efficiency, range/battery, parking drain, locations | Proposed; detail after M1 |
| M3 | Additional comparisons, activity timeline, and performance/usability improvements based on use | Proposed; scope not committed |

Release baseline: [`v0.1.0-alpha.2`](https://github.com/t-liu93/matescope/releases/tag/v0.1.0-alpha.2), image `ghcr.io/t-liu93/matescope:0.1.0-alpha.2`, source `646fdf82d2fdde7a46d7199e51b8bd01be8d926a`. Release publication does not establish that this version has been deployed on the VPS.

M0 includes MQTT/SMTP configuration and explicit testing, not live dashboards or notification rules. No later milestone implies full Grafana parity. Track local validation, image publication, and VPS acceptance separately; all three must pass before M0 is complete.

## Verification and delivery

Compose and image checks start in M0-T01. Branch/PR CI validates the application with synthetic services and browser tests; version tags run complete checks and both-platform runtime validation before publishing the tested images to GHCR. The user can optionally preview locally; formal manual acceptance is a consolidated VPS walkthrough using a published version or digest. CI does not automatically SSH into production. Record commands and outcomes once they exist; planned commands in M0 are not yet available tooling.

Related context: [upstream insight](../insight/teslamate.md) and [reference lessons](../insight/references.md).
