-- Modelo para el formulario (etapa 2, paso 3a; v0.5.2).
--
-- Cambios al modelo de docs/formulario-maquetas.md, sección 8, con las
-- decisiones del 15/11/2026:
--
-- 1. Parámetros: bateria_parametro_clave gana nombre, efecto, minimo y
--    maximo (descripcion pasa a ser el «qué es» de la Ayuda). Un trigger
--    rechaza en bateria_parametro los valores fuera de [minimo, maximo];
--    el CHECK por clave de 0014 se mantiene. Clave nueva
--    umbral_carga_externa_kwh (5, usuario).
-- 2. Cargas externas: tabla bateria_carga_externa (kWh y coste opcionales;
--    se anulan y corrigen como las lecturas). Evento carga_externa en
--    bateria_nivel (orden 4; la lectura pasa a 5) que parte los trayectos:
--    con kWh suma kwh × factor_bateria y entra en la reconciliación como una
--    sesión más; sin kWh no suma y el intervalo queda en
--    carga_externa_sin_kwh (ni reparto ni incoherencia: sus trayectos se
--    quedan sin reconciliar, fuera de la mediana y del P75). Un intervalo
--    con cargas externas no sirve para calibrar (E2.6).
-- 3. bateria_estado: confianza (leido, leido_carga, estimado), datos de la
--    última lectura y objetivo; los avisos salen a bateria_avisos().
--    bateria_salidas: consumo previsto y objetivo de cada salida.
-- 4. Avisos: bateria_avisos(p_ahora) y v_aviso (catálogo de 7.7) y
--    aviso_historial, que mantiene actualizar_avisos() (el muestreo cada
--    minuto y el formulario tras cada cambio).
-- 5. bateria_recalculo: hora y duración del último recálculo.
-- 6. Vistas para las pantallas y rol formulario_rw.

-- --- 1. Parámetros -------------------------------------------------------------------------

ALTER TABLE bateria_parametro_clave
    ADD COLUMN nombre text,
    ADD COLUMN efecto text,
    ADD COLUMN minimo numeric,
    ADD COLUMN maximo numeric;

INSERT INTO bateria_parametro_clave (clave, unidad, descripcion) VALUES
    ('umbral_carga_externa_kwh', 'kWh', '');

UPDATE bateria_parametro_clave k SET nombre = v.nombre, descripcion = v.descripcion, efecto = v.efecto,
                                     minimo = v.minimo, maximo = v.maximo
