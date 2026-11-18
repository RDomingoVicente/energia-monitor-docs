"""Franjas: agrupación de cuartos, tareas del cargador y medianoche (E3.1 y E3.4). Funciones puras.

Una tarea no cruza la medianoche local (verificado la noche del 17 al 18/11/2026):
una franja que la cruza se escribe como dos tareas, una hasta las 00:00 y otra
desde las 00:00. hourMin y duration son minutos de reloj local; en las noches de
cambio de hora la duración es la de reloj (sin verificar en el cargador: 3c).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from energia_monitor.plan_carga.modelo import CUARTO
from energia_monitor.tiempo import MADRID


def agrupar(cuartos: Iterable[datetime]) -> list[tuple[datetime, datetime]]:
    """Cuartos contiguos → intervalos [inicio, fin)."""
    grupos: list[tuple[datetime, datetime]] = []
    for q in sorted(set(cuartos)):
        if grupos and grupos[-1][1] == q:
            grupos[-1] = (grupos[-1][0], q + CUARTO)
        else:
            grupos.append((q, q + CUARTO))
    return grupos


@dataclass(frozen=True)
class Tarea:
    """Una tarea con fecha del cargador (day, month, weekday 0)."""

    fecha: date
    hora_min: int  # hourMin: minutos desde las 00:00 locales
    duracion: int  # duration en minutos de reloj local


def _minuto_local(instante: datetime) -> tuple[date, int]:
    local = instante.astimezone(MADRID)
    return local.date(), local.hour * 60 + local.minute


def tareas(inicio: datetime, fin: datetime) -> list[Tarea]:
    """Tareas de una franja, partida en cada medianoche local que cruce."""
    salida: list[Tarea] = []
    dia, minuto = _minuto_local(inicio)
    dia_fin, minuto_fin = _minuto_local(fin)
    while (dia, minuto) < (dia_fin, minuto_fin):
        hasta = minuto_fin if dia == dia_fin else 24 * 60
        salida.append(Tarea(dia, minuto, hasta - minuto))
        dia, minuto = dia + timedelta(days=1), 0
    return salida


def num_tareas(inicio: datetime, fin: datetime) -> int:
    return len(tareas(inicio, fin))


def solapa(a_inicio: datetime, a_fin: datetime, b_inicio: datetime, b_fin: datetime) -> bool:
    return a_inicio < b_fin and b_inicio < a_fin
