# Fase 2: medición y carga automática del coche

Estado a 14/11/2026. Especificación funcional de la fase 2. La API del
cargador está en [viaris-api.md](viaris-api.md); el contexto del suministro y
de la fase 1, en [contexto.md](contexto.md).

## Objetivo

Que el coche se cargue solo en los tramos más baratos compatibles con el uso
previsto, sin configurar franjas a mano, y saber cuánto cuesta cada carga.

La fase se entrega en tres etapas independientes y en este orden:

1. **Medición**: energía del coche y de la casa por cuarto de hora, sesiones
   de carga y coste en el dashboard. Solo lee del cargador.
2. **Batería y previsión**: nivel de batería estimado, horarios, excepciones
   y energía necesaria para la próxima salida, con una página para
   introducirlos.
3. **Planificador**: cálculo del plan, escritura de franjas en el cargador y
   modos "cargar ahora" y "automatización en pausa".

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

**Cerrada el 14/11/2026** (v0.4.0 y v0.4.1 en producción). Pendiente
conocido: calibrar el medidor del cargador y la pinza frente a la curva
cuartohoraria de i-DE.

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
  1262,45 kWh; al coche le corresponde en torno al 84 %). **Cumplido**
  (14/11/2026, en el NAS): coche 1.061,6 kWh y 320,96 € sin impuestos
  (0,3024 €/kWh), el 84,1 % de la energía de la factura. Residual de la casa:
  200,8 kWh a 0,290 €/kWh.

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

## Etapa 2: batería y previsión

Etapa actual, especificada el 14/11/2026. Tarea y orden de entrega:
[tareas/etapa-2-bateria.md](tareas/etapa-2-bateria.md).

Entrega:

1. Dashboards separados por tema: hecho (14/11/2026).
2. Modelo y cálculo (E2.1 a E2.8 y E2.11): hecho (v0.5.0, migración 0014).
   Verificado con tests (secuencias sintéticas, historias aleatorias con el SQL
   frente a la referencia en Python y la secuencia real del 13 al 14/11).
   Pendiente de verificar en el NAS: una lectura real, una desconexión y
   reconexión reales y su reconciliación con la lectura siguiente.
   2b. Excepciones con salida o sin salida (v0.5.1, migración 0015): cambio
   de E2.7 del 15/11/2026 (formulario-maquetas.md, sección 8, punto 2).
   Verificado con tests, también la conversión de las excepciones de 0014.
3. Formulario (E2.9), en dos subpasos; maquetas y cambios al modelo en
   formulario-maquetas.md (sección 8).
   3a. Modelo (v0.5.2, migración 0016): confianza del nivel, rangos y textos
   de los parámetros, cargas externas, avisos con historial, vistas de las
   pantallas, rol `formulario_rw` y CLI `bateria carga-externa`. Verificado
   con tests (SQL frente a la referencia, también en las historias
   aleatorias con cargas externas). Pendiente: medir `recalcular_bateria`
   en el NAS con los datos reales.
   3b. Servidor y pantallas (v0.5.3): pendiente.
4. Dashboard de batería (E2.10, segunda parte): pendiente.

### Objetivo

Saber en todo momento el nivel de batería estimado del coche y la energía
necesaria para la próxima salida, con una página para el móvil donde anotar
lecturas, excepciones y horarios. Un día normal no requiere ninguna acción.

Esta etapa **no escribe en el cargador**. Los modos "cargar ahora" y
"automatización en pausa" pasan a la etapa 3.

### Decisiones tomadas

- El nivel de batería se estima con un registro de eventos; no se lee del
  coche.
- **No se supone que el coche termine al 100 %**: el planificador cortará la
  carga con precios altos. La carga completa solo es un valor absoluto cuando
  se detecta (E2.3). La lectura manual es la fuente principal de valores
  absolutos.
- Consumo en dos niveles:
  - para **estimar el nivel**, la mediana del consumo por trayecto de los
    trayectos recientes (E2.4);
  - para **planificar** (dato que usará la etapa 3), el P75 del consumo por
    día de uso de las últimas cuatro semanas (E2.7).
- Mediana y percentiles con interpolación lineal: `percentile_cont` en SQL
  (la mediana es `percentile_cont(0.5)`) y el mismo criterio en la referencia
  de Python de los tests.
- Desconexiones de menos de 30 minutos: consumo cero (parámetro).
- El cálculo vive en PostgreSQL (función de recálculo y vistas), con una
  referencia en Python puro para los tests. El formulario, el CLI y el
  muestreo llaman a la misma función.
