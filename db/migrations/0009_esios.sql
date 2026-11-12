-- Series de ESIOS (REE): componentes de servicios de ajuste (SSAA), previsión
-- de SSAA del PVPC y PVPC 2.0TD, con la definición de SSAA por contrato en
-- tabla y la estimación para los tramos sin valor publicado.
--
-- Indicadores verificados contra https://api.esios.ree.es el 12/11/2026
-- (detalle en docs/contexto.md):
--   * Precio final de la demanda en contratación libre (804) =
--       mercado diario (792) + intradiario (795) + pagos por capacidad (801)
--     + restricciones técnicas (10379) + procesos OS (10382)
--     + participación servicios (10525)                 [error <= 0,05 €/MWh]
--   * 10379 = 793 + 794 + 2401                            [exacto en 2026]
--   * 10382 = 798 + 799 + 800 + 802 + 1366 + 2389 + 2392 + 2395 + 2398
--                                                         [error <= 0,02 €/MWh]
--   SSAA = 10379 + 10382 + 10525 (sin intradiario), guardado por
--   subcomponentes para que la definición sea configurable por contrato.
--
-- OMIE sigue siendo la fuente de precio_mercado: ESIOS no escribe en ella.

-- Catálogo de indicadores que descarga el colector.
CREATE TABLE esios_indicador (
    id                  integer  PRIMARY KEY,
    nombre              text     NOT NULL,
    unidad              text     NOT NULL CHECK (unidad = 'EUR/MWh'),
    resolucion          interval NOT NULL CHECK (resolucion IN (interval '15 minutes', interval '1 hour')),
    -- Zona que se descarga (8741 = Península).
    geo_id              integer  NOT NULL,
    uso                 text     NOT NULL CHECK (uso IN ('ssaa_componente', 'ssaa_control',
                                                         'ssaa_prevision', 'pvpc')),
    -- Componente cuyo total publica REE en otro indicador (control de la suma).
    control             integer  REFERENCES esios_indicador (id),
    -- Un día D se da por completo cuando REE ha actualizado la serie en D+n
    -- (values_updated_at). Antes, sus valores son parciales.
    dias_hasta_completo integer  NOT NULL CHECK (dias_hasta_completo >= 0),
    nota                text,
    UNIQUE (id, uso),
    CONSTRAINT esios_indicador_control_solo_componentes
        CHECK (control IS NULL OR uso = 'ssaa_componente')
);

-- Solo un indicador de PVPC y uno de previsión de SSAA.
CREATE UNIQUE INDEX esios_indicador_uso_unico ON esios_indicador (uso)
    WHERE uso IN ('ssaa_prevision', 'pvpc');

