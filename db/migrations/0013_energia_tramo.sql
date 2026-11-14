-- Energía por tramo guardada en tabla y casa desconocida durante la carga (v0.4.1).
--
-- 1. Casa: el 14/11/2026 se verificó que durante una carga homePower = 0 y
--    totalPower queda por debajo de evsePower (homePower = max(0, total −
--    evse)). En los tramos con alguna muestra de carga (evse_w > 0) la
--    energía de la casa es desconocida: NULL, no 0. coche_wh y total_wh no
--    cambian.
-- 2. energia_tramo: la energía medida por tramo, que hasta ahora cada panel
--    recalculaba desde todas las muestras. La mantiene el muestreo: en la
--    misma transacción que guarda una muestra, recalcula desde
--    cargador_muestra los tramos que esa muestra toca
--    (recalcular_energia_tramo). Tras insertar o borrar muestras por SQL:
--    `cargador recalcular`. `cargador estado` compara los dos últimos días
--    con el cálculo desde las muestras.
-- 3. v_sesion_coste busca las muestras de cada sesión por la PK de
--    cargador_muestra, sesión a sesión.

-- --- 1. Casa desconocida durante la carga -------------------------------------------

-- Mismas columnas que en 0011; casa_wh es NULL en las muestras con carga.
CREATE OR REPLACE VIEW v_energia_muestra AS
SELECT
    m.instante,
    t.tramo                                AS inicio,
    t.tramo + interval '15 minutes'        AS fin,
    t.segundos,
    m.evse_w,
    m.home_w,
    m.total_w,
    m.evse_w  * t.segundos / 3600          AS coche_wh,
    CASE WHEN m.evse_w > 0 THEN NULL
         ELSE m.home_w * t.segundos / 3600
    END                                    AS casa_wh,
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

COMMENT ON COLUMN cargador_muestra.home_w IS
    'homePower = max(0, totalPower − evsePower): 0 durante la carga (verificado el 14/11/2026); sin verificar fuera de ella.';
COMMENT ON COLUMN v_energia_muestra.casa_wh IS
    'NULL (desconocida) si la muestra tiene carga (evse_w > 0): homePower es 0 durante la carga.';

-- --- 2. Energía medida por tramo ----------------------------------------------------