- Formulario: HTML generado en el servidor con Python, dentro de la misma
  imagen, como tercer servicio del compose. Sin frameworks de frontend; el
  JavaScript, solo si mejora algo concreto y nunca imprescindible.
- Acceso: solo desde el tailnet, por HTTPS con el certificado de Tailscale,
  sin usuario. Nunca detrás de Tailscale Funnel.
- Grafana: un dashboard por tema, enlazados entre sí (E2.10).
- Todo parámetro vive en tabla con vigencia y origen, como en la fase 1.

### Requisitos

**E2.1 Registro de batería.** Registro de eventos con instante (UTC), tipo,
valor, origen (manual, cargador, sistema), referencia (sesión o trayecto),
nota y fecha de alta:

| Tipo | Efecto | Origen |
|------|--------|--------|
| `lectura` | Fija el nivel (valor absoluto) | Formulario o CLI, con fecha y hora editables |
| `carga_completa` | Fija el nivel en `tope_pct` × `capacidad_util_kwh` | Detectada (E2.3) |
| `sesion` | Suma la energía de la sesión × `factor_bateria` | Sesiones de la etapa 1 |
| `trayecto` | Resta el consumo del trayecto | E2.4 |

- El nivel se calcula internamente en kWh útiles; el porcentaje es
  kWh / `capacidad_util_kwh` (91,3).
- Nivel estimado = último valor absoluto + eventos posteriores, acotado entre
  0 y la capacidad. A igual instante se aplican en este orden: sesión, carga
  completa, trayecto, lectura.
- Una lectura con fecha pasada recalcula todo lo posterior. No se admiten
  lecturas con hora futura.
- Las lecturas manuales se pueden corregir o anular (borrado lógico con
  motivo), nunca se borran físicamente.
- Durante una sesión abierta, el nivel "ahora" incluye la energía ya
  entregada según el modulador (`coche_wh` de las muestras posteriores a la
  última sesión registrada y al último valor absoluto) × `factor_bateria`. Al
  cerrarse la sesión se usa el contador del cargador; se acepta el pequeño
  salto entre los dos (0,3 % medido el 14/11/2026).
- **Lectura a mitad de un evento** (el nivel se puede leer en remoto):
  - con el coche fuera, el trayecto en curso se parte por tiempo: la parte
    anterior a la lectura cuenta antes y el resto después;
  - durante una carga, la sesión se parte por la energía del modulador antes
    y después de la lectura.
- Antes de la primera lectura no hay nivel: la página lo dice y pide una.

**E2.2 Sesiones.** Cada sesión cerrada de `cargador_sesion` genera un evento
`sesion` con su energía (contador del cargador) × `factor_bateria`
(inicial 0,86, origen estimado). Idempotente: una sesión, un evento.

**E2.3 Carga completa.** Se registra `carga_completa` cuando una sesión
termina con origen `evsm` y el conector sigue conectado (estado 4 u 8) en la
primera muestra posterior, dentro de los 180 s siguientes. Una sesión que
termina con origen `schedman` es fin de franja, no carga completa (caso real:
la sesión del 14/11/2026, de 03:00 a 06:00). Si falta la muestra posterior,
no se registra el evento y el caso queda anotado como dudoso; mientras no han
pasado 180 s desde el fin, la sesión queda pendiente de evaluar. **Sin
verificar con el coche**: la regla se desactiva con `detectar_carga_completa`.

**E2.4 Trayectos.**

- Empieza en la transición de conectado (3, 4, 5, 6, 7, 8) a libre (1, 2) y
  termina en la transición inversa, según `cargador_estado`. Sin una
  transición anterior conocida no hay trayecto.
- Duración menor que `desconexion_min_s` (inicial 1800): consumo cero.
- Consumo estimado: mediana del consumo real por trayecto de los trayectos de
  los `ventana_consumo_dias` (28) anteriores a su salida, excluidos los de
  viaje largo y los cortos. Sin historia, `consumo_inicial_trayecto_kwh`
  (inicial 25, origen usuario).
- Si el día de salida tiene una excepción con consumo indicado (viaje
  largo, E2.7), sus trayectos usan
  el consumo declarado, repartido entre los trayectos no cortos de ese día en
  proporción a su duración.
- Mientras el coche está fuera, la página muestra el nivel a la salida y
  "fuera desde"; el trayecto se descuenta al reconectar.

