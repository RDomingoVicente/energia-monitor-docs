# Fase 2: medición y carga automática del coche

Estado a 18/11/2026. Especificación funcional de la fase 2. La API del
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
3. **Planificador**: cálculo del plan, escritura de franjas en el cargador,
   modos sombra, activo y pausa, y la acción "cargar ahora".

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
- El umbral de precio barato es relativo (un porcentaje de la media del
  precio final de los últimos 7 días), no un valor fijo en €/kWh (E3.4).
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
   Medido en el NAS el 15/11/2026 con los datos reales (103 eventos):
   `recalcular_bateria` 44–52 ms, `v_bateria_estado` 12 ms, `v_aviso` 7 ms,
   `actualizar_avisos()` 56 ms (330 ms la primera vez, al abrir 43 avisos).
   v0.5.3 (migración 0017): cierre `sin_muestreo` para las sesiones
   anteriores al muestreo (E2.3), que daban 42 falsos avisos de carga
   completa dudosa en el NAS.
   3b. Servidor y pantallas, en dos entregas:
   - v0.5.4: servicio `formulario` (HTTPS con `ssl`, recarga del
     certificado, `Host` y origen comprobados, CSP `default-src 'self'`),
     `/salud` y healthcheck que verifica el certificado real, Inicio con la
     lectura (PRG: el 303 lleva solo `?ok=lectura&id=N` y el mensaje sale de
     la base de datos), compose y deploy/README.md (certificados, arranque
     tras Tailscale y ACL). Verificado con tests (también TLS con certificado
     caducado, de otro nombre o ausente) y con la imagen contra PostgreSQL en
     local. Desplegada en el NAS y verificada desde el móvil (15/11/2026).
   - v0.5.5: resto de pantallas (Excepciones, Historial, Carga externa,
     Horario, Parámetros, Avisos y Ayuda), todas con PRG (303 con
     `?ok=código&id`) y 422 con los mensajes de validación; corregir y
     anular lecturas y cargas externas, guardar el horario y registrar
     parámetros y la calibración sugerida pasan por `bateria_servicio`.
     Verificado con tests de integración contra PostgreSQL (cada
     formulario, validaciones, contador de avisos, búsqueda en la Ayuda) y
     con Playwright en local (`tests/ui/Dockerfile`, fuera de la CI):
     ningún elemento sale de su contenedor en 1280×900 ni en 390×844 en 15
     vistas, sin errores de consola ni recursos bloqueados por la CSP.
     Capturas junto a las maquetas con el stage `capturas` del mismo
     Dockerfile. Desplegada en el NAS el 15/11/2026.
   - v0.5.6: Ayuda sin duplicar en escritorio (y el bloque Coche/Necesita
     de Inicio solo en móvil), test de visibilidad por tamaño, logo y
     favicon (formulario-maquetas.md, 3) y caja «Modelo» en el diagrama de
     la Ayuda. Pendiente: desplegar en el NAS.
   - v0.5.7 (migración 0018), por dos fallos vistos en Inicio con los datos
     del 15-16/11: un trayecto partido por la primera lectura (tomada con el
     coche fuera) salía como «trayecto est. −31,7 kWh» sin decir que 11,0
     kWh eran reales; ahora «(11,0 reales)», con `real_kwh` en
     `v_bateria_evento_reciente` y la columna «Reales» en «Trayectos
     recientes» de Grafana (bateria_trayecto y la estadística no cambian).
     Y «Conectado desde» mostraba el fin de la carga: ahora conectado desde,
     sin cargar desde y cargando desde, de `cargador_estado`. Verificado con
     tests (el caso del NAS, trayecto reconciliado entero, tramo en
     intervalo incoherente, secuencia de estados con la pausa 5).
     Pendiente: desplegar en el NAS.
   - v0.5.8 (migración 0019): en «Últimos eventos», las cargas mostraban la
     energía a batería sin decirlo. Ahora primero la medida y entre
     paréntesis la estimada a batería: «carga 18,3 kWh (15,3 a batería)» y
     «carga externa 30,0 kWh (25,8 a batería)» (sin kWh, «carga externa sin
     kWh»), con `medido_kwh` en `v_bateria_evento_reciente`. El panel
     Batería y la casilla LEÍDO + CARGA dicen «+19,3 kWh a batería». Igual en
     la anotación «Cargas en casa» de Grafana. Pendiente: desplegar en el
     NAS.
4. Dashboard de batería (E2.10, segunda parte), sin migraciones ni imagen:
   `grafana/energia-bateria.json` (`uid` `energia-bateria`, el del enlace
   del formulario) con nivel ahora, próxima salida, objetivo, energía
   necesaria y avisos; nivel en 7 días (reconciliado y previsto, lecturas
   como puntos, sesiones y trayectos como regiones, cargas externas y
   completas como puntos); acierto de la estimación; próximas salidas;
   trayectos recientes, y cargas externas. «Energía: coche» separa casa y
   fuera (E2.9, sección 8, punto 7 de formulario-maquetas.md), con el €/kWh
   de cada uno aparte. Verificado con tests: cada consulta como
   `grafana_ro` contra un historial con lectura, carga, trayecto y carga
   externa, y las cifras frente a la página Historial y las vistas.
   Importado en el NAS y aceptado el 16/11/2026: navegación entre los tres
   dashboards con el desplegable «Energía» (en batería, los botones sueltos
   no cabían junto a los interruptores de las anotaciones), regiones de
   cargas y trayectos, colores de la confianza y casa · fuera.

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
no se registra el evento y el caso queda anotado como dudoso, salvo que la
sesión termine antes de la primera muestra del cargador: entonces no había
muestreo y queda como `sin_muestreo`, sin aviso (0017; caso real: las 42
sesiones del histórico entre el 21/09 y el 12/11/2026). Mientras no han
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
  - carga completa dudosa (sesión sin muestra posterior con el muestreo ya en
    marcha, E2.3);
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

## Etapa 3: planificador de carga

