-- Excepciones con salida o sin salida (etapa 2, paso 2b; v0.5.1).
--
-- Cambio de la especificación de E2.7 (15/11/2026). bateria_excepcion pasa
-- de cuatro tipos (sin_coche, otra_hora, nivel_objetivo, viaje_largo) a dos:
--
-- - con_salida: hora_salida y consumo_modo obligatorios; pct_objetivo
--   opcional, combinable con cualquier modo.
--   · normal: consumo previsto = P75 vigente (se calcula, no se guarda).
--   · indicado: exactamente uno de km o pct (de capacidad_util_kwh); km se
--     convierte con consumo_kwh_100km. Los trayectos de ese día se tratan
--     como viaje largo (bateria_trayecto.viaje_largo): consumo declarado y
--     fuera de la mediana y del P75 (E2.4, E2.7).
-- - sin_salida: solo la fecha. Que el horario tenga salida ese día lo
--   comprueba la capa de servicio (bateria_servicio.registrar_excepcion),
--   no la base de datos.
--
-- Objetivo de salida = min(tope, max(reserva + consumo previsto, pct_objetivo)).
-- Hasta ahora nivel_objetivo solo miraba el porcentaje.
--
-- Conversión de las filas existentes (también las anuladas):
--   sin_coche      → sin_salida
--   otra_hora      → con_salida, normal
--   nivel_objetivo → con_salida, normal, con su pct_objetivo
--   viaje_largo    → con_salida, indicado, con su km o pct
--
-- Columnas renombradas: hora → hora_salida, consumo_km → km, consumo_pct → pct.
-- recalcular_bateria, bateria_estimar_trayecto y bateria_estado se sustituyen
-- con el mismo cuerpo que en 0014 salvo las líneas de excepciones;
-- bateria_salidas y v_bateria_salida se recrean (añaden consumo_modo).

-- --- Tabla -----------------------------------------------------------------------------------

ALTER TABLE bateria_excepcion DROP CONSTRAINT bateria_excepcion_campos_del_tipo;
ALTER TABLE bateria_excepcion DROP CONSTRAINT bateria_excepcion_tipo_check;
ALTER TABLE bateria_excepcion RENAME COLUMN hora TO hora_salida;
ALTER TABLE bateria_excepcion RENAME COLUMN consumo_km TO km;
ALTER TABLE bateria_excepcion RENAME COLUMN consumo_pct TO pct;
ALTER TABLE bateria_excepcion ADD COLUMN consumo_modo text;

UPDATE bateria_excepcion SET
    consumo_modo = CASE tipo WHEN 'otra_hora' THEN 'normal'
                             WHEN 'nivel_objetivo' THEN 'normal'
                             WHEN 'viaje_largo' THEN 'indicado' END,
    tipo = CASE tipo WHEN 'sin_coche' THEN 'sin_salida' ELSE 'con_salida' END;

ALTER TABLE bateria_excepcion
    ADD CONSTRAINT bateria_excepcion_tipo_check CHECK (tipo IN ('con_salida', 'sin_salida')),
    ADD CONSTRAINT bateria_excepcion_consumo_modo_check CHECK (consumo_modo IN ('normal', 'indicado')),
    ADD CONSTRAINT bateria_excepcion_campos_del_tipo CHECK (CASE tipo
        WHEN 'sin_salida' THEN hora_salida IS NULL AND consumo_modo IS NULL
                               AND km IS NULL AND pct IS NULL AND pct_objetivo IS NULL
        WHEN 'con_salida' THEN hora_salida IS NOT NULL AND consumo_modo IS NOT NULL
                               AND CASE consumo_modo
                                   WHEN 'normal' THEN km IS NULL AND pct IS NULL
                                   ELSE (km IS NULL) <> (pct IS NULL) END
    END);

COMMENT ON TABLE bateria_excepcion IS
    'Excepciones con fecha local; una activa por fecha. con_salida: hora_salida, consumo_modo '
    '(normal: P75; indicado: km o pct de capacidad_util_kwh) y pct_objetivo opcional. sin_salida: solo la fecha.';
COMMENT ON COLUMN bateria_excepcion.pct IS 'Consumo indicado en % de capacidad_util_kwh (no confundir con pct_objetivo).';
COMMENT ON COLUMN bateria_excepcion.pct_objetivo IS 'Nivel mínimo de salida en %; opcional en con_salida.';

