-- Batería: trayecto abierto partido por una lectura, tolerancia de lectura y nivel con el coche fuera (v0.8.1).
--
-- En el NAS, la lectura del 100 % del 19/11 a las 10:53 cayó 2 minutos después de salir (10:51), dentro del
-- trayecto aún abierto. La parte de un trayecto abierto anterior a un corte pesaba (tiempo de la parte) /
-- (tiempo hasta el último corte) = 1, así que los 21,47 kWh estimados se aplicaron enteros a esos 2 minutos:
-- la estimación dio 75 % en lugar de 99 % y el intervalo 18/11 10:26 – 19/11 10:53 salió «incoherente»
-- (consumo −0,98 kWh). Además, bateria_estado no descontaba el trayecto en curso si la lectura era posterior a
-- la salida (Inicio y el planificador partían del 100 %) y daba ESTIMADO con el coche fuera.
--
-- 1. tolerancia_lectura_pts (2, grupo 2): un consumo entre −2 pts y 0 cuenta como 0 en la reconciliación (dos
--    lecturas sin decimales) y no es incoherente; bateria_intervalo guarda el valor calculado.
-- 2. recalcular_bateria (cuerpo de la 0017): las partes de un trayecto abierto pesan 0; al cerrarse se reparten
--    por tiempo como siempre. Tolerancia del punto 1.
-- 3. bateria_estado (cuerpo de la 0016): consumo en curso = estimado del trayecto abierto − lo ya aplicado a
--    sus partes (0, o el consumo real si una lectura las ha reconciliado); con el coche fuera, confianza LEÍDO (o LEÍDO + CARGA) si la última lectura es
--    posterior a la salida en curso.
-- 4. v_bateria_evento_reciente (cuerpo de la 0019): sin «conexión» en la llegada de un trayecto corto (el
--    18/11 a las 14:21 el cargador dio estado 1 dos minutos, sin desconexión real).
--
-- Comprobado con los datos del NAS: los reinicios del muestreo con el coche conectado no dejan huecos ni
-- cambios de estado (el despliegue del 18/11 a las 14:01 solo desplazó el ritmo de muestreo).

-- --- 1. Parámetro ---------------------------------------------------------------------------

INSERT INTO bateria_parametro_clave (clave, unidad, nombre, descripcion, efecto, minimo, maximo, grupo, orden, nota)
VALUES ('tolerancia_lectura_pts', 'pts', 'Tolerancia de lectura',
        'Un consumo calculado negativo de hasta estos puntos entre dos valores absolutos se toma como 0: es el redondeo de dos lecturas sin decimales.',
        'Subir: menos intervalos incoherentes por lecturas redondeadas. Bajar: avisa de diferencias más pequeñas.',
        0, 5, 2, 6, 'dos lecturas sin decimales');

INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente) VALUES
    ('tolerancia_lectura_pts', '[2025-01-01,)', 2, 'usuario', 'Revisión del 19/11/2026');

