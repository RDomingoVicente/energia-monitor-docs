-- Contratos de suministro y sus parámetros con vigencia temporal.
--
-- Ningún parámetro tarifario se codifica en el colector ni en las vistas: todo
-- sale de contrato_parametro, con vigencia daterange '[desde, hasta)'.
-- periodo = '*' significa "todos los periodos"; NULL no se usa porque no
-- colisiona en la restricción de exclusión.

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE contrato (
    id              text PRIMARY KEY,
    descripcion     text NOT NULL,
    tarifa_acceso   text NOT NULL CHECK (tarifa_acceso IN ('2.0TD'))
);

-- Catálogo de parámetros: fija la unidad de cada clave y si va por periodo.
CREATE TABLE parametro_clave (
    clave           text PRIMARY KEY,
    unidad          text    NOT NULL CHECK (unidad IN ('EUR/MWh', 'EUR/kWh', 'fraccion')),
    por_periodo     boolean NOT NULL,
    descripcion     text    NOT NULL
);

INSERT INTO parametro_clave (clave, unidad, por_periodo, descripcion) VALUES
    ('rfe',                'EUR/MWh',  false, 'Margen de la comercializadora (RFE del contrato)'),
    ('peaje',              'EUR/kWh',  true,  'Peaje de transporte y distribución, término de energía'),
    ('cargo',              'EUR/kWh',  true,  'Cargo del sistema, término de energía'),
    ('impuesto_electrico', 'fraccion', false, 'Impuesto especial sobre la electricidad'),
    ('iva',                'fraccion', false, 'IVA'),
    ('ssaa',               'EUR/MWh',  false, 'Servicios de ajuste'),
    ('pagos_capacidad',    'EUR/kWh',  true,  'Pagos por capacidad, término de energía'),
    ('ros',                'EUR/MWh',  false, 'Retribución del operador del sistema'),
    ('rom',                'EUR/MWh',  false, 'Retribución del operador del mercado'),
    ('perdidas',           'fraccion', true,  'Coeficiente de pérdidas'),
    ('fnee',               'EUR/MWh',  false, 'Aportación al Fondo Nacional de Eficiencia Energética'),
    ('tasa_municipal',     'fraccion', false, 'Tasa municipal');

CREATE TABLE contrato_parametro (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contrato        text      NOT NULL REFERENCES contrato (id),
    clave           text      NOT NULL REFERENCES parametro_clave (clave),
    periodo         text      NOT NULL CHECK (periodo IN ('*', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6')),
    vigencia        daterange NOT NULL,
    -- NULL = valor pendiente (sin fuente). Solo se admite en parámetros estimados.
    valor           numeric,
    estimado        boolean   NOT NULL DEFAULT false,
    fuente          text      NOT NULL,
    nota            text,
    CONSTRAINT contrato_parametro_vigencia_valida
        CHECK (NOT isempty(vigencia) AND NOT lower_inf(vigencia)
               AND lower_inc(vigencia) AND NOT upper_inc(vigencia)),
    CONSTRAINT contrato_parametro_pendiente_estimado
        CHECK (valor IS NOT NULL OR estimado),
    CONSTRAINT contrato_parametro_sin_solape
        EXCLUDE USING gist (contrato WITH =, clave WITH =, periodo WITH =, vigencia WITH &&)
);

-- La clave decide si se admite '*' o hace falta un periodo concreto. Así no
-- pueden coexistir '*' y 'P1' para la misma clave y fecha.
CREATE FUNCTION contrato_parametro_comprobar_periodo() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_por_periodo boolean;
BEGIN
    SELECT por_periodo INTO v_por_periodo FROM parametro_clave WHERE clave = NEW.clave;
    IF v_por_periodo AND NEW.periodo = '*' THEN
        RAISE EXCEPTION 'el parámetro % va por periodo: no admite periodo ''*''', NEW.clave;
    ELSIF NOT v_por_periodo AND NEW.periodo <> '*' THEN
        RAISE EXCEPTION 'el parámetro % no va por periodo: use periodo ''*''', NEW.clave;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER contrato_parametro_periodo
    BEFORE INSERT OR UPDATE ON contrato_parametro
    FOR EACH ROW EXECUTE FUNCTION contrato_parametro_comprobar_periodo();

-- Suministro de la vivienda (docs/contexto.md).
INSERT INTO contrato (id, descripcion, tarifa_acceso) VALUES
    ('vivienda', 'Vivienda: Feníe Energía, indexado, 2.0TD, 4,6 kW', '2.0TD');

-- Parámetros del contrato y de la factura de octubre de 2026.
-- Vigencia desde el 01/01/2026: los peajes y cargos de la factura son los
-- de 2026 (los cargos coinciden con el segmento tarifario 1 de la Orden
-- TED/1524/2025, en vigor desde el 01/01/2026). Para fechas anteriores no hay
-- parámetros cargados y la vista no calcula precio final.
INSERT INTO contrato_parametro (contrato, clave, periodo, vigencia, valor, fuente) VALUES
    ('vivienda', 'rfe',   '*',  '[2026-01-01,)', 20.00,    'Contrato Feníe, cláusula 4.2'),
    ('vivienda', 'peaje', 'P1', '[2026-01-01,)', 0.033261, 'Factura Feníe octubre 2026'),
    ('vivienda', 'peaje', 'P2', '[2026-01-01,)', 0.016409, 'Factura Feníe octubre 2026'),
    ('vivienda', 'peaje', 'P3', '[2026-01-01,)', 0.000077, 'Factura Feníe octubre 2026'),
    ('vivienda', 'cargo', 'P1', '[2026-01-01,)', 0.064292, 'Factura Feníe octubre 2026; Orden TED/1524/2025, ap. primero b), segmento 1'),
    ('vivienda', 'cargo', 'P2', '[2026-01-01,)', 0.012858, 'Factura Feníe octubre 2026; Orden TED/1524/2025, ap. primero b), segmento 1'),
    ('vivienda', 'cargo', 'P3', '[2026-01-01,)', 0.003215, 'Factura Feníe octubre 2026; Orden TED/1524/2025, ap. primero b), segmento 1'),
    ('vivienda', 'impuesto_electrico', '*', '[2026-01-01,)', 0.0511269632, 'Factura Feníe octubre 2026'),
    ('vivienda', 'iva',                '*', '[2026-01-01,)', 0.21,         'Factura Feníe octubre 2026');
