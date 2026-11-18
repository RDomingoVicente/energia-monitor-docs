# Cargador Orbis Viaris: API HTTP local

Estado a 18/11/2026. Fuentes: respuestas reales del cargador (marcadas
**verificado**) y lectura del JavaScript de su interfaz web, `index.js`
(marcadas **del código**, sin probar). El alta, la modificación y el borrado de
franjas se probaron el 13/11/2026 con franjas inactivas y sin coche conectado;
el comportamiento de las franjas con el coche conectado, el 17 y el 18/11/2026
(ver "Comportamiento con el coche conectado").

## Equipo

| Dato | Valor |
|------|-------|
| Modelo | VIARIS UNI, monofásico (`fwv_pot` 1PH), manguera Mennekes tipo 2 de 5 m |
| Serie / MAC | EVVC3C9791D30 / EC:64:C9:79:1D:30 |
| Firmware | 7.7.34 (`fwv_cortex` 003.007.016, 24/02/2026), hardware 8.0 |
| Red | wifi, 192.168.1.34 (misma LAN que el NAS; el colector llega por HTTP) |
| Ampacidad | 20 A configurados (`ampacity`), 32 A máximo (`maxAmpacity`, `plugLimit`) |
| Potencias | `selectorPower` 4600 W, `maxPower` 4600 W, límite de red 4500 W |
| Medida de la vivienda | pinza conectada (`ctxDetected: true`) |
| Módulos | ocpp, modbus y solar disponibles; MQTT y OCPP sin configurar |

## Convenciones

- HTTP en el puerto 80, sin autenticación ni TLS. Cualquier equipo de la LAN
  puede leer y escribir la configuración.
- JSON en ambos sentidos; en escrituras,
  `Content-Type: application/json; charset=utf-8`.
- Patrón de rutas: `/device`, `/elements` y `/modules/<módulo>`, con
  `?level=cfg|stat|all` para elegir configuración o estado.
- Marcas de tiempo: segundos Unix en UTC (comprobado con el histórico:
  1793483435 = 31/10/2026 22:50 hora de Madrid, igual que el CSV exportado).
- Horas de las franjas: minutos desde medianoche en la hora local del cargador.
- Potencias en W, energías en Wh, corrientes en A (campos `...SmCh` en mA).
- El conector se llama `mennekes`.

## Lecturas verificadas

| Petición | Contenido |
|----------|-----------|
| `GET /device` | Modelo, serie, MAC, firmware, `ampacity`, `maxAmpacity`, `limitPower`, `selectorPower` |
| `GET /elements?level=stat` | Estado eléctrico del conector: `ampacity`, `plugLimit`, `current`, `connector`, `temperature` |
| `GET /elements?level=cfg` | `minPower` 1380, `limitPower` 4600 y parámetros de protección |
| `GET /modules/evsm/elements` | Estado de carga: `stat.state`, `event`, `idCharge`, `localtime` |
| `GET /modules/modulator?level=stat` | `totalPower`, `evsePower`, `homePower` (W), `ctxDetected` |
| `GET /modules/scheduler/elements?level=cfg` | Franjas de carga y tramos de potencia |
| `GET /modules/modulator/historic` | Sesiones de carga (`count`, `total`, `body`) |

### Estado de carga (`stat.state`)

Del código de la interfaz. Verificados con la carga del 14/11/2026 (muestras
cada 60 s): 1 libre (`event` 2), 4 conectado sin cargar (`event` 7) y 6
cargando (`event` 6). El 3 no ha aparecido.

Estado 5, primera vez el 16/11/2026: a las 05:06:58 (hora de Madrid) durante
un minuto, entre dos 6, dentro de la franja de 02:00 a 06:00. Es una pausa
durante la carga, probablemente pedida por el coche (sin verificar). El
formulario la trata como carga: «cargando desde» no se corta por ella.

| Valor | Significado |
|-------|-------------|
| 1, 2 | Libre |
| 3, 4 | Conectado (diferencia entre ambos sin aclarar) |
| 5, 6 | Cargando (5: pausa breve durante la carga, vista el 16/11/2026) |
| 7 | Pausado |
| 8 | Carga finalizada |
| 32 | Inoperativo |
| otros | 14, 30, 31, 33: encendido, apagado, seleccionado; el resto, error |

`localtime` no es la hora actual ni la del último cambio de estado: marca el
último cambio de evento (`event`). El 15/11/2026 marcaba 18:19 con el coche
conectado desde las 18:00:14 según las muestras (paso a 4). **No sirve como
hora de conexión**: el proyecto la toma de `cargador_estado` (primer estado no
libre tras el último 1 o 2). Corrige lo anotado el 14/11/2026 («último cambio
de estado»), que coincidía porque ese día estado y evento cambiaron a la vez.

