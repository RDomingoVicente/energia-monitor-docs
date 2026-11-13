# Tarea: fase 2, etapa 1 (medición)

Especificación: docs/fase2.md, apartado "Etapa 1: medición" (requisitos E1.1 a
E1.9 y criterios de aceptación). API del cargador: docs/viaris-api.md.

## Objetivo

Medir la energía del coche y de la casa por cuarto de hora, guardar las
sesiones de carga y mostrar el coste en el dashboard. Solo lecturas: esta
tarea no escribe nada en el cargador.

## Antes de escribir código

Presentar un plan y esperar la aprobación. El plan debe incluir:

1. Tablas y vistas nuevas, con sus claves y la migración en la que va cada una.
2. Cómo se integra el muestreo continuo con el proceso actual del colector y
   con deploy/docker-compose.yml.
3. Dónde se guarda el factor del contador y cómo encaja con el patrón de
   parámetros con vigencia.
4. Cómo se evita que un tramo estimado (E1.7) conviva con uno medido.
5. Lista de tests y fixtures.
6. Consultas de los paneles nuevos.

## Entregables

- Cliente del cargador con lista cerrada de lecturas (E1.1).
- Muestreo, energía por tramo, sesiones y cambios de estado (E1.2 a E1.5).
- Vista de coste del coche y de la casa (E1.6).
- Reparto estimado de las sesiones anteriores al muestreo (E1.7).
- Fila "Coche" en grafana/energia-monitor.json y su descripción en
  grafana/README.md (E1.8).
- Variables nuevas en deploy/.env.example y servicio de muestreo en el compose.
- Comando de diagnóstico: `python -m energia_monitor cargador estado`
  (alcanzable o no, última muestra, sesiones guardadas, huecos del día).
- Tests según los criterios de aceptación.

## Documentación que hay que corregir en esta tarea

En docs/contexto.md:

- El NAS está en la LAN de la vivienda y llega al cargador por HTTP; ya no
  hace falta un nodo intermedio.
- La carga real es de unos 4,0 a 4,5 kW (20 A), no 16 A y 3,7 kW.
- Las franjas del cargador ya no son las de la tabla: remitir a
  docs/viaris-api.md.
- La factura sí desglosa por periodo (mercado, peaje, cargos, PC + OS&OM +
  FNEE y tasa municipal) y trae una tabla de coste diario. Con la factura de
  octubre de 2026: la suma de las cinco columnas (353,85 €) más el RFE
  (1262,447 kWh × 20 €/MWh = 25,25 €) da el total de 379,10 €, y la tasa
  municipal es el 1,5 % de mercado + PC, OS&OM y FNEE + RFE (5,19 €). La
  conciliación deja de ser imposible: queda pendiente de la curva horaria.
- La fase 2 sigue docs/fase2.md; quitar el apartado antiguo de la fase 2 o
  dejarlo como remisión.

## Fuera de esta tarea

- Cualquier escritura en el cargador.
- Registro de batería, formulario y planificador (etapas 2 y 3).
- WebSocket del cargador (solo si se confirma que hace falta para el contador;
  preguntar antes).
- Importación de la curva de i-DE.

## Hecho cuando

- El CI pasa en verde.
- En el NAS, tras `docker compose up -d`, `cargador estado` muestra muestras
  recientes y el dashboard enseña el estado del cargador.
- Tras una carga real, el panel de gasto muestra kWh y euros de esa carga, y
  la suma de energía por tramo difiere menos de un 2 % de la sesión que
  registra el cargador.
- La documentación está actualizada y se resume qué queda pendiente de
  verificar con el coche conectado.
