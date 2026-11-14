# Fase 2: medición y carga automática del coche

Estado a 13/11/2026. Especificación funcional de la fase 2. La API del
cargador está en [viaris-api.md](viaris-api.md); el contexto del suministro y
de la fase 1, en [contexto.md](contexto.md).

## Objetivo

Que el coche se cargue solo en los tramos más baratos compatibles con el uso
previsto, sin configurar franjas a mano, y saber cuánto cuesta cada carga.

La fase se entrega en tres etapas independientes y en este orden:

1. **Medición**: energía del coche y de la casa por cuarto de hora, sesiones
   de carga y coste en el dashboard. Solo lee del cargador.
2. **Batería y formulario**: nivel de batería estimado, horarios, excepciones
   y modos, con una página para introducirlos.
3. **Planificador**: cálculo del plan y escritura de franjas en el cargador.

Cada etapa se publica con su tag y queda en producción antes de empezar la
siguiente.

## Datos de partida

| Dato | Valor | Origen |
|------|-------|--------|
| Coche | BYD Sealion 7 Excellence, batería LFP | Usuario |
| Capacidad útil | 91,3 kWh (93,0 kWh brutos); 1 % = 0,913 kWh | Ficha técnica |
| Tope de carga | 100 % (sin límite configurado en el coche) | Usuario |
| Cargador | Orbis VIARIS UNI monofásico, 192.168.1.34, API HTTP local sin autenticación | Verificado |
| Red | El NAS y el cargador están en la misma LAN (192.168.1.0/24) | Verificado |
| Límite de red | 4500 W para toda la vivienda (modulador con pinza) | Verificado |
| Potencia de carga real | unos 4,0 a 4,5 kW (20 A), no 3,7 kW | Histórico de sesiones |
| Energía del coche | unos 1060 kWh en octubre de 2026 (84 % del consumo); 34 kWh/día de media | Factura e histórico |
| Energía por noche de carga | mediana 44 kWh, máximo 83 kWh, muy irregular por día de la semana | Histórico |
| Horarios habituales | enchufa entre las 19 y las 23 h; sale hacia las 9 o 10 h (viernes, hacia las 7 h) | Histórico |
| Contador del cargador | marca en torno a un 4 % más que el de la distribuidora (estimación, entre 0 y 5 %) | Factura de octubre |
| Días de mucho uso | el usuario los conoce con antelación | Usuario |

## Decisiones tomadas

- El cargador se controla por su API HTTP local escribiendo franjas en su
  planificador. No se usan MQTT ni OCPP, ni la nube de BYD.
- El plan diario se escribe como franjas con fecha concreta (`day`, `month`).
  El cargador las ejecuta solo aunque el NAS no esté disponible.
- El planificador solo crea, modifica y borra las franjas que él ha creado.
  Nunca toca las del usuario.
- El nivel de batería no se lee del coche: se estima con un registro de
  eventos y se corrige con lecturas manuales.
- Reserva mínima al salir: 25 %. Tope: 100 %.
- El perfil semanal guarda solo horarios de salida. El consumo previsto lo
  calcula el sistema a partir del uso reciente; los días especiales se
  declaran como excepción.
- Los umbrales de precio son percentiles de una ventana móvil, no valores
  fijos en €/kWh.
- Todo parámetro vive en tabla con vigencia, como en la fase 1.

## Etapa 1: medición

### Requisitos

**E1.1 Cliente del cargador.** Módulo con las lecturas de viaris-api.md. Lista
cerrada de rutas y métodos permitidos, comprobada en el propio cliente: en
esta etapa, solo los GET que usa el código (modulador, evsm e histórico). Tiempo de espera de 5 s. Dirección del cargador por
variable de entorno (`VIARIS_URL`); sin ella, la funcionalidad queda
desactivada y el resto del colector no cambia.