Etapa actual, especificada el 18/11/2026 (antes docs/fase2-etapa3.md, revisada
y aprobada), con las pruebas del cargador del 17 y el 18/11/2026. Sustituye al
esbozo anterior de esta sección (umbrales por percentiles, franja de respaldo
recurrente y escritura activada por variable de entorno). Las aclaraciones del
plan del paso 3a (18/11/2026) están incorporadas en los requisitos y en
«Decisiones del paso 3a». Tarea y orden de entrega:
[tareas/etapa-3-planificador.md](tareas/etapa-3-planificador.md).

Entrega (E3.11):

| Paso | Contenido | Escribe en el cargador |
|---|---|---|
| 3.0 | Pruebas de E3.1 (hechas el 17 y 18/11/2026) | Sí, a mano, una a una |
| 3a | Modelo, disponibilidad, algoritmo, modo sombra, CLI (`planificador plan`, `planificador simular` sobre un día pasado), tests | No |
| 3b | Pantalla Plan de carga y panel de Inicio (sombra: solo lectura) | No |
| 3c | Escritura y reconciliación, franjas manuales, Detener, modo activo, auditoría | Sí |
| 3d | Aviso por correo, avisos nuevos, Grafana | — |

Entre 3b y la activación, en lugar de una semana en sombra (cambio del
18/11/2026): una simulación en seco de 3c en el NAS. La v0.7.0 en modo sombra
lee el programador y registra en la auditoría las escrituras que haría, sin
hacerlas, durante una o dos noches que cubran al menos una franja partida a
medianoche, la publicación de los precios del día siguiente con su recálculo
y una desconexión y reconexión del coche. Después, las pruebas de escritura a
mano y la activación.

Calendario (decisión del 18/11/2026, al revisar la segunda entrega de 3c):
además de la simulación en seco, **al menos una semana en sombra**. La
sombra empezó el 18/11 con v0.6.0: no se activa antes del 25/11/2026, y solo
tras revisar `v_plan_ahorro` de esa semana. Hasta entonces el usuario no
desactiva sus tareas 5 y 6 ni pasa a activo. La simulación en seco y las
pruebas de escritura a mano (una a una y en horario barato) sí pueden ir
antes.

1. Paso 3.0, pruebas del cargador: hecho (17 y 18/11/2026; E3.1 y
   viaris-api.md).
2. Paso 3a (v0.6.0, plan aprobado el 18/11/2026; algoritmo y simular dados
   por buenos el 18/11): desplegado en el NAS y aceptado el 18/11/2026 con
   las consultas de docs/tareas/etapa-3-planificador.md. En el despliegue se
   corrigió la colación de `postgres` y `template1` (deploy/README.md).
   Primera entrega:
   migración 0020 (parámetros agrupados y claves nuevas, `planificador_modo`,
   `planificador_config`, tablas del plan con los choques con tareas ajenas,
   solicitud y ciclo, entradas en
   SQL y vistas `v_plan_franja`, `v_plan_vigente`, `v_plan_efectivo` y
   `v_plan_ahorro`), referencia en Python del ahorro
   (`plan_carga/ahorro.py`) y Parámetros ordenada por grupo, con
   `id_tarea_guardiana` no editable y las horas en HH:MM (guardadas en
   minutos). La 0020 no se aplica en el NAS hasta cerrar 3a (v0.6.0);
   mientras, se puede corregir. Verificado con tests (ahorro SQL frente
   a Python en la noche del cambio de hora y en 12 casos aleatorios).
   Segunda entrega: algoritmo y franjas como funciones puras
   (`plan_carga/`), con búsqueda exhaustiva e invariantes aleatorios.
   Tercera entrega: servicio (`plan_carga/servicio.py`: entrada desde la
   base de datos, plan guardado), ciclo al final de cada ciclo del muestreo
   (cada 15 minutos, en cada cambio de estado o sesión nueva y con cada
   solicitud; un fallo queda en `planificador_ciclo` y el evento se
   reintenta), solicitudes de recálculo (`bateria_servicio.recalcular`, es
   decir, lecturas, excepciones, horario, cargas externas y parámetros, y
   `db.refrescar_precio_final`, precios nuevos) y CLI `planificador plan` y
   `planificador simular`. Probado con un volcado de producción
   restaurado en local (noches del 14 al 18/11). Medido en local: lectura de la entrada 0,12 s y
   algoritmo 0,03 s con 72 h de horizonte y 3 salidas; 0,15 s en un caso de
   estrés de 288 cuartos disponibles.
3. Paso 3b (v0.6.1, migración 0021), solo lectura: pantalla Plan de carga
   (`/plan`, 7.9 de formulario-maquetas.md) con panel de control, línea de
   tiempo de 60 h (24 h desplazable en móvil), franjas con su desglose por
   nivel y estados de sombra, últimas franjas de 7 días y ahorro (hoy, 7 y
   30 días, con la columna «Plan» en sombra); «Próximas cargas» en Inicio
   (con `SOMBRA`) y «Historial de lecturas →» en móvil; Parámetros rehecha
   por grupos (índice, grupo 6 plegado, calibración en el grupo 1, «solo
   CLI», notas de una línea); Ayuda con Parámetros por grupos y la sección
   «Plan de carga»; navegación nueva. Las acciones que escriben aparecen
   desactivadas con «disponible en el paso 3c». La 0021 añade
   `plan_ahorro(desde, hasta)` (con la conexión abierta; `v_plan_ahorro`
   pasa a usarla), `plan_franjas_pasadas(desde, hasta)` y
   `bateria_parametro_clave.nota`. Verificado con tests (pantallas contra
   PostgreSQL con `formulario_rw`, `/plan` por debajo de 300 ms) y con
   Playwright en 1280×900 y 390×844. Desplegado el 18/11/2026.
4. Paso 3c (v0.7.0, migración 0022): ver «Implementación del paso 3c».
   Pendiente: simulación en seco en el NAS, pruebas de escritura a mano,
   tareas 5 y 6 desactivadas en la app y activación.

### Objetivo

Programar automáticamente las franjas de carga del Viaris para que el coche
salga con el objetivo cubierto pagando lo mínimo, y cargar de más cuando la
energía está barata.

### Principios

- Determinista y explicable: cada franja tiene un motivo visible.
- El cargador sigue siendo la única protección eléctrica (modulador y 20 A).
- Si el sistema falla, el riesgo es no cargar, nunca cargar caro. El
  cargador solo bloquea fuera de las franjas mientras haya **al menos una
  tarea activa**; sin ninguna, carga libremente (E3.1). Por eso existe
  siempre una tarea guardiana activa (E3.6).
