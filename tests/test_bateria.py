"""Referencia en Python del nivel de batería y la previsión, con valores a mano.

Los mismos escenarios se comparan con el SQL en integration/test_bateria_db.py.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D

import pytest
from escenarios_bateria import ESCENARIOS, MIN, T0, aleatorio, con_intervalos, t

from energia_monitor import bateria
from energia_monitor.bateria import Excepcion, Parametro, Parametros, percentil_cont

CAP = D("91.3")


def kwh(pct: str | int) -> D:
    return D(pct) / 100 * CAP


def cerca(a: D | None, b: D | None, tol: str = "1e-9") -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= D(tol)


def calcular(nombre: str) -> tuple[bateria.Entradas, datetime, bateria.Resultado]:
    e, ahora = ESCENARIOS[nombre]()
    return e, ahora, bateria.recalcular(e, ahora)


def ultimo_nivel(r: bateria.Resultado) -> D | None:
    return r.eventos[-1].nivel_kwh


# --- Nivel -------------------------------------------------------------------------------------


def test_sin_lectura_no_hay_nivel() -> None:
    e, ahora, r = calcular("sin_lectura")
    assert [ev.tipo for ev in r.eventos] == ["sesion"]
    assert r.eventos[0].nivel_kwh is None
    assert bateria.estado(e, r, ahora).nivel_kwh is None


def test_sesion_suma_energia_por_factor() -> None:
    _, _, r = calcular("lectura_y_sesion")
    assert cerca(ultimo_nivel(r), kwh(50) + D("10") * D("0.86"))


def test_nivel_acotado_al_tope() -> None:
    _, _, r = calcular("acotado_al_tope")
    assert ultimo_nivel(r) == CAP


def test_trayecto_estimado_y_reconciliado() -> None:
    _, _, r = calcular("trayecto_reconciliado")
    (tr,) = r.trayectos
    assert tr.consumo_estimado_kwh == 25
    assert tr.origen_consumo == "reconciliado"
    assert cerca(tr.consumo_kwh, kwh(80) - kwh(40))
    lectura_final = r.eventos[-1]
    assert cerca(lectura_final.estimado_previo_kwh, kwh(80) - 25)  # lo previsto
    trayecto = next(ev for ev in r.eventos if ev.tipo == "trayecto")
    assert cerca(trayecto.nivel_estimado_kwh, kwh(80) - 25)
    assert cerca(trayecto.nivel_kwh, kwh(40))  # con el consumo real
    (i,) = r.intervalos
    assert i.estado == "reconciliado" and cerca(i.consumo_real_kwh, kwh(40))


def test_reconciliacion_reparte_en_proporcion_al_estimado() -> None:
    _, _, r = calcular("varios_trayectos")
    a, b = r.trayectos
    assert (a.origen_consumo, b.viaje_largo) == ("reconciliado", True)
    assert a.consumo_estimado_kwh == 25 and cerca(b.consumo_estimado_kwh, kwh(20))
    real = kwh(90) - kwh("46.2")
    assert cerca(a.consumo_kwh, real * 25 / (25 + kwh(20)))
    assert cerca(b.consumo_kwh, real * kwh(20) / (25 + kwh(20)))
    assert cerca(a.consumo_kwh + b.consumo_kwh, real)  # type: ignore[operator]


def test_desconexion_corta_consumo_cero() -> None:
    _, _, r = calcular("desconexion_corta")
    (tr,) = r.trayectos
    assert tr.corto and tr.consumo_kwh == 0 and tr.origen_consumo == "cero"
    (i,) = r.intervalos
    assert (i.estado, i.trayectos, i.trayectos_cortos, i.factor_observado) == ("sin_trayectos", 0, 1, None)


def test_intervalo_incoherente_no_se_aplica() -> None:
    _, _, r = calcular("intervalo_incoherente")
    (i,) = r.intervalos
    assert i.estado == "incoherente" and i.consumo_real_kwh < 0
    (tr,) = r.trayectos
    assert tr.origen_consumo == "inicial" and tr.consumo_kwh == 25


def test_lectura_retroactiva_recalcula_lo_posterior() -> None:
    e, ahora, r = calcular("lectura_retroactiva")
    assert [ev.tipo for ev in r.eventos] == ["lectura", "sesion", "trayecto", "lectura"]
    (i,) = r.intervalos
    assert cerca(i.consumo_real_kwh, kwh(50) + D(15) * D("0.86") - kwh(40))
    # Mismo resultado con las lecturas en el orden de sus instantes.
    e.lecturas.sort(key=lambda lec: lec.instante)
    r2 = bateria.recalcular(e, ahora)
    assert [ev.nivel_kwh for ev in r2.eventos] == [ev.nivel_kwh for ev in r.eventos]


def test_sesion_abierta_suma_lo_entregado() -> None:
    e, ahora, r = calcular("sesion_abierta")
    est = bateria.estado(e, r, ahora)
    pendiente = D(4400) * 3600 / 3600 / 1000 * D("0.86")  # 60 muestras de 60 s
    assert cerca(est.energia_pendiente_kwh, pendiente)
    assert cerca(est.nivel_kwh, kwh(50) + pendiente)
    assert est.coche == "cargando"


def test_lectura_durante_un_trayecto_lo_parte_por_tiempo() -> None:
    _, _, r = calcular("lectura_durante_trayecto")
    partes = [ev for ev in r.eventos if ev.tipo == "trayecto"]
    assert [p.peso for p in partes] == [D("0.5"), D("0.5")]
    assert [p.instante for p in partes] == [t(10), t(12)]
    i1, i2 = r.intervalos
    assert cerca(i1.consumo_real_kwh, kwh(80) - kwh(60)) and cerca(i2.consumo_real_kwh, kwh(60) - kwh(45))
    (tr,) = r.trayectos
    assert tr.origen_consumo == "reconciliado" and cerca(tr.consumo_kwh, kwh(80) - kwh(45))


def test_lectura_con_el_coche_fuera() -> None:
    e, ahora, r = calcular("lectura_durante_trayecto_abierto")
    (parte,) = [ev for ev in r.eventos if ev.tipo == "trayecto"]
    assert parte.peso == 1 and parte.instante == t(10)  # todo lo estimado, antes de la lectura
    (tr,) = r.trayectos
    assert tr.llegada is None and tr.consumo_kwh is None and tr.origen_consumo == "inicial"
    est = bateria.estado(e, r, ahora)
    assert est.coche == "fuera" and est.consumo_en_curso_kwh == 0  # ya descontado en la lectura


def test_lectura_durante_una_carga_la_parte_por_energia_del_modulador() -> None:
    _, _, r = calcular("lectura_durante_carga")
    partes = [ev for ev in r.eventos if ev.tipo == "sesion"]
    assert [p.peso for p in partes] == [D(1) / 3, D(2) / 3]
    i1, i2 = r.intervalos[0], r.intervalos[1]
    assert cerca(i1.sesiones_kwh, D(12) * D("0.86") / 3)
    assert cerca(i2.sesiones_kwh, D(12) * D("0.86") * 2 / 3)


def test_carga_completa_fija_el_tope() -> None:
    _, _, r = calcular("carga_completa")
    (s,) = r.sesiones
    assert s.cierre == "carga_completa"
    cc = next(ev for ev in r.eventos if ev.tipo == "carga_completa")
    assert cc.absoluto_kwh == CAP and cc.nivel_kwh == CAP
    assert [ev.tipo for ev in r.eventos] == ["lectura", "sesion", "carga_completa"]


def test_clasificacion_del_fin_de_sesion() -> None:
    _, _, r = calcular("fin_de_franja_y_dudosa")
    assert [s.cierre for s in r.sesiones] == ["fin_franja", "dudoso", "desconexion", "pendiente"]


def test_regla_de_carga_completa_desactivable() -> None:
    e, ahora = ESCENARIOS["carga_completa"]()
    filas = [Parametro(c, date(2026, 1, 1), None, v) for c, v in bateria.PARAMETROS_INICIALES.items()
             if c != "detectar_carga_completa"]
    e.parametros = Parametros([*filas, Parametro("detectar_carga_completa", date(2026, 1, 1), None, D(0))])
    r = bateria.recalcular(e, ahora)
    assert [s.cierre for s in r.sesiones] == ["sin_regla"]
    assert "carga_completa" not in [ev.tipo for ev in r.eventos]


# --- Calibración y estadística ---------------------------------------------------------------


def test_calibracion_observa_el_factor() -> None:
    e, ahora, r = calcular("calibracion")
    (i,) = r.intervalos
    assert not i.huecos and i.estado == "sin_trayectos"
    assert cerca(i.factor_observado, (kwh("48.26") - kwh(20)) / 30)
    factor, n = bateria.factor_sugerido(e, r.intervalos, ahora)
    assert n == 1 and factor == i.factor_observado


def test_calibracion_con_factor_mayor_que_el_vigente() -> None:
    # Factor real 0,9 > 0,86: el nivel sube más de lo esperado; no es incoherencia.
    e, ahora = ESCENARIOS["calibracion"]()
    e.lecturas[-1] = bateria.Lectura(e.lecturas[-1].id, e.lecturas[-1].instante, (kwh(20) + 27) / CAP * 100)
    r = bateria.recalcular(e, ahora)
    (i,) = r.intervalos
    assert i.estado == "sin_trayectos" and i.consumo_real_kwh < 0
    assert cerca(i.factor_observado, D("0.9"))


def test_sin_carga_el_nivel_no_puede_subir() -> None:
    e, ahora = ESCENARIOS["calibracion"]()
    e.sesiones = []
    (i,) = bateria.recalcular(e, ahora).intervalos
    assert i.estado == "incoherente"


def test_calibracion_descarta_intervalos_con_huecos() -> None:
    # 40 min sin muestras en mitad de la carga: la muestra siguiente queda sin intervalo.
    e, ahora = ESCENARIOS["calibracion"]()
    e.muestras = [m for m in e.muestras if not (t(3) < m.instante < t(3) + 40 * MIN)]
    con_intervalos(e)
    r = bateria.recalcular(e, ahora)
    (i,) = r.intervalos
    assert i.huecos and i.factor_observado is None


def test_estimacion_con_la_mediana_de_los_reconciliados() -> None:
    _, _, r = calcular("estimacion_por_mediana")
    reconciliados = [tr for tr in r.trayectos if tr.origen_consumo == "reconciliado"]
    assert [round(tr.consumo_kwh, 9) for tr in reconciliados] == [10, 20, 30]  # type: ignore[arg-type]
    ultimo = r.trayectos[-1]
    assert ultimo.origen_consumo == "estimado" and cerca(ultimo.consumo_estimado_kwh, D(20))
    # El segundo solo veía el primero, ya reconciliado antes de su salida.
    assert cerca(r.trayectos[1].consumo_estimado_kwh, D(10))


@pytest.mark.parametrize("valores", [[D(5)], [D(1), D(2)], [D("1.1"), D("2.2"), D("3.3")],
                                     [D(x) for x in (7, 1, 9, 3, 3, 12, 4)]])
@pytest.mark.parametrize("p", ["0.25", "0.5", "0.75", "0.9"])
def test_percentil_como_percentile_cont(valores: list[D], p: str) -> None:
    n = len(valores)
    if n == 1:
        assert percentil_cont(valores, D(p)) == valores[0]
        return
    # statistics.quantiles(method="inclusive") da los cortes k/100 con la misma interpolación lineal.
    cortes = statistics.quantiles([float(v) for v in valores], n=100, method="inclusive")
    assert abs(float(percentil_cont(valores, D(p))) - cortes[int(D(p) * 100) - 1]) < 1e-9  # type: ignore[arg-type]


def test_percentil_y_mediana_a_mano() -> None:
    assert percentil_cont([D("1.1"), D("2.2"), D("3.3")], D("0.75")) == D("2.75")
    assert bateria.mediana([D(10), D(30)]) == 20
    assert bateria.mediana([]) is None


def test_consumo_para_planificar_por_dia_de_uso() -> None:
    tr = [
        # Dos trayectos el 10/11 (8 + 12 = 20), uno el 11/11 (30), uno el 12/11 de viaje largo y uno el
        # 13/11 sin reconciliar: cuentan los días 10 y 11.
        bateria.Trayecto("c", datetime(2026, 11, 10, 7, tzinfo=UTC), datetime(2026, 11, 10, 8, tzinfo=UTC),
                         False, False, D(9), D(8), "reconciliado"),
        bateria.Trayecto("c", datetime(2026, 11, 10, 15, tzinfo=UTC), datetime(2026, 11, 10, 16, tzinfo=UTC),
                         False, False, D(9), D(12), "reconciliado"),
        bateria.Trayecto("c", datetime(2026, 11, 11, 7, tzinfo=UTC), datetime(2026, 11, 11, 9, tzinfo=UTC),
                         False, False, D(9), D(30), "reconciliado"),
        bateria.Trayecto("c", datetime(2026, 11, 12, 7, tzinfo=UTC), datetime(2026, 11, 12, 19, tzinfo=UTC),
                         False, True, D(60), D(55), "reconciliado"),
        bateria.Trayecto("c", datetime(2026, 11, 13, 7, tzinfo=UTC), datetime(2026, 11, 13, 9, tzinfo=UTC),
                         False, False, D(25), D(25), "inicial"),
        bateria.Trayecto("c", datetime(2026, 11, 13, 12, tzinfo=UTC), datetime(2026, 11, 13, 12, 5, tzinfo=UTC),
                         True, False, D(0), D(0), "cero"),
    ]
    dias = {c.fecha: c for c in bateria.consumo_por_dia(tr)}
    assert dias[date(2026, 11, 10)].consumo_kwh == 20 and dias[date(2026, 11, 10)].cuenta
    assert not dias[date(2026, 11, 12)].cuenta and not dias[date(2026, 11, 13)].cuenta
    e = bateria.Entradas()
    assert bateria.consumo_plan(e, tr, T0) == (D(20) + D(10) * D("0.75"), 2, "historia")
    # Fuera de la ventana de 28 días: sin historia, el consumo inicial.
    assert bateria.consumo_plan(e, tr, T0 + timedelta(days=40)) == (D(25), 0, "inicial")


# --- Previsión ---------------------------------------------------------------------------------


def test_salidas_con_horario_y_excepciones() -> None:
    e, ahora = ESCENARIOS["prevision"]()
    s = bateria.salidas(e, ahora, 7)
    # Lunes 16 sin coche; martes 17 a las 6:30; miércoles y jueves a las 9; viernes a las 7; lunes 23 a las 9.
    assert [(x.fecha.day, x.salida.astimezone(bateria.MADRID).time(), x.tipo) for x in s] == [
        (17, time(6, 30), "otra_hora"), (18, time(9), "horario"), (19, time(9), "horario"),
        (20, time(7), "horario"), (23, time(9), "horario")]


def test_energia_necesaria_reserva_mas_consumo() -> None:
    e, ahora, r = calcular("prevision")
    est = bateria.estado(e, r, ahora)
    assert est.proxima_salida == datetime(2026, 11, 17, 5, 30, tzinfo=UTC)
    assert cerca(est.energia_necesaria_kwh, kwh(25) + 25 - kwh(50))


def test_energia_necesaria_nivel_objetivo_y_tope() -> None:
    e, ahora = ESCENARIOS["prevision"]()
    lunes = Excepcion("sin_coche", date(2026, 11, 16))
    e.excepciones = [lunes, Excepcion("nivel_objetivo", date(2026, 11, 17), time(6, 30), pct_objetivo=D(80))]
    r = bateria.recalcular(e, ahora)
    assert cerca(bateria.estado(e, r, ahora).energia_necesaria_kwh, kwh(80) - kwh(50))
    e.excepciones = [lunes, Excepcion("viaje_largo", date(2026, 11, 17), time(6, 30), consumo_km=D(500))]
    # 25 % + 115 kWh supera el tope: lo que falta hasta el 100 %.
    assert cerca(bateria.estado(e, r, ahora).energia_necesaria_kwh, CAP - kwh(50))


def test_coche_fuera_resta_el_trayecto_en_curso() -> None:
    e, ahora, r = calcular("coche_fuera")
    est = bateria.estado(e, r, ahora)
    assert (est.coche, est.consumo_en_curso_kwh) == ("fuera", 25)
    assert cerca(est.nivel_plan_kwh, kwh(50) - 25)
    assert est.proxima_salida == datetime(2026, 11, 16, 19, tzinfo=UTC)


@pytest.mark.parametrize(("dia", "hora", "esperado"), [
    (date(2026, 3, 29), time(2, 30), datetime(2026, 3, 29, 1, 30, tzinfo=UTC)),    # no existe
    (date(2026, 10, 25), time(2, 30), datetime(2026, 10, 25, 1, 30, tzinfo=UTC)),  # ambigua: la segunda
    (date(2026, 10, 25), time(9, 0), datetime(2026, 10, 25, 8, 0, tzinfo=UTC)),
    (date(2026, 7, 1), time(9, 0), datetime(2026, 7, 1, 7, 0, tzinfo=UTC)),
])
def test_hora_local_como_postgresql(dia: date, hora: time, esperado: datetime) -> None:
    assert bateria.local_a_utc(dia, hora) == esperado


# --- Coherencia general ------------------------------------------------------------------------


@pytest.mark.parametrize("semilla", range(40))
def test_historias_aleatorias_coherentes(semilla: int) -> None:
    e, ahora = aleatorio(semilla)
    r = bateria.recalcular(e, ahora)
    cap = CAP
    for ev in r.eventos:
        if ev.nivel_kwh is not None:
            assert 0 <= ev.nivel_kwh <= cap
    # Las partes de cada sesión suman su energía.
    for s in r.sesiones:
        partes = [ev.contador_kwh for ev in r.eventos if ev.tipo == "sesion" and ev.sesion_id == s.id]
        assert cerca(sum(partes, D(0)), s.energia_wh / 1000)  # type: ignore[arg-type]
    # Intervalos reconciliados: sus partes de trayecto no cortos suman el consumo real.
    absolutos = [ev for ev in r.eventos if ev.tipo in ("lectura", "carga_completa")]
    cortos = {tr.salida for tr in r.trayectos if tr.corto}
    for a1, a2, i in zip(absolutos, absolutos[1:], r.intervalos):
        assert (a1.instante, a2.instante) == (i.desde, i.hasta)
        if i.estado == "reconciliado":
            partes = [ev for ev in r.eventos if ev.tipo == "trayecto" and a1.n < ev.n < a2.n
                      and ev.trayecto_salida not in cortos]
            total = sum((-ev.delta_kwh for ev in partes), D(0))  # type: ignore[operator]
            assert cerca(total, i.consumo_real_kwh, "1e-6")
    assert [ev.n for ev in r.eventos] == list(range(1, len(r.eventos) + 1))


def test_hueco_solo_tras_muestras_sin_intervalo() -> None:
    m = [bateria.MuestraEstado(t(h), 60, D(0), 4) for h in (0.5, 1)]
    hueco = [bateria.MuestraEstado(t(1), None, D(0), 4)]
    umbral = D(1800)
    assert not bateria.hay_hueco(m, t(0.4), t(1.2), umbral)
    assert not bateria.hay_hueco(m, t(0), t(1.2), umbral)  # 30 min justos hasta la primera: no
    assert bateria.hay_hueco(m, t(0) - timedelta(seconds=1), t(1.2), umbral)  # 30 min y 1 s: sí
    assert bateria.hay_hueco(m, t(0.5), t(2), umbral)  # 60 min desde la última
    # Entre 0,5 y 1 h hay 30 min: al límite, no es hueco; con 45 min sí, si la muestra no tiene intervalo.
    assert not bateria.hay_hueco([m[0], *hueco], t(0.4), t(1.1), umbral)
    lejos = [bateria.MuestraEstado(t(0.25), 60, D(0), 4), bateria.MuestraEstado(t(1), None, D(0), 4)]
    assert bateria.hay_hueco(lejos, t(0.2), t(1.1), umbral)
    assert not bateria.hay_hueco([lejos[0], bateria.MuestraEstado(t(1), 60, D(0), 4)], t(0.2), t(1.1), umbral)
    assert bateria.hay_hueco([], t(0), t(1), umbral)
