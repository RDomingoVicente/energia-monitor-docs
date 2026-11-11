-- Festivos que cuentan como valle (P3) en el 2.0TD y función de periodo.
--
-- Circular CNMC 3/2020, periodos horarios del 2.0TD (BOE-A-2020-1066, texto
-- consolidado):
-- "Se consideran como horas del periodo 3 (valle) todas las horas de los
-- sábados, domingos, el 6 de enero y los días festivos de ámbito nacional,
-- definidos como tales en el calendario oficial del año correspondiente, con
-- exclusión tanto de los festivos sustituibles como de los que no tienen
-- fecha fija."
-- Por tanto: Viernes Santo y Jueves Santo NO cuentan (no tienen fecha fija), y
-- los traslados autonómicos tampoco.
--
-- Se precargan 2025–2027. Revisar cada año con la resolución del BOE de
-- fiestas laborales y añadir las fechas en una nueva migración.

CREATE TABLE festivo (
    fecha       date PRIMARY KEY,
    descripcion text NOT NULL
);

INSERT INTO festivo (fecha, descripcion)
SELECT make_date(a.anio, f.mes, f.dia), f.descripcion
FROM generate_series(2025, 2027) AS a(anio)
CROSS JOIN (VALUES
    (1,  1,  'Año Nuevo'),
    (1,  6,  'Epifanía del Señor'),
    (5,  1,  'Fiesta del Trabajo'),
    (8,  15, 'Asunción de la Virgen'),
    (10, 12, 'Fiesta Nacional de España'),
    (11, 1,  'Todos los Santos'),
    (12, 6,  'Día de la Constitución Española'),
    (12, 8,  'Inmaculada Concepción'),
    (12, 25, 'Natividad del Señor')
) AS f(mes, dia, descripcion);

-- Periodo tarifario 2.0TD (Península) del instante dado.
-- Los tramos de liquidación (15 o 60 min) nunca cruzan un cambio de hora en
-- punto, así que basta evaluar el inicio del tramo.
CREATE FUNCTION periodo_2td(p_instante timestamptz) RETURNS text
LANGUAGE sql STABLE AS $$
    WITH l AS (SELECT p_instante AT TIME ZONE 'Europe/Madrid' AS t)
    SELECT CASE
        WHEN extract(isodow FROM l.t) IN (6, 7)
          OR EXISTS (SELECT 1 FROM festivo f WHERE f.fecha = l.t::date)
            THEN 'P3'
        WHEN extract(hour FROM l.t) < 8 THEN 'P3'
        WHEN extract(hour FROM l.t) IN (10, 11, 12, 13, 18, 19, 20, 21) THEN 'P1'
        ELSE 'P2'
    END
    FROM l
$$;

COMMENT ON FUNCTION periodo_2td(timestamptz) IS
    'Periodo P1/P2/P3 del peaje 2.0TD (Circular CNMC 3/2020) en hora de Europe/Madrid.';
