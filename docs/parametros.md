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
- Cada valor lleva su origen (`--origen`):
  - `contrato`: pactado con la comercializadora (RFE).
  - `regulado`: fijado por una norma publicada (peajes, cargos, impuestos,
    pérdidas, pagos por capacidad, ROM, FNEE, tasa municipal).
  - `estimado`: aproximación sin norma ni serie (hoy SSAA y ROS).
  Un valor desconocido se deja sin valor (NULL, solo con `regulado` o
  `estimado`) y el precio final de esas fechas queda vacío.
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
        --clave ssaa --desde 2027-01-01 --valor 28.0 --origen estimado \
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
INSERT INTO contrato_parametro (contrato, clave, periodo, vigencia, valor, origen, fuente, nota)
VALUES ('vivienda', 'ssaa', '*', daterange(DATE '2027-01-01', NULL, '[)'), 28.0, 'estimado',
        'Estimación a partir de referencias de prensa',
        'Aproximado; sustituir por la serie de ESIOS');

COMMIT;
```

Si la restricción `contrato_parametro_sin_solape` rechaza el INSERT, hay otra
fila posterior que se solapa: limita el `hasta` del nuevo valor al inicio de
esa fila.

## Refrescar el precio materializado

El dashboard "Energía: coche" y la comparación con el PVPC leen
`mv_precio_final`, una copia materializada de `v_precio_final` (migración
0012). Se refresca sola al final de cada ejecución del colector y al terminar
`parametro registrar` (de contrato), `recargar`, `esios recargar` y
`esios estimar`.

**Un cambio por SQL no la refresca.** Tras modificar a mano
`contrato_parametro`, `contrato_ssaa_componente`, `ssaa_estimacion`,
`serie_esios`, `precio_mercado` o `festivo`:

    docker compose exec collector python -m energia_monitor refrescar

El comando registra las filas de la materializada frente a las esperadas
(tramos de `precio_mercado` por contratos) y devuelve 1 si no coinciden.
Hasta refrescar, esos paneles muestran el precio anterior; el resto del
dashboard (que lee `v_precio_final`) ya ve el cambio.

En migraciones:

- Una migración que cambie esos datos debe terminar con
  `REFRESH MATERIALIZED VIEW mv_precio_final;` (dentro de la transacción
  de la migración no hace falta `CONCURRENTLY`).
- Si cambian las columnas de `v_precio_final`, hay que borrar
  `mv_precio_final` (y las vistas que la leen) y volver a crearlas en la
  misma migración.
- PostgreSQL 17 crea y refresca las vistas materializadas con
  `search_path = pg_catalog, pg_temp`: las funciones que use
  `v_precio_final` deben cualificar sus tablas (`public.festivo`).

## Recalcular la energía por tramo

"Energía: coche" lee la energía por cuarto de hora de la tabla
`energia_tramo`, que el servicio `muestreo` actualiza al guardar cada
muestra. Si se insertan, corrigen o borran filas de `cargador_muestra` por
SQL, hay que recalcular los días afectados (fechas locales, ambas incluidas):

```sh
docker compose exec collector python -m energia_monitor cargador recalcular --desde 2026-11-14 --hasta 2026-11-15
```

`--hasta` es hoy por defecto. El recálculo es idempotente y tarda unos
segundos incluso con un año de muestras. `cargador estado` compara los dos
últimos días de `energia_tramo` con el cálculo desde las muestras; si no
coinciden, muestra un aviso con el comando y devuelve 1.

Al desplegar la versión que aplica la migración 0013, el muestreo anterior
puede guardar alguna muestra entre la migración y su reinicio sin actualizar
la tabla: tras `docker compose up -d`, ejecutar `cargador estado` y, si
avisa, `cargador recalcular --desde` con la fecha de hoy.

## SSAA: valor real de ESIOS y definición por contrato

Con `ESIOS_TOKEN`, el SSAA de cada tramo sale de ESIOS y no de
`contrato_parametro`. Orden: real > estimación (`ssaa_estimacion`) > parámetro
`ssaa` (que sigue siendo el último recurso). `v_precio_final.ssaa_metodo`
indica cuál se ha usado: `esios`, `prevision_ree`, `perfil_14d` o `parametro`.

Qué componentes suman el SSAA real lo decide `contrato_ssaa_componente`
(por contrato, con vigencia `[desde, hasta)` y `incluido` sí/no). Por defecto,
los 13 componentes desde el 01/01/2026 (ver docs/contexto.md). Para ver la
definición vigente:

    docker compose exec collector python -m energia_monitor esios estado

Ejemplo: excluir los desvíos (799 y 800) desde el 01/01/2027 si la
conciliación con la factura confirma que Feníe los asume:

```sql
BEGIN;

