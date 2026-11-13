-- Fase 2, etapa 1: datos del cargador Orbis Viaris (solo lecturas).
--
-- Muestras de potencia cada 60 s, cambios de estado del conector, sesiones
-- del histórico del cargador y parámetros propios del cargador con vigencia.
-- Instantes en UTC. API del cargador: docs/viaris-api.md.

-- Parámetros del cargador, con el mismo patrón que contrato_parametro
-- (vigencia por fecha local [), origen y fuente), pero en tabla aparte:
-- v_precio_final marca como estimado cualquier parámetro estimado del
-- contrato, y estos no intervienen en el precio.
CREATE TABLE cargador_parametro_clave (
    clave           text PRIMARY KEY,
    unidad          text NOT NULL CHECK (unidad IN ('factor', 'W')),
    descripcion     text NOT NULL
);

INSERT INTO cargador_parametro_clave (clave, unidad, descripcion) VALUES
    ('factor_contador', 'factor',
     'Energía según la distribuidora por cada Wh del contador del cargador (solo coche)'),
    ('potencia_max_estimacion_w', 'W',
     'Potencia supuesta para repartir por tramos las sesiones sin muestras (E1.7)');

CREATE TABLE cargador_parametro (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clave           text      NOT NULL REFERENCES cargador_parametro_clave (clave),
    vigencia        daterange NOT NULL,
    valor           numeric   NOT NULL CHECK (valor > 0),
    origen          text      NOT NULL CHECK (origen IN ('contrato', 'regulado', 'estimado')),
    fuente          text      NOT NULL,
    nota            text,
    CONSTRAINT cargador_parametro_vigencia_valida
        CHECK (NOT isempty(vigencia) AND NOT lower_inf(vigencia)
               AND lower_inc(vigencia) AND NOT upper_inc(vigencia)),
    CONSTRAINT cargador_parametro_sin_solapes
        EXCLUDE USING gist (clave WITH =, vigencia WITH &&)
);

INSERT INTO cargador_parametro (clave, vigencia, valor, origen, fuente, nota) VALUES
    ('factor_contador', '[2026-01-01,)', 0.96, 'estimado',
     'Factura de octubre de 2026: el contador del cargador marca en torno a un 4 % más que el de i-DE',
     'Estimación entre 0 y 5 %; calibrar con la curva cuartohoraria de i-DE'),
    ('potencia_max_estimacion_w', '[2026-01-01,)', 4500, 'estimado',
     'Límite de red de la vivienda en el modulador del cargador (4500 W)',
     'Carga continua desde el inicio de la sesión a esta potencia');

-- Una fila por lectura del modulador. intervalo_s son los segundos desde la
-- muestra anterior; NULL en la primera o tras un hueco de más de 180 s (esa
-- muestra no cuenta energía: el hueco no se rellena).
CREATE TABLE cargador_muestra (
    instante        timestamptz PRIMARY KEY,
    intervalo_s     integer CHECK (intervalo_s BETWEEN 1 AND 180),
    evse_w          numeric NOT NULL,
    home_w          numeric NOT NULL,
    total_w         numeric NOT NULL,
    -- stat.state de /modules/evsm/elements; NULL si esa lectura falló.
    estado          smallint
);

COMMENT ON TABLE cargador_muestra IS
    'Potencias del modulador del cargador (W) cada 60 s; cubre [instante - intervalo_s, instante).';
COMMENT ON COLUMN cargador_muestra.home_w IS
    'homePower; sin verificar si excluye al coche (totalPower = evsePower + homePower).';

-- Cambios de stat.state detectados por el muestreo.
CREATE TABLE cargador_estado (
    conector        text        NOT NULL,
    detectado_en    timestamptz NOT NULL,
    estado          smallint    NOT NULL,
    estado_anterior smallint,
    -- Campos de evsm guardados en bruto: su significado está sin verificar.
    evento          text,
    id_carga        text,
    localtime_raw   bigint,
    PRIMARY KEY (conector, detectado_en)
);

COMMENT ON TABLE cargador_estado IS
    'Cambios de stat.state del conector; detectado_en es el instante de la muestra que lo vio.';

-- Sesiones cerradas del histórico del cargador (búfer de 100 en el equipo).
CREATE TABLE cargador_sesion (
    conector        text        NOT NULL,
    id              bigint      NOT NULL,
    inicio          timestamptz NOT NULL,
    fin             timestamptz NOT NULL,
    energia_wh      numeric     NOT NULL CHECK (energia_wh >= 0),
    origen_inicio   text,
    origen_fin      text,
    linea           text        NOT NULL,
    leida_en        timestamptz NOT NULL DEFAULT now(),
    actualizada_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conector, id),
    CONSTRAINT cargador_sesion_intervalo_valido CHECK (fin >= inicio)
);

CREATE INDEX cargador_sesion_inicio ON cargador_sesion (inicio);

COMMENT ON TABLE cargador_sesion IS
    'Sesiones del histórico del cargador (GET /modules/modulator/historic); linea es el texto original.';

-- Los privilegios por defecto de 0006 ya lo cubren; explícito por claridad.
GRANT SELECT ON cargador_parametro_clave, cargador_parametro, cargador_muestra,
                cargador_estado, cargador_sesion TO grafana_ro;
