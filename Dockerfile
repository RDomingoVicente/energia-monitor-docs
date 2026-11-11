FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Etapa de tests: docker build --target test .
FROM base AS test
COPY tests ./tests
RUN pip install --no-cache-dir pytest && pytest -q

# Etapa de ejecución (por defecto)
FROM base AS runtime
RUN useradd --system --uid 10001 --no-create-home app
USER app
CMD ["python", "-m", "energia_monitor"]
