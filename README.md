# MateScope

> **English — single source of truth** · [中文](README_zh.md)

MateScope is a self-hosted, mobile-first dashboard for [TeslaMate](https://github.com/teslamate-org/teslamate). Its M1 implementation makes complete vehicle history, trips, charges, maps, and selected recent values easier to explore on phones and desktops through a responsive PWA with its own authentication.

TeslaMate continues collecting vehicle data. MateScope will read historical data through a backend using a dedicated read-only PostgreSQL account, with optional MQTT for the latest vehicle state. It will run as an independent container alongside an existing TeslaMate deployment.

## M1 use and status

Open MateScope using its configured HTTPS origin, sign in, select a vehicle and a calendar range, then use **Overview**, **Trips**, and **Charges**. The date picker includes All history; changing its range updates period summaries and records, while Overview's latest recorded values remain independent of that range. Details provide bounded chart series and return to the prior list state.

For installation, read the [existing TeslaMate Compose guide](docs/operations/existing-teslamate-compose.md), then prepare the [dedicated PostgreSQL read-only account](docs/operations/postgresql-readonly.md). M1's minimal additional read-only grants are explicit: do not apply them until the owner reviews the upgrade procedure. The application never modifies TeslaMate data or schema.

Implementation and verification progress is tracked in the [roadmap](docs/plan/roadmap.md). M1 remains subject to owner acceptance, image publication, and separately authorized production deployment. Its browser automation covers Chromium and WebKit but does not replace a real-device walkthrough.

## Documentation

- [Documentation index](docs/README.md)
- [Project insight](docs/insight/teslamate.md)
- [Roadmap and milestones](docs/plan/roadmap.md)
- [M1 use, upgrade, and acceptance guide](docs/operations/m1-dashboard.md)
- [Agent guide](AGENTS.md)

## License

[MIT](LICENSE), copyright 2026 Tianyu Liu. Third-party components retain their respective licenses. MateScope is an independent project, not an official Tesla product.
