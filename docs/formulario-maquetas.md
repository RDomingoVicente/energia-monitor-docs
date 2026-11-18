# Formulario de batería: maquetas aprobadas

Estado a 18/11/2026. Alineado con v0.5.1 (migración 0015) para las pantallas
de la etapa 2 y con la etapa 3 de `docs/fase2.md` (E3.9 y E3.10) para Plan de
carga, el panel «Próximas cargas» de Inicio y los Parámetros agrupados. La
especificación manda en datos y cálculo; este documento manda en presentación,
textos y validaciones de la página. La sección 8 recoge los cambios al modelo
que salen de la revisión de las maquetas.

## 1. Referencias

- `docs/maquetas/*.html`: una página por pantalla y formato (escritorio 1280 px,
  móvil 390 px), estática, con datos de ejemplo. Son la referencia visual: se
  copian estructura, clases y estilos, no los datos.
- Los valores de los parámetros que muestran las maquetas 05 y 06 son de
  ejemplo (p. ej. `aviso_sin_lectura_dias` = 2 días, 0,5–7): mandan la tabla
  de 7.5 y `bateria_parametro_clave`.
- `docs/maquetas/00-referencia-estados-nivel.html`: lámina de los tres estados
  de la casilla Nivel. No es una pantalla.
- Datos de ejemplo: un mismo escenario (lectura el sáb 14/11 19:42, carga la
  noche del 15/11, dos trayectos el lun 16/11). Plan de carga (01d) muestra el
  dom 15/11 a las 14:10 con una franja en curso de 10:00 a 17:00. Las cifras
  son coherentes entre pantallas, pero no son datos reales ni casos de test.
- Etapa 3: 01d (Plan de carga) es nueva; 01a–01c llevan el panel «Próximas
  cargas»; 05 (Parámetros) está rehecha por grupos. En todas cambia la
  navegación (sección 6).

## 2. Principios

- HTML generado en el servidor. Formularios clásicos con POST, redirección 303
  y mensaje en la página destino (PRG); errores de validación con 422 y el
  formulario relleno.
- Sin JavaScript imprescindible. Mejora opcional permitida: activar los campos
  km/% al elegir «Indicar consumo» (sin JS se envían igual y el servidor ignora
  los que no correspondan) y abrir/cerrar los desplegables (`<details>`,
  nativo) de Ayuda y Parámetros. El selector de modo de Plan de carga es un
  formulario con POST.
- Una sola página responsive para escritorio y móvil, sin versiones separadas.
- Idioma es-ES; zona Europe/Madrid; 24 h; coma decimal; fechas cortas
  «lun 16/11 09:30».
- Accesibilidad: controles reales (`button`, `label`+`input`, `select`),
  `aria-current="page"` en la navegación, iconos SVG con `aria-hidden`, nada de
  emoji, el estado nunca solo por color (siempre etiqueta de texto), contraste
  ≥ 4,5:1 en texto.

## 3. Sistema visual

### Colores

| Token | Valor | Uso |
|---|---|---|
| fondo | `#0D1117` | página, campos |
| fondo-lateral | `#11161D` | barra lateral, cabecera móvil, barra inferior |
| panel | `#161B22` | paneles |
| borde | `#2A313B` | paneles, cabeceras de panel |
| borde-suave | `#21272F` | filas de tabla y listas |
| borde-control | `#3A424D` | campos y botones secundarios |
| texto | `#E6EDF3` | cifras y títulos |
| texto-2 | `#C9D1D9` | texto normal |
| texto-3 | `#9DA7B3` | secundario |
| texto-4 | `#8B949E` | etiquetas, notas |
| acento | `#1F6FEB` / `#2F81F7` / `#58A6FF` | botón primario / barra / cifras y enlaces |
| ok | `#3FB950` (fondo `#12261A`) | LEÍDO, carga, «cubierto», resuelto |
| aviso | `#D29922` (fondo `#2B2111`, borde `#5A4316`) | ESTIMADO, reserva, avisos, «manual · fijada», «avanzado» |
| error | `#F85149` (fondo `#3D1418`) | acciones destructivas, errores |
| info | `#58A6FF` (fondo `#132339`) | LEÍDO + CARGA, «con salida», «necesaria», info |
| barra bajo umbral | `#2EA043` | línea de tiempo: precio por debajo del umbral |
| barra sobre umbral | `#9E6A03` | línea de tiempo: precio por encima del umbral |
| barra provisional | rayado `#3A424D` / `#21272F` | línea de tiempo: precio esperado, sin publicar |

### Tipografía

- Texto: `-apple-system, 'Segoe UI', Roboto, Inter, sans-serif`.
- Datos (cifras, fechas, claves, ruta de la cabecera, tablas de datos):
  `ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace`.
- `font-variant-numeric: tabular-nums` en toda la página.
- Títulos de panel: 12 px, 600, mayúsculas, `letter-spacing: .06em`.
- Ninguna fuente externa (la CSP solo permite `self`).

### Componentes

