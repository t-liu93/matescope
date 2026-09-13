# M0 · 本地预览、镜像交付与 VPS 验收

> [English — 唯一权威来源](M0.md) · **中文**

本文是已确认的实现计划，不是实现报告。状态在[路线图](../roadmap_zh.md)维护。

## 1. 目标与范围

交付完整链路：本地 Compose 构建与预览 → 自动 CI → 带版本标签的多平台镜像发布 → VPS 拉取镜像 → 网页初始化与真实数据访问 → 备份恢复。

本地用于自动测试及可选 UI 检查。正式人工验收在 VPS 上完成，不要求用户对每个任务进行本地走查。

已确认选择：

- React/TypeScript 前端和 Python/FastAPI 后端。
- 单管理员、网页 onboarding、中英文界面。
- MateScope 自身数据保存在持久化 SQLite；TeslaMate 历史通过独立只读 PostgreSQL 账号访问。
- 本地使用合成 PostgreSQL，运行与生产相同的 SQL 适配层。
- M0 提供 MQTT/SMTP 配置与连接验证。
- 版本标签触发镜像发布，支持 `linux/amd64` 和 `linux/arm64`。
- 用户在 VPS 拉取指定镜像并启动。CI/CD 验证和交付镜像，不自动 SSH 到生产机。

实时 Dashboard、通知规则、高级统计、完整 PWA 安装/离线体验，以及修改所有者现有安装脚本，不在 M0 范围内。

## 2. 应用与数据契约

### 技术与持久化

- 前端：React 19、TypeScript、Vite、Mantine、React Router、TanStack Query、react-i18next。
- 地图：Leaflet 1.9 与 React Leaflet 5，按需加载。实际实现统计图表时再引入 Recharts。
- 后端：Python 3.13、FastAPI、Pydantic Settings；SQLite 使用 SQLAlchemy 2 和 Alembic。
- TeslaMate 适配层：psycopg 3 同步连接池和参数化 SQL。阻塞工作在同步端点或工作线程中执行。
- 工具：uv、pnpm、Node 22。固定兼容的正式发行依赖并提交锁文件。
- 导出 OpenAPI；使用 openapi-typescript 和 openapi-fetch 生成前端类型并集成客户端。CI 检查生成物漂移。
- 一个非 root 容器提供 API 和编译后的前端，运行单个应用进程。

环境变量管理端口、公开 URL、可信代理和数据目录。网页管理的 PostgreSQL/MQTT/SMTP 凭据及显示偏好保存在 SQLite，不生成 Compose 文件。

SQLite 和自动生成的加密密钥持久化在 `/app/data`。使用 cryptography 加密保存连接密码，限制密钥文件权限。API 不返回已保存密码；编辑时支持保留、替换或清除。

启动时仅迁移 MateScope SQLite。PostgreSQL 未配置或不可用时，登录、设置和 onboarding 仍须可用。

### Onboarding 与鉴权

网页流程：

1. 创建管理员：用户名、密码、确认密码。
2. 设置语言与显示时区，初始值由浏览器建议。
3. 配置 PostgreSQL 主机、端口、数据库、只读用户名/密码和 TLS；测试连接、权限及必要结构。
4. 可选配置 MQTT 认证、TLS、主题前缀；测试连接/订阅。
5. 可选配置 SMTP 认证、TLS、发件人，并显式向指定收件人发送测试邮件。
6. 展示成功、未验证、失败和跳过项目，进入应用。

没有管理员时直接展示创建页面，不使用初始化码。数据库事务保证并发创建只有一个成功。之后关闭公开创建端点，后续设置均要求登录。文档明确第一个完成初始化的人拥有实例。

保存后的进度跨刷新、退出和重启保留。缺少数据库凭据不阻止完成 onboarding。设置页继续提供相同的编辑/测试能力。

保存和测试分开。失败测试不得显示成功。MQTT 订阅成功与尚无新消息是不同结果。

PostgreSQL 帮助说明如何查找现有 Compose/安装脚本凭据、使用 Docker 服务名而非 localhost，以及通过独立准备脚本创建只读角色。MateScope 不保存数据库管理员凭据，不自动重置现有 PostgreSQL 密码。

使用 Argon2id 和服务端会话，Cookie 配置 HttpOnly、SameSite=Lax。生产 HTTPS 启用 Secure，本地 HTTP 预览显式关闭。会话默认 30 天。退出立即失效；修改密码或 CLI 重置撤销现有会话。写操作验证 CSRF，登录限流。M0 不提供公开注册、额外用户或邮件找回密码。

