-- SSAA: el estimado de noviembre de 2026 pasa a vigencia abierta.
--
-- Se aplica desde el 01/11/2026 hasta que se registre otro valor (con
-- `python -m energia_monitor parametro registrar ...`, que cierra esta
-- vigencia) o se sustituya por la serie de ESIOS. Ver docs/parametros.md.

DO $$
DECLARE
    n integer;
BEGIN
    UPDATE contrato_parametro
       SET vigencia = daterange('2026-11-01', NULL, '[)'),
           nota     = 'Aproximado; vigente hasta otro estimado o la serie de ESIOS'
     WHERE contrato = 'vivienda'
       AND clave    = 'ssaa'
       AND periodo  = '*'
       AND vigencia = daterange('2026-11-01', '2026-12-01', '[)');
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 1 THEN
        RAISE EXCEPTION 'se esperaba 1 fila de SSAA de noviembre de 2026 y hay %', n;
    END IF;
END;
$$;
