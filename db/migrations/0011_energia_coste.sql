-- Fase 2, etapa 1: energía por cuarto de hora y coste del coche y de la casa.
--
-- Origen de la energía de un tramo:
--   muestreo         muestras de potencia del modulador (E1.2, E1.3).
--   estimado_sesion  sesión del histórico sin ninguna muestra, repartida a
--                    potencia constante (E1.7). Nunca sustituye a un tramo
--                    con muestras.
--   (contador)       reservado: diferencia del contador acumulado si se
--                    verifica una lectura HTTP suya (docs/fase2.md).
-- Tramos de 15 min alineados en UTC, como precio_mercado. Todo en numeric
-- salvo la duración de la ventana estimada (make_interval, al microsegundo).

-- Cada muestra cubre [instante - intervalo_s, instante) a la potencia leída
-- en el instante. Con intervalo_s <= 180 s toca como mucho dos tramos.
CREATE VIEW v_energia_muestra AS
SELECT
    m.instante,
    t.tramo                                AS inicio,
    t.tramo + interval '15 minutes'        AS fin,
    t.segundos,
    m.evse_w,
    m.home_w,
    m.total_w,
    m.evse_w  * t.segundos / 3600          AS coche_wh,
    m.home_w  * t.segundos / 3600          AS casa_wh,
    m.total_w * t.segundos / 3600          AS total_wh
FROM cargador_muestra m
CROSS JOIN LATERAL (
    SELECT
        q.tramo,
        extract(epoch FROM least(m.instante, q.tramo + interval '15 minutes')
                         - greatest(m.instante - make_interval(secs => m.intervalo_s), q.tramo)) AS segundos
    FROM (VALUES (date_bin('15 minutes', m.instante - make_interval(secs => m.intervalo_s),
                           TIMESTAMPTZ '2000-01-01 00:00:00+00'))) b (primero)
    CROSS JOIN LATERAL (VALUES (b.primero), (b.primero + interval '15 minutes')) q (tramo)
) t
WHERE m.intervalo_s IS NOT NULL
  AND t.segundos > 0;

COMMENT ON VIEW v_energia_muestra IS
    'Energía (Wh) de cada muestra repartida entre los tramos de 15 min que cubre.';

CREATE VIEW v_energia_tramo_medida AS
SELECT
    inicio,
    fin,
    -- W·s sumados y una sola división por tramo.
    sum(evse_w * segundos) / 3600   AS coche_wh,
    sum(home_w * segundos) / 3600   AS casa_wh,
    sum(total_w * segundos) / 3600  AS total_wh,
    sum(segundos)       AS cubierto_s,
    sum(segundos) < 840 AS incompleto
FROM v_energia_muestra
GROUP BY inicio, fin;

-- E1.7: sesiones sin ninguna muestra entre su inicio y su fin. Carga continua
-- desde el inicio a potencia_max_estimacion_w (vigente en la fecha local del
-- inicio) hasta entregar la energía de la sesión; si a esa potencia no cabe
-- en la sesión, la energía se reparte uniformemente entre el inicio y el fin.
CREATE VIEW v_energia_sesion_estimada AS
WITH sesion AS (
    SELECT
        s.conector,
        s.id,
        s.inicio,
        s.energia_wh,
        p.valor AS potencia_w,
        least(s.fin, s.inicio + make_interval(secs => (s.energia_wh * 3600 / p.valor)::float8)) AS fin_carga
    FROM cargador_sesion s
    JOIN cargador_parametro p
      ON p.clave = 'potencia_max_estimacion_w'
     AND p.vigencia @> (s.inicio AT TIME ZONE 'Europe/Madrid')::date
    WHERE s.energia_wh > 0
      AND s.fin > s.inicio
      AND NOT EXISTS (
          SELECT 1 FROM cargador_muestra m
          WHERE m.instante >= s.inicio AND m.instante <= s.fin
      )
)
SELECT
    se.conector,
    se.id                                  AS sesion,
    t.tramo                                AS inicio,
    t.tramo + interval '15 minutes'        AS fin,
    se.potencia_w,
    se.energia_wh
        * extract(epoch FROM least(se.fin_carga, t.tramo + interval '15 minutes') - greatest(se.inicio, t.tramo))
        / extract(epoch FROM se.fin_carga - se.inicio) AS coche_wh
