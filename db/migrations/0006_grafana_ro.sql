-- Rol de solo lectura para el datasource de Grafana.
--
-- Se crea sin LOGIN y sin contraseña: el colector le asigna LOGIN y la
-- contraseña de GRAFANA_DB_PASSWORD al arrancar (los secretos no van en git).
-- Los roles son globales del clúster: si ya existe, se reutiliza.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        CREATE ROLE grafana_ro NOLOGIN;
    END IF;
END;
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO grafana_ro', current_database());
END;
$$;

GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;

-- Objetos futuros creados por el usuario que aplica las migraciones.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO grafana_ro;
