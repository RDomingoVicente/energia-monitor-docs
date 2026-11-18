# Tarea: fase 2, etapa 3 (planificador de carga)

Especificación: sección "Etapa 3: planificador de carga" de docs/fase2.md
(antes docs/fase2-etapa3.md, incorporada el 18/11/2026). API del cargador y
pruebas del 17 y 18/11/2026: docs/viaris-api.md.

## Objetivo

Programar las franjas de carga del Viaris para que el coche salga con el
objetivo cubierto pagando lo mínimo, y cargar de más cuando la energía está
barata (E3.0). Se entrega en pasos que se revisan uno a uno (E3.11).

## Orden de entrega

1. **3.0, pruebas del cargador**: hechas el 17 y 18/11/2026.
2. **3a, modelo, algoritmo y sombra** (este documento, abajo). Plan aprobado
   el 18/11/2026. Primera entrega (0020, formulario mínimo) revisada el
   18/11/2026. **No escribe en el cargador ni lo lee más allá de los GET
   del muestreo.**
3. **3b, pantalla Plan de carga y panel de Inicio** (solo lectura), cuando las
   maquetas 1a–1d y 5 estén en docs/formulario-maquetas.md.
4. Al menos una semana en sombra (desde el 18/11, v0.6.0), además de la
   simulación en seco de 3c: activación no antes del 25/11/2026 y tras
   revisar `v_plan_ahorro` de esa semana (E3.11).
5. **3c, escritura y reconciliación (v0.7.0)**: plan aceptado el 18/11/2026
   («Plan del paso 3c» en docs/fase2.md). Antes de activar: simulación en
   seco en el NAS (una o dos noches con una franja partida a medianoche, la
   publicación de los precios del día siguiente con su recálculo y una
   desconexión y reconexión del coche), pruebas de escritura a mano
   (incluido un POST con un id ocupado) y tareas 5 y 6 desactivadas en la
   app.
   Implementado (v0.7.0, sin desplegar): ver «Paso 3c» abajo.
6. **3d, avisos**: correo desde el NAS, avisos nuevos y Grafana.

## Paso 3a

### Migración 0020 (`0020_planificador.sql`)

- Parámetros (E3.9): tabla `bateria_parametro_grupo` (7 grupos con su
  pregunta; el 6 es avanzado; abiertos en móvil el 1 y el 4); columnas
  `grupo`, `orden` y `editable` en `bateria_parametro_clave`, para todas las
  claves; las 19 claves numéricas nuevas con nombre, descripción, efecto,
  unidad y rango; el CHECK de valores de 0014 se sustituye (enteros, 0/1 en
  `carga_oportunista`, 0 admitido en los márgenes).
- `planificador_modo` (historial, en 3a fijo en `sombra`) y
  `planificador_config` (una fila: `vivienda`, `omie`).
- Plan guardado: `plan_calculo`, `plan_cuarto`, `plan_franja` (con
  `choca_con`, las tareas ajenas que pisa), `plan_salida`.
- Control: `planificador_solicitud` (con `solicitar_plan(motivo)`) y
  `planificador_ciclo` (último ciclo correcto y último error).
- Entradas en SQL: cuartos observados y enchufados, potencia por hora,
  precios con publicación, conexiones.
- Vistas: `v_plan_vigente`, `v_plan_efectivo`, `v_plan_ahorro` (E3.8).
- Permisos: `grafana_ro` lee todo lo nuevo; `formulario_rw` lee y puede
  pedir un recálculo.

### Código

- Paquete `src/energia_monitor/plan_carga/`: `modelo`, `disponibilidad`,
  `precios`, `algoritmo`, `franjas` (funciones puras con `Decimal`) y
  `servicio` (base de datos).
- Ciclo en el servicio `muestreo` (fase2.md, «Decisiones del paso 3a»).
- CLI: `planificador plan [--instante]` y
  `planificador simular --dia D [--desde HH:MM]`. Ninguno guarda.
- Formulario mínimo: el orden de Parámetros sale de `grupo` y `orden` (sin
  `ORDEN_PARAMETROS`); las claves con `editable` = false no se pueden cambiar
  desde la página; unidad «hora»: minutos desde las 00:00 (810), que la página
  y el CLI muestran y piden como HH:MM (`input type="time"`). La pantalla
  agrupada es 3b.
