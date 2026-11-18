-- Planificador, paso 3b (v0.6.1): ahorro por periodo con la conexión abierta y
-- franjas pasadas del plan efectivo para la pantalla Plan de carga (7.9 de
-- docs/formulario-maquetas.md). La 0020 ya está desplegada: no se toca.
--
-- 1. plan_ahorro(desde, hasta): lo de v_plan_ahorro (E3.8), con la conexión
--    abierta hasta la última muestra (columna abierta) y solo las conexiones
--    cuyo fin (o última muestra) cae en [desde, hasta). v_plan_ahorro pasa a
--    ser plan_ahorro sin límites, con la columna abierta añadida al final.
-- 2. plan_franjas_pasadas(desde, hasta): el plan efectivo ya terminado,
--    agrupado en franjas (cuartos contiguos), con su desglose por nivel, la
--    energía real del cargador en su horario y su coste.
-- 3. bateria_parametro_clave.nota: la nota de una línea de la pantalla
--    Parámetros.

-- --- 1. Ahorro ---------------------------------------------------------------------------------

CREATE FUNCTION plan_ahorro(p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (inicio timestamptz, fin timestamptz, abierta boolean, fecha date, energia_cargador_kwh numeric,
               energia_kwh numeric, plan_kwh numeric, plan_cubre boolean, referencia_incompleta boolean,
               plan_propio_kwh numeric, resto_kwh numeric, coste_real_eur numeric, coste_plan_eur numeric,
               coste_plan_propio_eur numeric, coste_resto_eur numeric, coste_referencia_eur numeric,
               ahorro_plan_eur numeric, ahorro_real_eur numeric, sin_precio boolean)
LANGUAGE sql STABLE AS $$
WITH primero AS (
    SELECT min(ahora) AS t FROM public.plan_calculo
), ultima AS (
    SELECT max(m.instante) AS t FROM public.cargador_muestra m
), con AS (
    SELECT c.inicio, coalesce(c.fin, ultima.t) AS fin, c.fin IS NULL AS abierta
    FROM primero, ultima, public.plan_conexiones(primero.t, now()) c
    WHERE primero.t IS NOT NULL AND c.inicio >= primero.t
      AND coalesce(c.fin, ultima.t) > c.inicio
      AND coalesce(c.fin, ultima.t) >= p_desde AND coalesce(c.fin, ultima.t) < p_hasta
), pot AS (
    SELECT con.inicio AS con_inicio, ph.hora, ph.potencia_kw
    FROM con CROSS JOIN LATERAL public.plan_potencia_hora(con.inicio) ph
), q AS (
    SELECT con.inicio AS con_inicio, con.fin AS con_fin, con.abierta, g.q,
           extract(epoch FROM least(g.q + interval '15 minutes', con.fin) - greatest(g.q, con.inicio)) / 3600
               AS horas
    FROM con
    CROSS JOIN LATERAL generate_series(date_bin('15 minutes', con.inicio, TIMESTAMPTZ '2000-01-01 00:00:00+00'),
                                       con.fin - interval '1 microsecond', interval '15 minutes') g (q)
), d AS (
    SELECT q.con_inicio, q.con_fin, q.abierta, q.q,
           coalesce(et.coche_wh, 0) / 1000 AS e_kwh,
           pe.kwh_cargador * q.horas / 0.25 AS plan_cap,
           pot.potencia_kw * q.horas AS libre_cap,
           p.precio_final_eur_kwh AS precio,
           fc.valor AS factor
    FROM q
    LEFT JOIN public.energia_tramo et ON et.inicio = q.q
    LEFT JOIN public.v_plan_efectivo pe ON pe.inicio = q.q
    JOIN pot ON pot.con_inicio = q.con_inicio
            AND pot.hora = extract(hour FROM q.q AT TIME ZONE 'Europe/Madrid')::integer
    LEFT JOIN public.v_plan_precio p ON p.inicio = q.q
    LEFT JOIN public.cargador_parametro fc
      ON fc.clave = 'factor_contador' AND fc.vigencia @> (q.q AT TIME ZONE 'Europe/Madrid')::date
), acum AS (
    SELECT d.*,
           sum(d.e_kwh) OVER (PARTITION BY d.con_inicio) AS e_total,
           coalesce(d.plan_cap, d.libre_cap) AS cap_plan,
           sum(coalesce(d.plan_cap, d.libre_cap))
               OVER (PARTITION BY d.con_inicio
                     ORDER BY d.plan_cap IS NULL, CASE WHEN d.plan_cap IS NULL THEN d.precio END, d.q) AS cum_plan,
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
       r.abierta,
       (r.con_inicio AT TIME ZONE 'Europe/Madrid')::date AS fecha,
       r.energia_cargador_kwh,
       r.energia_kwh, r.plan_kwh, r.plan_kwh >= r.energia_cargador_kwh AS plan_cubre,
       r.referencia_kwh < r.energia_cargador_kwh AS referencia_incompleta,
       r.plan_propio_kwh, r.resto_kwh,
       r.coste_real_eur, r.coste_plan_eur, r.coste_plan_propio_eur, r.coste_resto_eur, r.coste_referencia_eur,
       r.coste_referencia_eur - r.coste_plan_eur AS ahorro_plan_eur,
       r.coste_referencia_eur - r.coste_real_eur AS ahorro_real_eur,
       r.sin_precio
FROM (
    SELECT x.con_inicio, x.con_fin, x.abierta,
           max(x.e_total) AS energia_cargador_kwh,
           coalesce(sum(x.plan_cap), 0) AS plan_kwh,
           coalesce(sum(x.e_plan) FILTER (WHERE x.plan_cap IS NOT NULL), 0) AS plan_propio_kwh,
           coalesce(sum(x.e_plan) FILTER (WHERE x.plan_cap IS NULL), 0) AS resto_kwh,
           sum(x.e_ref) AS referencia_kwh,
           bool_or(x.precio IS NULL OR x.factor IS NULL) AS sin_precio,
           CASE WHEN bool_and(x.factor IS NOT NULL) THEN sum(x.e_kwh * x.factor) END AS energia_kwh,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               sum(x.e_kwh * x.factor * x.precio) END AS coste_real_eur,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               sum(x.e_plan * x.factor * x.precio) END AS coste_plan_eur,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               coalesce(sum(x.e_plan * x.factor * x.precio) FILTER (WHERE x.plan_cap IS NOT NULL), 0)
           END AS coste_plan_propio_eur,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               coalesce(sum(x.e_plan * x.factor * x.precio) FILTER (WHERE x.plan_cap IS NULL), 0)
           END AS coste_resto_eur,
           CASE WHEN bool_and(x.precio IS NOT NULL AND x.factor IS NOT NULL) THEN
               sum(x.e_ref * x.factor * x.precio) END AS coste_referencia_eur
    FROM reparto x
    GROUP BY x.con_inicio, x.con_fin, x.abierta
) r
$$;

COMMENT ON FUNCTION plan_ahorro(timestamptz, timestamptz) IS
    'E3.8 por conexión con fin (o última muestra, si sigue abierta) en [desde, hasta). Ver v_plan_ahorro.';

-- Mismas columnas que en la 0020 y abierta al final (CREATE OR REPLACE solo admite añadir columnas al final).
CREATE OR REPLACE VIEW v_plan_ahorro AS
SELECT a.inicio, a.fin, a.fecha, a.energia_cargador_kwh, a.energia_kwh, a.plan_kwh, a.plan_cubre,
       a.referencia_incompleta, a.plan_propio_kwh, a.resto_kwh, a.coste_real_eur, a.coste_plan_eur,
       a.coste_plan_propio_eur, a.coste_resto_eur, a.coste_referencia_eur, a.ahorro_plan_eur, a.ahorro_real_eur,
       a.sin_precio, a.abierta
FROM plan_ahorro('-infinity', 'infinity') a;

COMMENT ON VIEW v_plan_ahorro IS
    'E3.8 por conexión: coste real, del plan efectivo y de cargar al enchufar, con la misma energía. '
    'coste_plan = plan propio + resto (energía real que el plan no tenía, en los cuartos libres más baratos). '
    'abierta: conexión aún abierta, hasta la última muestra. sin_precio: costes sin valor.';

-- --- 2. Franjas pasadas -----------------------------------------------------------------------

-- Franjas del plan efectivo (cuartos contiguos) que empiezan en desde o
-- después y han terminado en hasta. Las islas se forman sin cortar en los
-- límites, para que una franja aún en curso no parezca terminada. Energía real: la del
-- contador del cargador (energia_tramo) en el horario de la franja; coste real
-- = kWh × factor_contador × precio final, como v_coste_tramo. modo: el del
-- cálculo que fijó la franja (sombra: no se escribió en el cargador).
CREATE FUNCTION plan_franjas_pasadas(p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (inicio timestamptz, fin timestamptz, modo text, plan_kwh numeric, necesaria_kwh numeric,
               margen_kwh numeric, oportunista_kwh numeric, salidas_necesaria timestamptz[], provisional boolean,
               real_kwh numeric, coste_real_eur numeric, conectado boolean)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT e.inicio, e.nivel, e.salida, e.kwh_cargador, e.provisional, c.modo,
               e.inicio - (row_number() OVER (ORDER BY e.inicio)) * interval '15 minutes' AS isla
        FROM public.v_plan_efectivo e
        JOIN public.plan_calculo c ON c.id = e.calculo_id
        WHERE e.inicio >= p_desde - interval '1 day'
    ), f AS (
        SELECT min(q.inicio) AS inicio, max(q.inicio) + interval '15 minutes' AS fin, max(q.modo) AS modo,
               sum(q.kwh_cargador) AS plan_kwh,
               coalesce(sum(q.kwh_cargador) FILTER (WHERE q.nivel = 'necesaria'), 0) AS necesaria_kwh,
               coalesce(sum(q.kwh_cargador) FILTER (WHERE q.nivel = 'margen'), 0) AS margen_kwh,
               coalesce(sum(q.kwh_cargador) FILTER (WHERE q.nivel = 'oportunista'), 0) AS oportunista_kwh,
               array_agg(DISTINCT q.salida ORDER BY q.salida) FILTER (WHERE q.nivel = 'necesaria') AS salidas,
               bool_or(q.provisional) AS provisional
        FROM q
        GROUP BY q.isla
    )
    SELECT f.inicio, f.fin, f.modo, f.plan_kwh, f.necesaria_kwh, f.margen_kwh, f.oportunista_kwh, f.salidas,
           f.provisional, coalesce(r.kwh, 0),
           CASE WHEN r.sin_precio THEN NULL ELSE coalesce(r.eur, 0) END,
           EXISTS (SELECT 1 FROM public.plan_conexiones(f.inicio, f.fin))
    FROM f
    LEFT JOIN LATERAL (
        SELECT sum(t.coche_wh) / 1000 AS kwh,
               sum(t.coche_wh / 1000 * fc.valor * p.precio_final_eur_kwh) AS eur,
               bool_or(t.coche_wh > 0 AND (fc.valor IS NULL OR p.precio_final_eur_kwh IS NULL)) AS sin_precio
        FROM public.energia_tramo t
        LEFT JOIN public.v_plan_precio p ON p.inicio = t.inicio
        LEFT JOIN public.cargador_parametro fc
          ON fc.clave = 'factor_contador' AND fc.vigencia @> (t.inicio AT TIME ZONE 'Europe/Madrid')::date
        WHERE t.inicio >= f.inicio AND t.inicio < f.fin
    ) r ON true
    WHERE f.inicio >= p_desde AND f.fin <= p_hasta
    ORDER BY f.inicio;
$$;

COMMENT ON FUNCTION plan_franjas_pasadas(timestamptz, timestamptz) IS
    'Plan efectivo terminado, por franjas: kWh planificados por nivel, kWh reales y coste real en su horario.';

-- --- 3. Notas de la pantalla Parámetros --------------------------------------------------------

-- Nota de una línea bajo el nombre (7.5, maqueta 05). Tres se calculan al
-- pintar la página (umbral de hoy, historial actual, huecos para la app);
-- aquí queda su texto sin cifras.
ALTER TABLE bateria_parametro_clave ADD COLUMN nota text;

COMMENT ON COLUMN bateria_parametro_clave.nota IS
    'Nota de una línea bajo el nombre en la pantalla Parámetros (0021); NULL: sin nota.';

UPDATE bateria_parametro_clave k SET nota = v.nota
FROM (VALUES
    ('observaciones_calibracion', 'cargas necesarias para sugerir la eficiencia'),
    ('margen_carga_pct', 'sobre la energía necesaria; solo bajo el umbral'),
    ('umbral_oportunista_pct', 'de la media del precio de los últimos días'),
    ('ventana_umbral_dias', 'días completos para la media del umbral'),
    ('nivel_oportunista_pct', 'nunca por encima del tope'),
    ('ventana_precio_esperado_dias', 'para los cuartos sin precio publicado'),
    ('historial_min_disponibilidad_dias', 'con menos días, modo prudente'),
    ('potencia_carga_inicial_kw', 'solo sin sesiones medidas'),
    ('ventana_potencia_dias', 'mediana por hora local'),
    ('max_tareas_sistema', 'más la guardiana; el resto, para la app'),
    ('horizonte_plan_h', 'o hasta la última salida conocida'),
    ('hora_publicacion_precios', 'precios de D+1 publicados desde esta hora de D'),
    ('aviso_planificador_min', 'correo desde el NAS')
) AS v (clave, nota)
WHERE k.clave = v.clave;

-- --- 4. Permisos ----------------------------------------------------------------------------

GRANT EXECUTE ON FUNCTION plan_ahorro(timestamptz, timestamptz), plan_franjas_pasadas(timestamptz, timestamptz)
    TO grafana_ro, formulario_rw;
GRANT SELECT ON v_plan_ahorro TO grafana_ro, formulario_rw;