Panel (`.pn` + `.ph` + `.pt`), tabla, etiqueta (`.tag`), botón primario
(`.btn`), secundario (`.btn2`/`.lk`), destructivo (`.del`), modificar (`.mod`),
filtro (`.fl`), chip de estado (`.chip`), interruptor (`.sw`), selector
segmentado (`.seg`), banner de estado (ok/aviso), barra de batería, línea de
tiempo (Plan de carga). Todos están en las maquetas con sus estilos exactos.

Barra de batería: azul hasta el nivel de la última lectura; verde para lo
cargado desde la lectura (solo LEÍDO + CARGA); rayado hasta el objetivo si
falta energía; marca ámbar en la reserva y blanca en el objetivo.

### Responsive

- Corte en 900 px.
- ≥ 900 px: barra lateral de 220 px (Inicio, Plan de carga, Excepciones,
  Horario, Historial, Parámetros, Avisos; abajo Ayuda, Grafana, /salud) y
  cabecera con ruta y chips de estado. Contenido en rejilla (ver cada
  pantalla).
- < 900 px: una columna, barra inferior (Inicio, Plan, Excepciones, Horario,
  Ajustes) y en la cabecera la hora del recálculo, la campana de avisos y «?»
  (Ayuda). Historial no está en la barra: se llega desde Inicio («Historial de
  lecturas →») y desde «Últimas franjas» de Plan. Tablas convertidas en listas.
- Zonas táctiles ≥ 44 px en móvil.
- Logo y favicon (añadidos el 15/11/2026, fuera de las maquetas): logo BYD
  de Arcticons (CC BY-SA 4.0) en azul `#2F81F7`. En escritorio sustituye al
  rayo de la barra lateral; en móvil ocupa el lugar de «formulario» en la
  cabecera («logo / inicio»), porque con los dos la ruta no cabía en 390 px.
  Favicon en `/favicon.ico` (16, 32 y 48 px), `/favicon.svg` y
  `/apple-touch-icon.png` (180 px), generados desde el SVG; fuente en
  `src/energia_monitor/formulario/estatico/favicon.svg`.

## 4. Reglas de maquetación

- Nada se sale de su panel: sin anchos fijos en celdas de texto; `nowrap` en
  botones, etiquetas, chips, cabeceras de tabla y celdas numéricas (`td.r`).
- Datos secundarios en una segunda línea dentro de la celda (clave bajo el
  nombre, «indicado · 180 km» bajo los kWh, nota bajo la fecha), no en
  columnas extra.
- Tablas largas con `table-layout: fixed` y anchos solo en las columnas cortas
  (ver Ayuda, Parámetros y Plan de carga).
- La línea de tiempo del móvil va dentro de un contenedor con desplazamiento
  horizontal; es el único elemento que puede ser más ancho que la pantalla.
- Validado en las maquetas: ningún elemento desborda su contenedor en 1280 y
  390 px. Repetir la comprobación con la implementación (test con Playwright
  en 1280×900 y 390×844).

## 5. Convenciones de texto

- `%` solo para niveles absolutos (52 %, objetivo 59 %). Diferencias entre
  niveles en `pts` (puntos de batería), con kWh cuando ayuda:
  «+11 pts de margen (≈10 kWh)». 1 pt = `capacidad_util_kwh` / 100.
- kWh con un decimal; porcentajes enteros; pts enteros salvo medias (1 decimal).
  Precios en €/kWh con tres decimales; importes en € con dos. Los valores
  provisionales llevan «≈».
- Etiquetas de estado del nivel: `LEÍDO`, `LEÍDO + CARGA`, `ESTIMADO`.
- Eventos: «lectura 52 %», «conexión», «trayecto est. −9,2 kWh», «carga 18,3
  kWh (15,3 a batería)», «carga externa 30,0 kWh (25,8 a batería)» o «carga
  externa sin kWh», «inicio carga (en curso)». En las cargas, primero la
  energía medida por el cargador y entre paréntesis la estimada a batería (×
  `factor_bateria`; v0.5.8, migración 0019). Donde solo cuenta lo que llega a
  la batería (panel Batería, casilla LEÍDO + CARGA): «carga +19,3 kWh a
  batería». Trayecto partido por una lectura con algún
  tramo reconciliado y otro no (v0.5.7, migración 0018): el total y lo real,
  «trayecto est. −31,7 kWh (11,0 reales)»; «est.» mientras quede algún tramo
  estimado.
- Franjas: horario «10:00–17:00»; una franja que cruza la medianoche se
  muestra como una sola («23:00–02:30») aunque se escriba como 2 tareas.
- El campo de lectura usa como texto de ejemplo «0–100», nunca un número.

## 6. Navegación, cabecera y avisos

- Cabecera de escritorio: `formulario / <ruta>`, chip de avisos (solo si hay),
  chip del cargador (verde si la última muestra tiene menos de
  `hueco_muestreo_aviso_s`, rojo si no), chip de PostgreSQL y hora del último
  recálculo.
- Navegación: «Plan de carga» en segundo lugar en la barra lateral (icono de
  enchufe); en móvil, «Plan» en segundo lugar de la barra inferior (ver 3,
  Responsive).
- Avisos: chip «N avisos» en la cabecera y contador en la entrada Avisos del
  menú; en móvil, campana con contador. Color del aviso más grave activo
  (error > aviso > info). Sin avisos: no se muestra el chip y la entrada no
  lleva contador.
