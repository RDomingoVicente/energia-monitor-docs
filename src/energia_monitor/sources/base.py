"""Interfaz común de las fuentes de precios del mercado diario."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from energia_monitor.errores import ErrorFuente

__all__ = ["ErrorFuente", "FuentePrecios", "PrecioTramo", "PreciosDia"]


@dataclass(frozen=True)
class PrecioTramo:
    """Precio de un tramo de liquidación [inicio, fin) en UTC."""

    inicio: datetime
    fin: datetime
    precio_eur_mwh: Decimal


@dataclass(frozen=True)
class PreciosDia:
    """Precios de un día (en hora de Madrid) tal como los publica la fuente."""

    dia: date
    fuente: str
    version: int | None
    tramos: tuple[PrecioTramo, ...]


class FuentePrecios(Protocol):
    """Fuente de precios seleccionable con PRICE_SOURCE."""

    nombre: str

    def precios_dia(self, dia: date) -> PreciosDia | None:
        """Precios del día, o None si la fuente aún no los ha publicado."""
        ...
