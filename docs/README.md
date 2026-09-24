# Documentation

> **English — single source of truth** · [中文](README_zh.md)

Read the [agent guide](../AGENTS.md), the roadmap, and the relevant milestone before implementation. English documents are authoritative; update each Chinese `_zh` mirror in the same change.

| Document | Purpose |
| --- | --- |
| [Add to existing TeslaMate Compose](operations/existing-teslamate-compose.md) | Add MateScope in the existing installation directory and start only its service |
| [PostgreSQL read-only setup](operations/postgresql-readonly.md) | Production catalog preflight and explicit dedicated-account preparation |
| [M1 use, upgrade, and acceptance](operations/m1-dashboard.md) | Use the M1 dashboard, prepare a safe upgrade, and perform the owner walkthrough |
| [Reference projects](insight/references.md) | Lessons from the owner's projects; context, not inherited requirements |
| [TeslaMate insight](insight/teslamate.md) | Upstream observations, intended product, and questions to validate |
| [Roadmap](plan/roadmap.md) | Scope, technology decisions, and the authoritative milestone status table |
| [M0](plan/milestones/M0.md) | Initial scaffold and data-access validation outline |
| [M1](plan/milestones/M1.md) | Daily dashboard and complete-history design, data contracts, and 36 atomic implementation steps |
| [Milestone template](plan/milestones/_TEMPLATE.md) | Structure for future implementation tasks and acceptance criteria |

Insights record evidence and reasoning. The roadmap and milestone documents record project decisions; explicitly marked proposals remain open. Create detailed later milestones when their scope is understood. Do not treat an outline as a completed implementation or invent commands before the scaffold exists.

Keep operational secrets and actual vehicle records out of documentation. Public documentation must be usable without access to the owner's machine or other private repositories. `LICENSE` retains the standard English MIT legal text; the bilingual README identifies that authoritative license.
