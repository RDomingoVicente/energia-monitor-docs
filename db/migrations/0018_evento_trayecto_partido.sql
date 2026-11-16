-- Trayectos partidos por una lectura en los últimos eventos de Inicio (v0.5.7).
--
-- En el NAS, el trayecto del 15/11 (08:59 a 18:00) quedó partido por la
-- primera lectura del sistema (16:29, con el coche fuera). El tramo anterior
-- no puede reconciliarse (no hay valor absoluto antes) y el posterior sí
-- (10,96 kWh reales), así que el trayecto sigue sin reconciliar, con un
-- consumo de 31,74 kWh que mezcla lo estimado y lo real. Inicio lo mostraba
-- como «trayecto est. −31,7 kWh», sin decir que 11,0 kWh son reales.
--
-- v_bateria_evento_reciente gana real_kwh (al final, para conservar las
-- columnas y los permisos): en los trayectos, la suma (negativa, como valor)
-- de los tramos cuyo intervalo quedó reconciliado; NULL si no hay ninguno.
-- El intervalo de un tramo es el que termina en el siguiente valor absoluto
-- por n en bateria_nivel (no por instante: el tramo y la lectura que lo corta
-- comparten instante y el tramo va antes). valor (el total) y estimado («est.»
-- mientras quede algún tramo sin reconciliar) no cambian; bateria_trayecto y
-- la estadística (mediana, P75), tampoco.

CREATE OR REPLACE VIEW v_bateria_evento_reciente AS
SELECT l.instante, 'lectura'::text AS tipo, l.pct AS valor, '%'::text AS unidad, false AS estimado,
       false AS en_curso, NULL::numeric AS real_kwh
FROM bateria_lectura l
WHERE l.anulada_en IS NULL
UNION ALL
SELECT s.fin, 'carga', s.energia_wh / 1000 * s.factor, 'kWh', false, false, NULL
FROM bateria_sesion s
UNION ALL
SELECT x.instante, 'carga_externa', x.kwh * bateria_param('factor_bateria', x.instante), 'kWh', false, false, NULL
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
        WHERE b.tipo = 'trayecto' AND b.conector = t.conector AND b.trayecto_salida = t.salida)
FROM bateria_trayecto t
WHERE t.llegada IS NOT NULL AND NOT t.corto
UNION ALL
SELECT t.llegada, 'conexion', NULL, NULL, false, false, NULL
FROM bateria_trayecto t
WHERE t.llegada IS NOT NULL
UNION ALL
SELECT min(e.detectado_en), 'inicio_carga', NULL, NULL, false, true, NULL
FROM cargador_estado e
WHERE e.estado IN (5, 6)
  AND e.detectado_en > coalesce((SELECT max(s.fin) FROM cargador_sesion s), '-infinity')
  AND (SELECT u.estado FROM cargador_estado u ORDER BY u.detectado_en DESC LIMIT 1) IN (5, 6)
HAVING min(e.detectado_en) IS NOT NULL;

COMMENT ON COLUMN v_bateria_evento_reciente.real_kwh IS
    'Trayectos: suma (negativa) de los tramos en intervalos reconciliados; NULL si ninguno (0018).';