**E2.5 Reconciliación.** Entre dos valores absolutos consecutivos A1 y A2,
el consumo real de los trayectos intermedios es
A1 + energía de las sesiones × factor − A2.

- Se reparte entre los trayectos no cortos del intervalo en proporción a su
  consumo estimado (a partes iguales si todos los estimados son 0); esos
  trayectos quedan con consumo real y origen `reconciliado`.
- Si el resultado es negativo o supera la capacidad, no se aplica: se marca
  el intervalo como incoherente y se muestra en la página.
- Un intervalo sin trayectos no cortos no tiene nada que repartir: con
  carga, la diferencia es error del factor (E2.6), no incoherencia; solo es
  incoherente si supera la capacidad o si, sin ninguna carga, el nivel sube.
- La estadística de E2.4 y E2.7 usa solo trayectos con consumo real.

**E2.6 Calibración del factor.** En un intervalo entre dos valores absolutos
sin ningún trayecto (tampoco cortos), sin huecos de muestreo y con al menos
`energia_min_calibracion_kwh` (5) cargados, factor observado = (A2 − A1) / kWh
del contador del cargador. Se guardan las observaciones y se muestra el
factor sugerido: la mediana de las últimas `observaciones_calibracion` (5).
Una observación fuera de (0, 1] no se guarda (el factor no puede ser mayor
que 1). **No se aplica solo**: el usuario lo registra como nuevo valor del
parámetro.

Hueco de muestreo (E2.6 y avisos de E2.7): tramo sin muestras de más de
`hueco_muestreo_aviso_s`. Un hueco de más de 180 s deja la muestra siguiente
sin intervalo (`intervalo_s` NULL, migración 0010); el cálculo mira esas
muestras y los dos extremos del intervalo.

**E2.7 Previsión.**

- Horario semanal: hora de salida habitual por día de la semana, o "sin
  salida".
- Excepciones con fecha. **Solo hay una excepción activa por fecha**; la baja
  es lógica.

| Tipo | Campos |
|------|--------|
| Con salida (`con_salida`) | Fecha, `hora_salida`, `consumo_modo` y `pct_objetivo` opcional (nivel mínimo a la salida, en %) |
| Sin salida (`sin_salida`) | Fecha |

- `consumo_modo` (solo con salida):
  - `normal`: consumo previsto = P75 vigente; se calcula, no se guarda.
  - `indicado`: exactamente uno de `km` o `pct` (en % de
    `capacidad_util_kwh`). Los trayectos de ese día se tratan como viaje
    largo: usan el consumo declarado y quedan fuera de la mediana (E2.4) y
    del P75.
- Sin salida solo se admite si el horario tiene salida ese día de la
  semana. Lo comprueba la capa de servicio
  (`bateria_servicio.registrar_excepcion`, que usará el formulario con un
  422), no la base de datos: un INSERT por SQL se lo salta.
- «Modificar» = baja de la anterior y alta de la nueva en la misma
  transacción (`bateria_servicio.modificar_excepcion`).
- Cambio del 15/11/2026 (migración 0015). Antes había cuatro tipos; las
  filas existentes se convirtieron así: sin_coche → sin_salida; otra_hora →
  con_salida normal; nivel_objetivo → con_salida normal con su
  `pct_objetivo`; viaje_largo → con_salida indicado con sus km o pct.

- El consumo en km se convierte con `consumo_kwh_100km` (inicial 23, origen
  usuario).
- Próxima salida: la primera según horario y excepciones a partir de ahora.
- Consumo para planificar: percentil `percentil_planificacion` (0,75) del
  consumo por **día de uso** de los últimos 28 días. Día de uso: día local con
  al menos un trayecto no corto, todos sus trayectos con consumo real y
  ninguno de viaje largo; su consumo es la suma de sus trayectos. Sin
  historia, el consumo inicial.
- Objetivo de salida = min(tope, max(reserva + consumo previsto,
  `pct_objetivo`)); consumo previsto: el percentil o, con consumo indicado,
  el declarado. Energía necesaria para la próxima salida = max(0, objetivo −
  nivel estimado). Reserva: `reserva_pct` (25). Tope:
  `tope_pct` (100). Con el coche fuera, el nivel estimado es el de la salida
  menos el consumo estimado del trayecto en curso.
- Vistas para la página, Grafana y la etapa 3: estado de la batería y
  próximas salidas (la energía necesaria, solo de la primera: la de las
  siguientes depende del plan de carga de la etapa 3).
