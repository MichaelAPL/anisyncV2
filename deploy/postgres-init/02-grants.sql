-- Idempotent grants. Runs once at database creation and again after every migration (the pre-deploy hook),
-- because default privileges only cover objects created by anisync_owner: anything created by another role
-- would otherwise be invisible to the app.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anisync_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anisync_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO anisync_app;

-- The app never changes migration state.
DO $$
BEGIN
  IF to_regclass('public.alembic_version') IS NOT NULL THEN
    REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM anisync_app;
  END IF;
END
$$;