FROM (VALUES
    ('capacidad_util_kwh', 'Capacidad útil', 50, 120,
     'Energía utilizable de la batería. Convierte entre % y kWh: 1 pt = capacidad / 100.',
     'Solo cambia si cambias de coche o la batería se degrada de forma medible.'),
    ('reserva_pct', 'Reserva mínima', 10, 60,
     'Nivel por debajo del cual no quieres llegar nunca. Se suma al consumo previsto para calcular el objetivo de salida.',
     'Subir: más margen y más energía cargada. Bajar: menos coste, menos colchón ante imprevistos.'),
    ('tope_pct', 'Tope de carga', 50, 100,
     'Máximo al que se permite cargar. Si un objetivo lo supera, se recorta al tope y se avisa.',
     'No implica que el coche termine al tope: el objetivo real es reserva + consumo previsto.'),
    ('factor_bateria', 'Eficiencia de carga', 0.70, 1.00,
     'Fracción de la energía medida por el cargador que llega a la batería (pérdidas de carga). Valor inicial estimado.',
     'Se calibra con lecturas antes y después de cargar, sin trayectos entre ellas. No afecta al coste: se paga lo que mide el cargador.'),
    ('percentil_planificacion', 'Percentil de planificación', 0.50, 0.95,
     'Consumo diario usado para planificar: con 0,75, el 75 % de los días gastaste esto o menos.',
     'Subir (0,90): más prudente, carga más. Bajar (0,50): carga lo de un día típico; algún día irás justo.'),
    ('ventana_consumo_dias', 'Ventana de consumo', 7, 90,
     'Días hacia atrás de trayectos con consumo real que se usan para la mediana y el P75.',
     'Más días: más estable, tarda en adaptarse a cambios (invierno, verano). Menos días: se adapta antes, más sensible a días raros.'),
    ('desconexion_min_s', 'Desconexión sin consumo', 0, 14400,
     'Las desconexiones más cortas no cuentan como trayecto (consumo cero). Se guarda en segundos.',
     'Evita que mover el coche o desenchufar un momento reste batería.'),
    ('consumo_inicial_trayecto_kwh', 'Consumo inicial por trayecto', 1, 90,
     'Consumo que se supone para cada trayecto mientras no haya trayectos con consumo real para calcular la mediana.',
     'Deja de usarse en cuanto hay trayectos reconciliados con lecturas.'),
    ('consumo_kwh_100km', 'Consumo medio', 10, 40,
     'Convierte en kWh los km de una excepción con consumo indicado.',
     'Solo afecta a excepciones con consumo indicado en km. Ajústalo con el consumo que marca el coche.'),
    ('energia_min_calibracion_kwh', 'Energía mínima para calibrar', 1, 30,
     'Energía mínima cargada entre dos lecturas para usar ese intervalo como observación de la eficiencia de carga.',
     'Más alto: observaciones más fiables pero menos frecuentes.'),
    ('observaciones_calibracion', 'Observaciones para calibrar', 1, 20,
     'Número de observaciones más recientes de la eficiencia de carga cuya mediana es el valor sugerido.',
     'Más: sugerencia más estable, tarda en reflejar cambios. Menos: reacciona antes, más sensible a una lectura mala.'),
    ('detectar_carga_completa', 'Detectar carga completa', 0, 1,
     'Fija el nivel en el tope cuando se detecta que el coche dejó de cargar por estar lleno.',
     'Regla aún sin verificar con el coche: desactívala si produce falsos 100 %.'),
    ('hueco_muestreo_aviso_s', 'Aviso hueco de muestreo', 300, 14400,
     'Si faltan muestras del cargador durante más tiempo, se avisa: puede haberse perdido energía medida. Se guarda en segundos.',
     'Útil para detectar caídas de red o del contenedor.'),
    ('aviso_sin_lectura_dias', 'Aviso sin lecturas', 1, 14,
     'Si pasan más días sin una lectura y el nivel está ESTIMADO, se muestra el aviso. Se guarda en días enteros.',
     'Bajar: te avisa antes para afinar la estimación. Subir: menos avisos, más deriva del modelo.'),
    ('umbral_carga_externa_kwh', 'Umbral de carga externa', 1, 30,
     'Si una lectura muestra más energía de la esperada por encima de este valor, se pregunta si hubo una carga fuera de casa.',
     'Más bajo: detecta cargas pequeñas pero puede preguntar por errores de estimación. Más alto: solo cargas claras.')
) AS v (clave, nombre, minimo, maximo, descripcion, efecto)
WHERE k.clave = v.clave;

ALTER TABLE bateria_parametro_clave
    ALTER COLUMN nombre SET NOT NULL,
    ALTER COLUMN efecto SET NOT NULL,
    ALTER COLUMN minimo SET NOT NULL,
    ALTER COLUMN maximo SET NOT NULL,
    ADD CONSTRAINT bateria_parametro_clave_rango CHECK (minimo <= maximo);

COMMENT ON COLUMN bateria_parametro_clave.descripcion IS 'Qué es (página de Ayuda y de Parámetros).';
COMMENT ON COLUMN bateria_parametro_clave.efecto IS 'Efecto al cambiarlo (página de Ayuda).';
COMMENT ON COLUMN bateria_parametro_clave.minimo IS 'Rango admitido, en la unidad guardada; lo aplica un trigger.';

INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente) VALUES
    ('umbral_carga_externa_kwh', '[2025-01-01,)', 5, 'usuario', 'Maquetas del formulario, sección 8');

CREATE FUNCTION bateria_parametro_en_rango() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k   record;
BEGIN
    SELECT minimo, maximo INTO k FROM public.bateria_parametro_clave WHERE clave = NEW.clave;
    IF NEW.valor < k.minimo OR NEW.valor > k.maximo THEN
        RAISE EXCEPTION '% fuera de rango: % (admitido de % a %)', NEW.clave, NEW.valor, k.minimo, k.maximo
            USING ERRCODE = 'check_violation', CONSTRAINT = 'bateria_parametro_en_rango';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER bateria_parametro_en_rango BEFORE INSERT OR UPDATE OF clave, valor ON bateria_parametro
    FOR EACH ROW EXECUTE FUNCTION bateria_parametro_en_rango();

