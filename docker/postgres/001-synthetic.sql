-- Development only. All names, positions, identifiers and credentials are synthetic.
\connect teslamate_synthetic
CREATE TABLE public.matescope_synthetic_guard (identity text PRIMARY KEY);
INSERT INTO public.matescope_synthetic_guard VALUES ('matescope-synthetic-m0-t04');
CREATE TABLE public.cars (id smallint PRIMARY KEY, name text, model text);
CREATE TABLE public.drives (
 id integer PRIMARY KEY, car_id smallint REFERENCES cars(id),
 start_date timestamp without time zone NOT NULL, end_date timestamp without time zone,
 distance double precision, duration_min smallint, speed_max smallint
);
CREATE TABLE public.charging_processes (
 id integer PRIMARY KEY, car_id smallint REFERENCES cars(id),
 start_date timestamp without time zone NOT NULL, end_date timestamp without time zone,
 charge_energy_added numeric(8,2), duration_min smallint
);
CREATE TABLE public.positions (
 id integer PRIMARY KEY, car_id smallint REFERENCES cars(id), drive_id integer REFERENCES drives(id),
 date timestamp without time zone NOT NULL, latitude numeric(8,6), longitude numeric(9,6)
);
CREATE INDEX ON drives (car_id,start_date DESC,id DESC);
CREATE INDEX ON charging_processes (car_id,start_date DESC,id DESC);
CREATE INDEX ON positions (drive_id,date,id);
CREATE TABLE public.tokens (id integer PRIMARY KEY, access text, refresh text);
CREATE SCHEMA private;
CREATE TABLE private.tokens (id integer PRIMARY KEY, access bytea, refresh bytea);
INSERT INTO cars VALUES (1,'SYNTHETIC Atlas','Model 3'),(2,'SYNTHETIC Boreal',NULL);
INSERT INTO drives VALUES
 (1,1,timezone('UTC',now())-interval '2 days',timezone('UTC',now())-interval '2 days'+interval '25 minutes',12.5,25,72),
 (2,1,timezone('UTC',now())-interval '1 day',NULL,NULL,NULL,NULL),
 (3,2,timezone('UTC',now())-interval '3 days',timezone('UTC',now())-interval '3 days'+interval '5 minutes',NULL,5,NULL),
 (4,1,timezone('UTC',now())-interval '120 days',timezone('UTC',now())-interval '120 days'+interval '10 minutes',3.0,10,40),
 (5,1,timezone('UTC',now())-interval '2 days',timezone('UTC',now())-interval '2 days'+interval '2 minutes',1.0,2,30);
INSERT INTO charging_processes VALUES
 (1,1,timezone('UTC',now())-interval '2 days',timezone('UTC',now())-interval '2 days'+interval '45 minutes',22.50,45),
 (2,2,timezone('UTC',now())-interval '1 day',NULL,NULL,NULL);
INSERT INTO positions
 SELECT 10000+i,1,1,(SELECT start_date FROM drives WHERE id=1)+i*interval '200 milliseconds',
 CASE WHEN i BETWEEN 1400 AND 1450 THEN NULL ELSE 1.0+i*0.000001 END,
 CASE WHEN i BETWEEN 1400 AND 1450 THEN NULL ELSE 2.0+i*0.000001 END
 FROM generate_series(0,5000) i;
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