- `/salud`: se mantiene en el menú; devuelve el JSON del healthcheck. Los chips
  de la cabecera salen de la misma consulta.
- Grafana: enlace a «Energía: batería».

## 7. Pantallas

### 7.1 Inicio (`/`)

Maquetas 01a, 01b, 01c.

- Escritorio: cuatro indicadores (Nivel, Estado del coche, Próxima salida,
  Energía necesaria); debajo, a la izquierda, panel Batería, **«Próximas
  cargas»** y «Próximos 7 días»; a la derecha, banner, «Anotar lectura» y
  «Últimos eventos» (5). Móvil: banner, Batería, Coche/Necesita, **«Próximas
  cargas»**, Anotar lectura, próximos 3 días.
- Estado del coche (v0.5.7), de los cambios de estado del conector
  (`cargador_estado`; no de `localtime` del cargador, viaris-api.md):
  «Conectado · desde ayer 18:00 · sin cargar desde 06:00 · evsm 4» (tras una
  carga en esta conexión) o «Conectado · desde 18:00 · evsm 4 · sin carga»;
  mientras carga, sin «conectado desde»: «Cargando · desde 02:00 · evsm 6 ·
  4,6 kW», desde el inicio de la carga (las pausas 6→5→6 no lo cortan).
  Conectado desde: primer estado no libre tras el último 1–2.
- La etiqueta del nivel sale de `v_bateria_estado.confianza` (sección 8):

| confianza | Etiqueta | Texto bajo el nivel |
|---|---|---|
| leido | LEÍDO | «leído hoy 19:42» |
| leido_carga | LEÍDO + CARGA | «52 % leído 14/11 19:42 + 19,3 kWh» |
| estimado | ESTIMADO | «70 % tras carga − 2 trayectos» |

- Energía necesaria: kWh y «objetivo 59 % · faltan 21 pts»; si el nivel ya
  cubre el objetivo, «cubierto» en verde con «+N pts de margen (≈X kWh)».
- **Próximas cargas** (etapa 3): las 2–3 siguientes franjas del plan vigente.
  Escritorio, una fila por franja: día y horario, motivos (`necesaria`,
  `margen`, `oportunista`, `manual`, `provisional`), kWh del cargador, coste
  (con «≈» si es provisional) y estado de ejecución (`programada`, `cargando`,
  `en pausa`). Móvil: lista con los mismos datos en dos líneas. Enlace «Plan de
  carga →» («Plan →» en móvil). Sin franjas: «Sin cargas programadas». En
  modo sombra, la etiqueta `SOMBRA` en la cabecera del panel (siempre) y los
  estados de sombra de 7.9.
- Tras guardar una lectura: banner verde «Lectura guardada: 52 % a las 19:42.
  El modelo estimaba 55 % (diferencia −3 pts)…».
- Aviso ámbar «Sin lecturas…» solo con confianza `estimado` y pasado
  `aviso_sin_lectura_dias`.
- Próximos 7 días: fecha, salida, origen (`horario`, `horario · sin salida`,
  `sin salida`, `excepción · 180 km`, `excepción · mínimo 80 %`), consumo
  previsto, objetivo.
- Estados adicionales sin maqueta, con el mismo estilo: antes de la primera
  lectura (sin nivel; texto «Aún no hay nivel: anota el % que marca el coche» y
  el formulario de lectura destacado); coche fuera («Fuera desde 09:31», nivel
  a la salida); intervalo incoherente (aviso ámbar con enlace a Historial).
- Formulario de lectura: % (0–100, entero), fecha y hora prerrellenas con
  «ahora»; se admiten lecturas retroactivas. Debajo, enlace «¿Cargaste fuera
  de casa? Registrar carga externa →» y, en móvil, «Historial de lecturas →».

### 7.2 Excepciones (`/excepciones`)

Maqueta 02.

- Escritorio: tabla de próximas (fecha, tipo, salida, consumo previsto
  con su origen, objetivo, Modificar / Dar de baja) y de pasadas de 30 días
  (previsto, estimado real, desvío, estado); formulario a la derecha.
  Cambio del 15/11/2026 respecto a la maqueta: la nota no va bajo la fecha
  sino en una fila propia a todo el ancho bajo su excepción (texto pequeño
  en gris, sin borde entre las dos filas), también si es corta; en una
  columna tan estrecha una nota larga salía palabra a palabra. En móvil no
  cambia.
- Tipos: **Con salida** y **Sin salida** (sección 8).
- Con salida: fecha, hora de salida; consumo previsto con dos opciones,
  **Normal (P75) · 31,0 kWh** marcada por defecto, o **Indicar consumo** (km o
  %, uno solo); **Nivel mínimo a la salida** opcional (%). Recuadro con el
  cálculo resultante («consumo normal P75 = 31,0 kWh / objetivo = 25 % + 34
  pts = 59 %»).
- Sin salida: solo fecha.
- Validaciones (422 con mensaje junto al campo):
  - una excepción activa por fecha;
  - sin salida solo si el horario vigente tiene salida ese día de la semana
    («El sábado 21/11 ya no tiene salida según tu horario»);
  - con «Indicar consumo», exactamente uno de km o %;
  - si el objetivo resultante supera `tope_pct`, se recorta al calcular (no
    se guarda recortado) y aparece el aviso «Objetivo por encima del tope».
