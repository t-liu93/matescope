# 在现有 TeslaMate Compose 项目中添加 MateScope

> [English — 唯一权威来源](existing-teslamate-compose.md) · **中文**

本指南让 MateScope 在现有 TeslaMate 安装目录与 Compose 项目中统一管理。仓库根目录的 `compose.yaml` 和 `compose.prod.yaml` 描述独立部署，不能把它们的完整内容覆盖到已有 TeslaMate Compose 文件。本方案只在原文件中新增一个服务和一个专用数据卷。

## 1. 准备现有安装目录

确认安装器实际使用的 Compose 文件、项目名、环境文件和所有覆盖文件。操作时保留完全相同的调用方式：改变 `-p`、顶层 `name`、`COMPOSE_PROJECT_NAME`、文件顺序或环境文件选择，可能选中不同项目或数据卷。以下命令假设现有入口为 `docker-compose.yml` 加当前目录的 `.env`，没有额外项目名参数或覆盖文件；若实际不同，请相应调整 `dc` 函数。

确认安装器是否保留手工新增的服务。如果它会重新生成文件，应使用它支持的自定义机制，后续维护保持相同项目和完整文件列表。本指南不修改安装器。

准备 [PostgreSQL 专用只读账号](postgresql-readonly_zh.md)、空闲主机端口和独立 HTTPS 域名。账号已准备好时，直接进入连接验证，不要重复创建角色。不要对外发布 PostgreSQL 端口，也不要复用其数据卷。编辑前私下备份当前配置；这是配置备份，不是数据库备份：

```bash
cd /path/to/existing/teslamate
umask 077
backup_dir=$(mktemp -d ./matescope-config-backup.XXXXXX)
cp -p docker-compose.yml "$backup_dir/"
if [ -f .env ]; then cp -p .env "$backup_dir/"; fi
```

将示例目录和文件名替换为真实安装位置。文件可能包含秘密，备份应保持私有，不纳入版本控制。

## 2. 合并服务与数据卷

在原文件已有的 `services:` 下新增 `matescope`，在原文件顶层 `volumes:` 下新增 `matescope-data`。只有原本没有顶层 `volumes:` 时才创建它。**不要粘贴第二个 `services:` 或 `volumes:`，不要覆盖文件或删除原有条目。** 如果新服务名或卷名已被使用，先检查再继续。

```yaml
services:
  # Keep all existing TeslaMate services here.
  matescope:
    image: ${MATESCOPE_IMAGE:?Set MATESCOPE_IMAGE}
    restart: unless-stopped
    environment:
      MATESCOPE_DATA_DIR: /app/data
      MATESCOPE_PORT: "8000"
      MATESCOPE_PUBLIC_URL: ${MATESCOPE_PUBLIC_URL:?Set MATESCOPE_PUBLIC_URL}
      MATESCOPE_COOKIE_SECURE: "true"
      MATESCOPE_TRUSTED_PROXIES: ${MATESCOPE_TRUSTED_PROXIES:-}
    ports:
      - "127.0.0.1:${MATESCOPE_HOST_PORT:-18080}:8000"
    volumes:
      - matescope-data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s

volumes:
  # Keep all existing TeslaMate volumes here.
  matescope-data:
```

示例假设 PostgreSQL 使用项目默认网络；Compose 会自动把 MateScope 加入同一默认网络。它不会创建另一个 PostgreSQL、MQTT 或邮件服务。如果 PostgreSQL 使用具名网络，在 `matescope` 服务内补充以下配置，使用现有 Compose 网络键名，并保留该网络原有的顶层定义：

```yaml
    networks:
      - YOUR_EXISTING_DATABASE_NETWORK_KEY
```

Compose 网络键名可能不同于运行时的 Docker 网络名。应检查现有配置，不要猜一个名称再声明新网络。生产中不要引入仓库的开发或测试 Compose 文件。

将以下变量合并到原 `.env`，保留全部原有条目。替换示例域名，选择空闲端口；`18080` 只是示例，并不保证未被占用。若已有 MateScope 变量，应修改已有项而非重复添加：

