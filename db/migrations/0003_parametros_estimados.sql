-- Componentes de la fórmula del contrato sin serie propia todavía
-- (SSAA, pagos por capacidad, ROS, ROM, pérdidas, FNEE y tasa municipal).
--
-- Feníe no los desglosa en la factura. Hasta tener el token de ESIOS se cargan
-- como parámetros estimados (estimado = true):
--   * Si hay un valor regulado oficial, se usa ese valor con su vigencia
--     legal; se marca como estimado porque la comercializadora puede
--     trasladarlo de otra forma (p. ej., pérdidas ajustadas horarias).
--   * SSAA no tiene valor regulado: estimación mensual (vigencia por mes).
--   * Si no hay ni valor ni estimación, el valor queda a NULL (pendiente) y
--     la vista no calcula el precio final.
--
-- Para corregir un valor: cerrar la vigencia de la fila actual y añadir una
-- nueva fila desde la fecha correspondiente (nunca editar el valor histórico).

INSERT INTO contrato_parametro (contrato, clave, periodo, vigencia, valor, estimado, fuente, nota) VALUES

-- Pérdidas: coeficientes estándar del peaje 2.0TD.
-- Fuente: Circular CNMC 3/2020, art. 11.1 (BOE-A-2020-1066, texto consolidado):
--   2.0TD  P1 16,7 %  P2 16,3 %  P3 18,0 %.
-- En vigor desde la aplicación de los peajes de la Circular (01/06/2021).
-- En la liquidación real REE aplica coeficientes ajustados por hora.
    ('vivienda', 'perdidas', 'P1', '[2021-06-01,)', 0.167, true,
     'Circular CNMC 3/2020, art. 11.1 (BOE-A-2020-1066)',
     'Coeficiente estándar; la liquidación usa coeficientes ajustados horarios (pendiente ESIOS)'),
    ('vivienda', 'perdidas', 'P2', '[2021-06-01,)', 0.163, true,
     'Circular CNMC 3/2020, art. 11.1 (BOE-A-2020-1066)',
     'Coeficiente estándar; la liquidación usa coeficientes ajustados horarios (pendiente ESIOS)'),
    ('vivienda', 'perdidas', 'P3', '[2021-06-01,)', 0.180, true,
     'Circular CNMC 3/2020, art. 11.1 (BOE-A-2020-1066)',
     'Coeficiente estándar; la liquidación usa coeficientes ajustados horarios (pendiente ESIOS)'),

-- Pagos por capacidad, término de energía (€/kWh en barras de central).
-- Fuente: Orden TED/1524/2025 (BOE-A-2025-26705), apartado tercero, segmento
-- tarifario 1 (el del 2.0TD: sus cargos de energía coinciden con la factura):
--   P1 0,000800  P2 0,000133  P3 sin valor en la tabla (se toma 0).
-- En vigor desde el 01/01/2026 (apartado undécimo).
    ('vivienda', 'pagos_capacidad', 'P1', '[2026-01-01,)', 0.000800, true,
     'Orden TED/1524/2025, ap. tercero, segmento 1 (BOE-A-2025-26705)', NULL),
    ('vivienda', 'pagos_capacidad', 'P2', '[2026-01-01,)', 0.000133, true,
     'Orden TED/1524/2025, ap. tercero, segmento 1 (BOE-A-2025-26705)', NULL),
    ('vivienda', 'pagos_capacidad', 'P3', '[2026-01-01,)', 0, true,
     'Orden TED/1524/2025, ap. tercero, segmento 1 (BOE-A-2025-26705)',
     'Celda vacía en la tabla de la orden para el periodo 3'),

-- Retribución del operador del mercado (OMIE).
-- Fuente: Orden TED/1524/2025 (BOE-A-2025-26705): los comercializadores
-- pagan al Operador del Mercado 0,04070 €/MWh del programa horario final.
-- En vigor desde el 01/01/2026.
    ('vivienda', 'rom', '*', '[2026-01-01,)', 0.04070, true,
     'Orden TED/1524/2025 (BOE-A-2025-26705)', NULL),

-- Retribución del operador del sistema (REE): cuota variable que pagan los
-- comercializadores.
-- Fuente: CNMC, memoria de la resolución de retribución del operador del
-- sistema 2026 (RAP/DE/004/25, trámite de audiencia), ap. 7.1: "la cuota
-- variable que se establece en esta resolución asciende a 0,18285 €/MWh".
-- Estimado: es el texto sometido a audiencia, no la resolución definitiva.
    ('vivienda', 'ros', '*', '[2026-01-01,)', 0.18285, true,
     'CNMC RAP/DE/004/25, memoria (trámite de audiencia), ap. 7.1',
     'Cuota variable propuesta; sustituir por la de la resolución definitiva'),

-- Servicios de ajuste: valor mensual estimado.
-- Sin valor regulado (coste horario de mercado). Hasta tener la serie de
-- ESIOS (indicadores por verificar en su documentación) se carga una
-- estimación mensual. Fuera de los meses cargados, el precio final es NULL:
-- añadir cada mes una fila nueva.
    ('vivienda', 'ssaa', '*', '[2026-10-01,2026-11-01)', 31.5, true,
     'Estimación a partir de referencias de prensa sobre el coste de los servicios de ajuste',
     'Aproximado; sustituir por la serie de ESIOS'),
    ('vivienda', 'ssaa', '*', '[2026-11-01,2026-12-01)', 31.5, true,
     'Estimación a partir de referencias de prensa sobre el coste de los servicios de ajuste',
     'Aproximado; sustituir por la serie de ESIOS'),

-- FNEE: se usa el valor regulado para el PVPC como estimación.
-- Fuente: Orden TED/133/2026 (BOE-A-2026-4522, BOE 27/02/2026), apartado
-- cuarto: 0,0024374639 €/kWh = 2,4374639 €/MWh. La orden no fija fecha de
-- entrada en vigor, por lo que rige a los 20 días de su publicación (art. 2.1
-- del Código Civil): 19/03/2026. Antes rige el valor de 2025, no localizado.
    ('vivienda', 'fnee', '*', '[2026-01-01,2026-03-19)', NULL, true,
     'Pendiente: valor de 2025 (Orden TED anual de obligaciones de ahorro 2025)',
     'Valor anterior a la Orden TED/133/2026 no localizado'),
    ('vivienda', 'fnee', '*', '[2026-03-19,)', 2.4374639, true,
     'Orden TED/133/2026, ap. cuarto (BOE-A-2026-4522)',
     'Valor del PVPC; Feníe puede repercutir otro importe'),

-- Tasa municipal.
-- Fuente: Real Decreto Legislativo 2/2004 (TRLRHL), art. 24.1.c): 1,5 % de
-- los ingresos brutos de facturación de las empresas suministradoras.
-- Vigencia alineada con el resto de parámetros del contrato.
    ('vivienda', 'tasa_municipal', '*', '[2026-01-01,)', 0.015, true,
     'RDL 2/2004 (TRLRHL), art. 24.1.c) (BOE-A-2004-4214)',
     'Tipo legal; confirmar con Feníe cómo lo aplica en la cláusula 4.2');