### Potencias del modulador (verificado el 14/11/2026)

`totalPower` **no** es `evsePower + homePower`. Durante la carga,
`homePower` = 0 y `totalPower` queda de media 218 W por debajo de
`evsePower`: `homePower` se calcula como `max(0, totalPower − evsePower)`.

- La casa no es medible mientras carga el coche: en esos tramos su consumo es
  desconocido (no 0). Sigue marcada como no verificada. Desde la migración
  0013, `casa_wh` es NULL en los tramos con alguna muestra con `evsePower` >
  0 (sin carga, `evsePower` es exactamente 0), y el panel "Estado del
  cargador" deja vacía la potencia de la casa mientras carga. No se calcula
  como `totalPower − evsePower`: saldría negativa.
- El medidor del cargador (`evsePower`) y la pinza (`totalPower`) no cuadran
  entre sí. Pendiente de calibrar con la curva horaria de i-DE.

### Disponibilidad

Aproximadamente cada 2 h el cargador no responde en 5 s y queda un hueco de
unos 70 s entre muestras (14/11/2026), dentro del límite de 180 s: la muestra
siguiente sigue contando.

### Histórico

`body` es texto con una sesión por línea y 18 campos separados por comas.
Campos identificados: [0] identificador (igual al inicio), [3] conector,
[6] inicio, [8] origen del inicio, [10] fin, [12] origen del fin, [14] energía
en Wh, [15] a [17] energía por periodo (sin configurar: todo en el primero).
Orígenes vistos: `schedman` (planificador) y `evsm` (el coche o la desconexión).
Los campos [9] y [13] son un contador acumulado de energía (Wh) al inicio y al
fin: [13] − [9] = [14] en las 100 sesiones del histórico del 13/11/2026
(**verificado**).

Las sesiones del `body` van separadas por `\n` (el escape de JSON: en la
respuesta en bruto se ve `\n` literal; con `jq -r` sale una por línea). El
búfer **no está ordenado** por fecha (la sesión más antigua puede ir al final,
visto el 13 y el 18/11/2026): para encontrar la sesión más reciente hay que
ordenar por la marca de inicio ([6]) o de fin ([10]), no tomar la última línea.
Así, `jq -r .body | tail -5` no da las cinco últimas sesiones.

El búfer guarda 100 sesiones (unos 54 días con el uso actual): el colector debe
leerlo y persistirlo de forma periódica. El contador del cargador da en torno
a un 4 % más que el contador de la distribuidora (octubre de 2026).

## Planificador

`GET /modules/scheduler/elements?level=cfg` devuelve, por conector,
`data.cfg.tasks[]` y `data.cfg.defaultState` (1 en este equipo). Con
`defaultState` 1, el cargador bloquea la carga fuera de las tareas solo si hay
**al menos una tarea activa**; sin ninguna, carga libremente (verificado el 17
y el 18/11/2026, ver más abajo). El significado exacto del valor sigue sin
verificar (no se ha probado otro).

Tarea real (franja de 02:00 a 06:00, activa):

```json
{
  "id": 5, "user": "", "group": 0, "priority": 1,
  "initTime": {
    "day": 0, "month": 0, "weekday": 32,
    "timeList": [{"hourMin": 120, "duration": 240, "maxPower": 0, "phases": 0,
                  "_initLocaltime": 0, "_endLocaltime": 0}]
  },
  "active": 1, "type": 0, "maxPower": -1, "solarPriority": 0,
  "minCurrent": 0, "validFrom": 0, "validTo": 0, "chargeRelated": 0
}
```

| Campo | Significado | Estado |
|-------|-------------|--------|
| `type` | 0 franja de carga; 1 tramo de potencia de red (lleva `maxPower` en `timeList`) | del código |
| `active` | 1 activa, 0 inactiva | verificado |
| `hourMin`, `duration` | inicio y duración en minutos. La duración se acepta aunque pase de medianoche (y `_endLocaltime` la refleja), pero **la tarea deja de cargar a las 00:00**: una franja que cruce la medianoche se escribe como dos tareas | verificado (13/11 y noche del 17 al 18/11) |
| `weekday` | máscara de bits, bit 0 = domingo (1 D, 2 L, 4 M, 8 X, 16 J, 32 V, 64 S); 0 = todos los días | verificado: 32 dio ocurrencia el viernes 13/11 y 65 no |
| `day`, `month` | máscaras (bit d−1, bit m−1) para una fecha concreta, con `weekday` 0; 0 = cualquiera. Sin año: la franja se repite cada año si no se borra | verificado el cálculo de la ocurrencia (4096/1024 = 13/11; 8192/1024 = 14/11) y la ejecución ese día (17/11) |
| `validFrom`, `validTo` | presumible ventana de validez | sin verificar |
| `_initLocaltime`, `_endLocaltime` | calculados por el cargador, en UTC, también en franjas inactivas: la ocurrencia de hoy en franjas recurrentes (0 si hoy no aplica) y la de su fecha en franjas con `day` y `month` | verificado |

