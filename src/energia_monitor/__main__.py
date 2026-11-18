"""Punto de entrada: python -m energia_monitor [ejecutar | migrar | recargar | refrescar | parametro | esios | cargador | bateria
| planificador].

- ejecutar (por defecto): migraciones, descarga de los días que falten desde
  CARGA_INICIAL_DESDE hasta mañana y actualización diaria planificada.
- migrar: solo aplica las migraciones.
- recargar --desde AAAA-MM-DD [--hasta AAAA-MM-DD] [--forzar]: recarga de
  históricos bajo demanda; sin --forzar solo descarga los días incompletos.
- refrescar: recalcula mv_precio_final (necesario tras cambiar por SQL sus
  datos de origen, ver docs/parametros.md); devuelve 1 si sus filas no son
  las esperadas.
- parametro registrar | listar: alta de parámetros de contrato con vigencia
  (ver docs/parametros.md).
- esios recargar --desde AAAA-MM-DD [--hasta AAAA-MM-DD]: vuelve a descargar
  las series de ESIOS (requiere ESIOS_TOKEN).
- esios estimar: recalcula la estimación de SSAA de los días recientes.
- esios estado: previsión 1871 (pendiente, verificada o corregida),
  revisiones de REE y definición de SSAA de cada contrato.
- cargador muestrear: servicio de muestreo del cargador (requiere VIARIS_URL;
  sin ella queda en espera).
- cargador estado: cargador alcanzable o no, última muestra, sesiones
  guardadas, huecos de hoy y coherencia de energia_tramo con las muestras
  de los últimos 2 días; devuelve 1 si no coinciden.
- cargador recalcular --desde AAAA-MM-DD [--hasta AAAA-MM-DD]: recalcula
  energia_tramo desde las muestras en esos días locales (tras insertar o
  borrar muestras por SQL).
- parametro ... --ambito cargador: parámetros del cargador (factor del
  contador, potencia de la estimación).
- parametro ... --ambito bateria: parámetros de la batería (capacidad,
  reserva, factor...); registrar recalcula el nivel.
- bateria estado: nivel estimado y su antigüedad, coche, próxima salida,
  energía necesaria y avisos; devuelve 1 si hay avisos.
- bateria lectura --pct N [--instante "AAAA-MM-DD HH:MM"] [--nota TEXTO]:
  lectura del nivel (hora de Madrid; por defecto, ahora) y recálculo.
- bateria recalcular: rehace el nivel, los trayectos y la reconciliación
  (tras cambiar por SQL lecturas, excepciones o datos del cargador) y
  muestra cuánto ha tardado.
- formulario servir | salud: página de batería por HTTPS (FORMULARIO_HOST,
  certificados en FORMULARIO_CERTIFICADOS) y healthcheck del contenedor.
- bateria carga-externa [--kwh N] [--coste EUR] [--instante "AAAA-MM-DD HH:MM"]
  [--nota TEXTO]: carga fuera de casa (con el coche desconectado) y recálculo.
- planificador plan [--instante "AAAA-MM-DD HH:MM"]: calcula el plan de carga
  (hora de Madrid; por defecto, ahora) y lo muestra, sin guardarlo.
- planificador simular --dia AAAA-MM-DD [--desde HH:MM]: plan calculado al
  enchufar ese día (o a esa hora) y su coste frente al real y a cargar al
  enchufar en esa conexión. No guarda nada ni toca el cargador.
- planificador modo activo|sombra|pausa | tareas | reconciliar --en-seco |
  limpiar --confirmar | guardiana --id N --confirmar: paso 3c, hablan con el
  cargador (contenedor muestreo, con VIARIS_URL).

recargar, esios recargar, esios estimar y parametro registrar (de contrato)
refrescan mv_precio_final al terminar.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor import (
    __version__,
    bateria_servicio,
    colector,
    db,
    esios,
    estimacion,
    muestreo,
    parametros,
    planificador,
)
from energia_monitor.config import Config
from energia_monitor.errores import ErrorAutenticacion
from energia_monitor.sources import crear_cliente_esios, crear_fuente
from energia_monitor.tiempo import dias, hoy_madrid, limites_dia_utc
from energia_monitor.viaris import ClienteViaris

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
    sub.add_parser("refrescar", help="recalcula mv_precio_final (tras cambios por SQL)")

    parametro = sub.add_parser("parametro", help="parámetros de contrato con vigencia")
    sub_param = parametro.add_subparsers(dest="accion", required=True)
    registrar = sub_param.add_parser("registrar", help="nuevo valor desde una fecha (cierra el anterior)")
    registrar.add_argument("--ambito", choices=("contrato", "cargador", "bateria"), default="contrato")
    registrar.add_argument("--contrato", default="vivienda")
    registrar.add_argument("--clave", required=True)
    registrar.add_argument("--periodo", default="*", help="'*' o P1..P6 según la clave")
    registrar.add_argument("--desde", type=date.fromisoformat, required=True)
    registrar.add_argument("--valor", required=True,
                           help="en la unidad de la clave; las horas (hora_publicacion_precios), HH:MM")
    registrar.add_argument("--origen", required=True, choices=parametros.ORIGENES_BATERIA,
                           help="contrato (pactado), regulado (norma publicada) o estimado; en la batería,"
                                " también usuario y ficha_tecnica")
    registrar.add_argument("--fuente", required=True)
    registrar.add_argument("--nota")
    listar = sub_param.add_parser("listar", help="parámetros vigentes en una fecha")
    listar.add_argument("--ambito", choices=("contrato", "cargador", "bateria"), default="contrato")
    listar.add_argument("--contrato", default="vivienda")
    listar.add_argument("--fecha", type=date.fromisoformat, help="por defecto, hoy")

    esios_p = sub.add_parser("esios", help="series de ESIOS (SSAA y PVPC)")
    sub_esios = esios_p.add_subparsers(dest="accion", required=True)
    recargar_e = sub_esios.add_parser("recargar", help="vuelve a descargar un rango de días")
    recargar_e.add_argument("--desde", type=date.fromisoformat, required=True)
    recargar_e.add_argument("--hasta", type=date.fromisoformat, help="por defecto, mañana")
    sub_esios.add_parser("estimar", help="recalcula la estimación de SSAA")
    sub_esios.add_parser("estado", help="previsión 1871, revisiones y definición de SSAA")

    cargador = sub.add_parser("cargador", help="cargador Orbis Viaris (solo lecturas)")
    sub_cargador = cargador.add_subparsers(dest="accion", required=True)
    sub_cargador.add_parser("muestrear", help="servicio de muestreo continuo")
    sub_cargador.add_parser("estado", help="alcanzable, última muestra, sesiones, huecos y coherencia")
    recalcular = sub_cargador.add_parser("recalcular", help="recalcula energia_tramo desde las muestras")
    recalcular.add_argument("--desde", type=date.fromisoformat, required=True)
    recalcular.add_argument("--hasta", type=date.fromisoformat, help="por defecto, hoy")

    formulario = sub.add_parser("formulario", help="página de batería por HTTPS (servicio formulario)")
    sub_formulario = formulario.add_subparsers(dest="accion", required=True)
    sub_formulario.add_parser("servir", help="atiende la página en FORMULARIO_PUERTO (8443) con TLS")
    sub_formulario.add_parser("salud", help="healthcheck: GET /salud por HTTPS verificando el certificado")

    bateria = sub.add_parser("bateria", help="nivel de batería estimado y previsión")
    sub_bateria = bateria.add_subparsers(dest="accion", required=True)
    sub_bateria.add_parser("estado", help="nivel, coche, próxima salida, energía necesaria y avisos")
    lectura = sub_bateria.add_parser("lectura", help="registra una lectura del nivel y recalcula")
    lectura.add_argument("--pct", type=Decimal, required=True, help="porcentaje de 0 a 100")
    lectura.add_argument("--instante", help="AAAA-MM-DD HH:MM, hora de Madrid (por defecto, ahora)")
    lectura.add_argument("--nota")
    sub_bateria.add_parser("recalcular", help="rehace nivel, trayectos y reconciliación")
    externa = sub_bateria.add_parser("carga-externa", help="registra una carga fuera de casa y recalcula")
    externa.add_argument("--kwh", type=Decimal, help="energía cargada (opcional, 0 a 150)")
    externa.add_argument("--coste", type=Decimal, help="coste en € (opcional)")
    externa.add_argument("--instante", help="AAAA-MM-DD HH:MM, hora de Madrid (por defecto, ahora)")
    externa.add_argument("--nota")

    plan = sub.add_parser("planificador", help="planificador de carga (paso 3a: sombra, sin escribir en el cargador)")
    sub_plan = plan.add_subparsers(dest="accion", required=True)
    calcular = sub_plan.add_parser("plan", help="calcula el plan y lo muestra (no lo guarda)")
    calcular.add_argument("--instante", help="AAAA-MM-DD HH:MM, hora de Madrid (por defecto, ahora)")
    simular = sub_plan.add_parser("simular", help="plan al enchufar un día pasado y su coste frente al real")
    simular.add_argument("--dia", type=date.fromisoformat, required=True)
    simular.add_argument("--desde", help="HH:MM local; por defecto, el primer enchufe del día desde las 12:00")
    # Paso 3c: hablan con el cargador; en el contenedor muestreo (VIARIS_URL).
    cambiar = sub_plan.add_parser("modo", help="cambia el modo (lo aplica el muestreo al momento)")
    cambiar.add_argument("modo", choices=("sombra", "activo", "pausa"))
    cambiar.add_argument("--nota")
    sub_plan.add_parser("tareas", help="lee el programador: guardiana, tareas propias y ajenas")
    reconciliar = sub_plan.add_parser("reconciliar", help="lo que escribiría ahora en el cargador, sin escribir")
    reconciliar.add_argument("--en-seco", action="store_true", required=True)
    limpiar = sub_plan.add_parser("limpiar", help="pasa a sombra y borra las tareas propias salvo la guardiana")
    limpiar.add_argument("--confirmar", action="store_true", required=True)
    guardiana = sub_plan.add_parser("guardiana", help="traslada la tarea guardiana a otro id libre")
    guardiana.add_argument("--id", type=int, required=True)
    guardiana.add_argument("--confirmar", action="store_true", required=True)
    return parser


def _cliente_viaris(config: Config) -> ClienteViaris | None:
    return ClienteViaris(config.viaris_url) if config.viaris_url else None


def _cargador(args: argparse.Namespace, config: Config) -> int:
    cliente = _cliente_viaris(config)
    if args.accion == "muestrear":
        muestreo.ejecutar(cliente, _stop_por_senales())
        log.info("parada completada")
        return 0
    if args.accion == "recalcular":
        hasta = args.hasta or hoy_madrid()
        if hasta < args.desde:
            print("--hasta es anterior a --desde", file=sys.stderr)
            return 2
        desde_utc, _ = limites_dia_utc(args.desde)
        _, hasta_utc = limites_dia_utc(hasta)
        with db.conectar() as conn, conn.transaction():
            n = muestreo.recalcular_tramos(conn, desde_utc, hasta_utc)
        print(f"energia_tramo recalculada del {args.desde} al {hasta}: {n} tramo(s) con energía")
        return 0
    with db.conectar() as conn:
        lineas = muestreo.estado(conn, cliente, datetime.now(UTC))
    for linea in lineas:
        print(linea)
    return 1 if any(linea.startswith(muestreo.AVISO) for linea in lineas) else 0


def _bateria(args: argparse.Namespace) -> int:
    ahora = datetime.now(UTC)
    with db.conectar() as conn:
        if args.accion == "recalcular":
            n = bateria_servicio.recalcular(conn)
            fila = conn.execute("SELECT duracion_ms FROM bateria_recalculo").fetchone()
            duracion = f" en {fila[0]:.0f} ms" if fila else ""
            print(f"batería recalculada: {n} evento(s){duracion}")
            return 0
        if args.accion == "carga-externa":
            try:
                if args.instante:
                    instante = bateria_servicio.instante_local(args.instante).instante
                else:
                    instante = ahora.replace(second=0, microsecond=0)
                ident = bateria_servicio.registrar_carga_externa(
                    conn, bateria_servicio.NuevaCargaExterna(instante, args.kwh, args.coste, args.nota), "cli", ahora)
            except (bateria_servicio.ErrorLectura, bateria_servicio.ErrorCargaExterna) as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            kwh = "sin kWh" if args.kwh is None else f"{args.kwh} kWh"
            print(f"carga externa {ident} registrada: {kwh}")
        if args.accion == "lectura":
            try:
                if args.instante:
                    local = bateria_servicio.instante_local(args.instante)
                    if local.ambigua:
                        print(f"{args.instante} es ambigua (cambio de hora): se toma la primera, en horario de verano")
                    instante = local.instante
                else:
                    instante = ahora.replace(second=0, microsecond=0)
                ident = bateria_servicio.registrar_lectura(conn, instante=instante, pct=args.pct, origen="cli",
                                                           nota=args.nota, ahora=ahora)
            except bateria_servicio.ErrorLectura as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            print(f"lectura {ident} registrada: {args.pct} %")
        lineas = bateria_servicio.estado(conn, ahora)
    for linea in lineas:
        print(linea)
    if args.accion in ("lectura", "carga-externa"):
        return 0
    return 1 if any(linea.startswith(bateria_servicio.AVISO) for linea in lineas) else 0


SQL_DEFINICION_SSAA = """
SELECT d.contrato, d.indicador, d.incluido, d.vigencia::text, i.nombre, coalesce(d.nota, '')
FROM contrato_ssaa_componente d JOIN esios_indicador i ON i.id = d.indicador
WHERE d.vigencia @> %s
ORDER BY d.contrato, i.control NULLS LAST, d.indicador
"""


def _refrescar(conn: psycopg.Connection) -> bool:
    """Refresca mv_precio_final tras un cambio en sus datos; False si falla."""
    try:
        db.refrescar_precio_final(conn)
    except Exception:
        log.exception("fallo al refrescar mv_precio_final: los cambios están guardados, pero la fila"
                      " \"Coche\" y la comparación con el PVPC no los verán hasta ejecutar"
                      " `python -m energia_monitor refrescar`")
        return False
    return True


def _esios(args: argparse.Namespace, config: Config) -> int:
    with db.conectar() as conn:
        hoy = hoy_madrid()
        if args.accion == "estimar":
            estimacion.actualizar(conn, hoy)
            return 0 if _refrescar(conn) else 1
        if args.accion == "estado":
            estado = esios.estado_prevision(conn)
            print(f"Previsión 1871: {estado.estado} ({estado.dias_verificados} día(s) comprobados,"
                  f" {estado.corregidos} valor(es) corregidos por REE)")
            filas = esios.revisiones(conn)
            print("Revisiones de REE:" if filas else "Revisiones de REE: ninguna")
            for indicador, dia, tramos, max_cambio, _primera, modificado in filas:
                print(f"  {indicador:>6} {dia}  {tramos:>3} tramo(s), cambio máx. {max_cambio} €/MWh,"
                      f" último cambio {modificado:%Y-%m-%d %H:%M}")
            print(f"Definición de SSAA vigente el {hoy}:")
            for contrato, indicador, incluido, vigencia, nombre, nota in conn.execute(SQL_DEFINICION_SSAA, (hoy,)):
                marca = "sí" if incluido else "no"
                print(f"  {contrato:<9} {indicador:>6} {marca:<2} {vigencia}  {nombre}{'  [' + nota + ']' if nota else ''}")
            return 0
        cliente = crear_cliente_esios(config.esios_token, config.intervalo_descargas_s)
        if cliente is None:
            log.error("ESIOS_TOKEN no definido")
            return 1
        hasta = args.hasta or hoy + timedelta(days=1)
        rango = esios.rangos_contiguos(dias(args.desde, hasta))
        try:
            resumen = esios.descargar(conn, cliente, {ind: rango for ind in esios.indicadores(conn)},
                                      "recarga", _stop_por_senales())
        except ErrorAutenticacion as e:
            log.error("%s", e)
            return 1
        esios.comprobar_controles(conn, args.desde, hasta)
        refrescada = _refrescar(conn)
        return 1 if resumen.fallidos or not refrescada else 0


def _parametro(args: argparse.Namespace) -> int:
    with db.conectar() as conn:
        cargador = args.ambito == "cargador"
        bateria = args.ambito == "bateria"
        if args.accion == "listar":
            fecha = args.fecha or hoy_madrid()
            if cargador:
                print(f"Parámetros del cargador vigentes el {fecha}:")
                filas = parametros.listar_parametros_cargador(conn, fecha)
            elif bateria:
                print(f"Parámetros de la batería vigentes el {fecha}:")
                filas = parametros.listar_parametros_bateria(conn, fecha)
            else:
                print(f"Parámetros de {args.contrato} vigentes el {fecha}:")
                filas = parametros.listar_parametros(conn, args.contrato, fecha)
            for clave, periodo, valor, unidad, origen, vigencia, fuente in filas:
                texto = "sin valor" if valor is None else parametros.texto_de_valor(valor, unidad)
                print(f"  {clave:<28} {periodo:<3} {origen:<13} {texto}  {vigencia}  [{fuente}]")
            return 0
        try:
            valor = parametros.valor_de_texto(
                args.valor, parametros.unidad_bateria(conn, args.clave) if bateria else None)
            if cargador:
                registro = parametros.registrar_parametro_cargador(
                    conn, clave=args.clave, desde=args.desde, valor=valor,
                    origen=args.origen, fuente=args.fuente, nota=args.nota,
                )
            elif bateria:
                registro = parametros.registrar_parametro_bateria(
                    conn, clave=args.clave, desde=args.desde, valor=valor,
                    origen=args.origen, fuente=args.fuente, nota=args.nota,
                )
            else:
                registro = parametros.registrar_parametro(
                    conn, contrato=args.contrato, clave=args.clave, periodo=args.periodo, desde=args.desde,
                    valor=valor, origen=args.origen, fuente=args.fuente, nota=args.nota,
                )
        except parametros.ErrorParametro as e:
            log.error("%s", e)
            return 1
        if registro.cerrada:
            log.info("vigencia anterior %s cerrada el %s", registro.cerrada, args.desde)
        log.info("registrado %s/%s = %s con vigencia %s", args.clave, args.periodo, valor, registro.nueva)
        # Los parámetros del cargador se leen en directo; los de contrato entran en el precio; los
        # de la batería, en el nivel y los trayectos guardados.
        if cargador:
            return 0
        if bateria:
            n = bateria_servicio.recalcular(conn)
            log.info("batería recalculada: %d evento(s)", n)
            return 0
        return 0 if _refrescar(conn) else 1


def _planificador(args: argparse.Namespace) -> int:
    from energia_monitor.plan_carga import cli as plan_cli
    from energia_monitor.plan_carga import informe
    from energia_monitor.plan_carga import servicio as plan_servicio

    if args.accion in ("modo", "tareas", "reconciliar", "limpiar", "guardiana"):
        config = Config.desde_entorno()
        cliente = _cliente_viaris(config)
        with db.conectar() as conn:
            ahora = datetime.now(UTC)
            if args.accion == "modo":
                lineas = plan_cli.modo(conn, args.modo, args.nota)
            elif cliente is None:
                log.error("sin VIARIS_URL: ejecútalo en el contenedor muestreo")
                return 1
            elif args.accion == "tareas":
                lineas = plan_cli.tareas(conn, cliente, ahora)
            elif args.accion == "reconciliar":
                lineas = plan_cli.en_seco(conn, cliente, ahora)
            elif args.accion == "limpiar":
                lineas = plan_cli.limpiar(conn, cliente, ahora)
            else:
                lineas = plan_cli.guardiana(conn, cliente, ahora, args.id)
        print("\n".join(lineas))
        return 1 if any(x.startswith("ERROR") or x.startswith("No se pudo") for x in lineas) else 0
    with db.conectar() as conn:
        if args.accion == "plan":
            try:
                ahora = (bateria_servicio.instante_local(args.instante).instante if args.instante
                         else datetime.now(UTC))
            except bateria_servicio.ErrorLectura as e:
                log.error("%s", e)
                return 1
            entrada, ctx, plan, ms = plan_servicio.calcular(conn, ahora)
            print("\n".join(informe.lineas_plan(entrada, ctx, plan, ms)))
            return 0
        instante = plan_servicio.instante_de_simulacion(conn, args.dia, args.desde)
        if instante is None:
            log.error("no hay ningún enchufe el %s desde las 12:00; indica la hora con --desde", args.dia)
            return 1
        print("\n".join(informe.lineas_simulacion(plan_servicio.simular(conn, instante))))
        return 0


def _formulario(args: argparse.Namespace) -> int:
    from energia_monitor.formulario import config as config_formulario

    try:
        cfg = config_formulario.desde_entorno()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.accion == "salud":
        from energia_monitor.formulario import salud

        resultado = salud.comprobar(cfg.nombre, cfg.puerto)
        print(resultado.detalle)
        return 0 if resultado.ok else 1
    from energia_monitor.formulario.app import App, ConfigApp
    from energia_monitor.formulario.servidor import ErrorCertificado, servir

    app = App(ConfigApp(cfg.host, cfg.grafana_url, cfg.cargador))
    try:
        servir(app, cfg.certificados, cfg.puerto, _stop_por_senales())
    except ErrorCertificado as e:
        log.error("formulario sin arrancar: %s", e)
        return 1
    log.info("parada completada")
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

    if orden == "esios":
        return _esios(args, config)

    if orden == "cargador":
        return _cargador(args, config)

    if orden == "bateria":
        return _bateria(args)

    if orden == "formulario":
        return _formulario(args)
    if orden == "planificador":
        return _planificador(args)

    if orden == "migrar":
        with db.conectar() as conn:
            planificador.preparar_bd(conn, config)
        return 0

    if orden == "refrescar":
        try:
            with db.conectar() as conn:
                refresco = db.refrescar_precio_final(conn)
        except Exception:
            log.exception("fallo al refrescar mv_precio_final")
            return 1
        return 0 if refresco.completo else 1

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
            refrescada = _refrescar(conn)
        return 1 if resumen.fallidos or not refrescada else 0

    cliente_esios = crear_cliente_esios(config.esios_token, config.intervalo_descargas_s)
    planificador.ejecutar(config, fuente, stop, cliente_esios=cliente_esios)
    log.info("parada completada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
