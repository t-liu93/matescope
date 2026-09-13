# MateScope

> [English — 唯一权威来源](README.md) · **中文**

MateScope 是一个规划中的自托管、移动端优先的 [TeslaMate](https://github.com/teslamate-org/teslamate) Dashboard。它希望通过自带鉴权的响应式 PWA，让用户更方便地在手机和桌面上探索车辆历史、行程、充电、地图和统计数据。

TeslaMate 继续负责采集车辆数据。MateScope 将通过后端使用独立只读 PostgreSQL 账号读取历史数据，并可选接入 MQTT 获取车辆最新状态。它将作为独立容器，与现有 TeslaMate 部署一起运行。

## 状态

实现与验证进度在[路线图](docs/plan/roadmap_zh.md)中跟踪。

技术方向是前端 React + TypeScript + Vite，后端 Python + FastAPI。配套库和架构在[路线图](docs/plan/roadmap_zh.md)中跟踪。

## 文档

- [文档索引](docs/README_zh.md)
- [项目洞察](docs/insight/teslamate_zh.md)
- [路线图与里程碑](docs/plan/roadmap_zh.md)
- [Agent 指引](AGENTS_zh.md)

## 许可证

[MIT](LICENSE)，版权归 Tianyu Liu 所有，2026 年。第三方组件保留各自的许可证。MateScope 是独立项目，并非 Tesla 官方产品。