- «Modificar» carga la excepción en el formulario; al guardar, baja de la
  anterior y alta de la nueva en una transacción. «Dar de baja» es borrado
  lógico.

### 7.3 Horario (`/horario`)

Maqueta 03.

- Siete filas: día, interruptor «sale», hora (desactivada si no sale), consumo
  previsto (P75) y objetivo. «Guardar horario».
- Sin vigencia ni versiones (`bateria_horario` no las guarda): los cambios se
  aplican desde que se guardan; se muestra la fecha de la última modificación.
  Resumen de la semana tipo (salidas y kWh previstos).

### 7.4 Historial (`/historial`)

Maqueta 04.

- Indicadores (30 días por defecto; filtros 30 d, 90 d, todo, incluir
  anuladas):
  - **Error medio** = media de (leído − estimado) en pts de las lecturas
    comparables; con signo; texto «media con signo: el coche suele tener menos
    de lo estimado» si es negativo.
  - **Error absoluto medio** = media de |leído − estimado|, con su equivalente
    en kWh.
  - **Lecturas válidas**: comparables + cargas completas; las anuladas no
    cuentan.
  - **Última lectura** y umbral `aviso_sin_lectura_dias`.
- Tabla «Lecturas y cargas externas»: fecha, leído, estimado, diferencia
  (ámbar si |d| ≥ 3 pts), origen, estado, nota, Corregir / Anular. Las cargas
  externas aparecen como filas con origen «carga externa», los kWh en la
  columna de estado y los pts sumados en la de diferencia.
- Corregir: cambia %, fecha, hora o nota; crea una lectura nueva y anula la
  anterior con motivo «corregida». Anular pide motivo. Nada se borra.

### 7.5 Parámetros (`/parametros`)

Maqueta 05 (rehecha en la etapa 3).

- Los parámetros se agrupan por `bateria_parametro_grupo` y se ordenan por
  `orden` (E3.9); la pantalla no tiene listas fijas en el código.
- Escritorio: rejilla de dos columnas. Izquierda: nota «Cada cambio es un
  registro nuevo con fecha de inicio. El modo del planificador se cambia desde
  Plan de carga.» y un panel por grupo con título («1 · Coche y batería») y su
  pregunta a la derecha («¿Cómo es el coche?»); tabla con nombre (clave
  debajo y, si la hay, una nota de una línea), valor, rango, origen, desde,
  «Cambiar». Derecha: índice «Grupos» (nombre y número de parámetros; enlaces
  a cada panel), «Cambiar un valor» y «Últimos cambios».
- Móvil: un `<details>` por grupo con título, pregunta y número de
  parámetros; abiertos los marcados `abierto_movil` (1 y 4). Debajo, «Cambiar
  un valor» y «Últimos cambios».
- Grupo 6 (`avanzado`): plegado también en escritorio, con la etiqueta
  «avanzado» y el texto «Valores técnicos que casi nunca hay que tocar».
- «Calibración sugerida» va dentro del grupo 1, bajo la fila de la eficiencia
  de carga: valor observado, vigente, n, rango y botón «Registrar X» («Registrar
  X como valor nuevo» en móvil). No se aplica solo.
- Parámetros con `editable = false` (`id_tarea_guardiana`): sin botón
  «Cambiar»; en su lugar «solo CLI». No aparecen en el selector de «Cambiar un
  valor».
- «Cambiar un valor»: `<select>` con `<optgroup>` por grupo; valor, desde,
  nota; fuera de rango, 422 con el rango admitido. Mismas reglas que el CLI.
- «Últimos cambios»: los cinco más recientes; un alta en bloque (las claves de
  la etapa 3) se muestra como una sola línea.
- Lista, en orden de pantalla (los valores de las claves nuevas son los
  iniciales de E3.9; nombres, rangos y textos, de `bateria_parametro_clave`):

