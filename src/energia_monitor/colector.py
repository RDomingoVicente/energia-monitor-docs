"""Detección de días sin precios y descarga desde la fuente."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from energia_monitor.errores import ErrorFuente
from energia_monitor.repository import guardar_precios
from energia_monitor.sources.base import FuentePrecios, PreciosDia
from energia_monitor.tiempo import dias, limites_dia_utc

log = logging.getLogger(__name__)

# Duración cubierta por los tramos guardados de cada día civil de Madrid.
SQL_COBERTURA = """
SELECT (inicio AT TIME ZONE 'Europe/Madrid')::date AS dia, sum(fin - inicio) AS cubierto
FROM precio_mercado
WHERE fuente = %s AND inicio >= %s AND inicio < %s
GROUP BY 1
"""

PROGRESO_CADA = 10


def dias_incompletos(conn: psycopg.Connection, fuente: str, desde: date, hasta: date) -> list[date]:
    """Días de [desde, hasta] cuyos tramos guardados no cubren el día completo."""
    if hasta < desde:
        return []
    inicio, _ = limites_dia_utc(desde)
    _, fin = limites_dia_utc(hasta)
    cobertura: dict[date, timedelta] = dict(conn.execute(SQL_COBERTURA, (fuente, inicio, fin)).fetchall())
    incompletos = []
    for dia in dias(desde, hasta):
        a, b = limites_dia_utc(dia)
        if cobertura.get(dia) != b - a:
            incompletos.append(dia)
    return incompletos


@dataclass
class Resumen:
    guardados: list[date] = field(default_factory=list)
    no_publicados: list[date] = field(default_factory=list)
    fallidos: list[date] = field(default_factory=list)
    interrumpido: bool = False


def descargar_dias(
    conn: psycopg.Connection,
    fuente: FuentePrecios,
    pendientes: Sequence[date],
    etiqueta: str,
    stop: threading.Event | None = None,
    guardar: Callable[[psycopg.Connection, PreciosDia], None] = guardar_precios,
) -> Resumen:
    """Descarga y guarda los días indicados, registrando el avance en el log.

    Un fallo en un día no detiene el resto. Si se activa stop, termina tras el
    día en curso.
    """
    resumen = Resumen()
    total = len(pendientes)
    if total == 0:
        log.info("%s: sin días pendientes", etiqueta)
        return resumen
    log.info("%s: %d día(s) pendiente(s), de %s a %s", etiqueta, total, pendientes[0], pendientes[-1])
    for i, dia in enumerate(pendientes, start=1):
        if stop is not None and stop.is_set():
            resumen.interrumpido = True
            log.warning("%s: interrumpida en %d/%d", etiqueta, i - 1, total)
            break
        try:
            precios = fuente.precios_dia(dia)
            if precios is None:
                resumen.no_publicados.append(dia)
                log.info("%s: %s aún no publicado en %s", etiqueta, dia, fuente.nombre)
            else:
                guardar(conn, precios)
                resumen.guardados.append(dia)
                log.debug("%s: %s guardado (%d tramos, versión %s)",
                          etiqueta, dia, len(precios.tramos), precios.version)
        except ErrorFuente as e:
            resumen.fallidos.append(dia)
            log.warning("%s: fallo en %s: %s", etiqueta, dia, e)
        if total > 1 and (i % PROGRESO_CADA == 0 or i == total):
            log.info("%s: %d/%d días (%d%%), último %s", etiqueta, i, total, i * 100 // total, dia)
    log.info(
        "%s: %d guardado(s), %d sin publicar, %d con error",
        etiqueta, len(resumen.guardados), len(resumen.no_publicados), len(resumen.fallidos),
    )
    return resumen


def completar_desde(
    conn: psycopg.Connection,
    fuente: FuentePrecios,
    desde: date,
    hoy: date,
    stop: threading.Event | None = None,
) -> Resumen:
    """Al arrancar: descarga todo día sin precios entre desde y mañana.

    Con la tabla vacía equivale a la carga inicial; en arranques posteriores
    normalmente solo faltan hoy o mañana, o los días que fallaron antes.
    """
    pendientes = dias_incompletos(conn, fuente.nombre, desde, hoy + timedelta(days=1))
    etiqueta = "carga inicial" if len(pendientes) > 2 else "arranque"
    return descargar_dias(conn, fuente, pendientes, etiqueta, stop)


def actualizar_hoy_y_manana(
    conn: psycopg.Connection,
    fuente: FuentePrecios,
    hoy: date,
    stop: threading.Event | None = None,
) -> Resumen:
    """Ejecución planificada: completa hoy y mañana si les faltan tramos."""
    pendientes = dias_incompletos(conn, fuente.nombre, hoy, hoy + timedelta(days=1))
    return descargar_dias(conn, fuente, pendientes, "actualización diaria", stop)
