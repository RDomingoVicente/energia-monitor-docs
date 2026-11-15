-- Fase 2, etapa 2: nivel de batería estimado y previsión (v0.5.0).
--
-- Especificación: docs/fase2.md, "Etapa 2: batería y previsión" (E2.1 a
-- E2.8). Datos del usuario: lecturas, horario y excepciones. Datos
-- derivados, que solo escribe recalcular_bateria(): sesiones clasificadas,
-- trayectos, eventos con su nivel e intervalos entre valores absolutos.
-- recalcular_bateria() lo rehace todo desde cargador_estado,
-- cargador_sesion, cargador_muestra y los datos del usuario; la llaman el
-- muestreo (cambio de estado, sesiones nuevas), el CLI y, en el paso 3, el
-- formulario. Referencia en Python: src/energia_monitor/bateria.py.
--
-- Unidades: kWh útiles de batería; porcentajes de 0 a 100 sobre
-- capacidad_util_kwh. Instantes en UTC; fechas y horas del usuario en
-- Europe/Madrid.

-- --- Parámetros (E2.8) ----------------------------------------------------------------

CREATE TABLE bateria_parametro_clave (
    clave           text PRIMARY KEY,
    unidad          text NOT NULL,
    descripcion     text NOT NULL
);

INSERT INTO bateria_parametro_clave (clave, unidad, descripcion) VALUES
    ('capacidad_util_kwh', 'kWh', 'Capacidad útil de la batería'),
    ('reserva_pct', '%', 'Nivel mínimo al salir'),
    ('tope_pct', '%', 'Nivel máximo de carga; carga_completa fija el nivel aquí'),
    ('factor_bateria', 'factor', 'kWh que llegan a la batería por cada kWh del contador del cargador'),
    ('percentil_planificacion', 'fracción', 'Percentil del consumo por día de uso para planificar'),
    ('ventana_consumo_dias', 'días', 'Ventana de la estadística de consumo'),
    ('desconexion_min_s', 's', 'Desconexiones más cortas: consumo cero'),
    ('consumo_inicial_trayecto_kwh', 'kWh', 'Consumo por trayecto (o por día) sin historia'),
    ('consumo_kwh_100km', 'kWh/100 km', 'Conversión de los viajes largos declarados en km'),
    ('energia_min_calibracion_kwh', 'kWh', 'Energía mínima del contador para observar el factor'),
    ('observaciones_calibracion', 'n', 'Observaciones del factor que entran en el sugerido'),
    ('detectar_carga_completa', 'sí/no', 'Regla de carga completa (E2.3): 1 activa, 0 desactivada'),
    ('aviso_sin_lectura_dias', 'días', 'Aviso si la última lectura es más antigua'),
    ('hueco_muestreo_aviso_s', 's', 'Hueco de muestreo que puede esconder un trayecto');

CREATE TABLE bateria_parametro (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clave           text      NOT NULL REFERENCES bateria_parametro_clave (clave),
    vigencia        daterange NOT NULL,
    valor           numeric   NOT NULL,
    origen          text      NOT NULL
        CHECK (origen IN ('contrato', 'regulado', 'estimado', 'usuario', 'ficha_tecnica')),
    fuente          text      NOT NULL,
    nota            text,
    CONSTRAINT bateria_parametro_vigencia_valida
        CHECK (NOT isempty(vigencia) AND NOT lower_inf(vigencia)
               AND lower_inc(vigencia) AND NOT upper_inc(vigencia)),
    CONSTRAINT bateria_parametro_sin_solapes
        EXCLUDE USING gist (clave WITH =, vigencia WITH &&),
    CONSTRAINT bateria_parametro_valor_valido CHECK (CASE clave
        WHEN 'detectar_carga_completa' THEN valor IN (0, 1)
        WHEN 'percentil_planificacion' THEN valor > 0 AND valor < 1
        WHEN 'factor_bateria'          THEN valor > 0 AND valor <= 1
        WHEN 'reserva_pct'             THEN valor >= 0 AND valor < 100
        WHEN 'tope_pct'                THEN valor > 0 AND valor <= 100
        WHEN 'ventana_consumo_dias'    THEN valor >= 1 AND valor = trunc(valor)
        WHEN 'observaciones_calibracion' THEN valor >= 1 AND valor = trunc(valor)
        WHEN 'aviso_sin_lectura_dias'  THEN valor >= 1 AND valor = trunc(valor)
        WHEN 'desconexion_min_s'       THEN valor >= 0
        WHEN 'hueco_muestreo_aviso_s'  THEN valor >= 180
        ELSE valor > 0
    END)
);

INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente, nota) VALUES
    ('capacidad_util_kwh', '[2025-01-01,)', 91.3, 'ficha_tecnica',
     'BYD Sealion 7 Excellence: 91,3 kWh útiles (93,0 brutos)', NULL),
    ('reserva_pct', '[2025-01-01,)', 25, 'usuario', 'Especificación de la fase 2', NULL),
    ('tope_pct', '[2025-01-01,)', 100, 'usuario', 'Sin límite de carga configurado en el coche', NULL),
    ('factor_bateria', '[2025-01-01,)', 0.86, 'estimado', 'Estimación inicial de la fase 2',
     'Calibrar con pares de lecturas (factor sugerido de bateria estado)'),
    ('percentil_planificacion', '[2025-01-01,)', 0.75, 'usuario', 'Especificación de la etapa 2', NULL),
    ('ventana_consumo_dias', '[2025-01-01,)', 28, 'usuario', 'Especificación de la etapa 2', NULL),
    ('desconexion_min_s', '[2025-01-01,)', 1800, 'usuario', 'Especificación de la etapa 2', NULL),
    ('consumo_inicial_trayecto_kwh', '[2025-01-01,)', 25, 'usuario', 'Especificación de la etapa 2', NULL),
    ('consumo_kwh_100km', '[2025-01-01,)', 23, 'usuario', 'Especificación de la etapa 2', NULL),
    ('energia_min_calibracion_kwh', '[2025-01-01,)', 5, 'usuario', 'Especificación de la etapa 2', NULL),
    ('observaciones_calibracion', '[2025-01-01,)', 5, 'usuario', 'Especificación de la etapa 2', NULL),
    ('detectar_carga_completa', '[2025-01-01,)', 1, 'usuario', 'Especificación de la etapa 2',
     'Regla sin verificar con el coche'),
    ('aviso_sin_lectura_dias', '[2025-01-01,)', 7, 'usuario', 'Especificación de la etapa 2', NULL),
    ('hueco_muestreo_aviso_s', '[2025-01-01,)', 1800, 'usuario', 'Especificación de la etapa 2', NULL);

-- Valor vigente en la fecha local del instante; error si no hay.
CREATE FUNCTION bateria_param(p_clave text, p_instante timestamptz) RETURNS numeric
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v numeric;
BEGIN
    SELECT p.valor INTO v
    FROM public.bateria_parametro p
    WHERE p.clave = p_clave
      AND p.vigencia @> (p_instante AT TIME ZONE 'Europe/Madrid')::date;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'parámetro de batería % sin valor el %', p_clave,
            (p_instante AT TIME ZONE 'Europe/Madrid')::date;
    END IF;
    RETURN v;
END
$$;

-- --- Datos del usuario -------------------------------------------------------------------

-- Lecturas del nivel (E2.1). Nunca se borran: se anulan con motivo; una
-- corrección es una lectura nueva que apunta a la anulada.
CREATE TABLE bateria_lectura (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instante        timestamptz NOT NULL,
    pct             numeric     NOT NULL CHECK (pct >= 0 AND pct <= 100),
    origen          text        NOT NULL CHECK (origen IN ('formulario', 'cli')),
    nota            text,
    alta_en         timestamptz NOT NULL DEFAULT now(),
    anulada_en      timestamptz,
    motivo          text,
    corrige         bigint REFERENCES bateria_lectura (id),
    CONSTRAINT bateria_lectura_anulacion_con_motivo
        CHECK ((anulada_en IS NULL) = (motivo IS NULL) AND (motivo IS NULL OR btrim(motivo) <> ''))
);

CREATE UNIQUE INDEX bateria_lectura_instante_activa ON bateria_lectura (instante) WHERE anulada_en IS NULL;

COMMENT ON TABLE bateria_lectura IS
    'Lecturas manuales del nivel (%). Se anulan (anulada_en y motivo), nunca se borran.';

