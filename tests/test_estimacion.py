"""Estimación de SSAA: perfil de 14 días por franja y previsión 1871 escalada."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from energia_monitor import estimacion
from energia_monitor.tiempo import MADRID, dias, limites_dia_utc

QH = timedelta(minutes=15)
H = timedelta(hours=1)
FESTIVOS = {date(2026, 12, 8)}


def horas(dia: date) -> list[datetime]:
    inicio, fin = limites_dia_utc(dia)
    salida, t = [], inicio
    while t < fin:
        salida.append(t)
        t += H
    return salida


def real_sintetico(desde: date, hasta: date) -> dict[datetime, D]:
    """Laborables: 10 + hora local; fines de semana y festivos: 50."""
    real = {}
    for dia in dias(desde, hasta):
        for h in horas(dia):
            local = h.astimezone(MADRID)
            real[h] = D(10 + local.hour) if estimacion.es_laborable(dia, FESTIVOS) else D(50)
    return real


def cuartos(dia: date) -> list[tuple[datetime, datetime]]:
    inicio, fin = limites_dia_utc(dia)
    salida, t = [], inicio
    while t < fin:
        salida.append((t, t + QH))
        t += QH
    return salida


def test_laborables_y_festivos() -> None:
    assert estimacion.es_laborable(date(2026, 12, 7), FESTIVOS)        # lunes
    assert not estimacion.es_laborable(date(2026, 12, 8), FESTIVOS)    # festivo nacional
    assert not estimacion.es_laborable(date(2026, 12, 12), FESTIVOS)   # sábado


def test_dias_completos_descarta_incompletos_y_admite_25_horas() -> None:
    real = real_sintetico(date(2026, 10, 24), date(2026, 10, 26))
    del real[horas(date(2026, 10, 26))[5]]
    completos = estimacion.dias_completos(real)
    assert set(completos) == {date(2026, 10, 24), date(2026, 10, 25)}
    # 25 h: la hora local 2 aparece dos veces.
    assert len(completos[date(2026, 10, 25)][2]) == 2


def test_perfil_por_tipo_de_dia() -> None:
    dias_reales = estimacion.dias_completos(real_sintetico(date(2026, 11, 1), date(2026, 12, 10)))
    lunes = estimacion.perfil(dias_reales, date(2026, 12, 14), FESTIVOS)
    sabado = estimacion.perfil(dias_reales, date(2026, 12, 12), FESTIVOS)
    festivo = estimacion.perfil(dias_reales, date(2026, 12, 8), FESTIVOS)
    assert lunes == {h: D(10 + h) for h in range(24)}
    assert sabado == {h: D(50) for h in range(24)}
    assert festivo == sabado


def test_perfil_solo_usa_los_14_ultimos_dias() -> None:
    real = real_sintetico(date(2026, 11, 1), date(2026, 11, 30))
    # Un valor anómalo antes de la ventana no cuenta; dentro sí.
    real[horas(date(2026, 11, 16))[13]] = D(1000)   # lunes 16, fuera (ventana: 17-30)
    dias_reales = estimacion.dias_completos(real)
    assert estimacion.perfil(dias_reales, date(2026, 12, 1), FESTIVOS)[13] == D(23)
    real[horas(date(2026, 11, 30))[13]] = D(23 + 10 * 977)  # lunes 30, dentro: 10 laborables
    dias_reales = estimacion.dias_completos(real)
    assert estimacion.perfil(dias_reales, date(2026, 12, 1), FESTIVOS)[13] == D(23) + D(977)


def test_perfil_sin_dias_del_mismo_tipo_usa_todos() -> None:
    dias_reales = estimacion.dias_completos(real_sintetico(date(2026, 11, 9), date(2026, 11, 13)))  # lun-vie
    assert estimacion.perfil(dias_reales, date(2026, 11, 14), FESTIVOS)[0] == D(10)
    assert estimacion.perfil({}, date(2026, 11, 14), FESTIVOS) == {}


def test_factor_prevision() -> None:
    real = real_sintetico(date(2026, 11, 1), date(2026, 11, 30))
    prevision = {h: v / 2 for h, v in real.items()}
    assert estimacion.factor_prevision(real, prevision) == D(2)
    # Menos de 7 días con ambos valores: sin factor.
    pocos = {h: v for h, v in prevision.items() if h.astimezone(MADRID).date() >= date(2026, 11, 25)}
    assert estimacion.factor_prevision(real, pocos) is None
    assert estimacion.factor_prevision(real, {h: -v for h, v in prevision.items()}) is None
    assert estimacion.factor_prevision(real, {}) is None


def test_calcular_prefiere_la_prevision_verificada() -> None:
    real = real_sintetico(date(2026, 11, 1), date(2026, 12, 10))
    prevision = {h: v / 2 for h, v in real.items()}
    objetivo = date(2026, 12, 14)
    for h in horas(objetivo)[:12]:                    # previsión solo de 00:00 a 11:59
        prevision[h] = D("7.33333")
    tramos = cuartos(objetivo)

    con = estimacion.calcular(tramos, real, prevision, FESTIVOS, usar_prevision=True)
    assert len(con) == 96
    assert {e.metodo for e in con[:48]} == {"prevision_ree"}
    assert con[0].valor == D("14.6667")                # 2 × 7,33333, a 4 decimales
    assert {e.metodo for e in con[48:]} == {"perfil_14d"}
    assert con[48].valor == D(22)                      # 12:00, laborable

    sin = estimacion.calcular(tramos, real, prevision, FESTIVOS, usar_prevision=False)
    assert {e.metodo for e in sin} == {"perfil_14d"}
    assert [e.valor for e in sin[:4]] == [D(10)] * 4


def test_calcular_sin_datos_no_estima() -> None:
    assert estimacion.calcular(cuartos(date(2026, 12, 14)), {}, {}, FESTIVOS, usar_prevision=True) == []


def test_tramo_cubierto() -> None:
    inicio = datetime(2026, 11, 10, 10, tzinfo=UTC)
    con_real = {inicio + i * QH for i in range(4)}
    assert estimacion._cubierto(inicio, inicio + QH, con_real)
    assert estimacion._cubierto(inicio, inicio + H, con_real)
    assert not estimacion._cubierto(inicio, inicio + H, con_real - {inicio + 3 * QH})
