-- Sesiones anteriores al muestreo (v0.5.3).
--
-- En el NAS, las 42 sesiones del histórico del cargador terminadas por el
-- coche (evsm) entre el 21/09 y el 12/11/2026 salían como `dudoso`, con su
-- aviso «Carga completa dudosa», porque no había muestras: el muestreo empezó
-- el 13/11. Cierre nuevo `sin_muestreo`: sesión evsm sin muestra en los 180 s
-- siguientes que termina antes de la primera muestra del cargador (o sin
-- ninguna muestra). No es un valor absoluto, como `dudoso`: el nivel no
-- cambia, solo la etiqueta y los avisos. Un hueco de muestreo posterior sigue
-- dando `dudoso` y su aviso.
--
-- recalcular_bateria se sustituye con el mismo cuerpo que en 0016 salvo la
-- clasificación de las sesiones. Después del recálculo se borran de
-- aviso_historial los avisos «Carga completa dudosa» de las sesiones que pasan
-- a `sin_muestreo` (falsas alarmas).

ALTER TABLE bateria_sesion DROP CONSTRAINT bateria_sesion_cierre_check;
ALTER TABLE bateria_sesion ADD CONSTRAINT bateria_sesion_cierre_check
    CHECK (cierre IN ('carga_completa', 'fin_franja', 'desconexion', 'dudoso', 'pendiente', 'sin_regla', 'otro',
                      'sin_muestreo'));