-- Horario semanal (E2.7): hora local de salida; NULL o sin fila = sin salida.
CREATE TABLE bateria_horario (
    dia_iso         smallint PRIMARY KEY CHECK (dia_iso BETWEEN 1 AND 7),
    hora_salida     time,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE bateria_horario IS
    'Hora local de salida por día de la semana (1 = lunes); NULL o sin fila: sin salida.';

-- Excepciones con fecha (E2.7). Una activa por fecha; la baja es lógica.
CREATE TABLE bateria_excepcion (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo            text    NOT NULL CHECK (tipo IN ('sin_coche', 'otra_hora', 'nivel_objetivo', 'viaje_largo')),
    fecha           date    NOT NULL,
    hora            time,
    pct_objetivo    numeric CHECK (pct_objetivo > 0 AND pct_objetivo <= 100),
    consumo_pct     numeric CHECK (consumo_pct > 0 AND consumo_pct <= 100),
    consumo_km      numeric CHECK (consumo_km > 0),
    nota            text,
    alta_en         timestamptz NOT NULL DEFAULT now(),
    anulada_en      timestamptz,
    CONSTRAINT bateria_excepcion_campos_del_tipo CHECK (CASE tipo
        WHEN 'sin_coche' THEN hora IS NULL AND pct_objetivo IS NULL
                              AND consumo_pct IS NULL AND consumo_km IS NULL
        WHEN 'otra_hora' THEN hora IS NOT NULL AND pct_objetivo IS NULL
                              AND consumo_pct IS NULL AND consumo_km IS NULL
        WHEN 'nivel_objetivo' THEN hora IS NOT NULL AND pct_objetivo IS NOT NULL
                              AND consumo_pct IS NULL AND consumo_km IS NULL
        WHEN 'viaje_largo' THEN hora IS NOT NULL AND pct_objetivo IS NULL
                              AND (consumo_pct IS NULL) <> (consumo_km IS NULL)
    END)
);

CREATE UNIQUE INDEX bateria_excepcion_fecha_activa ON bateria_excepcion (fecha) WHERE anulada_en IS NULL;

COMMENT ON TABLE bateria_excepcion IS
    'Excepciones con fecha local; una activa por fecha. Viaje largo: consumo en % de capacidad_util_kwh o en km.';

-- --- Datos derivados (solo los escribe recalcular_bateria) -------------------------------

-- Sesiones cerradas con su clasificación de fin (E2.2, E2.3).
CREATE TABLE bateria_sesion (
    conector        text        NOT NULL,
    id              bigint      NOT NULL,
    inicio          timestamptz NOT NULL,
    fin             timestamptz NOT NULL,
    energia_wh      numeric     NOT NULL,
    factor          numeric     NOT NULL,
    cierre          text        NOT NULL CHECK (cierre IN ('carga_completa', 'fin_franja', 'desconexion',
                                                          'dudoso', 'pendiente', 'sin_regla', 'otro')),
    PRIMARY KEY (conector, id)
);

COMMENT ON COLUMN bateria_sesion.cierre IS
    'carga_completa: origen evsm y conector en 4 u 8 en la primera muestra de los 180 s siguientes;'
    ' fin_franja: origen schedman; desconexion: libre en esa muestra; dudoso: sin esa muestra o con otro'
    ' estado; pendiente: aún no han pasado 180 s; sin_regla: detectar_carga_completa = 0; otro: otro origen.';

-- Trayectos (E2.4): de conectado (3..8) a libre (1, 2) y vuelta.
CREATE TABLE bateria_trayecto (
    conector              text        NOT NULL,
    salida                timestamptz NOT NULL,
    llegada               timestamptz,
    corto                 boolean     NOT NULL,
    viaje_largo           boolean     NOT NULL,
    consumo_estimado_kwh  numeric,
    consumo_kwh           numeric,
    origen_consumo        text CHECK (origen_consumo IN ('cero', 'estimado', 'inicial', 'declarado',
                                                         'reconciliado')),
    reconciliado_en       timestamptz,
    PRIMARY KEY (conector, salida)
);

COMMENT ON COLUMN bateria_trayecto.consumo_kwh IS
    'Consumo aplicado (kWh): el real si está reconciliado; si no, el estimado. NULL mientras el coche está fuera.';

-- Eventos en orden, partidos por los valores absolutos que caen dentro de
-- una sesión o de un trayecto, con el nivel después de cada uno (E2.1).
CREATE TABLE bateria_nivel (
    n                   integer     PRIMARY KEY,
    instante            timestamptz NOT NULL,
    orden               smallint    NOT NULL,  -- 1 sesión, 2 carga completa, 3 trayecto, 4 lectura
    ref                 text        NOT NULL,
    tipo                text        NOT NULL CHECK (tipo IN ('sesion', 'carga_completa', 'trayecto', 'lectura')),
    conector            text,
    sesion_id           bigint,
    trayecto_salida     timestamptz,
    lectura_id          bigint,
    peso                numeric,    -- parte de la sesión o del trayecto que cae aquí (0..1)
    contador_kwh        numeric,    -- sesión: kWh del contador del cargador en esta parte
    absoluto_kwh        numeric,    -- lectura y carga completa
    delta_estimado_kwh  numeric,    -- sesión: + energía × factor; trayecto: − consumo estimado
    delta_kwh           numeric,    -- el aplicado: en trayectos reconciliados, − consumo real
    nivel_estimado_kwh  numeric,    -- nivel con los consumos estimados (lo previsto)
    estimado_previo_kwh numeric,    -- valores absolutos: nivel previsto justo antes
    nivel_kwh           numeric,    -- nivel con los consumos reconciliados; NULL antes de la 1.ª lectura
    nivel_pct           numeric,
    UNIQUE (instante, orden, ref)
);

CREATE INDEX bateria_nivel_trayecto ON bateria_nivel (conector, trayecto_salida) WHERE trayecto_salida IS NOT NULL;

COMMENT ON TABLE bateria_nivel IS
    'Eventos de batería en orden con el nivel después de cada uno; lo rehace recalcular_bateria().';

-- Intervalos entre valores absolutos consecutivos (E2.5, E2.6).
CREATE TABLE bateria_intervalo (
    desde                 timestamptz PRIMARY KEY,
    hasta                 timestamptz NOT NULL,
    a1_kwh                numeric     NOT NULL,
    a2_kwh                numeric     NOT NULL,
    sesiones_kwh          numeric     NOT NULL,  -- con factor_bateria
    contador_kwh          numeric     NOT NULL,  -- sin factor
    trayectos             integer     NOT NULL,  -- partes de trayectos no cortos
    trayectos_cortos      integer     NOT NULL,
    consumo_estimado_kwh  numeric     NOT NULL,
    consumo_real_kwh      numeric     NOT NULL,  -- a1 + sesiones − a2
    estado                text        NOT NULL CHECK (estado IN ('reconciliado', 'incoherente', 'sin_trayectos')),
    huecos                boolean     NOT NULL,
    factor_observado      numeric               -- solo en los intervalos válidos para calibrar (E2.6)
);

-- --- Huecos de muestreo ------------------------------------------------------------------

-- ¿Hay entre p_desde y p_hasta un tramo sin muestras de más de p_umbral_s
-- (>= 180)? Un hueco de más de 180 s deja la muestra siguiente con
-- intervalo_s NULL (0010): basta mirar esas muestras y los dos extremos.
CREATE INDEX cargador_muestra_tras_hueco ON cargador_muestra (instante) WHERE intervalo_s IS NULL;

CREATE FUNCTION bateria_hueco(p_desde timestamptz, p_hasta timestamptz, p_umbral_s numeric) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT
        -- Desde la última muestra hasta p_hasta (o todo, si no hay ninguna).
        extract(epoch FROM p_hasta - coalesce(
            (SELECT max(m.instante) FROM public.cargador_muestra m
             WHERE m.instante > p_desde AND m.instante < p_hasta), p_desde)) > p_umbral_s
        OR EXISTS (
            -- Antes de cada muestra tras un hueco, hasta la anterior (o p_desde).
            SELECT 1 FROM public.cargador_muestra m
            WHERE m.intervalo_s IS NULL AND m.instante > p_desde AND m.instante < p_hasta
              AND extract(epoch FROM m.instante - coalesce(
                  (SELECT max(a.instante) FROM public.cargador_muestra a
                   WHERE a.instante > p_desde AND a.instante < m.instante), p_desde)) > p_umbral_s)
        OR extract(epoch FROM coalesce(
            (SELECT min(m.instante) FROM public.cargador_muestra m
             WHERE m.instante > p_desde AND m.instante < p_hasta), p_hasta) - p_desde) > p_umbral_s
$$;

-- --- Recálculo -----------------------------------------------------------------------------

-- Rehace sesiones, trayectos, eventos e intervalos. Idempotente; un advisory
-- lock serializa recálculos simultáneos. SECURITY DEFINER: el rol del
-- formulario (paso 3) podrá ejecutarla sin escribir en las tablas derivadas.
CREATE FUNCTION recalcular_bateria(p_ahora timestamptz DEFAULT now()) RETURNS integer
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
               WHERE x.anulada_en IS NULL AND x.tipo = 'viaje_largo'
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

-- Consumo estimado de un trayecto (E2.4); lo guarda y lo devuelve. Usa los
-- trayectos ya reconciliados por un valor absoluto anterior a su salida.
CREATE FUNCTION bateria_estimar_trayecto(p_conector text, p_salida timestamptz, p_ahora timestamptz)
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
        WHERE anulada_en IS NULL AND tipo = 'viaje_largo' AND fecha = v_dia;
        v_decl := coalesce(x.consumo_pct / 100 * public.bateria_param('capacidad_util_kwh', t.salida),
                           x.consumo_km * public.bateria_param('consumo_kwh_100km', t.salida) / 100);
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

-- Solo la escribe el servicio (y, en el paso 3, el formulario).
REVOKE EXECUTE ON FUNCTION recalcular_bateria(timestamptz) FROM PUBLIC, grafana_ro;
REVOKE EXECUTE ON FUNCTION bateria_estimar_trayecto(text, timestamptz, timestamptz) FROM PUBLIC, grafana_ro;

-- --- Estadística y previsión (E2.6, E2.7) --------------------------------------------------

-- Días de uso: día local de salida con algún trayecto no corto. Cuenta para
-- la estadística si todos sus trayectos están cerrados y reconciliados y
-- ninguno es de viaje largo.
CREATE VIEW v_bateria_consumo_dia AS
SELECT
    (salida AT TIME ZONE 'Europe/Madrid')::date                       AS fecha,
    count(*)                                                          AS trayectos,
    sum(consumo_kwh)                                                  AS consumo_kwh,
    bool_and(origen_consumo = 'reconciliado')                         AS reconciliado,
    bool_or(viaje_largo)                                              AS viaje_largo,
    bool_and(origen_consumo = 'reconciliado') AND NOT bool_or(viaje_largo) AS cuenta
FROM bateria_trayecto
WHERE NOT corto
GROUP BY 1;

-- Consumo para planificar (E2.7): percentil del consumo por día de uso de los
-- últimos ventana_consumo_dias días locales (hoy incluido).
CREATE FUNCTION bateria_consumo_plan(p_ahora timestamptz)
RETURNS TABLE (consumo_kwh numeric, dias integer, origen text)
LANGUAGE sql STABLE AS $$
    WITH d AS (
        SELECT c.consumo_kwh
        FROM public.v_bateria_consumo_dia c
        WHERE c.cuenta
          AND c.fecha > (p_ahora AT TIME ZONE 'Europe/Madrid')::date
                        - public.bateria_param('ventana_consumo_dias', p_ahora)::integer
          AND c.fecha <= (p_ahora AT TIME ZONE 'Europe/Madrid')::date
    )
    SELECT coalesce((SELECT percentile_cont(public.bateria_param('percentil_planificacion', p_ahora)::float8)
                            WITHIN GROUP (ORDER BY d.consumo_kwh)::numeric FROM d),
                    public.bateria_param('consumo_inicial_trayecto_kwh', p_ahora)),
           (SELECT count(*)::integer FROM d),
           CASE WHEN EXISTS (SELECT 1 FROM d) THEN 'historia' ELSE 'inicial' END
$$;

-- Observaciones del factor y factor sugerido (E2.6).
CREATE VIEW v_bateria_calibracion AS
SELECT desde, hasta, a1_kwh, a2_kwh, contador_kwh, factor_observado
FROM bateria_intervalo
WHERE factor_observado IS NOT NULL;

CREATE FUNCTION bateria_factor_sugerido(p_ahora timestamptz)
RETURNS TABLE (factor numeric, observaciones integer)
LANGUAGE sql STABLE AS $$
    WITH ultimas AS (
        SELECT c.factor_observado FROM public.v_bateria_calibracion c
        WHERE c.hasta <= p_ahora
        ORDER BY c.hasta DESC
        LIMIT public.bateria_param('observaciones_calibracion', p_ahora)::integer
    )
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY factor_observado)::numeric, count(*)::integer
    FROM ultimas
