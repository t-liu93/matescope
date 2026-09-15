# 配置 TeslaMate PostgreSQL 只读访问

> [English — 唯一权威来源](postgresql-readonly.md) · **中文**

本指南为现有 Docker 部署的 TeslaMate 数据库准备 MateScope 访问，区分只读检查与需要明确授权的角色变更。它不部署 MateScope，也不代替 VPS 验收。遵循 [M0 范围](../plan/milestones/M0_zh.md)，使用所选发布版本检出目录中的[权威准备脚本](../../scripts/postgresql/prepare-readonly.sql)。

## 边界与前置条件

不修改 TeslaMate 数据行、表、现有账号密码、共享授权、数据库卷或服务运行状态。不要在生产运行合成数据初始化、写入拒绝探测、迁移、`down --volumes` 或整个栈的重建。准备脚本只改变 PostgreSQL 角色及权限元数据。只读查询仍消耗资源；读取车辆历史前，先以短超时检查系统目录。

准备 SSH 访问、实际 PostgreSQL 容器名、数据库名，以及具备创建角色和所需授权能力的现有数据库管理员身份。密码留在服务器或密码管理器；不要把 `.env`、完整 `docker inspect`、展开的 Compose 配置或车辆记录贴进报告。了解现有备份状态，不覆盖备份。确认现有 Docker 网络、数据库服务别名及实际 TLS 配置。

以下命令在 VPS 的 Bash 中执行，先替换占位符。现有管理员只用于此次维护，绝不能保存为 MateScope 的连接账号。

## 1. 定位部署，不暴露凭据

在开发机执行：

```bash
ssh YOUR_VPS_SSH_ALIAS
```

在 VPS 执行：

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
db_container='REPLACE_WITH_POSTGRES_CONTAINER'
db_owner='REPLACE_WITH_DATABASE_ADMIN'
db_name='REPLACE_WITH_TESLAMATE_DATABASE'
docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$db_container"
docker inspect --format '{{json .NetworkSettings.Networks}}' "$db_container"
docker inspect --format '{{json .NetworkSettings.Ports}}' "$db_container"
```

这些选定字段含有运维标识，分享前应脱敏。不要根据浮动镜像标签推断实际数据库版本。共享 Docker 网络通常无需向主机发布 5432 端口。MateScope 优先使用独立 Compose 项目与数据卷，不要停止现有 TeslaMate 项目。

## 2. 在只读事务中检查系统目录

使用容器现有的本地数据库管理入口。如需认证，沿用已有的安全方式，不要为了执行本指南而放宽认证规则。

```bash
docker exec -i \
  -e 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000' \
  "$db_container" psql -X -v ON_ERROR_STOP=1 -U "$db_owner" -d "$db_name" <<'SQL'
BEGIN READ ONLY;
SELECT current_database(), current_user, current_setting('server_version'),
       current_setting('transaction_read_only'), current_setting('ssl');
SELECT rolname, rolsuper, rolcreaterole
FROM pg_roles WHERE rolname IN (current_user, 'matescope_readonly');
SELECT c.relname, c.relkind, a.attname, t.typname
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_type t ON t.oid=a.atttypid
WHERE n.nspname='public'
  AND c.relname IN ('cars','drives','charging_processes','positions')
  AND a.attnum>0 AND NOT a.attisdropped
ORDER BY c.relname,a.attnum;
SELECT n.nspname, c.relname, x.privilege_type
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
WHERE n.nspname IN ('public','private')
  AND c.relkind IN ('r','p','v','m','f') AND x.grantee=0;
SELECT n.nspname, c.relname, a.attname, x.privilege_type
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_attribute a ON a.attrelid=c.oid
CROSS JOIN LATERAL aclexplode(a.attacl) x
WHERE n.nspname IN ('public','private') AND a.attnum>0 AND x.grantee=0;
SELECT n.nspname, x.privilege_type
FROM pg_namespace n
CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) x
WHERE n.nspname IN ('public','private') AND x.grantee=0;
SELECT count(*) AS public_executable_security_definer_functions
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
WHERE n.nspname NOT IN ('pg_catalog','information_schema')
  AND p.prosecdef AND x.grantee=0 AND x.privilege_type='EXECUTE';
