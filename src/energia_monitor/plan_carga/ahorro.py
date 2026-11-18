"""Ahorro por conexión (E3.8): referencia en Python de la vista v_plan_ahorro.

Con la energía real E (contador del cargador) de una conexión:

- real: la energía de cada cuarto a su precio;
- plan: E repartida en orden de hora en los cuartos del plan efectivo dentro
  de la conexión y, si no basta, en los primeros cuartos libres de la
  conexión a la potencia de carga;
- referencia: E desde el enchufe a la potencia de carga.

Coste = kWh × factor_contador × precio final. En los cuartos partidos por el
enchufe o la desconexión, la capacidad es proporcional a la parte conectada.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from energia_monitor.tiempo import MADRID

CUARTO = timedelta(minutes=15)
_ORIGEN = datetime(2000, 1, 1, tzinfo=UTC)


def cuarto_de(instante: datetime) -> datetime:
    """Inicio del cuarto de hora que contiene el instante (date_bin de 15 min)."""
    n = (instante - _ORIGEN) // CUARTO
    return _ORIGEN + n * CUARTO


@dataclass(frozen=True)
class Ahorro:
    energia_cargador_kwh: Decimal
    plan_kwh: Decimal
    referencia_kwh: Decimal
    coste_real_eur: Decimal | None
    coste_plan_eur: Decimal | None
    coste_referencia_eur: Decimal | None
    sin_precio: bool

    @property
    def plan_cubre(self) -> bool:
        return self.plan_kwh >= self.energia_cargador_kwh


def _repartir(capacidades: list[Decimal], total: Decimal) -> list[Decimal]:
    resto, salida = total, []
    for cap in capacidades:
        e = max(Decimal(0), min(cap, resto))
        salida.append(e)
        resto -= e
    return salida


def ahorro_conexion(
    inicio: datetime,
    fin: datetime,
    energia_wh: Mapping[datetime, Decimal],
    plan_kwh: Mapping[datetime, Decimal],
    potencia_kw: Mapping[int, Decimal],
    precio: Mapping[datetime, Decimal],
    factor: Callable[[date], Decimal | None],
) -> Ahorro:
    """energia_wh y plan_kwh por inicio de cuarto; potencia_kw por hora local (0–23)."""
    cuartos: list[tuple[datetime, Decimal]] = []
    q = cuarto_de(inicio)
    while q < fin:
        segundos = Decimal((min(q + CUARTO, fin) - max(q, inicio)).total_seconds())
        cuartos.append((q, segundos / 3600))
        q += CUARTO
    e_kwh = {q: energia_wh.get(q, Decimal(0)) / 1000 for q, _ in cuartos}
    total = sum(e_kwh.values(), Decimal(0))
    libre = {q: potencia_kw[q.astimezone(MADRID).hour] * h for q, h in cuartos}
    plan_cap = {q: plan_kwh[q] * h / Decimal("0.25") for q, h in cuartos if q in plan_kwh}

    orden_plan = [q for q, _ in cuartos if q in plan_cap] + [q for q, _ in cuartos if q not in plan_cap]
    e_plan = dict(zip(orden_plan, _repartir([plan_cap.get(q, libre[q]) for q in orden_plan], total), strict=True))
    orden_ref = [q for q, _ in cuartos]
    e_ref = dict(zip(orden_ref, _repartir([libre[q] for q in orden_ref], total), strict=True))

    factores = {q: factor(q.astimezone(MADRID).date()) for q, _ in cuartos}
    sin_precio = any(q not in precio or factores[q] is None for q, _ in cuartos)

    def coste(e: Mapping[datetime, Decimal]) -> Decimal | None:
        if sin_precio:
            return None
        return sum((e[q] * factores[q] * precio[q] for q, _ in cuartos), Decimal(0))  # type: ignore[operator]

    return Ahorro(
        energia_cargador_kwh=total,
        plan_kwh=sum(plan_cap.values(), Decimal(0)),
        referencia_kwh=sum(e_ref.values(), Decimal(0)),
        coste_real_eur=coste(e_kwh),
        coste_plan_eur=coste(e_plan),
        coste_referencia_eur=coste(e_ref),
        sin_precio=sin_precio,
    )