UPDATE contrato_ssaa_componente
   SET vigencia = daterange(lower(vigencia), DATE '2027-01-01', '[)')
 WHERE contrato = 'vivienda' AND indicador IN (799, 800)
   AND vigencia @> DATE '2027-01-01';

INSERT INTO contrato_ssaa_componente (contrato, indicador, vigencia, incluido, nota)
VALUES ('vivienda', 799, daterange(DATE '2027-01-01', NULL, '[)'), false, 'Asumido por Feníe (factura ...)'),
       ('vivienda', 800, daterange(DATE '2027-01-01', NULL, '[)'), false, 'Asumido por Feníe (factura ...)');

COMMIT;
```

Si el cambio debe aplicarse a todo el histórico, basta con poner
`incluido = false` en la fila vigente (no hay versión que publicar). La vista
recalcula al momento; la estimación se recalcula en la siguiente ejecución
del colector o con `python -m energia_monitor esios estimar`.

## Batería (fase 2, etapa 2)

Especificación: docs/fase2.md, "Etapa 2: batería y previsión". El nivel se
recalcula entero en PostgreSQL (`recalcular_bateria()`). Lo lanzan el
muestreo (cambio de estado del conector y sesiones nuevas del histórico), una
lectura y un parámetro nuevo registrados por el CLI.

```sh
docker compose exec collector python -m energia_monitor bateria estado
docker compose exec collector python -m energia_monitor bateria lectura --pct 62
docker compose exec collector python -m energia_monitor bateria lectura --pct 58 --instante "2026-11-15 08:30" --nota "app del coche"
docker compose exec collector python -m energia_monitor bateria recalcular
docker compose exec collector python -m energia_monitor bateria carga-externa --kwh 28.4 --coste 12.75 \
  --instante "2026-11-21 13:10" --nota "Ionity Zaragoza"
```

- `bateria estado` muestra el nivel con su confianza (LEÍDO, LEÍDO + CARGA o
  ESTIMADO) y devuelve 1 si hay avisos (catálogo de
  formulario-maquetas.md, 7.7).
- `bateria recalcular` dice cuánto ha tardado (también en
  `bateria_recalculo`). El dashboard «Energía: batería» lee lo que deja el
  recálculo: tras cambiar lecturas, sesiones o cargas externas por SQL, no
  cambia hasta ejecutarlo.
- `bateria carga-externa`: carga fuera de casa, con kWh y coste opcionales.
  Solo con el coche desconectado a esa hora. Sin kWh no suma al nivel y el
  intervalo queda fuera de la estadística: al volver, anota una lectura.
  Para anular una por SQL: `UPDATE bateria_carga_externa SET anulada_en = now(),
  motivo = '...' WHERE id = N` y `bateria recalcular`.
- `bateria lectura` toma la hora de Madrid (por defecto, ahora). Para
  corregir o anular una lectura hasta que llegue el formulario:

  ```sql
  UPDATE bateria_lectura SET anulada_en = now(), motivo = 'error al leer' WHERE id = 7;
  ```

  y después `bateria recalcular`. Las lecturas no se borran.

Parámetros (`bateria_parametro`, mismas reglas que los del cargador; origen
también `usuario` o `ficha_tecnica`):

```sh
docker compose exec collector python -m energia_monitor parametro listar --ambito bateria
docker compose exec collector python -m energia_monitor parametro registrar --ambito bateria \
  --clave factor_bateria --desde 2026-12-01 --valor 0.88 --origen estimado \
  --fuente "factor sugerido por bateria estado (5 observaciones)"
```

Registrar un parámetro de batería recalcula el nivel. Los booleanos van como
0/1 (`detectar_carga_completa`). Cada clave tiene un rango
(`bateria_parametro_clave.minimo` y `maximo`, migración 0016) que aplica un
trigger, con el mensaje «fuera de rango (admitido de X a Y)»; además siguen
los CHECK por clave de la 0014 (enteros, 0/1). Los nombres, textos y rangos
son los de formulario-maquetas.md, 7.5. Clave nueva en la 0016:
`umbral_carga_externa_kwh` (5, usuario).

Horario y excepciones, por SQL hasta que llegue el formulario (horas
locales; una excepción activa por fecha; la baja es lógica):

```sql
-- Lunes a jueves a las 9:00, viernes a las 7:00, sin salida el fin de semana.
INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES
    (1, '09:00'), (2, '09:00'), (3, '09:00'), (4, '09:00'), (5, '07:00'), (6, NULL), (7, NULL)
ON CONFLICT (dia_iso) DO UPDATE SET hora_salida = EXCLUDED.hora_salida, actualizado_en = now();