- Tú mandas: cualquier franja se puede tocar desde el formulario y el
  planificador lo respeta.
- Puesta en marcha gradual: modo sombra antes de escribir en el cargador.

### Requisitos

**E3.1 Hechos verificados del cargador (15–18/11/2026).** Detalle y
procedimiento en viaris-api.md.

| Hecho | Fuente |
|---|---|
| Con al menos una tarea activa, el coche enchufado no carga fuera de las tareas (`defaultState: 1`) | 15/11 y 18/11 |
| **Sin ninguna tarea activa, el cargador carga libremente** en cuanto el coche está enchufado | 17/11 20:16 (sesión de 53 s al desactivar todas) y prueba del 18/11 08:47 (estado 6, 4040 W) |
| Una única tarea activa con fecha de otro día (31/12 00:00, 1 min) basta para bloquear la carga; al reactivarla durante una carga libre, la corta | prueba del 18/11 08:45–08:48 |
| La tarea se cumple al segundo: inicio 02:00:01, fin 06:00:01 | sesión del 16/11 |
| Desactivar una tarea en curso (`active: 0`) corta la carga | prueba con la app de Orbis, 17/11; es la misma operación que hará el planificador |
| Cuando el coche se llena deja de entrar energía y la franja sigue activa; el coche avisa de fin de carga | usuario |
| Potencia real ≈ 4,5 kW constante (20 A en el cargador y potencia contratada 4,6 kW) | sesiones del 15 y 16/11 |
| El modulador prioriza la vivienda y corta la carga si se supera la potencia contratada | configuración del usuario |
| Estado 5 durante la carga: pausa (probablemente pedida por el coche); la franja sigue activa | 16/11 05:06:58 |
| `localtime` de `/modules/evsm/elements` es el último cambio de evento, no la hora de conexión | 15/11 |
| Tareas: `hourMin` y `duration` en minutos; `weekday` máscara (bit 0 = domingo, 0 = todos); máscaras de día y mes sin año; máximo 10 tareas | etapa 1 |
| La app de Orbis solo crea tareas semanales y parte en la medianoche las franjas que la cruzan (23:00 + 59 min y 00:00 + 480 min, con un minuto sin carga entre ambas) | 17/11 |
| La API acepta tareas con fecha (`day` y `month`, `weekday` 0) y calcula bien su ocurrencia; con `hourMin` 1380 y `duration` 540 el fin calculado es las 08:00 del día siguiente | 17/11 (`_initLocaltime`, `_endLocaltime`) |
| La app muestra una tarea con fecha como «todos los días»; si se guarda desde la app, probablemente pierde la fecha | 17/11 |
| El cargador **no impide solapes**: acepta y activa dos tareas activas que se pisan, sin error (HTTP 200) | 17/11, tarea 9 sobre las tareas 5 y 6 |
| Una tarea con fecha se ejecuta ese día | 17/11, tarea 9 con fecha 17/11 desde las 23:00, sin tareas semanales activas |
| **Una tarea no cruza la medianoche**: a las 00:00 deja de cargar aunque `_endLocaltime` marque las 08:00 (probablemente porque la fecha o el día de la semana se comprueban en cada instante) | noche del 17 al 18/11 (sesión 23:00:00–00:00:01, inicio y fin `schedman`, 4,6 kWh) |

Consecuencia: toda franja que cruce la medianoche se escribe como dos tareas,
una hasta las 00:00 y otra desde las 00:00, igual que hace la app.

**E3.2 Entradas del planificador.**

| Entrada | Origen |
|---|---|
| Nivel actual y confianza | `bateria_estado(instante)` (en `now()` es `v_bateria_estado`) |
| Salidas, consumo previsto y objetivo de cada una | `bateria_salidas(desde, días)` (horario + excepciones); la energía necesaria la calcula el planificador (E3.4) |
| Precio final por cuarto de hora | `mv_precio_final`, con el contrato y la fuente de `planificador_config` (una fila: `vivienda`, `omie`; no se edita desde el formulario) |
| Eficiencia de carga | `factor_bateria` |
| Potencia de carga | mediana de la potencia medida (`evse_w` de `cargador_muestra`) con carga en los últimos `ventana_potencia_dias`, por hora local (0–23); si no hay datos, `potencia_carga_inicial_kw` |
| Disponibilidad del coche | E3.3 |

**E3.3 Disponibilidad aprendida.** No hay hora de vuelta manual. La
disponibilidad se aprende de las conexiones y desconexiones registradas en
`cargador_estado`.

- Para cada día de la semana y cuarto de hora: probabilidad de que el coche
  esté enchufado, con los últimos `ventana_disponibilidad_dias`.
- **Ventana segura:** desde ahora, si el coche está enchufado, hasta la próxima
  salida.
- **Ventana probable:** cuartos de hora futuros con probabilidad ≥
  `umbral_disponibilidad_pct`. Para la energía necesaria de la próxima salida
  solo se usan si la ventana segura no basta o no existe, y siempre que
  terminen antes de la salida − `margen_salida_min`; para las salidas
  posteriores son la única fuente. Los niveles 2 y 3 también pueden usarlas,
  prefiriendo la segura en caso de empate. Si al llegar el coche no está, se
  replanifica al enchufar.
- Con menos de `historial_min_disponibilidad_dias` de historial, solo se usa la
  ventana segura (**modo prudente**). En modo prudente el nivel 1 cubre solo
  la próxima salida; las siguientes se planifican al volver a enchufar.
- Un cuarto de hora cuenta como observado si tiene alguna muestra del
  cargador, y como «enchufado» si todas sus muestras con estado tienen un
  estado no libre (ni 1 ni 2). La probabilidad es enchufados ÷ observados,
  por día de la semana y cuarto de hora local; los cuartos sin muestras (huecos
  del muestreo) no cuentan. El historial son los días locales con alguna
  muestra dentro de la ventana.

**E3.4 Algoritmo.** Unidad de tiempo: el cuarto de hora del mercado.
Horizonte: hasta la última salida conocida o `horizonte_plan_h` (72 h), lo que
sea menor.

