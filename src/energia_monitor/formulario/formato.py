"""Formato es-ES de la página: coma decimal, 24 h, «lun 16/11 09:30», pts (formulario-maquetas.md, 5)."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from energia_monitor.tiempo import MADRID

DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MENOS = "−"


def numero(x: Decimal | float | int, decimales: int = 1) -> str:
    q = Decimal(str(x)).quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    if q == 0:
        q = abs(q)
    texto = f"{q:.{decimales}f}".replace(".", ",")
    return texto.replace("-", MENOS)


def entero(x: Decimal | float | int) -> int:
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def kwh(x: Decimal | float | int | None, signo: bool = False) -> str:
    if x is None:
        return "—"
    prefijo = "+" if signo and Decimal(str(x)) > 0 else ""
    return f"{prefijo}{numero(x)} kWh"


def pct(x: Decimal | float | int | None) -> str:
    return "—" if x is None else f"{entero(x)} %"


def pts(x: Decimal | float | int, signo: bool = True) -> str:
    n = entero(x)
    if not signo or n == 0:
        return f"{abs(n) if not signo else n} pts"
    return f"+{n} pts" if n > 0 else f"{MENOS}{-n} pts"


def local(instante: datetime) -> datetime:
    return instante.astimezone(MADRID)


def dia(instante: datetime) -> str:
    """«lun 16/11»."""
    t = local(instante)
    return f"{DIAS[t.weekday()]} {t:%d/%m}"


def hora(instante: datetime) -> str:
    return f"{local(instante):%H:%M}"


def dia_hora(instante: datetime) -> str:
    """«lun 16/11 09:30»."""
    return f"{dia(instante)} {hora(instante)}"


def corta(instante: datetime) -> str:
    """«14/11 19:42»."""
    return f"{local(instante):%d/%m %H:%M}"


def cuando(instante: datetime, ahora: datetime) -> str:
    """«hoy 19:42», «ayer 19:42» o «14/11 19:42»."""
    d = (local(ahora).date() - local(instante).date()).days
    if d == 0:
        return f"hoy {hora(instante)}"
    if d == 1:
        return f"ayer {hora(instante)}"
    return corta(instante)
