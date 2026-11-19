-- Planificador, paso 3d (v0.8.0): avisos que faltaban de E3.10 y catálogo con correo.
--
-- 1. aviso_precios_min (30, grupo 7): margen tras hora_publicacion_precios
--    antes de avisar de que faltan los precios del día siguiente.
-- 2. aviso_tipo: catálogo de avisos (nivel, título, regla y si van por
--    correo). El tipo es la clave del aviso sin el sufijo «:N». La pantalla
--    Avisos y la vigilancia del NAS (deploy/vigilancia.sh) lo leen.
-- 3. planificador_aviso.origen: los del reconciliador se reemplazan en cada
--    ciclo; los del muestreo («Cargador distinto del plan») los mantiene el
--    muestreo.
-- 4. bateria_avisos(): añade, en SQL, «Planificador parado», «Precios del día
--    siguiente sin publicar» y «El plan no cubre una salida». Los dos
--    primeros solo cuando el planificador ha funcionado alguna vez (hay fila
--    en planificador_ciclo).
-- 5. v_aviso con la columna correo.

-- --- 1. Parámetro ---------------------------------------------------------------------------

INSERT INTO bateria_parametro_clave (clave, unidad, nombre, descripcion, efecto, minimo, maximo, grupo, orden, nota)
VALUES ('aviso_precios_min', 'min', 'Aviso precios sin publicar',
        'Margen tras la hora de publicación de los precios: si pasado este tiempo aún faltan los precios del día siguiente, se avisa.',
        'Bajar: te enteras antes. Subir: menos avisos si la descarga se retrasa un poco.',
        0, 240, 7, 4, 'el colector descarga a la misma hora que la publicación');

INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente) VALUES
    ('aviso_precios_min', '[2025-01-01,)', 30, 'usuario', 'Paso 3d (19/11/2026)');

-- CHECK de la 0022 con la clave nueva (minutos enteros, 0 admitido).
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
    WHEN clave = 'margen_carga_pct'        THEN valor >= 0
    WHEN clave = 'desconexion_min_s'       THEN valor >= 0
    WHEN clave = 'hueco_muestreo_aviso_s'  THEN valor >= 180
    WHEN clave = 'hora_publicacion_precios' THEN valor >= 0 AND valor < 1440 AND valor = trunc(valor)
    ELSE valor > 0
END);

-- --- 2. Catálogo de avisos ------------------------------------------------------------------

CREATE TABLE aviso_tipo (
    tipo        text PRIMARY KEY,
    nivel       text     NOT NULL CHECK (nivel IN ('error', 'aviso', 'info')),
    titulo      text     NOT NULL,
    regla       text     NOT NULL,
    correo      boolean  NOT NULL,
    orden       smallint NOT NULL UNIQUE
);

COMMENT ON TABLE aviso_tipo IS
    'Catálogo de avisos (formulario-maquetas.md, 7.7). tipo = clave del aviso sin «:N». correo: la vigilancia del NAS lo envía.';