Tareas presentes el 12/11/2026: id 2 (00:00 a 01:00, todos los días,
inactiva), id 4 (10:00 a 14:00, `weekday` 65, inactiva) e id 5 (02:00 a 06:00,
`weekday` 32, activa). No hay tareas de tipo 1: rige el límite de red general
de 4500 W.

Estado el 18/11/2026: la tarea 9 es la **guardiana** (activa, con fecha 31/12
a las 00:00 y 1 minuto; ver docs/fase2.md, E3.6) y las tareas 5 y 6 son del
usuario, creadas con la app de Orbis (inactivas). Las tareas 2 y 4 del 12/11
las borró el usuario el 15/11. Ninguna de las tres la toca el código;
hasta el paso 3c de la etapa 3 el código no escribe en el cargador.

### Escrituras (verificadas el 13/11/2026)

| Operación | Petición | Cuerpo |
|-----------|----------|--------|
| Crear franja | `POST /modules/scheduler/elements/mennekes` | `{"tasks":[{...}]}` con un `id` libre |
| Modificar o activar | `PUT /modules/scheduler/elements/mennekes` | `{"tasks":[{...}]}` con el `id` existente |
| Borrar | `DELETE /modules/scheduler/elements/mennekes` | `{"tasks":[{"id":N}]}`; para tipo 1, `{"id":N,"type":1}` |

Cuerpo mínimo que envía la interfaz al crear o modificar una franja:

```json
{"tasks":[{"id":3,"active":1,"user":"","group":0,"priority":1,
  "initTime":{"day":0,"month":0,"weekday":0,
              "timeList":[{"hourMin":120,"duration":240}]}}]}
```

Las tres devuelven 200 y la configuración completa del conector (`cfg` y
`stat`), y la app e-VIARIS refleja el cambio. El PUT se probó con la tarea
completa, como hace la interfaz; el cuerpo parcial está sin probar. El DELETE
de varias tareas en una petición está sin probar.

### Comportamiento con el coche conectado (verificado el 17 y el 18/11/2026)

| Hecho | Prueba |
|-------|--------|
| Con al menos una tarea activa, el coche enchufado no carga fuera de las tareas | 15/11 y 18/11 |
| **Sin ninguna tarea activa, carga libremente** en cuanto el coche está enchufado | 17/11 20:16 (sesión de 53 s al desactivar todas) y 18/11 08:47 (estado 6, 4040 W) |
| Una única tarea activa con fecha de otro día basta para bloquear la carga; al **reactivarla** durante una carga libre, la carga se corta al momento | 18/11 08:45–08:48, tarea 9 (31/12 00:00, 1 min) |
| Una tarea con fecha (`day` y `month`, `weekday` 0) se ejecuta ese día | 17/11, tarea 9 con fecha 17/11 desde las 23:00, sin tareas semanales activas |
| **Una tarea no cruza la medianoche**: con `hourMin` 1380 y `duration` 540, el cargador cortó a las 00:00:01 (inicio y fin `schedman`, 4,6 kWh) aunque `_endLocaltime` marcaba las 08:00. Probablemente comprueba la fecha o el día de la semana en cada instante (sin verificar) | noche del 17 al 18/11 |
| La tarea se cumple al segundo: inicio 02:00:01, fin 06:00:01 | sesión del 16/11 |
| **Desactivar una tarea en curso** (`active: 0`) corta la carga | 17/11, desde la app de Orbis |
| El cargador **no impide solapes**: acepta y activa dos tareas activas que se pisan, sin error (HTTP 200) | 17/11, tarea 9 sobre las tareas 5 y 6 |
| La app de Orbis solo crea tareas semanales y parte en dos, a medianoche, las franjas que la cruzan (23:00 + 59 min y 00:00 + 480 min, con un minuto sin carga entre ambas) | 17/11 |
| La app muestra una tarea con fecha como «todos los días»; si se guarda desde la app, probablemente pierde la fecha (sin verificar) | 17/11 |