-- CHECK de la 0023 con la clave nueva (0 admitido).
ALTER TABLE bateria_parametro DROP CONSTRAINT bateria_parametro_valor_valido;
ALTER TABLE bateria_parametro ADD CONSTRAINT bateria_parametro_valor_valido CHECK (CASE
    WHEN clave IN ('detectar_carga_completa', 'carga_oportunista') THEN valor IN (0, 1)
    WHEN clave = 'percentil_planificacion' THEN valor > 0 AND valor < 1
    WHEN clave = 'factor_bateria'          THEN valor > 0 AND valor <= 1
    WHEN clave = 'reserva_pct'             THEN valor >= 0 AND valor < 100
    WHEN clave IN ('tope_pct', 'umbral_oportunista_pct', 'nivel_oportunista_pct', 'umbral_disponibilidad_pct')
                                           THEN valor > 0 AND valor <= 100
    WHEN clave IN ('ventana_consumo_dias', 'observaciones_calibracion', 'aviso_sin_lectura_dias',
                   'ventana_disponibilidad_dias', 'historial_min_disponibilidad_dias', 'horizonte_provisional_h',
                   'franja_min_min', 'max_tareas_sistema', 'id_tarea_guardiana', 'aviso_planificador_min',
                   'ventana_precio_esperado_dias', 'ventana_umbral_dias', 'ventana_potencia_dias',
                   'horizonte_plan_h')
                                           THEN valor >= 1 AND valor = trunc(valor)
    WHEN clave IN ('margen_salida_min', 'congelar_franja_min', 'aviso_precios_min')
                                           THEN valor >= 0 AND valor = trunc(valor)
    WHEN clave IN ('margen_carga_pct', 'tolerancia_lectura_pts') THEN valor >= 0
    WHEN clave = 'desconexion_min_s'       THEN valor >= 0
    WHEN clave = 'hueco_muestreo_aviso_s'  THEN valor >= 180
    WHEN clave = 'hora_publicacion_precios' THEN valor >= 0 AND valor < 1440 AND valor = trunc(valor)
    ELSE valor > 0
END);

-- --- 2. Recálculo ----------------------------------------------------------------------------

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
    v_aplicado      numeric;
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
        SELECT tr.conector, tr.salida, tr.llegada, b.desde, b.hasta,
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
               -- Abierto (0024): sus partes pesan 0; todo el consumo queda en curso hasta la llegada.
               CASE WHEN llegada IS NULL THEN 0
                    ELSE extract(epoch FROM hasta - desde) / extract(epoch FROM fin_partes - salida) END,
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
                -- Tolerancia de lectura (0024): hasta tolerancia_lectura_pts por debajo de 0 es redondeo de
                -- dos lecturas sin decimales y cuenta como 0; el intervalo guarda v_real.
                v_aplicado := CASE WHEN v_real < 0 AND v_real >= - public.bateria_param('tolerancia_lectura_pts',
                                            r.instante) / 100 * v_cap THEN 0 ELSE v_real END;
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
                    IF v_aplicado > v_cap OR (v_c = 0 AND v_aplicado < 0) THEN
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
                ELSIF v_aplicado < 0 OR v_aplicado > v_cap THEN
                    v_estado := 'incoherente';
                ELSE
                    v_estado := 'reconciliado';
                    UPDATE public.bateria_nivel b
                    SET delta_kwh = - CASE WHEN v_est = 0 THEN v_aplicado / v_partes
                                           ELSE v_aplicado * (- b.delta_estimado_kwh) / v_est END
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

