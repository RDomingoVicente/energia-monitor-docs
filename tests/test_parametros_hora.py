"""Unidad «hora» de los parámetros de batería (0020): minutos desde las 00:00, escritos HH:MM."""

from decimal import Decimal as D

import pytest

from energia_monitor.parametros import ErrorParametro, hora_de_minutos, minutos_de_hora, valor_de_texto


@pytest.mark.parametrize(("texto", "minutos"), [("13:30", 810), ("0:00", 0), ("09:05", 545), ("23:59", 1439),
                                                (" 12:00 ", 720)])
def test_minutos_de_hora(texto: str, minutos: int) -> None:
    assert minutos_de_hora(texto) == D(minutos)
    assert minutos_de_hora(hora_de_minutos(D(minutos))) == D(minutos)


@pytest.mark.parametrize("texto", ["13,5", "13.5", "24:00", "12:60", "1330", "13:3", "", "-1:00"])
def test_hora_no_valida(texto: str) -> None:
    with pytest.raises(ErrorParametro, match="HH:MM"):
        minutos_de_hora(texto)


def test_valor_de_texto() -> None:
    assert valor_de_texto("13:30", "hora") == D(810)
    assert valor_de_texto("0,86", "factor") == D("0.86")
    with pytest.raises(ErrorParametro):
        valor_de_texto("13:30", "%")
    assert hora_de_minutos(D(810)) == "13:30"
