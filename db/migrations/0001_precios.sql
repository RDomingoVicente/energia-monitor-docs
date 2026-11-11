-- Precio del mercado diario por tramo de liquidación.
--
-- Un tramo es [inicio, fin) en UTC. Desde el 01/10/2025 el mercado diario es
-- cuartohorario (15 min); los históricos anteriores son horarios (60 min).
-- La clave es (inicio, fuente): cada fuente (omie, esios...) guarda su serie y
-- las recargas son idempotentes (upsert).

CREATE TABLE precio_mercado (
    inicio          timestamptz   NOT NULL,
    fin             timestamptz   NOT NULL,
    fuente          text          NOT NULL,
    precio_eur_mwh  numeric(10,2) NOT NULL,
    -- Versión del fichero de origen (OMIE publica .1, .2...); NULL si la
    -- fuente no versiona.
    version_fichero integer,
    descargado_en   timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (inicio, fuente),
    CONSTRAINT precio_mercado_tramo_valido CHECK (fin > inicio),
    CONSTRAINT precio_mercado_version_valida CHECK (version_fichero IS NULL OR version_fichero >= 1)
);

COMMENT ON TABLE precio_mercado IS
    'Precio marginal del mercado diario (zona España) por tramo de liquidación, en €/MWh.';
