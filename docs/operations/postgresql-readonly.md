# Prepare read-only TeslaMate PostgreSQL access

> **English — single source of truth** · [中文](postgresql-readonly_zh.md)

This guide prepares an existing Docker-hosted TeslaMate database for MateScope. It separates read-only inspection from an explicitly authorized role change. It does not deploy MateScope or perform VPS acceptance. Follow the [M0 scope](../plan/milestones/M0.md) and use the [canonical preparation script](../../scripts/postgresql/prepare-readonly.sql) from the selected release checkout.

## Boundaries and prerequisites

Do not change TeslaMate rows, tables, existing account passwords, shared grants, database volumes, or service lifecycle. Do not run synthetic initialization, write-denial probes, migrations, `down --volumes`, or whole-stack recreation on production. The preparation script changes PostgreSQL role/privilege metadata only. Read-only queries still consume resources; inspect catalogs with short timeouts before querying vehicle history.

Prepare SSH access, the actual PostgreSQL container name, database name, and an existing database administrator identity with role-creation and required grant authority. Keep passwords on the server or in a password manager; do not paste `.env`, full `docker inspect`, expanded Compose configuration, or vehicle records into reports. Know the status of your existing backup without replacing it. Identify the existing Docker network, database service alias, and actual TLS configuration.

Commands below use Bash on the VPS. Replace placeholders before use. The existing administrator is used for this maintenance operation only, never as MateScope's saved connection account.

## 1. Locate the deployment without exposing credentials

On the development machine:

```bash
ssh YOUR_VPS_SSH_ALIAS
```

On the VPS:

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
db_container='REPLACE_WITH_POSTGRES_CONTAINER'
db_owner='REPLACE_WITH_DATABASE_ADMIN'
db_name='REPLACE_WITH_TESLAMATE_DATABASE'
docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$db_container"
docker inspect --format '{{json .NetworkSettings.Networks}}' "$db_container"
docker inspect --format '{{json .NetworkSettings.Ports}}' "$db_container"
```

These selected fields contain operational identifiers; redact them before sharing. Do not infer the database version from a floating image tag. A shared Docker network normally avoids publishing port 5432 on the host. Prefer a separate Compose project and data volume for MateScope; do not bring down the existing TeslaMate project.

## 2. Inspect catalogs in a read-only transaction

This uses the container's existing local database administration access. If authentication is required, use your established secure method; do not weaken authentication rules to run this guide.

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

Check required columns/types against [the adapter](../../backend/matescope/postgresql.py) and the preparation script. Stop if the target is wrong, required tables/types differ, or `matescope_readonly` already exists. Existing roles need a separate membership, ownership, and effective-permission review; this script intentionally fails rather than reusing them.

`PUBLIC` grants also apply to a new role. Investigate any shared write privileges, credential-table reads, schema creation grants, or executable security-definer functions before proceeding. Do not automatically revoke `PUBLIC` privileges: existing services may depend on them. These catalog checks are bounded preflight checks, not a complete audit of every function or extension.

## 3. Create the account — explicit database change

Proceed only after the owner has reviewed the target and script and authorized the role change. The script creates `matescope_readonly`, grants database `CONNECT`, schema `USAGE`, and only the required columns' `SELECT` permissions on four tables. It sets the new role's default transactions to read-only and prompts twice for a new password. It does not change existing roles/passwords or grant future tables automatically. Actual grants, rather than the default read-only setting alone, enforce the access boundary.

From the development machine, at the selected MateScope release checkout, review and transfer the script. Use an unused destination filename if necessary:

```bash
cat scripts/postgresql/prepare-readonly.sql
sha256sum scripts/postgresql/prepare-readonly.sql
scp scripts/postgresql/prepare-readonly.sql YOUR_VPS_SSH_ALIAS:matescope-prepare-readonly.sql
```

Back in the VPS Bash session, with the three variables from step 1 still set:

```bash
sha256sum "$HOME/matescope-prepare-readonly.sql"
cat "$HOME/matescope-prepare-readonly.sql"
```

Confirm the hash matches the local reviewed script before executing. The following creates a temporary script file in the container, runs the role transaction, and removes only that temporary file:

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

Enter a new password for `matescope_readonly` at the interactive prompts; do not put it in command arguments. Success ends with `COMMIT`. A SQL error stops the file and the uncommitted transaction rolls back when the connection closes. On a timeout, disconnect, or uncertain result, inspect whether the role exists before retrying; never drop or overwrite an account automatically. No PostgreSQL restart or configuration reload is required for these role/grant changes.

## 4. Verify and connect MateScope

In MateScope's PostgreSQL settings, enable the connection and enter:

| Field | Value |
| --- | --- |
| Host | PostgreSQL service alias on the shared Docker network, not localhost |
| Port | Internal PostgreSQL port, normally `5432` |
| Database | The inspected TeslaMate database name |
| Username | `matescope_readonly` |
| Password | The new password entered above |
| SSL mode | Match the actual server configuration; do not assume TLS is enabled |

If PostgreSQL has SSL disabled, `disable` matches that configuration and provides **no transport encryption**. Keep that connection on the intended host-local Docker network. For a TLS-enabled server, choose certificate-verifying settings appropriate to its certificates; `prefer` does not guarantee encryption. This guide does not change the server's TLS or authentication configuration.

Save, then explicitly test the saved connection. Save alone does not test it. The test checks effective privileges, required columns/types, and schema access without writing TeslaMate data. It rejects privileged roles, table/column writes in `public`/`private`, and read access to `tokens`/`users` there. Do not test read-only behavior by attempting production writes, even inside a transaction. Local socket or loopback authentication may use `trust`; a successful local login alone does not validate the password used across the Docker network.

If validation fails, retain the error category and inspect metadata; do not broaden grants to all tables or reuse the TeslaMate administrator. On success, start real-data acceptance with a small time window. Keep MateScope storage separate and perform later backup/restore only on MateScope resources.