### API 与查询

业务 API 前缀：`/api/v1`。

| 分组 | M0 能力 |
| --- | --- |
| 初始化/鉴权 | 初始化状态、首位管理员、完成 onboarding、登录/退出、当前用户、改密码 |
| 设置 | 读取/更新偏好；保存、禁用和测试 PostgreSQL/MQTT/SMTP |
| 车辆 | 车辆标识、名称和车型 |
| 行程 | 按车辆/时间筛选列表、单条摘要及轨迹 |
| 充电 | 按车辆/时间筛选列表及单条摘要 |
| 运维 | 最小存活检查、应用就绪检查、鉴权后的数据源诊断 |

列表默认最近 30 天、50 条，上限 100 条。使用时间加 ID 的游标分页，单次查询跨度不超过 90 天。更早历史通过切换窗口访问。

使用 UTC ISO 8601 时间及明确的 km、km/h、kWh 单位。按设置时区显示时间，缺失值保留 null。M0 展示已有行程和充电摘要，不推导能耗、电池健康或费用。

仅读取 `cars`、`drives`、`charging_processes` 和 `positions` 必要字段。验证实际结构能力，不假设版本兼容。应用角色不得读取凭据表；超级用户或明显具有业务表写权限的账号不能作为可用数据源。

PostgreSQL 连接最多三个，SQL 超时五秒。轨迹最多 2,000 点，按时间排序且保留起终点，标明简化状态，不跨缺失坐标段连线。

瓦片 URL 可配置，默认 OpenStreetMap 标准瓦片，保留署名且不离线预下载。瓦片故障不阻止查看行程摘要。自动测试使用固定本地瓦片响应。

## 3. 本地 Compose 与 CI/CD

### 本地预览

提供：

- `compose.yaml`：公共应用配置、健康检查、持久化。
- `compose.dev.yaml`：源码构建，加上合成 PostgreSQL、测试 MQTT 和本地邮件接收服务。
- `compose.prod.yaml`：发布镜像，加入指定的现有 TeslaMate Docker 网络。

计划中的预览命令：

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

在 `http://localhost:8000` 提供真实编译前端与同源 API，端口可配置。不要求在宿主机启动前端开发服务器。

包含两辆合成车辆、近期行程/充电、空结果、缺失字段和长轨迹。明确标注合成数据，并记录 onboarding 所需的开发连接值。只初始化开发数据库，应用运行时使用其只读角色。

开发项目名、数据目录和网络相互隔离。不硬编码全局唯一容器名。默认不映射 PostgreSQL 宿主机端口；本地公开服务绑定 loopback。记录目录准备步骤并验证非 root 写权限。普通停止保留数据；重置单独执行，限定为明确标识的开发数据。

本地、CI、发布共用一个 Dockerfile。运行镜像不包含源码挂载、开发服务器或测试数据库。

### 持续集成

分支推送和面向 main 的 PR 运行 CI；普通 CI 不发布镜像。

必要检查：

- 后端 Ruff、mypy、pytest。
- 前端 lint、TypeScript、Vitest、生产构建。
- OpenAPI/类型同步。
- 合成 PostgreSQL 集成及只读权限测试。
- 本地 MQTT/SMTP 集成测试。
- Compose 验证、镜像构建及实际容器启动。
- Playwright 桌面/手机流程：onboarding、登录、连接设置、行程/充电、地图、退出。
- 容器重建后的持久化及备份恢复。

测试报告、失败截图及脱敏容器日志保存为 artifacts。测试不依赖生产凭据、真实记录或真实外发邮件。使用本地瓦片样例，避免 CI 依赖公共地图服务。

普通分支/PR CI 在 amd64 上运行完整应用检查。发布 CI 还实际启动 amd64 和 arm64 镜像并分别测试关键操作，不能只验证编译成功。

### 镜像发布

镜像地址：`ghcr.io/t-liu93/matescope`。通过 GitHub Actions 使用 GITHUB_TOKEN 发布。仅发布 job 授予 packages:write，普通检查保持只读。

| Git 标签 | 发布镜像标签 |
| --- | --- |
| `v0.1.0-alpha.1` | `:0.1.0-alpha.1`，以及源提交 SHA 追溯标签 |
| `v0.1.0` | `:0.1.0`、源提交 SHA 标签及 `:latest` |

预发布不更新 latest。M0 不发布 edge，普通 main 推送不更新镜像。

标签流水线对实际标签提交运行相同的完整检查。构建并测试两个平台，全部必要检查通过后才发布版本标签及多平台 manifest。

