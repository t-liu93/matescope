-- Development only. All names, positions, identifiers and credentials are synthetic.
\connect teslamate_synthetic
CREATE TABLE public.matescope_synthetic_guard (identity text PRIMARY KEY);
INSERT INTO public.matescope_synthetic_guard VALUES ('matescope-synthetic-m0-t04');
CREATE TABLE public.cars (
 id smallint PRIMARY KEY, name text, model text, efficiency numeric(8,4)
);
CREATE TABLE public.addresses (
 id integer PRIMARY KEY, name text, road text, house_number text, city text
);
CREATE TABLE public.geofences (id integer PRIMARY KEY, name text);
CREATE TABLE public.drives (
 id integer PRIMARY KEY, car_id smallint REFERENCES cars(id),
 start_date timestamp without time zone NOT NULL, end_date timestamp without time zone,
 distance double precision, duration_min smallint, speed_max smallint,
 start_position_id integer, end_position_id integer,
 start_address_id integer REFERENCES addresses(id), end_address_id integer REFERENCES addresses(id),
 start_geofence_id integer REFERENCES geofences(id), end_geofence_id integer REFERENCES geofences(id),
 start_rated_range_km numeric(8,2), end_rated_range_km numeric(8,2),
 start_ideal_range_km numeric(8,2), end_ideal_range_km numeric(8,2)
);
CREATE TABLE public.charging_processes (
 id integer PRIMARY KEY, car_id smallint REFERENCES cars(id),
 start_date timestamp without time zone NOT NULL, end_date timestamp without time zone,
 charge_energy_added numeric(8,2), duration_min smallint,
 address_id integer REFERENCES addresses(id), geofence_id integer REFERENCES geofences(id),
 start_battery_level smallint, end_battery_level smallint,
 charge_energy_used numeric(8,2), cost numeric(8,2)
);
CREATE TABLE public.positions (
 id integer PRIMARY KEY, car_id smallint REFERENCES cars(id), drive_id integer REFERENCES drives(id),
 date timestamp without time zone NOT NULL, latitude numeric(8,6), longitude numeric(9,6),
 odometer numeric(10,2), battery_level smallint,
 rated_battery_range_km numeric(8,2), ideal_battery_range_km numeric(8,2),
 speed numeric(8,2), power numeric(8,2), inside_temp numeric(8,2),
 outside_temp numeric(8,2), elevation numeric(10,2)
);
CREATE TABLE public.charges (
 id integer PRIMARY KEY, charging_process_id integer NOT NULL REFERENCES charging_processes(id),
 date timestamp without time zone NOT NULL, battery_level smallint,
 rated_battery_range_km numeric(8,2), ideal_battery_range_km numeric(8,2),
 charger_power numeric(8,2), outside_temp numeric(8,2)
);
ALTER TABLE public.drives
 ADD CONSTRAINT drives_start_position_fk FOREIGN KEY (start_position_id) REFERENCES positions(id),
 ADD CONSTRAINT drives_end_position_fk FOREIGN KEY (end_position_id) REFERENCES positions(id);
CREATE INDEX ON drives (car_id,start_date DESC,id DESC);
CREATE INDEX ON charging_processes (car_id,start_date DESC,id DESC);
CREATE INDEX ON positions (drive_id,date,id);
CREATE INDEX ON charges (charging_process_id,date,id);
CREATE TABLE public.tokens (id integer PRIMARY KEY, access text, refresh text);
CREATE SCHEMA private;
CREATE TABLE private.tokens (id integer PRIMARY KEY, access bytea, refresh bytea);
INSERT INTO cars VALUES (1,'SYNTHETIC Atlas','Model 3',0.1800),(2,'SYNTHETIC Boreal',NULL,NULL);
INSERT INTO addresses VALUES
 (1,'Synthetic home','Example Road','1','Amsterdam'),
 (2,NULL,'Old Road',NULL,'Utrecht'),
 (3,NULL,NULL,NULL,NULL);