$$;

-- Salidas desde p_desde durante p_dias días locales más (E2.7): horario
-- semanal y excepciones. consumo_declarado_kwh solo en viajes largos.
CREATE FUNCTION bateria_salidas(p_desde timestamptz, p_dias integer)
RETURNS TABLE (fecha date, salida timestamptz, tipo text, pct_objetivo numeric, consumo_declarado_kwh numeric)
LANGUAGE sql STABLE AS $$
    SELECT q.fecha, q.salida, q.tipo, q.pct_objetivo,
           CASE WHEN q.tipo = 'viaje_largo' THEN
               coalesce(q.consumo_pct / 100 * public.bateria_param('capacidad_util_kwh', q.salida),
                        q.consumo_km * public.bateria_param('consumo_kwh_100km', q.salida) / 100)
           END
    FROM (
        SELECT d.fecha,
               (d.fecha + coalesce(x.hora, h.hora_salida)) AT TIME ZONE 'Europe/Madrid' AS salida,
               coalesce(x.tipo, 'horario') AS tipo,
               x.pct_objetivo, x.consumo_pct, x.consumo_km
        FROM (SELECT g::date AS fecha
              FROM generate_series((p_desde AT TIME ZONE 'Europe/Madrid')::date,
                                   (p_desde AT TIME ZONE 'Europe/Madrid')::date + p_dias,
                                   interval '1 day') g) d
        LEFT JOIN public.bateria_excepcion x ON x.fecha = d.fecha AND x.anulada_en IS NULL
        LEFT JOIN public.bateria_horario h ON h.dia_iso = extract(isodow FROM d.fecha)
        WHERE x.tipo IS DISTINCT FROM 'sin_coche'
          AND coalesce(x.hora, h.hora_salida) IS NOT NULL
    ) q
    WHERE q.salida > p_desde
    ORDER BY q.salida