Consecuencia para el código: una franja que cruce la medianoche se escribe
como dos tareas, una hasta las 00:00 y otra desde las 00:00, como hace la app;
y debe haber siempre una tarea activa (la guardiana) para que el cargador no
cargue libremente.

### Respuesta de `stat`

`stat` del planificador (visto en esas respuestas, sin coche): `idTask` 0,
`idTaskPower` 0, `state` 0, `active` 1, `priority` 255. Presumiblemente indica
la tarea que gobierna en ese momento; significado por confirmar con una carga.

La interfaz limita a 10 las tareas por conector.

## Otras escrituras (del código, sin probar)

| Operación | Petición |
|-----------|----------|
| Ampacidad del cargador | `PUT /device?level=ampacity` con `{"ampacity":N}` |
| Límite de potencia del conector | `PUT /elements/mennekes` con `limitPower`, `phaseRotation`, `safetyOnRetries` |
| Límite de red del modulador | `PUT /modules/modulator` con `limitPower`, `limitPowerByPhase` |
| Suscripción a tiempo real | `PUT /device/rt` con `{"period":3,"timeout":30,"status":true}` (caduca sola) |

No hay orden de iniciar o detener la carga en esta interfaz: el control se
hace con las franjas.

## Tiempo real (WebSocket)

`ws://192.168.1.34/`, tras activar la suscripción con `PUT /device/rt`.
Mensajes de texto con el formato `tema carga-JSON`:

- `evsm_elem:<conector> {...}`: cambio de estado (`stat.state`).
- `modulator_processes {...}`: `totalPower`, `evsePower`, `homePower`,
  `limitPower`, `totalCurrent[]` (mA) y, por conector, `now.active` (Wh
  acumulados), `now.aPow[]` (W) y `chargeProcess.last.active`.

El sondeo por HTTP da lo mismo; el WebSocket no es necesario para empezar.

## Uso en el código (etapa 1)

`energia_monitor.viaris.ClienteViaris` solo admite, por comparación exacta de
método, ruta y query, las lecturas que usa el muestreo:

| Petición | Uso | Cada |
|----------|-----|------|
| `GET /modules/modulator?level=stat` | `evsePower`, `homePower`, `totalPower` | 60 s |
| `GET /modules/evsm/elements` | `stat.state` del primer elemento (`event`, `idCharge` y `localtime` se guardan en bruto, sin interpretar) | 60 s |
| `GET /modules/modulator/historic` | sesiones del `body` | 15 min |

Tiempo de espera de 5 s, sin reintentos, sin redirecciones y sin proxies. La
dirección viene de `VIARIS_URL`. Las respuestas reales usadas como fixtures
están en `tests/fixtures/viaris/` (firmware 7.7.34).

Energía del coche verificada el 14/11/2026: la integrada por muestras desde
un registro cada 60 s dio 13.678 Wh frente a los 13.667 Wh de la sesión del
cargador (+0,08 %), y el servicio `muestreo` del NAS, con 180 muestras,
13.624 Wh (−0,31 %). La energía de la casa no es fiable durante una carga
(`homePower` = 0, ver "Potencias del modulador") y sigue como no verificada.

## Uso en el código (etapa 3, paso 3c)

El cliente añade a su lista cerrada, también por comparación exacta:

| Petición | Uso |
|----------|-----|
| `GET /modules/scheduler/elements?level=cfg` | lectura del programador (antes de reconciliar y tras cada escritura) |
| `POST /modules/scheduler/elements/mennekes` | tarea propia nueva en un id libre |
| `PUT /modules/scheduler/elements/mennekes` | tarea propia completa (activar, desactivar, mover) |
| `DELETE /modules/scheduler/elements/mennekes` | `{"tasks":[{"id":N}]}` de una tarea propia |

`ClienteViaris.escribir` valida el cuerpo antes de conectar: una tarea, id
1–10 y dentro de los ids propios que le pasa el llamador
(`planificador_tarea`), tipo 0, `active` 0/1, con fecha (`weekday` 0, `day`
y `month` con un único bit), una franja que no cruza la medianoche. Las
tareas ajenas (5 y 6 de la app) y la guardiana solo se leen; la guardiana
solo se crea, se activa o se corrige, nunca se desactiva ni se borra.
Cada escritura queda en `plan_auditoria` (cuerpo, resultado, respuesta).

En sombra no se escribe: se lee el programador y se registra en la
auditoría, con `en_seco`, lo que se habría escrito.