INSERT INTO esios_indicador (id, nombre, unidad, resolucion, geo_id, uso, control, dias_hasta_completo, nota) VALUES
    -- Totales de control (no entran en la suma).
    (10379, 'Precio medio cuarto horario componente restricciones técnicas contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_control', NULL, 2, '= 793 + 794 + 2401'),
    (10382, 'Precio medio cuarto horario componente procesos OS contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_control', NULL, 2,
     '= 798 + 799 + 800 + 802 + 1366 + 2389 + 2392 + 2395 + 2398'),
    -- Restricciones técnicas.
    (793,  'Precio medio cuarto horario componente restricciones PBF contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10379, 2, NULL),
    (794,  'Precio medio cuarto horario componente restricciones tiempo real contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10379, 2, NULL),
    (2401, 'Precio medio cuarto horario componente control de tensión (coste) contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10379, 2, NULL),
    -- Procesos del operador del sistema.
    (798,  'Precio medio cuarto horario componente banda secundaria contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (799,  'Precio medio cuarto horario componente desvíos contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (800,  'Precio medio cuarto horario componente saldo desvíos contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (802,  'Precio medio cuarto horario componente saldo P.O.14.6. contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (1366, 'Precio medio cuarto horario componente incumplimiento energía de balance contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (2389, 'Precio medio cuarto horario componente servicio de respuesta activa incumplimiento energía activada contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (2392, 'Precio medio cuarto horario componente intercambios apoyo con precio contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (2395, 'Precio medio cuarto horario componente coste a BRP servicio de respuesta activa contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    (2398, 'Precio medio cuarto horario componente incumplimiento aFRR contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', 10382, 2, NULL),
    -- Participación en servicios (respuesta activa de la demanda).
    (10525, 'Precio medio cuarto horario componente importe participación servicios contratación libre',
     'EUR/MWh', '15 minutes', 8741, 'ssaa_componente', NULL, 2, NULL),
    -- Previsión de SSAA del PVPC para el día siguiente (se publica en D-1 hacia las 20:45).
    (1871, 'Desglose peaje por defecto 2.0TD Servicios de ajuste',
     'EUR/MWh', '1 hour', 8741, 'ssaa_prevision', NULL, 0,
     'Previsión del PVPC; se escala con la relación real/previsión de los últimos 28 días'),
    -- PVPC 2.0TD: término de energía con peajes y cargos, sin IEE ni IVA.
    (1001, 'Término de facturación de energía activa del PVPC 2.0TD',
     'EUR/MWh', '1 hour', 8741, 'pvpc', NULL, 0, 'Incluye peajes y cargos; sin IEE ni IVA');

-- Valores por tramo [inicio, fin) en UTC. Upsert por (indicador, geo_id, inicio).
-- primer_valor y primera_descarga conservan lo que se vio la primera vez:
-- con ellos se mide cuánto y cuándo revisa REE cada serie.
CREATE TABLE serie_esios (
    indicador        integer       NOT NULL REFERENCES esios_indicador (id),
    geo_id           integer       NOT NULL,
    inicio           timestamptz   NOT NULL,
    fin              timestamptz   NOT NULL,
    valor            numeric(12,4) NOT NULL,
    -- values_updated_at de la respuesta de REE en la última descarga.
    publicado_en     timestamptz   NOT NULL,
    descargado_en    timestamptz   NOT NULL DEFAULT now(),
    primer_valor     numeric(12,4) NOT NULL,
    primera_descarga timestamptz   NOT NULL DEFAULT now(),
    -- Última vez que cambió el valor; NULL si nunca ha cambiado.
    modificado_en    timestamptz,
    PRIMARY KEY (indicador, geo_id, inicio),
    CONSTRAINT serie_esios_tramo_valido CHECK (fin > inicio)
);

COMMENT ON TABLE serie_esios IS
    'Series de ESIOS por tramo (€/MWh), con el primer valor visto y la última publicación de REE.';

-- Definición de SSAA por contrato: qué componentes se suman y desde cuándo.
-- Para cambiarla, cerrar la vigencia de la fila y añadir otra (como en
-- contrato_parametro); ver docs/parametros.md.
CREATE TABLE contrato_ssaa_componente (
    contrato  text      NOT NULL REFERENCES contrato (id),
    indicador integer   NOT NULL,
    uso       text      NOT NULL DEFAULT 'ssaa_componente' CHECK (uso = 'ssaa_componente'),
    vigencia  daterange NOT NULL,
    incluido  boolean   NOT NULL,
    nota      text,
    FOREIGN KEY (indicador, uso) REFERENCES esios_indicador (id, uso),
    CONSTRAINT contrato_ssaa_componente_vigencia_valida
        CHECK (NOT isempty(vigencia) AND NOT lower_inf(vigencia)
               AND lower_inc(vigencia) AND NOT upper_inc(vigencia)),
    CONSTRAINT contrato_ssaa_componente_sin_solape
        EXCLUDE USING gist (contrato WITH =, indicador WITH =, vigencia WITH &&)
);

-- Vivienda: todos los componentes incluidos desde el 01/01/2026.
-- Duda abierta: el contrato dice "Sobrecoste de desvíos: 100% asumido por
-- Fenie Energía". No está claro si se refiere a los desvíos propios de Feníe
-- o a los componentes de desvíos del sistema (799 y 800). Se resolverá
-- conciliando una factura con la curva horaria de i-DE.
INSERT INTO contrato_ssaa_componente (contrato, indicador, vigencia, incluido, nota)
SELECT 'vivienda', i.id, daterange('2026-01-01', NULL, '[)'), true,
       CASE WHEN i.id IN (799, 800) THEN
           'Duda: la cláusula "Sobrecoste de desvíos: 100% asumido por Fenie Energía" podría excluirlo; '
           'pendiente de conciliar con una factura y la curva horaria de i-DE'
       END
FROM esios_indicador i
WHERE i.uso = 'ssaa_componente';

-- Estimación de SSAA para tramos sin valor real (la calcula el colector).
CREATE TABLE ssaa_estimacion (
    contrato     text          NOT NULL REFERENCES contrato (id),
    inicio       timestamptz   NOT NULL,
    fin          timestamptz   NOT NULL,
    valor        numeric(12,4) NOT NULL,
    metodo       text          NOT NULL CHECK (metodo IN ('prevision_ree', 'perfil_14d')),
    calculado_en timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (contrato, inicio),
    CONSTRAINT ssaa_estimacion_tramo_valido CHECK (fin > inicio)
);

COMMENT ON TABLE ssaa_estimacion IS
    'SSAA estimado por tramo: prevision_ree (1871 escalado) o perfil_14d (media por franja horaria local).';

-- SSAA real por contrato y tramo de ESIOS: suma de los componentes incluidos
-- en la definición vigente. Solo hay fila si están todos y el día está
-- completo (fecha local <= fecha de publicación de REE - dias_hasta_completo).
CREATE VIEW v_ssaa_real AS
WITH definicion AS (
    SELECT d.contrato, d.indicador, d.vigencia, i.geo_id, i.dias_hasta_completo
    FROM contrato_ssaa_componente d
    JOIN esios_indicador i ON i.id = d.indicador
    WHERE d.incluido
),
valor AS (
    SELECT d.contrato, s.inicio, s.fin, s.valor,
           (s.inicio AT TIME ZONE 'Europe/Madrid')::date
               <= (s.publicado_en AT TIME ZONE 'Europe/Madrid')::date - d.dias_hasta_completo AS completo
    FROM definicion d
    JOIN serie_esios s
      ON s.indicador = d.indicador
     AND s.geo_id = d.geo_id
     AND d.vigencia @> (s.inicio AT TIME ZONE 'Europe/Madrid')::date
)
SELECT v.contrato, v.inicio, min(v.fin) AS fin, sum(v.valor) AS ssaa_eur_mwh
FROM valor v
GROUP BY v.contrato, v.inicio
HAVING bool_and(v.completo)
   AND count(*) = (SELECT count(*) FROM definicion d
                   WHERE d.contrato = v.contrato
                     AND d.vigencia @> (v.inicio AT TIME ZONE 'Europe/Madrid')::date);

COMMENT ON VIEW v_ssaa_real IS
    'SSAA real (€/MWh) por contrato y tramo según contrato_ssaa_componente; solo días completos.';

-- Precio final: misma fórmula y columnas que en 0008, con SSAA por orden de
-- preferencia: real de ESIOS > estimación (ssaa_estimacion) > parámetro
-- estimado de contrato_parametro. Columnas nuevas al final:
--   ssaa_origen         real | estimado (o el origen del parámetro)
--   ssaa_metodo         esios | prevision_ree | perfil_14d | parametro
--   estado_componentes  {"omie": "real", "ssaa": ..., "<clave>": "<origen>"}
-- CREATE OR REPLACE conserva los permisos de grafana_ro.
CREATE OR REPLACE VIEW v_precio_final AS
WITH tramo AS (
    SELECT
        pm.inicio,
        pm.fin,
        pm.fuente,
        pm.precio_eur_mwh,
        c.id AS contrato,
        periodo_2td(pm.inicio) AS periodo,
        (pm.inicio AT TIME ZONE 'Europe/Madrid')::date AS fecha_local
    FROM precio_mercado pm
    CROSS JOIN contrato c
),
param AS (
    SELECT
        t.*,
        p.rfe, p.peaje, p.cargo, p.impuesto_electrico, p.iva,
        p.pagos_capacidad, p.ros, p.rom, p.perdidas, p.fnee, p.tasa_municipal,
        CASE
            WHEN sr.cubierto = t.fin - t.inicio THEN sr.valor
            WHEN se.valor IS NOT NULL THEN se.valor
            ELSE p.ssaa
        END::numeric AS ssaa,
        CASE
            WHEN sr.cubierto = t.fin - t.inicio THEN 'real'
            WHEN se.valor IS NOT NULL THEN 'estimado'
            ELSE p.ssaa_origen
        END AS ssaa_origen,
        CASE
            WHEN sr.cubierto = t.fin - t.inicio THEN 'esios'
            WHEN se.valor IS NOT NULL THEN se.metodo
            WHEN p.ssaa IS NOT NULL THEN 'parametro'
        END AS ssaa_metodo,
        array_remove(coalesce(p.componentes_regulados, '{}'), 'ssaa') AS regulados_sin_ssaa,
        array_remove(coalesce(p.componentes_estimados, '{}'), 'ssaa') AS estimados_sin_ssaa,
        coalesce(p.origenes, '{}'::jsonb) - 'ssaa' AS origenes_sin_ssaa
    FROM tramo t
    LEFT JOIN LATERAL (
        SELECT
            max(cp.valor) FILTER (WHERE cp.clave = 'rfe')                AS rfe,
            max(cp.valor) FILTER (WHERE cp.clave = 'peaje')              AS peaje,
            max(cp.valor) FILTER (WHERE cp.clave = 'cargo')              AS cargo,
            max(cp.valor) FILTER (WHERE cp.clave = 'impuesto_electrico') AS impuesto_electrico,
            max(cp.valor) FILTER (WHERE cp.clave = 'iva')                AS iva,
            max(cp.valor) FILTER (WHERE cp.clave = 'ssaa')               AS ssaa,
            max(cp.origen) FILTER (WHERE cp.clave = 'ssaa' AND cp.valor IS NOT NULL) AS ssaa_origen,
            max(cp.valor) FILTER (WHERE cp.clave = 'pagos_capacidad')    AS pagos_capacidad,
            max(cp.valor) FILTER (WHERE cp.clave = 'ros')                AS ros,
            max(cp.valor) FILTER (WHERE cp.clave = 'rom')                AS rom,
            max(cp.valor) FILTER (WHERE cp.clave = 'perdidas')           AS perdidas,
            max(cp.valor) FILTER (WHERE cp.clave = 'fnee')               AS fnee,
            max(cp.valor) FILTER (WHERE cp.clave = 'tasa_municipal')     AS tasa_municipal,
            array_agg(cp.clave ORDER BY cp.clave)
                FILTER (WHERE cp.origen = 'regulado' AND cp.valor IS NOT NULL) AS componentes_regulados,
            array_agg(cp.clave ORDER BY cp.clave)
                FILTER (WHERE cp.origen = 'estimado' AND cp.valor IS NOT NULL) AS componentes_estimados,
            jsonb_object_agg(cp.clave, cp.origen) FILTER (WHERE cp.valor IS NOT NULL) AS origenes
        FROM contrato_parametro cp
        WHERE cp.contrato = t.contrato
          AND cp.periodo IN (t.periodo, '*')
          AND cp.vigencia @> t.fecha_local
    ) p ON true
    LEFT JOIN LATERAL (
        -- Media de los tramos de ESIOS que cubren el tramo de mercado.
        SELECT round(avg(r.ssaa_eur_mwh), 4) AS valor, sum(r.fin - r.inicio) AS cubierto
        FROM v_ssaa_real r
        WHERE r.contrato = t.contrato
          AND r.inicio >= t.inicio
          AND r.inicio < t.fin
    ) sr ON true
    LEFT JOIN ssaa_estimacion se
      ON se.contrato = t.contrato AND se.inicio = t.inicio AND se.fin = t.fin
),
estado AS (
    SELECT
        param.*,
        ARRAY(SELECT c FROM unnest(regulados_sin_ssaa
                  || CASE WHEN ssaa_origen = 'regulado' THEN ARRAY['ssaa'] ELSE '{}'::text[] END) AS c
              ORDER BY c) AS componentes_regulados,
        ARRAY(SELECT c FROM unnest(estimados_sin_ssaa
                  || CASE WHEN ssaa_origen = 'estimado' THEN ARRAY['ssaa'] ELSE '{}'::text[] END) AS c
              ORDER BY c) AS componentes_estimados,
        origenes_sin_ssaa
            || jsonb_build_object('omie', 'real')
            || CASE WHEN ssaa_origen IS NOT NULL
                    THEN jsonb_build_object('ssaa', ssaa_origen) ELSE '{}'::jsonb END
            AS estado_componentes
    FROM param
),
calculo AS (
    SELECT
        estado.*,
        -- Cualquier parámetro sin valor se propaga al resultado.
        ((precio_eur_mwh + ssaa + pagos_capacidad * 1000 + ros + rom) * (1 + perdidas)
            + fnee + rfe) * (1 + tasa_municipal) / 1000 AS energia_eur_kwh,
        array_remove(ARRAY[
            CASE WHEN cargo              IS NULL THEN 'cargo' END,
            CASE WHEN fnee               IS NULL THEN 'fnee' END,
            CASE WHEN impuesto_electrico IS NULL THEN 'impuesto_electrico' END,
            CASE WHEN iva                IS NULL THEN 'iva' END,
            CASE WHEN pagos_capacidad    IS NULL THEN 'pagos_capacidad' END,
            CASE WHEN peaje              IS NULL THEN 'peaje' END,
            CASE WHEN perdidas           IS NULL THEN 'perdidas' END,
            CASE WHEN rfe                IS NULL THEN 'rfe' END,
            CASE WHEN rom                IS NULL THEN 'rom' END,
            CASE WHEN ros                IS NULL THEN 'ros' END,
            CASE WHEN ssaa               IS NULL THEN 'ssaa' END,
            CASE WHEN tasa_municipal     IS NULL THEN 'tasa_municipal' END
        ], NULL) AS componentes_sin_valor
    FROM estado
)
SELECT
    inicio,
    fin,
    fuente,
    contrato,
    periodo,
    precio_eur_mwh AS omie_eur_mwh,
    ssaa, pagos_capacidad, ros, rom, perdidas, fnee, rfe, tasa_municipal,
    peaje, cargo, impuesto_electrico, iva,
    energia_eur_kwh,
    energia_eur_kwh + peaje + cargo AS coste_sin_impuestos_eur_kwh,
    (energia_eur_kwh + peaje + cargo) * (1 + impuesto_electrico) * (1 + iva)
        AS precio_final_eur_kwh,
    cardinality(componentes_estimados) > 0 AS incluye_estimados,
    componentes_regulados,
    componentes_estimados,
    componentes_sin_valor,
    ssaa_origen,
    ssaa_metodo,
    estado_componentes
FROM calculo;

COMMENT ON VIEW v_precio_final IS
    'Precio final por tramo y contrato en €/kWh (cláusula 4.2 + peajes, cargos, IEE e IVA); SSAA real de ESIOS si existe.';

-- Comparación horaria con el PVPC 2.0TD (indicador de uso 'pvpc').
-- Ambos sin impuestos (el PVPC incluye peajes y cargos) y con IEE e IVA del
-- contrato aplicados a los dos. Solo horas con todos los tramos de mercado.
CREATE VIEW v_comparacion_pvpc AS
SELECT
    s.inicio,
    s.fin,
    h.contrato,
    h.fuente,
    s.valor / 1000 AS pvpc_sin_impuestos_eur_kwh,
    s.valor / 1000 * (1 + h.impuesto_electrico) * (1 + h.iva) AS pvpc_final_eur_kwh,
    h.coste_sin_impuestos_eur_kwh,
    h.precio_final_eur_kwh,
    h.precio_final_eur_kwh - s.valor / 1000 * (1 + h.impuesto_electrico) * (1 + h.iva)
        AS diferencia_eur_kwh,
    h.incluye_estimados,
    h.ssaa_estimado
FROM esios_indicador i
JOIN serie_esios s ON s.indicador = i.id AND s.geo_id = i.geo_id
CROSS JOIN LATERAL (
    SELECT
        v.contrato,
        v.fuente,
        sum(v.fin - v.inicio)              AS cubierto,
        avg(v.coste_sin_impuestos_eur_kwh) AS coste_sin_impuestos_eur_kwh,
        avg(v.precio_final_eur_kwh)        AS precio_final_eur_kwh,
        max(v.impuesto_electrico)          AS impuesto_electrico,
        max(v.iva)                         AS iva,
        bool_or(v.incluye_estimados)       AS incluye_estimados,
        bool_or(v.ssaa_origen = 'estimado') AS ssaa_estimado
    FROM v_precio_final v
    WHERE v.inicio >= s.inicio AND v.inicio < s.fin
    GROUP BY v.contrato, v.fuente
) h
WHERE i.uso = 'pvpc'
  AND h.cubierto = s.fin - s.inicio;

COMMENT ON VIEW v_comparacion_pvpc IS
    'PVPC 2.0TD frente al precio del contrato por hora (€/kWh), sin y con impuestos.';

-- Sin JIT en esta base de datos: con la búsqueda de SSAA por tramo el
-- planificador sobrestima el coste de v_precio_final y compila con JIT
-- consultas de unos miles de filas (30 días: 2,2 s con JIT y 1,15 s sin él,
-- de ellos ~1 s de compilación). Afecta a las sesiones nuevas.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET jit = off', current_database());
END;
$$;

-- Los privilegios por defecto de 0006 cubren los objetos nuevos; explícito por claridad.
GRANT SELECT ON esios_indicador, serie_esios, contrato_ssaa_componente, ssaa_estimacion,
                v_ssaa_real, v_precio_final, v_comparacion_pvpc TO grafana_ro;
