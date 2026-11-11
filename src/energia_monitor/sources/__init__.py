"""Fuentes de precios seleccionables con PRICE_SOURCE."""

from __future__ import annotations

from energia_monitor.http import ClienteHttp, Limitador
from energia_monitor.sources.base import ErrorFuente, FuentePrecios, PrecioTramo, PreciosDia
from energia_monitor.sources.omie import FuenteOmie

__all__ = ["ErrorFuente", "FuenteOmie", "FuentePrecios", "PrecioTramo", "PreciosDia", "crear_fuente"]


def crear_fuente(nombre: str, intervalo_descargas_s: float = 1.0) -> FuentePrecios:
    """Fuente según PRICE_SOURCE. ESIOS se añadirá cuando haya token."""
    if nombre == "omie":
        return FuenteOmie(ClienteHttp(limitador=Limitador(intervalo_descargas_s)))
    raise ValueError(f"PRICE_SOURCE no soportada: {nombre!r} (disponibles: omie)")