$$;

-- Estado de la batería y próxima salida en p_ahora (E2.1, E2.7).
CREATE FUNCTION bateria_estado(p_ahora timestamptz)
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
    consumo_plan_kwh := CASE WHEN v_sal.tipo = 'viaje_largo' THEN v_sal.consumo_declarado_kwh
                             ELSE v_plan.consumo_kwh END;
    origen_consumo_plan := CASE WHEN v_sal.tipo = 'viaje_largo' THEN 'declarado' ELSE v_plan.origen END;
    reserva_kwh := public.bateria_param('reserva_pct', p_ahora) / 100 * v_cap;
    tope_kwh := public.bateria_param('tope_pct', p_ahora) / 100 * v_cap;
    IF v_sal.salida IS NOT NULL AND nivel_plan_kwh IS NOT NULL THEN
        IF v_sal.tipo = 'nivel_objetivo' THEN
            v_objetivo := v_sal.pct_objetivo / 100 * v_cap;
        ELSE
            v_objetivo := reserva_kwh + consumo_plan_kwh;
        END IF;
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

CREATE VIEW v_bateria_estado AS SELECT * FROM bateria_estado(now());

CREATE VIEW v_bateria_salida AS
SELECT s.fecha, s.salida, s.tipo, s.pct_objetivo, s.consumo_declarado_kwh,
       CASE WHEN s.salida = e.proxima_salida THEN e.energia_necesaria_kwh END AS energia_necesaria_kwh
FROM bateria_salidas(now(), 7) s
CROSS JOIN bateria_estado(now()) e;

-- Registro de eventos (E2.1): lecturas, cargas completas, sesiones y trayectos.
CREATE VIEW v_bateria_evento AS
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
FROM bateria_trayecto t;

-- Lectura de todo lo nuevo para grafana_ro (los privilegios por defecto de
-- 0006 ya lo cubren; explícito por claridad).
GRANT SELECT ON bateria_parametro_clave, bateria_parametro, bateria_lectura, bateria_horario,
                bateria_excepcion, bateria_sesion, bateria_trayecto, bateria_nivel, bateria_intervalo,
                v_bateria_consumo_dia, v_bateria_calibracion, v_bateria_estado, v_bateria_salida,
                v_bateria_evento TO grafana_ro;

SELECT recalcular_bateria();
