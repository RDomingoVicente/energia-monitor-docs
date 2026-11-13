---
name: viaris
description: Reglas y procedimiento para cualquier trabajo que toque el cargador Orbis Viaris del proyecto (cliente HTTP del cargador, muestreo, sesiones, franjas del planificador, pruebas contra 192.168.1.34). Usar siempre antes de escribir o modificar código que llame al cargador, antes de proponer un comando curl contra él y al interpretar una respuesta suya.
---

# Cargador Orbis Viaris

Equipo físico en producción, sin autenticación: una petición equivocada puede
dejar el coche sin cargar o el cargador fuera de la red. La referencia de la
API es `docs/viaris-api.md`; leerla antes de actuar y respetar sus marcas de
"verificado" y "sin probar".

## Reglas

1. Solo se usan rutas y campos que figuren en `docs/viaris-api.md`. Si hace
   falta algo que no está, se propone al usuario una lectura (GET) para
   verificarlo y se documenta el resultado antes de usarlo.
2. El código accede al cargador únicamente a través del cliente del proyecto,
   que valida método y ruta contra una lista cerrada. No se amplía esa lista
   sin que lo pida la tarea en curso.
3. Nunca, en ningún contexto: `POST /reset/sys`, escrituras en
   `/modules/network`, ocpp, mqtt_user, solar, spl, modbus, `/elements` o
   `/device`.
4. Las respuestas de `/modules/network` y `/modules/mqtt_user` pueden llevar
   credenciales: no se piden, no se guardan como fixture y no se registran.
5. Las pruebas contra el cargador real las ejecuta el usuario. Se le da el
   comando exacto, se explica qué debe devolver y se espera su salida.
6. Los tests automáticos usan fixtures; nunca la red.

## Lecturas de comprobación

```sh
V=http://192.168.1.34
curl -s "$V/device" | jq '{model, fwv, ampacity, maxAmpacity, limitPower}'
curl -s "$V/modules/evsm/elements" | jq '.[0].stat'
curl -s "$V/modules/modulator?level=stat" | jq .stat
curl -s "$V/modules/scheduler/elements?level=cfg" \
  | jq '.[0].data.cfg.tasks[] | {id, active, type, weekday: .initTime.weekday,
        day: .initTime.day, month: .initTime.month,
        franja: .initTime.timeList[0]}'
curl -s "$V/modules/modulator/historic" | jq -r .body | tail -5
```

## Procedimiento para probar una escritura (etapa 3)

1. Guardar la configuración: lectura del planificador a un fichero.
2. Probar primero con una franja inactiva (`"active":0`) y un `id` libre.
3. Leer y comprobar el resultado, también en la app e-VIARIS.
4. Borrar la franja de prueba y comparar con la copia del paso 1.
5. Anotar en `docs/viaris-api.md` lo que queda verificado, con la fecha.

## Fixtures

Al guardar una respuesta real como fixture, conservarla íntegra salvo
credenciales, e indicar en el nombre o en un comentario la fecha y el firmware
(`fwv` de `/device`).

## Datos que conviene recordar

- Marcas de tiempo en segundos Unix UTC; horas de franja en minutos locales.
- `weekday`: bit 0 = domingo. `day` y `month`: máscaras (bit d−1, bit m−1).
- Estados del conector: 1 y 2 libre, 3 y 4 conectado, 5 y 6 cargando,
  7 pausado, 8 finalizada.
- El histórico guarda solo 100 sesiones.
- Máximo de 10 franjas por conector.