-- --- Cálculo -----------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION recalcular_bateria(p_ahora timestamptz DEFAULT now()) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    r               record;
    v_t             record;
    v_nivel         numeric;
    v_cap           numeric;
    v_a1_n          integer;
    v_a1_t          timestamptz;
    v_a1_kwh        numeric;
    v_s             numeric;
    v_c             numeric;
    v_est           numeric;
    v_real          numeric;
    v_partes        integer;
    v_cortos        integer;
    v_estado        text;
    v_huecos        boolean;
    v_factor_obs    numeric;
    v_filas         integer;
    v_dia_cap       date;
BEGIN
    PERFORM pg_advisory_xact_lock(7265303);

    DELETE FROM public.bateria_nivel;
    DELETE FROM public.bateria_intervalo;
    DELETE FROM public.bateria_trayecto;
    DELETE FROM public.bateria_sesion;

    -- Sesiones (E2.2, E2.3).
    INSERT INTO public.bateria_sesion (conector, id, inicio, fin, energia_wh, factor, cierre)
    SELECT s.conector, s.id, s.inicio, s.fin, s.energia_wh,
           public.bateria_param('factor_bateria', s.fin),
           CASE
               WHEN s.origen_fin = 'schedman' THEN 'fin_franja'
               WHEN public.bateria_param('detectar_carga_completa', s.fin) = 0 THEN 'sin_regla'
               WHEN s.origen_fin IS DISTINCT FROM 'evsm' THEN 'otro'
               WHEN p_ahora < s.fin + interval '180 seconds' THEN 'pendiente'
               WHEN m.estado IN (4, 8) THEN 'carga_completa'
               WHEN m.estado IN (1, 2) THEN 'desconexion'
               ELSE 'dudoso'
           END
    FROM public.cargador_sesion s
    LEFT JOIN LATERAL (
        SELECT cm.estado
        FROM public.cargador_muestra cm
        WHERE cm.instante > s.fin AND cm.instante <= s.fin + interval '180 seconds'
        ORDER BY cm.instante
        LIMIT 1
    ) m ON true;

    -- Trayectos (E2.4): cambios de clase (c conectado, l libre) del conector.
    INSERT INTO public.bateria_trayecto (conector, salida, llegada, corto, viaje_largo)
    WITH clase AS (
        SELECT e.conector, e.detectado_en,
               CASE WHEN e.estado BETWEEN 3 AND 8 THEN 'c' WHEN e.estado IN (1, 2) THEN 'l' END AS c
        FROM public.cargador_estado e
        WHERE e.detectado_en <= p_ahora
    ), cambio AS (
        SELECT conector, detectado_en, c, lag(c) OVER (PARTITION BY conector ORDER BY detectado_en) AS antes
        FROM clase
        WHERE c IS NOT NULL
    ), transicion AS (
        SELECT conector, detectado_en, c FROM cambio WHERE antes IS NOT NULL AND antes <> c
    ), siguiente AS (
        -- Las transiciones alternan: la siguiente a una salida es la llegada.
        SELECT conector, detectado_en, c, lead(detectado_en) OVER (PARTITION BY conector ORDER BY detectado_en) AS sig
        FROM transicion
    ), viaje AS (
        SELECT conector, detectado_en AS salida, sig AS llegada FROM siguiente WHERE c = 'l'
    ), marcado AS (
        SELECT v.*,
               v.llegada IS NOT NULL
               AND extract(epoch FROM v.llegada - v.salida) < public.bateria_param('desconexion_min_s', v.salida)
                   AS corto
        FROM viaje v
    )
    SELECT m.conector, m.salida, m.llegada, m.corto,
           NOT m.corto AND EXISTS (
               SELECT 1 FROM public.bateria_excepcion x
               WHERE x.anulada_en IS NULL AND x.tipo = 'con_salida' AND x.consumo_modo = 'indicado'
                 AND x.fecha = (m.salida AT TIME ZONE 'Europe/Madrid')::date)
    FROM marcado m;

    UPDATE public.bateria_trayecto SET consumo_estimado_kwh = 0, origen_consumo = 'cero' WHERE corto;

    -- Eventos y sus partes. Cortes: valores absolutos (instantes distintos).
    CREATE TEMP TABLE IF NOT EXISTS bateria_corte (t timestamptz PRIMARY KEY) ON COMMIT DROP;
    TRUNCATE bateria_corte;
    INSERT INTO bateria_corte
    SELECT instante FROM public.bateria_lectura WHERE anulada_en IS NULL AND instante <= p_ahora
    UNION
    SELECT fin FROM public.bateria_sesion WHERE cierre = 'carga_completa';

    INSERT INTO public.bateria_nivel (n, instante, orden, ref, tipo, conector, sesion_id, trayecto_salida,
                                      lectura_id, peso, contador_kwh, absoluto_kwh, delta_estimado_kwh)
    WITH sesion_parte AS (
        -- Límites: inicio, cortes dentro de (inicio, fin), fin.
        SELECT s.*, b.desde, b.hasta
        FROM public.bateria_sesion s
        CROSS JOIN LATERAL (
            SELECT lag(x.t) OVER (ORDER BY x.t) AS desde, x.t AS hasta
            FROM (SELECT s.inicio AS t
                  UNION ALL SELECT c.t FROM bateria_corte c WHERE c.t > s.inicio AND c.t < s.fin
                  UNION ALL SELECT s.fin) x
        ) b
        WHERE b.desde IS NOT NULL
    ), sesion_ws AS (
        -- Energía del modulador (W·s) de las muestras de cada parte.
        SELECT p.*,
               (SELECT coalesce(sum(m.evse_w * m.intervalo_s), 0) FROM public.cargador_muestra m
                WHERE m.instante > p.desde AND m.instante <= p.hasta) AS ws
        FROM sesion_parte p
    ), sesion_peso AS (
        SELECT w.*, sum(w.ws) OVER (PARTITION BY w.conector, w.id) AS ws_total
        FROM sesion_ws w
    ), sesion_evento AS (
        SELECT hasta AS instante, 1 AS orden, 'sesion:' || conector || ':' || id AS ref, 'sesion' AS tipo,
               conector, id AS sesion_id, NULL::timestamptz AS trayecto_salida, NULL::bigint AS lectura_id,
               w AS peso, energia_wh / 1000 * w AS contador_kwh, NULL::numeric AS absoluto_kwh,
               energia_wh / 1000 * factor * w AS delta_estimado_kwh
        FROM (SELECT sp.*,
                     CASE WHEN sp.ws_total > 0 THEN sp.ws / sp.ws_total
                          WHEN sp.fin > sp.inicio
                              THEN extract(epoch FROM sp.hasta - sp.desde) / extract(epoch FROM sp.fin - sp.inicio)
                          ELSE 1
                     END AS w
              FROM sesion_peso sp) q
    ), trayecto_parte AS (
        -- Trayecto cerrado: salida, cortes, llegada. Abierto: salida y cortes
        -- (la parte posterior al último corte se aplica al llegar).
        SELECT tr.conector, tr.salida, b.desde, b.hasta,
               max(b.hasta) OVER (PARTITION BY tr.conector, tr.salida) AS fin_partes
        FROM public.bateria_trayecto tr
        CROSS JOIN LATERAL (
            SELECT lag(x.t) OVER (ORDER BY x.t) AS desde, x.t AS hasta
            FROM (SELECT tr.salida AS t
                  UNION ALL SELECT c.t FROM bateria_corte c
                            WHERE c.t > tr.salida AND (tr.llegada IS NULL OR c.t < tr.llegada)
                  UNION ALL SELECT tr.llegada WHERE tr.llegada IS NOT NULL) x
        ) b
        WHERE b.desde IS NOT NULL
    ), trayecto_evento AS (
        SELECT hasta, 3, 'trayecto:' || conector || ':' || extract(epoch FROM salida)::bigint, 'trayecto',
               conector, NULL::bigint, salida, NULL::bigint,
               extract(epoch FROM hasta - desde) / extract(epoch FROM fin_partes - salida),
               NULL::numeric, NULL::numeric, NULL::numeric
        FROM trayecto_parte
    ), absoluto AS (
        SELECT fin, 2, 'carga_completa:' || conector || ':' || id, 'carga_completa', conector, id,
               NULL::timestamptz, NULL::bigint, NULL::numeric, NULL::numeric,
               public.bateria_param('tope_pct', fin) / 100 * public.bateria_param('capacidad_util_kwh', fin),
               NULL::numeric
        FROM public.bateria_sesion WHERE cierre = 'carga_completa'
        UNION ALL
        SELECT instante, 4, 'lectura:' || id, 'lectura', NULL, NULL, NULL, id, NULL, NULL,
               pct / 100 * public.bateria_param('capacidad_util_kwh', instante), NULL
        FROM public.bateria_lectura WHERE anulada_en IS NULL AND instante <= p_ahora
    ), todo AS (
        SELECT * FROM sesion_evento
        UNION ALL SELECT * FROM trayecto_evento
        UNION ALL SELECT * FROM absoluto
    )
    SELECT row_number() OVER (ORDER BY instante, orden, ref), * FROM todo;

    -- Primera pasada: consumos estimados, nivel previsto y reconciliación.
    v_nivel := NULL;
    v_a1_n := NULL;
    FOR r IN SELECT * FROM public.bateria_nivel ORDER BY n LOOP
        IF v_dia_cap IS DISTINCT FROM (r.instante AT TIME ZONE 'Europe/Madrid')::date THEN
            v_dia_cap := (r.instante AT TIME ZONE 'Europe/Madrid')::date;
            v_cap := public.bateria_param('capacidad_util_kwh', r.instante);
        END IF;
        IF r.tipo = 'sesion' THEN
            IF v_nivel IS NOT NULL THEN
                v_nivel := least(greatest(v_nivel + r.delta_estimado_kwh, 0), v_cap);
            END IF;
            IF v_a1_n IS NOT NULL THEN
                v_s := v_s + r.delta_estimado_kwh;
                v_c := v_c + r.contador_kwh;
            END IF;
            UPDATE public.bateria_nivel SET nivel_estimado_kwh = v_nivel WHERE n = r.n;

        ELSIF r.tipo = 'trayecto' THEN
            SELECT tr.consumo_estimado_kwh, tr.conector, tr.salida INTO v_t FROM public.bateria_trayecto tr
            WHERE tr.conector = r.conector AND tr.salida = r.trayecto_salida;
            v_est := coalesce(v_t.consumo_estimado_kwh,
                              public.bateria_estimar_trayecto(v_t.conector, v_t.salida, p_ahora));
            IF v_nivel IS NOT NULL THEN
                v_nivel := least(greatest(v_nivel - v_est * r.peso, 0), v_cap);
            END IF;
            UPDATE public.bateria_nivel SET delta_estimado_kwh = - v_est * r.peso, nivel_estimado_kwh = v_nivel
            WHERE n = r.n;

        ELSE
            UPDATE public.bateria_nivel
            SET estimado_previo_kwh = v_nivel, nivel_estimado_kwh = r.absoluto_kwh
            WHERE n = r.n;
            IF v_a1_n IS NOT NULL THEN
                -- Reconciliación del intervalo (v_a1_n, r.n) (E2.5).
                SELECT coalesce(sum(- b.delta_estimado_kwh) FILTER (WHERE NOT tr.corto), 0),
                       count(*) FILTER (WHERE NOT tr.corto),
                       count(*) FILTER (WHERE tr.corto)
                INTO v_est, v_partes, v_cortos
                FROM public.bateria_nivel b
                JOIN public.bateria_trayecto tr ON tr.conector = b.conector AND tr.salida = b.trayecto_salida
                WHERE b.tipo = 'trayecto' AND b.n > v_a1_n AND b.n < r.n;

                v_real := v_a1_kwh + v_s - r.absoluto_kwh;
                v_huecos := public.bateria_hueco(v_a1_t, r.instante,
                                                 public.bateria_param('hueco_muestreo_aviso_s', r.instante));
                v_factor_obs := NULL;
                IF v_partes = 0 THEN
                    -- Sin trayectos que repartir. Con carga, la diferencia es error del
                    -- factor (E2.6), no incoherencia; sin carga, el nivel no puede subir.
                    IF v_real > v_cap OR (v_c = 0 AND v_real < 0) THEN
                        v_estado := 'incoherente';
                    ELSE
                        v_estado := 'sin_trayectos';
                        IF v_cortos = 0 AND NOT v_huecos AND v_c > 0
                           AND v_c >= public.bateria_param('energia_min_calibracion_kwh', r.instante) THEN
                            v_factor_obs := (r.absoluto_kwh - v_a1_kwh) / v_c;
                            IF v_factor_obs <= 0 OR v_factor_obs > 1 THEN
                                v_factor_obs := NULL;
                            END IF;
                        END IF;
                    END IF;
                ELSIF v_real < 0 OR v_real > v_cap THEN
                    v_estado := 'incoherente';
                ELSE
                    v_estado := 'reconciliado';
                    UPDATE public.bateria_nivel b
                    SET delta_kwh = - CASE WHEN v_est = 0 THEN v_real / v_partes
                                           ELSE v_real * (- b.delta_estimado_kwh) / v_est END
                    FROM public.bateria_trayecto tr
                    WHERE tr.conector = b.conector AND tr.salida = b.trayecto_salida AND NOT tr.corto
                      AND b.tipo = 'trayecto' AND b.n > v_a1_n AND b.n < r.n;
                    -- Trayectos cerrados con todas sus partes reconciliadas.
                    UPDATE public.bateria_trayecto tr
                    SET origen_consumo = 'reconciliado', reconciliado_en = r.instante,
                        consumo_kwh = (SELECT sum(- b.delta_kwh) FROM public.bateria_nivel b
                                       WHERE b.conector = tr.conector AND b.trayecto_salida = tr.salida)
                    FROM (SELECT DISTINCT b.conector, b.trayecto_salida FROM public.bateria_nivel b
                          WHERE b.tipo = 'trayecto' AND b.n > v_a1_n AND b.n < r.n) x
                    WHERE tr.conector = x.conector AND tr.salida = x.trayecto_salida
                      AND tr.llegada IS NOT NULL AND NOT tr.corto
                      AND NOT EXISTS (SELECT 1 FROM public.bateria_nivel b
                                      WHERE b.conector = tr.conector AND b.trayecto_salida = tr.salida
                                        AND b.delta_kwh IS NULL);
                END IF;
                INSERT INTO public.bateria_intervalo (desde, hasta, a1_kwh, a2_kwh, sesiones_kwh, contador_kwh,
                    trayectos, trayectos_cortos, consumo_estimado_kwh, consumo_real_kwh, estado, huecos,
                    factor_observado)
                VALUES (v_a1_t, r.instante, v_a1_kwh, r.absoluto_kwh, v_s, v_c, v_partes, v_cortos, v_est,
                        v_real, v_estado, v_huecos, v_factor_obs);
            END IF;
            v_nivel := r.absoluto_kwh;
            v_a1_n := r.n;
            v_a1_t := r.instante;
            v_a1_kwh := r.absoluto_kwh;
            v_s := 0;
            v_c := 0;
        END IF;
    END LOOP;

    -- Trayectos sin partes (fuera, sin lecturas desde la salida): su estimación.
    FOR v_t IN SELECT tr.conector, tr.salida FROM public.bateria_trayecto tr
               WHERE tr.consumo_estimado_kwh IS NULL ORDER BY tr.salida LOOP
        PERFORM public.bateria_estimar_trayecto(v_t.conector, v_t.salida, p_ahora);
    END LOOP;

    -- Segunda pasada: nivel con los consumos aplicados.
    v_nivel := NULL;
    v_dia_cap := NULL;
    FOR r IN SELECT * FROM public.bateria_nivel ORDER BY n LOOP
        IF v_dia_cap IS DISTINCT FROM (r.instante AT TIME ZONE 'Europe/Madrid')::date THEN
            v_dia_cap := (r.instante AT TIME ZONE 'Europe/Madrid')::date;
            v_cap := public.bateria_param('capacidad_util_kwh', r.instante);
        END IF;
        IF r.tipo IN ('lectura', 'carga_completa') THEN
            v_nivel := r.absoluto_kwh;
        ELSE
            r.delta_kwh := coalesce(r.delta_kwh, r.delta_estimado_kwh);
            IF v_nivel IS NOT NULL THEN
                v_nivel := least(greatest(v_nivel + r.delta_kwh, 0), v_cap);
            END IF;
        END IF;
        UPDATE public.bateria_nivel
        SET delta_kwh = r.delta_kwh, nivel_kwh = v_nivel, nivel_pct = v_nivel / v_cap * 100
        WHERE n = r.n;
    END LOOP;

    -- Consumo aplicado de los trayectos cerrados sin reconciliar.
    UPDATE public.bateria_trayecto tr
    SET consumo_kwh = (SELECT sum(- b.delta_kwh) FROM public.bateria_nivel b
                       WHERE b.conector = tr.conector AND b.trayecto_salida = tr.salida)
    WHERE tr.llegada IS NOT NULL AND tr.origen_consumo IS DISTINCT FROM 'reconciliado';

    SELECT count(*) INTO v_filas FROM public.bateria_nivel;
    RETURN v_filas;
