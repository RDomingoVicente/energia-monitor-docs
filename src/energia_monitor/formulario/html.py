"""HTML que escapa por defecto.

Todo valor que llega a una plantilla se escapa salvo que ya sea `Html`, que solo
crea este módulo (escapando) o el código de las pantallas con texto fijo.
"""

from __future__ import annotations

import html as _html
from collections.abc import Iterable


class Html(str):
    """Texto ya seguro para insertar en HTML."""

    __slots__ = ()


def esc(valor: object) -> Html:
    if isinstance(valor, Html):
        return valor
    if valor is None:
        return Html("")
    return Html(_html.escape(str(valor), quote=True))


def h(plantilla: str, *args: object, **kwargs: object) -> Html:
    """str.format con cada argumento escapado. La plantilla es texto fijo del código."""
    return Html(plantilla.format(*(esc(a) for a in args), **{k: esc(v) for k, v in kwargs.items()}))


def unir(partes: Iterable[object], sep: str = "") -> Html:
    return Html(sep.join(esc(p) for p in partes))
