-- Run explicitly with psql as the TeslaMate database owner, on the intended database.
-- This script is NEVER executed by MateScope. It creates a new role; existing roles fail.
-- Database owner: review this script before explicit execution. The password is prompted.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE matescope_readonly LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
 NOREPLICATION NOBYPASSRLS;
SELECT format('GRANT CONNECT ON DATABASE %I TO matescope_readonly', current_database()) \gexec
GRANT USAGE ON SCHEMA public TO matescope_readonly;
GRANT SELECT (id,name,model) ON public.cars TO matescope_readonly;
GRANT SELECT (id,car_id,start_date,end_date,distance,duration_min,speed_max)
 ON public.drives TO matescope_readonly;
GRANT SELECT (id,car_id,start_date,end_date,charge_energy_added,duration_min)
 ON public.charging_processes TO matescope_readonly;
GRANT SELECT (id,drive_id,date,latitude,longitude) ON public.positions TO matescope_readonly;
ALTER ROLE matescope_readonly SET default_transaction_read_only=on;
\password matescope_readonly
COMMIT;