-- Cálculo de referencia: tramos de 15 min con inicio en [p_desde, p_hasta)
-- desde las muestras. Una muestra cubre [instante − intervalo_s, instante)
-- con intervalo_s <= 180 (CHECK de 0010): las de esos tramos tienen instante
-- en (p_desde, p_hasta + 180 s), y el filtro va a la PK de cargador_muestra.
-- p_desde y p_hasta deben ser inicios de tramo.
CREATE FUNCTION energia_tramo_calculada(p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (
    inicio      timestamptz,
    fin         timestamptz,
    coche_wh    numeric,
    casa_wh     numeric,
    total_wh    numeric,
    cubierto_s  numeric,
    incompleto  boolean
)
LANGUAGE sql STABLE AS $$
    SELECT
        em.inicio,
        em.fin,
        -- W·s sumados y una sola división por tramo, como en 0011.
        sum(em.evse_w * em.segundos) / 3600,
        CASE WHEN bool_or(em.evse_w > 0) THEN NULL
             ELSE sum(em.home_w * em.segundos) / 3600
        END,
        sum(em.total_w * em.segundos) / 3600,
        sum(em.segundos),
        sum(em.segundos) < 840
    FROM public.v_energia_muestra em
    WHERE em.instante > p_desde
      AND em.instante < p_hasta + interval '180 seconds'
      AND em.inicio >= p_desde
      AND em.inicio < p_hasta
    GROUP BY em.inicio, em.fin
$$;

COMMENT ON FUNCTION energia_tramo_calculada(timestamptz, timestamptz) IS
    'Energía medida de los tramos con inicio en [p_desde, p_hasta), calculada desde cargador_muestra.';

CREATE TABLE energia_tramo (
    inicio      timestamptz PRIMARY KEY,
    fin         timestamptz NOT NULL,
    coche_wh    numeric NOT NULL,
    casa_wh     numeric,
    total_wh    numeric NOT NULL,
    cubierto_s  numeric NOT NULL,
    incompleto  boolean NOT NULL,
    CONSTRAINT energia_tramo_cuarto_de_hora CHECK (fin = inicio + interval '15 minutes')
);

COMMENT ON TABLE energia_tramo IS
    'Energía medida (Wh) por tramo de 15 min; la mantiene el muestreo con recalcular_energia_tramo.';
COMMENT ON COLUMN energia_tramo.casa_wh IS
    'NULL (desconocida) si alguna muestra del tramo tiene carga (evse_w > 0): durante la carga'
    ' homePower = 0 (verificado el 14/11/2026). No calcularla como total_wh − coche_wh: el medidor'
    ' del cargador y la pinza no cuadran entre sí y saldría negativa. Sin verificar fuera de la carga.';

INSERT INTO energia_tramo
SELECT * FROM energia_tramo_calculada('-infinity', 'infinity');

-- Recalcula los tramos que se solapan con [p_desde, p_hasta): borra los
-- guardados y escribe el cálculo desde las muestras. Idempotente; un
-- advisory lock evita que dos recálculos del mismo tramo choquen.
CREATE FUNCTION recalcular_energia_tramo(p_desde timestamptz, p_hasta timestamptz) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    d timestamptz := date_bin('15 minutes', p_desde, TIMESTAMPTZ '2000-01-01 00:00:00+00');
    h timestamptz := date_bin('15 minutes', p_hasta - interval '1 microsecond',
                              TIMESTAMPTZ '2000-01-01 00:00:00+00') + interval '15 minutes';
    n integer;
BEGIN
    IF p_hasta <= p_desde THEN
        RETURN 0;
    END IF;
    PERFORM pg_advisory_xact_lock(7265302);
    DELETE FROM public.energia_tramo WHERE inicio >= d AND inicio < h;
    INSERT INTO public.energia_tramo
    SELECT * FROM public.energia_tramo_calculada(d, h);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;

COMMENT ON FUNCTION recalcular_energia_tramo(timestamptz, timestamptz) IS
    'Recalcula desde cargador_muestra los tramos que se solapan con [p_desde, p_hasta); devuelve cuántos quedan.';

-- Solo la escribe el servicio: grafana_ro no necesita ejecutarla.
REVOKE EXECUTE ON FUNCTION recalcular_energia_tramo(timestamptz, timestamptz) FROM PUBLIC, grafana_ro;

-- Mismas columnas que en 0011, ahora desde la tabla.
CREATE OR REPLACE VIEW v_energia_tramo_medida AS
SELECT inicio, fin, coche_wh, casa_wh, total_wh, cubierto_s, incompleto
FROM energia_tramo;

COMMENT ON COLUMN v_energia_tramo.casa_wh IS
    'NULL si es desconocida: tramo con carga (homePower = 0 durante la carga, verificado el 14/11/2026)'
    ' o estimado por sesión. No calcularla como total_wh − coche_wh: los dos sensores no cuadran.';

-- --- 3. Coste por sesión --------------------------------------------------------------

-- Mismas columnas y resultado que en 0012. Las muestras de cada sesión se
-- buscan por la PK de cargador_muestra (OFFSET 0 impide que el planificador
-- aplane el LATERAL en un join con todas las muestras).
CREATE OR REPLACE VIEW v_sesion_coste AS
WITH tramo_sesion AS MATERIALIZED (
    SELECT s.conector, s.id, em.inicio AS tramo, sum(em.coche_wh) AS coche_wh, 'muestreo'::text AS origen
    FROM cargador_sesion s
    CROSS JOIN LATERAL (
        SELECT v.inicio, v.coche_wh
        FROM v_energia_muestra v
        WHERE v.instante >= s.inicio AND v.instante < s.fin
        OFFSET 0
    ) em
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
GRANT SELECT ON energia_tramo TO grafana_ro;