**E1.2 Muestreo.** Cada 60 s se leen `GET /modules/modulator?level=stat`
(`evsePower`, `homePower`, `totalPower`) y `GET /modules/evsm/elements`
(`stat.state`). Cada muestra se guarda con su instante en UTC y con el
intervalo en segundos desde la muestra anterior, y cubre ese intervalo a la
potencia leída. Si han pasado más de 180 s (o es la primera muestra), el
intervalo es NULL y la muestra no cuenta energía: el hueco no se rellena. El muestreo corre como proceso continuo, separado de la
descarga diaria de precios (segundo servicio del compose con la misma imagen).

**E1.3 Energía por tramo.** Energía por cuarto de hora del coche, de la casa
y total (Wh = suma de potencia × intervalo / 3600) y los segundos cubiertos.
Un tramo con menos de 840 s cubiertos se marca como incompleto. Desde la
migración 0013 se guarda en la tabla `energia_tramo`: el muestreo, en la
misma transacción que guarda cada muestra, recalcula desde `cargador_muestra`
los tramos que esa muestra cubre (uno, o dos si cruza el cuarto de hora), así
que repetir una muestra no duplica energía. Tras insertar o borrar muestras
por SQL hay que ejecutar `cargador recalcular --desde AAAA-MM-DD`;
`cargador estado` compara los dos últimos días con el cálculo desde las
muestras, avisa si difieren y devuelve 1. En los tramos con alguna muestra de
carga (`evse_w` > 0) la energía de la casa es desconocida (NULL, no 0); la
del coche y la total no cambian. La casa no se calcula como total − coche:
el medidor del cargador y la pinza no cuadran y saldría negativa.

**E1.4 Sesiones.** Cada 15 min se lee `GET /modules/modulator/historic` y se
insertan de forma idempotente las sesiones (identificador, conector, inicio,
fin, Wh, origen de inicio y de fin, línea original). El búfer del cargador
solo guarda 100.

**E1.5 Cambios de estado.** Se registra cada cambio de `stat.state` con su
instante, para detectar conexión, inicio y fin de carga y desconexión.

**E1.6 Coste.** Vista que cruza la energía del coche por tramo con
`v_precio_final` del contrato: energía corregida por el factor del contador
(parámetro con vigencia, valor inicial 0,96, origen estimado) × precio final
del tramo. Columnas: energía, coste, periodo tarifario, coste equivalente con
el PVPC, e indicadores de dato estimado (SSAA no real, tramo incompleto,
origen de la energía). Lo mismo para la casa, sin factor (el factor se
calibró con el contador del cargador; la casa la mide la pinza). La energía
de la casa queda sin verificar: el 14/11/2026 se comprobó que totalPower no
es evsePower + homePower (homePower = max(0, total − evse), 0 durante la
carga), así que durante una carga la casa es desconocida (docs/viaris-api.md):
esos tramos no tienen fila de casa en `v_coste_tramo` (0013).
El factor y la potencia de E1.7 están en
`cargador_parametro`, no en `contrato_parametro`: allí `v_precio_final`
marcaría como estimado todo tramo por un parámetro que no interviene en el
precio. Coste por sesión: las muestras con instante dentro de [inicio, fin),
cada una al precio de su tramo (varias sesiones pueden compartir un cuarto
de hora); las sesiones sin muestras, con su reparto de E1.7.

**E1.7 Histórico anterior al muestreo.** Las sesiones sin ninguna muestra
entre su inicio y su fin se reparten por tramos suponiendo carga continua
desde el inicio a `potencia_max_estimacion_w` (parámetro del cargador con
vigencia; inicial 4500 W, origen estimado), con origen `estimado_sesion`. No se
usa la media de las muestras: la estimación debe ser reproducible. Si a esa
potencia la energía no cabe en la sesión, se reparte de forma uniforme entre
el inicio y el fin. Nunca sustituyen a un tramo medido: un tramo con alguna
muestra que lo cubra es medido y la estimación no entra.

