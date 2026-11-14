"""Referencia en Python de la energía por tramo y del coste (migraciones 0011 y 0013).

El servicio calcula en SQL; este módulo replica la misma aritmética con
Decimal para validar las vistas en los tests.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

TRAMO = timedelta(minutes=15)
COBERTURA_MIN_S = 840
ORIGEN_EPOCA = datetime(2000, 1, 1, tzinfo=UTC)
MIL = Decimal(1000)
SEG_HORA = Decimal(3600)


def inicio_tramo(instante: datetime) -> datetime:
    """Cuarto de hora UTC que contiene el instante (date_bin de PostgreSQL)."""
    return ORIGEN_EPOCA + (instante - ORIGEN_EPOCA) // TRAMO * TRAMO


@dataclass(frozen=True)
class Muestra:
    instante: datetime
    intervalo_s: int | None
    evse_w: Decimal
    home_w: Decimal = Decimal(0)
    total_w: Decimal = Decimal(0)


@dataclass
class EnergiaTramo:
    # W·s acumulados; los Wh se dividen una sola vez, como la vista.
    coche_ws: Decimal = Decimal(0)
    casa_ws: Decimal = Decimal(0)
    total_ws: Decimal = Decimal(0)
    cubierto_s: Decimal = Decimal(0)
    # Alguna muestra del tramo con carga (evse_w > 0): la casa es desconocida.
    con_carga: bool = False

    @property
    def coche_wh(self) -> Decimal:
        return self.coche_ws / SEG_HORA

    @property
    def casa_wh(self) -> Decimal | None:
        # Durante la carga homePower = 0 (verificado el 14/11/2026): no se sabe.
        return None if self.con_carga else self.casa_ws / SEG_HORA

    @property
    def total_wh(self) -> Decimal:
        return self.total_ws / SEG_HORA

    @property
    def incompleto(self) -> bool:
        return self.cubierto_s < COBERTURA_MIN_S


def _solape_s(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> Decimal:
    segundos = (min(a1, b1) - max(a0, b0)) / timedelta(microseconds=1)
    return Decimal(max(int(segundos), 0)) / Decimal(1_000_000)


def reparto_muestra(m: Muestra) -> list[tuple[datetime, Decimal]]:
    """(tramo, segundos) que cubre la muestra; vacío si no cuenta."""
    if m.intervalo_s is None:
        return []
    desde = m.instante - timedelta(seconds=m.intervalo_s)
    primero = inicio_tramo(desde)
    partes = []
    for tramo in (primero, primero + TRAMO):
        s = _solape_s(desde, m.instante, tramo, tramo + TRAMO)
        if s > 0:
            partes.append((tramo, s))
    return partes


def energia_por_tramo(muestras: Iterable[Muestra]) -> dict[datetime, EnergiaTramo]:
    tramos: dict[datetime, EnergiaTramo] = defaultdict(EnergiaTramo)
    for m in muestras:
        for tramo, s in reparto_muestra(m):
            e = tramos[tramo]
            e.coche_ws += m.evse_w * s
            e.casa_ws += m.home_w * s
            e.total_ws += m.total_w * s
            e.cubierto_s += s
            e.con_carga = e.con_carga or m.evse_w > 0
    return dict(tramos)


def reparto_estimado(inicio: datetime, fin: datetime, energia_wh: Decimal,
                     potencia_w: Decimal) -> dict[datetime, Decimal]:
    """E1.7: carga continua desde el inicio a potencia_w; uniforme si no cabe en la sesión."""
    if energia_wh <= 0 or fin <= inicio:
        return {}
    duracion = timedelta(seconds=float(energia_wh * SEG_HORA / potencia_w))
    fin_carga = min(fin, inicio + duracion)
    ventana = _solape_s(inicio, fin_carga, inicio, fin_carga)
    reparto: dict[datetime, Decimal] = {}
    tramo = inicio_tramo(inicio)
    while tramo < fin_carga:
        s = _solape_s(inicio, fin_carga, tramo, tramo + TRAMO)
        if s > 0:
            reparto[tramo] = energia_wh * s / ventana
        tramo += TRAMO
    return reparto


def coste(energia_wh: Decimal, factor: Decimal, precio_eur_kwh: Decimal) -> Decimal:
    """Coste en € de la energía del tramo corregida por el factor del contador."""
    return energia_wh * factor / MIL * precio_eur_kwh