ROLLBACK;
SQL
```

对照[数据适配器](../../backend/matescope/postgresql.py)及准备脚本核对所需字段与类型。如果目标不正确、所需表或类型不匹配，或 `matescope_readonly` 已存在，先停止。已有角色需要另行检查成员关系、所有权和有效权限；本脚本故意报错，不直接复用已有角色。

`PUBLIC` 授权同样适用于新角色。发现共享写权限、凭据表读取、模式创建权限或可执行的 security-definer 函数时，先调查再继续。不要自动撤销 `PUBLIC` 权限，现有服务可能依赖它。这些系统目录查询是有限的预检，不是对所有函数及扩展的完整审计。

## 3. 创建账号——明确的数据库变更

只有所有者审阅目标和脚本，并授权角色变更后，才执行本节。脚本创建 `matescope_readonly`，授予数据库 `CONNECT`、模式 `USAGE`，以及四张表所需列的 `SELECT`；把新角色默认事务设为只读，并提示两次输入新密码。它不修改已有角色或密码，也不自动授权未来新增表。访问边界依靠实际授权，而不能只依赖默认事务只读设置。

在开发机所选 MateScope 发布版本的检出目录中，审阅并传输脚本。必要时选择未占用的目标文件名：

```bash
cat scripts/postgresql/prepare-readonly.sql
sha256sum scripts/postgresql/prepare-readonly.sql
scp scripts/postgresql/prepare-readonly.sql YOUR_VPS_SSH_ALIAS:matescope-prepare-readonly.sql
```

回到 VPS 的 Bash 会话，保留第 1 步设置的三个变量：

```bash
sha256sum "$HOME/matescope-prepare-readonly.sql"
cat "$HOME/matescope-prepare-readonly.sql"
```

执行前确认哈希与本地审阅过的脚本一致。以下命令在容器中创建临时脚本文件，执行角色事务，然后只删除该临时文件：

```bash
(
  set -eu
  db_script=$(docker exec "$db_container" mktemp /tmp/matescope-readonly.XXXXXX)
  trap 'docker exec "$db_container" rm -f -- "$db_script"' EXIT
  docker cp "$HOME/matescope-prepare-readonly.sql" "${db_container}:${db_script}"
  docker exec -it \
    -e 'PGOPTIONS=-c statement_timeout=5000 -c lock_timeout=2000' \
    "$db_container" psql -X -v ON_ERROR_STOP=1 \
      -U "$db_owner" -d "$db_name" -f "$db_script"
)
```

在交互提示中为 `matescope_readonly` 输入新密码，不要将密码放在命令参数中。成功应以 `COMMIT` 结束。SQL 报错会停止文件执行，连接关闭时未提交事务回滚。超时、断连或结果不确定时，先检查角色是否已存在，再决定是否重试；不要自动删除或覆盖账号。这些角色及授权变更不需要重启 PostgreSQL 或重新加载配置。

## 4. 验证并连接 MateScope

在 MateScope 的 PostgreSQL 设置中启用连接，填写：

| 字段 | 值 |
| --- | --- |
| Host | 共享 Docker 网络中的 PostgreSQL 服务别名，不是 localhost |
| Port | PostgreSQL 内部端口，通常为 `5432` |
| Database | 预检确认的 TeslaMate 数据库名 |
| Username | `matescope_readonly` |
| Password | 上面输入的新密码 |
| SSL mode | 与服务器实际配置一致，不要假定已启用 TLS |

如果 PostgreSQL 未启用 SSL，`disable` 与现状匹配，但**不提供传输加密**；连接应保留在预期的主机内部 Docker 网络。服务器已启用 TLS 时，选择与证书匹配的证书验证设置；`prefer` 不保证加密。本指南不修改服务器 TLS 或认证配置。

先保存，再显式测试已保存连接；保存本身不会测试。测试检查有效权限、所需字段及类型和模式访问，不写入 TeslaMate 数据。它拒绝高权限角色、`public`/`private` 中表级或列级写权限，以及其中 `tokens`/`users` 的读取权限。不要通过尝试生产写入验证只读，即使包在事务里也不要。本地 socket 或回环连接可能使用 `trust`；本地登录成功不能单独证明跨 Docker 网络所用密码有效。

验证失败时保留错误类别并检查元数据，不要扩大为全部表授权或改用 TeslaMate 管理员。成功后从小时间范围开始真实数据验收。MateScope 存储保持独立，后续备份恢复只针对 MateScope 自有资源。