**E1.8 Dashboard.** Fila nueva "Coche" en el dashboard existente:

| Panel | Contenido |
|-------|-----------|
| Gasto del coche | Euros y kWh de hoy, semana en curso y mes en curso |
| Gasto en el rango | Euros, kWh y €/kWh medio del rango del selector de Grafana |
| Precio medio pagado | €/kWh del coche frente a la media simple del mismo rango |
| Gasto diario | Barras de 30 días con euros y kWh |
| Reparto por periodo | kWh y euros en P1, P2 y P3 |
| Frente al PVPC | Coste real y coste con PVPC de la misma energía |
| Estado del cargador | Estado del conector, potencia del coche y de la casa, última muestra |
| Sesiones recientes | Inicio, fin, kWh, coste y €/kWh |

Los valores estimados se distinguen de los medidos.

**E1.9 Resiliencia.** Si el cargador no responde, se registra el error y se
reintenta en el siguiente ciclo, sin detener el proceso ni afectar a la
descarga de precios.

### Criterios de aceptación

- Tests con respuestas reales del cargador guardadas como fixtures (las de
  viaris-api.md); el CI no llama al cargador.
- Test que demuestra que el cliente rechaza cualquier método distinto de GET y
  cualquier ruta fuera de la lista.
- Test de energía por tramo con muestras sintéticas, incluidos huecos y cambio
  de hora.
- Test de coste con precios y energía sintéticos, en `numeric`.
- Migraciones nuevas desde la 0010; ninguna publicada se modifica.
- `grafana_ro` puede leer las tablas y vistas nuevas.
- Comprobación manual: el coste estimado del coche en octubre de 2026 es
  coherente con la factura (término de energía 379,10 € sin impuestos para
  1262,45 kWh; al coche le corresponde en torno al 84 %).

### Verificado con la carga del 14/11/2026

- `stat.state`: 1 libre (`event` 2), 4 conectado sin cargar (`event` 7), 6
  cargando (`event` 6); `localtime` es el instante del último cambio.
- Energía del coche: 13.678 Wh integrados por muestras frente a 13.667 Wh de
  la sesión del cargador (+0,08 %); el servicio `muestreo` del NAS, 180
  muestras y 13.624 Wh (−0,31 %). Criterio de E1 (2 %) cumplido.
- `totalPower` ≠ `evsePower` + `homePower`: durante la carga `homePower` = 0 y
  `totalPower` queda de media 218 W por debajo de `evsePower`. La casa no es
  medible durante la carga y sigue sin verificar. Desde la migración 0013,
  los tramos con alguna muestra de carga tienen la casa como desconocida
  (NULL), no como 0; `total_wh` se mantiene.
- Huecos de unos 70 s cada ~2 h (el cargador no responde en 5 s), dentro del
  límite de 180 s.

### Pendiente de verificar con el coche conectado

- Estados 3 y 5 (no han aparecido) y su diferencia con 4 y 6.
- Calibración del medidor del cargador frente a la pinza con la curva de i-DE.
- Si existe una lectura HTTP del contador acumulado de energía (el WebSocket
  lo da en `modulator_processes`). Si existe, la energía por tramo pasará a
  calcularse por diferencia de contador; el diseño debe admitir ese origen.

## Etapa 2: batería y formulario

Se detalla al cerrar la etapa 1. Requisitos funcionales ya acordados:

**Registro de batería.** Tabla de eventos con instante, tipo y valor:

| Evento | Efecto | Origen |
|--------|--------|--------|
| Lectura manual | Fija el nivel (absoluto) | Formulario, con fecha y hora editables |
| Carga completa | Fija el nivel en el tope | Cargador: deja de cargar y sigue conectado |
| Sesión de carga cerrada | Suma energía × factor de conversión | Sesiones de la etapa 1 |
| Trayecto | Resta el consumo previsto o declarado | Entre desconexión y reconexión |

