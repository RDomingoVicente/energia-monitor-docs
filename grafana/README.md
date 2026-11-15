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

## 3. Dashboards

Un dashboard por tema (Grafana 10.4, plugin `grafana-postgresql-datasource`):

| Fichero | Título | `uid` |
|---------|--------|-------|
| `energia-precios.json` | Energía: precios | `energia-monitor` |
| `energia-coche.json` | Energía: coche | `energia-coche` |
| `energia-bateria.json` | Energía: batería | `energia-bateria` |

Importar cada uno: Dashboards → New → Import → Upload dashboard JSON file.
"Energía: precios" conserva el `uid` del antiguo dashboard único
(`energia-monitor.json`): al importarlo, Grafana avisa de que ya existe y lo
sobrescribe ("Import (Overwrite)"); desaparece la fila "Coche", que pasa al
dashboard nuevo. Los enlaces guardados al dashboard de precios siguen
valiendo.

Todos llevan la etiqueta `energia` y, arriba a la derecha, el enlace
"Energía", que lista los demás y conserva el rango y las variables
(`keepTime` e `includeVars`). Por eso tienen las mismas variables: el
datasource (`ds`), el contrato y la fuente. Rango por defecto: hoy y mañana
(zona Europe/Madrid).

### Energía: precios

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

### Energía: coche (fase 2, etapa 1)

Datos del servicio `muestreo` (requiere `VIARIS_URL`). Euros con IEE e IVA;
kWh del coche corregidos con el factor del contador del cargador
(`cargador_parametro.factor_contador`, estimado). «Casa» es lo cargado en
el cargador de casa; «fuera», las cargas externas anotadas en el formulario
(`v_coste_carga_externa`: coste pagado y kWh del cargador externo). Los
€/kWh de casa y de fuera van siempre por separado.

| Panel | Qué muestra |
|-------|-------------|
| Gasto del coche | Euros y kWh de hoy, semana y mes en curso, en casa y fuera; kWh estimados de casa del mes aparte |
| Gasto en el rango | Euros y kWh de casa y de fuera, €/kWh medio de cada uno y kWh estimados de casa, en el rango del selector |
| Precio medio pagado | €/kWh del coche en casa (ponderado) frente a la media simple del precio final en el mismo rango, hasta ahora; aparte, el €/kWh de fuera |
| Frente al PVPC | Coste real y coste con el PVPC de la misma energía (solo casa y solo tramos con PVPC) |
| Estado del cargador | Estado del conector, potencia del coche y de la casa (sin verificar; vacía mientras carga) y antigüedad de la última muestra (rojo desde 180 s) |
| Gasto diario del coche (30 días) | Euros de casa (medidos y estimados) y de fuera, apilados; kWh de casa + fuera en el eje derecho |
| Reparto por periodo | kWh, euros y €/kWh por periodo 2.0TD en el rango |
| Sesiones recientes | Últimas 15 sesiones: inicio, fin, kWh, euros, €/kWh, origen (medida o estimada) y diferencia con la energía del cargador |

Origen de la energía: `muestreo` (muestras de potencia) o `estimado_sesion`
(sesiones anteriores al muestreo, repartidas a potencia constante). La
potencia de la casa sigue sin verificar y, durante una carga, es desconocida:
el cargador da `homePower` = 0 mientras carga (verificado el 14/11/2026), así
que esos tramos no tienen energía ni coste de la casa. Vistas:
`v_energia_tramo`, `v_coste_tramo` (por tramo y consumidor) y
`v_sesion_coste`.

Energía: el dashboard lee la tabla `energia_tramo` (migración 0013), que el
muestreo mantiene al guardar cada muestra. Muestras insertadas o borradas por
SQL no se ven hasta ejecutar `cargador recalcular` (docs/parametros.md).

Precios: "Energía: coche" (y la comparación con el PVPC) leen
`mv_precio_final`, copia materializada de `v_precio_final` que el colector
refresca al terminar cada ejecución. Un cambio hecho por SQL en parámetros,
precios o series de ESIOS no se ve hasta ejecutar `refrescar`
(docs/parametros.md). Los paneles de "Energía: precios" siguen leyendo
`v_precio_final` en directo.

### Energía: batería (fase 2, etapa 2)

Nivel de batería y previsión (docs/fase2.md, E2.10). Sin migraciones: lee
`bateria_nivel`, `bateria_lectura`, `bateria_sesion`, `bateria_trayecto`,
`bateria_carga_externa` y las vistas `v_bateria_estado`, `v_bateria_salida`,
`v_bateria_lectura_historial`, `v_aviso` y `v_coste_carga_externa`. Lo
mantiene `recalcular_bateria()` (muestreo, formulario y CLI): tras cambiar
datos por SQL, `bateria recalcular` (docs/parametros.md).

| Panel | Qué muestra |
|-------|-------------|
| Nivel ahora | %, kWh y fiabilidad: LEÍDO (verde), LEÍDO + CARGA (azul) o ESTIMADO (ámbar), como en el formulario |
| Próxima salida | Día y hora, origen (horario o excepción) y consumo previsto |
| Objetivo de salida | % y kWh: reserva + consumo previsto (o el mínimo de la excepción), recortado al tope |
| Energía necesaria | kWh y pts que faltan para el objetivo |
| Avisos activos | Los del formulario, por nivel (error, aviso, info) |
| Nivel de batería (7 días) | Nivel estimado (reconciliado) y previsto (discontinua), lecturas como puntos, reserva y tope. Anotaciones: cargas en casa y trayectos como regiones (inicio y fin); cargas externas y cargas completas como puntos |
| Acierto de la estimación (30 días) | Error medio con signo y error absoluto medio (pts y kWh) de las lecturas comparables, como en la página Historial |
| Estimado frente a leído (7 días) | Leído − estimado en cada lectura comparable, en pts |
| Próximas salidas (7 días) | Salidas con origen, consumo previsto, objetivo y energía necesaria (la primera) |
| Trayectos recientes | Últimos 15 (sin desconexiones cortas): duración, consumo en kWh y pts, estimado y origen del consumo |
| Fuera de casa en el rango | Coste, kWh y €/kWh de las cargas externas del rango |
| Cargas externas (90 días) | Cada carga externa con kWh, pts a la batería, coste, €/kWh y nota |

Las anotaciones solo se dibujan en «Nivel de batería»; se apagan con sus
interruptores arriba del dashboard. El enlace «Grafana» del formulario
apunta aquí: `FORMULARIO_GRAFANA_URL=https://<grafana>/d/energia-bateria`
(deploy/.env.example).

Las gráficas temporales dibujan el rango del selector (hoy, por defecto).
Las que muestran un periodo fijo ("Precio final: media diaria", "Diferencia
diaria con el PVPC", "Gasto diario del coche (30 días)") lo fijan con
"Relative time" = 30d (`timeFrom` en el JSON), y las dos de batería ("Nivel
de batería" y "Estimado frente a leído") con 7d; sin él, los días anteriores
quedan fuera del eje aunque la consulta los devuelva.

El JSON está en el repositorio: los cambios hechos en la interfaz de Grafana
se exportan (Share → Export) y se guardan aquí.
