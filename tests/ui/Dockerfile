# Tests de interfaz con Playwright (formulario-maquetas.md, 9): solo en local, fuera de la CI.
#   docker build -f tests/ui/Dockerfile --target test-ui .
# Imagen oficial de Playwright 1.55.0 (Ubuntu 24.04, Python 3.12), fijada por digest.
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble@sha256:640d578aae63cfb632461d1b0aecb01414e4e020864ac3dd45a868dc0eff3078 AS test-ui
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Imagen desechable de tests: sin python3-venv, se instala en el Python del sistema.
ENV PIP_BREAK_SYSTEM_PACKAGES=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . pytest playwright==1.55.0
COPY tests ./tests
RUN pytest -q -p no:cacheprovider tests/ui

# Capturas de las pantallas junto a sus maquetas (tests/ui/capturas.py), para revisarlas a ojo:
#   docker build -f tests/ui/Dockerfile --target capturas --output type=local,dest=capturas .
FROM test-ui AS capturas-gen
COPY docs/maquetas ./docs/maquetas
RUN cd tests/ui && python3 capturas.py /capturas

FROM scratch AS capturas
COPY --from=capturas-gen /capturas /
