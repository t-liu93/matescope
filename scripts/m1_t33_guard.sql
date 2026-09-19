-- Only mounted by compose.m1-t33.scale.yaml after the base synthetic schema.
-- A distinct marker makes mutation tooling reject development and production DBs.
UPDATE public.matescope_synthetic_guard
SET identity = 'matescope-synthetic-m1-t33'
WHERE identity = 'matescope-synthetic-m0-t04';
DO $$
BEGIN
  IF (SELECT identity FROM public.matescope_synthetic_guard) <> 'matescope-synthetic-m1-t33' THEN
    RAISE EXCEPTION 'M1-T33 synthetic guard was not established';
  END IF;
END $$;
