# Grafana

Grafana corre en el NAS (rdv-nas, 100.102.118.77:3000) dentro del stack de
monitorización de Odoo. Llega a PostgreSQL por una red Docker compartida; el
puerto publicado 5434 del NAS no interviene.

## 1. Unir Grafana a la red de energia-monitor

En el compose del stack donde está Grafana:

```yaml
services:
  grafana:
    networks:
      - default        # mantener las redes que ya tenga
      - energia

networks:
  energia:
    name: energia-monitor_default
    external: true
```

La red la crea energia-monitor: levantarlo antes y después recrear Grafana
(`docker compose up -d grafana`).

## 2. Datasource PostgreSQL

| Campo    | Valor                                         |
|----------|-----------------------------------------------|
| Host     | `energia-db:5432`                             |
| Database | `energia` (POSTGRES_DB)                       |
| User     | `grafana_ro`                                  |
| Password | la de `GRAFANA_DB_PASSWORD` en el `.env`      |
| TLS/SSL  | disable                                       |
| Version  | 17                                            |

`grafana_ro` lo crea la migración 0006 sin login; el colector le pone la
contraseña de `GRAFANA_DB_PASSWORD` al arrancar. Solo tiene SELECT.

Comprobación desde el contenedor de Grafana:

    docker exec <contenedor-grafana> getent hosts energia-db

## Dashboard

Pendiente (JSON en este directorio).
