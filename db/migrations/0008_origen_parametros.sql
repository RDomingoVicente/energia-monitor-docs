-- Origen de cada parámetro: contrato, regulado (norma publicada) o estimado.
--
-- Sustituye la columna booleana `estimado`, que mezclaba valores regulados
-- (pérdidas, pagos por capacidad, ROM, FNEE, tasa municipal) con
-- estimaciones. "Sin valor" no es un origen: es valor IS NULL.
--
--   contrato  pactado con la comercializadora (RFE).
--   regulado  fijado por una norma publicada (peajes, cargos, impuestos,
--             pérdidas estándar, pagos por capacidad, ROM, FNEE, tasa).
--   estimado  sin norma ni serie todavía (SSAA, ROS): aproximación hasta
--             tener la serie de ESIOS o la resolución definitiva.

ALTER TABLE contrato_parametro ADD COLUMN origen text;

UPDATE contrato_parametro SET origen = CASE
    WHEN NOT estimado AND clave = 'rfe' THEN 'contrato'
    WHEN NOT estimado THEN 'regulado'
    -- Filas de 0003 con norma publicada (y el FNEE de 2025 aún no localizado).
    WHEN clave IN ('perdidas', 'pagos_capacidad', 'rom', 'fnee', 'tasa_municipal')
         AND (fuente LIKE 'Circular CNMC 3/2020%'
              OR fuente LIKE 'Orden TED/%'
              OR fuente LIKE 'RDL 2/2004%'
              OR fuente LIKE 'Pendiente: valor de 2025%')
        THEN 'regulado'
    ELSE 'estimado'
END;

ALTER TABLE contrato_parametro
    ALTER COLUMN origen SET NOT NULL,
    ADD CONSTRAINT contrato_parametro_origen_valido
        CHECK (origen IN ('contrato', 'regulado', 'estimado')),
    DROP CONSTRAINT contrato_parametro_pendiente_estimado,
    -- Un valor pactado en el contrato siempre se conoce.
    ADD CONSTRAINT contrato_parametro_contrato_con_valor
        CHECK (valor IS NOT NULL OR origen <> 'contrato');

-- La vista depende de `estimado`: se recrea con los tres estados.
DROP VIEW v_precio_final;
ALTER TABLE contrato_parametro DROP COLUMN estimado;

CREATE VIEW v_precio_final AS
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
        p.ssaa, p.pagos_capacidad, p.ros, p.rom, p.perdidas, p.fnee, p.tasa_municipal,
        coalesce(p.componentes_regulados, '{}') AS componentes_regulados,
        coalesce(p.componentes_estimados, '{}') AS componentes_estimados
    FROM tramo t
    LEFT JOIN LATERAL (
        SELECT
            max(cp.valor) FILTER (WHERE cp.clave = 'rfe')                AS rfe,
            max(cp.valor) FILTER (WHERE cp.clave = 'peaje')              AS peaje,
            max(cp.valor) FILTER (WHERE cp.clave = 'cargo')              AS cargo,
            max(cp.valor) FILTER (WHERE cp.clave = 'impuesto_electrico') AS impuesto_electrico,
            max(cp.valor) FILTER (WHERE cp.clave = 'iva')                AS iva,
            max(cp.valor) FILTER (WHERE cp.clave = 'ssaa')               AS ssaa,
            max(cp.valor) FILTER (WHERE cp.clave = 'pagos_capacidad')    AS pagos_capacidad,
            max(cp.valor) FILTER (WHERE cp.clave = 'ros')                AS ros,
            max(cp.valor) FILTER (WHERE cp.clave = 'rom')                AS rom,
            max(cp.valor) FILTER (WHERE cp.clave = 'perdidas')           AS perdidas,
            max(cp.valor) FILTER (WHERE cp.clave = 'fnee')               AS fnee,
            max(cp.valor) FILTER (WHERE cp.clave = 'tasa_municipal')     AS tasa_municipal,
            array_agg(cp.clave ORDER BY cp.clave)
                FILTER (WHERE cp.origen = 'regulado' AND cp.valor IS NOT NULL) AS componentes_regulados,
            array_agg(cp.clave ORDER BY cp.clave)
                FILTER (WHERE cp.origen = 'estimado' AND cp.valor IS NOT NULL) AS componentes_estimados
        FROM contrato_parametro cp
        WHERE cp.contrato = t.contrato
          AND cp.periodo IN (t.periodo, '*')
          AND cp.vigencia @> t.fecha_local
    ) p ON true
),
calculo AS (
    SELECT
        param.*,
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
    FROM param
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
    componentes_sin_valor
FROM calculo;

COMMENT ON VIEW v_precio_final IS
    'Precio final por tramo y contrato en €/kWh (cláusula 4.2 + peajes, cargos, IEE e IVA).';

-- Los privilegios por defecto de 0006 ya lo cubren; explícito por claridad.
GRANT SELECT ON v_precio_final TO grafana_ro;
