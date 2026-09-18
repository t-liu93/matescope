#!/usr/bin/env sh
# M1-T03: run only against a disposable synthetic PostgreSQL container.
set -eu

test_image=${MATESCOPE_T03_TEST_POSTGRES_IMAGE:-postgres:17.6-alpine}
test_container_id=''
test_volume=''

cleanup() {
  if [ -n "$test_container_id" ]; then
    docker rm -f "$test_container_id" >/dev/null 2>&1 || true
  fi
  if [ -n "$test_volume" ]; then
    docker volume rm "$test_volume" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

test_volume=$(docker volume create --label com.matescope.test=M1-T03)
if [ "$(docker volume inspect -f '{{index .Labels "com.matescope.test"}}' "$test_volume")" != M1-T03 ]; then
  exit 1
fi
test_container_id=$(docker create --label com.matescope.test=M1-T03 -v "$test_volume":/var/lib/postgresql/data \
  -e POSTGRES_DB=teslamate_synthetic -e POSTGRES_USER=teslamate_admin \
  -e POSTGRES_PASSWORD=synthetic-only "$test_image")
if [ "$(docker inspect -f '{{index .Config.Labels "com.matescope.test"}}' "$test_container_id")" != M1-T03 ]; then
  exit 1
fi
docker start "$test_container_id" >/dev/null
ready_attempt=0
until docker exec "$test_container_id" pg_isready -U teslamate_admin -d teslamate_synthetic >/dev/null 2>&1; do
  ready_attempt=$((ready_attempt + 1))
  if [ "$ready_attempt" -ge 30 ]; then
    docker logs "$test_container_id" >&2 || true
    exit 1
  fi
  sleep 1
done

docker exec -i "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic <<'SQL'
CREATE TABLE public.matescope_synthetic_guard (identity text PRIMARY KEY);
INSERT INTO public.matescope_synthetic_guard VALUES ('matescope-synthetic-m1-t03');
BEGIN READ ONLY;
SELECT identity FROM public.matescope_synthetic_guard WHERE identity = 'matescope-synthetic-m1-t03';
ROLLBACK;
CREATE TABLE cars (id int,name text,model text,efficiency numeric);
CREATE TABLE drives (id int,car_id int,start_date timestamp,end_date timestamp,distance numeric,duration_min int,speed_max int,start_position_id int,end_position_id int,start_address_id int,end_address_id int,start_geofence_id int,end_geofence_id int,start_rated_range_km numeric,end_rated_range_km numeric,start_ideal_range_km numeric,end_ideal_range_km numeric);
CREATE TABLE charging_processes (id int,car_id int,start_date timestamp,end_date timestamp,charge_energy_added numeric,duration_min int,address_id int,geofence_id int,start_battery_level int,end_battery_level int,charge_energy_used numeric,cost numeric);
CREATE TABLE positions (id int,drive_id int,date timestamp,latitude numeric,longitude numeric,car_id int,odometer numeric,battery_level int,rated_battery_range_km numeric,ideal_battery_range_km numeric,speed numeric,power numeric,inside_temp numeric,outside_temp numeric,elevation numeric);
CREATE TABLE charges (id int,charging_process_id int,date timestamp,battery_level int,rated_battery_range_km numeric,ideal_battery_range_km numeric,charger_power numeric,outside_temp numeric);
CREATE TABLE addresses (id int,name text,road text,house_number text,city text);
CREATE TABLE geofences (id int,name text);
CREATE TABLE tokens (id int,access text);
CREATE TABLE unrelated_table (id int);
SQL

docker cp scripts/postgresql/prepare-readonly.sql "$test_container_id":/tmp/prepare-readonly.sql
printf '%s\n%s\n' fresh-synthetic-only fresh-synthetic-only | docker exec -i "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic -f /tmp/prepare-readonly.sql
docker exec -i "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic <<'SQL'
CREATE FUNCTION assert_m1_columns(test_role name) RETURNS void LANGUAGE plpgsql AS $$
DECLARE expected jsonb := '{"cars":["id","name","model","efficiency"],"drives":["id","car_id","start_date","end_date","distance","duration_min","speed_max","start_position_id","end_position_id","start_address_id","end_address_id","start_geofence_id","end_geofence_id","start_rated_range_km","end_rated_range_km","start_ideal_range_km","end_ideal_range_km"],"charging_processes":["id","car_id","start_date","end_date","charge_energy_added","duration_min","address_id","geofence_id","start_battery_level","end_battery_level","charge_energy_used","cost"],"positions":["id","drive_id","date","latitude","longitude","car_id","odometer","battery_level","rated_battery_range_km","ideal_battery_range_km","speed","power","inside_temp","outside_temp","elevation"],"charges":["id","charging_process_id","date","battery_level","rated_battery_range_km","ideal_battery_range_km","charger_power","outside_temp"],"addresses":["id","name","road","house_number","city"],"geofences":["id","name"]}';
BEGIN
 IF EXISTS (
   WITH required AS (
     SELECT key AS table_name, jsonb_array_elements_text(value) AS column_name FROM jsonb_each(expected)
   ), actual AS (
     SELECT table_name, column_name FROM information_schema.column_privileges
     WHERE grantee = test_role::text AND privilege_type = 'SELECT' AND table_schema = 'public'
       AND table_name IN (SELECT key FROM jsonb_each(expected))
   )
   (SELECT * FROM required EXCEPT SELECT * FROM actual) UNION ALL
   (SELECT * FROM actual EXCEPT SELECT * FROM required)
 ) THEN RAISE EXCEPTION 'role % does not have exactly the M0 and M1 allowlist columns', test_role; END IF;
END $$;
SELECT assert_m1_columns('matescope_readonly');
DO $$ BEGIN
 IF has_table_privilege('matescope_readonly','public.tokens','SELECT')
    OR has_table_privilege('matescope_readonly','public.unrelated_table','SELECT') THEN RAISE EXCEPTION 'fresh role reads an unrelated table'; END IF;
END $$;
CREATE ROLE m1_t03_upgrade_reader LOGIN;
CREATE ROLE m1_t03_non_target_reader LOGIN;
GRANT CONNECT ON DATABASE teslamate_synthetic TO m1_t03_upgrade_reader;
GRANT USAGE ON SCHEMA public TO m1_t03_upgrade_reader;
GRANT SELECT (id,name,model) ON cars TO m1_t03_upgrade_reader;
GRANT SELECT (id,car_id,start_date,end_date,distance,duration_min,speed_max) ON drives TO m1_t03_upgrade_reader;
GRANT SELECT (id,car_id,start_date,end_date,charge_energy_added,duration_min) ON charging_processes TO m1_t03_upgrade_reader;
GRANT SELECT (id,drive_id,date,latitude,longitude) ON positions TO m1_t03_upgrade_reader;
GRANT SELECT (id) ON unrelated_table TO m1_t03_upgrade_reader;
SQL

docker cp scripts/postgresql/upgrade-readonly.sql "$test_container_id":/tmp/upgrade-readonly.sql
docker exec "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic -v matescope_role=m1_t03_upgrade_reader -f /tmp/upgrade-readonly.sql
docker exec "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic -v matescope_role=m1_t03_upgrade_reader -f /tmp/upgrade-readonly.sql
docker exec -i "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic <<'SQL'
SELECT assert_m1_columns('m1_t03_upgrade_reader');
DO $$ BEGIN
 IF has_table_privilege('m1_t03_upgrade_reader','public.tokens','SELECT')
    OR NOT has_column_privilege('m1_t03_upgrade_reader','public.unrelated_table','id','SELECT')
    OR has_column_privilege('m1_t03_non_target_reader','public.cars','efficiency','SELECT')
    OR has_column_privilege('m1_t03_non_target_reader','public.charges','outside_temp','SELECT') THEN RAISE EXCEPTION 'upgrade target or non-target isolation check failed'; END IF;
END $$;
SQL

if docker exec "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic -v matescope_role=does_not_exist -f /tmp/upgrade-readonly.sql; then exit 1; fi
if docker exec "$test_container_id" psql -X -v ON_ERROR_STOP=1 -U teslamate_admin -d teslamate_synthetic -f /tmp/upgrade-readonly.sql; then exit 1; fi
printf '%s\n' 'M1-T03 synthetic read-only grant checks passed.'