-- --- 3. Estado ------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION bateria_estado(p_ahora timestamptz)
RETURNS TABLE (
    ahora                       timestamptz,
    capacidad_kwh               numeric,
    nivel_kwh                   numeric,
    nivel_pct                   numeric,
    energia_pendiente_kwh       numeric,
    ultimo_absoluto             timestamptz,
    ultimo_absoluto_tipo        text,
    trayectos_desde             integer,
    coche                       text,
    estado_conector             smallint,
    fuera_desde                 timestamptz,
    consumo_en_curso_kwh        numeric,
    nivel_plan_kwh              numeric,
    dudoso                      boolean,
    factor_vigente              numeric,
    factor_sugerido             numeric,
    observaciones_factor        integer,
    proxima_salida              timestamptz,
    tipo_salida                 text,
    consumo_plan_kwh            numeric,
    origen_consumo_plan         text,
    reserva_kwh                 numeric,
    tope_kwh                    numeric,
    objetivo_kwh                numeric,
    objetivo_pct                numeric,
    energia_necesaria_kwh       numeric,
    confianza                   text,
    ultima_lectura_ts           timestamptz,
    ultima_lectura_pct          numeric,
    kwh_cargados_desde_lectura  numeric,
    trayectos_desde_lectura     integer,
    kwh_trayectos_desde_lectura numeric
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_cap       numeric := public.bateria_param('capacidad_util_kwh', p_ahora);
    v_factor    numeric := public.bateria_param('factor_bateria', p_ahora);
    v_base      record;
    v_abs       record;
    v_limite    timestamptz;
    v_wh        numeric;
    v_est       record;
    v_tray      record;
    v_sug       record;
    v_sal       record;
    v_plan      record;
    v_ul        record;
    v_n_lec     integer;
    v_salida_curso timestamptz;
BEGIN
    ahora := p_ahora;
    capacidad_kwh := v_cap;
    factor_vigente := v_factor;

    SELECT b.instante, b.nivel_kwh INTO v_base FROM public.bateria_nivel b
    WHERE b.instante <= p_ahora ORDER BY b.n DESC LIMIT 1;
    SELECT b.n, b.instante, b.tipo INTO v_abs FROM public.bateria_nivel b
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
        v_salida_curso := v_tray.salida;
        IF v_tray.salida IS NOT NULL THEN
            -- Lo que falta del trayecto en curso (0024): su estimado menos lo ya aplicado a sus partes (0
            -- estimado; el consumo real si una lectura las ha reconciliado).
            consumo_en_curso_kwh := greatest(coalesce(v_tray.consumo_estimado_kwh, 0) - coalesce(
                (SELECT sum(- b.delta_kwh) FROM public.bateria_nivel b
                 WHERE b.tipo = 'trayecto' AND b.conector = v_tray.conector AND b.trayecto_salida = v_tray.salida
                   AND b.instante <= p_ahora), 0), 0);
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

    -- Próxima salida, objetivo y energía necesaria.
    SELECT * INTO v_sal FROM public.bateria_salidas(p_ahora, 7) s ORDER BY s.salida LIMIT 1;
    SELECT * INTO v_plan FROM public.bateria_consumo_plan(p_ahora);
    proxima_salida := v_sal.salida;
    tipo_salida := v_sal.tipo;
    consumo_plan_kwh := coalesce(v_sal.consumo_previsto_kwh, v_plan.consumo_kwh);
    origen_consumo_plan := CASE WHEN v_sal.consumo_modo = 'indicado' THEN 'declarado' ELSE v_plan.origen END;
    reserva_kwh := public.bateria_param('reserva_pct', p_ahora) / 100 * v_cap;
    tope_kwh := public.bateria_param('tope_pct', p_ahora) / 100 * v_cap;
    IF v_sal.salida IS NOT NULL THEN
        objetivo_kwh := v_sal.objetivo_kwh;
        objetivo_pct := v_sal.objetivo_kwh / public.bateria_param('capacidad_util_kwh', v_sal.salida) * 100;
        IF nivel_plan_kwh IS NOT NULL THEN
            energia_necesaria_kwh := greatest(v_sal.objetivo_kwh - nivel_plan_kwh, 0);
        END IF;
    END IF;

    -- Última lectura y confianza (0016).
    SELECT l.id, l.instante, l.pct INTO v_ul FROM public.bateria_lectura l
    WHERE l.anulada_en IS NULL AND l.instante <= p_ahora ORDER BY l.instante DESC LIMIT 1;
    ultima_lectura_ts := v_ul.instante;
    ultima_lectura_pct := v_ul.pct;
    IF v_ul.id IS NOT NULL THEN
        SELECT b.n INTO v_n_lec FROM public.bateria_nivel b WHERE b.tipo = 'lectura' AND b.lectura_id = v_ul.id;
        SELECT coalesce(sum(b.delta_kwh) FILTER (WHERE b.tipo IN ('sesion', 'carga_externa')), 0)
                   + energia_pendiente_kwh,
               coalesce(sum(- b.delta_kwh) FILTER (WHERE b.tipo = 'trayecto'), 0)
        INTO kwh_cargados_desde_lectura, kwh_trayectos_desde_lectura
        FROM public.bateria_nivel b
        WHERE b.n > v_n_lec AND b.instante <= p_ahora;
        SELECT count(*)::integer INTO trayectos_desde_lectura FROM public.bateria_trayecto tr
        WHERE NOT tr.corto AND tr.llegada IS NOT NULL AND tr.llegada > v_ul.instante AND tr.llegada <= p_ahora;
    END IF;
    confianza := 'estimado';
    -- Con el coche fuera, LEÍDO si la lectura es posterior a la salida en curso: el nivel a la salida (0024).
    IF v_abs.tipo = 'lectura' AND (coche <> 'fuera' OR v_abs.instante > v_salida_curso) AND NOT EXISTS (
        SELECT 1 FROM public.bateria_nivel b
        JOIN public.bateria_trayecto tr ON tr.conector = b.conector AND tr.salida = b.trayecto_salida
        WHERE b.tipo = 'trayecto' AND b.n > v_abs.n AND b.instante <= p_ahora AND NOT tr.corto)
    THEN
        IF energia_pendiente_kwh > 0 OR EXISTS (
            SELECT 1 FROM public.bateria_nivel b
            WHERE b.tipo IN ('sesion', 'carga_externa') AND b.n > v_abs.n AND b.instante <= p_ahora
              AND b.delta_kwh > 0)
        THEN
            confianza := 'leido_carga';
        ELSE
            confianza := 'leido';
        END IF;
    END IF;
    RETURN NEXT;
END
$$;

-- --- 4. Eventos recientes ----------------------------------------------------------------------

CREATE OR REPLACE VIEW v_bateria_evento_reciente AS
SELECT l.instante, 'lectura'::text AS tipo, l.pct AS valor, '%'::text AS unidad, false AS estimado,
       false AS en_curso, NULL::numeric AS real_kwh, NULL::numeric AS medido_kwh
FROM bateria_lectura l
WHERE l.anulada_en IS NULL
UNION ALL
SELECT s.fin, 'carga', s.energia_wh / 1000 * s.factor, 'kWh', false, false, NULL, s.energia_wh / 1000
FROM bateria_sesion s
UNION ALL
SELECT x.instante, 'carga_externa', x.kwh * bateria_param('factor_bateria', x.instante), 'kWh', false, false, NULL,
       x.kwh
FROM bateria_carga_externa x
WHERE x.anulada_en IS NULL
UNION ALL
SELECT t.llegada, 'trayecto', - t.consumo_kwh, 'kWh', t.origen_consumo IS DISTINCT FROM 'reconciliado', false,
       (SELECT sum(b.delta_kwh)
        FROM bateria_nivel b
        CROSS JOIN LATERAL (SELECT a.instante FROM bateria_nivel a
                            WHERE a.tipo IN ('lectura', 'carga_completa') AND a.n > b.n
                            ORDER BY a.n LIMIT 1) sig
        JOIN bateria_intervalo i ON i.hasta = sig.instante AND i.estado = 'reconciliado'
        WHERE b.tipo = 'trayecto' AND b.conector = t.conector AND b.trayecto_salida = t.salida),
       NULL
FROM bateria_trayecto t
WHERE t.llegada IS NOT NULL AND NOT t.corto
UNION ALL
SELECT t.llegada, 'conexion', NULL, NULL, false, false, NULL, NULL
FROM bateria_trayecto t
WHERE t.llegada IS NOT NULL AND NOT t.corto  -- 0024: un trayecto corto no es una conexión
UNION ALL
SELECT min(e.detectado_en), 'inicio_carga', NULL, NULL, false, true, NULL, NULL
FROM cargador_estado e
WHERE e.estado IN (5, 6)
  AND e.detectado_en > coalesce((SELECT max(s.fin) FROM cargador_sesion s), '-infinity')
  AND (SELECT u.estado FROM cargador_estado u ORDER BY u.detectado_en DESC LIMIT 1) IN (5, 6)
HAVING min(e.detectado_en) IS NOT NULL;


-- Rehace el nivel, los trayectos y los intervalos con las reglas nuevas.
SELECT recalcular_bateria();