| Grupo | Clave | Nombre en pantalla | Valor | Rango |
|---|---|---|---|---|
| 1 | `capacidad_util_kwh` | Capacidad útil | 91,3 kWh | 50–120 |
| 1 | `reserva_pct` | Reserva mínima | 25 % | 10–60 |
| 1 | `tope_pct` | Tope de carga | 100 % | 50–100 |
| 1 | `factor_bateria` | Eficiencia de carga | (vigente) | 0,70–1,00 |
| 1 | `consumo_kwh_100km` | Consumo medio | 23 kWh/100 km | 10–40 |
| 2 | `desconexion_min_s` | Desconexión sin consumo | 30 min (1800 s) | 0–4 h |
| 2 | `detectar_carga_completa` | Detectar carga completa | sí | sí/no |
| 2 | `energia_min_calibracion_kwh` | Energía mínima para calibrar | 5 kWh | 1–30 |
| 2 | `observaciones_calibracion` | Observaciones para calibrar | (vigente) | 1–20 |
| 2 | `umbral_carga_externa_kwh` | Umbral de carga externa | 5 kWh | 1–30 |
| 3 | `percentil_planificacion` | Percentil de planificación | 0,75 (P75) | 0,50–0,95 |
| 3 | `ventana_consumo_dias` | Ventana de consumo | 28 días | 7–90 |
| 3 | `consumo_inicial_trayecto_kwh` | Consumo inicial por trayecto | 25 kWh | 1–90 |
| 4 | `margen_carga_pct` | Margen de carga | 10 % | 0–30 |
| 4 | `margen_salida_min` | Margen antes de salir | 30 min | 0–2 h |
| 4 | `carga_oportunista` | Carga oportunista | sí | sí/no |
| 4 | `umbral_oportunista_pct` | Umbral barato | 60 % | 20–90 |
| 4 | `nivel_oportunista_pct` | Nivel oportunista | 100 % | 50–100 |
| 4 | `ventana_precio_esperado_dias` | Ventana del precio esperado | 28 días | 7–90 |
| 4 | `ventana_umbral_dias` | Ventana del umbral | 7 días | 3–28 |
| 5 | `ventana_disponibilidad_dias` | Ventana de disponibilidad | 28 días | 7–90 |
| 5 | `umbral_disponibilidad_pct` | Umbral de disponibilidad | 80 % | 50–100 |
| 5 | `historial_min_disponibilidad_dias` | Historial mínimo | 21 días | 7–60 |
| 6 | `potencia_carga_inicial_kw` | Potencia de carga inicial | 4,5 kW | 1,4–11 |
| 6 | `ventana_potencia_dias` | Ventana de potencia | 28 días | 7–90 |
| 6 | `franja_min_min` | Franja mínima | 30 min | 15 min–2 h |
| 6 | `max_tareas_sistema` | Franjas máximas | 7 | 1–9 |
| 6 | `horizonte_provisional_h` | Horizonte escrito | 48 h | 24–72 h |
| 6 | `horizonte_plan_h` | Horizonte del plan | 72 h | 24–96 h |
| 6 | `hora_publicacion_precios` | Publicación de precios | 13:30 | 12:00–18:00 |
| 6 | `id_tarea_guardiana` | Tarea guardiana | 9 | solo CLI |
| 7 | `aviso_sin_lectura_dias` | Aviso sin lecturas | 7 días | 1–14 (días enteros) |
| 7 | `hueco_muestreo_aviso_s` | Aviso hueco de muestreo | 30 min (1800 s) | 5 min–4 h |
| 7 | `aviso_planificador_min` | Aviso planificador parado | 60 min | 15 min–6 h |

- Notas de una línea bajo el nombre (de `bateria_parametro_clave`, calculadas
  cuando procede): «de la media de 7 días · hoy 0,211 €/kWh» en el umbral
  barato; «historial actual: 4 días → modo prudente» en el historial mínimo;
  «+ guardiana = 8; 2 huecos para la app» en las franjas máximas.
- `modo_planificador` no está en esta pantalla (tabla `planificador_modo`; se
  cambia en Plan de carga).
- La clave del parámetro no cambia (`factor_bateria`); solo el nombre en
  pantalla es «Eficiencia de carga». Las claves guardadas en segundos o días
  se muestran en minutos, horas o días. Nombres, orden y rangos son los de
  `bateria_parametro_clave` (migración 0020, E3.9); la maqueta usa valores
  de ejemplo en algunas claves.

### 7.6 Ayuda (`/ayuda`)

Maqueta 06 (de la etapa 2; sin maqueta nueva).

- Secciones: Cómo funciona (diagrama de flujo y ejemplo de cálculo con los
  valores vigentes, empezando por «1 pt = capacidad útil / 100»), Estados del
  nivel, Pantallas, Parámetros (vigente, rango, qué es, efecto al cambiarlo),
  Glosario.
- Etapa 3: la sección Parámetros sigue los mismos grupos que la pantalla
  Parámetros, con la pregunta de cada grupo como subtítulo. Se añade una
  sección «Plan de carga» con: qué hace cada modo, los tres niveles
  (necesaria, margen, oportunista), la tarea guardiana y el aviso de **no
  editar desde la app de Orbis las franjas del planificador** (la app las
  convierte en diarias).
- Escritorio: índice lateral con búsqueda; móvil: `<details>` (abiertos «Cómo
  funciona» y «Parámetros»).
- Los textos «qué es» y «efecto» de cada parámetro se leen de
  `bateria_parametro_clave` (sección 8); el resto es estático. Las cifras del
  ejemplo se calculan con los valores vigentes.

### 7.7 Avisos (`/avisos`)

Maqueta 07.

- Activos: tipo (ERROR/AVISO/INFO), título, detalle, «desde», regla que lo
  dispara y botón a la pantalla donde se resuelve. Sin marcar como leído:
  desaparecen cuando cesa la causa.
- Resueltos de los últimos 7 días.
- Catálogo (escritorio):

