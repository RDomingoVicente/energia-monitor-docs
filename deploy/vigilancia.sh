#!/bin/sh
# Vigilancia de avisos por correo (paso 3d). Se ejecuta en el NAS, fuera de los
# contenedores del servicio, desde el Programador de tareas de DSM cada 15 minutos
# (usuario root; «Enviar detalles de ejecución por correo electrónico → solo si el
# script termina de forma anómala»). Ver deploy/README.md.
#
# La lógica va en la imagen (python -m energia_monitor vigilancia, servicio
# `vigilancia` del compose, con el rol de solo lectura grafana_ro): lee los avisos
# con correo de v_aviso y solo imprime algo, y termina con 1, cuando aparece o se
# resuelve uno, o para recordar un ERROR que sigue activo tras 24 h. Sin cambios,
# termina con 0 sin salida y DSM no manda nada.
#
# Este envoltorio solo añade el caso en que docker no puede ejecutar la vigilancia:
# lo avisa una vez (fichero vigilancia/docker-fallo) y otra al recuperarse.
#
# Uso: vigilancia.sh [--prueba]   (--prueba: correo de prueba, no toca el estado)

set -u

DIR=${VIGILANCIA_DIR:-$(cd "$(dirname "$0")" && pwd)}
DOCKER=${DOCKER:-docker}
MARCA="$DIR/vigilancia/docker-fallo"
CABECERA="energia-monitor · vigilancia"

cd "$DIR" || { echo "$CABECERA: no existe el directorio $DIR"; exit 1; }

ERRORES=$(mktemp) || exit 1
trap 'rm -f "$ERRORES"' EXIT

SALIDA=$("$DOCKER" compose run --rm --no-deps -T vigilancia python -m energia_monitor vigilancia "$@" 2>"$ERRORES")
CODIGO=$?

# La vigilancia ha corrido si termina con 0, o con 1 y su cabecera (un 1 sin ella
# es un fallo de docker compose o de Python).
case "$CODIGO:$SALIDA" in
    "0:"*|"1:$CABECERA"*)
        if [ -f "$MARCA" ]; then
            rm -f "$MARCA"
            echo "$CABECERA: vuelve a ejecutarse con normalidad."
            if [ -n "$SALIDA" ]; then
                printf '\n%s\n' "$SALIDA"
            fi
            exit 1
        fi
        if [ -n "$SALIDA" ]; then
            printf '%s\n' "$SALIDA"
        fi
        exit "$CODIGO"
        ;;
esac

if [ -f "$MARCA" ]; then
    exit 0  # ya avisado: no se repite cada 15 minutos
fi
mkdir -p "$DIR/vigilancia" && date '+%Y-%m-%d %H:%M' >"$MARCA"
echo "$CABECERA: no se pudo ejecutar (docker compose run, código $CODIGO)."
echo "Comprueba en el NAS, en $DIR: docker compose ps y docker compose run --rm vigilancia."
if [ -s "$ERRORES" ]; then
    echo ""
    echo "Últimas líneas de error:"
    tail -n 5 "$ERRORES"
fi
exit 1