**Precio de cada cuarto de hora:** el publicado si existe; si no, el esperado
(media del precio final del mismo día de la semana y hora en los últimos
`ventana_precio_esperado_dias`). Un cuarto de hora con precio esperado es
**provisional**. Los precios del día D+1 cuentan como publicados desde la
`hora_publicacion_precios` (13:30) del día D; lo usan el planificador,
`planificador simular` y el aviso de precios sin publicar (3d).

**Umbral barato:** `umbral_oportunista_pct` × media del precio final de los
últimos `ventana_umbral_dias` días completos.

**Energía del cargador:** energía a batería ÷ `factor_bateria`.

**Tres niveles, en este orden:**

| Nivel | Energía | Horas que puede usar |
|---|---|---|
| 1. Necesaria | La que hace falta para cubrir cada salida (acumulada: antes de cada salida, la suya y la de las siguientes sin otra ventana) | Las más baratas disponibles antes de la salida − `margen_salida_min`. Si las de por debajo del umbral no bastan, se usan las siguientes más baratas, sin límite de precio |
| 2. Margen | `margen_carga_pct` sobre la necesaria | Solo por debajo del umbral y con precio publicado. Si no hay, no se añade |
| 3. Oportunista | Hasta `nivel_oportunista_pct` (si `carga_oportunista` = sí) | Solo por debajo del umbral y con precio publicado |

Nunca se supera `tope_pct` en ningún punto de la trayectoria del nivel (el
consumo previsto de cada salida se resta en su instante). Entre igualdad de
precio se prefieren la madrugada (00:00–08:00) y los cuartos ya en ventana
segura (menos riesgo de corte por la casa o de ausencia). Los cuartos se
asignan enteros (se redondea hacia arriba, sin pasar del tope).

Consolidación en franjas:

- Cuartos de hora contiguos → una franja.
- Franjas de menos de `franja_min_min` se descartan o se unen a la vecina:
  gana la opción de menor coste total.
- Si salen más de `max_tareas_sistema` tareas, se rellenan los huecos más
  baratos de cerrar hasta caber. Una franja que cruza la medianoche cuenta
  como dos tareas.
- Una franja que cruce la medianoche se escribe como dos tareas (E3.1); en el
  formulario y en el plan se muestra como una sola franja.

**Recálculo:** cada 15 minutos y en cada evento (conexión, desconexión,
lectura, excepción, horario, parámetro, precios nuevos y cambio manual).

Reglas fijas:

- El planificador **nunca toca una franja en curso**. Que no entre energía
  (modulador, estado 5, coche lleno) no es un fallo: si al terminar ha entrado
  menos de lo previsto, se añade energía en franjas futuras.
- Las franjas manuales y los bloqueos (E3.5) se respetan siempre.
- **Sin solapes.** El cargador no los impide (E3.1), así que la regla es
  nuestra: las franjas automáticas solo ocupan los huecos que dejan las demás
  tareas activas (manuales del formulario y tareas de la app de Orbis), y la
  energía de esas tareas se descuenta de la necesaria. Si aparece en el
  cargador una tarea ajena que pisa una franja automática futura, el
  planificador recorta o mueve la suya; si la automática está en curso, no la
  toca y da un aviso.
- La guardiana (1 minuto el 31/12) no cuenta como solape.
- **En sombra** se planifica como si en el cargador solo estuviera la
  guardiana (como en activo, con las tareas de la app desactivadas), y se
  anota con qué tareas ajenas chocaría cada franja (`plan_franja.choca_con`;
  se muestra en el plan).
  Leer el programador para saberlo llega en 3c, con el `GET` en la lista
  cerrada; en 3a y 3b no hay choques anotados.

Modos (`modo_planificador`, tabla `planificador_modo` con historial: desde,
modo, origen y nota; en 3a, fijo en sombra):

- **sombra:** calcula y guarda el plan, no escribe en el cargador.
- **activo:** escribe y reconcilia.
- **pausa:** desactiva sus propias tareas futuras y deja de escribir; no toca
  las tareas de la app, la franja en curso ni la guardiana.
- **Cargar ahora** (acción, no modo): crea una franja manual desde ahora con
  la duración necesaria para llegar al objetivo de la próxima salida o, si ya
  está cubierto, al `nivel_oportunista_pct`, sin mirar el precio.

**E3.5 Franjas: estados y control manual.** Cada franja tiene dos estados
independientes:

| Estado en el cargador | Significado |
|---|---|
| pendiente | aún no escrita |
| confirmada | escrita y comprobada leyendo el programador |
| error | la escritura o la comprobación falló (con el motivo) |

| Estado de ejecución | Significado |
|---|---|
| programada | todavía no ha empezado |
| cargando | franja activa, estado 6 y potencia > 0 |
| en pausa | franja activa sin energía (modulador, estado 5 o coche lleno; el motivo si se puede distinguir) |
| terminada | acabó; con energía y coste reales |
| no ejecutada | acabó sin energía (normalmente, coche no enchufado) |
| cancelada | detenida o eliminada por el usuario |

Control desde el formulario:

- **Franjas futuras:** añadir, modificar horas y eliminar, automáticas o
  manuales.
- **Franja en curso:** solo **Detener**. Desactiva la tarea (`active: 0`) y la
  franja pasa a cancelada; después se puede eliminar. No se puede cambiar su
  horario.
- El formulario no deja guardar una franja manual que se solape con otra
  tarea activa (propia o de la app); muestra con cuál choca.
- Una franja añadida o modificada a mano queda **fijada**: el planificador
  cuenta su energía y no la mueve ni la borra.
- Cancelar o detener una franja automática crea un **bloqueo** en ese tramo
  hasta su fin original, para que el planificador no la recree.
- Todo cambio manual se envía al cargador **al momento** (no espera al ciclo)
  y se confirma leyendo de vuelta.

**E3.6 Escritura en el cargador.**

- Solo `POST`, `PUT` y `DELETE` en `/modules/scheduler/elements/mennekes`, a
  través del cliente de lista cerrada. Nada más cambia respecto a las reglas de
  `CLAUDE.md`.