发布已经测试的镜像产物，不在测试后重新构建另一份镜像。通过 CI artifacts 传递各平台已测试镜像，在发布 job 上传并组装多平台 manifest。

发布摘要记录源提交、版本、镜像 digest 和拉取命令。首次发布时验证 GHCR 包可见性及 VPS 拉取能力，目标是公开可拉取。构建中不包含生产密钥或数据。

### VPS 部署与回退

生产 Compose 固定所选版本或 digest，加入用户指定的 TeslaMate 网络。仅新增 MateScope 及其自身持久化数据。

双语 runbook 覆盖创建只读账号、准备数据目录/代理、拉取启动镜像、onboarding 和真实数据检查。

升级前备份 MateScope 并记录旧镜像。失败时恢复旧镜像；SQLite 迁移与旧版不兼容时，同时恢复升级前备份。不要让旧应用读取不兼容的新结构。不得通过整组 Compose down 停止现有 TeslaMate 部署。

## 4. 任务与备份

| 任务 | 交付物与完成标准 |
| --- | --- |
| M0-T01 | 工程/交付骨架：前后端、双语框架、生成类型、公共/开发 Compose、Dockerfile；localhost 页面可用，CI 构建并启动镜像 |
| M0-T02 | SQLite 迁移、持久化密钥、管理员创建、会话/账户操作及鉴权测试 |
| M0-T03 | Onboarding/设置：步骤、配置持久化、跳过、恢复进度及后续编辑 |
| M0-T04 | PostgreSQL 适配层：只读角色脚本、诊断、真实 SQL、合成集成测试 |
| M0-T05 | MQTT/SMTP 配置及显式连接/订阅/测试邮件；成功与失败测试 |
| M0-T06 | 车辆/行程/充电页面、详情与地图；桌面/手机可操作及完整 Playwright 流程 |
| M0-T07 | 备份/恢复/重置密码 CLI；重建和恢复后配置可用 |
| M0-T08 | 标签发布流水线、双平台运行检查、GHCR 交付、生产 Compose 和 runbook |
| M0-T09 | VPS 验收：拉取发布镜像、首次安装、真实数据对照、持久化、升级/恢复走查 |

按此顺序推进。Compose/镜像检查从 T01 开始并持续执行；T08 完成远程镜像交付，T09 验证该发布产物。

提供 `matescope backup`、`matescope restore`、`matescope reset-password`。备份采用 SQLite 在线备份 API，包含数据库、解密密钥及格式版本清单，不包含 TeslaMate 历史。

备份写入明确指定位置并限制权限，包含可恢复凭据。M0 不调度、不上传、不提供网页备份下载，现有 VPS 脚本可调用 CLI。

恢复时停止服务并使用空目录。校验归档内容/版本并撤销旧会话。密钥缺失必须明确失败，不得静默替换。在可丢弃数据上实际测试恢复，不覆盖原目录。

明确要求编排时，使用现有 AGENTS 的模型、评审、实现提交、fixup 和按步骤 squash 规则。本计划不修改评审严重度标准。

## 5. 验收与文档

交付 VPS 验收前证明：

- 干净环境启动开发 Compose 能完成完整用户流程。
- 两种发布架构均能启动并执行关键 API 操作。
- 并发初始化、未登录访问、退出、配置修改和重启持久化正确。
- 错误凭据、空数据、缺字段、SQL 超时、权限不足/过大有明确结果。
- 分页、时区跨日、未结束记录、缺失坐标和长轨迹通过测试。
- 实际演练过备份恢复。
- 目标镜像可拉取，并能追溯源码和 digest。

用户正式走查集中为一次完整 VPS 验收：管理员初始化、连接配置、代表性行程/充电、Grafana 对照、手机地图、重启及备份恢复。记录实际 TeslaMate 版本和表结构兼容性。公开报告不包含坐标或凭据。

CI 覆盖应用行为和已知结构，不能保证未知 VPS 环境无需调整。分别记录本地检查、镜像发布、VPS 验收，三者全部完成才关闭 M0。

同步更新中英文里程碑和路线图。开发、发布、部署及恢复文档全部双语，英文权威。不得将计划中的命令、workflow、发布或走查标记为已实现。

## 参考资料

- [FastAPI 并发](https://fastapi.tiangolo.com/async/)
- [SQLite 备份 API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup)
- [OpenStreetMap 瓦片政策](https://operations.osmfoundation.org/policies/tiles/)
- [GitHub 镜像发布](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [Docker 多平台 CI](https://docs.docker.com/build/ci/github-actions/multi-platform/)
