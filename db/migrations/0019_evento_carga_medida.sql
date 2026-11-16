-- Energía medida en los eventos de carga de Inicio (v0.5.8).
--
-- En «Últimos eventos», la carga mostraba la energía a batería (energía del
-- cargador × factor_bateria: 18,26 kWh × 0,84 = 15,3 kWh) sin decirlo. Inicio
-- pasa a mostrar primero la energía medida y entre paréntesis la estimada a
-- batería: «carga 18,3 kWh (15,3 a batería)»; igual en las cargas externas,
-- con los kWh del cargador externo: «carga externa 30,0 kWh (25,8 a batería)».
--
-- v_bateria_evento_reciente gana medido_kwh (al final, para conservar las
-- columnas y los permisos): energia_wh / 1000 en las cargas de casa, kwh en las
-- externas (NULL si se desconoce) y NULL en el resto. valor (a batería) no
-- cambia. El resto de la vista, como en 0018.

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
WHERE t.llegada IS NOT NULL
UNION ALL
SELECT min(e.detectado_en), 'inicio_carga', NULL, NULL, false, true, NULL, NULL
FROM cargador_estado e
WHERE e.estado IN (5, 6)
  AND e.detectado_en > coalesce((SELECT max(s.fin) FROM cargador_sesion s), '-infinity')
  AND (SELECT u.estado FROM cargador_estado u ORDER BY u.detectado_en DESC LIMIT 1) IN (5, 6)
HAVING min(e.detectado_en) IS NOT NULL;

COMMENT ON COLUMN v_bateria_evento_reciente.medido_kwh IS
    'Cargas: energía medida (cargador de casa: energia_wh / 1000; externa: kwh); valor es la estimada a batería (0019).';