- Avisos de la batería (`bateria_avisos()` y `v_aviso`, 0016; catálogo en
  formulario-maquetas.md, 7.7):
  - **cargador no accesible** (error): la última muestra es más antigua que
    `hueco_muestreo_aviso_s`; la página no consulta el cargador;
  - sin lecturas en los últimos `aviso_sin_lectura_dias`, solo con el nivel
    ESTIMADO;
  - **hueco de muestreo** de más de `hueco_muestreo_aviso_s` desde el último
    valor absoluto: puede esconder un trayecto, así que el nivel estimado
    queda marcado como dudoso hasta la siguiente lectura;
  - intervalo incoherente (E2.5);
  - **¿Cargaste fuera de casa?**: solo en el intervalo que termina en la
    última lectura, si no tiene cargas externas y el residuo (consumo
    estimado − consumo real) supera `umbral_carga_externa_kwh`;
  - objetivo por encima del tope, en cada salida de los próximos 7 días;
  - carga completa dudosa (sesión sin muestra posterior, E2.3);
  - factor sugerido distinto del vigente (E2.6, info).
- `aviso_historial` guarda cuándo empieza y termina cada aviso; lo mantiene
  `actualizar_avisos()`, que llaman el muestreo cada minuto y cada recálculo.
- **Confianza del nivel** (0016): `leido` si desde la última lectura no ha
  pasado nada; `leido_carga` si solo ha habido cargas (sesiones, la sesión en
  curso o cargas externas con kWh); `estimado` con trayectos no cortos desde
  entonces, con el coche fuera, si el último valor absoluto es una carga
  completa (regla sin verificar) o si no hay lecturas (nivel NULL).
- **Cargas externas** (0016): evento `carga_externa` (orden 4; la lectura
  pasa a 5) que parte el trayecto por tiempo. Con kWh suma kWh ×
  `factor_bateria` y entra en la reconciliación como una sesión más; sin kWh
  no suma y el intervalo queda en `carga_externa_sin_kwh`: no se reparte ni se
  marca incoherente, y sus trayectos quedan sin reconciliar (fuera de la
  mediana y del P75). Un intervalo con cargas externas no sirve para
  calibrar. Solo se admite un instante en que el coche estaba desconectado
  (último estado del conector libre).

**E2.8 Parámetros.** Tabla `bateria_parametro` con vigencia, origen, fuente y
nota, siguiendo el patrón de `cargador_parametro`. Origen: contrato,
regulado, estimado, usuario o ficha_tecnica. Los booleanos se guardan como
0/1, con un CHECK por clave.

| Clave | Inicial | Origen |
|-------|---------|--------|
| `capacidad_util_kwh` | 91,3 | ficha_tecnica |
| `reserva_pct` | 25 | usuario |
| `tope_pct` | 100 | usuario |
| `factor_bateria` | 0,86 | estimado |
| `percentil_planificacion` | 0,75 | usuario |
| `ventana_consumo_dias` | 28 | usuario |
| `desconexion_min_s` | 1800 | usuario |
| `consumo_inicial_trayecto_kwh` | 25 | usuario |
| `consumo_kwh_100km` | 23 | usuario |
| `energia_min_calibracion_kwh` | 5 | usuario |
| `observaciones_calibracion` | 5 | usuario |
| `detectar_carga_completa` | 1 (sí) | usuario |
| `aviso_sin_lectura_dias` | 7 | usuario |
| `hueco_muestreo_aviso_s` | 1800 | usuario |

**E2.9 Formulario.**

- Servicio `formulario` en el compose, misma imagen, con `container_name`
  fijo (lo usa el script de renovación del certificado del NAS).
- HTTPS con `ssl` de la biblioteca estándar, como Miniflux: `server.crt` y
  `server.key` en `/volume1/docker/energia-monitor/certs`, montado en solo
  lectura; el directorio no va al repositorio. Puerto `8421:8443` enlazado a
  la IP del NAS en el tailnet (`FORMULARIO_BIND`, 100.102.118.77):
  https://rdv-nas.tail1aac93.ts.net:8421/. Arranque del NAS y renovación, en
  deploy/README.md.
- Rol de PostgreSQL propio, con escritura solo en las tablas que la página
  necesita (lecturas, excepciones, horario y nuevos valores de parámetros),
  sin DELETE, y permiso para ejecutar el recálculo.
- Formularios clásicos con POST y redirección posterior (PRG); validación en
  el servidor; horas introducidas y mostradas en Europe/Madrid, guardadas en
  UTC; uso cómodo con una mano en el móvil.
