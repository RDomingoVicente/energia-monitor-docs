"""Referencia en Python de la energía por tramo, la estimación y el coste."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from energia_monitor.energia import (
    TRAMO,
    Muestra,
    coste,
    energia_por_tramo,
    inicio_tramo,
    reparto_estimado,
    reparto_muestra,
)
from energia_monitor.tiempo import limites_dia_utc

T = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


def cada_minuto(desde: datetime, hasta: datetime, evse: D = D(4400)) -> list[Muestra]:
    muestras = [Muestra(desde, None, evse)]
    t = desde + timedelta(seconds=60)
    while t <= hasta:
        muestras.append(Muestra(t, 60, evse, D(300), evse + 300))
        t += timedelta(seconds=60)
    return muestras


def test_inicio_tramo() -> None:
    assert inicio_tramo(T + timedelta(minutes=14, seconds=59)) == T
    assert inicio_tramo(T + timedelta(minutes=15)) == T + timedelta(minutes=15)


def test_muestra_que_cruza_el_cuarto_de_hora_se_reparte() -> None:
    m = Muestra(T + timedelta(seconds=40), 60, D(3600))
    assert reparto_muestra(m) == [(T - timedelta(minutes=15), D(20)), (T, D(40))]
    e = energia_por_tramo([m])
    assert e[T - timedelta(minutes=15)].coche_wh == D(20)
    assert e[T].coche_wh == D(40)


def test_muestra_en_el_limite_va_entera_al_tramo_anterior() -> None:
    assert reparto_muestra(Muestra(T, 60, D(1))) == [(T - timedelta(minutes=15), D(60))]


def test_muestra_tras_un_hueco_no_cuenta() -> None:
    assert reparto_muestra(Muestra(T, None, D(4400))) == []


def test_tramo_completo_e_incompleto() -> None:
    e = energia_por_tramo(cada_minuto(T, T + timedelta(minutes=15)))
    assert e[T].cubierto_s == 900 and not e[T].incompleto
    assert e[T].coche_wh == D(1100)  # 4400 W × 0,25 h
    assert e[T].total_wh == D(1175)
    # Hueco de 5 min: la muestra de después no cuenta y el tramo queda incompleto.
    muestras = cada_minuto(T, T + timedelta(minutes=5)) + cada_minuto(T + timedelta(minutes=10),
                                                                      T + timedelta(minutes=15))
    e = energia_por_tramo(muestras)
    assert e[T].cubierto_s == 600 and e[T].incompleto


def test_dia_del_cambio_de_hora_tiene_100_tramos() -> None:
    inicio, fin = limites_dia_utc(datetime(2026, 10, 25).date())
    e = energia_por_tramo(cada_minuto(inicio, fin, D(1000)))
    assert len(e) == 100
    assert all(not t.incompleto for t in e.values())
    assert sum(t.coche_wh for t in e.values()) == D(25_000)  # 1 kW × 25 h


def test_reparto_estimado_a_potencia_constante() -> None:
    inicio = T + timedelta(minutes=5)
    r = reparto_estimado(inicio, inicio + timedelta(hours=10), D(9000), D(4500))  # 2 h de carga
    assert min(r) == T and max(r) == T + timedelta(hours=2)
    assert r[T] == D(750)  # 10 min a 4,5 kW
    assert r[T + timedelta(hours=2)] == D(375)  # los 5 min finales
    assert sum(r.values()) == D(9000)


def test_reparto_estimado_uniforme_si_no_cabe_en_la_sesion() -> None:
    r = reparto_estimado(T, T + timedelta(hours=1), D(9000), D(4500))
    assert len(r) == 4 and all(v == D(2250) for v in r.values())


def test_reparto_estimado_vacio() -> None:
    assert reparto_estimado(T, T + timedelta(hours=1), D(0), D(4500)) == {}
    assert reparto_estimado(T, T, D(100), D(4500)) == {}


def test_coste_en_decimal() -> None:
    assert coste(D(1100), D("0.96"), D("0.123456")) == D("0.13036953600")


def test_casa_desconocida_si_alguna_muestra_del_tramo_carga() -> None:
    t0 = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)
    muestras = [Muestra(t0 + timedelta(minutes=i), None if i == 0 else 60,
                        D(0) if i <= 20 else D(4400), D(350) if i <= 20 else D(0),
                        D(350) if i <= 20 else D(4182)) for i in range(32)]
    tramos = energia_por_tramo(muestras)
    assert tramos[t0].casa_wh == D("87.5")
    assert tramos[t0 + TRAMO].casa_wh is None            # carga desde las 20:20
    assert tramos[t0 + 2 * TRAMO].casa_wh is None        # solo la muestra de las 20:31, cargando
    assert tramos[t0 + TRAMO].total_wh > 0
