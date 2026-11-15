FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY db ./db
ENV MIGRATIONS_DIR=/app/db/migrations

# Etapa de tests: docker build --target test .
FROM base AS test
COPY tests ./tests
# Los tests validan el dashboard; la imagen de ejecución no lo incluye.
COPY grafana ./grafana
RUN pip install --no-cache-dir pytest && pytest -q

# Etapa de ejecución (por defecto)
FROM base AS runtime
# UID y GID fijos (10001): los certificados del formulario se entregan a ese
# dueño (deploy/README.md).
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid 10001 --no-create-home app
USER app
CMD ["python", "-m", "energia_monitor"]
