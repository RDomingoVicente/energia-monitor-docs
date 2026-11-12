# Proyecto: Monitor de precio de la energía (mercado eléctrico español)

## Objetivo
Servicio propio que muestre el precio del mercado eléctrico (OMIE) y mi precio
real según contrato, para decidir cuándo cargar el coche eléctrico. En una
segunda fase, programar automáticamente el cargador en las horas más baratas.

## Contexto del suministro (vivienda)
- Comercializadora: Feníe Energía, contrato indexado, tarifa de acceso 2.0TD.
- Potencia contratada: 4,6 kW (P1 y P2).
- Margen de la comercializadora (RFE): 20,00 €/MWh. Desvíos asumidos por Feníe.
- Fórmula del término de energía (cláusula 4.2 del contrato), por periodo de
  liquidación p:

      TEV = Consumo_p × [ ( (OMIE_p + SSAA_p + PC_p + ROS&OM_p) × (1 + %Pérdidas_p)
            + FNEE + RFE ) × (1 + %TasaMunicipal) + Peaje_p + Cargo_p ]

- Peajes + cargos de energía 2.0TD (valores de factura de octubre 2026, €/kWh):
  - P1: 0,033261 + 0,064292 = 0,097553
  - P2: 0,016409 + 0,012858 = 0,029267
  - P3: 0,000077 + 0,003215 = 0,003292
- Impuestos sobre el total: impuesto eléctrico 5,11269632 % e IVA 21 %.
- OMIE medio de octubre 2026: 166,59 €/MWh (media simple de los 2980 cuartos
  de hora del 01/10 al 31/10, ficheros marginalpdbc de OMIE, precio España).
- Factura de octubre 2026 (periodo 01/10/2026–31/10/2026): precio coste
  energía facturado 0,274447 (P1), 0,279393 (P2), 0,279020 (P3) €/kWh, con
  consumos de 182, 240 y 840 kWh respectivamente. Feníe no desglosa SSAA,
  pagos por capacidad, ROS&OM, pérdidas, FNEE ni tasa municipal, así que la
  conciliación con la factura no es un test: queda pendiente hasta tener la
  curva horaria de consumo (las series de ESIOS ya están disponibles).

## Periodos horarios 2.0TD (Península)
- P1 punta: laborables 10:00–14:00 y 18:00–22:00.
- P2 llano: laborables 08:00–10:00, 14:00–18:00 y 22:00–24:00.
- P3 valle: laborables 00:00–08:00; sábados, domingos y festivos nacionales
  las 24 h.
- El precio de mercado varía por tramo de liquidación (actualmente
  cuartohorario) con independencia de estos periodos; los periodos solo
  determinan peajes y cargos.

## Fuentes de datos
- API de ESIOS (REE), https://api.esios.ree.es, con token personal (variable
  ESIOS_TOKEN; nunca en ficheros ni en CI). Detalle verificado más abajo.
- Alternativas sin token: API REData (apidatos.ree.es) y ficheros diarios de
  OMIE. Verificar formato y disponibilidad antes de usarlas.
- Los precios del día siguiente se publican hacia el mediodía del día anterior.

## ESIOS: indicadores verificados (12/11/2026)

Comprobado contra la API real; ningún identificador sin verificar.

- Petición: `GET /indicators/{id}?start_date=...Z&end_date=...Z&geo_ids[]=8741`,
  fechas en UTC con ambos extremos incluidos. Cabecera `x-api-key: <token>`
  (también vale `Authorization: Token token="..."`). Sin token, el catálogo
  `/indicators` responde 403. `/indicators/{id}` respondió también sin token,
  pero no se cuenta con ello.
- Respuesta: `indicator.values[]` con `value` (€/MWh, 2 decimales),
  `datetime_utc`, `geo_id`; `indicator.values_updated_at` es la última
  actualización de REE. 8741 = Península.

### Servicios de ajuste (SSAA)

