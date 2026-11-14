"""Bucle del servicio: arranque y ejecuciones diarias a horas fijas de Madrid.

Dos calendarios: OMIE (HORARIOS) y, si hay ESIOS_TOKEN, ESIOS
(ESIOS_HORARIOS). Tras cualquier ejecución con ESIOS activo se recalcula la
estimación de SSAA. Los domingos, la primera ejecución de ESIOS recarga los
últimos 90 días para detectar revisiones de REE. Al final del arranque y de
cada ejecución se refresca mv_precio_final una vez.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta

import psycopg

from energia_monitor import colector, db, esios, estimacion
from energia_monitor.config import Config
from energia_monitor.errores import ErrorAutenticacion
from energia_monitor.sources.base import FuentePrecios
from energia_monitor.tiempo import MADRID, hoy_madrid

log = logging.getLogger(__name__)


def proxima_ejecucion(ahora: datetime, horarios: Sequence[time]) -> datetime:
    """Primera hora planificada (Europe/Madrid) estrictamente posterior a ahora."""
    local = ahora.astimezone(MADRID)
    for desplazamiento in (0, 1):
        dia = local.date() + timedelta(days=desplazamiento)
        for hora in sorted(horarios):
            candidata = datetime.combine(dia, hora, tzinfo=MADRID)
            if candidata > local:
                return candidata
    raise ValueError("sin horarios planificados")


def proximas_tareas(ahora: datetime, planes: Mapping[str, Sequence[time]]) -> tuple[datetime, list[str]]:
    """Siguiente instante planificado y las tareas que tocan en él."""
    candidatas = {nombre: proxima_ejecucion(ahora, horarios) for nombre, horarios in planes.items() if horarios}
    if not candidatas:
        raise ValueError("sin horarios planificados")
    siguiente = min(candidatas.values())
    return siguiente, sorted(n for n, t in candidatas.items() if t == siguiente)


def es_recarga_semanal(instante: datetime, horarios_esios: Sequence[time]) -> bool:
    """Domingo, en la primera hora de ESIOS del día."""
    local = instante.astimezone(MADRID)
    return local.weekday() == 6 and local.time() == min(horarios_esios)


def preparar_bd(conn: psycopg.Connection, config: Config) -> None:
    aplicadas = db.migrar(conn)
    log.info("migraciones aplicadas: %s", ", ".join(aplicadas) if aplicadas else "ninguna pendiente")
    if config.grafana_db_password:
        db.configurar_rol_grafana(conn, config.grafana_db_password)
        log.info("rol grafana_ro habilitado para login")


def ejecutar(
    config: Config,
    fuente: FuentePrecios,
    stop: threading.Event,
    conectar: Callable[[], psycopg.Connection] = db.conectar,
    reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    cliente_esios: esios.Cliente | None = None,
) -> None:
    """Arranque (migraciones y huecos) y después bucle de ejecuciones diarias."""
    if cliente_esios is None:
        log.info("ESIOS desactivado (sin ESIOS_TOKEN): SSAA con el parámetro estimado")
    with conectar() as conn:
        preparar_bd(conn, config)
        colector.completar_desde(conn, fuente, config.carga_inicial_desde, hoy_madrid(reloj()), stop)
        if cliente_esios is not None and not stop.is_set():
            cliente_esios = _tarea_esios(
                lambda: esios.completar_desde(conn, cliente_esios, config.esios_carga_desde,  # type: ignore[arg-type]
                                              hoy_madrid(reloj()), stop),
                cliente_esios, "carga inicial de ESIOS",
            )
        if cliente_esios is not None and not stop.is_set():
            _estimar(conn, hoy_madrid(reloj()))
        _refrescar(conn)

    while not stop.is_set():
        planes = {"omie": config.horarios, "esios": config.esios_horarios if cliente_esios else ()}
        siguiente, tareas = proximas_tareas(reloj(), planes)
        log.info("próxima ejecución: %s (%s)", siguiente.isoformat(), ", ".join(tareas))
        espera = (siguiente - reloj()).total_seconds()
        if stop.wait(max(espera, 0)):
            break
        try:
            with conectar() as conn:
                hoy = hoy_madrid(reloj())
                if "omie" in tareas:
                    try:
                        colector.actualizar_hoy_y_manana(conn, fuente, hoy, stop)
                    except Exception:
                        log.exception("fallo en la actualización de precios")
                if "esios" in tareas and cliente_esios is not None:
                    semanal = es_recarga_semanal(siguiente, config.esios_horarios)
                    cliente = cliente_esios
                    cliente_esios = _tarea_esios(
                        lambda: esios.actualizar(
                            conn, cliente, hoy, stop=stop,
                            dias_atras=esios.DIAS_RECARGA_SEMANAL if semanal else esios.DIAS_ACTUALIZACION,
                            etiqueta="recarga semanal" if semanal else "actualización",
                        ),
                        cliente_esios, "actualización de ESIOS",
                    )
                if cliente_esios is not None and not stop.is_set():
                    _estimar(conn, hoy)
                _refrescar(conn)
        except Exception:
            # El servicio sigue vivo: la siguiente ejecución lo reintentará.
            log.exception("fallo en la actualización planificada")


def _tarea_esios(tarea: Callable[[], object], cliente: esios.Cliente,
                 descripcion: str) -> esios.Cliente | None:
    """Ejecuta una tarea de ESIOS; si REE rechaza el token, desactiva ESIOS."""
    try:
        tarea()
    except ErrorAutenticacion as e:
        log.error("%s: %s. ESIOS queda desactivado hasta reiniciar con un ESIOS_TOKEN válido", descripcion, e)
        return None
    except Exception:
        log.exception("fallo en la %s", descripcion)
    return cliente


def _estimar(conn: psycopg.Connection, hoy: date) -> None:
    try:
        estimacion.actualizar(conn, hoy)
    except Exception:
        log.exception("fallo al estimar SSAA")


def _refrescar(conn: psycopg.Connection) -> None:
    """Un fallo solo se registra: los datos ya están guardados y el siguiente
    ciclo vuelve a refrescar."""
    try:
        db.refrescar_precio_final(conn)
    except Exception:
        log.exception("fallo al refrescar mv_precio_final")
