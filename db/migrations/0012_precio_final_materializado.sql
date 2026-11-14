-- Precio final materializado (v0.4.1).
--
-- v_precio_final se calcula tramo a tramo (~0,25 ms por tramo y contrato) y
-- un filtro por inicio no llega a precio_mercado: cualquier consulta que la
-- cruce con otra cosa la calcula entera. mv_precio_final es una copia exacta
-- (mismas columnas, mismas filas) que el servicio refresca al terminar cada
-- ejecución del colector y tras cada cambio por CLI (docs/parametros.md).
-- v_precio_final sigue siendo la única definición del precio.
--
-- Si cambian las columnas de v_precio_final, hay que borrar y volver a crear
-- esta vista materializada (y sus índices) en la misma migración.

-- PostgreSQL 17 crea y refresca las vistas materializadas con search_path =
-- pg_catalog, pg_temp: cualquier función que use v_precio_final debe
-- cualificar sus tablas. Mismo cuerpo que en 0004, con public.festivo.
CREATE OR REPLACE FUNCTION periodo_2td(p_instante timestamptz) RETURNS text
LANGUAGE sql STABLE AS $$
    WITH l AS (SELECT p_instante AT TIME ZONE 'Europe/Madrid' AS t)
    SELECT CASE
        WHEN extract(isodow FROM l.t) IN (6, 7)
          OR EXISTS (SELECT 1 FROM public.festivo f WHERE f.fecha = l.t::date)
            THEN 'P3'
        WHEN extract(hour FROM l.t) < 8 THEN 'P3'
        WHEN extract(hour FROM l.t) IN (10, 11, 12, 13, 18, 19, 20, 21) THEN 'P1'
        ELSE 'P2'
    END
    FROM l
$$;

CREATE MATERIALIZED VIEW mv_precio_final AS
SELECT * FROM v_precio_final
WITH DATA;

-- Único: lo exige REFRESH ... CONCURRENTLY. Uno por tramo, fuente y contrato
-- (PK de precio_mercado por contrato).
CREATE UNIQUE INDEX mv_precio_final_clave ON mv_precio_final (contrato, fuente, inicio);

-- Hora UTC que contiene el tramo, para cruzar con el PVPC por igualdad
-- (el indicador 1001 es horario; date_bin sobre timestamptz es inmutable).
CREATE INDEX mv_precio_final_hora
    ON mv_precio_final (date_bin('1 hour', inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00'));

COMMENT ON MATERIALIZED VIEW mv_precio_final IS
    'Copia de v_precio_final; se refresca al final de cada ejecución del colector y con `refrescar`.';

-- Comparación con el PVPC: las mismas columnas que en 0009, desde la
-- materializada y con los tramos de cada hora por igualdad de hora.
CREATE OR REPLACE VIEW v_comparacion_pvpc AS
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
    FROM mv_precio_final v
    WHERE date_bin('1 hour', v.inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00') = s.inicio
    GROUP BY v.contrato, v.fuente
) h
WHERE i.uso = 'pvpc'
  AND s.fin = s.inicio + interval '1 hour'
  AND h.cubierto = s.fin - s.inicio;

-- Coste por tramo: mismas columnas que en 0011; precio de la materializada y
-- PVPC de la hora del tramo por igualdad (PK de serie_esios).
CREATE OR REPLACE VIEW v_coste_tramo AS
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
JOIN mv_precio_final p ON p.inicio = e.inicio
LEFT JOIN LATERAL (
    -- PVPC horario (indicador de uso 'pvpc') de la hora UTC que contiene el
    -- tramo, con IEE e IVA del contrato como en v_comparacion_pvpc.
    SELECT s.valor / 1000 * (1 + p.impuesto_electrico) * (1 + p.iva) AS pvpc_final_eur_kwh
    FROM esios_indicador i
    JOIN serie_esios s ON s.indicador = i.id AND s.geo_id = i.geo_id
    WHERE i.uso = 'pvpc'
      AND s.inicio = date_bin('1 hour', e.inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00')
      AND s.fin = s.inicio + interval '1 hour'
    -- OFFSET 0: sin él el planificador aplana el LATERAL en un join con
    -- filtro (millones de filas descartadas); así es una búsqueda por la PK
    -- de serie_esios para cada tramo.
    OFFSET 0
) pv ON true
WHERE c.energia_wh IS NOT NULL;

-- Coste por sesión: mismas columnas que en 0011; precio de la materializada
-- por su índice único, sin recalcular v_precio_final.
CREATE OR REPLACE VIEW v_sesion_coste AS
WITH tramo_sesion AS MATERIALIZED (
    SELECT s.conector, s.id, em.inicio AS tramo, sum(em.coche_wh) AS coche_wh, 'muestreo'::text AS origen
    FROM cargador_sesion s
    JOIN v_energia_muestra em ON em.instante >= s.inicio AND em.instante < s.fin
    GROUP BY s.conector, s.id, em.inicio
    UNION ALL
    SELECT conector, sesion, inicio, coche_wh, 'estimado_sesion'::text
    FROM v_energia_sesion_estimada
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
JOIN mv_precio_final p ON p.inicio = t.tramo
GROUP BY s.conector, s.id, s.inicio, s.fin, s.energia_wh, p.contrato, p.fuente, t.origen;

-- Los privilegios por defecto de 0006 ya lo cubren; explícito por claridad.
GRANT SELECT ON mv_precio_final TO grafana_ro;
