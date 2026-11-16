# Formulario de batería: maquetas aprobadas

Estado a 15/11/2026, alineado con v0.5.1 (migración 0015). Complementa a
`docs/fase2.md` (sección «Etapa 2: batería y previsión», E2.9). La
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
  noche del 15/11, dos trayectos el lun 16/11). Las cifras son coherentes entre
  pantallas, pero no son datos reales ni casos de test.

## 2. Principios

- HTML generado en el servidor. Formularios clásicos con POST, redirección 303
  y mensaje en la página destino (PRG); errores de validación con 422 y el
  formulario relleno.
- Sin JavaScript imprescindible. Mejora opcional permitida: activar los campos
  km/% al elegir «Indicar consumo» (sin JS se envían igual y el servidor ignora
  los que no correspondan) y abrir/cerrar los desplegables de Ayuda
  (`<details>`, nativo).
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
| aviso | `#D29922` (fondo `#2B2111`, borde `#5A4316`) | ESTIMADO, reserva, avisos |
| error | `#F85149` (fondo `#3D1418`) | acciones destructivas, errores |
| info | `#58A6FF` (fondo `#132339`) | LEÍDO + CARGA, «con salida», info |

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
segmentado (`.seg`), banner de estado (ok/aviso), barra de batería. Todos están
en las maquetas con sus estilos exactos.

Barra de batería: azul hasta el nivel de la última lectura; verde para lo
cargado desde la lectura (solo LEÍDO + CARGA); rayado hasta el objetivo si
falta energía; marca ámbar en la reserva y blanca en el objetivo.

### Responsive

- Corte en 900 px.
- ≥ 900 px: barra lateral de 220 px (Inicio, Excepciones, Horario, Historial,
  Parámetros, Avisos; abajo Ayuda, Grafana, /salud) y cabecera con ruta y
  chips de estado. Contenido en rejilla (ver cada pantalla).
- < 900 px: una columna, barra inferior (Inicio, Excepciones, Horario,
  Historial, Ajustes) y en la cabecera la hora del recálculo, la campana de
  avisos y «?» (Ayuda). Tablas convertidas en listas.
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
  (ver Ayuda).
- Validado en las maquetas: ningún elemento desborda su contenedor en 1280 y
  390 px. Repetir la comprobación con la implementación (test con Playwright
  en 1280×900 y 390×844).

## 5. Convenciones de texto

- `%` solo para niveles absolutos (52 %, objetivo 59 %). Diferencias entre
  niveles en `pts` (puntos de batería), con kWh cuando ayuda:
  «+11 pts de margen (≈10 kWh)». 1 pt = `capacidad_util_kwh` / 100.
- kWh con un decimal; porcentajes enteros; pts enteros salvo medias (1 decimal).
- Etiquetas de estado del nivel: `LEÍDO`, `LEÍDO + CARGA`, `ESTIMADO`.
- Eventos: «lectura 52 %», «conexión», «trayecto est. −9,2 kWh», «carga +19,3
  kWh», «inicio carga (en curso)». Trayecto partido por una lectura con algún
  tramo reconciliado y otro no (v0.5.7, migración 0018): el total y lo real,
  «trayecto est. −31,7 kWh (11,0 reales)»; «est.» mientras quede algún tramo
  estimado.
- El campo de lectura usa como texto de ejemplo «0–100», nunca un número.

## 6. Navegación, cabecera y avisos

- Cabecera de escritorio: `formulario / <ruta>`, chip de avisos (solo si hay),
  chip del cargador (verde si la última muestra tiene menos de
  `hueco_muestreo_aviso_s`, rojo si no), chip de PostgreSQL y hora del último
  recálculo.
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
  Energía necesaria); debajo, a la izquierda, panel Batería y «Próximos 7
  días»; a la derecha, banner, «Anotar lectura» y «Últimos eventos» (5).
  Móvil: banner, Batería, Coche/Necesita, Anotar lectura, próximos 3 días.
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
  de casa? Registrar carga externa →».

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

