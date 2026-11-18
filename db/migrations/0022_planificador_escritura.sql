-- Planificador, paso 3c (v0.7.0): escritura en el cargador ("Plan del paso 3c" de docs/fase2.md).
--
-- 1. congelar_franja_min (30): franjas que empiezan antes no se cambian.
-- 2. planificador_tarea: tareas propias del cargador (guardiana y franjas).
--    Se registran como pendientes antes del POST.
-- 3. plan_manual (franjas fijadas), plan_bloqueo (tramos detenidos o
--    eliminados) y plan_accion: cola de acciones del formulario; NOTIFY
--    planificador solo despierta al muestreo, que la procesa.
-- 4. planificador_lectura (última lectura del programador) y plan_auditoria
--    (cada escritura, real o en seco).
-- 5. planificador_aviso: avisos críticos del reconciliador; bateria_avisos()
--    los une a los de la 0016 (v_aviso, aviso_historial y el formulario los
--    recogen sin más cambios).

-- --- 1. Parámetro ---------------------------------------------------------------------------

INSERT INTO bateria_parametro_clave (clave, unidad, nombre, descripcion, efecto, minimo, maximo, grupo, orden, nota)
VALUES ('congelar_franja_min', 'min', 'Congelar franjas',
        'Una franja propia que empieza dentro de este tiempo ya no se cambia ni se borra: se respeta como la franja en curso.',
        'Evita cambios de última hora en el cargador. Con 0 se puede cambiar hasta que empieza.',
        0, 120, 6, 9, 'evita cambios de última hora');

INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente) VALUES
    ('congelar_franja_min', '[2025-01-01,)', 30, 'usuario', 'Plan del paso 3c (18/11/2026)');

-- CHECK de la 0020 con la clave nueva (minutos enteros, 0 admitido).
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
    WHEN clave IN ('margen_salida_min', 'congelar_franja_min') THEN valor >= 0 AND valor = trunc(valor)
    WHEN clave = 'margen_carga_pct'        THEN valor >= 0
    WHEN clave = 'desconexion_min_s'       THEN valor >= 0
    WHEN clave = 'hueco_muestreo_aviso_s'  THEN valor >= 180
    WHEN clave = 'hora_publicacion_precios' THEN valor >= 0 AND valor < 1440 AND valor = trunc(valor)
    ELSE valor > 0
END);

-- --- 2. Tareas propias ------------------------------------------------------------------------

-- Una fila por tarea propia en el cargador. Se inserta como pendiente antes
-- del POST; tras el GET de confirmación pasa a confirmada (o error, con el
-- motivo). Al borrarla del cargador se borra la fila (el rastro queda en
-- plan_auditoria). fecha, hour_min y duracion: lo que se escribió.
CREATE TABLE planificador_tarea (
    id_tarea        smallint PRIMARY KEY CHECK (id_tarea BETWEEN 1 AND 10),
    papel           text        NOT NULL CHECK (papel IN ('guardiana', 'franja')),
    origen          text        NOT NULL CHECK (origen IN ('automatica', 'manual', 'guardiana')),
    fecha           date        NOT NULL,
    hour_min        smallint    NOT NULL CHECK (hour_min BETWEEN 0 AND 1439),
    duracion        smallint    NOT NULL CHECK (duracion BETWEEN 1 AND 1440),
    activa          boolean     NOT NULL,
    franja_inicio   timestamptz,
    franja_fin      timestamptz,
    estado          text        NOT NULL CHECK (estado IN ('pendiente', 'confirmada', 'error')),
    error           text,
    alta_en         timestamptz NOT NULL DEFAULT now(),
    actualizada_en  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT planificador_tarea_dentro_del_dia CHECK (hour_min + duracion <= 1440)
);

CREATE UNIQUE INDEX planificador_tarea_una_guardiana ON planificador_tarea (papel) WHERE papel = 'guardiana';

COMMENT ON TABLE planificador_tarea IS
    'Tareas propias del cargador (E3.6): las demás son ajenas y nunca se tocan. Pendiente se registra antes del POST.';

-- --- 3. Franjas manuales, bloqueos y cola de acciones -------------------------------------------