- Se rechazan las peticiones con otro `Host` y los POST de otro origen.
  `/salud` (healthcheck) queda fuera de la comprobación de `Host` y responde
  con el certificado real.
- Pantallas:
  1. **Inicio**: nivel estimado (% y kWh), antigüedad del último valor
     absoluto y trayectos estimados desde entonces, estado del coche (en
     casa, cargando o fuera desde), próxima salida y energía necesaria,
     avisos (E2.7) y la **lectura rápida**: porcentaje, con la hora actual
     por defecto.
  2. **Excepciones**: próximas, alta y baja.
  3. **Horario**: hora de salida por día de la semana.
  4. **Historial**: lecturas con estimado frente a leído; corregir o anular.
  5. **Parámetros**: valores vigentes y registro de un valor nuevo desde
     hoy, con las mismas reglas que el CLI.
- El diseño visual se fija con maquetas aprobadas antes de implementar las
  pantallas (docs/formulario-maquetas.md).

**E2.10 Grafana.**

- El dashboard único se divide: "Energía: precios" conserva el `uid`
  `energia-monitor` y la fila "Coche" pasa a "Energía: coche"
  (`energia-coche`). Hecho el 14/11/2026 (paso 1 de la tarea).
- Nuevo "Energía: batería": nivel estimado en el tiempo con las lecturas como
  puntos, eventos como anotaciones, acierto de la estimación (estimado frente
  a leído en cada lectura), próxima salida y energía necesaria, y trayectos
  recientes con su consumo y origen.
- Los tres llevan la etiqueta `energia` y un enlace de tipo dashboards por esa
  etiqueta, con `keepTime` e `includeVars`. Un JSON por dashboard en
  `grafana/`.
- `grafana_ro` puede leer todas las tablas y vistas `bateria_*` y
  `v_bateria_*`, pero no ejecutar el recálculo.

**E2.11 CLI.** `bateria estado`, `bateria lectura --pct N [--instante ...]`,
`bateria recalcular` y `parametro registrar|listar --ambito bateria`
(docs/parametros.md).

- `bateria recalcular` no tiene `--desde`: el recálculo siempre rehace todo
  desde las tablas de origen, porque una lectura dentro de una sesión o de un
  trayecto cambia también el intervalo anterior. Con un año de datos
  sintéticos tarda unos 0,6 s en local.
- `bateria lectura`: hora de Madrid; una hora que no existe (cambio de marzo)
  se rechaza, una ambigua (cambio de octubre) se toma como la primera, y no se
  admiten lecturas futuras ni dos lecturas activas en el mismo instante.
- Los parámetros de batería tienen vigencia desde el 1/1/2025, antes de
  cualquier sesión del cargador.

### Criterios de aceptación

- Tests del cálculo del nivel con secuencias sintéticas: lectura retroactiva,
  reconciliación con varios trayectos, desconexión corta, intervalo
  incoherente, sesión abierta y lectura a mitad de un trayecto y de una carga.
- Test de detección de trayectos y de fin de franja frente a carga completa
  con la secuencia real del 13 al 14/11/2026
  (`tests/fixtures/viaris/muestras-2026-11-13.log` y la sesión del 14/11, que
  termina con `schedman`).
- Tests de la mediana, el percentil y la calibración, con referencia en
  Python puro y cálculo en SQL coincidentes.
- Tests de la página con un cliente de pruebas: validación, PRG, zona horaria
  y permisos del rol (no puede escribir fuera de sus tablas).
- Test de los permisos de `grafana_ro` sobre las tablas y vistas de batería.
- El cliente del cargador sigue admitiendo solo sus GET.
- Migraciones nuevas desde la 0014; ninguna publicada se modifica.
- Páginas por debajo de 300 ms y paneles por debajo de 1 s en el NAS.

### Pendiente de verificar con el coche

- La regla de carga completa (E2.3).
- El estado del conector cuando el coche corta la carga por estar lleno (4 u
  8).

## Etapa 3: planificador

Se detalla al cerrar la etapa 2 y tras las pruebas de escritura pendientes.

### Modos

"Cargar ahora" (sin restricción de precio, hasta un nivel o una hora, y vuelta
automática al modo normal; queda registrado con su coste) y "automatización
en pausa" (el sistema no escribe en el cargador). Pasan aquí desde la etapa 2
porque solo tienen sentido cuando el sistema escribe franjas.

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
