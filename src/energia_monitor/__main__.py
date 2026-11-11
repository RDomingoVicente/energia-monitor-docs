"""Punto de entrada: python -m energia_monitor [ejecutar | migrar | recargar].

- ejecutar (por defecto): migraciones, descarga de los días que falten desde
  CARGA_INICIAL_DESDE hasta mañana y actualización diaria planificada.
- migrar: solo aplica las migraciones.
- recargar --desde AAAA-MM-DD [--hasta AAAA-MM-DD] [--forzar]: recarga de
  históricos bajo demanda; sin --forzar solo descarga los días incompletos.
- parametro registrar | listar: alta de parámetros de contrato con vigencia
  (ver docs/parametros.md).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import date, timedelta
from decimal import Decimal

from energia_monitor import __version__, colector, db, parametros, planificador
from energia_monitor.config import Config
from energia_monitor.sources import crear_fuente
from energia_monitor.tiempo import dias, hoy_madrid

log = logging.getLogger("energia_monitor")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="energia_monitor")
    sub = parser.add_subparsers(dest="orden")
    sub.add_parser("ejecutar", help="servicio: arranque y actualización diaria (por defecto)")
    sub.add_parser("migrar", help="aplica las migraciones pendientes")
    recargar = sub.add_parser("recargar", help="recarga precios de un rango de días")
    recargar.add_argument("--desde", type=date.fromisoformat, required=True)
    recargar.add_argument("--hasta", type=date.fromisoformat, help="por defecto, mañana")
    recargar.add_argument("--forzar", action="store_true", help="descarga también los días completos")

    parametro = sub.add_parser("parametro", help="parámetros de contrato con vigencia")
    sub_param = parametro.add_subparsers(dest="accion", required=True)
    registrar = sub_param.add_parser("registrar", help="nuevo valor desde una fecha (cierra el anterior)")
    registrar.add_argument("--contrato", default="vivienda")
    registrar.add_argument("--clave", required=True)
    registrar.add_argument("--periodo", default="*", help="'*' o P1..P6 según la clave")
    registrar.add_argument("--desde", type=date.fromisoformat, required=True)
    registrar.add_argument("--valor", type=Decimal, required=True, help="en la unidad de la clave")
    registrar.add_argument("--estimado", action="store_true")
    registrar.add_argument("--fuente", required=True)
    registrar.add_argument("--nota")
    listar = sub_param.add_parser("listar", help="parámetros vigentes en una fecha")
    listar.add_argument("--contrato", default="vivienda")
    listar.add_argument("--fecha", type=date.fromisoformat, help="por defecto, hoy")
    return parser


def _parametro(args: argparse.Namespace) -> int:
    with db.conectar() as conn:
        if args.accion == "listar":
            fecha = args.fecha or hoy_madrid()
            print(f"Parámetros de {args.contrato} vigentes el {fecha}:")
            for clave, periodo, valor, unidad, estimado, vigencia, fuente in parametros.listar_parametros(
                conn, args.contrato, fecha
            ):
                marca = " (estimado)" if estimado else ""
                texto = "pendiente" if valor is None else f"{valor} {unidad}"
                print(f"  {clave:<19} {periodo:<3} {texto}{marca}  {vigencia}  [{fuente}]")
            return 0
        try:
            registro = parametros.registrar_parametro(
                conn, contrato=args.contrato, clave=args.clave, periodo=args.periodo, desde=args.desde,
                valor=args.valor, estimado=args.estimado, fuente=args.fuente, nota=args.nota,
            )
        except parametros.ErrorParametro as e:
            log.error("%s", e)
            return 1
        if registro.cerrada:
            log.info("vigencia anterior %s cerrada el %s", registro.cerrada, args.desde)
        log.info("registrado %s/%s = %s con vigencia %s", args.clave, args.periodo, args.valor, registro.nueva)
        return 0


def _stop_por_senales() -> threading.Event:
    stop = threading.Event()

    def parar(signum: int, _frame: object) -> None:
        log.info("señal %s recibida: parando", signal.Signals(signum).name)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, parar)
    return stop


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args(argv)
    config = Config.desde_entorno()
    orden = args.orden or "ejecutar"
    log.info("energia-monitor %s: %s (fuente %s)", __version__, orden, config.price_source)

    if orden == "parametro":
        return _parametro(args)

    if orden == "migrar":
        with db.conectar() as conn:
            planificador.preparar_bd(conn, config)
        return 0

    stop = _stop_por_senales()
    fuente = crear_fuente(config.price_source, config.intervalo_descargas_s)

    if orden == "recargar":
        hasta = args.hasta or hoy_madrid() + timedelta(days=1)
        with db.conectar() as conn:
            if args.forzar:
                pendientes = list(dias(args.desde, hasta))
            else:
                pendientes = colector.dias_incompletos(conn, fuente.nombre, args.desde, hasta)
            resumen = colector.descargar_dias(conn, fuente, pendientes, "recarga", stop)
        return 1 if resumen.fallidos else 0

    planificador.ejecutar(config, fuente, stop)
    log.info("parada completada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