| Nivel | Aviso | Regla | Acción |
|---|---|---|---|
| ERROR | Cargador no accesible | última muestra más antigua que `hueco_muestreo_aviso_s` (la página no consulta el cargador) | — |
| ERROR | Base de datos no accesible | (página de error 503) | — |
| AVISO | Sin lecturas | `estimado` y más de `aviso_sin_lectura_dias` | Anotar lectura |
| AVISO | Hueco de muestreo | hueco > `hueco_muestreo_aviso_s` | — |
| AVISO | Intervalo incoherente | E2.5 | Historial |
| AVISO | ¿Cargaste fuera de casa? | residuo positivo > `umbral_carga_externa_kwh` en el intervalo que termina en la última lectura | Carga externa |
| AVISO | Objetivo por encima del tope | objetivo > `tope_pct` | Excepciones |
| AVISO | Carga completa dudosa | sesión terminada por el coche (evsm) sin muestra en los 180 s siguientes o con un estado que no es 1, 2, 4 ni 8, con el muestreo ya en marcha (las anteriores a la primera muestra quedan como `sin_muestreo`, sin aviso); solo las posteriores al último valor absoluto (E2.3) | Historial |
| INFO | Calibración sugerida | E2.6 | Parámetros |

- Avisos de la etapa 3 (E3.10; se implementan en 3c y 3d, con el mismo
  formato y acción «Plan de carga»): ERROR guardiana ausente o inactiva;
  ERROR ninguna tarea activa en el cargador; ERROR planificador parado (más de
  `aviso_planificador_min`); AVISO escritura fallida; AVISO cargador distinto
  del plan; AVISO tarea propia modificada fuera del sistema; AVISO tarea ajena
  que pisa una franja en curso; AVISO el plan no cubre una salida; AVISO
  precios del día siguiente sin publicar (pasada `hora_publicacion_precios`).

### 7.8 Carga externa (`/carga-externa`)

Maqueta 08. Sin entrada propia en el menú: se llega desde Inicio y desde el
aviso «¿Cargaste fuera de casa?».

- Formulario: fecha y hora aproximada, energía cargada en kWh (opcional,
  0–150, un decimal), coste en € (opcional, ≥ 0, dos decimales), lugar o nota.
  Recuadro con el cálculo: «28,4 kWh × eficiencia 0,86 = 24,4 kWh → +27 pts ·
  coste 0,449 €/kWh».
- Validación: el instante debe caer dentro de un periodo en que el coche
  estaba desconectado de casa (422 «A esa hora el coche estaba conectado en
  casa»).
- Lista de los últimos 90 días: fecha, energía (con los pts debajo o «sin
  kWh»), coste (con €/kWh debajo), lugar, Corregir / Anular. Total de coste en
  la cabecera del panel.
- Texto fijo: al volver hay que anotar una lectura; sin kWh, el intervalo
  queda fuera de la mediana y del P75.
- Desviación aceptada (15/11/2026): el recuadro bajo la lista («Si al anotar
  una lectura…») va en azul con el icono de información, no en ámbar como
  en la maqueta 08. El ámbar queda solo para los avisos activos.

### 7.9 Plan de carga (`/plan`)

Maqueta 01d (etapa 3). Ruta en la cabecera: `formulario / plan`. En 3b es de
solo lectura (modo sombra): las acciones que escriben (selector de modo,
Cargar ahora, Detener, Modificar, Eliminar, Añadir franja) se muestran
desactivadas con el texto «disponible en el paso 3c».

Escritorio, de arriba abajo:

1. **Panel de control** (rejilla de tres columnas):
   - Estado: «Próxima salida lun 16/11 09:30» con etiqueta `cubierta` / `no
     cubierta`; «objetivo 59 % · al terminar la franja en curso: 82 %»; «plan
     hasta mar 17/11 09:30 · 3 franjas · 8,95 € (≈4,09 € provisional)»;
     «umbral barato 0,211 €/kWh = 60 % × 0,352 (media 7 días)». En modo
     prudente se añade «modo prudente: solo con el coche enchufado».
   - Modo: selector segmentado `sombra · activo · pausa` y, debajo, una frase
     sobre el modo elegido: «Activo: escribe sus franjas en el cargador y las
     revisa cada 15 min. En sombra solo calcula; en pausa no escribe y
     desactiva sus franjas futuras.» Cada opción lleva su explicación como
     `title`.
   - «Cargar ahora» (`.btn2`) con la etiqueta «Ignora el precio» encima y
     «Hasta el objetivo de la próxima salida.» debajo.
2. **Línea de tiempo** («Próximas 60 h · precio y franjas»), desde las 00:00
   del día actual. Columna de etiquetas de 112 px y tres filas:
   - «Cargas programadas»: un bloque por franja (20 px de alto, radio 3 px).
     En curso: verde sólido hasta «ahora» y verde oscuro con borde verde el
     resto, texto «cargando». Programada: verde con borde. Provisional: borde
     discontinuo gris, texto «prov.». Manual: borde ámbar. Bajo cada bloque, su
     horario («10:00–17:00»), centrado y en monoespaciada de 11 px.
   - «Precio €/kWh» (120 px): una barra por hora, altura proporcional al
     precio con escala fija 0–0,60 €/kWh; color según la sección 3 (bajo /
     sobre el umbral, provisional). Eje con marcas 0,10…0,50 a la izquierda y
     líneas de guía; línea discontinua verde del umbral con su valor a la
     derecha («umbral 0,211»).
   - «Coche en casa»: barra azul sólida mientras está enchufado o en la
     ventana segura; azul discontinua en los tramos «probables» (probabilidad
     ≥ `umbral_disponibilidad_pct`).
   - Marcas verticales: blanca en cada salida (con «salida lun 09:30» debajo)
     y azul en «ahora». Eje de horas cada 6 h, con el día en negrita a las
     00 («dom 00»).
   - Leyenda: bajo el umbral, sobre el umbral, precio provisional, enchufado,
     probable (≥ 80 % de los días), ahora 14:10.
   - Franjas fuera de la vista: enlace en la cabecera del panel, en ámbar,
     «+1 franja después: mié 18/11 14:00 ▸», que lleva a la tabla. La línea de
     tiempo no se alarga para incluirlas.
