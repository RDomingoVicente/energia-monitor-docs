-- Planificador de carga: modelo del paso 3a (fase 2, etapa 3; docs/fase2.md,
-- sección «Etapa 3», y docs/tareas/etapa-3-planificador.md).
--
-- 1. Parámetros (E3.9): grupos con su pregunta, columnas grupo, orden y
--    editable en bateria_parametro_clave, 19 claves numéricas nuevas y el
--    CHECK de valores de 0014 sustituido (enteros, 0/1, márgenes con 0).
-- 2. planificador_modo (historial; en 3a, sombra) y planificador_config
--    (contrato y fuente del precio).
-- 3. Plan guardado: plan_calculo y su detalle (plan_cuarto, plan_franja,
--    plan_salida). Lo escribe el servicio muestreo; el CLI no guarda.
-- 4. Control del ciclo: planificador_solicitud (solicitar_plan) y
--    planificador_ciclo.
-- 5. Entradas en SQL: conexiones, cuartos observados y potencia por hora.
-- 6. Vistas: v_plan_vigente, v_plan_franja, v_plan_efectivo y v_plan_ahorro
--    (E3.8, por conexión).
-- 7. Permisos.
--
-- Nada de esto escribe ni lee el programador del cargador (paso 3c).

-- --- 1. Parámetros -------------------------------------------------------------------------

CREATE TABLE bateria_parametro_grupo (
    grupo           smallint PRIMARY KEY CHECK (grupo > 0),
    nombre          text     NOT NULL,
    pregunta        text     NOT NULL,
    avanzado        boolean  NOT NULL DEFAULT false,
    abierto_movil   boolean  NOT NULL DEFAULT false
);

COMMENT ON TABLE bateria_parametro_grupo IS
    'Grupos de la pantalla Parámetros y de la Ayuda (E3.9); pregunta es la frase de propósito.';

INSERT INTO bateria_parametro_grupo (grupo, nombre, pregunta, avanzado, abierto_movil) VALUES
    (1, 'Coche y batería', '¿Cómo es el coche?', false, true),
    (2, 'Estimación del nivel', '¿Cómo se calcula el % sin lecturas?', false, false),
    (3, 'Previsión de consumo', '¿Cuánto voy a gastar?', false, false),
    (4, 'Estrategia de carga', '¿Cuánto cargar y a qué precio?', false, true),
    (5, 'Disponibilidad del coche', '¿Cuándo estará enchufado?', false, false),
    (6, 'Cargador y franjas', '¿Cómo se escribe en el cargador?', true, false),
    (7, 'Avisos y vigilancia', '¿Cuándo me avisa?', false, false);

ALTER TABLE bateria_parametro_clave
    ADD COLUMN grupo    smallint REFERENCES bateria_parametro_grupo (grupo),
    ADD COLUMN orden    smallint,
    ADD COLUMN editable boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN bateria_parametro_clave.unidad IS
    'Unidad del valor guardado; «hora»: minutos desde las 00:00 locales, que se muestran y se escriben HH:MM (0020).';
COMMENT ON COLUMN bateria_parametro_clave.editable IS
    'false: se muestra pero no se cambia desde el formulario (id_tarea_guardiana: solo por CLI).';

