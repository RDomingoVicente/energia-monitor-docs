# energia-monitor

Colector de precios del mercado eléctrico español y cálculo del precio final
según contrato, con PostgreSQL y visualización en Grafana.

- Contexto y decisiones: [docs/contexto.md](docs/contexto.md)
- Convenciones de desarrollo: [CLAUDE.md](CLAUDE.md)
- Despliegue en el NAS: [deploy/](deploy/)

## Desarrollo

    docker build --target test .     # tests
    docker build -t energia-monitor:dev .

## Publicación

    git tag v0.1.0 && git push origin v0.1.0

El workflow de Gitea Actions construye la imagen y la publica en Harbor.
