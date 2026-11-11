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
- Referencia de validación (octubre 2026): OMIE medio 165,25 €/MWh; precio
  coste energía facturado 0,274447 (P1), 0,279393 (P2), 0,279020 (P3) €/kWh,
  con consumos de 182, 240 y 840 kWh respectivamente.

## Periodos horarios 2.0TD (Península)
- P1 punta: laborables 10:00–14:00 y 18:00–22:00.
- P2 llano: laborables 08:00–10:00, 14:00–18:00 y 22:00–24:00.
- P3 valle: laborables 00:00–08:00; sábados, domingos y festivos nacionales
  las 24 h.
- El precio de mercado varía por tramo de liquidación (actualmente
  cuartohorario) con independencia de estos periodos; los periodos solo
  determinan peajes y cargos.

## Fuentes de datos
- API de ESIOS (REE), https://api.esios.ree.es, requiere token personal
  (solicitado a consultasios@ree.es; pendiente de recibir).
  - Indicador 600: precio del mercado diario (spot OMIE), €/MWh.
  - Indicador 1001: PVPC 2.0TD (como referencia de comparación).
  - Por confirmar: indicadores de servicios de ajuste, pagos por capacidad y
    coeficientes de pérdidas 2.0TD.
- Alternativas sin token: API REData (apidatos.ree.es) y ficheros diarios de
  OMIE. Verificar formato y disponibilidad antes de usarlas.
- Los precios del día siguiente se publican hacia el mediodía del día anterior.

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
