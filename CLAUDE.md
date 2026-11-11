# energia-monitor

Contexto de negocio: ver docs/contexto.md (leer antes de cualquier cambio).

## Alcance actual: fase 1
Colector de precios + PostgreSQL + dashboard de Grafana. La fase 2
(cargador Orbis Viaris) queda fuera hasta nuevo aviso.

## Estado
Esqueleto inicial: el paquete `energia_monitor` solo contiene un proceso
de espera y un test de humo. Todo lo demás está por implementar.

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