3. Rejilla de dos columnas (`minmax(0, 1fr) 320px`):
   - **Franjas programadas** (`table-layout: fixed`; columnas 104 / auto / 126
     / 150 / 96 px): Franja (día; horario; duración), Motivo · kWh (una línea
     por motivo con su etiqueta y kWh: «necesaria 7,4 · lun 09:30», «margen
     0,7», «oportunista 23,4»; `provisional`; `manual · fijada`), Energía ·
     coste (kWh, +pts, €, €/kWh), Cargador · ejecución (las dos etiquetas de
     estado de E3.5 y una nota: progreso «17,6 de 31,5 kWh», pausas «pausa
     13:00–13:15 (casa)», «sin horas bajo el umbral: margen no añadido»,
     «creada por ti el 15/11 09:12»; en sombra, ver abajo), Acciones (en curso: solo «Detener»,
     `.del`; futuras: «Modificar» y «Eliminar», apiladas). Pie: «La franja en
     curso solo se puede detener; luego, eliminar. Una franja que cruza la
     medianoche se muestra como una sola y se escribe como 2 tareas. Al
     detener o eliminar una franja automática, el planificador no la vuelve a
     crear en ese tramo.»
   - **Últimas franjas · 7 días**: franja (día y horario), motivo, plan kWh,
     real kWh, coste, resultado (`terminada`, `no ejecutada` + motivo,
     `cancelada` + «detenida 03:40», pausas). Enlace «Historial →».
   - Columna derecha:
     - **Añadir franja manual**: día, inicio y fin (`type="time"`,
       `step="900"`), recuadro con el cálculo («1 h × 4,5 kW = 4,5 kWh → +4
       pts · precio: sin publicar»), texto «Se envía al cargador al guardar y
       se confirma leyéndolo de vuelta. No puede solaparse con otra tarea
       activa: si choca, se indica con cuál. El planificador la respeta y no
       la mueve.» y botón «Añadir franja». Validación 422: solape («Choca con
       la tarea 5 de la app (mar 23:00–23:59)»), fin ≤ inicio, menos de
       `franja_min_min`, más de `max_tareas_sistema`.
     - **Ahorro** (frente a cargar al enchufar): filas hoy, 7 días y 30 días;
       columnas Real, Al enchufar y Ahorro (verde). En sombra se añade la
       columna «Plan». Nota: «Hoy: lo cargado hasta ahora en la franja en
       curso.»
     - **Tareas en el cargador** («6 de 10»): una fila por tarea: id, origen
       (`planificador`, `manual`, `app Orbis`, `guardiana`), programación y
       estado (`activa`, `en curso`, `inactiva`). Pie: «Libres: 4, 7, 8 y 10.
       La guardiana evita que el cargador cargue libremente si no queda
       ninguna tarea activa. No edites desde la app las tareas del
       planificador.»

Estados en modo sombra (decisión del 18/11/2026): las franjas no se escriben
en el cargador, así que no aparecen como ejecutadas.

- Estado en el cargador: «no escrita (sombra)».
- Franja en curso: «en su horario · coche cargando» o «en su horario · coche
  sin cargar», en lugar de `cargando` / `en pausa`. Futuras: `programada`.
- Últimas franjas: «coincidió con carga real» (entró energía en su horario)
  o «sin carga real», en lugar de `terminada` / `no ejecutada`.
- «Próximas cargas» de Inicio, igual; con la etiqueta `SOMBRA` en la cabecera
  del panel siempre que el modo sea sombra.
- Tareas en el cargador (3b): «disponible en el paso 3c» y «En sombra el plan
  no tiene en cuenta las tareas de la app de Orbis (E3.4)».
- En modo activo (3c), los estados de E3.5 tal cual.

Móvil, en una columna: Planificador (estado, selector de modo con su frase,
Cargar ahora), Próximas 24 h (desde la hora actual redondeada hacia abajo,
desplazable en horizontal, sin columna de etiquetas, con el eje de precios a
la izquierda), Franjas programadas (tarjetas con las acciones debajo), Añadir
franja manual, Ahorro, Tareas en el cargador y Últimas franjas · 7 días
(«real / plan kWh»).

## 8. Cambios al modelo derivados de la revisión

1. **Hecho en v0.5.2 (migración 0016).** `v_bateria_estado.confianza` text NOT NULL CHECK IN (`leido`,
   `leido_carga`, `estimado`), con `ultima_lectura_ts`, `ultima_lectura_pct`,
   `kwh_cargados_desde_lectura`, `trayectos_desde_lectura`,
   `kwh_trayectos_desde_lectura`. Tests: un caso por estado y la secuencia
   lectura → carga → trayecto → lectura.