- **Tarea guardiana:** una tarea propia siempre activa, con fecha 31/12 a las
  00:00 y 1 minuto, para que el cargador nunca quede sin tareas activas. El
  sistema no la desactiva ni la borra nunca, ni en pausa. En cada ciclo
  comprueba que existe y está activa; si falta o está inactiva, la recrea o
  la reactiva y da un aviso. Con el contenedor parado sigue protegiendo,
  porque vive en el cargador. Lleva un id reservado (`id_tarea_guardiana`,
  inicialmente 9) y cuenta para el límite de 10 tareas.
- Tareas propias registradas en una tabla (id de tarea ↔ franja). Una tarea que
  no esté en esa tabla es **ajena** y nunca se modifica ni se borra.
- Tareas con fecha (máscaras de día y mes) y `weekday 0`, una por franja o tramo.
- En cada ciclo se comprueba que las tareas propias conservan su fecha y su
  horario. Si alguna ha cambiado (p. ej. guardada desde la app, que la
  convierte en diaria), se corrige y se da el aviso «tarea modificada fuera
  del sistema». La Ayuda del formulario advierte de no editar desde la app las
  franjas del planificador.
- Reconciliación: plan deseado frente a lo que hay en el cargador → solo las
  diferencias. Idempotente.
- Tras cada escritura, `GET` del programador para confirmar.
- Las tareas propias ya terminadas se borran para liberar huecos.
- Cada escritura queda en una tabla de auditoría: instante, método, cuerpo,
  respuesta y resultado.
- Límite: como mucho una tanda de escrituras por ciclo, salvo cambios
  manuales.

**E3.7 Red de seguridad.**

- El plan escrito en el cargador cubre siempre al menos
  `horizonte_provisional_h` (48 h), con franjas provisionales donde aún no hay
  precio. Si el sistema se cae, el cargador sigue cargando según lo escrito.
- Las provisionales se ajustan cuando se publican los precios del día
  siguiente.
- **Aviso por correo** si el planificador lleva más de
  `aviso_planificador_min` sin completar un ciclo. Lo comprueba un script en el
  NAS, fuera del contenedor (como la renovación de certificados), leyendo la
  hora del último ciclo en la base de datos. También aparece como aviso en el
  formulario.

**E3.8 Modo sombra y medida del ahorro.**

- En sombra el plan se calcula y se guarda igual que en activo.
- Para cada día se compara el coste de:
  1. lo que se cargó de verdad;
  2. lo que habría costado el plan;
  3. cargar nada más enchufar (referencia).
- El ahorro frente a 3 se muestra en el formulario y en Grafana, también en
  modo activo.
- **Plan efectivo** de un cuarto de hora: el del último cálculo del servicio
  hecho antes de que empiece (los de `planificador plan` y `simular` no
  cuentan).
- La comparación es **por conexión** (de enchufar a desenchufar), con la
  energía real E medida en esa conexión en los tres casos:
  1. real: el coste de lo cargado (`v_coste_tramo`);
  2. plan: E repartida en orden de hora en los cuartos del plan efectivo
     dentro de la conexión (plan propio); si el plan tenía menos que E, el
     resto se carga en los cuartos libres más baratos de la conexión (lo que
     habría hecho el planificador de saber que hacía falta). El plan propio
     y el resto se muestran también por separado (kWh y coste), para
     distinguir el ahorro del plan del que sale de esa suposición
     (revisión del 18/11/2026);
  3. referencia: E desde el enchufe, a la potencia de carga (E3.2).
- Todo en SQL con `numeric`, con el precio de `planificador_config` y
  `factor_contador`, como `v_coste_tramo`.

**E3.9 Parámetros nuevos y agrupación.** Parámetros nuevos de la etapa 3:

| Clave | Valor inicial | Rango |
|---|---|---|
| `modo_planificador` | sombra | sombra / activo / pausa |
| `carga_oportunista` | sí | sí / no |
| `umbral_oportunista_pct` | 60 | 20–90 |
| `nivel_oportunista_pct` | 100 | 50–100 |
| `margen_carga_pct` | 10 | 0–30 |
| `margen_salida_min` | 30 | 0–120 |
| `potencia_carga_inicial_kw` | 4,5 | 1,4–11 |
| `ventana_disponibilidad_dias` | 28 | 7–90 |
| `umbral_disponibilidad_pct` | 80 | 50–100 |
| `historial_min_disponibilidad_dias` | 21 | 7–60 |
| `horizonte_provisional_h` | 48 | 24–72 |
| `franja_min_min` | 30 | 15–120 |
| `max_tareas_sistema` | 7 | 1–9 |
| `id_tarea_guardiana` | 9 | 1–10 |
| `aviso_planificador_min` | 60 | 15–360 |
| `ventana_precio_esperado_dias` | 28 | 7–90 |
| `ventana_umbral_dias` | 7 | 3–28 |
| `ventana_potencia_dias` | 28 | 7–90 |
| `horizonte_plan_h` | 72 | 24–96 |
| `hora_publicacion_precios` | 13:30 | 12:00–18:00 |

`modo_planificador` no es una fila de `bateria_parametro` (valores numéricos
con vigencia por día): vive en `planificador_modo` (E3.4). `carga_oportunista`
se guarda como 1 (sí) o 0 (no). `hora_publicacion_precios` se guarda en
minutos desde las 00:00 (810 = 13:30; unidad «hora») y se muestra y se
escribe HH:MM en el formulario y en el CLI. Los cinco últimos parámetros se añadieron al
aprobar el plan del paso 3a (18/11/2026), para no dejar constantes en el
código.

`max_tareas_sistema` cuenta solo las franjas: con la guardiana son 8 tareas
propias y quedan 2 huecos para la app de Orbis.

Con nombre, descripción, efecto y rango en `bateria_parametro_clave`, como los
de la etapa 2.

Agrupación de todos los parámetros (etapa 2 y 3): columnas nuevas `grupo` y
`orden` en `bateria_parametro_clave`; la pantalla Parámetros y la sección
Parámetros de la Ayuda se generan a partir de ellas, sin listas fijas en el
código.