El nivel estimado es el último valor absoluto más los eventos posteriores. Una
lectura con fecha pasada recalcula lo posterior. Factor de conversión del
contador del cargador a batería: inicial 0,86, calibrado con pares de lecturas
manuales. Se muestra siempre la antigüedad del último valor absoluto.

**Horarios.** Hora de salida habitual por día de la semana.

**Consumo previsto.** Percentil alto del consumo diario de las últimas cuatro
semanas (parámetro; valor inicial por decidir entre 75 y 90).

**Excepciones con fecha.** Día sin coche, salida a otra hora, nivel objetivo
concreto a una hora, viaje largo.

**Modos.** "Cargar ahora" (sin restricción de precio, hasta un nivel o una
hora, y vuelta automática al modo normal; queda registrado con su coste) y
"automatización en pausa" (el sistema no escribe en el cargador).

**Formulario.** Página web para móvil servida desde el NAS dentro del tailnet:
nivel estimado y su antigüedad, lectura de batería, excepciones, horarios,
modos y parámetros. Un día normal no requiere ninguna acción.

## Etapa 3: planificador

Se detalla al cerrar la etapa 2 y tras las pruebas de escritura pendientes.

### Cálculo

1. Tramos de 15 min con precio publicado en los que se prevé el coche en casa.
2. Energía obligatoria por cada salida prevista: la necesaria para salir con
   reserva + consumo previsto (o el nivel de la excepción).
3. Selección de los tramos más baratos antes de cada salida hasta cubrirla,
   con un margen del 10 % y la potencia de planificación (parámetro, 4,0 kW).
4. Energía oportunista: tramos por debajo del umbral de precio, hasta el nivel
   que corresponda.
5. Agrupación en bloques contiguos (mínimo 30 min, máximo 6 bloques).
6. Escritura de las franjas y borrado de las caducadas.
7. Recálculo periódico con la energía realmente entregada.

### Umbrales

Referencia: precio final de los últimos 14 días en las horas en que el coche
suele estar en casa.

| Precio del tramo | Nivel hasta el que se carga |
|------------------|-----------------------------|
| Por debajo del percentil 30 | Tope (100 %) |
| Entre los percentiles 30 y 70 | Intermedio (70 %) |
| Por encima del percentil 70 | Solo lo obligatorio |

Todos los valores son parámetros con vigencia.

### Puesta en marcha

Primero en **modo sombra**: el plan se calcula, se guarda y se muestra en el
dashboard junto a lo que realmente ocurrió, sin escribir en el cargador. La
escritura se activa después con una variable de entorno.

Métrica de resultado: ahorro frente a cargar al enchufar a potencia máxima.

### Pendiente de verificar con el coche conectado

1. Que una franja activa con fecha concreta inicia y detiene la carga.
2. Qué hace el cargador sin ninguna franja activa (`defaultState`).
3. Cómo reacciona a una franja creada, modificada o borrada durante una carga.
4. Franja de respaldo: una recurrente siempre activa garantiza carga si el NAS
   falla, pero impide saltarse días caros. Decisión abierta.

## Fuera del alcance de la fase 2

- Suministro del local (3.0TD).
- Lectura del nivel de batería desde la nube de BYD.
- Cambios de configuración eléctrica del cargador (ampacidad, límites, red).

## Asuntos externos al código

- Subir la potencia de valle (P2) a 7,4 kW: el término de potencia de valle
  cuesta 0,001988 €/kW·día (unos 0,92 €/kW·año con impuestos). Depende de la
  potencia máxima admisible, del cableado y de `selectorPower` del cargador.
- La factura ya supera la potencia contratada: máximas de 5,13 kW (P1) y
  5,99 kW (P2) con 4,6 kW contratados.
- Importar la curva cuartohoraria de i-DE para afinar el histórico y calibrar
  el factor del contador.
- Decidir entre el contrato indexado y el PVPC con la comparación ponderada
  por consumo que dará la etapa 1.