INSERT INTO bateria_parametro_clave (clave, unidad, nombre, descripcion, efecto, minimo, maximo) VALUES
    ('carga_oportunista', 'sí/no', 'Carga oportunista',
     'Si está activada, carga por encima de lo necesario cuando la energía está barata (por debajo del umbral y con precio publicado), hasta el nivel oportunista.',
     'Desactivar: solo se carga lo necesario y el margen; menos energía en la batería y menos coste total.',
     0, 1),
    ('umbral_oportunista_pct', '%', 'Umbral barato',
     'Un cuarto de hora es barato si su precio final está por debajo de este porcentaje de la media de los últimos días completos (ventana del umbral). Solo en horas baratas se cargan el margen y la carga oportunista.',
     'Subir: más horas cuentan como baratas y se carga más de lo necesario. Bajar: solo las horas muy baratas.',
     20, 90),
    ('nivel_oportunista_pct', '%', 'Nivel oportunista',
     'Nivel de batería hasta el que se carga en horas baratas, por encima de lo necesario.',
     'Bajar: deja hueco para aprovechar precios aún más bajos otro día. Subir: más colchón con energía barata. Nunca supera el tope de carga.',
     50, 100),
    ('margen_carga_pct', '%', 'Margen de carga',
     'Energía extra sobre la necesaria para cada salida, solo en horas baratas con precio publicado.',
     'Cubre errores de la previsión. Con 0 se carga lo justo.',
     0, 30),
    ('margen_salida_min', 'min', 'Margen antes de salir',
     'La carga planificada termina al menos estos minutos antes de cada salida.',
     'Subir: más holgura si sales antes de la hora. Bajar: aprovecha las últimas horas, a veces más baratas.',
     0, 120),
    ('potencia_carga_inicial_kw', 'kW', 'Potencia de carga inicial',
     'Potencia de carga que se supone en cada hora mientras no hay muestras con carga en la ventana de potencia.',
     'Deja de usarse en cuanto hay medidas. Demasiado alta: el plan reserva pocas horas y puede quedarse corto.',
     1.4, 11),
    ('ventana_disponibilidad_dias', 'días', 'Ventana de disponibilidad',
     'Días hacia atrás de muestras con los que se calcula la probabilidad de que el coche esté enchufado en cada cuarto de hora de la semana.',
     'Más días: más estable, tarda en adaptarse a horarios nuevos. Menos: se adapta antes, más sensible a semanas raras.',
     7, 90),
    ('umbral_disponibilidad_pct', '%', 'Umbral de disponibilidad',
     'Probabilidad mínima de que el coche esté enchufado para planificar en un cuarto de hora futuro (ventana probable).',
     'Subir: solo cuenta con horas casi seguras. Bajar: planifica en más horas, con más riesgo de que el coche no esté.',
     50, 100),
    ('historial_min_disponibilidad_dias', 'días', 'Historial mínimo',
     'Con menos días de muestras, solo se planifica mientras el coche está enchufado y hasta la próxima salida (modo prudente).',
     'Bajar: usa antes la disponibilidad aprendida, con menos datos.',
     7, 60),
    ('horizonte_provisional_h', 'h', 'Horizonte escrito',
     'Horas por delante que el plan escrito en el cargador cubre siempre, con franjas provisionales donde aún no hay precio (red de seguridad; se escribe desde el paso 3c).',
     'Más horas: si el sistema se cae, el cargador sigue cargando más tiempo según lo escrito.',
     24, 72),
    ('franja_min_min', 'min', 'Franja mínima',
     'Duración mínima de una franja: las más cortas se descartan o se unen a la vecina, lo que cueste menos.',
     'Subir: menos franjas y menos arranques. Bajar: se aprovechan mejor los cuartos de hora sueltos baratos.',
     15, 120),
    ('max_tareas_sistema', 'n', 'Franjas máximas',
     'Número máximo de tareas que el planificador usa en el cargador, sin contar la guardiana. Una franja que cruza la medianoche ocupa dos.',
     'El cargador admite 10 tareas: con 7 franjas y la guardiana quedan 2 para la app de Orbis.',
     1, 9),
    ('id_tarea_guardiana', 'n', 'Tarea guardiana',
     'Id de la tarea del cargador siempre activa (31/12 a las 00:00, 1 minuto) que impide que cargue libremente cuando no hay franjas.',
     'No se cambia desde el formulario: dejaría una guardiana huérfana en el cargador. Solo por CLI.',
     1, 10),
    ('aviso_planificador_min', 'min', 'Aviso planificador parado',
     'Si el planificador lleva más tiempo sin completar un ciclo, se avisa (también por correo desde el NAS, paso 3d).',
     'Bajar: te enteras antes de un fallo. Subir: menos avisos por cortes breves.',
     15, 360),
    ('ventana_precio_esperado_dias', 'días', 'Ventana del precio esperado',
     'Días hacia atrás con los que se calcula el precio esperado de las horas aún sin precio publicado: media del mismo día de la semana y hora.',
     'Solo afecta a las franjas provisionales.',
     7, 90),
    ('ventana_umbral_dias', 'días', 'Ventana del umbral',
     'Días completos hacia atrás cuya media de precio final es la referencia del umbral barato.',
     'Más días: umbral estable. Menos: sigue antes las subidas y bajadas del mercado.',
     3, 28),
    ('ventana_potencia_dias', 'días', 'Ventana de potencia',
     'Días hacia atrás de muestras con carga con los que se calcula la potencia de carga de cada hora (mediana).',
     'Más días: más estable. Menos: refleja antes un cambio (otra potencia contratada, otro coche).',
     7, 90),
    ('horizonte_plan_h', 'h', 'Horizonte del plan',
     'Horas por delante que se planifican como máximo (o hasta la última salida conocida, si es antes).',
     'Más horas: tiene en cuenta salidas más lejanas al acumular la energía necesaria. Menos: plan más corto.',
     24, 96),
    ('hora_publicacion_precios', 'hora', 'Publicación de precios',
     'Desde esta hora del día anterior se consideran publicados los precios del día siguiente; antes, sus franjas son provisionales.',
     'También fija cuándo se avisa de precios sin publicar (paso 3d).',
     720, 1080);