| Grupo | Pregunta | Parámetros, en orden |
|---|---|---|
| 1. Coche y batería | ¿Cómo es el coche? | `capacidad_util_kwh`, `reserva_pct`, `tope_pct`, `factor_bateria`, `consumo_kwh_100km` |
| 2. Estimación del nivel | ¿Cómo se calcula el % sin lecturas? | `desconexion_min_s`, `detectar_carga_completa`, `energia_min_calibracion_kwh`, `observaciones_calibracion`, `umbral_carga_externa_kwh` |
| 3. Previsión de consumo | ¿Cuánto voy a gastar? | `percentil_planificacion`, `ventana_consumo_dias`, `consumo_inicial_trayecto_kwh` |
| 4. Estrategia de carga | ¿Cuánto cargar y a qué precio? | `margen_carga_pct`, `margen_salida_min`, `carga_oportunista`, `umbral_oportunista_pct`, `nivel_oportunista_pct`, `ventana_precio_esperado_dias`, `ventana_umbral_dias` |
| 5. Disponibilidad del coche | ¿Cuándo estará enchufado? | `ventana_disponibilidad_dias`, `umbral_disponibilidad_pct`, `historial_min_disponibilidad_dias` |
| 6. Cargador y franjas (avanzado) | ¿Cómo se escribe en el cargador? | `potencia_carga_inicial_kw`, `ventana_potencia_dias`, `franja_min_min`, `max_tareas_sistema`, `horizonte_provisional_h`, `horizonte_plan_h`, `hora_publicacion_precios`, `id_tarea_guardiana` |
| 7. Avisos y vigilancia | ¿Cuándo me avisa? | `aviso_sin_lectura_dias`, `hueco_muestreo_aviso_s`, `aviso_planificador_min` |

- `modo_planificador` no aparece en Parámetros: se controla desde la cabecera
  de «Plan de carga» (junto a «Cargar ahora»). Se guarda con historial como
  cualquier parámetro.
- `id_tarea_guardiana` se muestra pero no se edita desde el formulario
  (cambiarlo dejaría una guardiana huérfana en el cargador); solo por CLI, que
  migra la guardiana.
- Cada grupo lleva una frase de propósito (la columna «Pregunta»), que se
  muestra en la pantalla y en la Ayuda.

**E3.10 Pantallas y Grafana.**

- **Plan de carga** (pantalla nueva en el menú): modo actual y sus botones
  (sombra/activo/pausa, Cargar ahora); franjas futuras y en curso con motivo
  (necesaria para qué salida, margen, oportunista, provisional, manual), energía,
  precio medio, coste, los dos estados y sus acciones; franjas de los últimos
  días con lo real frente a lo planificado; ahorro.
  - Bajo el selector de modo, una frase que explica el modo elegido (sombra:
    calcula sin escribir; activo: escribe y revisa cada 15 min; pausa: no
    escribe y desactiva sus franjas futuras), y la misma explicación como
    texto emergente en cada opción. Bajo «Cargar ahora»: «Hasta el objetivo
    de la próxima salida».
  - Línea de tiempo de 60 h (24 h desplazable en móvil) con las filas
    «Cargas programadas», «Precio €/kWh» y «Coche en casa». Cada franja es un
    bloque coloreado por estado con su horario escrito debajo. Eje de precios
    con marcas cada 0,10 €/kWh y líneas de guía, y la línea del umbral con su
    valor. La banda «probable» marca los tramos con probabilidad ≥
    `umbral_disponibilidad_pct`.
  - Las franjas fuera de la vista se indican en la cabecera de la gráfica
    («+1 franja después: mié 18/11 14:00 ▸», con enlace a la tabla); la línea
    de tiempo no se alarga.
- **Navegación:** «Plan de carga» en segundo lugar en la barra lateral. Barra
  inferior del móvil: Inicio, Plan, Excepciones, Horario, Ajustes. Historial
  sale de la barra; se llega desde Inicio («Historial de lecturas →») y desde
  «Últimas franjas».
- **Inicio:** panel «Próximas cargas» con las 2–3 siguientes franjas.
- **Avisos nuevos:** escritura fallida; cargador distinto del plan; tarea
  propia modificada fuera del sistema; guardiana ausente o inactiva (crítico);
  ninguna tarea activa en el cargador (crítico); tarea ajena que pisa una
  franja en curso; el plan no cubre una salida (ni saltando el umbral);
  precios del día siguiente sin publicar a la hora esperada; planificador
  parado.
- **Grafana:** franjas como bandas sobre la gráfica de precios; panel de
  ahorro.
- **Parámetros** (pantalla existente, se rehace): un panel por grupo (E3.9).
  Escritorio: índice lateral de grupos. Móvil: `<details>` por grupo, abiertos
  «Coche y batería» y «Estrategia de carga». El grupo 6 va plegado con la
  etiqueta «avanzado» también en escritorio. «Calibración sugerida» pasa al
  grupo 1, junto a la eficiencia de carga. «Cambiar un valor» y «Últimos
  cambios» se mantienen.
- **Ayuda:** la sección Parámetros sigue la misma agrupación; añade la tarea
  guardiana y el aviso de no editar desde la app las franjas del planificador.
- Maquetas de Plan de carga (1d), Inicio con «Próximas cargas» (1a–1c) y
  Parámetros agrupados (5): se añadirán a docs/formulario-maquetas.md antes
  de 3b.

### Decisiones del paso 3a (18/11/2026)

- El algoritmo vive en Python puro (paquete `plan_carga/`, sin reloj ni base
  de datos: todo llega en la entrada), porque solo ordena precios. El dinero
  que se muestra (coste del plan, ahorro) se calcula en SQL con `numeric`, con
  referencia en Python para los tests.
- El paquete se llama `plan_carga/` porque `planificador.py` ya es el bucle
  del colector; el CLI se llama `planificador`.
- En sombra el planificador corre dentro del servicio `muestreo`, al final de
  su ciclo de 60 s: cada 15 minutos, en cada conexión o desconexión, al
  cerrarse una sesión y cuando hay una solicitud pendiente
  (`planificador_solicitud`, que activan el formulario, el CLI y el colector
  tras refrescar los precios). En 3c quien escribe en el cargador es un único
  proceso, el que ya tiene el cliente. Un fallo del planificador no detiene
  el muestreo.
