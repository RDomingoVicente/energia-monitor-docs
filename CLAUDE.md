# energia-monitor

Antes de cualquier cambio, leer:

- docs/contexto.md: suministro, contrato, fórmula de precio y fuentes de datos.
- docs/fase2.md: especificación de la fase 2 y decisiones tomadas.
- docs/viaris-api.md: API local del cargador (qué está verificado y qué no).

## Estado

- Fase 1 en producción desde el 12/11/2026: colector de OMIE y ESIOS,
  PostgreSQL, precio final según contrato, comparación con el PVPC y dashboard
  de Grafana. Migraciones 0001 a 0009 publicadas.
- Etapa 1 de la fase 2 cerrada el 14/11/2026 (v0.4.0): migraciones 0010
  (tablas del cargador) y 0011 (energía y coste), servicio `muestreo` en el
  compose y fila "Coche" del dashboard (hoy "Energía: coche"). Pendiente conocido: calibración del
  cargador frente a la curva de i-DE.
- v0.4.1 en producción desde el 14/11/2026: migración 0012, `mv_precio_final` (copia
  materializada de `v_precio_final`) que leen la fila "Coche" y la comparación
  con el PVPC. Se refresca al final de cada ejecución del colector y de los
  comandos que cambian sus datos; tras un cambio por SQL, `refrescar`.
  Migración 0013: tabla `energia_tramo`, que mantiene el muestreo (tras
  cambiar muestras por SQL, `cargador recalcular`), y casa desconocida (NULL)
  en los tramos con carga.
- Fase 2 en curso, por etapas (docs/fase2.md). Etapa 2, batería y
  previsión (tarea: docs/tareas/etapa-2-bateria.md, cuatro pasos que se
  revisan uno a uno). Paso 1 hecho: dashboards separados por tema
  (`grafana/energia-precios.json` y `grafana/energia-coche.json`). Paso 2
  hecho (v0.5.0): migración 0014 (nivel de batería, trayectos,
  reconciliación y previsión, en `recalcular_bateria()`), CLI `bateria` y
  recálculo desde el muestreo; referencia en Python en
  `src/energia_monitor/bateria.py`. Paso 2b hecho (v0.5.1): migración 0015,
  excepciones `con_salida`/`sin_salida` (validación en
  `bateria_servicio.registrar_excepcion`). Paso 3a hecho (v0.5.2): migración 0016 (confianza,
  cargas externas, avisos con historial, rangos de parámetros, rol
  `formulario_rw`) y CLI `bateria carga-externa`. v0.5.3: migración 0017,
  cierre `sin_muestreo` para sesiones anteriores al muestreo. Paso 3b,
  primera entrega (v0.5.4): servicio `formulario` (HTTPS, Inicio y lectura;
  `src/energia_monitor/formulario/`, deploy/README.md). Paso 3b, segunda
  entrega (v0.5.5): resto de pantallas y tests de interfaz con Playwright
  (tests/ui/, solo en local). v0.5.6: arreglos de la Ayuda, logo y favicon.
  Paso 4 hecho (solo JSON): `grafana/energia-bateria.json` y casa · fuera en
  «Energía: coche»; pendiente importarlo y la aceptación en el NAS.
  v0.5.7: migración 0018 (trayecto partido en los eventos: total y parte
  real) y estado del coche (conectado/cargando/sin cargar desde). Paso 4
  aceptado en el NAS el 16/11/2026. v0.5.8: migración 0019 (cargas con la
  energía medida y la estimada a batería). Falta la aceptación final de la
  etapa 2.
- **Etapa actual: 3, planificador de carga**, autorizada el 18/11/2026
  (sección «Etapa 3» de docs/fase2.md; tarea: docs/tareas/etapa-3-planificador.md;
  pasos 3a a 3d que se revisan uno a uno). Paso 3.0 hecho: pruebas del
  cargador del 17 y 18/11 (docs/viaris-api.md). Paso 3a (v0.6.0, migración
  0020): modelo, disponibilidad, algoritmo (`plan_carga/`), modo sombra en
  el muestreo y CLI `planificador plan` y `simular`, **sin escrituras en el
  cargador**; desplegado y aceptado en el NAS el 18/11/2026. **Paso actual:
  3b** (pantalla Plan de carga, Próximas cargas en Inicio, Parámetros por
  grupos, Ayuda y navegación; solo lectura), con las maquetas de
  docs/formulario-maquetas.md y docs/maquetas/ (01d, 01a–01c, 05). Tareas del cargador hoy: 5 y 6 del usuario
  (app de Orbis, inactivas) y 9, la guardiana; el código no toca ninguna.
  Después de la etapa 3, calibración con Datadis (docs/fase2.md).