-- --- 2. Cargas externas ----------------------------------------------------------------------

CREATE TABLE bateria_carga_externa (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instante        timestamptz NOT NULL,
    kwh             numeric CHECK (kwh >= 0 AND kwh <= 150),
    coste_eur       numeric CHECK (coste_eur >= 0),
    nota            text,
    origen          text        NOT NULL CHECK (origen IN ('formulario', 'cli')),
    alta_en         timestamptz NOT NULL DEFAULT now(),
    anulada_en      timestamptz,
    motivo          text,
    corrige         bigint REFERENCES bateria_carga_externa (id),
    CONSTRAINT bateria_carga_externa_anulacion_con_motivo
        CHECK ((anulada_en IS NULL) = (motivo IS NULL) AND (motivo IS NULL OR btrim(motivo) <> ''))
);

CREATE UNIQUE INDEX bateria_carga_externa_instante_activa ON bateria_carga_externa (instante)
    WHERE anulada_en IS NULL;

COMMENT ON TABLE bateria_carga_externa IS
    'Cargas fuera de casa. kwh y coste_eur NULL si no se saben. Se anulan (anulada_en y motivo), nunca se borran.';

ALTER TABLE bateria_nivel DROP CONSTRAINT bateria_nivel_tipo_check;
ALTER TABLE bateria_nivel
    ADD CONSTRAINT bateria_nivel_tipo_check
        CHECK (tipo IN ('sesion', 'carga_completa', 'trayecto', 'carga_externa', 'lectura')),
    ADD COLUMN carga_externa_id bigint;
COMMENT ON COLUMN bateria_nivel.orden IS '1 sesión, 2 carga completa, 3 trayecto, 4 carga externa, 5 lectura (0016)';

ALTER TABLE bateria_intervalo DROP CONSTRAINT bateria_intervalo_estado_check;
ALTER TABLE bateria_intervalo
    ADD CONSTRAINT bateria_intervalo_estado_check
        CHECK (estado IN ('reconciliado', 'incoherente', 'sin_trayectos', 'carga_externa_sin_kwh')),
    ADD COLUMN cargas_externas integer NOT NULL DEFAULT 0,
    ADD COLUMN externa_kwh numeric NOT NULL DEFAULT 0;
COMMENT ON COLUMN bateria_intervalo.consumo_real_kwh IS 'a1 + sesiones + cargas externas − a2';
COMMENT ON COLUMN bateria_intervalo.externa_kwh IS 'Cargas externas con kWh, × factor_bateria';

-- --- 5. Último recálculo ---------------------------------------------------------------------

CREATE TABLE bateria_recalculo (
    id              boolean PRIMARY KEY DEFAULT true CHECK (id),
    calculado_en    timestamptz NOT NULL,
    ahora           timestamptz NOT NULL,
    duracion_ms     numeric     NOT NULL,
    eventos         integer     NOT NULL
);

COMMENT ON TABLE bateria_recalculo IS 'Una fila: hora, instante de cálculo y duración del último recalcular_bateria().';

-- --- Recálculo con cargas externas ------------------------------------------------------------

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

-- --- 3. Salidas y estado ---------------------------------------------------------------------

DROP VIEW v_bateria_salida;
DROP VIEW v_bateria_estado;
DROP FUNCTION bateria_estado(timestamptz);
DROP FUNCTION bateria_salidas(timestamptz, integer);

-- Salidas desde p_desde durante p_dias días locales más: horario semanal y
-- excepciones. Consumo previsto: el declarado (consumo indicado) o el P75 de
-- bateria_consumo_plan(p_desde). Objetivo = min(tope, max(reserva + consumo
-- previsto, pct_objetivo)), con los parámetros del día de la salida.
CREATE FUNCTION bateria_salidas(p_desde timestamptz, p_dias integer)
RETURNS TABLE (fecha date, salida timestamptz, tipo text, consumo_modo text, pct_objetivo numeric,
               consumo_declarado_kwh numeric, consumo_previsto_kwh numeric, objetivo_kwh numeric,
               objetivo_sin_tope_kwh numeric)
