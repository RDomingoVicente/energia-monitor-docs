"""Bucle del servicio: arranque y ejecuciones diarias a horas fijas de Madrid."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time, timedelta

import psycopg

from energia_monitor import colector, db
from energia_monitor.config import Config
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
) -> None:
    """Arranque (migraciones y huecos) y después bucle de ejecuciones diarias."""
    with conectar() as conn:
        preparar_bd(conn, config)
        colector.completar_desde(conn, fuente, config.carga_inicial_desde, hoy_madrid(reloj()), stop)

    while not stop.is_set():
        siguiente = proxima_ejecucion(reloj(), config.horarios)
        log.info("próxima ejecución: %s", siguiente.isoformat())
        espera = (siguiente - reloj()).total_seconds()
        if stop.wait(max(espera, 0)):
            break
        try:
            with conectar() as conn:
                colector.actualizar_hoy_y_manana(conn, fuente, hoy_madrid(reloj()), stop)
        except Exception:
            # El servicio sigue vivo: la siguiente ejecución lo reintentará.
            log.exception("fallo en la actualización planificada")
