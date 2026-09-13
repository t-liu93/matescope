# TeslaMate 与 MateScope 洞察

> [English — 唯一权威来源](teslamate.md) · **中文**

研究快照：2026-09-13。上游 `main` 和文档可能变化；M0 时需要针对实际部署版本验证。

## 现有系统

TeslaMate 负责采集车辆数据，标准部署包含 PostgreSQL、Grafana 和 MQTT broker。Grafana 连接 PostgreSQL 生成 Dashboard。因此，独立的只读展示层是一个可行的集成切入点。[官方部署文档](https://docs.teslamate.org/)。

检查到的 Web 路由在 `/api` 下提供日志恢复/暂停操作，另有 GPX 导出路由，但没有完整的历史 Dashboard 查询 API。共享 Docker 网络不能补齐缺失的接口能力。[路由源码](https://github.com/teslamate-org/teslamate/blob/main/lib/teslamate_web/router.ex)。

MQTT 发布车辆状态、电池、充电和位置等值。它可以用最新已知状态补充历史查询，但不是历史查询存储，也不保证休眠车辆的数据是新鲜的。[MQTT 集成](https://docs.teslamate.org/docs/integrations/mqtt/)。

官方 Dashboard 覆盖行程、充电、效率、电池健康、续航、状态、时间线、停车掉电和访问地点。将其用作后续覆盖清单，而不是立即承诺功能对齐。[Dashboard 总览](https://docs.teslamate.org/docs/screenshots/)。

## 产品目标

MateScope 将提供独立鉴权 API 和移动端优先的 PWA。浏览器只连接 MateScope；后端通过独立只读账号读取 TeslaMate 历史数据。可选 MQTT 补充最新状态。账号、偏好及以后可能加入的备注放在 MateScope 自身存储中。

首批实用流程是查找行程、查看路线、检查充电以及了解近期用车情况。后续分析可以增加时段对比、统一活动时间线和变化解释。车辆控制和替换 TeslaMate 采集器不在初始范围内。

## 部署与开发

所有者的 TeslaMate 运行在另一台 VPS 上。开发机器通过 WireGuard 连通，但不应为了本地开发开放生产 PostgreSQL。先在本地显式使用合成数据，再将 MateScope 部署到 VPS 的 Docker 网络验证真实访问。所有者的安装使用外部 MQTT，后续可由其自维护安装脚本集成 Compose 和反向代理。

## 需要用证据解决的问题

- 记录实际部署的 TeslaMate 版本和必要表字段；定义不支持的表结构如何报错。
- 在相同车辆、时间范围、单位和时区下，将部分结果与上游 Dashboard SQL 和 Grafana 记录对照。
- 构建聚合前定义电量和费用计算、缺失值，以及实测与估算的区别。
- 限制查询开销和轨迹规模；规划全历史地图前先在手机上评估长行程渲染。
- 选择鉴权/会话实现、MateScope 存储、地图瓦片提供方和 PWA 缓存策略。显式展示数据新鲜度，真实连接失败时不静默回退到合成数据。

建议起点是只读 PostgreSQL 加可选 MQTT。这是根据观察到的接口覆盖所做的项目决策，并不代表其他 API 集成永远不可行。