- `planificador simular` repite el ciclo de sombra desde el enchufe hasta la
  desconexión (o la última muestra, si sigue enchufado): un cálculo cada 15
  minutos y en cada evento que vería el servicio (publicación de precios a
  `hora_publicacion_precios`, cambio de estado, lectura, desconexión),
  conservando la franja en curso, y compara el plan efectivo, igual que
  `v_plan_ahorro` (revisión del 18/11/2026). Mira hacia atrás con los datos
  de hoy: el nivel está reconciliado con lecturas posteriores y el horario
  no tiene historial (se usa el actual). En sombra el nivel de cada cálculo
  incluye la carga real de esa noche (las tareas de la app): la necesaria del
  plan efectivo baja a medida que carga el coche y el resto pasa a
  oportunista.
- Cada franja lleva su desglose por nivel (kWh del contador): necesaria con
  su salida, margen y oportunista («necesaria 8,0 · mar 09:30 /
  oportunista 24,7», maqueta 01d). `v_plan_franja` lo deriva de
  `plan_cuarto` (`necesaria_kwh`, `margen_kwh`, `oportunista_kwh`,
  `salidas_necesaria`).
- Algoritmo (`plan_carga/algoritmo.py`, función pura `planificar`):
  - Nivel 1 por salidas en orden: para cada una, los cuartos más baratos que
    terminan antes de la salida − margen (para la próxima, primero la
    ventana segura). Un cuarto elegido para una salida posterior puede caer
    antes de una anterior: así se acumula la energía de las salidas sin otra
    ventana. Con potencia uniforme coincide con la búsqueda exhaustiva
    (test).
  - Los cuartos del margen antes de cada salida no se usan para ningún nivel.
  - El nivel nunca pasa del tope por el plan: si las tareas fijas del usuario
    ya lo superan, el plan no añade energía.
  - Franja corta necesaria: se comparan alargarla hasta `franja_min_min`
    (cuartos vecinos) y descartarla reponiendo su energía (bordes de otras
    franjas o una franja nueva de la duración mínima); gana la de menor
    coste. Sin alternativa, se queda corta.
  - Franja corta de margen u oportunista (revisión del 18/11/2026): se alarga
    solo con cuartos que cumplan su nivel (bajo el umbral, precio publicado,
    sin pasar de `tope_pct` ni, si es oportunista, de
    `nivel_oportunista_pct`); si no los hay, se descarta.
  - La consolidación puede añadir energía necesaria después de elegir la
    oportunista. Si con ella algún punto pasa de `nivel_oportunista_pct`, se
    quitan los cuartos oportunistas más caros de los bordes de sus franjas
    (sin partirlas ni dejarlas cortas). Solo puede quedar por encima por un
    cuarto oportunista en medio de una franja.
  - Tests reproducibles: semillas fijas, con la semilla en el id del test
    (`[semilla=77]`). La tanda larga (8000 casos) va marcada `lento`:
    `PLAN_LENTO=1 pytest -m lento tests/test_plan_carga.py`.
  - Al alargar una franja o cerrar un hueco, los cuartos añadidos llevan el
    nivel de la franja y cumplen sus reglas (antes de su salida − margen; si
    es margen u oportunista, baratos y publicados). Si el coche ya está al
    tope, el cuarto entra en la franja con 0 kWh.
  - Más tareas que `max_tareas_sistema`: se cierra el hueco más barato que
    reduzca tareas; sin huecos cerrables, se quita la franja opcional más
    cara; si solo quedan necesarias, aviso.
  - Tareas de una franja: una por día local; `hourMin` y `duration` en
    minutos de reloj local. En las noches del cambio de hora la duración es la
    de reloj (sin verificar en el cargador; se comprueba en 3c y en el cambio
    de hora del 28/03/2027).

### Plan del paso 3c (v0.7.0, 18/11/2026)

Plan aceptado con estas decisiones del usuario:

- **Cliente del cargador:** lista cerrada exacta con `GET
  /modules/scheduler/elements?level=cfg` y `POST`, `PUT`, `DELETE
  /modules/scheduler/elements/mennekes`. Cada escritura se valida antes de
  conectar: una tarea por petición, tipo 0, id 1–10, con fecha (`day` y
  `month` con un único bit, `weekday` 0), sin cruzar la medianoche;
  `DELETE` solo `{"tasks":[{"id":N}]}`. Solo los formatos verificados el
  13/11 (POST con id libre, PUT con la tarea completa, DELETE de una). El
  cliente recibe los ids propios y rechaza tocar uno ajeno. Tras cada
  escritura, GET de confirmación.
- **Migración 0022:** `planificador_tarea` (tareas propias: id, papel,
  fecha, horario, activa, estado en el cargador), `plan_manual`,
  `plan_bloqueo`, `plan_auditoria` (real o en seco), `planificador_lectura`
  (última lectura del programador), cola de acciones manuales y parámetro
  `congelar_franja_min` (30).
- **Reconciliador puro:** guardiana primero (nunca se desactiva ni se
  borra; el cargador nunca queda sin tareas activas); ajenas nunca se
  tocan y en activo se respetan y su energía se descuenta de la necesaria;
  franja en curso intocable salvo Detener; franjas que empiezan en menos de
  `congelar_franja_min` también congeladas; solo diferencias, una tanda por
  ciclo; pausa desactiva las propias futuras.
- **Guardiana:** antes de crearla se comprueba que el id
  (`id_tarea_guardiana`) está libre o ya es la guardiana; si lo ocupa otra
  tarea (p. ej. la de prueba del 17/11), no se crea encima: aviso crítico y
  se resuelve a mano o con `planificador guardiana --id N`.
- **Orden de escritura:** la tarea propia se registra como pendiente en
  `planificador_tarea` antes del POST; después, POST y GET de confirmación.
- **Caducadas:** las máscaras no llevan año: una tarea propia terminada y
  no borrada se repetiría al año siguiente. Borrarla es obligatorio; aviso
  crítico si queda alguna caducada.
- **Acciones manuales** (modo, Cargar ahora, Detener, Modificar, Eliminar,
  Añadir franja): el formulario no habla con el cargador. La tabla es la
  cola; `NOTIFY` solo despierta al muestreo, que procesa las pendientes al
  arrancar, en cada ciclo y al recibir el aviso, bajo un
  `pg_advisory_lock`. Detener se reintenta en el acto y, si falla, el
  formulario ofrece «Reintentar».