-- Con salida a otra hora y consumo normal (P75), con nivel mínimo opcional:
INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo, pct_objetivo)
    VALUES ('con_salida', '2026-11-20', '06:30', 'normal', 80);
-- Con consumo indicado (km o pct, uno solo):
INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo, km)
    VALUES ('con_salida', '2026-11-21', '07:30', 'indicado', 350);
-- Sin salida (solo en un día que tenga salida en el horario; por SQL no se comprueba):
INSERT INTO bateria_excepcion (tipo, fecha) VALUES ('sin_salida', '2026-11-23');
UPDATE bateria_excepcion SET anulada_en = now() WHERE id = 3;  -- baja
```

Una excepción con consumo indicado con fecha de hoy o pasada cambia
trayectos ya guardados: después, `bateria recalcular`. El horario y las excepciones futuras no lo
necesitan.


## Planificador de carga (fase 2, etapa 3)

Especificación: docs/fase2.md, "Etapa 3: planificador de carga" (E3.9). Los
parámetros del planificador viven en `bateria_parametro`, con las mismas
reglas que los de la batería (CLI con `--ambito bateria`). Migración 0020:

- 19 claves nuevas (estrategia, disponibilidad, cargador y franjas, avisos),
  vigentes desde el 1/1/2025 con los valores de E3.9.
- Todas las claves tienen `grupo` y `orden` (`bateria_parametro_grupo`: 7
  grupos con su pregunta); la pantalla Parámetros y la Ayuda las ordenan así.
  Una clave nueva en una migración futura debe llevar grupo y orden (son NOT
  NULL).
- `editable` = false en `id_tarea_guardiana`: la página no deja cambiarla;
  solo el CLI. Desde el paso 3c no se cambia con `parametro registrar`
  sino con `planificador guardiana --id N --confirmar` (crea la guardiana en
  el id nuevo, la confirma, registra el parámetro y borra la vieja).
- `congelar_franja_min` (0022, grupo 6, 30 min, rango 0–120): una franja
  propia que empieza en menos de ese tiempo ya no se mueve ni se borra; se
  deja como está en el cargador y el plan cuenta su energía.
- `carga_oportunista` va como 0/1. `hora_publicacion_precios` (unidad
  «hora») se guarda en minutos desde las 00:00 (810 = 13:30, rango
  720–1080), pero en la página y en el CLI se escribe y se muestra HH:MM:

  ```sh
  docker compose exec collector python -m energia_monitor parametro registrar --ambito bateria \
    --clave hora_publicacion_precios --desde 2026-12-01 --valor 14:00 --origen usuario --fuente "OMIE"
  ```

  El CHECK de valores de 0014 se sustituye en la 0020 (enteros, 0/1,
  márgenes con 0, horas en minutos de 0 a 1439).

Fuera de `bateria_parametro`:

- `planificador_modo` (sombra, activo o pausa, desde un instante, con
  historial). Desde el paso 3c se cambia en Plan de carga o con el CLI
  (contenedor `muestreo`, que es el que tiene `VIARIS_URL`):

  ```sh
  docker compose exec muestreo python -m energia_monitor planificador modo activo
  ```
- `planificador_config` (una fila): contrato y fuente del precio del
  planificador (`vivienda`, `omie`). Solo por SQL:

  ```sql
  UPDATE planificador_config SET fuente = 'omie';
  ```

Recálculo del plan: el muestreo recalcula cada 15 minutos, en cada cambio
de estado del conector o sesión nueva, y cuando hay una solicitud. La piden
`bateria_servicio.recalcular` (lecturas, excepciones, horario, cargas
externas y parámetros, desde el formulario o el CLI) y el refresco de
`mv_precio_final`. Tras cambiar por SQL algo que use el plan, basta con:

```sql
SELECT solicitar_plan('cambio por SQL');
```

Ver el plan sin guardarlo, o el que se habría calculado al enchufar un día
pasado con su coste frente al real y a cargar al enchufar:

```sh
docker compose exec collector python -m energia_monitor planificador plan
docker compose exec collector python -m energia_monitor planificador simular --dia 2026-11-17
```

Paso 3c (hablan con el cargador; en el contenedor `muestreo`):

```sh
docker compose exec muestreo python -m energia_monitor planificador tareas
docker compose exec muestreo python -m energia_monitor planificador reconciliar --en-seco
docker compose exec muestreo python -m energia_monitor planificador limpiar --confirmar
docker compose exec muestreo python -m energia_monitor planificador guardiana --id 10 --confirmar
```

`limpiar` pasa a sombra y borra las tareas propias salvo la guardiana: con
solo la guardiana activa el cargador no carga fuera de ella.
