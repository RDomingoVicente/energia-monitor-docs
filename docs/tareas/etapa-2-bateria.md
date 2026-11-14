# Tarea: fase 2, etapa 2 (batería y previsión)

Especificación: sección "Etapa 2: batería y previsión" de docs/fase2.md
(antes docs/fase2-etapa2.md, incorporada el 14/11/2026 como primer cambio de
la tarea). API del cargador: docs/viaris-api.md.

## Objetivo

Estimar el nivel de batería, prever la energía necesaria para la próxima
salida y ofrecer una página para el móvil donde anotar lecturas, excepciones
y horarios. Esta tarea no escribe nada en el cargador.

## Antes de escribir código

Presentar un plan y esperar la aprobación. El plan debe incluir:

1. Tablas, vistas y migraciones, con sus claves.
2. El cálculo del nivel y de la reconciliación en pseudocódigo, y dónde vive
   (SQL, Python o ambos), con la referencia en Python para los tests.
3. Qué dispara el recálculo: cierre de sesión, cambio de estado del conector,
   alta o corrección de una lectura, nuevo valor de un parámetro.
4. El servicio `formulario`: cómo se sirve, el TLS con el mecanismo de
   Miniflux (pregúntame lo que necesites del NAS) y el rol de PostgreSQL.
5. La separación de los dashboards y las consultas de los paneles nuevos.
6. Lista de tests y fixtures.

## Orden de entrega

Cada paso se revisa antes del siguiente.

1. **Separación de dashboards** (E2.10, primera parte). Solo JSON y README;
   sin migraciones ni imagen.
2. **Modelo y cálculo** (E2.1 a E2.8 y E2.11): migraciones, recálculo, CLI y
   tests.
3. **Formulario** (E2.9), cuando las maquetas estén aprobadas. Las pasaré en
   docs/formulario-maquetas.md; hasta entonces, planifica el servidor y las
   rutas, no el aspecto.
4. **Dashboard de batería** (E2.10, segunda parte).

## Fixture que aporta el usuario

`muestras-2026-11-13.log`: una muestra por minuto (modulador y evsm) del
13/11 a las 12:45 al 14/11 a las 12:46, hora de Madrid, con enchufe, carga de
03:00 a 06:00, fin de franja y desconexión. Incluye líneas vacías por fallos
de lectura. Va en tests/fixtures/viaris/.

## Fuera de esta tarea

- Cualquier escritura en el cargador.
- Modos "cargar ahora" y "automatización en pausa", y el planificador
  (etapa 3).
- Importación de la curva de i-DE.

## Hecho cuando

- El CI pasa en verde.
- En el NAS, tras una lectura manual, la página y `bateria estado` muestran el
  nivel, su antigüedad, la próxima salida y la energía necesaria.
- Tras una desconexión y una reconexión reales, el trayecto aparece con su
  consumo estimado y, tras la siguiente lectura, con su consumo reconciliado.
- Los tres dashboards se navegan entre sí manteniendo rango y variables.
- docs/fase2.md, CLAUDE.md (etapa actual: 2), grafana/README.md y
  docs/parametros.md están actualizados.
