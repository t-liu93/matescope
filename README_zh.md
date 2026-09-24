# MateScope

> [English — 唯一权威来源](README.md) · **中文**

MateScope 是一个自托管、移动端优先的 [TeslaMate](https://github.com/teslamate-org/teslamate) Dashboard。其 M1 实现通过自带鉴权的响应式 PWA，让用户更方便地在手机和桌面上探索完整车辆历史、行程、充电、地图和部分最近记录值。

TeslaMate 继续负责采集车辆数据。MateScope 将通过后端使用独立只读 PostgreSQL 账号读取历史数据，并可选接入 MQTT 获取车辆最新状态。它将作为独立容器，与现有 TeslaMate 部署一起运行。

## M1 使用与状态

通过配置好的 HTTPS 源打开 MateScope、登录、选择车辆和日历范围，然后使用**概览**、**行程**和**充电**。日期选择器包含“全部历史”；切换范围会更新周期摘要和记录，而概览的最近记录值保持独立。详情页提供有界曲线，返回时会恢复之前的列表状态。

安装前请阅读[接入现有 TeslaMate Compose 指南](docs/operations/existing-teslamate-compose_zh.md)，然后准备[专用 PostgreSQL 只读账号](docs/operations/postgresql-readonly_zh.md)。M1 所需的最小新增只读授权需要明确执行；请在所有者审查升级步骤后再应用。应用不会修改 TeslaMate 数据或 schema。

实现与验证进度在[路线图](docs/plan/roadmap_zh.md)中跟踪。M1 仍需所有者验收、镜像发布和单独授权的生产部署。浏览器自动化覆盖 Chromium 与 WebKit，但不能代替真机走查。

## 文档

- [文档索引](docs/README_zh.md)
- [项目洞察](docs/insight/teslamate_zh.md)
- [路线图与里程碑](docs/plan/roadmap_zh.md)
- [M1 使用、升级与验收指南](docs/operations/m1-dashboard_zh.md)
- [Agent 指引](AGENTS_zh.md)

## 许可证

[MIT](LICENSE)，版权归 Tianyu Liu 所有，2026 年。第三方组件保留各自的许可证。MateScope 是独立项目，并非 Tesla 官方产品。
