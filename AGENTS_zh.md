# AGENTS.md · MateScope

> [English — 唯一权威来源](AGENTS.md) · **中文镜像**

保持本文件简短：只记录稳定的项目背景和工作规则。详细架构、实现计划和进度按需放入专门文档。

## 项目背景

MateScope 是一个自托管、移动端优先的响应式 PWA，用于探索 TeslaMate 车辆数据。它提供独立鉴权，目标是通过改进导航、地图和分析体验，覆盖 TeslaMate Grafana Dashboard 的大部分使用场景。

仓库：[t-liu93/matescope](https://github.com/t-liu93/matescope)。SSH 远程地址：`git@github.com:t-liu93/matescope.git`。

- 前端：React + TypeScript + Vite。优先考虑 Mantine、TanStack Query、Leaflet / React Leaflet、Recharts，以及基于 OpenAPI 生成的客户端类型。
- 后端方向：Python + FastAPI。详细架构、依赖版本、存储选型和目录结构尚未确定，在搭建脚手架时记录决定。
- 部署：独立容器化应用，通过 Docker 网络和项目所有者的安装脚本，与现有 TeslaMate 部署集成。
- 历史数据：仅后端使用独立只读账号访问 TeslaMate PostgreSQL。可选 MQTT 提供车辆最新状态。MateScope 自身数据独立存储。
- 初始范围：先验证脚手架和真实数据访问，再构建日常使用的 Dashboard。

从[文档索引](docs/README_zh.md)、[路线图](docs/plan/roadmap_zh.md)及相关里程碑开始阅读。进度由路线图管理；insight 提供背景，而非额外 agent 规则。

## 语言与文档

- 使用用户的语言回复。受版本控制的 agent 编写的项目文档，包括 README、指南和计划，必须提供中英双语版本。`development-notes/` 中的本地材料，包括步骤 implementation/review/fix report 和 Milestone implementation report，仅用中文编写，不需要英文镜像或语言配对链接。
- 英文是唯一权威来源。保留英文原文件名，中文忠实镜像在扩展名前插入 `_zh`：例如 `README.md` / `README_zh.md`、`AGENTS.md` / `AGENTS_zh.md` 或 `plan.md` / `plan_zh.md`。两者冲突时以英文为准；翻译不得引入独立规则。
- 同一次修改中同步更新每一对文件。在各自 H1 标题正下方添加另一语言的链接。其他文档链接在存在镜像时应指向同语言版本。
- 不要假设规划中的文件、命令或功能已经存在。修改前阅读相关实现及已有设计文档。

- 临时实现笔记、逐任务走查、评审意见、验证报告及其他供项目所有者查看的中间材料，统一放在项目根目录的 `development-notes/` 中。整个目录必须被 Git 和 Docker 构建上下文忽略；不得强制添加、提交，或将其中的笔记复制进受版本控制的文档。这些本地报告同时供冷启动 agent 和项目所有者人工查看。
- 受版本控制的文档只保留稳定项目信息、已确认的计划和简明路线图进度。任务完成或笔记包含实用命令，不代表它自动成为长期文档；只有用户明确要求时才将材料转为受版本控制的文档。逐任务执行与评审证据放在 `development-notes/`，不要写入 README 或里程碑计划。

## 工作规则

- 在用户要求的范围内，以小而便于评审的改动推进。保留用户无关改动，避免推测性的抽象或重构。
- 自主处理常规实现选择；缺失的决定会实质改变范围或行为时再询问。
- 不得提交密钥或真实车辆及位置数据。使用合成或脱敏测试数据。不得向前端暴露数据库凭据。
- 应用不得修改 TeslaMate 数据或表结构。数据库角色初始化属于独立、明确的部署步骤。未经授权，不得修改实际运行的部署。
- 使用仓库已有检查验证相关行为。为高风险逻辑添加有意义的测试；修改容器打包时实际构建镜像。报告通过的检查、未运行的检查和剩余限制。
- API 契约变化时同步生成的类型。明确处理时区、单位、缺失数据，以及实测指标与估算指标的区别。
- 提交保持范围清晰，使用英文 Conventional Commits，不添加 AI 署名。遵循下文的提交授权与历史规则。

## Git 与提交生命周期

- 单人开发可以直接在 `main` 上进行；除非用户要求，否则分支和 PR 可选。不得把无关改动混入提交。
- 非编排模式仅在明确要求时提交。推送、发布 release/tag、改写已推送历史，都需要覆盖该操作的明确授权。
- 用户明确要求运行编排实现，即授权在指定范围内创建本地实现提交、fixup 提交，以及按步骤 squash 尚未发布的提交。这本身不授权推送或发布。
- **实现：**当前步骤的必要检查通过后，创建一个实现提交，并记录其 SHA 和前一个提交作为步骤边界。
- **返工：**每轮评审修复使用 `git commit --fixup=<implementation-sha>` 提交。修复限定在同一步骤内；报告中包含新增验证。
- **收尾：**评审与集成检查通过后，先将该步骤的实现和 fixup 合并成一个提交，再继续下一步。步骤存在前置提交时，使用 `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <preceding-sha>`。实现提交是根提交时，仅在确认受影响历史属于该步骤后，使用 `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash --root`。
- Autosquash 会将 fixup 折叠到目标提交，但不会合并多个普通实现提交。若同一步骤有多个普通实现提交，明确编排非交互 rebase todo，只 squash 该步骤的提交。保留其他步骤或贡献者的提交。
- 改写前确认工作区干净、提交边界准确，且所有受影响提交均未发布。不得自动改写已推送历史或 force-push。未获改写授权时，保留历史并使用后续提交。
- 里程碑完成后，每个已完成步骤保留一个提交，不将整个里程碑压成一个提交。在 `development-notes/` 中生成仅中文的 Milestone implementation report，说明已完成与待完成工作、验证结果、限制，以及带预期结果的人工走查步骤。不得把待完成的验收描述为已完成。

## Agent 编排

默认由单个 agent 工作。仅在用户明确要求编排或委派时，启动委派实现与评审循环。本节定义该流程，不会自动启用它。

### 模型默认配置

| 角色 | 首选模型 | 推理强度 |
| --- | --- | --- |
| 编排器 / reviewer | `gpt-6-astra` | `medium` |
| implementer / fixer | `gpt-5.6-luna` | `medium` |

- 这些是可调整的项目默认值，不表示模型质量相同。用户明确指定的配置优先。
- 遇到困难的架构决策、隐蔽的正确性问题或安全敏感评审，确实需要更多推理时，使用 Astra `high`。
- 给 Luna 分配边界明确、验收标准具体的任务。当复杂度或反复失败表明需要升级时，将实现或修复升级到 `gpt-5.6-terra` 的 `medium`，更困难的工作交给 Astra。涉及鉴权边界、数据库权限和含糊的统计逻辑时，视情况从一开始就交给更强模型。
- 通过实际运行环境的模型及推理参数配置，不要仅在提示词中指定角色名称。如果不支持所选配置，说明限制并使用符合用户偏好的可用替代方案。除非运行环境实际完成切换，否则不得声称已更改主会话模型。

### 报告与冷启动交接

- 每个原子步骤实现后保存 implementation report，每轮评审后保存 review report，每轮修复后保存 fix report，均在交接前完成。使用 `development-notes/<milestone>/<step>/implementation-report.md`、`review-report-r<N>.md` 和 `fix-report-r<N>.md`；内容全部用中文，保留每轮记录。Milestone implementation report 保存在 `development-notes/<milestone>/implementation-report.md`。
- 报告是唯一的验收与交接记录。报告与对应代码 diff 共同构成冷启动 reviewer/fixer 唯一的任务专属输入；不得传递过程对话、口头补充或编排器倾向的结论。仓库规则仍适用，agent 必须检查相关代码并独立验证报告中的结论。将已确认范围和验收标准写入报告，使下一位 agent 无需依赖之前的对话。
- implementation report 记录步骤 ID、范围/排除项、验收标准、改动/文件、前置及实现提交 SHA、准确 diff 边界、检查及结果、未运行检查、限制和人工验证。review report 标明被评审的 SHA，每个问题记录 ID、优先级、位置、复现/证据、预期行为和所需修正，并记录独立检查及验收结论；没有可操作问题时明确写出。fix report 逐项对应问题、修复内容、fixup SHA/diff、验证及未解决事项。
- 全新 reviewer 接收 implementation report、后续已有的 review/fix report 及当前步骤的累计 diff。全新 fixer 接收这些报告、最新一轮可操作的 review report 及对应的累计/修复 diff。范围、证据或提交边界缺失或过期时，必须先修正报告再交接。squash 后记录最终 SHA 及新旧映射，保证后续验收使用实际当前 diff。

### 委派流程

1. 明确任务边界、验收标准、相关文件和必要检查。仅并行处理相互独立且文件归属明确的工作。
2. implementer 完成任务及必要检查；评审前创建实现提交和中文 implementation report。多个 agent 共用工作区时，由编排器统一执行 Git 修改操作，不得并发提交或 rebase。
3. 冷启动 reviewer 使用上述报告与 diff 交接方式，检查代码、独立验证关键行为，并编写中文 review report。
4. 冷启动 fixer 处理 review report 中的可操作问题，为该步骤的实现提交创建 fixup，并编写中文 fix report。全新 reviewer 再次评审更新后的报告与 diff，记录下一轮评审。同一个问题修复两次仍失败时升级处理，不要原样重复指令。修复达到五轮后停止循环，向用户报告未解决的问题。
5. 没有剩余可操作问题后，编排器验证集成、执行已授权的按步骤 squash、核对最终 diff 和历史，并在报告中更新最终边界及验收结论后，再推进下一步。评审是测试的补充，不能替代测试。

## 维护本指引

稳定决策变化时同步更新两种语言版本。不要将里程碑状态、临时研究、本机特定路径或其他仓库的指令写入本文件。临时开发命令及执行证据放在 `development-notes/`；稳定指引不得链接到被忽略的本地文件。
