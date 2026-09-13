# TeslaMate and MateScope insight

> **English — single source of truth** · [中文](teslamate_zh.md)

Research snapshot: 2026-09-13. Upstream `main` and documentation can change; validate against the deployed release during M0.

## Existing system

TeslaMate collects vehicle data and its standard deployment includes PostgreSQL, Grafana, and an MQTT broker. Grafana connects to PostgreSQL for dashboards. This makes a separate read-only presentation layer a practical integration point. [Official deployment](https://docs.teslamate.org/).

The inspected web router exposes logging resume/suspend operations under `/api`, and a separate GPX export route, but not a complete historical dashboard query API. Sharing a Docker network does not add missing API capabilities. [Router source](https://github.com/teslamate-org/teslamate/blob/main/lib/teslamate_web/router.ex).

MQTT publishes vehicle state, battery, charging, and location values. It can supplement historical queries with the latest known state; it is not a historical query store or a guarantee that sleeping-vehicle data is fresh. [MQTT integration](https://docs.teslamate.org/docs/integrations/mqtt/).

Official dashboards cover drives, charges, efficiency, battery health, range, states, timelines, vampire drain, and visited locations. Use these as a future coverage inventory rather than promising immediate parity. [Dashboard overview](https://docs.teslamate.org/docs/screenshots/).

## Intended product

MateScope will provide its own authenticated API and a mobile-first PWA. The browser talks only to MateScope; its backend reads historical TeslaMate data through a dedicated read-only account. Optional MQTT adds latest-state updates. Accounts, preferences, and any later annotations belong to MateScope's own storage.

The first usable journeys are finding a trip, viewing its route, inspecting a charge, and understanding recent usage. Later analysis can add period comparisons, unified activity timelines, and explanations of changes. Vehicle control and replacing TeslaMate's collector are outside the initial scope.

## Deployment and development

The owner's TeslaMate runs on a separate VPS. The development machine has WireGuard connectivity, but production PostgreSQL should not be exposed for local development. Use explicit synthetic data locally; validate real access by deploying MateScope into the VPS Docker network. MQTT is external in the owner's installation. The owner-maintained installer can later supply Compose and reverse-proxy integration.

## Questions to resolve with evidence

- Record the deployed TeslaMate version and required schema fields; define how unsupported schemas are reported.
- Compare selected results with upstream dashboard SQL and Grafana records using identical vehicle, time range, units, and timezone.
- Define energy and cost calculations, missing values, and measured versus estimated quantities before building aggregates.
- Bound query cost and trajectory size; assess long-trip rendering on a phone before planning lifetime maps.
- Choose the authentication/session implementation, MateScope storage, map tile provider, and PWA caching policy. Show freshness explicitly and avoid silently falling back to synthetic data when real access fails.

The recommended starting point is read-only PostgreSQL plus optional MQTT. This is a project decision based on the observed interface coverage, not a claim that no alternative API integration can ever work.