No hay un indicador llamado "SSAA". El precio final de la demanda en
contratación libre (804, cuartohorario) se descompone exactamente, con un error
de redondeo de hasta 0,05 €/MWh en todo 2026:

    804 = 792 mercado diario + 795 intradiario + 801 pagos por capacidad
        + 10379 restricciones técnicas + 10382 procesos OS + 10525 participación servicios
    10379 = 793 restricciones PBF + 794 restricciones tiempo real + 2401 control de tensión (coste)
    10382 = 798 banda secundaria + 799 desvíos + 800 saldo desvíos + 802 saldo P.O.14.6
          + 1366 incumplimiento energía de balance + 2389 RAD incumplimiento energía activada
          + 2392 intercambios apoyo con precio + 2395 coste a BRP servicio de respuesta activa
          + 2398 incumplimiento aFRR

(todos "contratación libre", cuartohorarios, €/MWh en barras de central).

- **Definición: SSAA = 10379 + 10382 + 10525** (sin intradiario). Se guardan
  los 13 subcomponentes y la suma la decide la tabla
  `contrato_ssaa_componente` (por contrato y con vigencia); 10379 y 10382 se
  descargan solo para comprobar que la suma cuadra (si no, REE ha añadido un
  componente).
- **Duda abierta sobre los desvíos.** El contrato dice "Sobrecoste de
  desvíos: 100% asumido por Fenie Energía". No está claro si se refiere a los
  desvíos propios de Feníe o a los componentes de desvíos del sistema
  repercutidos a la demanda (799 desvíos y 800 saldo de desvíos, incluidos en
  10382). Por defecto se incluyen todos; se resolverá conciliando una factura
  con la curva horaria de i-DE. Para excluirlos, ver docs/parametros.md.
- **Publicación.** REE actualiza estas series cada día hacia las 08:08 (hora
  de Madrid). El día D aparece ya en D con valores parciales (restricciones
  PBF y banda); tiempo real, desvíos e intradiario se completan en D+2. Por
  eso un día cuenta como real solo si `values_updated_at` es de D+2 o
  posterior (`esios_indicador.dias_hasta_completo`). Los valores pueden
  cambiar después (p. ej., en el 10/11, desvíos de +42,9 y saldo de −30,3
  €/MWh en la primera publicación); el colector guarda el primer valor visto
  y registra cada revisión (`python -m energia_monitor esios estado`).
- Medias mensuales de 2026 (€/MWh): ene 14,37 · feb 24,13 · mar 27,38 ·
  abr 20,86 · may 20,20 · jun 16,41 · jul 17,32 · ago 17,22 · sep 24,53 ·
  oct 23,45 · nov (1–10) 20,59. El estimado anterior (31,5) era alto.

### Previsión de SSAA y PVPC

- **1871** "Desglose peaje por defecto 2.0TD Servicios de ajuste": horario,
  se publica en D−1 hacia las 20:45. Es la previsión de SSAA del PVPC (sin
  escalar, un 7 €/MWh por encima del valor de contratación libre de media).
- **1001** "Término de facturación de energía activa del PVPC 2.0TD":
  horario, 5 zonas, publicado en D−1 hacia las 20:45. Incluye peajes y cargos
  (coinciden con los del contrato: 97,55 / 29,27 / 3,29 €/MWh) y no incluye
  IEE ni IVA. Coincide con 10391 (suma de componentes).
- **ROS**: no hay serie de mercado. Solo 1877 ("Financiación OS" del
  desglose del PVPC 2.0TD, horario, 0,23–0,25 €/MWh redondeado a 2 decimales),
  que no es lo que repercute Feníe. Se mantiene como parámetro.

### Estimación de SSAA sin valor publicado

