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
| PVPC 2.0TD frente a mi precio | Curva horaria del PVPC (ESIOS 1001) y del precio final; ambos con IEE e IVA |
| PVPC: mes en curso | Medias simples del mes y diferencia, ambos con IEE e IVA (positiva: el contrato es más caro) |
| Diferencia diaria con el PVPC | Últimos 30 días, ambos con IEE e IVA |
| Estado de los componentes | Real, estimado, regulado o contrato en el tramo actual |
| Origen de SSAA por día | Últimos 10 días y mañana: esios, prevision_ree, perfil_14d o parametro |

Los paneles de ESIOS necesitan `ESIOS_TOKEN` en el colector; sin él quedan
vacíos y el resto del dashboard no cambia. Vistas: `v_comparacion_pvpc`
(PVPC frente al contrato, por hora) y las columnas `ssaa_origen`,
`ssaa_metodo` y `estado_componentes` de `v_precio_final`.

### Fila "Coche" (fase 2, etapa 1)

Datos del servicio `muestreo` (requiere `VIARIS_URL`). Euros con IEE e IVA;
kWh del coche corregidos con el factor del contador del cargador
(`cargador_parametro.factor_contador`, estimado).

| Panel | Qué muestra |
|-------|-------------|
| Gasto del coche | Euros y kWh de hoy, semana y mes en curso; kWh estimados del mes aparte |
| Gasto en el rango | Euros, kWh y €/kWh medio del rango del selector; kWh estimados aparte |
| Precio medio pagado | €/kWh del coche (ponderado) frente a la media simple del precio final en el mismo rango, hasta ahora |
| Frente al PVPC | Coste real y coste con el PVPC de la misma energía (solo tramos con PVPC) |
| Estado del cargador | Estado del conector, potencia del coche y de la casa (sin verificar; vacía mientras carga) y antigüedad de la última muestra (rojo desde 180 s) |
| Gasto diario del coche (30 días) | Euros medidos y estimados apilados y kWh en el eje derecho |
| Reparto por periodo | kWh, euros y €/kWh por periodo 2.0TD en el rango |
| Sesiones recientes | Últimas 15 sesiones: inicio, fin, kWh, euros, €/kWh, origen (medida o estimada) y diferencia con la energía del cargador |

Origen de la energía: `muestreo` (muestras de potencia) o `estimado_sesion`
(sesiones anteriores al muestreo, repartidas a potencia constante). La
potencia de la casa sigue sin verificar y, durante una carga, es desconocida:
el cargador da `homePower` = 0 mientras carga (verificado el 14/11/2026), así
que esos tramos no tienen energía ni coste de la casa. Vistas:
`v_energia_tramo`, `v_coste_tramo` (por tramo y consumidor) y
`v_sesion_coste`.

Energía: la fila lee la tabla `energia_tramo` (migración 0013), que el
muestreo mantiene al guardar cada muestra. Muestras insertadas o borradas por
SQL no se ven hasta ejecutar `cargador recalcular` (docs/parametros.md).

Precios: la fila "Coche" (y la comparación con el PVPC) leen
`mv_precio_final`, copia materializada de `v_precio_final` que el colector
refresca al terminar cada ejecución. Un cambio hecho por SQL en parámetros,
precios o series de ESIOS no se ve hasta ejecutar `refrescar`
(docs/parametros.md). Los paneles de la fila de precios siguen leyendo
`v_precio_final` en directo.

El JSON está en el repositorio: los cambios hechos en la interfaz de Grafana
se exportan (Share → Export) y se guardan aquí.
