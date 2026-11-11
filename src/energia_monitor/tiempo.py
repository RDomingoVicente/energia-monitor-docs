"""Utilidades de fechas: días civiles de Madrid expresados en UTC."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")


def limites_dia_utc(dia: date) -> tuple[datetime, datetime]:
    """Inicio y fin en UTC del día civil de Madrid (23, 24 o 25 h)."""
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=MADRID).astimezone(UTC)
    siguiente = dia + timedelta(days=1)
    fin = datetime(siguiente.year, siguiente.month, siguiente.day, tzinfo=MADRID).astimezone(UTC)
    return inicio, fin


def dias(desde: date, hasta: date) -> Iterator[date]:
    """Días de desde a hasta, ambos incluidos."""
    dia = desde
    while dia <= hasta:
        yield dia
        dia += timedelta(days=1)


def hoy_madrid(ahora: datetime | None = None) -> date:
    return (ahora or datetime.now(UTC)).astimezone(MADRID).date()