END
$$;

CREATE OR REPLACE FUNCTION bateria_estimar_trayecto(p_conector text, p_salida timestamptz, p_ahora timestamptz)
RETURNS numeric
LANGUAGE plpgsql AS $$
DECLARE
    t           record;
    x           record;
    v_est       numeric;
    v_origen    text;
    v_decl      numeric;
    v_dia       date;
    v_dur       numeric;
    v_total     numeric;
    v_n         integer;
BEGIN
    SELECT * INTO t FROM public.bateria_trayecto WHERE conector = p_conector AND salida = p_salida;
    IF t.corto THEN
        v_est := 0;
        v_origen := 'cero';
    ELSIF t.viaje_largo THEN
        v_dia := (t.salida AT TIME ZONE 'Europe/Madrid')::date;
        SELECT * INTO x FROM public.bateria_excepcion
        WHERE anulada_en IS NULL AND tipo = 'con_salida' AND consumo_modo = 'indicado' AND fecha = v_dia;
        v_decl := coalesce(x.pct / 100 * public.bateria_param('capacidad_util_kwh', t.salida),
                           x.km * public.bateria_param('consumo_kwh_100km', t.salida) / 100);
        SELECT sum(extract(epoch FROM coalesce(o.llegada, p_ahora) - o.salida)), count(*)
        INTO v_total, v_n
        FROM public.bateria_trayecto o
        WHERE o.conector = t.conector AND o.viaje_largo
          AND (o.salida AT TIME ZONE 'Europe/Madrid')::date = v_dia;
        v_dur := extract(epoch FROM coalesce(t.llegada, p_ahora) - t.salida);
        v_est := CASE WHEN v_total > 0 THEN v_decl * v_dur / v_total ELSE v_decl / v_n END;
        v_origen := 'declarado';
    ELSE
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY o.consumo_kwh)::numeric
        INTO v_est
        FROM public.bateria_trayecto o
        WHERE o.conector = t.conector
          AND o.origen_consumo = 'reconciliado' AND NOT o.corto AND NOT o.viaje_largo
          AND o.reconciliado_en <= t.salida
          AND o.salida >= t.salida - public.bateria_param('ventana_consumo_dias', t.salida) * interval '1 day'
          AND o.salida < t.salida;
        IF v_est IS NULL THEN
            v_est := public.bateria_param('consumo_inicial_trayecto_kwh', t.salida);
            v_origen := 'inicial';
        ELSE
            v_origen := 'estimado';
        END IF;
    END IF;
    UPDATE public.bateria_trayecto
    SET consumo_estimado_kwh = v_est, origen_consumo = v_origen
    WHERE conector = p_conector AND salida = p_salida;
    RETURN v_est;