Orden: real de ESIOS > `prevision_ree` (1871 × real/1871 de los últimos 28
días) > `perfil_14d` (media por hora local de los 14 últimos días con real,
separando laborables de fines de semana y festivos) > parámetro estimado.
Contraste con 269 días de 2026 usando solo lo disponible a las 20:30 de D−1
(MAE horario / MAE de la media diaria, €/MWh): 1871 escalado 3,38 / 1,96;
perfil 14 días 5,35 / 3,40; perfil 28 días 5,60 / 3,84.

`prevision_ree` solo se usa cuando se ha comprobado que REE no corrige lo que
publica la víspera: el colector guarda el 1871 de cada noche y lo compara con
las descargas posteriores. Hasta tener 7 días comprobados (o si hay alguna
corrección) se usa el perfil de 14 días.

## Infraestructura disponible
- NAS Synology (DSM) con Docker / Container Manager; despliegues en
  /volume1/docker/<servicio>.
- Acceso por Tailscale (el NAS no está en la LAN de la vivienda).
- En el NAS: Gitea (con runner de Gitea Actions) y registry Harbor, con TLS
  mediante el certificado de Tailscale.
- Grafana existente (stack de monitorización de Odoo: cAdvisor, Prometheus,
  Loki, Promtail, Alertmanager).

## Arquitectura decidida
1. Colector en Python (contenedor Docker en el NAS):
   - Ejecución diaria tras la publicación de precios, con reintento por la tarde.
   - Descarga el precio del mercado diario (hoy y mañana) y lo inserta de forma
     idempotente.
   - Recarga de históricos bajo demanda.
2. PostgreSQL dedicado (mismo docker compose):
   - Tabla de precios por tramo (timestamptz como clave, €/MWh, fuente).
   - Tabla de parámetros del contrato con vigencia temporal (RFE, peajes,
     cargos, pérdidas, impuestos), para no codificar constantes.
   - Calendario de festivos nacionales.
   - Vista con el periodo tarifario de cada tramo y el precio final en €/kWh.
3. Grafana existente: nuevo datasource PostgreSQL y dashboard con:
   - Precio actual (€/MWh) y precio final (€/kWh).
   - Curva de hoy y de mañana.
   - Tabla con los tramos más baratos de las próximas 24–36 h.
   - Media diaria y mensual acumulada.
4. No usar Prometheus para los precios (datos con fecha futura, recargas de
   histórico y cálculos de fórmula encajan mejor en SQL).

## Fase 2: automatización de la carga del vehículo
- Cargador: Orbis Viaris, identificador EVVC3C9791D30. Admite MQTT (broker
  local), HTTP y OCPP 1.6; API documentada en support.orbis.com.es.
- Límite: 16 A (3,7 kW) por la potencia contratada de 4,6 kW.
- Franjas configuradas hoy en la app e-VIARIS: 01:00–06:00 (base, activa),
  00:00–01:00, 06:00–08:00, 10:00–14:00 y 14:00–18:00 (inactivas).
- Lógica prevista: elegir los N tramos más baratos en precio final (mercado +
  peaje del periodo) según los kWh necesarios y enviarlos al cargador.
- Pendiente: el cargador está en la LAN de la vivienda y el NAS fuera de ella.
  Hace falta un nodo en esa LAN (subnet router de Tailscale o broker MQTT
  local).
- Pendiente: confirmar en la API de Orbis el topic o método para programar
  franjas o iniciar y detener la carga.
- Alternativa a evaluar antes de programar: evcc (carga inteligente de código
  abierto, con tarifa ESIOS y soporte OCPP).

## Posibles ampliaciones
- Segundo suministro (local, 3.0TD, 17,5 kW, seis periodos por temporada), con
  sus propios parámetros de contrato.
- Importar la curva de consumo horaria de la distribuidora (i-DE) para
  recalcular facturas y validar importes.
- Alertas cuando el precio medio del día siguiente supere o baje de un umbral.

## Cuestiones abiertas
- En qué máquina corre el Grafana actual y cómo alcanza al PostgreSQL del NAS.
- Margen (RFE) y fórmula del contrato del local, si se añade.
