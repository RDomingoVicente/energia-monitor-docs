-- Precio final por tramo y contrato (cláusula 4.2 del contrato Feníe).
--
-- Por tramo de liquidación p, en €/MWh salvo indicación:
--   mercado_p = OMIE_p + SSAA + PC_p·1000 + ROS + ROM
--   energia_p = (mercado_p · (1 + %Pérdidas_p) + FNEE + RFE) · (1 + %TasaMunicipal)
--   coste_p   = energia_p / 1000 + Peaje_p + Cargo_p                     (€/kWh)
--   final_p   = coste_p · (1 + IEE) · (1 + IVA)                           (€/kWh)
-- IEE e IVA se aplican como coste marginal del kWh (sin término de potencia).
-- Todo en numeric: sin float en ningún paso.
--
-- Los parámetros estimados (contrato_parametro.estimado) se usan en el
-- cálculo; componentes_estimados lista cuáles han intervenido. El precio solo
-- es NULL si falta algún parámetro sin valor ni estimación (p. ej., fechas
-- anteriores a los parámetros del contrato); componentes_pendientes los lista.

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
                FILTER (WHERE cp.estimado AND cp.valor IS NOT NULL)      AS componentes_estimados
        FROM contrato_parametro cp
        WHERE cp.contrato = t.contrato
          AND cp.periodo IN (t.periodo, '*')
          AND cp.vigencia @> t.fecha_local
    ) p ON true
),
calculo AS (
    SELECT
        param.*,
        -- Cualquier parámetro NULL se propaga al resultado.
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
        ], NULL) AS componentes_pendientes
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
    componentes_estimados,
    componentes_pendientes
FROM calculo;

COMMENT ON VIEW v_precio_final IS
    'Precio final por tramo y contrato en €/kWh (cláusula 4.2 + peajes, cargos, IEE e IVA).';
