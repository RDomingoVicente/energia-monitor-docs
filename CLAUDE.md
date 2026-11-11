# energia-monitor

Contexto de negocio: ver docs/contexto.md (leer antes de cualquier cambio).

## Alcance actual: fase 1
Colector de precios + PostgreSQL + dashboard de Grafana. La fase 2
(cargador Orbis Viaris) queda fuera hasta nuevo aviso.

## Estado
Hechas: migraciones 0001–0007 (precios, contrato con vigencias, parámetros
estimados, festivos y periodos, vista v_precio_final, rol grafana_ro), fuente
OMIE, upsert de precios y tests (unitarios + integración con PostgreSQL).
Colector: `python -m energia_monitor [ejecutar|migrar|recargar]`; al arrancar
migra y descarga los días que falten desde CARGA_INICIAL_DESDE, y después
actualiza hoy y mañana en HORARIOS (13:30, 17:30, 20:30 Madrid).
Pendiente: dashboard de Grafana y ESIOS. SSAA y ROS son estimados (SSAA con
vigencia abierta desde 11/2026) hasta tener la serie de ESIOS.
Tests de integración en local: definir PG* (PGHOST incluido) y `pytest -m integration`.

## Estructura
- src/energia_monitor/   código del colector
- tests/                 pytest
- db/migrations/         migraciones SQL versionadas (NNNN_descripcion.sql)
- grafana/               dashboard(s) en JSON y notas del datasource
- deploy/                docker-compose.yml y .env.example para el NAS
- .gitea/workflows/      CI (tests en cada push; imagen a Harbor en cada tag v*)

## Comandos
- Tests (igual que en CI):   docker build --target test .
- Tests en local:            pip install -e . pytest && pytest
- Imagen de ejecución:       docker build -t energia-monitor:dev .

## Convenciones
- Python 3.12, dependencias mínimas, tipado y tests con pytest.
- Fuentes de precios detrás de una interfaz común (fuente sin token ahora,
  ESIOS cuando haya token), seleccionable con la variable PRICE_SOURCE.
- Conexión a PostgreSQL con las variables estándar de libpq
  (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD).
- Toda marca temporal en UTC (timestamptz); la zona Europe/Madrid solo se
  aplica al calcular periodos tarifarios y al presentar.
- Inserciones idempotentes (upsert por tramo y fuente).
- Parámetros del contrato en tabla con vigencia, nunca como constantes.
- Migraciones publicadas inmutables (desde el commit 5d79781): cualquier
  cambio va en una migración nueva. tests/test_migraciones.py guarda el hash
  de cada una; al publicar una nueva, añadir su hash.
- Valores de parámetros en el NAS: `python -m energia_monitor parametro
  registrar|listar` o SQL, según docs/parametros.md (sin versión nueva).
- Usuario de solo lectura para Grafana, creado por migración.
- Secretos solo por variables de entorno; .env fuera de git.
- No inventar endpoints ni identificadores de indicadores: verificarlos
  contra la documentación de la API antes de usarlos.
- El contenedor se ejecuta con usuario sin privilegios.

## Entregables de la fase 1
- Colector con planificación diaria y recarga de históricos.
- Migraciones: precios, parámetros de contrato, festivos, vista de precio final.
- Dashboard de Grafana en grafana/ con su datasource documentado.
- Test de validación con los datos de octubre de 2026 de docs/contexto.md.

## Despliegue
- Un tag vX.Y.Z publica la imagen en Harbor (ver .gitea/workflows/ci.yml).
- En el NAS: deploy/docker-compose.yml + .env en /volume1/docker/energia-monitor.
