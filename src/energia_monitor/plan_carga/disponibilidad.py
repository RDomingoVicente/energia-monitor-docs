"""Disponibilidad aprendida (E3.3). Funciones puras.

Un cuarto observado tiene alguna muestra con estado; está enchufado si todas
sus muestras con estado son no libres (plan_cuartos_observados). La
probabilidad por (día ISO, minuto local del día) es enchufados ÷ observados;
los cuartos sin muestras no cuentan.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from energia_monitor.tiempo import MADRID

Clave = tuple[int, int]


def clave(inicio: datetime) -> Clave:
    local = inicio.astimezone(MADRID)
    return local.isoweekday(), local.hour * 60 + local.minute


def perfil(observados: Iterable[tuple[datetime, bool]]) -> dict[Clave, Decimal]:
    total: dict[Clave, int] = defaultdict(int)
    enchufado: dict[Clave, int] = defaultdict(int)
    for inicio, conectado in observados:
        k = clave(inicio)
        total[k] += 1
        enchufado[k] += conectado
    return {k: Decimal(enchufado[k]) / total[k] for k in total}


def dias_historial(observados: Iterable[tuple[datetime, bool]]) -> int:
    """Días locales con algún cuarto observado."""
    return len({inicio.astimezone(MADRID).date() for inicio, _ in observados})