FROM sesion se
CROSS JOIN LATERAL generate_series(
    date_bin('15 minutes', se.inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00'),
    se.fin_carga - interval '1 microsecond',
    interval '15 minutes'
) t (tramo)
WHERE se.fin_carga > se.inicio;

COMMENT ON VIEW v_energia_sesion_estimada IS
    'Reparto estimado por tramos de las sesiones sin muestras (E1.7), sesión a sesión.';

CREATE VIEW v_energia_tramo AS
SELECT
    inicio, fin, coche_wh, casa_wh, total_wh, cubierto_s, incompleto,
    'muestreo'::text AS origen
FROM v_energia_tramo_medida
UNION ALL
SELECT
    e.inicio, e.fin, e.coche_wh, NULL::numeric, NULL::numeric, NULL::numeric, NULL::boolean,
    'estimado_sesion'::text
FROM (
    SELECT inicio, fin, sum(coche_wh) AS coche_wh
    FROM v_energia_sesion_estimada
    GROUP BY inicio, fin
) e
-- Un tramo con alguna muestra que lo cubra es medido: la estimación no entra.
WHERE NOT EXISTS (
    SELECT 1 FROM cargador_muestra m
    WHERE m.intervalo_s IS NOT NULL
      AND m.instante > e.inicio
      AND m.instante < e.fin + interval '180 seconds'
      AND m.instante - make_interval(secs => m.intervalo_s) < e.fin
);

COMMENT ON VIEW v_energia_tramo IS
    'Energía (Wh) por tramo de 15 min: coche, casa y total; origen muestreo o estimado_sesion.';
COMMENT ON COLUMN v_energia_tramo.casa_wh IS
    'Sin verificar hasta comprobar totalPower = evsePower + homePower durante una carga.';

-- Coste por tramo, contrato, fuente y consumidor (coche o casa). La energía
-- del coche se corrige con factor_contador; la de la casa, no (otro sensor).
CREATE VIEW v_coste_tramo AS
SELECT
    e.inicio,
    e.fin,
    c.consumidor,
    p.contrato,
    p.fuente,
    p.periodo,
    c.energia_wh                                        AS energia_wh_bruta,
    c.factor,
    c.factor_origen,
    c.energia_wh * c.factor / 1000                      AS energia_kwh,
    p.precio_final_eur_kwh,
    c.energia_wh * c.factor / 1000 * p.precio_final_eur_kwh  AS coste_eur,
    pv.pvpc_final_eur_kwh,
    c.energia_wh * c.factor / 1000 * pv.pvpc_final_eur_kwh   AS coste_pvpc_eur,
    e.origen                                            AS origen_energia,
    e.incompleto,
    p.ssaa_origen IS DISTINCT FROM 'real'               AS ssaa_estimado,
    p.incluye_estimados
FROM v_energia_tramo e
LEFT JOIN cargador_parametro f
  ON f.clave = 'factor_contador'
 AND f.vigencia @> (e.inicio AT TIME ZONE 'Europe/Madrid')::date
CROSS JOIN LATERAL (VALUES
    ('coche', e.coche_wh, f.valor,     f.origen),
    ('casa',  e.casa_wh,  1::numeric,  NULL::text)
) c (consumidor, energia_wh, factor, factor_origen)
JOIN v_precio_final p ON p.inicio = e.inicio
LEFT JOIN LATERAL (
    -- PVPC horario (indicador de uso 'pvpc') de la hora que contiene el
    -- tramo, con IEE e IVA del contrato como en v_comparacion_pvpc.
    SELECT s.valor / 1000 * (1 + p.impuesto_electrico) * (1 + p.iva) AS pvpc_final_eur_kwh
    FROM esios_indicador i
    JOIN serie_esios s ON s.indicador = i.id AND s.geo_id = i.geo_id
    WHERE i.uso = 'pvpc' AND s.inicio <= e.inicio AND s.fin > e.inicio
) pv ON true
WHERE c.energia_wh IS NOT NULL;

COMMENT ON VIEW v_coste_tramo IS
    'Energía (kWh corregidos) y coste (€, con IEE e IVA) por tramo, contrato, fuente y consumidor.';

-- Coste por sesión. Sesiones con muestras: las muestras con instante en
-- [inicio, fin), cada parte al precio de su tramo (varias sesiones pueden
-- compartir un cuarto de hora). Sesiones sin muestras: su reparto estimado.
CREATE VIEW v_sesion_coste AS
-- MATERIALIZED: sin él, el planificador subestima las filas de tramo_sesion
-- y recalcula v_precio_final completa por cada tramo (minutos con un mes de
-- muestras). Así los precios se calculan una vez y solo para esos tramos.
WITH tramo_sesion AS MATERIALIZED (
    SELECT s.conector, s.id, em.inicio AS tramo, sum(em.coche_wh) AS coche_wh, 'muestreo'::text AS origen
    FROM cargador_sesion s
    JOIN v_energia_muestra em ON em.instante >= s.inicio AND em.instante < s.fin
    GROUP BY s.conector, s.id, em.inicio
    UNION ALL
    SELECT conector, sesion, inicio, coche_wh, 'estimado_sesion'::text
    FROM v_energia_sesion_estimada
),
precio AS MATERIALIZED (
    SELECT inicio, contrato, fuente, precio_final_eur_kwh, ssaa_origen
    FROM v_precio_final
    WHERE inicio IN (SELECT tramo FROM tramo_sesion)
)
SELECT
    s.conector,
    s.id,
    s.inicio,
    s.fin,
    p.contrato,
    p.fuente,
    t.origen                                                    AS origen_energia,
    s.energia_wh                                                AS energia_wh_cargador,
    sum(t.coche_wh)                                             AS energia_wh_bruta,
    sum(t.coche_wh * f.valor) / 1000                            AS energia_kwh,
    sum(t.coche_wh * f.valor / 1000 * p.precio_final_eur_kwh)  AS coste_eur,
    sum(t.coche_wh * f.valor / 1000 * p.precio_final_eur_kwh)
        / nullif(sum(t.coche_wh * f.valor) / 1000, 0)           AS eur_kwh,
    CASE WHEN t.origen = 'muestreo'
         THEN (sum(t.coche_wh) - s.energia_wh) * 100 / nullif(s.energia_wh, 0)
    END                                                         AS diferencia_pct,
    bool_or(p.ssaa_origen IS DISTINCT FROM 'real')              AS ssaa_estimado
FROM cargador_sesion s
JOIN tramo_sesion t ON t.conector = s.conector AND t.id = s.id
LEFT JOIN cargador_parametro f
  ON f.clave = 'factor_contador'
 AND f.vigencia @> (t.tramo AT TIME ZONE 'Europe/Madrid')::date
JOIN precio p ON p.inicio = t.tramo
GROUP BY s.conector, s.id, s.inicio, s.fin, s.energia_wh, p.contrato, p.fuente, t.origen;

COMMENT ON VIEW v_sesion_coste IS
    'Sesiones del cargador con energía medida o estimada, coste (€, con IEE e IVA) y €/kWh.';
COMMENT ON COLUMN v_sesion_coste.diferencia_pct IS
    'Energía de las muestras frente a la del cargador, en %, antes del factor del contador.';

-- Los privilegios por defecto de 0006 ya lo cubren; explícito por claridad.
GRANT SELECT ON v_energia_muestra, v_energia_tramo_medida, v_energia_sesion_estimada,
                v_energia_tramo, v_coste_tramo, v_sesion_coste TO grafana_ro;