UPDATE bateria_parametro_clave SET editable = false WHERE clave = 'id_tarea_guardiana';

UPDATE bateria_parametro_clave k SET grupo = v.grupo, orden = v.orden
FROM (VALUES
    ('capacidad_util_kwh', 1, 1), ('reserva_pct', 1, 2), ('tope_pct', 1, 3), ('factor_bateria', 1, 4),
    ('consumo_kwh_100km', 1, 5),
    ('desconexion_min_s', 2, 1), ('detectar_carga_completa', 2, 2), ('energia_min_calibracion_kwh', 2, 3),
    ('observaciones_calibracion', 2, 4), ('umbral_carga_externa_kwh', 2, 5),
    ('percentil_planificacion', 3, 1), ('ventana_consumo_dias', 3, 2), ('consumo_inicial_trayecto_kwh', 3, 3),
    ('margen_carga_pct', 4, 1), ('margen_salida_min', 4, 2), ('carga_oportunista', 4, 3),
    ('umbral_oportunista_pct', 4, 4), ('nivel_oportunista_pct', 4, 5), ('ventana_precio_esperado_dias', 4, 6),
    ('ventana_umbral_dias', 4, 7),
    ('ventana_disponibilidad_dias', 5, 1), ('umbral_disponibilidad_pct', 5, 2),
    ('historial_min_disponibilidad_dias', 5, 3),
    ('potencia_carga_inicial_kw', 6, 1), ('ventana_potencia_dias', 6, 2), ('franja_min_min', 6, 3),
    ('max_tareas_sistema', 6, 4), ('horizonte_provisional_h', 6, 5), ('horizonte_plan_h', 6, 6),
    ('hora_publicacion_precios', 6, 7), ('id_tarea_guardiana', 6, 8),
    ('aviso_sin_lectura_dias', 7, 1), ('hueco_muestreo_aviso_s', 7, 2), ('aviso_planificador_min', 7, 3)
) AS v (clave, grupo, orden)
WHERE k.clave = v.clave;

-- Toda clave tiene grupo y orden (falla aquí si alguna se quedó sin ellos).
ALTER TABLE bateria_parametro_clave
    ALTER COLUMN grupo SET NOT NULL,
    ALTER COLUMN orden SET NOT NULL,
    ADD CONSTRAINT bateria_parametro_clave_grupo_orden UNIQUE (grupo, orden);

INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente) VALUES
    ('carga_oportunista', '[2025-01-01,)', 1, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('umbral_oportunista_pct', '[2025-01-01,)', 60, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('nivel_oportunista_pct', '[2025-01-01,)', 100, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('margen_carga_pct', '[2025-01-01,)', 10, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('margen_salida_min', '[2025-01-01,)', 30, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('potencia_carga_inicial_kw', '[2025-01-01,)', 4.5, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('ventana_disponibilidad_dias', '[2025-01-01,)', 28, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('umbral_disponibilidad_pct', '[2025-01-01,)', 80, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('historial_min_disponibilidad_dias', '[2025-01-01,)', 21, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('horizonte_provisional_h', '[2025-01-01,)', 48, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('franja_min_min', '[2025-01-01,)', 30, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('max_tareas_sistema', '[2025-01-01,)', 7, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('id_tarea_guardiana', '[2025-01-01,)', 9, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('aviso_planificador_min', '[2025-01-01,)', 60, 'usuario', 'Especificación de la etapa 3 (E3.9)'),
    ('ventana_precio_esperado_dias', '[2025-01-01,)', 28, 'usuario', 'Plan del paso 3a (18/11/2026)'),
    ('ventana_umbral_dias', '[2025-01-01,)', 7, 'usuario', 'Plan del paso 3a (18/11/2026)'),
    ('ventana_potencia_dias', '[2025-01-01,)', 28, 'usuario', 'Plan del paso 3a (18/11/2026)'),
    ('horizonte_plan_h', '[2025-01-01,)', 72, 'usuario', 'Plan del paso 3a (18/11/2026)'),
    ('hora_publicacion_precios', '[2025-01-01,)', 810, 'usuario', 'Plan del paso 3a (18/11/2026)');

-- CHECK de 0014 sustituido: con su ELSE valor > 0, carga_oportunista = 0 y
-- los márgenes a 0 no cabían. Los rangos siguen en el trigger de 0016.
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
    WHEN clave = 'margen_salida_min'       THEN valor >= 0 AND valor = trunc(valor)
    WHEN clave = 'margen_carga_pct'        THEN valor >= 0
    WHEN clave = 'desconexion_min_s'       THEN valor >= 0
    WHEN clave = 'hueco_muestreo_aviso_s'  THEN valor >= 180
    -- Hora local en minutos desde las 00:00 (810 = 13:30); se muestra y se escribe HH:MM.
    WHEN clave = 'hora_publicacion_precios' THEN valor >= 0 AND valor < 1440 AND valor = trunc(valor)
    ELSE valor > 0
END);

-- --- 2. Modo y configuración ----------------------------------------------------------------

-- Modo del planificador (E3.4) con historial. No es un parámetro de
-- bateria_parametro: es texto y cambia en un instante, no en un día.
CREATE TABLE planificador_modo (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    desde       timestamptz NOT NULL UNIQUE,
    modo        text        NOT NULL CHECK (modo IN ('sombra', 'activo', 'pausa')),
    origen      text        NOT NULL CHECK (origen IN ('usuario', 'sistema')),
    nota        text,
    alta_en     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE planificador_modo IS
    'Modo del planificador desde cada instante (sombra, activo, pausa). En el paso 3a solo hay sombra.';

INSERT INTO planificador_modo (desde, modo, origen, nota) VALUES
    ('2025-01-01 00:00:00+01', 'sombra', 'usuario', 'Etapa 3: puesta en marcha en sombra (E3.4).');

-- Modo vigente en un instante; sin filas, sombra (no escribe).
CREATE FUNCTION planificador_modo_en(p_instante timestamptz) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce((SELECT m.modo FROM public.planificador_modo m WHERE m.desde <= p_instante
                     ORDER BY m.desde DESC LIMIT 1), 'sombra');
$$;

-- Contrato y fuente del precio del planificador (E3.2). Una fila; no se
-- edita desde el formulario.
CREATE TABLE planificador_config (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),
    contrato    text NOT NULL REFERENCES contrato (id),
    fuente      text NOT NULL CHECK (fuente <> '')
);

INSERT INTO planificador_config (contrato, fuente) VALUES ('vivienda', 'omie');

-- --- 3. Plan guardado ---------------------------------------------------------------------

-- Un cálculo del planificador. ahora es el instante de referencia (el de los
-- datos); calculado_en, cuándo se hizo.
CREATE TABLE plan_calculo (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ahora                   timestamptz NOT NULL,
    calculado_en            timestamptz NOT NULL DEFAULT now(),
    motivo                  text        NOT NULL,
    modo                    text        NOT NULL CHECK (modo IN ('sombra', 'activo', 'pausa')),
    prudente                boolean     NOT NULL,
    conectado               boolean     NOT NULL,
    nivel_inicial_kwh       numeric,
    capacidad_kwh           numeric     NOT NULL,
    umbral_eur_kwh          numeric,
    horizonte_fin           timestamptz NOT NULL,
    salidas_no_cubiertas    integer     NOT NULL DEFAULT 0 CHECK (salidas_no_cubiertas >= 0),
    avisos                  text[]      NOT NULL DEFAULT '{}',
    duracion_ms             numeric     NOT NULL CHECK (duracion_ms >= 0)
);

CREATE INDEX plan_calculo_ahora ON plan_calculo (ahora);

COMMENT ON TABLE plan_calculo IS
    'Cálculos del planificador (servicio muestreo). ahora: instante de los datos; avisos: frases del cálculo.';

-- Cuartos de hora con carga del plan. kwh_cargador: energía del contador del
-- cargador prevista en el cuarto (potencia × 0,25 h). precio: el publicado o,
-- si provisional, el esperado (E3.4).
CREATE TABLE plan_cuarto (
    calculo_id      bigint      NOT NULL REFERENCES plan_calculo (id) ON DELETE CASCADE,
    inicio          timestamptz NOT NULL,
    nivel           text        NOT NULL CHECK (nivel IN ('necesaria', 'margen', 'oportunista', 'en_curso')),
    salida          timestamptz,
    kwh_cargador    numeric     NOT NULL CHECK (kwh_cargador >= 0),
    precio_eur_kwh  numeric,
    provisional     boolean     NOT NULL,
    ventana         text        NOT NULL CHECK (ventana IN ('segura', 'probable')),
    sobre_umbral    boolean     NOT NULL,
    PRIMARY KEY (calculo_id, inicio),
    CONSTRAINT plan_cuarto_en_rejilla
        CHECK (inicio = date_bin('15 minutes', inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00'))
);

CREATE INDEX plan_cuarto_inicio ON plan_cuarto (inicio);

-- Franjas consolidadas (E3.4). motivo: el nivel de mayor prioridad de sus
-- cuartos; tareas: 1, o 2 si cruza la medianoche local.
CREATE TABLE plan_franja (
    calculo_id      bigint      NOT NULL REFERENCES plan_calculo (id) ON DELETE CASCADE,
    n               smallint    NOT NULL CHECK (n >= 1),
    inicio          timestamptz NOT NULL,
    fin             timestamptz NOT NULL,
    motivo          text        NOT NULL CHECK (motivo IN ('necesaria', 'margen', 'oportunista', 'en_curso')),
    salida          timestamptz,
    kwh_cargador    numeric     NOT NULL CHECK (kwh_cargador >= 0),
    provisional     boolean     NOT NULL,
    tareas          smallint    NOT NULL CHECK (tareas IN (1, 2)),
    en_curso        boolean     NOT NULL,
    -- Ids de las tareas ajenas del cargador que la franja pisa. En sombra se
    -- planifica sin ellas y se anotan aquí; en activo se respetan (E3.4). Se
    -- rellena cuando 3c lea el programador; hasta entonces, vacío.
    choca_con       smallint[]  NOT NULL DEFAULT '{}',
    PRIMARY KEY (calculo_id, n),
    CONSTRAINT plan_franja_intervalo CHECK (fin > inicio)
);

-- Salidas del horizonte y su cobertura. necesaria_kwh: energía a batería que
-- faltaba para el objetivo (antes del plan); nivel_salida_kwh: el previsto
-- al salir con el plan.
CREATE TABLE plan_salida (
    calculo_id              bigint      NOT NULL REFERENCES plan_calculo (id) ON DELETE CASCADE,
    salida                  timestamptz NOT NULL,
    tipo                    text        NOT NULL,
    objetivo_kwh            numeric     NOT NULL,
    consumo_previsto_kwh    numeric     NOT NULL,
    necesaria_kwh           numeric     NOT NULL CHECK (necesaria_kwh >= 0),
    nivel_salida_kwh        numeric,
    cubierta                boolean     NOT NULL,
    salto_umbral            boolean     NOT NULL,
    PRIMARY KEY (calculo_id, salida)
);

-- --- 4. Control del ciclo -------------------------------------------------------------------

-- Petición de recálculo desde otro proceso (formulario, CLI, colector). El
-- muestreo recalcula si solicitado_en es posterior a su último cálculo.
CREATE TABLE planificador_solicitud (
    id              boolean PRIMARY KEY DEFAULT true CHECK (id),
    solicitado_en   timestamptz NOT NULL,
    motivo          text        NOT NULL
);

CREATE FUNCTION solicitar_plan(p_motivo text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    INSERT INTO public.planificador_solicitud (id, solicitado_en, motivo) VALUES (true, clock_timestamp(), p_motivo)
    ON CONFLICT (id) DO UPDATE SET solicitado_en = EXCLUDED.solicitado_en, motivo = EXCLUDED.motivo;
$$;

-- Último ciclo del planificador (aviso «planificador parado», 3d).
CREATE TABLE planificador_ciclo (
    id                  boolean PRIMARY KEY DEFAULT true CHECK (id),
    ultimo_ok_en        timestamptz,
    ultimo_calculo_id   bigint REFERENCES plan_calculo (id) ON DELETE SET NULL,
    ultimo_error_en     timestamptz,
    ultimo_error        text
);

-- --- 5. Entradas en SQL -------------------------------------------------------------------

-- Conexiones del coche que se solapan con [p_desde, p_hasta): rachas de
-- estados no libres (ni 1 ni 2) de cargador_estado, como «conectado desde»
-- del formulario. fin NULL: sigue conectado.
CREATE FUNCTION plan_conexiones(p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (inicio timestamptz, fin timestamptz)
LANGUAGE sql STABLE AS $$
    WITH e AS (
        SELECT s.detectado_en, s.estado NOT IN (1, 2) AS con,
               lag(s.estado NOT IN (1, 2)) OVER (ORDER BY s.detectado_en) AS antes
        FROM public.cargador_estado s
    ), cambios AS (
        SELECT e.detectado_en, e.con, lead(e.detectado_en) OVER (ORDER BY e.detectado_en) AS siguiente
        FROM e
        WHERE e.antes IS DISTINCT FROM e.con
    )
    SELECT c.detectado_en, c.siguiente
    FROM cambios c
    WHERE c.con AND c.detectado_en < p_hasta AND (c.siguiente IS NULL OR c.siguiente > p_desde)
    ORDER BY c.detectado_en;
$$;

-- Cuartos de hora con muestras del cargador en [p_desde, p_hasta), para la
-- disponibilidad aprendida (E3.3). conectado: todas sus muestras con estado
-- tienen un estado no libre. Los cuartos sin muestras no salen.
CREATE FUNCTION plan_cuartos_observados(p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (inicio timestamptz, conectado boolean, muestras integer)
LANGUAGE sql STABLE AS $$
    SELECT date_bin('15 minutes', m.instante, TIMESTAMPTZ '2000-01-01 00:00:00+00'),
           bool_and(m.estado NOT IN (1, 2)), count(*)::integer
    FROM public.cargador_muestra m
    WHERE m.estado IS NOT NULL AND m.instante >= p_desde AND m.instante < p_hasta
    GROUP BY 1
    ORDER BY 1;
$$;

-- Potencia de carga por hora local (E3.2): mediana de evse_w de las muestras
-- en estado 6 con potencia en los ventana_potencia_dias anteriores a
-- p_instante; sin muestras en esa hora, potencia_carga_inicial_kw. Con
-- muestras de W enteros la mediana es exacta (entera o ,5).
CREATE FUNCTION plan_potencia_hora(p_instante timestamptz)
RETURNS TABLE (hora integer, potencia_kw numeric, muestras integer, origen text)
LANGUAGE sql STABLE AS $$
    WITH m AS (
        SELECT extract(hour FROM x.instante AT TIME ZONE 'Europe/Madrid')::integer AS hora,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY x.evse_w)::numeric / 1000 AS mediana,
               count(*)::integer AS n
        FROM public.cargador_muestra x
        WHERE x.estado = 6 AND x.evse_w > 0
          AND x.instante >= p_instante - public.bateria_param('ventana_potencia_dias', p_instante) * interval '1 day'
          AND x.instante < p_instante
        GROUP BY 1
    )
    SELECT h.hora, coalesce(m.mediana, public.bateria_param('potencia_carga_inicial_kw', p_instante)),
           coalesce(m.n, 0), CASE WHEN m.n IS NULL THEN 'inicial' ELSE 'medida' END
    FROM generate_series(0, 23) h (hora)
    LEFT JOIN m USING (hora)
    ORDER BY h.hora;
$$;

-- --- 6. Vistas ---------------------------------------------------------------------------

-- Precio final del planificador por cuarto (contrato y fuente de
-- planificador_config).
CREATE VIEW v_plan_precio AS
SELECT p.inicio, p.fin, p.precio_final_eur_kwh
FROM mv_precio_final p
JOIN planificador_config c ON c.contrato = p.contrato AND c.fuente = p.fuente;

-- Franjas con su precio medio y su coste previsto (precio del cuarto ×
-- energía × factor_contador, como v_coste_tramo). Sin precio en algún cuarto,
-- precio y coste quedan sin valor.
CREATE VIEW v_plan_franja AS
SELECT f.calculo_id, f.n, f.inicio, f.fin, f.motivo, f.salida, f.kwh_cargador, f.provisional, f.tareas,
       f.en_curso, f.choca_con,
       CASE WHEN bool_and(q.precio_eur_kwh IS NOT NULL) THEN
           sum(q.kwh_cargador * q.precio_eur_kwh) / nullif(sum(q.kwh_cargador), 0) END AS precio_medio_eur_kwh,
       CASE WHEN bool_and(q.precio_eur_kwh IS NOT NULL AND fc.valor IS NOT NULL) THEN
           sum(q.kwh_cargador * fc.valor * q.precio_eur_kwh) END AS coste_eur
FROM plan_franja f
JOIN plan_cuarto q ON q.calculo_id = f.calculo_id AND q.inicio >= f.inicio AND q.inicio < f.fin
LEFT JOIN cargador_parametro fc
  ON fc.clave = 'factor_contador' AND fc.vigencia @> (q.inicio AT TIME ZONE 'Europe/Madrid')::date
GROUP BY f.calculo_id, f.n, f.inicio, f.fin, f.motivo, f.salida, f.kwh_cargador, f.provisional, f.tareas,
         f.en_curso, f.choca_con;

-- Franjas del último cálculo.
CREATE VIEW v_plan_vigente AS
SELECT f.*, c.ahora, c.calculado_en, c.modo
FROM v_plan_franja f
JOIN plan_calculo c ON c.id = f.calculo_id
WHERE c.id = (SELECT id FROM plan_calculo ORDER BY ahora DESC, id DESC LIMIT 1);

-- Plan efectivo (E3.8): cada cuarto del plan según el último cálculo hecho
-- antes de que empiece (ahora <= inicio < ahora del cálculo siguiente).
CREATE VIEW v_plan_efectivo AS
WITH c AS (
    SELECT id, ahora, lead(ahora) OVER (ORDER BY ahora, id) AS siguiente
    FROM plan_calculo
)
SELECT q.*, c.ahora AS calculado_para
FROM c
JOIN plan_cuarto q ON q.calculo_id = c.id
WHERE q.inicio >= c.ahora AND (c.siguiente IS NULL OR q.inicio < c.siguiente);

-- Ahorro por conexión (E3.8), con la energía real E (contador del cargador)
-- de cada conexión cerrada posterior al primer cálculo:
--   real: E de cada cuarto a su precio;
--   plan: E repartida en orden de hora en los cuartos del plan efectivo
--         dentro de la conexión y, si no basta, en los primeros cuartos
--         libres de la conexión a la potencia de carga;
--   referencia: E desde el enchufe a la potencia de carga.
-- Coste = kWh × factor_contador × precio final (como v_coste_tramo).
-- Cuartos partidos por el enchufe o la desconexión: capacidad proporcional.
CREATE VIEW v_plan_ahorro AS
WITH primero AS (
    SELECT min(ahora) AS t FROM plan_calculo
), con AS (
    SELECT c.inicio, c.fin
    FROM primero, plan_conexiones(primero.t, now()) c
    WHERE primero.t IS NOT NULL AND c.fin IS NOT NULL AND c.inicio >= primero.t
), pot AS (
    SELECT con.inicio AS con_inicio, ph.hora, ph.potencia_kw
    FROM con CROSS JOIN LATERAL plan_potencia_hora(con.inicio) ph
), q AS (
    SELECT con.inicio AS con_inicio, con.fin AS con_fin, g.q,
           extract(epoch FROM least(g.q + interval '15 minutes', con.fin) - greatest(g.q, con.inicio)) / 3600
               AS horas
    FROM con
    CROSS JOIN LATERAL generate_series(date_bin('15 minutes', con.inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00'),
                                       con.fin - interval '1 microsecond', interval '15 minutes') g (q)
), d AS (
    SELECT q.con_inicio, q.con_fin, q.q,
           coalesce(et.coche_wh, 0) / 1000 AS e_kwh,
           pe.kwh_cargador * q.horas / 0.25 AS plan_cap,
           pot.potencia_kw * q.horas AS libre_cap,
           p.precio_final_eur_kwh AS precio,
           fc.valor AS factor
    FROM q
    LEFT JOIN energia_tramo et ON et.inicio = q.q
    LEFT JOIN v_plan_efectivo pe ON pe.inicio = q.q
    JOIN pot ON pot.con_inicio = q.con_inicio
            AND pot.hora = extract(hour FROM q.q AT TIME ZONE 'Europe/Madrid')::integer
    LEFT JOIN v_plan_precio p ON p.inicio = q.q
    LEFT JOIN cargador_parametro fc
      ON fc.clave = 'factor_contador' AND fc.vigencia @> (q.q AT TIME ZONE 'Europe/Madrid')::date
), acum AS (
    SELECT d.*,
           sum(d.e_kwh) OVER (PARTITION BY d.con_inicio) AS e_total,
           coalesce(d.plan_cap, d.libre_cap) AS cap_plan,
           sum(coalesce(d.plan_cap, d.libre_cap))
               OVER (PARTITION BY d.con_inicio ORDER BY d.plan_cap IS NULL, d.q) AS cum_plan,
           sum(d.libre_cap) OVER (PARTITION BY d.con_inicio ORDER BY d.q) AS cum_ref
    FROM d
), reparto AS (
    SELECT a.*,
           greatest(0, least(a.cap_plan, a.e_total - (a.cum_plan - a.cap_plan))) AS e_plan,
           greatest(0, least(a.libre_cap, a.e_total - (a.cum_ref - a.libre_cap))) AS e_ref
    FROM acum a
)
SELECT r.con_inicio AS inicio,
       r.con_fin AS fin,
       (r.con_inicio AT TIME ZONE 'Europe/Madrid')::date AS fecha,
       r.energia_cargador_kwh,
       r.energia_kwh, r.plan_kwh, r.plan_kwh >= r.energia_cargador_kwh AS plan_cubre,
       r.referencia_kwh < r.energia_cargador_kwh AS referencia_incompleta,
       r.coste_real_eur, r.coste_plan_eur, r.coste_referencia_eur,
       r.coste_referencia_eur - r.coste_plan_eur AS ahorro_plan_eur,
       r.coste_referencia_eur - r.coste_real_eur AS ahorro_real_eur,
       r.sin_precio
FROM (
    SELECT x.con_inicio, x.con_fin,
           max(x.e_total) AS energia_cargador_kwh,
           coalesce(sum(x.plan_cap), 0) AS plan_kwh,
           sum(x.e_ref) AS referencia_kwh,
           bool_or(x.precio IS NULL OR x.factor IS NULL) AS sin_precio,
           CASE WHEN bool_and(x.factor IS NOT NULL) THEN sum(x.e_kwh * x.factor) END AS energia_kwh,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               sum(x.e_kwh * x.factor * x.precio) END AS coste_real_eur,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               sum(x.e_plan * x.factor * x.precio) END AS coste_plan_eur,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               sum(x.e_ref * x.factor * x.precio) END AS coste_referencia_eur
    FROM reparto x
    GROUP BY x.con_inicio, x.con_fin
) r;

COMMENT ON VIEW v_plan_ahorro IS
    'E3.8 por conexión cerrada: coste real, del plan efectivo y de cargar al enchufar, con la misma energía. '
    'sin_precio: algún cuarto sin precio o sin factor_contador (los costes salen sin valor).';

-- --- 7. Permisos -----------------------------------------------------------------------------

GRANT SELECT ON bateria_parametro_grupo, planificador_modo, planificador_config, plan_calculo, plan_cuarto,
                plan_franja, plan_salida, planificador_solicitud, planificador_ciclo, v_plan_precio,
                v_plan_franja, v_plan_vigente, v_plan_efectivo, v_plan_ahorro
    TO grafana_ro, formulario_rw;
GRANT EXECUTE ON FUNCTION solicitar_plan(text) TO formulario_rw;
REVOKE EXECUTE ON FUNCTION solicitar_plan(text) FROM PUBLIC, grafana_ro;
