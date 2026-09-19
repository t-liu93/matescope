# MateScope 路线图

> [English — 唯一权威来源](roadmap.md) · **中文**

本文是范围与里程碑总览，不是冻结的实现规格。下表是唯一权威的进度记录。各里程碑在即将实现时使用[模板](milestones/_TEMPLATE_zh.md)细化。

## 方向与决策

已确认方向：自托管、响应式 React PWA，具有独立鉴权、Python/FastAPI 后端、TeslaMate 历史只读访问、可选 MQTT，以及独立可部署容器。产品目标是以现代、信息充分的界面替代 TeslaMate Grafana，让手机、平板和桌面用户方便地找到常用信息。所有者通过自己的安装脚本集成部署。M0 已建立数据访问基础，M1 设计日常 Dashboard。

| 领域 | 当前方向 | 脚手架期间待验证 |
| --- | --- | --- |
| 前端 | React + TypeScript + Vite；优先 Mantine | 兼容版本、移动端框架 |
| 数据 UI | 优先 TanStack Query、Leaflet/React Leaflet、Recharts | 轨迹/序列规模限制、触摸交互、瓦片提供方 |
| API | FastAPI、生成 OpenAPI 类型和类型化前端客户端 | 路由组织、错误、代码生成及同步检查 |
| 历史存储 | TeslaMate PostgreSQL、独立只读角色、psycopg 3；本地合成 PostgreSQL | 实际 VPS 结构兼容及有界查询 |
| 应用存储 | 持久化 SQLite、SQLAlchemy 2/Alembic；加密连接配置及 CLI 备份恢复 | 迁移、重启和恢复验证 |
| 初始化 | 双语网页 onboarding；单管理员；PostgreSQL/MQTT/SMTP 设置 | 恢复进度/跳过/编辑行为及连接测试 |
| 工具 | Python 3.13、Node 22、uv、pnpm | 兼容的固定发行版本、锁文件、可复现检查 |
| 打包 | 单个非 root 镜像提供 API 和编译前端；本地/生产 Compose | 健康检查、隔离的持久化数据、代理配置 |
| 交付 | GitHub Actions；版本标签发布 GHCR amd64/arm64 镜像 | 发布已测试产物；预发布不更新 latest；VPS 拉取验证 |

## 约束

- MateScope 不得迁移或写入 TeslaMate 表。角色权限限定为必要数据；账号初始化是独立部署操作。
- 本地开发使用合成测试数据。不得发布生产数据、开放生产数据库端口，或让普通测试依赖在线 VPS。
- 部署真实数据前，为车辆数据端点提供应用鉴权。区分凭据无效、表结构不兼容、空结果和临时连接失败，且不泄露密钥。
- 对照上游行为核验指标，记录有意差异。明确处理时区、单位、缺失值和估算。
- 限制数据库工作量和客户端载荷。分别处理 UI 资源与敏感数据的缓存策略。
- 文档双语，API 契约同步。执行和 Git 规则遵循 [agent 指引](../../AGENTS_zh.md)。

## 里程碑与进度

| 里程碑 | 结果 | 状态 |
| --- | --- | --- |
| [M0](milestones/M0_zh.md) | 本地 Compose 预览、网页初始化、只读数据、备份恢复、CI 与双平台发布、VPS 验收 | 已完成：所有者已确认走查完成及 M0 结束。本地验证与镜像交付已有记录；此结论不表示原计划中的每项人工测试均被逐项执行。 |
| Alpha 后增强 | 密码管理器提示、可安装 PWA、可选 TOTP 与恢复码 | 所有者已完成人工走查，并确认 2FA 创建及使用正常。`v0.1.0-alpha.2` 已发布；main CI、发布检查、原生 amd64/arm64 镜像验证及匿名拉取通过。离线时隐藏并清除车辆数据。 |
| [M1](milestones/M1_zh.md) | 精简概览、完整历史日期选择器、行程/充电摘要与曲线、响应式导航；复用已有 PWA 基础 | 已授权按编排流程实现全部 36 个有界步骤：T01–T14 已通过实现、独立审查及集成检查；T15–T36 尚未开始。M1 仅使用 PostgreSQL 数据，不含持续 MQTT 或高级分析。人工验收、发布及部署仍待进行。 |
| M2 | 功能覆盖清单与优先统计：效率、续航/电池、停车掉电、地点 | 提议；M1 后细化 |
| M3 | 根据实际使用增加对比、活动时间线、性能与体验优化 | 提议；范围未承诺 |

发布基线：[`v0.1.0-alpha.2`](https://github.com/t-liu93/matescope/releases/tag/v0.1.0-alpha.2)，镜像 `ghcr.io/t-liu93/matescope:0.1.0-alpha.2`，源提交 `646fdf82d2fdde7a46d7199e51b8bd01be8d926a`。发布完成不代表该版本已部署到 VPS。

M0 包含 MQTT/SMTP 配置和显式测试，不包含实时 Dashboard 或通知规则。后续里程碑均不代表完整 Grafana 功能对齐承诺。本地验证、镜像发布和 VPS 验收分别记录，三项全部通过才能完成 M0。

## 验证与交付

Compose 和镜像检查从 M0-T01 开始。分支/PR CI 使用合成服务及浏览器测试验证应用；版本标签在完整检查和双平台运行验证后，将已测试镜像发布至 GHCR。用户可选本地预览；正式人工验收集中在 VPS，使用发布版本或 digest。CI 不自动 SSH 到生产机。命令存在后再记录命令及结果；M0 中的规划命令尚不是可用工具。

相关背景：[上游洞察](../insight/teslamate_zh.md)与[参考经验](../insight/references_zh.md)。
