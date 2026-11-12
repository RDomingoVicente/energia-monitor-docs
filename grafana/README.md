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

## 3. Dashboard

`energia-monitor.json` (Grafana 10.4, plugin `grafana-postgresql-datasource`).

Importar: Dashboards → New → Import → Upload dashboard JSON file. Arriba se
elige el datasource (variable `ds`), el contrato y la fuente. Rango por
defecto: hoy y mañana (zona Europe/Madrid).

| Panel | Qué muestra |
|-------|-------------|
| Precio final ahora / OMIE ahora / Periodo | Tramo actual |
| Precio final medio | Media de hoy y de mañana (mañana, tras publicar OMIE) |
| Precio final (€/kWh) y OMIE (€/MWh) | Curvas por tramo de 15 min en el rango elegido |
| Horas más baratas (próximas 36 h) | 12 horas con menor precio final medio |
| Media diaria | Últimos 30 días |
| Mes en curso | Media hasta ahora (simple, no ponderada por consumo) |
| Componentes del tramo actual | Parámetros estimados y los que falten |
| PVPC 2.0TD frente a mi precio | Curva horaria del PVPC (ESIOS 1001) y del precio final, con impuestos |
| PVPC: mes en curso | Medias simples del mes y diferencia (positiva: el contrato es más caro) |
| Diferencia diaria con el PVPC | Últimos 30 días |
| Estado de los componentes | Real, estimado, regulado o contrato en el tramo actual |
| Origen de SSAA por día | Últimos 10 días y mañana: esios, prevision_ree, perfil_14d o parametro |

Los paneles de ESIOS necesitan `ESIOS_TOKEN` en el colector; sin él quedan
vacíos y el resto del dashboard no cambia. Vistas: `v_comparacion_pvpc`
(PVPC frente al contrato, por hora) y las columnas `ssaa_origen`,
`ssaa_metodo` y `estado_componentes` de `v_precio_final`.

El JSON está en el repositorio: los cambios hechos en la interfaz de Grafana
se exportan (Share → Export) y se guardan aquí.
