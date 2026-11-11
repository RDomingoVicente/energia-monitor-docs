"""Aritmética de la fórmula del contrato con parámetros fijados a mano.

No es una conciliación con la factura de octubre (Feníe no desglosa SSAA,
pagos por capacidad, ROS&OM, pérdidas, FNEE ni tasa municipal): comprueba que
la fórmula se aplica bien con unos valores conocidos. Los resultados
esperados se han calculado aparte con fracciones exactas.
"""

from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from energia_monitor.tarifa import (
    ParametrosTramo,
    coste_sin_impuestos_eur_kwh,
    energia_eur_kwh,
    periodo_2td,
    precio_final_eur_kwh,
)

OMIE_OCTUBRE = D("166.59")  # media simple de octubre de 2026 (docs/contexto.md)

COMUNES = dict(
    ssaa=D("12.50"),
    ros=D("0.30"),
    rom=D("0.0407"),
    fnee=D("2.4374639"),
    rfe=D("20"),
    tasa_municipal=D("0.015"),
    impuesto_electrico=D("0.0511269632"),
    iva=D("0.21"),
)

PARAMETROS = {
    "P1": ParametrosTramo(pagos_capacidad=D("0.000800"), perdidas=D("0.167"),
                          peaje=D("0.033261"), cargo=D("0.064292"), **COMUNES),
    "P2": ParametrosTramo(pagos_capacidad=D("0.000133"), perdidas=D("0.163"),
                          peaje=D("0.016409"), cargo=D("0.012858"), **COMUNES),
    "P3": ParametrosTramo(pagos_capacidad=D("0"), perdidas=D("0.180"),
                          peaje=D("0.000077"), cargo=D("0.003215"), **COMUNES),
}

CONSUMO_OCTUBRE_KWH = {"P1": D(182), "P2": D(240), "P3": D(840)}


def test_energia_p1_paso_a_paso() -> None:
    # mercado = 166,59 + 12,50 + 0,000800·1000 + 0,30 + 0,0407 = 180,2307 €/MWh
    # × (1 + 0,167)                     = 210,3292269
    # + FNEE 2,4374639 + RFE 20         = 232,7666908
    # × (1 + 0,015)                     = 236,258191162 €/MWh
    assert energia_eur_kwh(OMIE_OCTUBRE, PARAMETROS["P1"]) == D("0.236258191162")


@pytest.mark.parametrize(
    ("periodo", "energia", "coste", "final"),
    [
        ("P1", "0.236258191162", "0.333811191162", "0.424562311814428164478464"),
        ("P2", "0.234739097705", "0.264006097705", "0.335779752573796358741760"),
        ("P3", "0.2376781752485", "0.2409701752485", "0.306481200722180408529792"),
    ],
)
def test_precio_por_periodo(periodo: str, energia: str, coste: str, final: str) -> None:
    p = PARAMETROS[periodo]
    assert energia_eur_kwh(OMIE_OCTUBRE, p) == D(energia)
    assert coste_sin_impuestos_eur_kwh(OMIE_OCTUBRE, p) == D(coste)
    assert precio_final_eur_kwh(OMIE_OCTUBRE, p) == D(final)


def test_tev_octubre_con_consumos_de_la_factura() -> None:
    # TEV = Σ Consumo_p × (energía_p + peaje_p + cargo_p), sin impuestos.
    tev = sum(
        CONSUMO_OCTUBRE_KWH[per] * coste_sin_impuestos_eur_kwh(OMIE_OCTUBRE, p)
        for per, p in PARAMETROS.items()
    )
    assert tev == D("326.530047449424")


def test_impuestos_como_coste_marginal() -> None:
    p = PARAMETROS["P2"]
    coste = coste_sin_impuestos_eur_kwh(OMIE_OCTUBRE, p)
    assert precio_final_eur_kwh(OMIE_OCTUBRE, p) == coste * D("1.0511269632") * D("1.21")


FESTIVOS = {date(2026, 10, 12), date(2026, 12, 8), date(2026, 1, 6)}


@pytest.mark.parametrize(
    ("instante_utc", "esperado"),
    [
        # Jueves 01/10/2026, CEST (UTC+2).
        (datetime(2026, 10, 1, 5, 59, tzinfo=UTC), "P3"),   # 07:59
        (datetime(2026, 10, 1, 6, 0, tzinfo=UTC), "P2"),    # 08:00
        (datetime(2026, 10, 1, 8, 0, tzinfo=UTC), "P1"),    # 10:00
        (datetime(2026, 10, 1, 11, 45, tzinfo=UTC), "P1"),  # 13:45
        (datetime(2026, 10, 1, 12, 0, tzinfo=UTC), "P2"),   # 14:00
        (datetime(2026, 10, 1, 16, 0, tzinfo=UTC), "P1"),   # 18:00
        (datetime(2026, 10, 1, 20, 0, tzinfo=UTC), "P2"),   # 22:00
        (datetime(2026, 10, 1, 21, 45, tzinfo=UTC), "P2"),  # 23:45
        (datetime(2026, 10, 1, 22, 0, tzinfo=UTC), "P3"),   # viernes 00:00
        # Sábado y domingo: valle todo el día.
        (datetime(2026, 10, 3, 9, 0, tzinfo=UTC), "P3"),
        (datetime(2026, 10, 4, 17, 0, tzinfo=UTC), "P3"),
        # Lunes 12/10/2026, festivo nacional.
        (datetime(2026, 10, 12, 9, 0, tzinfo=UTC), "P3"),
        # Viernes Santo (03/04/2026): no tiene fecha fija, no es valle.
        (datetime(2026, 4, 3, 8, 0, tzinfo=UTC), "P1"),
        # Martes 08/12/2026, CET (UTC+1).
        (datetime(2026, 12, 8, 10, 0, tzinfo=UTC), "P3"),
        (datetime(2026, 12, 9, 9, 0, tzinfo=UTC), "P1"),    # 10:00 CET
        (datetime(2026, 12, 9, 8, 59, tzinfo=UTC), "P2"),   # 09:59 CET
    ],
)
def test_periodo_2td(instante_utc: datetime, esperado: str) -> None:
    assert periodo_2td(instante_utc, FESTIVOS) == esperado


def test_periodo_2td_exige_zona() -> None:
    with pytest.raises(ValueError):
        periodo_2td(datetime(2026, 10, 1, 10, 0), FESTIVOS)