INSERT INTO geofences VALUES (1,'Synthetic home'),(2,NULL);
INSERT INTO drives VALUES
 (1,1,timezone('UTC',now())-interval '2 days',timezone('UTC',now())-interval '2 days'+interval '25 minutes',12.5,25,72),
 (2,1,timezone('UTC',now())-interval '1 day',NULL,NULL,NULL,NULL),
 (3,2,timezone('UTC',now())-interval '3 days',timezone('UTC',now())-interval '3 days'+interval '5 minutes',NULL,5,NULL),
 (4,1,timezone('UTC',now())-interval '120 days',timezone('UTC',now())-interval '120 days'+interval '10 minutes',3.0,10,40),
 (5,1,timezone('UTC',now())-interval '2 days',timezone('UTC',now())-interval '2 days'+interval '2 minutes',1.0,2,30),
 (6,2,TIMESTAMP '2021-02-03 10:00:00',TIMESTAMP '2021-02-03 10:42:00',42.0,42,95),
 (7,1,TIMESTAMP '2024-06-11 08:00:00',NULL,NULL,NULL,NULL);
INSERT INTO charging_processes
 (id,car_id,start_date,end_date,charge_energy_added,duration_min)
 VALUES
 (1,1,timezone('UTC',now())-interval '2 days',timezone('UTC',now())-interval '2 days'+interval '45 minutes',22.50,45),
 (2,2,timezone('UTC',now())-interval '1 day',NULL,NULL,NULL);
INSERT INTO charging_processes
 (id,car_id,start_date,end_date,charge_energy_added,duration_min,address_id,geofence_id,
  start_battery_level,end_battery_level,charge_energy_used,cost)
 VALUES
 (3,2,TIMESTAMP '2021-02-03 09:00:00',TIMESTAMP '2021-02-03 10:00:00',15.00,60,1,2,20,80,13.50,0),
 (4,1,TIMESTAMP '2024-06-11 07:00:00',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO positions
 SELECT 10000+i,1,1,(SELECT start_date FROM drives WHERE id=1)+i*interval '200 milliseconds',
 CASE WHEN i BETWEEN 1400 AND 1450 THEN NULL ELSE 1.0+i*0.000001 END,
 CASE WHEN i BETWEEN 1400 AND 1450 THEN NULL ELSE 2.0+i*0.000001 END
 FROM generate_series(0,5000) i;
UPDATE public.drives SET
 start_position_id=10000, end_position_id=15000,
 start_address_id=1, end_address_id=2, start_geofence_id=1, end_geofence_id=2,
 start_rated_range_km=300, end_rated_range_km=288,
 start_ideal_range_km=330, end_ideal_range_km=316
 WHERE id=1;
INSERT INTO public.positions
 (id,car_id,drive_id,date,latitude,longitude,odometer,battery_level,
  rated_battery_range_km,ideal_battery_range_km,speed,power,inside_temp,outside_temp,elevation)
 VALUES
 (20001,2,6,TIMESTAMP '2021-02-03 10:00:00',52.1,5.1,12345.6,75,210,245,0,0,19,4,3),
 (20002,2,6,TIMESTAMP '2021-02-03 10:42:00',52.2,5.2,12387.6,58,168,198,0,0,NULL,5,6),
 (30001,1,NULL,timezone('UTC',now())-interval '1 hour',NULL,NULL,98765.4,82,250,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO public.charges
 (id,charging_process_id,date,battery_level,rated_battery_range_km,ideal_battery_range_km,charger_power,outside_temp)
 VALUES
 (1,1,timezone('UTC',now())-interval '2 days'+interval '1 minute',40,120,150,7.2,8),
 (2,1,timezone('UTC',now())-interval '2 days'+interval '44 minutes',80,240,NULL,NULL,9),
 (3,3,TIMESTAMP '2021-02-03 09:30:00',50,NULL,180,11.0,NULL),
 (4,4,TIMESTAMP '2024-06-11 07:30:00',NULL,NULL,NULL,NULL,NULL);
CREATE ROLE matescope_readonly LOGIN PASSWORD 'synthetic-reader-only'
 NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE teslamate_synthetic TO matescope_readonly;
GRANT USAGE ON SCHEMA public TO matescope_readonly;
GRANT SELECT (id,name,model) ON public.cars TO matescope_readonly;
GRANT SELECT (id,car_id,start_date,end_date,distance,duration_min,speed_max)
 ON public.drives TO matescope_readonly;
GRANT SELECT (id,car_id,start_date,end_date,charge_energy_added,duration_min)
 ON public.charging_processes TO matescope_readonly;
GRANT SELECT (id,drive_id,date,latitude,longitude) ON public.positions TO matescope_readonly;
ALTER ROLE matescope_readonly SET default_transaction_read_only=on;