- Las solicitudes de recálculo tras lectura, excepción, horario, parámetro y
  precios nuevos.

### Tests

Sin base de datos: medianoche partida (y cambios de hora), sin solapes con
tareas ajenas, franja en curso intocable, nivel necesario que se salta el
umbral, margen y oportunista solo por debajo del umbral y con precio
publicado, tope en toda la trayectoria, modo prudente sin historial,
acumulación entre salidas, `margen_salida_min`, empates y determinismo,
`franja_min_min`, `max_tareas_sistema`, precio esperado, umbral, potencia,
disponibilidad con huecos de muestreo, y el nivel 1 frente a la búsqueda
exhaustiva en instancias pequeñas.

Con PostgreSQL: claves con grupo y orden únicos, rangos y CHECK, permisos,
lectura de la entrada y guardado del plan, `v_plan_efectivo` y
`v_plan_ahorro` frente a la referencia en Python, disparadores del ciclo en el
muestreo (y que un fallo no lo detiene), salida del CLI, formulario
(orden y `editable`). El cliente del cargador sigue admitiendo solo sus GET.

## Criterios de aceptación del paso 3a

- El CI pasa en verde (tests unitarios y de integración).
- Ninguna migración publicada cambia; la 0020 se aplica sobre una copia de la
  base de datos del NAS sin errores.
- En el NAS, con el muestreo en marcha, `plan_calculo` recibe un cálculo al
  menos cada 15 minutos y otro en menos de 2 minutos tras enchufar o
  desenchufar el coche y tras guardar una lectura; `planificador_ciclo`
  no registra errores.
- El ciclo del muestreo sigue por debajo de 60 s; el cálculo del plan, por
  debajo de 2 s en el NAS.
- `planificador plan` muestra modo, modo prudente o no, nivel, umbral,
  salidas con su objetivo y si quedan cubiertas, y franjas con motivo, kWh,
  precio medio, coste, provisional y tareas (con la medianoche partida).
- `planificador simular` sobre una noche del 15 al 18/11/2026 da el plan y
  el coste real, el del plan y el de la referencia, coherentes con
  «Energía: coche» de Grafana.
- La pantalla Parámetros muestra las claves nuevas en el orden de E3.9 y no
  deja cambiar `id_tarea_guardiana`.
- En sombra, cada plan registra con qué tareas ajenas del cargador chocaría
  cada franja (`plan_franja.choca_con`). Se rellena cuando 3c lea el
  programador; en 3a el campo existe y los tests lo cubren con tramos
  ocupados de entrada.
- El cliente del cargador no admite nada nuevo; nada se escribe en el
  cargador.
- La 0020 no se aplica en el NAS hasta cerrar 3a (v0.6.0). Hasta entonces se
  puede corregir, actualizando su hash en tests/test_migraciones.py.
- docs/fase2.md, docs/parametros.md y CLAUDE.md actualizados, con lo
  verificado y lo pendiente.

## Fuera del paso 3a

- Cualquier lectura del programador o escritura en el cargador (3c).
- Pantallas Plan de carga, panel de Inicio y Parámetros agrupada (3b).
- Cambio de modo, Cargar ahora, franjas manuales y bloqueos (3c).
- Avisos nuevos, correo y Grafana (3d).

## Comprobación en el NAS (aceptación de 3a)

Desde /volume1/docker/energia-monitor, con `psql` en el contenedor `db`
(`docker compose exec db psql -U energia -d energia`).

Ciclo cada 15 minutos (minutos desde el cálculo anterior; con el muestreo
cada 60 s, el periódico sale entre 15 y 16):

```sql
SELECT ahora AT TIME ZONE 'Europe/Madrid' AS hora, motivo, round(duracion_ms) AS ms,
       round(extract(epoch FROM ahora - lag(ahora) OVER (ORDER BY ahora)) / 60, 1) AS min_desde_anterior
FROM plan_calculo WHERE ahora > now() - interval '3 hours' ORDER BY ahora;
```