-- Correo: todos los ERROR, más escritura fallida y el plan que no cubre una salida (paso 3d).
INSERT INTO aviso_tipo (tipo, nivel, titulo, regla, correo, orden) VALUES
    ('cargador_no_accesible', 'error', 'Cargador no accesible',
     'última muestra más antigua que hueco_muestreo_aviso_s (la página no consulta el cargador)', true, 10),
    ('bd_no_accesible', 'error', 'Base de datos no accesible',
     'la página da error 503; la vigilancia del NAS no puede consultarla', true, 20),
    ('planificador_parado', 'error', 'Planificador parado',
     'último ciclo correcto del planificador más antiguo que aviso_planificador_min', true, 30),
    ('guardiana_ausente', 'error', 'Falta la tarea guardiana',
     'no hay tarea id_tarea_guardiana en el cargador: se crea', true, 40),
    ('guardiana_inactiva', 'error', 'Tarea guardiana inactiva',
     'la guardiana está inactiva en el cargador: se activa', true, 50),
    ('guardiana_ocupada', 'error', 'Id de la tarea guardiana ocupado',
     'id_tarea_guardiana lo ocupa otra tarea: no se crea encima', true, 60),
    ('guardiana_en_otro_id', 'error', 'Guardiana en otro id',
     'hay una tarea como la guardiana en un id distinto de id_tarea_guardiana', true, 70),
    ('ninguna_tarea_activa', 'error', 'Ninguna tarea activa en el cargador',
     'sin tareas activas el cargador carga libremente', true, 80),
    ('carga_sin_tarea', 'error', 'Cargador distinto del plan',
     'el coche carga sin ninguna tarea activa que cubra ese momento durante más de un ciclo de muestreo', true, 90),
    ('tarea_caducada', 'error', 'Tarea propia caducada sin borrar',
     'tarea propia terminada que sigue en el cargador: se repetiría el año que viene', true, 100),
    ('escritura_fallida', 'aviso', 'Escritura fallida en el cargador',
     'escritura o confirmación fallida', true, 110),
    ('salida_no_cubierta', 'aviso', 'El plan no cubre una salida',
     'con el coche enchufado, el plan no llega al objetivo de una salida ni saltando el umbral', true, 120),
    ('tarea_modificada', 'aviso', 'Tarea propia modificada fuera del sistema',
     'tarea propia distinta de como se escribió (¿guardada desde la app?): se corrige', false, 130),
    ('guardiana_modificada', 'aviso', 'Tarea guardiana modificada fuera del sistema',
     'guardiana distinta de 31/12 00:00, 1 minuto: se corrige', false, 140),
    ('tarea_desaparecida', 'aviso', 'Tarea propia borrada fuera del sistema',
     'tarea propia confirmada que ya no está en el cargador', false, 150),
    ('ajena_pisa_franja', 'aviso', 'Tarea ajena que pisa una franja en curso',
     'una tarea activa de la app se solapa con la franja propia en curso', false, 160),
    ('sin_huecos', 'aviso', 'No quedan ids libres en el cargador',
     'ids 1–10 ocupados: alguna franja del plan queda sin escribir', false, 170),
    ('programador_sin_lectura', 'aviso', 'No se pudo leer el programador del cargador',
     'GET del programador fallido: sin lectura no se escribe nada', false, 180),
    ('precios_sin_publicar', 'aviso', 'Precios del día siguiente sin publicar',
     'pasada hora_publicacion_precios + aviso_precios_min faltan precios de mañana', false, 190),
    ('sin_lecturas', 'aviso', 'Sin lecturas',
     'nivel ESTIMADO y más de aviso_sin_lectura_dias sin lecturas', false, 200),
    ('hueco_muestreo', 'aviso', 'Hueco de muestreo',
     'hueco > hueco_muestreo_aviso_s desde el último valor absoluto', false, 210),
    ('intervalo_incoherente', 'aviso', 'Intervalo incoherente', 'E2.5', false, 220),
    ('carga_externa_posible', 'aviso', '¿Cargaste fuera de casa?',
     'residuo de la reconciliación > umbral_carga_externa_kwh', false, 230),
    ('objetivo_tope', 'aviso', 'Objetivo por encima del tope', 'objetivo > tope_pct', false, 240),
    ('carga_completa_dudosa', 'aviso', 'Carga completa dudosa', 'E2.3', false, 250),
    ('calibracion_sugerida', 'info', 'Calibración sugerida', 'E2.6', false, 260);

-- --- 3. Origen de los avisos del planificador ----------------------------------------------------

ALTER TABLE planificador_aviso ADD COLUMN origen text NOT NULL DEFAULT 'reconciliar'
    CHECK (origen IN ('reconciliar', 'muestreo'));

COMMENT ON COLUMN planificador_aviso.origen IS
    'reconciliar: se reemplazan en cada ciclo del reconciliador; muestreo: «Cargador distinto del plan», del muestreo.';

-- --- 4. Avisos nuevos en SQL ---------------------------------------------------------------------