```dotenv
MATESCOPE_IMAGE=ghcr.io/t-liu93/matescope:0.1.0-alpha.1
MATESCOPE_PUBLIC_URL=https://matescope.example.com
MATESCOPE_HOST_PORT=18080
MATESCOPE_TRUSTED_PROXIES=
```

镜像标签是首个 Alpha；后续版本使用已审阅的发布标签或已发布清单摘要。PostgreSQL 凭据不写入此文件，应在 MateScope 已认证的初始化界面填写专用账号。本例容器始终监听 8000；`MATESCOPE_HOST_PORT` 只改变主机端口映射。

## 3. 配置 HTTPS 并仅启动 MateScope

宿主机反向代理应将整个独立域名转发到 `http://127.0.0.1:18080`（端口改变时同步调整）。保留浏览器的 `Origin` 请求头。`MATESCOPE_PUBLIC_URL` 必须匹配浏览器可见的 HTTPS 源地址，包含非默认端口。不支持子路径部署。创建首位管理员前应限制入口访问。

容器化反向代理不能通过自己的 `127.0.0.1` 访问宿主机。沿用现有代理配置，把代理和 MateScope 加入合适的共享网络，再使用 `http://matescope:8000` 作为上游。添加代理网络时保留 MateScope 的数据库网络。可选可信代理设置只应包含应用实际看到的直接代理地址，禁止 `*`；留空时忽略转发请求头。本指南不修改现有代理路由或服务器 TLS 设置。生产 Cookie 要求 HTTPS。

在同一安装目录和 shell 中，先校验合并配置，再启动：

```bash
dc() { docker compose -f docker-compose.yml "$@"; }
dc config --quiet
dc config --services
dc pull matescope
dc up -d --no-deps matescope
dc ps matescope
curl --fail http://127.0.0.1:18080/api/v1/readiness
```

`config --services` 应列出全部原有服务及新增的 `matescope`。在本地检查编辑内容，确认原服务、网络和卷未变化；展开配置可能包含秘密，不要公开。校验失败时，先修正配置，再执行 `pull` 或 `up`。

定向执行的 `up --no-deps matescope` 只创建或重建 MateScope，不要求重启数据库、TeslaMate 或 Grafana。它会创建 MateScope 专用卷，通常名为 `<现有项目名>_matescope-data`，并加入配置的网络。此次添加不要使用整个项目的 `down`、`down --volumes`、`up --force-recreate` 或 `--remove-orphans`。就绪检查验证应用，不代表 PostgreSQL 连接已成功。

## 4. 测试账号与真实数据

打开 HTTPS 域名，创建管理员，配置 PostgreSQL：

| 设置 | 值 |
| --- | --- |
| Enable | 勾选 |
| Host | 现有数据库服务别名，通常是 `database`；不是 localhost |
| Port | 数据库内部端口，通常是 `5432` |
| Database | 现有 TeslaMate 数据库名 |
| Username | `matescope_readonly` |
| Password | 新建专用账号的密码 |
| SSL | 与实际 PostgreSQL TLS 配置一致 |

保存后显式点击 **Test Saved Connection**。它检查有效权限和所需结构，不要在生产尝试写入探测。若 PostgreSQL 未启用 SSL，`disable` 与现状匹配，但不加密传输；连接应留在预期的内部 Docker 网络。错误处理参考[账号指南](postgresql-readonly_zh.md)。

成功后选择小时间范围，与 Grafana 对照代表性行程和充电。初期可以跳过 MQTT/SMTP。真实 SMTP 测试会发邮件，只在有意测试时触发。MateScope 卷中的 SQLite 数据库及加密密钥必须一起保留。

排查时如需停止新增应用，使用：

```bash
dc stop matescope
```

这会保留其数据卷和已有 TeslaMate 服务。后续更换镜像同样应先备份应用数据，再只操作 `matescope`。接入服务本身不代表已完成真实 VPS 验收，也不证明安装器升级会保留手工修改。