END
$$;

-- --- Salidas y estado (E2.7) -------------------------------------------------------------------

DROP VIEW v_bateria_salida;
DROP FUNCTION bateria_salidas(timestamptz, integer);

-- Salidas desde p_desde durante p_dias días locales más: horario semanal y
-- excepciones. tipo: horario o con_salida; consumo_declarado_kwh solo con
-- consumo indicado.
CREATE FUNCTION bateria_salidas(p_desde timestamptz, p_dias integer)
RETURNS TABLE (fecha date, salida timestamptz, tipo text, consumo_modo text, pct_objetivo numeric,
               consumo_declarado_kwh numeric)
LANGUAGE sql STABLE AS $$
    SELECT q.fecha, q.salida, q.tipo, q.consumo_modo, q.pct_objetivo,
           CASE WHEN q.consumo_modo = 'indicado' THEN
               coalesce(q.pct / 100 * public.bateria_param('capacidad_util_kwh', q.salida),
                        q.km * public.bateria_param('consumo_kwh_100km', q.salida) / 100)
           END
    FROM (
        SELECT d.fecha,
               (d.fecha + coalesce(x.hora_salida, h.hora_salida)) AT TIME ZONE 'Europe/Madrid' AS salida,
               coalesce(x.tipo, 'horario') AS tipo,
               coalesce(x.consumo_modo, 'normal') AS consumo_modo,
               x.pct_objetivo, x.pct, x.km
        FROM (SELECT g::date AS fecha
              FROM generate_series((p_desde AT TIME ZONE 'Europe/Madrid')::date,
                                   (p_desde AT TIME ZONE 'Europe/Madrid')::date + p_dias,
                                   interval '1 day') g) d
        LEFT JOIN public.bateria_excepcion x ON x.fecha = d.fecha AND x.anulada_en IS NULL
        LEFT JOIN public.bateria_horario h ON h.dia_iso = extract(isodow FROM d.fecha)
        WHERE x.tipo IS DISTINCT FROM 'sin_salida'
          AND coalesce(x.hora_salida, h.hora_salida) IS NOT NULL
    ) q
    WHERE q.salida > p_desde
    ORDER BY q.salida