COMMENT ON COLUMN bateria_sesion.cierre IS
    'carga_completa: origen evsm y conector en 4 u 8 en la primera muestra de los 180 s siguientes;'
    ' fin_franja: origen schedman; desconexion: libre en esa muestra; dudoso: sin esa muestra o con otro'
    ' estado; sin_muestreo: sin esa muestra y terminada antes de la primera muestra del cargador (0017);'
    ' pendiente: aún no han pasado 180 s; sin_regla: detectar_carga_completa = 0; otro: otro origen.';

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
    v_x             numeric;
    v_nx            integer;
    v_sin_kwh       integer;
    v_inicio        timestamptz := clock_timestamp();
    v_primera       timestamptz := (SELECT min(instante) FROM public.cargador_muestra);
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
               -- Terminada antes de la primera muestra: no había muestreo (0017).
               WHEN m.instante IS NULL AND (v_primera IS NULL OR s.fin < v_primera) THEN 'sin_muestreo'
               ELSE 'dudoso'
           END
    FROM public.cargador_sesion s
    LEFT JOIN LATERAL (
        SELECT cm.instante, cm.estado
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
    -- Cortes de trayecto también en cada carga externa (0016); las sesiones solo
    -- se parten en los valores absolutos.
    CREATE TEMP TABLE IF NOT EXISTS bateria_corte_0016 (t timestamptz PRIMARY KEY, absoluto boolean NOT NULL)
        ON COMMIT DROP;
    TRUNCATE bateria_corte_0016;
    INSERT INTO bateria_corte_0016
    SELECT t, bool_or(absoluto) FROM (
        SELECT instante AS t, true AS absoluto FROM public.bateria_lectura
        WHERE anulada_en IS NULL AND instante <= p_ahora
        UNION ALL
        SELECT fin, true FROM public.bateria_sesion WHERE cierre = 'carga_completa'
        UNION ALL
        SELECT instante, false FROM public.bateria_carga_externa WHERE anulada_en IS NULL AND instante <= p_ahora
    ) c
    GROUP BY t;

    INSERT INTO public.bateria_nivel (n, instante, orden, ref, tipo, conector, sesion_id, trayecto_salida,
                                      lectura_id, peso, contador_kwh, absoluto_kwh, delta_estimado_kwh,
                                      carga_externa_id)
    WITH sesion_parte AS (
        -- Límites: inicio, cortes dentro de (inicio, fin), fin.
        SELECT s.*, b.desde, b.hasta
        FROM public.bateria_sesion s
        CROSS JOIN LATERAL (
            SELECT lag(x.t) OVER (ORDER BY x.t) AS desde, x.t AS hasta
            FROM (SELECT s.inicio AS t
                  UNION ALL SELECT c.t FROM bateria_corte_0016 c WHERE c.absoluto AND c.t > s.inicio AND c.t < s.fin
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
               energia_wh / 1000 * factor * w AS delta_estimado_kwh, NULL::bigint AS carga_externa_id
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
                  UNION ALL SELECT c.t FROM bateria_corte_0016 c
                            WHERE c.t > tr.salida AND (tr.llegada IS NULL OR c.t < tr.llegada)
                  UNION ALL SELECT tr.llegada WHERE tr.llegada IS NOT NULL) x
        ) b
        WHERE b.desde IS NOT NULL
    ), trayecto_evento AS (
        SELECT hasta, 3, 'trayecto:' || conector || ':' || extract(epoch FROM salida)::bigint, 'trayecto',
               conector, NULL::bigint, salida, NULL::bigint,
               extract(epoch FROM hasta - desde) / extract(epoch FROM fin_partes - salida),
               NULL::numeric, NULL::numeric, NULL::numeric, NULL::bigint
        FROM trayecto_parte
    ), absoluto AS (
        SELECT fin, 2, 'carga_completa:' || conector || ':' || id, 'carga_completa', conector, id,
               NULL::timestamptz, NULL::bigint, NULL::numeric, NULL::numeric,
               public.bateria_param('tope_pct', fin) / 100 * public.bateria_param('capacidad_util_kwh', fin),
               NULL::numeric, NULL::bigint
        FROM public.bateria_sesion WHERE cierre = 'carga_completa'
        UNION ALL
        SELECT instante, 5, 'lectura:' || id, 'lectura', NULL, NULL, NULL, id, NULL, NULL,
               pct / 100 * public.bateria_param('capacidad_util_kwh', instante), NULL, NULL
        FROM public.bateria_lectura WHERE anulada_en IS NULL AND instante <= p_ahora
        UNION ALL
        -- Cargas externas (0016): kWh × factor_bateria; sin kWh, delta NULL.
        SELECT instante, 4, 'carga_externa:' || id, 'carga_externa', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               kwh * public.bateria_param('factor_bateria', instante), id
        FROM public.bateria_carga_externa WHERE anulada_en IS NULL AND instante <= p_ahora
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

        ELSIF r.tipo = 'carga_externa' THEN
            IF r.delta_estimado_kwh IS NOT NULL AND v_nivel IS NOT NULL THEN
                v_nivel := least(greatest(v_nivel + r.delta_estimado_kwh, 0), v_cap);
            END IF;
            IF v_a1_n IS NOT NULL THEN
                v_nx := v_nx + 1;
                IF r.delta_estimado_kwh IS NULL THEN
                    v_sin_kwh := v_sin_kwh + 1;
                ELSE
                    v_x := v_x + r.delta_estimado_kwh;
                END IF;
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

                v_real := v_a1_kwh + v_s + v_x - r.absoluto_kwh;
                v_huecos := public.bateria_hueco(v_a1_t, r.instante,
                                                 public.bateria_param('hueco_muestreo_aviso_s', r.instante));
                v_factor_obs := NULL;
                IF v_sin_kwh > 0 THEN
                    -- Carga externa sin kWh (0016): ni reparto ni incoherencia; sus trayectos
                    -- quedan sin reconciliar (fuera de la mediana y del P75).
                    v_estado := 'carga_externa_sin_kwh';
                ELSIF v_partes = 0 THEN
                    -- Sin trayectos que repartir. Con carga, la diferencia es error del
                    -- factor (E2.6), no incoherencia; sin carga, el nivel no puede subir.
                    IF v_real > v_cap OR (v_c = 0 AND v_real < 0) THEN
                        v_estado := 'incoherente';
                    ELSE
                        v_estado := 'sin_trayectos';
                        IF v_cortos = 0 AND NOT v_huecos AND v_nx = 0 AND v_c > 0
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
                    factor_observado, cargas_externas, externa_kwh)
                VALUES (v_a1_t, r.instante, v_a1_kwh, r.absoluto_kwh, v_s, v_c, v_partes, v_cortos, v_est,
                        v_real, v_estado, v_huecos, v_factor_obs, v_nx, v_x);
            END IF;
            v_nivel := r.absoluto_kwh;
            v_a1_n := r.n;
            v_a1_t := r.instante;
            v_a1_kwh := r.absoluto_kwh;
            v_s := 0;
            v_c := 0;
            v_x := 0;
            v_nx := 0;
            v_sin_kwh := 0;
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
            -- Solo una carga externa sin kWh queda sin delta: no cambia el nivel.
            IF v_nivel IS NOT NULL AND r.delta_kwh IS NOT NULL THEN
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
    INSERT INTO public.bateria_recalculo (id, calculado_en, ahora, duracion_ms, eventos)
    VALUES (true, now(), p_ahora, extract(epoch FROM clock_timestamp() - v_inicio) * 1000, v_filas)
    ON CONFLICT (id) DO UPDATE SET calculado_en = EXCLUDED.calculado_en, ahora = EXCLUDED.ahora,
                                   duracion_ms = EXCLUDED.duracion_ms, eventos = EXCLUDED.eventos;
    RETURN v_filas;
END
$$;

SELECT recalcular_bateria();

DELETE FROM aviso_historial h
USING bateria_sesion s
WHERE s.cierre = 'sin_muestreo'
  AND h.clave = 'carga_completa_dudosa:' || s.conector || ':' || s.id;
