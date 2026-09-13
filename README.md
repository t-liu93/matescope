# MateScope

> **English — single source of truth** · [中文](README_zh.md)

MateScope is a planned self-hosted, mobile-first dashboard for [TeslaMate](https://github.com/teslamate-org/teslamate). It aims to make vehicle history, trips, charging, maps, and statistics easier to explore on phones and desktops through a responsive PWA with its own authentication.

TeslaMate continues collecting vehicle data. MateScope will read historical data through a backend using a dedicated read-only PostgreSQL account, with optional MQTT for the latest vehicle state. It will run as an independent container alongside an existing TeslaMate deployment.

## Status

Early planning; there is no runnable application or published container image yet. The first milestone will validate the application scaffold and real data access before expanding the dashboard.

The technology direction is React + TypeScript + Vite on the frontend and Python + FastAPI on the backend. Supporting libraries and architecture are tracked in the [roadmap](docs/plan/roadmap.md). Setup instructions will be added after they are verified.

## Documentation

- [Documentation index](docs/README.md)
- [Project insight](docs/insight/teslamate.md)
- [Roadmap and milestones](docs/plan/roadmap.md)
- [Agent guide](AGENTS.md)

## License

[MIT](LICENSE), copyright 2026 Tianyu Liu. Third-party components retain their respective licenses. MateScope is an independent project, not an official Tesla product.
