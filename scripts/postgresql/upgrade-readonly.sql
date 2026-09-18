-- Run explicitly with psql as the TeslaMate database owner, on the intended database.
-- This script is NEVER executed by MateScope. It grants M1 columns to one existing role.
-- Example: psql -X -v ON_ERROR_STOP=1 -v matescope_role=existing_reader -f upgrade-readonly.sql
\set ON_ERROR_STOP on
\if :{?matescope_role}
\else
  \echo 'Set matescope_role to the existing role that the owner selected.'
  SELECT 1 / 0;
\endif

BEGIN;
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'matescope_role') AS role_exists \gset
\if :role_exists
\else
  \echo 'The selected matescope_role does not exist; no privileges were changed.'
  SELECT 1 / 0;
\endif

GRANT SELECT (efficiency) ON public.cars TO :"matescope_role";
GRANT SELECT (start_position_id,end_position_id,start_address_id,end_address_id,
 start_geofence_id,end_geofence_id,start_rated_range_km,end_rated_range_km,
 start_ideal_range_km,end_ideal_range_km) ON public.drives TO :"matescope_role";
GRANT SELECT (address_id,geofence_id,start_battery_level,end_battery_level,
 charge_energy_used,cost) ON public.charging_processes TO :"matescope_role";
GRANT SELECT (car_id,odometer,battery_level,rated_battery_range_km,
 ideal_battery_range_km,speed,power,inside_temp,outside_temp,elevation)
 ON public.positions TO :"matescope_role";
GRANT SELECT (id,charging_process_id,date,battery_level,rated_battery_range_km,
 ideal_battery_range_km,charger_power,outside_temp) ON public.charges TO :"matescope_role";
GRANT SELECT (id,name,road,house_number,city) ON public.addresses TO :"matescope_role";
GRANT SELECT (id,name) ON public.geofences TO :"matescope_role";
COMMIT;