$$;

CREATE OR REPLACE FUNCTION bateria_estado(p_ahora timestamptz)
RETURNS TABLE (
    ahora                   timestamptz,
    capacidad_kwh           numeric,
    nivel_kwh               numeric,
    nivel_pct               numeric,
    energia_pendiente_kwh   numeric,
    ultimo_absoluto         timestamptz,
    ultimo_absoluto_tipo    text,
    trayectos_desde         integer,
    coche                   text,
    estado_conector         smallint,
    fuera_desde             timestamptz,
    consumo_en_curso_kwh    numeric,
    nivel_plan_kwh          numeric,
    dudoso                  boolean,
    factor_vigente          numeric,
    factor_sugerido         numeric,
    observaciones_factor    integer,
    proxima_salida          timestamptz,
    tipo_salida             text,
    consumo_plan_kwh        numeric,
    origen_consumo_plan     text,
    reserva_kwh             numeric,
    tope_kwh                numeric,
    energia_necesaria_kwh   numeric,
    avisos                  text[]
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_cap       numeric := public.bateria_param('capacidad_util_kwh', p_ahora);
    v_factor    numeric := public.bateria_param('factor_bateria', p_ahora);
    v_base      record;
    v_abs       record;
    v_lect      timestamptz;
    v_limite    timestamptz;
    v_wh        numeric;
    v_est       record;
    v_tray      record;
    v_sug       record;
    v_sal       record;
    v_plan      record;
    v_avisos    text[] := '{}';
    v_objetivo  numeric;
    x           record;
BEGIN
    ahora := p_ahora;
    capacidad_kwh := v_cap;
    factor_vigente := v_factor;

    SELECT b.instante, b.nivel_kwh INTO v_base FROM public.bateria_nivel b
    WHERE b.instante <= p_ahora ORDER BY b.n DESC LIMIT 1;
    SELECT b.instante, b.tipo INTO v_abs FROM public.bateria_nivel b
    WHERE b.instante <= p_ahora AND b.tipo IN ('lectura', 'carga_completa') ORDER BY b.n DESC LIMIT 1;
    ultimo_absoluto := v_abs.instante;
    ultimo_absoluto_tipo := v_abs.tipo;

    -- Energía entregada que aún no está en ningún evento: muestras posteriores
    -- a la última sesión registrada y al último valor absoluto (E2.1).
    v_limite := greatest(v_abs.instante, (SELECT max(s.fin) FROM public.cargador_sesion s WHERE s.fin <= p_ahora));
    SELECT coalesce(sum(m.evse_w * m.intervalo_s), 0) / 3600 INTO v_wh
    FROM public.cargador_muestra m
    WHERE m.intervalo_s IS NOT NULL AND m.instante <= p_ahora
      AND (v_limite IS NULL OR m.instante > v_limite);
    energia_pendiente_kwh := v_wh / 1000 * v_factor;

    IF v_base.nivel_kwh IS NOT NULL THEN
        nivel_kwh := least(greatest(v_base.nivel_kwh + energia_pendiente_kwh, 0), v_cap);
        nivel_pct := nivel_kwh / v_cap * 100;
    END IF;

    SELECT count(*)::integer INTO trayectos_desde FROM public.bateria_trayecto tr
    WHERE NOT tr.corto AND tr.llegada IS NOT NULL AND tr.llegada <= p_ahora
      AND (v_abs.instante IS NULL OR tr.llegada > v_abs.instante);

    SELECT e.estado, e.detectado_en INTO v_est FROM public.cargador_estado e
    WHERE e.detectado_en <= p_ahora ORDER BY e.detectado_en DESC LIMIT 1;
    estado_conector := v_est.estado;
    coche := CASE WHEN v_est.estado IS NULL THEN 'desconocido'
                  WHEN v_est.estado IN (1, 2) THEN 'fuera'
                  WHEN v_est.estado IN (5, 6) THEN 'cargando'
                  WHEN v_est.estado BETWEEN 3 AND 8 THEN 'en_casa'
                  ELSE 'desconocido' END;
    consumo_en_curso_kwh := 0;
    IF coche = 'fuera' THEN
        SELECT tr.* INTO v_tray FROM public.bateria_trayecto tr
        WHERE tr.llegada IS NULL ORDER BY tr.salida DESC LIMIT 1;
        fuera_desde := coalesce(v_tray.salida, v_est.detectado_en);
        IF v_tray.salida IS NOT NULL AND (v_abs.instante IS NULL OR v_abs.instante <= v_tray.salida) THEN
            consumo_en_curso_kwh := v_tray.consumo_estimado_kwh;
        END IF;
    END IF;
    IF nivel_kwh IS NOT NULL THEN
        nivel_plan_kwh := greatest(nivel_kwh - consumo_en_curso_kwh, 0);
    END IF;

    IF v_abs.instante IS NOT NULL THEN
        dudoso := public.bateria_hueco(v_abs.instante, p_ahora,
                                       public.bateria_param('hueco_muestreo_aviso_s', p_ahora));
    END IF;

    SELECT * INTO v_sug FROM public.bateria_factor_sugerido(p_ahora);
    factor_sugerido := v_sug.factor;
    observaciones_factor := v_sug.observaciones;

    -- Próxima salida y energía necesaria.
    SELECT * INTO v_sal FROM public.bateria_salidas(p_ahora, 7) s ORDER BY s.salida LIMIT 1;
    SELECT * INTO v_plan FROM public.bateria_consumo_plan(p_ahora);
    proxima_salida := v_sal.salida;
    tipo_salida := v_sal.tipo;
    consumo_plan_kwh := CASE WHEN v_sal.consumo_modo = 'indicado' THEN v_sal.consumo_declarado_kwh
                             ELSE v_plan.consumo_kwh END;
    origen_consumo_plan := CASE WHEN v_sal.consumo_modo = 'indicado' THEN 'declarado' ELSE v_plan.origen END;
    reserva_kwh := public.bateria_param('reserva_pct', p_ahora) / 100 * v_cap;
    tope_kwh := public.bateria_param('tope_pct', p_ahora) / 100 * v_cap;
    IF v_sal.salida IS NOT NULL AND nivel_plan_kwh IS NOT NULL THEN
        -- Objetivo de salida = min(tope, max(reserva + consumo previsto, pct_objetivo)).
        v_objetivo := greatest(reserva_kwh + consumo_plan_kwh, v_sal.pct_objetivo / 100 * v_cap);
        energia_necesaria_kwh := least(greatest(v_objetivo - nivel_plan_kwh, 0),
                                       greatest(tope_kwh - nivel_plan_kwh, 0));
    END IF;

    -- Avisos (E2.7).
    SELECT max(l.instante) INTO v_lect FROM public.bateria_lectura l
    WHERE l.anulada_en IS NULL AND l.instante <= p_ahora;
    IF v_lect IS NULL THEN
        v_avisos := v_avisos || 'sin lecturas: registra una para tener nivel'::text;
    ELSIF p_ahora - v_lect > public.bateria_param('aviso_sin_lectura_dias', p_ahora) * interval '1 day' THEN
        v_avisos := v_avisos || format('sin lecturas desde el %s',
                                       to_char(v_lect AT TIME ZONE 'Europe/Madrid', 'DD/MM/YYYY HH24:MI'));
    END IF;
    FOR x IN SELECT i.desde, i.hasta, i.consumo_real_kwh FROM public.bateria_intervalo i
             WHERE i.estado = 'incoherente' AND i.hasta <= p_ahora
               AND i.hasta > p_ahora - public.bateria_param('ventana_consumo_dias', p_ahora) * interval '1 day'
             ORDER BY i.desde LOOP
        v_avisos := v_avisos || format('intervalo incoherente del %s al %s (consumo calculado %s kWh)',
            to_char(x.desde AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'),
            to_char(x.hasta AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'), round(x.consumo_real_kwh, 1));
    END LOOP;
    IF factor_sugerido IS NOT NULL AND abs(factor_sugerido - v_factor) >= 0.01 THEN
        v_avisos := v_avisos || format('factor sugerido %s (vigente %s, %s observación(es))',
                                       round(factor_sugerido, 3), v_factor, observaciones_factor);
    END IF;
    IF dudoso THEN
        v_avisos := v_avisos || 'hueco de muestreo desde el último valor absoluto: nivel dudoso'::text;
    END IF;
    FOR x IN SELECT s.fin FROM public.bateria_sesion s
             WHERE s.cierre = 'dudoso' AND (v_abs.instante IS NULL OR s.fin > v_abs.instante)
             ORDER BY s.fin LOOP
        v_avisos := v_avisos || format('sesión terminada el %s sin muestra posterior: carga completa dudosa',
                                       to_char(x.fin AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'));
    END LOOP;
    avisos := v_avisos;
    RETURN NEXT;
END
$$;

CREATE VIEW v_bateria_salida AS
SELECT s.fecha, s.salida, s.tipo, s.consumo_modo, s.pct_objetivo, s.consumo_declarado_kwh,
       CASE WHEN s.salida = e.proxima_salida THEN e.energia_necesaria_kwh END AS energia_necesaria_kwh
FROM bateria_salidas(now(), 7) s
CROSS JOIN bateria_estado(now()) e;

GRANT SELECT ON bateria_excepcion, v_bateria_salida TO grafana_ro;

SELECT recalcular_bateria();