Verificado el 18/11/2026 (prueba 1, a mano): el `POST` mínimo que genera el
código, con fecha e inactivo,

```json
{"tasks":[{"id":10,"active":0,"user":"","group":0,"priority":1,
  "initTime":{"day":524288,"month":1024,"weekday":0,
              "timeList":[{"hourMin":180,"duration":30}]}}]}
```

queda guardado con la fecha intacta (`day` y `month` de un bit, `weekday`
0). El cargador completa el resto (`type` 0, `maxPower` -1,
`solarPriority`, `minCurrent`, `validFrom`, `validTo`, `chargeRelated` y, en
la franja, `maxPower` y `phases` a 0) y calcula `_initLocaltime` y
`_endLocaltime` (1795140000 y 1795141800: 20/11/2026 de 03:00 a 03:30 hora
de Madrid). La respuesta del `POST` es un **objeto**, no la lista que
devuelve el GET; el código no la interpreta (la guarda en la auditoría y
confirma con un GET). **La app e-VIARIS muestra una tarea con fecha como
«todos los días»**: es solo cómo la muestra. No hay que editarla desde la
app, que la convertiría en semanal.

La respuesta del `POST` es el objeto `data` del conector: claves `uid`,
`cfg` y `stat`.

Verificado el 18/11/2026 (prueba 2, a mano): **un `POST` sobre un id ya
ocupado sobrescribe la tarea sin avisar.** La tarea 10 pasó de 03:00 a
03:30 y las demás no cambiaron. Por eso el código solo hace POST sobre ids
que no están ni en la última lectura ni registrados como propios, con esa
lectura hecha justo antes y bajo `pg_advisory_lock`. Una tarea creada desde
la app en ese mismo id, entre esa lectura y el POST, se perdería; la
ventana es de segundos.

Verificado el 18/11/2026 (prueba 3, a mano): el `PUT` que genera el código
(`reconciliar.cuerpo` con la tarea leída como base y sin los campos `_`)
mueve la tarea 10 a las 04:00 con 45 min. La fecha y el resto de campos no
cambian, y el cargador recalcula `_initLocaltime` y `_endLocaltime`
(1795143600 y 1795146300: 20/11/2026 de 04:00 a 04:45 hora de Madrid).

Verificado el 18/11/2026 (prueba 4, a mano): el `DELETE` del código
(`{"tasks":[{"id":10}]}`) borra la tarea con fecha. El programador queda
idéntico a la copia tomada antes de la prueba 1 (comparado sin los campos
`_`).

Con las pruebas 1 a 4 quedan verificadas contra el cargador real las tres
escrituras tal como las genera el código. Sigue sin verificar el
comportamiento de una tarea propia **activa** escrita por el planificador
con el coche conectado: se verá en la primera noche en activo.

## Rutas que el código no debe usar

- `POST /reset/sys`: reinicio y restauración de fábrica.
- `PUT /modules/network`: configuración de red (puede dejar el cargador
  inaccesible).
- `/modules/network?level=all` y `/modules/mqtt_user?level=all`: pueden
  devolver credenciales; no registrar sus respuestas.
- Escrituras en `/modules/ocpp`, `/modules/mqtt_user`, `/modules/solar`,
  `/modules/spl` y `/modules/modbus`.

## Pendiente de verificar (con el coche conectado)

1. Qué significan los campos de `stat` del planificador (`idTask`,
   `idTaskPower`, `state`) durante una carga.
2. Cómo reacciona a una tarea **borrada** o con el **horario cambiado**
   durante una carga (desactivarla la corta: verificado el 17/11/2026).
3. Diferencia entre los estados 3 y 4 (el 3 no ha aparecido), y confirmar
   que el 5 es una pausa pedida por el coche (visto un minuto el 16/11/2026).
4. Calibración de `evsePower` frente a `totalPower` (la pinza) con la curva
   horaria de i-DE.
5. Qué mide `homePower`. Durante la carga libre del 18/11/2026 a las 08:47
   marcó 0 W cuando antes del enchufe estaba en unos 420 W, con `evsePower`
   a 4040 W. Coincide con lo visto el 14/11 (`homePower` = 0 mientras carga,
   ver "Potencias del modulador"), pero vuelve a poner en duda que sea el
   consumo de la casa. Sin resolver; no cambia nada en el código.
6. Si guardar desde la app una tarea con fecha la convierte en diaria.

Sin coche: PUT con cuerpo parcial, DELETE múltiple, `validFrom` y `validTo`,
y si `selectorPower` (4600 W) es un selector físico que limita los 32 A.