Recálculo tras enchufar o desenchufar (cada cambio de estado) y tras una
lectura (desde su alta): retraso hasta el primer cálculo posterior, menos de
2 minutos:

```sql
SELECT 'estado ' || e.estado AS evento, e.detectado_en AT TIME ZONE 'Europe/Madrid' AS cuando,
       (SELECT min(c.calculado_en) FROM plan_calculo c WHERE c.calculado_en >= e.detectado_en) - e.detectado_en
           AS retraso
FROM cargador_estado e WHERE e.detectado_en > now() - interval '1 day'
UNION ALL
SELECT 'lectura ' || l.pct, l.alta_en AT TIME ZONE 'Europe/Madrid',
       (SELECT min(c.calculado_en) FROM plan_calculo c WHERE c.calculado_en >= l.alta_en) - l.alta_en
FROM bateria_lectura l WHERE l.alta_en > now() - interval '1 day'
ORDER BY 2;
```

Sin errores del ciclo, duración y último cálculo:

```sql
SELECT ultimo_ok_en AT TIME ZONE 'Europe/Madrid', ultimo_error_en, ultimo_error FROM planificador_ciclo;
SELECT max(duracion_ms), avg(duracion_ms) FROM plan_calculo WHERE ahora > now() - interval '1 day';
```

Muestreo igual que antes: mayor hueco entre muestras en las últimas 24 h
(como antes: 60 s, con algún hueco de unos 70 s cada 2 h) y
`cargador estado`:

```sql
SELECT max(d) AS mayor_hueco, count(*) FILTER (WHERE d > interval '180 seconds') AS huecos_de_mas_de_180_s
FROM (SELECT instante - lag(instante) OVER (ORDER BY instante) AS d
      FROM cargador_muestra WHERE instante > now() - interval '24 hours') x;
```

## Paso 3c

Plan y decisiones: «Plan del paso 3c» e «Implementación del paso 3c» en
docs/fase2.md.

### Antes de activar (en este orden)

1. Desplegar v0.7.0 en sombra (la 0022 la aplica el arranque). Comprobar:

   ```sh
   docker compose exec muestreo python -m energia_monitor planificador tareas
   docker compose exec muestreo python -m energia_monitor planificador reconciliar --en-seco
   ```

   `tareas` debe mostrar la 9 como guardiana y la 5 y la 6 como ajenas.
2. Simulación en seco, una o dos noches: con el coche conectado, una franja
   partida a medianoche, la publicación de los precios del día siguiente
   con su recálculo y una desconexión y reconexión. Lo que habría escrito:

   ```sql
   SELECT instante AT TIME ZONE 'Europe/Madrid' AS hora, metodo, id_tarea, motivo,
          cuerpo->'tasks'->0->'initTime'->'timeList'->0 AS franja
   FROM plan_auditoria WHERE en_seco ORDER BY id DESC LIMIT 40;
   ```

   Ninguna operación sobre 5, 6 ni 9; cada franja del plan, una tarea (dos si
   cruza la medianoche) en ids libres. Mientras tengas tareas de la app
   activas, las franjas que caigan encima llevan en el motivo «choca con la
   tarea N de la app» (v0.7.1): es lo esperado en sombra. En sombra nada queda escrito, así que
   cada plan nuevo repite los POST de sus franjas: es lo esperado.
3. Pruebas de escritura a mano (skill `viaris`), una a una y en horario
   barato, incluido un POST sobre un id ocupado y un PUT con la tarea
   completa. **Hechas el 18/11/2026** (pruebas 1 a 4 en
   docs/viaris-api.md): POST con fecha, POST sobre un id ocupado (sobrescribe
   sin avisar), PUT del código y DELETE; el programador quedó como al
   empezar.
4. No antes del 25/11/2026: revisar `v_plan_ahorro` de la semana en sombra.
   Después, desactivar en la app las tareas 5 y 6 (sin borrarlas).
5. Activar de día (Plan de carga o `planificador modo activo`) y vigilar la
   primera noche: `planificador tareas`, la app y `plan_auditoria`.

Para volver atrás: `planificador modo sombra` (no borra) o
`planificador limpiar --confirmar` (borra las propias; el coche no cargará
hasta que actives tus tareas en la app).
