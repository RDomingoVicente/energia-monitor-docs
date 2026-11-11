# Parámetros del contrato

Los parámetros de la fórmula (peajes, cargos, RFE, impuestos y componentes
estimados como SSAA o ROS) viven en `contrato_parametro`, con vigencia
`daterange '[desde, hasta)'`. Cambiar un valor no requiere publicar una versión
nueva del colector.

Reglas:

- No se modifican valores históricos: se cierra la vigencia anterior y se
  añade una fila nueva.
- `periodo = '*'` para claves que no van por periodo (rfe, ssaa, ros, rom,
  fnee, tasa_municipal, impuesto_electrico, iva); `P1`..`P3` para peaje, cargo,
  pagos_capacidad y perdidas. Lo valida un trigger.
- Las unidades son las del catálogo `parametro_clave` (€/MWh, €/kWh o
  fracción: 21 % se escribe 0.21).
- Un cambio de esquema o de datos de referencia que deba reproducirse en
  instalaciones nuevas va en una migración nueva (las publicadas son
  inmutables). El CLI y el SQL de abajo son para mantener los valores de la
  instalación en el NAS.

## Con el CLI (recomendado)

En el NAS, desde /volume1/docker/energia-monitor:

    # Ver los parámetros vigentes hoy (o en --fecha AAAA-MM-DD)
    docker compose exec collector python -m energia_monitor parametro listar

    # Nuevo estimado de SSAA desde el 1 de enero de 2027
    docker compose exec collector python -m energia_monitor parametro registrar \
        --clave ssaa --desde 2027-01-01 --valor 28.0 --estimado \
        --fuente "Estimación a partir de referencias de prensa" \
        --nota "Aproximado; sustituir por la serie de ESIOS"

`registrar` cierra, en la fecha `--desde`, la fila vigente ese día y añade la
nueva hasta el inicio de la siguiente fila posterior (o con vigencia abierta).
Si ya existe una fila que empieza justo ese día, se niega: corrígela en SQL.

## Con SQL (equivalente)

Conectado como el usuario propietario (no `grafana_ro`):

```sql
BEGIN;

-- 1. Cerrar la vigencia que contiene la fecha de inicio del nuevo valor.
UPDATE contrato_parametro
   SET vigencia = daterange(lower(vigencia), DATE '2027-01-01', '[)')
 WHERE contrato = 'vivienda' AND clave = 'ssaa' AND periodo = '*'
   AND vigencia @> DATE '2027-01-01';

-- 2. Añadir el nuevo valor con vigencia abierta.
INSERT INTO contrato_parametro (contrato, clave, periodo, vigencia, valor, estimado, fuente, nota)
VALUES ('vivienda', 'ssaa', '*', daterange(DATE '2027-01-01', NULL, '[)'), 28.0, true,
        'Estimación a partir de referencias de prensa',
        'Aproximado; sustituir por la serie de ESIOS');

COMMIT;
```

Si la restricción `contrato_parametro_sin_solape` rechaza el INSERT, hay otra
fila posterior que se solapa: limita el `hasta` del nuevo valor al inicio de
esa fila.