2. Excepciones: **hecho en v0.5.1 (migración 0015)**. Tipos `con_salida` y
   `sin_salida`, `consumo_modo`, `km`/`pct`, `pct_objetivo`; validación y
   «Modificar» en `bateria_servicio.py` (`ErrorExcepcion` → 422).
3. **Hecho en v0.5.2.** Parámetros: se usan los existentes `aviso_sin_lectura_dias` y
   `hueco_muestreo_aviso_s`. Único parámetro nuevo: `umbral_carga_externa_kwh`
   (punto 7).
4. **Hecho en v0.5.2.** `bateria_parametro_clave`: columnas `nombre`, `descripcion`, `efecto`,
   `unidad`, `minimo`, `maximo` (textos de la sección 7.5 y de la maqueta 06).
   La validación de rangos lee `minimo`/`maximo`.
5. **Hecho en v0.5.2.** Avisos: vista `v_aviso` (activos, calculados, incluido «Objetivo por
   encima del tope») y tabla `aviso_historial` (tipo, inicio, fin, detalle)
   mantenida por el recálculo periódico.
6. Horario sin vigencia (decisión tomada): no hay cambio de modelo.
7. **Hecho en v0.5.2**; los paneles de coste de Grafana, en el paso 4 (hecho).
   Cargas externas: evento nuevo `carga_externa` en el registro de batería
   (instante, `kwh` NULL si se desconoce, `coste_eur` NULL si se desconoce,
   nota; corregir y anular como las lecturas).
   - Con kWh: suma `kwh × factor_bateria` al nivel y entra en la
     reconciliación (E2.5) como una sesión más.
   - Sin kWh: no suma; el intervalo entre los dos valores absolutos que la
     rodean no se marca como incoherente y sus trayectos quedan fuera de la
     mediana y del P75 (E2.4, E2.7).
   - Detección: solo se evalúa el intervalo que termina en la última
     lectura. Si no tiene cargas externas y su reconciliación da un residuo
     positivo (consumo estimado − consumo real: más energía de la
     explicable) mayor que `umbral_carga_externa_kwh` (parámetro nuevo, 5,
     usuario), se genera el aviso «¿Cargaste fuera de casa?». Desaparece con
     la siguiente lectura o al registrar una carga externa en ese intervalo.
   - Coste: el gasto del coche en Grafana («Energía: coche» y «Energía:
     batería») suma el coste de las cargas externas, separado del de casa.
   - Tests: carga externa con kWh (reconciliación exacta), sin kWh (intervalo
     excluido), aviso por residuo y anulación.
8. Etapa 3 (migración 0020 y siguientes; detalle en `docs/fase2.md`):
   - `bateria_parametro_grupo` (grupo, nombre, pregunta, avanzado,
     `abierto_movil`) y, en `bateria_parametro_clave`, `grupo`, `orden` y
     `editable`. Pantallas 7.5 y 7.6 los leen.
   - `planificador_modo` (con historial) para el selector de modo de 7.9.
   - Plan de carga lee `v_plan_vigente`, `v_plan_efectivo` y `v_plan_ahorro`;
     las franjas manuales, los bloqueos, las tareas propias y la auditoría
     llegan en 3c.

## 9. Criterios de aceptación de las pantallas

Estado: Inicio (7.1) hecho en v0.5.4; el resto de pantallas, en v0.5.5. Etapa
3 (Plan de carga, Próximas cargas, Parámetros agrupados): paso 3b (v0.6.1).

Comprobación en local (fuera de la CI):

- Desbordamiento: `docker build -f tests/ui/Dockerfile --target test-ui .`
- Capturas de cada pantalla junto a su maqueta, en los dos tamaños:
  `docker build -f tests/ui/Dockerfile --target capturas --output type=local,dest=capturas .`
  (en móvil, la barra inferior fija aparece a media página en las capturas
  de página completa; es un efecto de la captura).

- Cada pantalla reproduce la maqueta en 1280×900 y 390×844 (mismos bloques,
  orden, componentes y textos fijos).
- Test de desbordamiento con Playwright: ningún elemento sale de su contenedor
  en ambos tamaños (salvo el contenido del contenedor desplazable de la línea
  de tiempo en móvil).
- Tests con cliente de pruebas: PRG (303) y 422 en cada formulario, mensajes
  de validación de las secciones 7.2, 7.8 y 7.9, zona horaria, cálculo del
  objetivo con `pct_objetivo`, estados de la casilla Nivel, contador de
  avisos.
- Etapa 3:
  - Parámetros: todas las claves aparecen una vez, en su grupo y orden; las de
    `editable = false` no tienen «Cambiar» y un POST con ellas da 422.
  - Plan de carga: una franja que cruza la medianoche se pinta como un bloque
    y una fila; la franja en curso solo ofrece «Detener»; la línea de tiempo
    colorea cada hora según el umbral vigente y marca las provisionales; el
    indicador «+N franjas después» cuenta las que quedan fuera de las 60 h.
  - Navegación: «Plan de carga» / «Plan» en segundo lugar; Historial
    accesible desde Inicio en móvil.
- Ninguna petición a recursos externos (CSP `default-src 'self'`).