LANGUAGE sql STABLE AS $$
    WITH plan AS (
        SELECT p.consumo_kwh FROM public.bateria_consumo_plan(p_desde) p
    ), s AS (
        SELECT q.fecha, q.salida, q.tipo, q.consumo_modo, q.pct_objetivo,
               CASE WHEN q.consumo_modo = 'indicado' THEN
                   coalesce(q.pct / 100 * public.bateria_param('capacidad_util_kwh', q.salida),
                            q.km * public.bateria_param('consumo_kwh_100km', q.salida) / 100)
               END AS declarado
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
    ), o AS (
        SELECT s.*, coalesce(s.declarado, plan.consumo_kwh) AS previsto,
               public.bateria_param('capacidad_util_kwh', s.salida) AS cap
        FROM s CROSS JOIN plan
    ), t AS (
        SELECT o.*, greatest(public.bateria_param('reserva_pct', o.salida) / 100 * o.cap + o.previsto,
                             o.pct_objetivo / 100 * o.cap) AS sin_tope
        FROM o
    )
    SELECT t.fecha, t.salida, t.tipo, t.consumo_modo, t.pct_objetivo, t.declarado, t.previsto,
           least(t.sin_tope, public.bateria_param('tope_pct', t.salida) / 100 * t.cap), t.sin_tope
    FROM t
    ORDER BY t.salida
$$;

