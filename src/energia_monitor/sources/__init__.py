"""Fuentes de datos: precios del mercado diario (PRICE_SOURCE) y ESIOS."""

from __future__ import annotations

from energia_monitor.http import ClienteHttp, Limitador
from energia_monitor.sources.base import ErrorFuente, FuentePrecios, PrecioTramo, PreciosDia
from energia_monitor.sources.esios import ClienteEsios
from energia_monitor.sources.omie import FuenteOmie

__all__ = [
    "ClienteEsios", "ErrorFuente", "FuenteOmie", "FuentePrecios", "PrecioTramo", "PreciosDia",
    "crear_cliente_esios", "crear_fuente",
]


def crear_fuente(nombre: str, intervalo_descargas_s: float = 1.0) -> FuentePrecios:
    """Fuente del precio de mercado según PRICE_SOURCE (OMIE)."""
    if nombre == "omie":
        return FuenteOmie(ClienteHttp(limitador=Limitador(intervalo_descargas_s)))
    raise ValueError(f"PRICE_SOURCE no soportada: {nombre!r} (disponibles: omie)")


def crear_cliente_esios(token: str | None, intervalo_descargas_s: float = 1.0) -> ClienteEsios | None:
    """Cliente de ESIOS (SSAA y PVPC), o None sin ESIOS_TOKEN."""
    if not token:
        return None
    return ClienteEsios(token, ClienteHttp(limitador=Limitador(intervalo_descargas_s)))