## Estructura

- src/energia_monitor/   código del colector (formulario/: página de batería por HTTPS;
                         plan_carga/: planificador de carga; planificador.py es el bucle del colector)
- tests/                 pytest, con respuestas reales guardadas como fixtures
- db/migrations/         migraciones SQL versionadas (NNNN_descripcion.sql)
- grafana/               dashboards en JSON (uno por tema) y notas del datasource
- deploy/                docker-compose.yml, .env.example y README.md (formulario: certificados, arranque, ACL)
- docs/                  contexto, especificaciones y tareas (docs/tareas/)
- .gitea/workflows/      CI (tests en cada push; imagen a Harbor en cada tag v*)

## Comandos

- Tests (igual que en CI):   docker build --target test .
- Tests en local:            pip install -e . pytest && pytest
- Imagen de ejecución:       docker build -t energia-monitor:dev .
- Tests de interfaz (local, fuera de la CI):
  docker build -f tests/ui/Dockerfile --target test-ui .
- Capturas junto a las maquetas:
  docker build -f tests/ui/Dockerfile --target capturas --output type=local,dest=capturas .
- Tanda larga del planificador (8000 casos aleatorios, fuera de la CI):
  PLAN_LENTO=1 pytest -m lento tests/test_plan_carga.py
- Planificador (sombra, no escribe en el cargador):
  python -m energia_monitor planificador plan [--instante "AAAA-MM-DD HH:MM"]
  python -m energia_monitor planificador simular --dia AAAA-MM-DD [--desde HH:MM]

## Convenciones

- Python 3.12, dependencias mínimas (`psycopg[binary]`, descargas con
  `urllib`), tipado y tests con pytest.
- Marcas temporales en UTC (`timestamptz`); Europe/Madrid solo para periodos
  tarifarios y presentación.
- Inserciones idempotentes (upsert).
- Parámetros en tabla con vigencia (`daterange '[)'`), nunca constantes en el
  código. Cada valor lleva su origen: contrato, regulado o estimado.
- Cálculos de dinero en SQL con `numeric`; referencia en Python puro para los
  tests.
- **Las migraciones publicadas son inmutables**: cualquier cambio va en una
  migración nueva (la siguiente es la 0021).
- Usuario `grafana_ro` de solo lectura; las tablas y vistas nuevas deben
  quedar legibles para él.
- Secretos solo por variables de entorno; `.env` fuera de git; el token de
  ESIOS nunca en el repositorio, los logs ni el CI.
- No usar endpoints, indicadores ni campos sin verificarlos contra el sistema
  real. Lo no verificado se marca como tal en la documentación.
- El CI no llama a servicios externos ni al cargador.
- El contenedor se ejecuta con usuario sin privilegios.

## Reglas para el cargador (obligatorias)

El cargador es un equipo físico en producción, sin autenticación.

- Todo acceso pasa por el cliente del cargador, que comprueba método y ruta
  contra una lista cerrada. Nada de peticiones HTTP sueltas.
- Hasta el paso 3c de la etapa 3 solo se permiten los GET del muestreo. En
  3c entran el GET del programador y las escrituras, limitadas a
  `/modules/scheduler/elements/<conector>`.
- Rutas prohibidas siempre: `/reset/sys`, `/modules/network`, y cualquier
  escritura en ocpp, mqtt_user, solar, spl, modbus, `/elements` y `/device`.
- No registrar en logs las respuestas de `/modules/network` ni de
  `/modules/mqtt_user`.
- Los tests nunca contactan con el cargador real.
- Las pruebas contra el cargador real las hace el usuario; proponer el
  comando y esperar el resultado. Usar la skill `viaris`.

## Forma de trabajo

- Cada tarea parte de un documento de docs/tareas/. Primero se presenta un
  plan (ficheros, migraciones, tests) y se espera la aprobación; después se
  implementa.
- Si la especificación y la realidad no coinciden, parar y preguntar; no
  improvisar una alternativa.
- Al terminar una tarea: actualizar la documentación afectada y resumir qué
  queda verificado y qué pendiente.

## Despliegue

- Un tag vX.Y.Z publica la imagen en Harbor (ver .gitea/workflows/ci.yml).
- En el NAS: deploy/docker-compose.yml + .env en /volume1/docker/energia-monitor.
- Cambiar el `.env` exige `docker compose up -d`; un `restart` no relee las
  variables.