-- Estado de la batería y próxima salida en p_ahora (E2.1, E2.7). Confianza
-- (0016): leido si desde la última lectura no ha pasado nada; leido_carga si
-- solo ha habido cargas; estimado en cualquier otro caso (trayectos, coche
-- fuera, carga completa como último valor absoluto o sin lecturas).
CREATE FUNCTION bateria_estado(p_ahora timestamptz)
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
    IF v_abs.tipo = 'lectura' AND coche <> 'fuera' AND NOT EXISTS (
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

CREATE VIEW v_bateria_estado AS SELECT * FROM bateria_estado(now());

CREATE VIEW v_bateria_salida AS
SELECT s.*, CASE WHEN s.salida = e.proxima_salida THEN e.energia_necesaria_kwh END AS energia_necesaria_kwh
FROM bateria_salidas(now(), 7) s
CROSS JOIN bateria_estado(now()) e;

-- --- 4. Avisos (catálogo de formulario-maquetas.md, 7.7) ---------------------------------------

-- Avisos activos en p_ahora. clave identifica el aviso (y su causa) en
-- aviso_historial; nivel: error, aviso o info; accion: pantalla donde se
-- resuelve. «Base de datos no accesible» no está aquí: es la página 503.
CREATE FUNCTION bateria_avisos(p_ahora timestamptz)
RETURNS TABLE (clave text, nivel text, titulo text, detalle text, desde timestamptz, regla text, accion text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    e           record;
    x           record;
    v_ult       timestamptz;
    v_dias      numeric;
BEGIN
    SELECT * INTO e FROM public.bateria_estado(p_ahora);

    SELECT max(m.instante) INTO v_ult FROM public.cargador_muestra m WHERE m.instante <= p_ahora;
    IF v_ult IS NULL
       OR p_ahora - v_ult > public.bateria_param('hueco_muestreo_aviso_s', p_ahora) * interval '1 second' THEN
        clave := 'cargador_no_accesible'; nivel := 'error'; titulo := 'Cargador no accesible';
        detalle := CASE WHEN v_ult IS NULL THEN 'No hay ninguna muestra del cargador.'
                        ELSE format('Última muestra del cargador: %s.',
                                    to_char(v_ult AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI')) END;
        desde := v_ult; regla := 'última muestra más antigua que hueco_muestreo_aviso_s'; accion := NULL;
        RETURN NEXT;
    END IF;

    v_dias := public.bateria_param('aviso_sin_lectura_dias', p_ahora);
    IF e.confianza = 'estimado'
       AND (e.ultima_lectura_ts IS NULL OR p_ahora - e.ultima_lectura_ts > v_dias * interval '1 day') THEN
        clave := 'sin_lecturas'; nivel := 'aviso'; titulo := 'Sin lecturas';
        detalle := CASE WHEN e.ultima_lectura_ts IS NULL THEN 'Aún no hay ninguna lectura: anota el % que marca el coche.'
                        ELSE format('Última lectura: %s.',
                                    to_char(e.ultima_lectura_ts AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI')) END;
        desde := e.ultima_lectura_ts + v_dias * interval '1 day';
        regla := 'nivel ESTIMADO y más de aviso_sin_lectura_dias sin lecturas'; accion := 'lectura';
        RETURN NEXT;
    END IF;

    IF e.dudoso THEN
        clave := 'hueco_muestreo'; nivel := 'aviso'; titulo := 'Hueco de muestreo';
        detalle := 'Faltan muestras del cargador desde el último valor absoluto: el nivel es dudoso.';
        desde := e.ultimo_absoluto; regla := 'hueco > hueco_muestreo_aviso_s'; accion := NULL;
        RETURN NEXT;
    END IF;

    FOR x IN SELECT i.desde, i.hasta, i.consumo_real_kwh FROM public.bateria_intervalo i
             WHERE i.estado = 'incoherente' AND i.hasta <= p_ahora
               AND i.hasta > p_ahora - public.bateria_param('ventana_consumo_dias', p_ahora) * interval '1 day'
             ORDER BY i.desde LOOP
        clave := 'intervalo_incoherente:' || extract(epoch FROM x.desde)::bigint;
        nivel := 'aviso'; titulo := 'Intervalo incoherente';
        detalle := format('Del %s al %s: consumo calculado %s kWh.',
                          to_char(x.desde AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'),
                          to_char(x.hasta AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'),
                          round(x.consumo_real_kwh, 1));
        desde := x.hasta; regla := 'E2.5'; accion := 'historial';
        RETURN NEXT;
    END LOOP;

    -- ¿Carga fuera de casa? Solo el intervalo que termina en la última lectura.
    FOR x IN SELECT i.desde, i.hasta, i.consumo_estimado_kwh - i.consumo_real_kwh AS residuo
             FROM public.bateria_intervalo i
             WHERE i.hasta = e.ultima_lectura_ts AND i.cargas_externas = 0
               AND i.consumo_estimado_kwh - i.consumo_real_kwh
                   > public.bateria_param('umbral_carga_externa_kwh', i.hasta) LOOP
        clave := 'carga_externa_posible:' || extract(epoch FROM x.desde)::bigint;
        nivel := 'aviso'; titulo := '¿Cargaste fuera de casa?';
        detalle := format('La lectura del %s muestra %s kWh más de lo esperado.',
                          to_char(x.hasta AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'), round(x.residuo, 1));
        desde := x.hasta; regla := 'residuo de la reconciliación > umbral_carga_externa_kwh';
        accion := 'carga_externa';
        RETURN NEXT;
    END LOOP;

    FOR x IN SELECT s.fecha, s.salida, s.objetivo_kwh, s.objetivo_sin_tope_kwh,
                    public.bateria_param('capacidad_util_kwh', s.salida) AS cap
             FROM public.bateria_salidas(p_ahora, 7) s
             WHERE s.objetivo_sin_tope_kwh > s.objetivo_kwh LOOP
        clave := 'objetivo_tope:' || x.fecha; nivel := 'aviso'; titulo := 'Objetivo por encima del tope';
        detalle := format('Salida del %s: objetivo %s %% recortado al tope (%s %%).',
                          to_char(x.salida AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'),
                          round(x.objetivo_sin_tope_kwh / x.cap * 100), round(x.objetivo_kwh / x.cap * 100));
        desde := NULL; regla := 'objetivo > tope_pct'; accion := 'excepciones';
        RETURN NEXT;
    END LOOP;

    FOR x IN SELECT s.conector, s.id, s.fin FROM public.bateria_sesion s
             WHERE s.cierre = 'dudoso' AND (e.ultimo_absoluto IS NULL OR s.fin > e.ultimo_absoluto)
             ORDER BY s.fin LOOP
        clave := 'carga_completa_dudosa:' || x.conector || ':' || x.id; nivel := 'aviso';
        titulo := 'Carga completa dudosa';
        detalle := format('Sesión terminada el %s sin muestra posterior.',
                          to_char(x.fin AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'));
        desde := x.fin; regla := 'E2.3'; accion := 'historial';
        RETURN NEXT;
    END LOOP;

    IF e.factor_sugerido IS NOT NULL AND abs(e.factor_sugerido - e.factor_vigente) >= 0.01 THEN
        clave := 'calibracion_sugerida'; nivel := 'info'; titulo := 'Calibración sugerida';
        detalle := format('Eficiencia de carga sugerida %s (vigente %s, %s observación(es)).',
                          round(e.factor_sugerido, 3), e.factor_vigente, e.observaciones_factor);
        desde := NULL; regla := 'E2.6'; accion := 'parametros';
        RETURN NEXT;
    END IF;
END
$$;

CREATE VIEW v_aviso AS SELECT * FROM bateria_avisos(now());

-- Historial: una fila por aparición de un aviso (inicio) hasta que cesa su causa (fin).
CREATE TABLE aviso_historial (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clave       text        NOT NULL,
    nivel       text        NOT NULL CHECK (nivel IN ('error', 'aviso', 'info')),
    titulo      text        NOT NULL,
    detalle     text,
    inicio      timestamptz NOT NULL,
    fin         timestamptz
);

CREATE UNIQUE INDEX aviso_historial_abierto ON aviso_historial (clave) WHERE fin IS NULL;
CREATE INDEX aviso_historial_fin ON aviso_historial (fin) WHERE fin IS NOT NULL;

-- Abre los avisos nuevos, actualiza el detalle de los abiertos y cierra los
-- que ya no tienen causa. Devuelve cuántos hay activos.
CREATE FUNCTION actualizar_avisos(p_ahora timestamptz DEFAULT now()) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_n     integer;
BEGIN
    PERFORM pg_advisory_xact_lock(7265304);
    CREATE TEMP TABLE IF NOT EXISTS aviso_activo_0016 (
        clave text PRIMARY KEY, nivel text, titulo text, detalle text, desde timestamptz) ON COMMIT DROP;
    TRUNCATE aviso_activo_0016;
    INSERT INTO aviso_activo_0016 SELECT a.clave, a.nivel, a.titulo, a.detalle, a.desde
                                  FROM public.bateria_avisos(p_ahora) a;
    UPDATE public.aviso_historial h SET fin = p_ahora
    WHERE h.fin IS NULL AND NOT EXISTS (SELECT 1 FROM aviso_activo_0016 a WHERE a.clave = h.clave);
    UPDATE public.aviso_historial h SET detalle = a.detalle
    FROM aviso_activo_0016 a
    WHERE h.fin IS NULL AND h.clave = a.clave AND h.detalle IS DISTINCT FROM a.detalle;
    INSERT INTO public.aviso_historial (clave, nivel, titulo, detalle, inicio)
    SELECT a.clave, a.nivel, a.titulo, a.detalle, least(coalesce(a.desde, p_ahora), p_ahora)
    FROM aviso_activo_0016 a
    WHERE NOT EXISTS (SELECT 1 FROM public.aviso_historial h WHERE h.fin IS NULL AND h.clave = a.clave);
    SELECT count(*) INTO v_n FROM aviso_activo_0016;
    RETURN v_n;
END
$$;

REVOKE EXECUTE ON FUNCTION actualizar_avisos(timestamptz) FROM PUBLIC, grafana_ro;

-- --- Vistas para las pantallas ------------------------------------------------------------------

-- Historial de lecturas (también las anuladas): estimado justo antes y
-- diferencia en pts. Comparable: hay un valor absoluto anterior, sin huecos de
-- muestreo ni cargas externas sin kWh en el intervalo.
CREATE VIEW v_bateria_lectura_historial AS
SELECT l.id, l.instante, l.pct, l.origen, l.nota, l.alta_en, l.anulada_en, l.motivo, l.corrige,
       b.estimado_previo_kwh / bateria_param('capacidad_util_kwh', l.instante) * 100 AS estimado_pct,
       l.pct - b.estimado_previo_kwh / bateria_param('capacidad_util_kwh', l.instante) * 100 AS diferencia_pts,
       i.estado AS estado_intervalo,
       coalesce(b.estimado_previo_kwh IS NOT NULL AND NOT i.huecos
                AND i.estado <> 'carga_externa_sin_kwh', false) AS comparable
FROM bateria_lectura l
LEFT JOIN bateria_nivel b ON b.tipo = 'lectura' AND b.lectura_id = l.id
LEFT JOIN bateria_intervalo i ON i.hasta = l.instante AND l.anulada_en IS NULL;

-- Excepciones de los 30 días anteriores a hoy: una por fecha (la activa o, si
-- no queda ninguna, la última dada de baja). Previsto: lo declarado o el P75
-- con los datos hasta el día anterior; real: consumo aplicado de sus trayectos.
CREATE VIEW v_bateria_excepcion_pasada AS
WITH hoy AS (
    SELECT (now() AT TIME ZONE 'Europe/Madrid')::date AS d
), x AS (
    SELECT DISTINCT ON (x.fecha) x.*
    FROM bateria_excepcion x, hoy
    WHERE x.fecha < hoy.d AND x.fecha >= hoy.d - 30
    ORDER BY x.fecha, (x.anulada_en IS NULL) DESC, x.alta_en DESC
), dia AS (
    SELECT (tr.salida AT TIME ZONE 'Europe/Madrid')::date AS fecha, count(*)::integer AS trayectos,
           sum(tr.consumo_kwh) AS consumo_kwh, bool_and(tr.origen_consumo = 'reconciliado') AS reconciliado
    FROM bateria_trayecto tr
    WHERE NOT tr.corto
    GROUP BY 1
)
SELECT x.id, x.fecha, x.tipo, x.hora_salida, x.consumo_modo, x.km, x.pct, x.pct_objetivo, x.nota, x.anulada_en,
       CASE WHEN x.anulada_en IS NOT NULL THEN NULL
            WHEN x.tipo = 'sin_salida' THEN 0
            WHEN x.consumo_modo = 'indicado' THEN
                coalesce(x.pct / 100 * bateria_param('capacidad_util_kwh', (x.fecha + x.hora_salida) AT TIME ZONE 'Europe/Madrid'),
                         x.km * bateria_param('consumo_kwh_100km', (x.fecha + x.hora_salida) AT TIME ZONE 'Europe/Madrid') / 100)
            ELSE (SELECT p.consumo_kwh
                  FROM bateria_consumo_plan(x.fecha::timestamp AT TIME ZONE 'Europe/Madrid' - interval '1 microsecond') p)
       END AS previsto_kwh,
       CASE WHEN x.anulada_en IS NULL THEN coalesce(dia.consumo_kwh, 0) END AS real_kwh,
       coalesce(dia.trayectos, 0) AS trayectos,
       dia.reconciliado,
       CASE WHEN x.anulada_en IS NOT NULL THEN 'dada_de_baja'
            WHEN (x.tipo = 'sin_salida') = (coalesce(dia.trayectos, 0) = 0) THEN 'cumplida'
            ELSE 'no_cumplida'
       END AS estado
FROM x
LEFT JOIN dia ON dia.fecha = x.fecha;

-- Registro de eventos con las cargas externas.
CREATE OR REPLACE VIEW v_bateria_evento AS
SELECT l.instante, 'lectura'::text AS tipo, l.pct AS valor, '%'::text AS unidad, 'manual'::text AS origen,
       'lectura:' || l.id AS referencia, l.nota, l.alta_en
FROM bateria_lectura l
WHERE l.anulada_en IS NULL
UNION ALL
SELECT s.fin, 'carga_completa', bateria_param('tope_pct', s.fin), '%', 'cargador',
       'sesion:' || s.conector || ':' || s.id, NULL, NULL
FROM bateria_sesion s
WHERE s.cierre = 'carga_completa'
UNION ALL
SELECT s.fin, 'sesion', s.energia_wh / 1000 * s.factor, 'kWh', 'cargador',
       'sesion:' || s.conector || ':' || s.id, s.cierre, NULL
FROM bateria_sesion s
UNION ALL
SELECT coalesce(t.llegada, t.salida), 'trayecto', t.consumo_kwh, 'kWh', 'sistema',
       'trayecto:' || t.conector || ':' || extract(epoch FROM t.salida)::bigint, t.origen_consumo, NULL
FROM bateria_trayecto t
UNION ALL
SELECT x.instante, 'carga_externa', x.kwh * bateria_param('factor_bateria', x.instante), 'kWh', 'manual',
       'carga_externa:' || x.id, x.nota, x.alta_en
FROM bateria_carga_externa x
WHERE x.anulada_en IS NULL;

-- Últimos eventos de la página de inicio: lectura, carga (+kWh a la batería),
-- carga externa, trayecto (−kWh; estimado si no está reconciliado), conexión e
-- inicio de la carga en curso.
CREATE VIEW v_bateria_evento_reciente AS
SELECT l.instante, 'lectura'::text AS tipo, l.pct AS valor, '%'::text AS unidad, false AS estimado,
       false AS en_curso
FROM bateria_lectura l
WHERE l.anulada_en IS NULL
UNION ALL
SELECT s.fin, 'carga', s.energia_wh / 1000 * s.factor, 'kWh', false, false
FROM bateria_sesion s
UNION ALL
SELECT x.instante, 'carga_externa', x.kwh * bateria_param('factor_bateria', x.instante), 'kWh', false, false
FROM bateria_carga_externa x
WHERE x.anulada_en IS NULL
UNION ALL
SELECT t.llegada, 'trayecto', - t.consumo_kwh, 'kWh', t.origen_consumo IS DISTINCT FROM 'reconciliado', false
FROM bateria_trayecto t
WHERE t.llegada IS NOT NULL AND NOT t.corto
UNION ALL
SELECT t.llegada, 'conexion', NULL, NULL, false, false
FROM bateria_trayecto t
WHERE t.llegada IS NOT NULL
UNION ALL
SELECT min(e.detectado_en), 'inicio_carga', NULL, NULL, false, true
FROM cargador_estado e
WHERE e.estado IN (5, 6)
  AND e.detectado_en > coalesce((SELECT max(s.fin) FROM cargador_sesion s), '-infinity')
  AND (SELECT u.estado FROM cargador_estado u ORDER BY u.detectado_en DESC LIMIT 1) IN (5, 6)
HAVING min(e.detectado_en) IS NOT NULL;

-- Coste de las cargas externas (los paneles de Grafana, en el paso 4).
CREATE VIEW v_coste_carga_externa AS
SELECT x.id, x.instante, (x.instante AT TIME ZONE 'Europe/Madrid')::date AS fecha, x.kwh, x.coste_eur,
       x.coste_eur / nullif(x.kwh, 0) AS eur_kwh,
       x.kwh * bateria_param('factor_bateria', x.instante) AS bateria_kwh,
       x.kwh * bateria_param('factor_bateria', x.instante)
           / bateria_param('capacidad_util_kwh', x.instante) * 100 AS pts,
       x.nota
FROM bateria_carga_externa x
WHERE x.anulada_en IS NULL;

-- --- 6. Permisos -----------------------------------------------------------------------------------

GRANT SELECT ON bateria_carga_externa, bateria_recalculo, aviso_historial, v_bateria_estado, v_bateria_salida,
                v_aviso, v_bateria_lectura_historial, v_bateria_excepcion_pasada, v_bateria_evento,
                v_bateria_evento_reciente, v_coste_carga_externa TO grafana_ro;

-- Rol del formulario (E2.9): lee todo, escribe solo en lo que la página
-- necesita (sin DELETE) y ejecuta el recálculo y los avisos. La contraseña la
-- fija el colector desde FORMULARIO_DB_PASSWORD.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'formulario_rw') THEN
        CREATE ROLE formulario_rw NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO formulario_rw', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO formulario_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO formulario_rw;
GRANT INSERT, UPDATE ON bateria_lectura, bateria_excepcion, bateria_horario, bateria_carga_externa
    TO formulario_rw;
GRANT INSERT, UPDATE (vigencia) ON bateria_parametro TO formulario_rw;
GRANT EXECUTE ON FUNCTION recalcular_bateria(timestamptz), actualizar_avisos(timestamptz) TO formulario_rw;

SELECT recalcular_bateria();