Maqueta 05.

- Tabla de vigentes: nombre (y clave debajo), valor, rango, origen, desde,
  «Cambiar». Lista y rangos:

| Clave | Nombre en pantalla | Valor | Rango |
|---|---|---|---|
| `capacidad_util_kwh` | Capacidad útil | 91,3 kWh | 50–120 |
| `reserva_pct` | Reserva mínima | 25 % | 10–60 |
| `tope_pct` | Tope de carga | 100 % | 50–100 |
| `factor_bateria` | Eficiencia de carga | 0,86 | 0,70–1,00 |
| `percentil_planificacion` | Percentil de planificación | 0,75 (P75) | 0,50–0,95 |
| `ventana_consumo_dias` | Ventana de consumo | 28 días | 7–90 |
| `desconexion_min_s` | Desconexión sin consumo | 30 min (1800 s) | 0–4 h |
| `consumo_inicial_trayecto_kwh` | Consumo inicial por trayecto | 25 kWh | 1–90 |
| `consumo_kwh_100km` | Consumo medio | 23 kWh/100 km | 10–40 |
| `energia_min_calibracion_kwh` | Energía mínima para calibrar | 5 kWh | 1–30 |
| `observaciones_calibracion` | Observaciones para calibrar | 5 | 1–20 |
| `detectar_carga_completa` | Detectar carga completa | sí | sí/no |
| `hueco_muestreo_aviso_s` | Aviso hueco de muestreo | 30 min (1800 s) | 5 min–4 h |
| `aviso_sin_lectura_dias` | Aviso sin lecturas | 7 días | 1–14 (días enteros) |
| `umbral_carga_externa_kwh` | Umbral de carga externa | 5 kWh | 1–30 |

- La clave del parámetro no cambia (`factor_bateria`); solo el nombre en
  pantalla es «Eficiencia de carga». Las claves guardadas en segundos o días
  se muestran en minutos, horas o días.
- Panel «Calibración sugerida»: valor observado, n, rango, vigente y botón
  «Registrar X como valor nuevo». No se aplica solo.
- «Cambiar un valor»: parámetro, valor, desde, nota; fuera de rango, 422 con
  el rango admitido. Mismas reglas que el CLI.
- «Últimos cambios»: los cinco más recientes.

### 7.6 Ayuda (`/ayuda`)

Maqueta 06.

- Secciones: Cómo funciona (diagrama de flujo y ejemplo de cálculo con los
  valores vigentes, empezando por «1 pt = capacidad útil / 100»), Estados del
  nivel, Pantallas, Parámetros (vigente, rango, qué es, efecto al cambiarlo),
  Glosario.
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

## 9. Criterios de aceptación de las pantallas

Estado: Inicio (7.1) hecho en v0.5.4; el resto de pantallas, en v0.5.5.

Comprobación en local (fuera de la CI):

- Desbordamiento: `docker build -f tests/ui/Dockerfile --target test-ui .`
- Capturas de cada pantalla junto a su maqueta, en los dos tamaños:
  `docker build -f tests/ui/Dockerfile --target capturas --output type=local,dest=capturas .`
  (en móvil, la barra inferior fija aparece a media página en las capturas
  de página completa; es un efecto de la captura).


- Cada pantalla reproduce la maqueta en 1280×900 y 390×844 (mismos bloques,
  orden, componentes y textos fijos).
- Test de desbordamiento con Playwright: ningún elemento sale de su contenedor
  en ambos tamaños.
- Tests con cliente de pruebas: PRG (303) y 422 en cada formulario, mensajes
  de validación de las secciones 7.2 y 7.8, zona horaria, cálculo del objetivo con
  `pct_objetivo`, estados de la casilla Nivel, contador de avisos.
- Ninguna petición a recursos externos (CSP `default-src 'self'`).
