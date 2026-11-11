"""Referencia en Python del cálculo de la vista v_precio_final.

El cálculo que usa el servicio está en SQL (db/migrations/0005_*.sql). Este
módulo replica la misma aritmética con Decimal para validarla en los tests y
comprobar que la vista coincide tramo a tramo.
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
MIL = Decimal(1000)
UNO = Decimal(1)

HORAS_P1 = frozenset({10, 11, 12, 13, 18, 19, 20, 21})


def periodo_2td(instante: datetime, festivos: Container[date]) -> str:
    """Periodo 2.0TD (Circular CNMC 3/2020) del instante, evaluado en Europe/Madrid."""
    if instante.tzinfo is None:
        raise ValueError("el instante debe llevar zona horaria")
    local = instante.astimezone(MADRID)
    if local.isoweekday() >= 6 or local.date() in festivos:
        return "P3"
    if local.hour < 8:
        return "P3"
    if local.hour in HORAS_P1:
        return "P1"
    return "P2"


@dataclass(frozen=True)
class ParametrosTramo:
    """Parámetros aplicables a un tramo (unidades como en contrato_parametro)."""

    ssaa: Decimal  # €/MWh
    pagos_capacidad: Decimal  # €/kWh
    ros: Decimal  # €/MWh
    rom: Decimal  # €/MWh
    perdidas: Decimal  # fracción
    fnee: Decimal  # €/MWh
    rfe: Decimal  # €/MWh
    tasa_municipal: Decimal  # fracción
    peaje: Decimal  # €/kWh
    cargo: Decimal  # €/kWh
    impuesto_electrico: Decimal  # fracción
    iva: Decimal  # fracción


def energia_eur_kwh(omie_eur_mwh: Decimal, p: ParametrosTramo) -> Decimal:
    """Término de energía de la cláusula 4.2 sin peajes ni cargos, en €/kWh."""
    mercado = omie_eur_mwh + p.ssaa + p.pagos_capacidad * MIL + p.ros + p.rom
    return (mercado * (UNO + p.perdidas) + p.fnee + p.rfe) * (UNO + p.tasa_municipal) / MIL


def coste_sin_impuestos_eur_kwh(omie_eur_mwh: Decimal, p: ParametrosTramo) -> Decimal:
    return energia_eur_kwh(omie_eur_mwh, p) + p.peaje + p.cargo


def precio_final_eur_kwh(omie_eur_mwh: Decimal, p: ParametrosTramo) -> Decimal:
    """Coste marginal del kWh con IEE e IVA."""
    return (
        coste_sin_impuestos_eur_kwh(omie_eur_mwh, p)
        * (UNO + p.impuesto_electrico)
        * (UNO + p.iva)
    )
