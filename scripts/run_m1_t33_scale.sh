#!/usr/bin/env bash
# Reproducible M1-T33 scale run. It creates a new owned project unless the
# caller explicitly supplies one, and cleans precisely that project's resources.
set -euo pipefail

if [[ -n ${MATESCOPE_M1_T33_PROJECT:-} ]]; then
  project=$MATESCOPE_M1_T33_PROJECT
else
  project="matescope-m1-t33-$(date -u +%Y%m%d%H%M%S)-$$-$RANDOM"
fi
pg_port=${MATESCOPE_M1_T33_PG_PORT:-25433}
app_port=${MATESCOPE_M1_T33_APP_PORT:-49233}
guard=matescope-synthetic-m1-t33
case "$project" in
  matescope-m1-t33-[a-z0-9][a-z0-9_-]*) ;;
  *) echo "project must begin matescope-m1-t33- and use lowercase letters, digits, _ or -" >&2; exit 2 ;;
esac
case "$pg_port:$app_port" in 25432:*|*":8000") echo "refusing known small-test/default port" >&2; exit 2;; esac
for port in "$pg_port" "$app_port"; do
  if ss -ltn "sport = :$port" | grep -q LISTEN; then echo "port $port is occupied" >&2; exit 2; fi
done
export MATESCOPE_M1_T33_PROJECT=$project MATESCOPE_M1_T33_PG_PORT=$pg_port MATESCOPE_M1_T33_APP_PORT=$app_port
echo "M1-T33 owned Compose project: $project"

reject_existing_resources() {
  local kind resource found=0
  local -a resource_command
  for kind in container volume network; do
    case "$kind" in
      container) resource_command=(docker ps -a --format '{{.Names}}') ;;
      volume) resource_command=(docker volume ls --format '{{.Name}}') ;;
      network) resource_command=(docker network ls --format '{{.Name}}') ;;
    esac
    while IFS= read -r resource; do
      case "$resource" in
        "$project"*)
          echo "refusing existing $kind for project $project: $resource" >&2
          found=1
          ;;
      esac
    done < <("${resource_command[@]}")
  done
  if (( found )); then
    echo "owned project name already has resources; choose a new project" >&2
    exit 2
  fi
}

reject_existing_resources
mkdir -p development-notes/M1/M1-T33
results_file=$(mktemp "development-notes/M1/M1-T33/scale-results-${project}.XXXXXX.json")
echo "M1-T33 scale results: $results_file"
cleanup() {
  docker compose -p "$project" -f compose.m1-t33.scale.yaml down --volumes --remove-orphans
}
trap cleanup EXIT
docker compose -p "$project" -f compose.m1-t33.scale.yaml up -d --build
until docker compose -p "$project" -f compose.m1-t33.scale.yaml exec -T postgres psql -X -U teslamate_admin -d teslamate_synthetic -Atqc "SELECT identity FROM public.matescope_synthetic_guard" | grep -qx "$guard"; do sleep 1; done
export MATESCOPE_M1_T33_PG_DSN="postgresql://teslamate_admin:synthetic-only@127.0.0.1:${pg_port}/teslamate_synthetic"
PYTHONPATH=backend uv run python scripts/m1_t33_scale.py | tee "$results_file"
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${app_port}" MATESCOPE_M1_T33_BROWSER=1 pnpm --dir frontend exec playwright test --config playwright.m1-t33-scale.config.ts
