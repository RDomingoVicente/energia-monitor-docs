"""Precio de cada cuarto, umbral barato y potencia de carga (E3.2 y E3.4). Funciones puras."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal

from energia_monitor.tiempo import MADRID


def publicado(inicio: datetime, ahora: datetime, hora_publicacion_min: int) -> bool:
    """Los precios del día D+1 cuentan como publicados desde la hora_publicacion del día D (hora local)."""
    dia = inicio.astimezone(MADRID).date()
    local = ahora.astimezone(MADRID)
    if dia <= local.date():
        return True
    return dia == local.date() + timedelta(days=1) and local.hour * 60 + local.minute >= hora_publicacion_min


def perfil_esperado(historico: Iterable[tuple[datetime, Decimal]], ahora: datetime,
                    ventana_dias: int) -> dict[tuple[int, int], Decimal]:
    """Media del precio final por (día ISO de la semana, hora local) en [ahora − ventana, ahora)."""
    desde = ahora - timedelta(days=ventana_dias)
    grupos: dict[tuple[int, int], list[Decimal]] = defaultdict(list)
    for inicio, precio in historico:
        if desde <= inicio < ahora:
            local = inicio.astimezone(MADRID)
            grupos[(local.isoweekday(), local.hour)].append(precio)
    return {k: sum(v, Decimal(0)) / len(v) for k, v in grupos.items()}


def umbral(historico: Iterable[tuple[datetime, Decimal]], ahora: datetime, ventana_dias: int,
           pct: Decimal) -> Decimal | None:
    """pct × media del precio final de los ventana_dias días locales completos anteriores a hoy."""
    hoy = ahora.astimezone(MADRID).date()
    desde = hoy - timedelta(days=ventana_dias)
    precios = [p for inicio, p in historico if desde <= inicio.astimezone(MADRID).date() < hoy]
    if not precios:
        return None
    return pct / 100 * sum(precios, Decimal(0)) / len(precios)


def precio_cuarto(inicio: datetime, ahora: datetime, precios: Mapping[datetime, Decimal],
                  perfil: Mapping[tuple[int, int], Decimal], hora_publicacion_min: int) -> tuple[Decimal | None, bool]:
    """(precio, provisional): el publicado si existe; si no, el esperado del perfil (provisional)."""
    if inicio in precios and publicado(inicio, ahora, hora_publicacion_min):
        return precios[inicio], False
    local = inicio.astimezone(MADRID)
    return perfil.get((local.isoweekday(), local.hour)), True


def mediana(valores: list[Decimal]) -> Decimal:
    """Con interpolación lineal (percentile_cont(0.5))."""
    v = sorted(valores)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