CREATE TABLE plan_manual (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inicio      timestamptz NOT NULL,
    fin         timestamptz NOT NULL,
    origen      text        NOT NULL CHECK (origen IN ('manual', 'cargar_ahora')),
    nota        text,
    alta_en     timestamptz NOT NULL DEFAULT now(),
    anulada_en  timestamptz,
    CONSTRAINT plan_manual_intervalo CHECK (fin > inicio),
    CONSTRAINT plan_manual_cuartos CHECK (
        inicio = date_bin('15 minutes', inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00')
        AND fin = date_bin('15 minutes', fin, TIMESTAMPTZ '2000-01-01 00:00:00+00') OR origen = 'cargar_ahora')
);

COMMENT ON TABLE plan_manual IS 'Franjas fijadas (E3.5): el planificador cuenta su energía y no las mueve ni las borra.';

CREATE TABLE plan_bloqueo (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inicio      timestamptz NOT NULL,
    fin         timestamptz NOT NULL,
    motivo      text        NOT NULL CHECK (motivo IN ('detenida', 'eliminada')),
    alta_en     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_bloqueo_intervalo CHECK (fin > inicio)
);

COMMENT ON TABLE plan_bloqueo IS 'Tramos donde el planificador no vuelve a crear una franja detenida o eliminada (E3.5).';

CREATE TABLE plan_accion (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo            text        NOT NULL CHECK (tipo IN ('modo', 'cargar_ahora', 'detener', 'modificar', 'eliminar',
                                                         'anadir')),
    datos           jsonb       NOT NULL DEFAULT '{}',
    solicitada_en   timestamptz NOT NULL DEFAULT now(),
    estado          text        NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'hecha', 'error')),
    intentos        integer     NOT NULL DEFAULT 0,
    procesada_en    timestamptz,
    resultado       text
);

CREATE INDEX plan_accion_pendientes ON plan_accion (id) WHERE estado = 'pendiente';

COMMENT ON TABLE plan_accion IS
    'Cola de acciones del formulario. La tabla es la cola; NOTIFY planificador solo despierta al muestreo.';

CREATE FUNCTION plan_accion_avisar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('planificador', NEW.id::text);
    RETURN NEW;
END
$$;

CREATE TRIGGER plan_accion_avisar AFTER INSERT ON plan_accion
    FOR EACH ROW EXECUTE FUNCTION plan_accion_avisar();

-- --- 4. Lectura del programador y auditoría ------------------------------------------------------

CREATE TABLE planificador_lectura (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),
    leida_en    timestamptz NOT NULL,
    ok          boolean     NOT NULL,
    tareas      jsonb,
    error       text
);

COMMENT ON TABLE planificador_lectura IS 'Última lectura del programador (GET ...?level=cfg): tareas tal cual o el error.';

CREATE TABLE plan_auditoria (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instante    timestamptz NOT NULL DEFAULT now(),
    metodo      text        NOT NULL CHECK (metodo IN ('POST', 'PUT', 'DELETE')),
    id_tarea    smallint    NOT NULL,
    cuerpo      jsonb       NOT NULL,
    en_seco     boolean     NOT NULL,
    resultado   text        NOT NULL CHECK (resultado IN ('ok', 'error', 'en_seco')),
    respuesta   jsonb,
    motivo      text        NOT NULL,
    accion_id   bigint REFERENCES plan_accion (id) ON DELETE SET NULL
);

CREATE INDEX plan_auditoria_instante ON plan_auditoria (instante);

COMMENT ON TABLE plan_auditoria IS
    'Cada escritura en el cargador (E3.6): real (ok o error, con la respuesta) o en seco (sombra: lo que haría).';

-- --- 5. Avisos ------------------------------------------------------------------------------------

-- Los del último ciclo del reconciliador (se reemplazan en cada ciclo).
CREATE TABLE planificador_aviso (
    clave       text PRIMARY KEY,
    nivel       text        NOT NULL CHECK (nivel IN ('error', 'aviso', 'info')),
    titulo      text        NOT NULL,
    detalle     text,
    desde       timestamptz NOT NULL,
    regla       text        NOT NULL
);

ALTER FUNCTION bateria_avisos(timestamptz) RENAME TO bateria_avisos_0016;

CREATE FUNCTION bateria_avisos(p_ahora timestamptz)
RETURNS TABLE (clave text, nivel text, titulo text, detalle text, desde timestamptz, regla text, accion text)
LANGUAGE sql STABLE AS $$
    SELECT * FROM public.bateria_avisos_0016(p_ahora)
    UNION ALL
    SELECT a.clave, a.nivel, a.titulo, a.detalle, a.desde, a.regla, 'plan'
    FROM public.planificador_aviso a;
$$;

-- La vista ligaba la función por su oid: se rehace con la nueva.
CREATE OR REPLACE VIEW v_aviso AS SELECT * FROM bateria_avisos(now());

-- --- 6. Permisos -----------------------------------------------------------------------------------

GRANT SELECT ON planificador_tarea, plan_manual, plan_bloqueo, plan_accion, planificador_lectura, plan_auditoria,
                planificador_aviso, v_aviso TO grafana_ro, formulario_rw;
-- El formulario solo encola acciones: el muestreo es el único que escribe en el cargador y en estas tablas.
GRANT INSERT ON plan_accion TO formulario_rw;
