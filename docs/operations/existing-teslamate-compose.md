# Add MateScope to an existing TeslaMate Compose project

> **English — single source of truth** · [中文](existing-teslamate-compose_zh.md)

Use this guide to manage MateScope in the existing TeslaMate installation directory and Compose project. The repository's root `compose.yaml` and `compose.prod.yaml` describe a separate deployment; do not copy their complete contents over an existing TeslaMate Compose file. Here you add one service and one dedicated volume to that file.

## 1. Prepare the existing installation

Identify the active Compose file, project name, environment files, and any overlays used by your installer. Keep that exact invocation for this procedure: changing `-p`, top-level `name`, `COMPOSE_PROJECT_NAME`, file order, or environment-file selection can select a different project or volumes. The commands below assume the existing entry point is `docker-compose.yml` plus the directory's `.env`, with no additional project-name option or overlay. Adapt the `dc` function if your current invocation differs.

Check whether the installer preserves manual service additions. If it regenerates the file, use its supported customization mechanism and retain the same project and complete file list for later maintenance. This guide does not modify the installer.

Prepare the [dedicated PostgreSQL read-only account](postgresql-readonly.md), an unused host port, and an independent HTTPS hostname. If the account is already prepared, proceed to connection validation without rerunning role creation. Do not publish the PostgreSQL port or reuse its data volume. Save a private copy of the current configuration before editing; this is a configuration backup, not a database backup:

```bash
cd /path/to/existing/teslamate
umask 077
backup_dir=$(mktemp -d ./matescope-config-backup.XXXXXX)
cp -p docker-compose.yml "$backup_dir/"
if [ -f .env ]; then cp -p .env "$backup_dir/"; fi
```

Replace the example directory and filename with the actual installation. These files may contain secrets; keep the backup private and outside version control.

## 2. Merge the service and volume

In the existing file, add `matescope` under the existing `services:` mapping and `matescope-data` under the existing top-level `volumes:` mapping. Create the latter mapping only if it does not exist. **Do not paste a second `services:` or `volumes:` key, replace the file, or remove existing entries.** If either new name is already used, inspect it before proceeding.

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

The example assumes PostgreSQL uses the project's default network; Compose automatically attaches MateScope to the same default network. It does not create another PostgreSQL, MQTT, or mail service. If PostgreSQL uses a named network, add the following under the `matescope` service, using the existing Compose network key and retaining its existing top-level definition:

```yaml
    networks:
      - YOUR_EXISTING_DATABASE_NETWORK_KEY
```

A Compose network key may differ from the runtime Docker network name. Inspect the existing configuration instead of declaring a new network with a guessed name. Do not add repository development/test Compose files to production.

Merge these variables into the existing `.env`, preserving all current entries. Replace the example domain and choose an unused port; `18080` is an example, not a reservation. Update existing MateScope entries rather than duplicating them:

```dotenv
MATESCOPE_IMAGE=ghcr.io/t-liu93/matescope:0.1.0-alpha.1
MATESCOPE_PUBLIC_URL=https://matescope.example.com
MATESCOPE_HOST_PORT=18080
MATESCOPE_TRUSTED_PROXIES=
```

The image tag is the first Alpha; use a reviewed release tag or its published manifest digest for subsequent versions. Keep PostgreSQL credentials out of this file: enter the dedicated account through MateScope's authenticated setup UI. The container always listens on port 8000 in this example; `MATESCOPE_HOST_PORT` changes only the host mapping.

## 3. Configure HTTPS and start only MateScope

For a host-based reverse proxy, forward the whole dedicated hostname to `http://127.0.0.1:18080` (adjust the host port if changed). Preserve the browser's `Origin` header. `MATESCOPE_PUBLIC_URL` must match the browser-visible HTTPS origin, including any non-default port. Subpath hosting is unsupported. Restrict access until you create the first administrator.

A containerized proxy cannot reach the host through its own `127.0.0.1`. Attach it and MateScope to an appropriate shared network using your existing proxy configuration, then use `http://matescope:8000` as upstream. Preserve MateScope's database network when adding a proxy network. The optional trusted-proxy setting must identify only the immediate proxy addresses actually seen by the app, never `*`; leaving it empty ignores forwarded headers. This guide does not change existing proxy routes or server TLS settings. Production cookies require HTTPS.

From the same installation directory and shell, validate the merged configuration before starting:

```bash
dc() { docker compose -f docker-compose.yml "$@"; }
dc config --quiet
dc config --services
dc pull matescope
dc up -d --no-deps matescope
dc ps matescope
curl --fail http://127.0.0.1:18080/api/v1/readiness
```

`config --services` should list all existing services plus `matescope`. Check your edits locally to ensure existing services, networks and volumes are unchanged; expanded configuration can contain secrets, so do not publish it. If validation fails, fix the configuration before running `pull` or `up`.

The targeted `up --no-deps matescope` creates/recreates only MateScope; it does not request restarting the database, TeslaMate, or Grafana. It creates MateScope's dedicated volume, normally named `<existing-project>_matescope-data`, and joins its configured networks. Do not run whole-project `down`, `down --volumes`, `up --force-recreate`, or `--remove-orphans` for this addition. Readiness verifies the application, not PostgreSQL access.

## 4. Test the account and real data

Open the HTTPS hostname, create the administrator, and configure PostgreSQL:

| Setting | Value |
| --- | --- |
| Enable | Checked |
| Host | Existing database service alias, often `database`; not localhost |
| Port | Internal database port, normally `5432` |
| Database | Existing TeslaMate database name |
| Username | `matescope_readonly` |
| Password | The new dedicated account password |
| SSL | Match the actual PostgreSQL TLS configuration |

Save and explicitly choose **Test Saved Connection**. It checks effective permissions and required schema; do not perform write probes against production. If PostgreSQL has SSL disabled, `disable` matches that configuration but does not encrypt transport; keep the connection on the intended internal Docker network. See the [account guide](postgresql-readonly.md) for failure handling.

After success, select a small time window and compare representative trips and charges with Grafana. MQTT/SMTP may be skipped initially. A real SMTP test sends mail; trigger it only intentionally. Preserve both the SQLite database and encryption key in the MateScope volume.

If you need to stop the new application while investigating, use:

```bash
dc stop matescope
```

This leaves its volume and existing TeslaMate services intact. Later image changes should also target only `matescope`, after backing up its application data. Integrating the service does not by itself complete real VPS acceptance or prove installer upgrades will preserve your edits.
