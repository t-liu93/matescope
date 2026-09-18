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
GRANT SELECT (efficiency) ON public.cars TO matescope_readonly;
GRANT SELECT (start_position_id,end_position_id,start_address_id,end_address_id,
 start_geofence_id,end_geofence_id,start_rated_range_km,end_rated_range_km,
 start_ideal_range_km,end_ideal_range_km) ON public.drives TO matescope_readonly;
GRANT SELECT (address_id,geofence_id,start_battery_level,end_battery_level,
 charge_energy_used,cost) ON public.charging_processes TO matescope_readonly;
GRANT SELECT (car_id,odometer,battery_level,rated_battery_range_km,
 ideal_battery_range_km,speed,power,inside_temp,outside_temp,elevation)
 ON public.positions TO matescope_readonly;
GRANT SELECT (id,charging_process_id,date,battery_level,rated_battery_range_km,
 ideal_battery_range_km,charger_power,outside_temp) ON public.charges TO matescope_readonly;
GRANT SELECT (id,name,road,house_number,city) ON public.addresses TO matescope_readonly;
GRANT SELECT (id,name) ON public.geofences TO matescope_readonly;
ALTER ROLE matescope_readonly SET default_transaction_read_only=on;
\password matescope_readonly
COMMIT;
