# Reference projects

> **English — single source of truth** · [中文](references_zh.md)

These notes summarize projects inspected during initial planning on 2026-09-13. They are self-contained observations, not dependencies or instructions to access those repositories in later sessions. No source code was copied. Dependency ranges observed there are not a version lock for MateScope.

| Reference | Observed patterns | Useful lesson for MateScope |
| --- | --- | --- |
| just-another-invoice | Short agent guide; `docs/insight/`, a roadmap, per-milestone documents, and a task template; English/Chinese pairs | Separate background, stable rules, and actionable plans; deliver small end-to-end slices |
| omniventory | React/TypeScript/Vite, Mantine, centralized theme, responsive shell, i18n, PWA configuration, OpenAPI-generated types and client, Python/FastAPI | Reuse the organizational ideas for themes, UI states, authentication, and typed contracts |
| home-automation | React/TypeScript/Vite, Mantine, TanStack Query, Leaflet/React Leaflet with heatmaps and clustering, Recharts | Keep map rendering behind a typed component; bound time-series queries and preserve gaps for missing values |
| Linux-install-helper | Owner-maintained TeslaMate Compose generation, external MQTT configuration, host Nginx HTTPS proxy, PostgreSQL internal to Docker networking | Supply a standalone MateScope deployment example; later integrate it through the owner's installer |

## What to carry forward

- Keep maps and charts encapsulated so their rendering libraries can change without changing the API contract.
- Design explicitly for mobile navigation and touch interaction; a responsive component library alone does not deliver that experience.
- Generate frontend types from the backend's OpenAPI and verify that the generated contract remains synchronized.
- Keep a roadmap overview and expand the next milestone into bounded tasks with acceptance criteria and reproducible verification.

## What requires fresh decisions

The references use different React and Mantine major versions. Select and test a compatible set during scaffolding instead of combining dependency files. Inventory workflows and invoicing-specific architecture are outside MateScope's scope.

The inspected installer regenerates its Compose file from a template. Integration can be implemented in that installer because the owner controls it; it is not a requirement to alter TeslaMate upstream. Local templates do not establish the live VPS version, network name, or credentials.

Future code reuse requires checking the source license and adapting behavior to MateScope. No other repository or local filesystem path is required to follow this plan.
