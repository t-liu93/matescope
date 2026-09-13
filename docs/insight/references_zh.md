# 参考项目

> [English — 唯一权威来源](references.md) · **中文**

这些笔记总结了 2026-09-13 初始规划时检查的项目。内容是可独立阅读的观察记录，不是依赖，也不要求后续会话访问这些仓库。未复制源代码。参考项目中观察到的依赖范围不构成 MateScope 的版本锁定。

| 参考项目 | 观察到的模式 | 对 MateScope 的借鉴 |
| --- | --- | --- |
| just-another-invoice | 精简 agent 指引；`docs/insight/`、路线图、逐里程碑文档和任务模板；中英镜像 | 分离背景、稳定规则和可执行计划；用小型端到端功能推进 |
| omniventory | React/TypeScript/Vite、Mantine、集中主题、响应式框架、多语言、PWA 配置、OpenAPI 生成类型与客户端、Python/FastAPI | 借鉴主题、UI 状态、鉴权及类型化契约的组织方式 |
| home-automation | React/TypeScript/Vite、Mantine、TanStack Query、Leaflet/React Leaflet 热力图与聚合、Recharts | 用类型化组件封装地图渲染；限制时间序列查询范围，缺失值保留断点 |
| Linux-install-helper | 所有者维护的 TeslaMate Compose 生成、外部 MQTT 配置、宿主机 Nginx HTTPS 代理、Docker 网络内的 PostgreSQL | 提供独立 MateScope 部署示例，后续通过所有者的安装脚本集成 |

## 值得沿用的做法

- 封装地图与图表，使渲染库可以替换而不改变 API 契约。
- 显式设计移动端导航和触摸交互；响应式组件库本身不能完成这些体验。
- 从后端 OpenAPI 生成前端类型，并验证生成的契约保持同步。
- 保留路线图总览，将下一里程碑展开为边界明确、具备验收标准和可复现验证的任务。

## 需要重新决定的内容

参考项目使用不同的 React 和 Mantine 主版本。脚手架阶段应选择并测试兼容组合，而不是拼接依赖文件。库存业务和发票领域专属架构不属于 MateScope 范围。

检查到的安装脚本会从模板重新生成 Compose 文件。由于所有者控制该脚本，可以在其中完成集成；无需因此修改 TeslaMate 上游。本地模板不能证明 VPS 实际版本、网络名或凭据。

以后复用代码时，需要检查来源许可证并适配 MateScope 的行为。执行本计划无需依赖其他仓库或本机文件路径。