CREATE FUNCTION bateria_avisos_planificador(p_ahora timestamptz)
RETURNS TABLE (clave text, nivel text, titulo text, detalle text, desde timestamptz, regla text, accion text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    c           record;
    x           record;
    v_min       numeric;
    v_local     timestamp;
    v_manana    date;
    v_ini       timestamptz;
    v_fin       timestamptz;
    v_esperados integer;
    v_hay       integer;
BEGIN
    SELECT * INTO c FROM public.planificador_ciclo;
    IF FOUND THEN
        -- Planificador parado: lo comprueba la base de datos, así salta también con el muestreo caído.
        v_min := public.bateria_param('aviso_planificador_min', p_ahora);
        IF c.ultimo_ok_en IS NULL OR p_ahora - c.ultimo_ok_en > v_min * interval '1 minute' THEN
            clave := 'planificador_parado'; nivel := 'error'; titulo := 'Planificador parado';
            detalle := CASE WHEN c.ultimo_ok_en IS NULL THEN 'El planificador no ha completado ningún ciclo.'
                            ELSE format('Último ciclo correcto: %s (más de %s min).',
                                        to_char(c.ultimo_ok_en AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'),
                                        v_min) END;
            desde := c.ultimo_ok_en + v_min * interval '1 minute';
            regla := 'último ciclo correcto más antiguo que aviso_planificador_min'; accion := 'plan';
            RETURN NEXT;
        END IF;

        -- Precios del día siguiente: cuartos de mañana (92, 96 o 100) frente a los que hay.
        v_local := p_ahora AT TIME ZONE 'Europe/Madrid';
        v_min := public.bateria_param('hora_publicacion_precios', p_ahora)
                 + public.bateria_param('aviso_precios_min', p_ahora);
        IF extract(hour FROM v_local) * 60 + extract(minute FROM v_local) >= v_min THEN
            v_manana := v_local::date + 1;
            v_ini := v_manana::timestamp AT TIME ZONE 'Europe/Madrid';
            v_fin := (v_manana + 1)::timestamp AT TIME ZONE 'Europe/Madrid';
            v_esperados := (extract(epoch FROM v_fin - v_ini) / 900)::integer;
            SELECT count(DISTINCT p.inicio) INTO v_hay
            FROM public.v_plan_precio p WHERE p.inicio >= v_ini AND p.inicio < v_fin;
            IF v_hay < v_esperados THEN
                clave := 'precios_sin_publicar:' || v_manana; nivel := 'aviso';
                titulo := 'Precios del día siguiente sin publicar';
                detalle := format('Faltan precios del %s: hay %s de %s cuartos de hora.',
                                  to_char(v_manana, 'DD/MM'), v_hay, v_esperados);
                desde := (v_local::date + v_min * interval '1 minute') AT TIME ZONE 'Europe/Madrid';
                regla := 'pasada hora_publicacion_precios + aviso_precios_min'; accion := 'plan';
                RETURN NEXT;
            END IF;
        END IF;
    END IF;

    -- Salidas que el último plan no cubre, solo con el coche enchufado (con el coche fuera no hay ventana
    -- segura y la salida se planifica al volver). En modo prudente solo se planifica la próxima.
    FOR x IN WITH u AS (
                 SELECT k.id, k.calculado_en, k.prudente, k.conectado FROM public.plan_calculo k
                 WHERE k.ahora <= p_ahora ORDER BY k.ahora DESC, k.id DESC LIMIT 1)
             SELECT s.salida, s.objetivo_kwh, s.nivel_salida_kwh, u.calculado_en
             FROM u JOIN public.plan_salida s ON s.calculo_id = u.id
             WHERE u.conectado AND NOT s.cubierta AND s.salida > p_ahora
               AND (NOT u.prudente OR s.salida = (SELECT min(s2.salida) FROM public.plan_salida s2
                                                 WHERE s2.calculo_id = u.id))
             ORDER BY s.salida LOOP
        clave := 'salida_no_cubierta:' || extract(epoch FROM x.salida)::bigint; nivel := 'aviso';
        titulo := 'El plan no cubre una salida';
        detalle := format('Salida del %s: objetivo %s kWh, el plan llega a %s kWh.',
                          to_char(x.salida AT TIME ZONE 'Europe/Madrid', 'DD/MM HH24:MI'),
                          round(x.objetivo_kwh, 1), coalesce(round(x.nivel_salida_kwh, 1)::text, '?'));
        desde := x.calculado_en; regla := 'el plan no llega al objetivo ni saltando el umbral'; accion := 'plan';
        RETURN NEXT;
    END LOOP;
END
$$;

-- Misma firma: v_aviso y aviso_historial siguen ligados a esta función.
CREATE OR REPLACE FUNCTION bateria_avisos(p_ahora timestamptz)
RETURNS TABLE (clave text, nivel text, titulo text, detalle text, desde timestamptz, regla text, accion text)
LANGUAGE sql STABLE AS $$
    SELECT * FROM public.bateria_avisos_0016(p_ahora)
    UNION ALL
    SELECT a.clave, a.nivel, a.titulo, a.detalle, a.desde, a.regla, 'plan'
    FROM public.planificador_aviso a
    UNION ALL
    SELECT * FROM public.bateria_avisos_planificador(p_ahora);
$$;

-- --- 5. v_aviso con correo ------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_aviso AS
SELECT a.clave, a.nivel, a.titulo, a.detalle, a.desde, a.regla, a.accion, coalesce(t.correo, false) AS correo
FROM bateria_avisos(now()) a
LEFT JOIN aviso_tipo t ON t.tipo = split_part(a.clave, ':', 1);

-- --- 6. Permisos -----------------------------------------------------------------------------------

GRANT SELECT ON aviso_tipo TO grafana_ro, formulario_rw;