- **Avisos críticos en 3c:** guardiana ausente, inactiva u ocupada; ninguna
  tarea activa; escritura fallida; tarea propia modificada fuera; tarea
  propia caducada sin borrar; cargador distinto del plan. Correo y resto de
  avisos, en 3d.
- **CLI:** `planificador modo`, `tareas`, `reconciliar --en-seco`,
  `limpiar` (pasa a sombra y borra las propias salvo la guardiana; avisa de
  que con solo la guardiana activa el coche no cargará) y `guardiana --id N`.
- **Despliegue por pasos** (con la semana en sombra; calendario en E3.11):
  simulación en seco en el NAS (v0.7.0 en sombra, lee el programador y
  registra en la auditoría lo que haría), pruebas de escritura a mano con
  la skill `viaris` (incluido un POST con un id ya ocupado, para ver si
  sobrescribe o da error), el usuario desactiva en la app sus tareas 5 y 6
  sin borrarlas, y activación de día con la primera noche vigilada.

### Implementación del paso 3c

- `viaris.py`: lista cerrada ampliada y `escribir` con validación (ver
  docs/viaris-api.md, «Uso en el código (etapa 3, paso 3c)»).
- `plan_carga/reconciliar.py` (puro): de la lectura, las tareas propias y
  las franjas deseadas saca las operaciones y los avisos.
- `plan_carga/escritura.py`: lee el programador (`planificador_lectura`),
  reconcilia y escribe bajo `pg_advisory_lock`; registra la tarea como
  pendiente antes del POST, confirma con GET y audita. En sombra, todo en
  seco. Para el plan, `ocupados()` da las tareas ajenas activas (respetadas
  en activo y pausa; en sombra solo se anotan en `choca_con`), las franjas
  manuales, los bloqueos (sin energía) y las propias congeladas o en curso
  (con energía, para no cargar dos veces).
- `plan_carga/acciones.py`: cola `plan_accion`. El formulario valida (sin
  solapes con tareas activas, manuales ni propias; franja mínima; máximo de
  tareas) y encola; el muestreo vuelve a validar y aplica. Modificar y
  Eliminar de una automática la bloquean hasta su fin original; de una
  manual, la anulan. Detener desactiva al momento, con un reintento
  inmediato, y bloquea el resto; si falla, «Reintentar». Cargar ahora:
  desde ahora hasta el objetivo de la próxima salida (o el nivel
  oportunista), al menos `franja_min_min`, como mucho 12 h.
- Muestreo: en cada ciclo, acciones pendientes, plan y, si hay plan nuevo
  o acciones, reconciliación. Entre ciclos escucha `LISTEN planificador`
  con una conexión propia; un `NOTIFY` lo despierta al momento.
- En sombra no se escribe nada en el cargador, tampoco por acción manual:
  añadir, modificar, eliminar, Detener y Cargar ahora aparecen desactivados
  («Cambia a activo para usarlo»), el formulario responde 409 y el muestreo
  las rechaza al procesar. Las únicas escrituras posibles en sombra son las
  del CLI con `--confirmar` (`limpiar`, `guardiana --id N`).
- Guardiana: si la 9 es la guardiana, se adopta (se registra en
  `planificador_tarea`) sin escribir. Solo se crea si no hay ninguna tarea
  igual (31/12 00:00, 1 min); si la hay en otro id, aviso
  `guardiana_en_otro_id` y no se toca nada.
- `limpiar --confirmar` borra solo las tareas registradas en
  `planificador_tarea` como franja propia que siguen en el cargador tal como
  se escribieron. Nunca la guardiana, las ajenas ni una propia cambiada
  fuera (la informa).
- Formulario: Plan de carga con el modo (pasar a activo pide confirmación),
  Cargar ahora, Detener, Modificar, Eliminar, Añadir franja (fin anterior
  al inicio = día siguiente), estado de cada franja en el cargador
  (pendiente, confirmada, error) y panel «Tareas en el cargador» de la
  última lectura. Tras encolar espera hasta 4 s al muestreo y muestra el
  resultado. Los avisos del planificador enlazan a Plan de carga.
- CLI en el contenedor `muestreo` (el colector no tiene `VIARIS_URL`):
  `planificador modo|tareas|reconciliar --en-seco|limpiar --confirmar|
  guardiana --id N --confirmar`.
- Verificado con tests contra un cargador simulado a nivel HTTP (el cliente
  real con su validación): escritura idempotente, sombra en seco, fallo de
  POST con la tarea en error, escritura no confirmada, guardiana ausente,
  cargador sin respuesta, ajenas respetadas, NOTIFY, cola y permisos de
  `formulario_rw` (solo inserta en `plan_accion`). Sin probar contra el
  cargador real.

### Fuera de alcance

- Calibración con Datadis (tarea posterior ya anotada).
- Cambio de potencia contratada en valle.
- Predicción de precios más allá de lo publicado (solo el perfil de 28 días
  para las provisionales).

### Pendiente de verificar con el coche

- Qué mide `homePower`: en la carga libre del 18/11/2026 marcó 0 W (antes del
  enchufe, unos 420 W) con `evsePower` a 4040 W (viaris-api.md, pendiente 5).
  No bloquea la etapa 3.
- Cómo reacciona el cargador a una tarea en curso borrada o con el horario
  cambiado (desactivarla la corta).
- Si guardar desde la app una tarea con fecha la convierte en diaria.

## Después de la etapa 3

Tareas anotadas para cuando termine la etapa 3; sin código hasta entonces.

- Calibración con Datadis: descarga diaria por API de la curva horaria del
  CUPS (credenciales en .env) y ajuste lineal (ganancia y desplazamiento) de
  la pinza de la casa y del contador del cargador. Datos del 15/11: con carga
  (~4,4 kW) la pinza marca −1 % frente a i-DE; sin carga marca ~+150 W (día:
  33,0 frente a 31,0 kWh, +6,6 %); el contador del cargador marca ~+5–6 %
  (22,33 kWh del coche frente a 22,13 kWh de toda la casa en 03:00–08:00).
  Revisar el factor del contador con esos datos.

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
