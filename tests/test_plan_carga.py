"""Planificador de carga (paso 3a): algoritmo, franjas, precios y disponibilidad, sin base de datos.

Cuartos de 1 kWh salvo que se diga otra cosa: 4 kW × 0,25 h con factor_bateria 1.
"""

from __future__ import annotations

import itertools
import os
import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from energia_monitor.plan_carga import disponibilidad, franjas, precios
from energia_monitor.plan_carga.algoritmo import planificar
from energia_monitor.plan_carga.modelo import (
    CUARTO,
    Cuarto,
    CuartoPlan,
    Entrada,
    FranjaEnCurso,
    Ocupado,
    Parametros,
    Plan,
    Salida,
)
from energia_monitor.tiempo import MADRID

# Miércoles 18/11/2026, 20:00 en Madrid.
T0 = datetime(2026, 11, 18, 20, tzinfo=MADRID).astimezone(UTC)


def local(dia: int, hora: int, minuto: int = 0, mes: int = 11) -> datetime:
    return datetime(2026, mes, dia, hora, minuto, tzinfo=MADRID).astimezone(UTC)


def params(**cambios: object) -> Parametros:
    base = Parametros(capacidad_kwh=D(100), tope_kwh=D(100), factor_bateria=D(1), margen_carga_pct=D(0),
                      margen_salida_min=0, carga_oportunista=False, nivel_oportunista_kwh=D(100),
                      umbral_disponibilidad_pct=D(80), franja_min_min=15, max_tareas=9)
    return replace(base, **cambios)  # type: ignore[arg-type]


def entrada(precios_: list[float | None], *, salidas: list[tuple[int, float, float]] | None = None,
            nivel: float = 50, ahora: datetime = T0, conectado: bool = True, prudente: bool = False,
            umbral: float | None = 0.10, provisionales: set[int] = frozenset(),  # type: ignore[assignment]
            probabilidad: dict[int, float] | None = None, potencia: float = 4, **otros: object) -> Entrada:
    """Un cuarto por precio desde ahora; salidas: (índice del cuarto de la salida, objetivo, consumo)."""
    cuartos = tuple(Cuarto(ahora + i * CUARTO, None if p is None else D(str(p)), i in provisionales,
                           None if probabilidad is None else D(str(probabilidad.get(i, 0))), D(str(potencia)))
                    for i, p in enumerate(precios_))
    sal = tuple(Salida(ahora + i * CUARTO, "horario", D(str(o)), D(str(c))) for i, o, c in (salidas or []))
    fin = sal[-1].instante if sal else ahora + len(precios_) * CUARTO
    return Entrada(ahora=ahora, parametros=otros.pop("parametros", params()), nivel_kwh=D(str(nivel)),  # type: ignore[arg-type]
                   conectado=conectado, prudente=prudente, umbral=None if umbral is None else D(str(umbral)),
                   cuartos=cuartos, salidas=sal, horizonte_fin=fin, **otros)  # type: ignore[arg-type]


def indices(plan: Plan, nivel: str | None = None, ahora: datetime = T0) -> list[int]:
    return [int((c.inicio - ahora) / CUARTO) for c in plan.cuartos if nivel is None or c.nivel == nivel]


def coste(plan: Plan, nivel: str | None = None) -> D:
    return sum((c.kwh * c.precio for c in plan.cuartos if c.precio is not None and (nivel is None or c.nivel == nivel)),
               D(0))


# --- Nivel 1: necesaria ---------------------------------------------------------------------


def test_necesaria_en_los_cuartos_mas_baratos() -> None:
    plan = planificar(entrada([0.20, 0.05, 0.30, 0.04, 0.06, 0.25, 0.07, 0.30], salidas=[(8, 53, 10)]))
    assert indices(plan) == [1, 3, 4]
    s = plan.salidas[0]
    assert (s.necesaria_kwh, s.nivel_salida_kwh, s.cubierta, s.salto_umbral) == (D(3), D(53), True, False)


def test_necesaria_se_salta_el_umbral() -> None:
    plan = planificar(entrada([0.30, 0.20, 0.25, 0.40], salidas=[(4, 52, 10)], umbral=0.10))
    assert indices(plan, "necesaria") == [1, 2]
    assert plan.salidas[0].cubierta and plan.salidas[0].salto_umbral
    assert all(c.sobre_umbral for c in plan.cuartos)


def test_salida_no_cubierta_da_aviso() -> None:
    plan = planificar(entrada([0.1, 0.1], salidas=[(2, 60, 10)]))
    assert indices(plan) == [0, 1] and not plan.salidas[0].cubierta
    assert any("no cubre la salida" in a for a in plan.avisos)


def test_margen_antes_de_salir() -> None:
    plan = planificar(entrada([0.3, 0.3, 0.3, 0.01, 0.01], salidas=[(5, 51, 5)],
                              parametros=params(margen_salida_min=30)))
    assert indices(plan) == [0]  # 3 y 4 terminan después de salida − 30 min


def test_acumula_la_salida_sin_otra_ventana() -> None:
    """Salida a las 8 cuartos y vuelta sin ventana antes de la segunda: su energía se carga antes de la primera."""
    precios_ = [0.10] * 8 + [0.01] * 8 + [0.02] * 4
    probabilidad = {i: 0 for i in range(8, 16)} | {i: 1 for i in range(16, 20)}
    plan = planificar(entrada(precios_, salidas=[(8, 52, 4), (20, 54, 4)], probabilidad=probabilidad))
    # 1.ª: 2 kWh; 2.ª: nivel 48 + cargado; necesita 54 → 6 antes de su salida: 4 en 16-19 y el resto antes de la 1.ª.
    assert [s.cubierta for s in plan.salidas] == [True, True]
    antes_de_la_primera = [i for i in indices(plan) if i < 8]
    assert len(antes_de_la_primera) == 4 and indices(plan) == [*antes_de_la_primera, 16, 17, 18, 19]


def test_probable_solo_si_la_segura_no_basta() -> None:
    probabilidad = dict.fromkeys(range(8), 0.9)
    # Desenchufado: no hay ventana segura; las probables bastan.
    plan = planificar(entrada([0.2, 0.1, 0.3, 0.1] * 2, salidas=[(8, 52, 5)], conectado=False,
                              probabilidad=probabilidad))
    assert indices(plan) == [1, 3] and {c.ventana for c in plan.cuartos} == {"probable"}
    # Por debajo del umbral de disponibilidad no cuentan.
    plan = planificar(entrada([0.1] * 8, salidas=[(8, 52, 5)], conectado=False,
                              probabilidad=dict.fromkeys(range(8), 0.7)))
    assert plan.cuartos == () and not plan.salidas[0].cubierta


def test_nunca_pasa_del_tope() -> None:
    plan = planificar(entrada([0.01] * 12, salidas=[(12, 99.5, 10)], nivel=97,
                              parametros=params(tope_kwh=D(99.5), carga_oportunista=True)))
    assert plan.salidas[0].nivel_salida_kwh == D("99.5")
    assert sum(c.kwh for c in plan.cuartos) == D("2.5")  # el último cuarto, recortado


# --- Niveles 2 y 3 ----------------------------------------------------------------------------


def test_margen_y_oportunista_solo_baratos_y_publicados() -> None:
    precios_ = [0.30, 0.20, 0.05, 0.04, 0.06, 0.03, 0.02, 0.15]
    plan = planificar(entrada(precios_, salidas=[(8, 54, 4)], umbral=0.10, provisionales={5, 6},
                              parametros=params(margen_carga_pct=D(25), carga_oportunista=True,
                                                nivel_oportunista_kwh=D(60))))
    # Necesaria 4: los cuatro más baratos (6, 5, 3, 2), aunque 5 y 6 sean provisionales.
    assert indices(plan, "necesaria") == [2, 3, 5, 6]
    # Margen 25 % de 4 = 1: el más barato publicado bajo el umbral (4); oportunista: nada más barato.
    assert indices(plan, "margen") == [4]
    assert indices(plan, "oportunista") == []
    for c in plan.cuartos:
        if c.nivel in ("margen", "oportunista"):
            assert not c.provisional and not c.sobre_umbral


def test_oportunista_hasta_su_nivel() -> None:
    plan = planificar(entrada([0.05] * 10 + [0.5] * 2, salidas=[(12, 40, 5)], nivel=50,
                              parametros=params(carga_oportunista=True, nivel_oportunista_kwh=D(56))))
    assert indices(plan, "oportunista") == list(range(6))
    assert plan.salidas[0].nivel_salida_kwh == D(56)
    plan = planificar(entrada([0.05] * 10, salidas=[(10, 40, 5)], nivel=50,
                              parametros=params(carga_oportunista=False)))
    assert plan.cuartos == ()


def test_sin_umbral_no_hay_margen_ni_oportunista() -> None:
    plan = planificar(entrada([0.01] * 6, salidas=[(6, 51, 5)], umbral=None,
                              parametros=params(margen_carga_pct=D(30), carga_oportunista=True)))
    assert indices(plan) == [0] and plan.cuartos[0].nivel == "necesaria"


# --- Modo prudente ----------------------------------------------------------------------------


def test_prudente_sin_enchufar_no_planifica() -> None:
    plan = planificar(entrada([0.1] * 8, salidas=[(8, 55, 5)], conectado=False, prudente=True,
                              probabilidad=dict.fromkeys(range(8), 1)))
    assert plan.cuartos == () and any("prudente" in a for a in plan.avisos)


def test_prudente_solo_la_ventana_segura_y_la_proxima_salida() -> None:
    probabilidad = {i: 1 for i in range(12, 20)}
    plan = planificar(entrada([0.2] * 8 + [0.01] * 4 + [0.01] * 8, salidas=[(8, 52, 4), (20, 60, 4)],
                              prudente=True, probabilidad=probabilidad,
                              parametros=params(carga_oportunista=True, margen_carga_pct=D(50))))
    assert all(i < 8 for i in indices(plan))  # nada tras la salida, aunque sea probable y barato
    assert plan.salidas[0].cubierta and not plan.salidas[1].cubierta
    assert not any("19/11" in a for a in plan.avisos)  # la segunda no se avisa: se planifica al volver


# --- Franjas ---------------------------------------------------------------------------------


def test_medianoche_partida() -> None:
    assert franjas.tareas(local(18, 23), local(19, 1)) == [franjas.Tarea(date(2026, 11, 18), 1380, 60),
                                                          franjas.Tarea(date(2026, 11, 19), 0, 60)]
    assert franjas.tareas(local(18, 22), local(19, 0)) == [franjas.Tarea(date(2026, 11, 18), 1320, 120)]
    assert franjas.tareas(local(19, 0), local(19, 3)) == [franjas.Tarea(date(2026, 11, 19), 0, 180)]
    ahora = local(18, 22)
    plan = planificar(entrada([0.2] * 4 + [0.01] * 8 + [0.3] * 4, salidas=[(16, 58, 4)], ahora=ahora))
    assert [(f.inicio, f.fin, f.tareas) for f in plan.franjas] == [(local(18, 23), local(19, 1), 2)]
    assert plan.tareas == 2


@pytest.mark.parametrize(("inicio", "fin", "esperadas"), [
    # 25/10/2026: de 03:00 CEST se vuelve a las 02:00 CET (25 h). Reloj local: 01:00–04:00 = 180 min.
    (datetime(2026, 10, 25, 1, tzinfo=MADRID), datetime(2026, 10, 25, 4, tzinfo=MADRID), [(0, 60, 180)]),
    # 28/03/2027: de 02:00 CET se salta a las 03:00 CEST (23 h).
    (datetime(2027, 3, 28, 1, tzinfo=MADRID), datetime(2027, 3, 28, 4, tzinfo=MADRID), [(0, 60, 180)]),
    (datetime(2027, 3, 27, 23, tzinfo=MADRID), datetime(2027, 3, 28, 4, tzinfo=MADRID), [(0, 1380, 60), (1, 0, 240)]),
])
def test_medianoche_y_cambio_de_hora(inicio: datetime, fin: datetime, esperadas: list[tuple[int, int, int]]) -> None:
    tareas = franjas.tareas(inicio.astimezone(UTC), fin.astimezone(UTC))
    assert [((t.fecha - inicio.date()).days, t.hora_min, t.duracion) for t in tareas] == esperadas


def test_franja_corta_se_alarga_o_se_mueve() -> None:
    # Necesita 1 kWh: el más barato está aislado; con franja mínima de 30 min sale una franja de 2 cuartos.
    plan = planificar(entrada([0.30, 0.10, 0.50, 0.11, 0.12, 0.40], salidas=[(6, 51, 5)],
                              parametros=params(franja_min_min=30)))
    assert all(f.fin - f.inicio >= timedelta(minutes=30) for f in plan.franjas)
    assert plan.salidas[0].cubierta
    assert indices(plan) == [3, 4]  # 0,11 + 0,12 < alargar el de 0,10 con 0,30 o 0,50


def test_franja_corta_de_margen_se_alarga_con_cuartos_de_su_nivel() -> None:
    # Necesaria 1 (el de 0,02, alargado con el de 0,06); margen 100 %: el de 0,03, corto, se alarga con el de 0,04
    # (barato y publicado). Sin ese vecino barato, se descartaría.
    plan = planificar(entrada([0.03, 0.04, 0.30, 0.02, 0.06, 0.40], salidas=[(6, 51, 5)], umbral=0.10,
                              parametros=params(franja_min_min=30, margen_carga_pct=D(100))))
    assert indices(plan, "margen") == [0, 1]
    assert indices(plan, "necesaria") == [3, 4]
    plan = planificar(entrada([0.03, 0.14, 0.30, 0.02, 0.06, 0.40], salidas=[(6, 51, 5)], umbral=0.10,
                              parametros=params(franja_min_min=30, margen_carga_pct=D(100))))
    assert indices(plan, "margen") == []


def test_franja_corta_oportunista_no_pasa_de_su_nivel() -> None:
    # Cabe un solo cuarto oportunista hasta 51: no se puede alargar sin pasar de nivel_oportunista → se descarta.
    plan = planificar(entrada([0.5, 0.01, 0.02, 0.5], salidas=[(4, 40, 5)], nivel=50,
                              parametros=params(franja_min_min=30, carga_oportunista=True,
                                                nivel_oportunista_kwh=D(51))))
    assert plan.cuartos == ()


def test_franja_corta_opcional_se_descarta() -> None:
    plan = planificar(entrada([0.5, 0.01, 0.5, 0.5], salidas=[(4, 40, 5)], nivel=50,
                              parametros=params(franja_min_min=30, carga_oportunista=True)))
    assert plan.cuartos == ()


def test_huecos_hasta_caber_en_las_tareas() -> None:
    precios_ = [0.01, 0.20, 0.01, 0.30, 0.01, 0.05, 0.01, 0.50]
    plan = planificar(entrada(precios_, salidas=[(8, 54, 5)], parametros=params(max_tareas=2)))
    assert plan.tareas == 2 and plan.salidas[0].cubierta
    # Se cierra el hueco más barato (0,05) y después el de 0,20; el de 0,30 no hace falta.
    assert [(f.inicio, f.fin) for f in plan.franjas] == [(T0, T0 + 3 * CUARTO), (T0 + 4 * CUARTO, T0 + 7 * CUARTO)]


def test_desempate_madrugada_y_ventana_segura() -> None:
    ahora = local(18, 22)
    plan = planificar(entrada([0.10] * 16, salidas=[(16, 52, 4)], ahora=ahora))
    assert all(c.inicio.astimezone(MADRID).hour < 8 for c in plan.cuartos)
    assert [c.inicio for c in plan.cuartos] == [local(19, 0), local(19, 0, 15)]


def test_determinista() -> None:
    e = entrada([0.1, 0.2, 0.1, 0.05, 0.05, 0.2, 0.1, 0.1], salidas=[(8, 53, 4)],
                parametros=params(carga_oportunista=True, margen_carga_pct=D(50), nivel_oportunista_kwh=D(56)))
    barajada = replace(e, cuartos=tuple(random.Random(3).sample(e.cuartos, len(e.cuartos))))
    assert planificar(e) == planificar(e) == planificar(barajada)


# --- Tareas ajenas y franja en curso ---------------------------------------------------------------


def test_sin_solapes_con_tareas_respetadas() -> None:
    ahora = local(18, 22)
    ajena = Ocupado(local(18, 23), local(18, 23, 59), "ajena", 5)
    e = entrada([0.01] * 8 + [0.05] * 8, salidas=[(16, 56, 4)], ahora=ahora, ocupados=(ajena,),
                respetar_ajenas=True)
    plan = planificar(e)
    assert not any(franjas.solapa(c.inicio, c.inicio + CUARTO, ajena.inicio, ajena.fin) for c in plan.cuartos)
    # Su energía (59 min a 4 kW ≈ 3,93 kWh) se descuenta: faltan 6, quedan ~2,07 → 3 cuartos.
    assert len(plan.cuartos) == 3 and plan.salidas[0].cubierta
    assert all(f.choca_con == () for f in plan.franjas)


def test_en_sombra_se_anotan_los_choques() -> None:
    ahora = local(18, 22)
    ajena = Ocupado(local(18, 23), local(18, 23, 59), "ajena", 5)
    plan = planificar(entrada([0.01] * 8 + [0.05] * 8, salidas=[(16, 56, 4)], ahora=ahora, ocupados=(ajena,),
                              respetar_ajenas=False))
    assert len(plan.cuartos) == 6  # sin descontar la ajena
    assert [f.choca_con for f in plan.franjas] == [(5,)]


def test_las_manuales_se_respetan_siempre() -> None:
    manual = Ocupado(T0, T0 + 2 * CUARTO, "manual", 7)
    plan = planificar(entrada([0.01] * 2 + [0.05] * 6, salidas=[(8, 53, 4)], ocupados=(manual,)))
    assert indices(plan) == [2]  # la manual aporta 2


def en_curso(desde: datetime, n: int, ahora: datetime) -> FranjaEnCurso:
    cuartos = tuple(CuartoPlan(desde + i * CUARTO, "necesaria", None, D(1), D("0.08"), False, "segura", False)
                    for i in range(n))
    return FranjaEnCurso(desde, desde + n * CUARTO, cuartos)


def test_franja_en_curso_intocable() -> None:
    ahora = T0 + timedelta(minutes=40)  # 20:40; franja de 20:00 a 21:30
    curso = en_curso(T0, 6, ahora)
    cuartos = [Cuarto(T0 + timedelta(minutes=30) + i * CUARTO, D("0.90") if i < 4 else D("0.01"), False, None, D(4))
               for i in range(12)]
    e = Entrada(ahora=ahora, parametros=params(), nivel_kwh=D(50), conectado=True, prudente=False, umbral=D("0.1"),
                cuartos=tuple(cuartos), salidas=(Salida(T0 + 14 * CUARTO, "horario", D(54), D(4)),),
                horizonte_fin=T0 + 14 * CUARTO, en_curso=curso)
    plan = planificar(e)
    curso_plan = [f for f in plan.franjas if f.en_curso]
    assert [(f.inicio, f.fin) for f in curso_plan] == [(T0, T0 + 6 * CUARTO)]
    # Quedan 50 min de la en curso (3,33 kWh): falta ~0,67 → un cuarto barato después.
    nuevos = [c for c in plan.cuartos if c.inicio >= curso.fin]
    assert len(nuevos) == 1 and nuevos[0].precio == D("0.01")
    assert not any(c.inicio >= ahora and c.inicio < curso.fin and c.precio == D("0.90") for c in plan.cuartos)


def test_ajena_sobre_la_franja_en_curso_da_aviso() -> None:
    ahora = T0 + timedelta(minutes=20)
    e = entrada([0.1] * 8, salidas=[(8, 50, 4)], ahora=ahora, en_curso=en_curso(T0, 4, ahora),
                ocupados=(Ocupado(T0 + CUARTO * 2, T0 + CUARTO * 6, "ajena", 6),), respetar_ajenas=True)
    plan = planificar(e)
    assert any(f.en_curso and f.choca_con == (6,) for f in plan.franjas)
    assert any("pisa la franja en curso" in a for a in plan.avisos)


# --- Óptimo del nivel 1 ---------------------------------------------------------------------


def semillas(n: int) -> pytest.MarkDecorator:
    """Semillas fijas; el id del test la muestra (p. ej. [semilla=77]) para reproducir un fallo."""
    return pytest.mark.parametrize("semilla", range(n), ids=lambda s: f"semilla={s}")


LENTO = pytest.mark.skipif(not os.environ.get("PLAN_LENTO"),
                           reason="tanda larga: PLAN_LENTO=1 pytest -m lento tests/test_plan_carga.py")


@semillas(40)
def test_nivel_1_igual_a_la_busqueda_exhaustiva(semilla: int) -> None:
    _exhaustiva(semilla)


@pytest.mark.lento
@LENTO
@semillas(3000)
def test_nivel_1_igual_a_la_busqueda_exhaustiva_larga(semilla: int) -> None:
    _exhaustiva(semilla)


def _exhaustiva(semilla: int) -> None:
    rng = random.Random(semilla)
    n = rng.randint(4, 10)
    precios_ = [rng.randint(1, 30) / 100 for _ in range(n)]
    s1 = rng.randint(2, n - 1)
    salidas = [(s1, 50 + rng.randint(0, s1), 2), (n, 48 + rng.randint(0, n), 2)]
    # Coche fuera solo en la salida; de vuelta, enchufado seguro (probabilidad 1).
    plan = planificar(entrada(precios_, salidas=salidas, probabilidad=dict.fromkeys(range(s1, n), 1)))
    objetivo = [D(o) for _, o, _ in salidas]

    def factible(sel: tuple[int, ...]) -> bool:
        en_1 = sum(1 for i in sel if i < s1)
        return 50 + en_1 >= objetivo[0] and 50 + len(sel) - 2 >= objetivo[1]

    optimos = [sum(D(str(precios_[i])) for i in sel) for k in range(n + 1)
               for sel in itertools.combinations(range(n), k) if factible(sel)]
    if not optimos:
        assert not all(s.cubierta for s in plan.salidas)
        return
    assert all(s.cubierta for s in plan.salidas)
    assert coste(plan, "necesaria") == min(optimos)


# --- Precios y disponibilidad -----------------------------------------------------------------


def test_publicado_con_la_hora_de_publicacion() -> None:
    manana = local(19, 10)
    assert precios.publicado(local(18, 23), local(18, 9), 810)
    assert not precios.publicado(manana, local(18, 13, 29), 810)
    assert precios.publicado(manana, local(18, 13, 30), 810)
    assert not precios.publicado(local(20, 1), local(18, 20), 810)


def test_precio_esperado_y_provisional() -> None:
    historico = [(local(11, 22) + i * CUARTO, D("0.10") + i * D("0.01")) for i in range(4)]  # miércoles 22:xx
    historico += [(local(4, 22), D("0.30"))]  # fuera de una ventana de 7 días
    perfil = precios.perfil_esperado(historico, local(18, 12), 7)
    assert perfil == {(3, 22): D("0.115")}
    publicados = {local(18, 22): D("0.2")}
    assert precios.precio_cuarto(local(18, 22), local(18, 12), publicados, perfil, 810) == (D("0.2"), False)
    assert precios.precio_cuarto(local(25, 22, 30), local(18, 12), publicados, perfil, 810) == (D("0.115"), True)
    assert precios.precio_cuarto(local(25, 3), local(18, 12), publicados, perfil, 810) == (None, True)


def test_umbral_con_dias_completos() -> None:
    historico = [(local(17, 10), D("0.10")), (local(16, 10), D("0.20")), (local(18, 1), D("5")),  # hoy: no cuenta
                 (local(9, 10), D("9"))]  # fuera de 7 días
    assert precios.umbral(historico, local(18, 15), 7, D(60)) == D("0.09")
    assert precios.umbral([], local(18, 15), 7, D(60)) is None


def test_mediana() -> None:
    assert precios.mediana([D(4400), D(4200), D(4300)]) == D(4300)
    assert precios.mediana([D(1), D(2)]) == D("1.5")


def test_disponibilidad_con_huecos_y_cambio_de_hora() -> None:
    observados = [(local(4, 22), True), (local(11, 22), True), (local(18, 22), False),  # miércoles 22:00: 2 de 3
                  (local(18, 22, 15), True),
                  (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), True),  # 02:30 CEST
                  (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), False)]  # 02:30 CET: misma clave
    perfil = disponibilidad.perfil(observados)
    assert perfil[(3, 22 * 60)] == D(2) / 3
    assert perfil[(3, 22 * 60 + 15)] == D(1)
    assert perfil[(7, 150)] == D("0.5")
    assert (3, 22 * 60 + 30) not in perfil  # sin muestras: no cuenta
    assert disponibilidad.dias_historial(observados) == 4


# --- Invariantes con entradas aleatorias -------------------------------------------------------


@semillas(60)
def test_invariantes_aleatorios(semilla: int) -> None:
    _invariantes(semilla)


@pytest.mark.lento
@LENTO
@semillas(5000)
def test_invariantes_aleatorios_larga(semilla: int) -> None:
    _invariantes(semilla)


def _invariantes(semilla: int) -> None:
    rng = random.Random(1000 + semilla)
    ahora = local(18, rng.randint(17, 23), rng.choice((0, 15, 30, 45)))
    n = rng.randint(16, 96)
    precios_ = [None if rng.random() < 0.03 else rng.randint(-2, 40) / 100 for _ in range(n)]
    s1 = rng.randint(4, n - 1)
    salidas = [(s1, rng.randint(40, 95), rng.randint(2, 30))]
    if s1 + 8 < n and rng.random() < 0.6:
        salidas.append((rng.randint(s1 + 4, n), rng.randint(40, 95), rng.randint(2, 30)))
    probabilidad = {i: rng.choice((0, 0.5, 0.9, 1)) for i in range(n)}
    ocupados = tuple(Ocupado(ahora + a * CUARTO + timedelta(minutes=rng.choice((0, 7))),
                             ahora + (a + rng.randint(1, 6)) * CUARTO, rng.choice(("ajena", "manual")), 1 + k)
                     for k, a in enumerate(rng.sample(range(n), rng.randint(0, 3))))
    p = params(tope_kwh=D(rng.choice((90, 95, 100))), factor_bateria=D("0.86"), margen_carga_pct=D(rng.randint(0, 30)),
               margen_salida_min=rng.choice((0, 30, 60)), carga_oportunista=rng.random() < 0.7,
               nivel_oportunista_kwh=D(rng.randint(50, 100)), franja_min_min=rng.choice((15, 30, 45)),
               max_tareas=rng.randint(1, 9))
    e = entrada(precios_, salidas=salidas, nivel=rng.randint(10, 92), ahora=ahora, conectado=rng.random() < 0.7,
                prudente=rng.random() < 0.3, umbral=rng.choice((None, 0.08, 0.15)),
                provisionales={i for i in range(n) if rng.random() < 0.2}, probabilidad=probabilidad,
                potencia=rng.choice((3.7, 4.4)), parametros=p, ocupados=ocupados,
                respetar_ajenas=rng.random() < 0.5)
    plan = planificar(e)
    assert plan == planificar(e)

    cuartos = {c.inicio: c for c in e.cuartos}
    respetados = [o for o in ocupados if o.origen == "manual" or e.respetar_ajenas]
    margen = timedelta(minutes=p.margen_salida_min)
    for c in plan.cuartos:
        assert c.inicio >= ahora and c.inicio + CUARTO <= e.horizonte_fin
        assert not any(franjas.solapa(c.inicio, c.inicio + CUARTO, o.inicio, o.fin) for o in respetados)
        assert not any(franjas.solapa(c.inicio, c.inicio + CUARTO, s.instante - margen, s.instante)
                       for s in e.salidas)
        if e.prudente:
            assert e.conectado and c.inicio + CUARTO <= e.salidas[0].instante
        if c.nivel in ("margen", "oportunista"):
            # Mismas condiciones también para los cuartos añadidos al alargar o al cerrar huecos.
            assert not c.provisional and c.precio is not None and e.umbral is not None and c.precio < e.umbral
            assert c.kwh > 0
        assert c.kwh <= cuartos[c.inicio].potencia_kw / 4
    # El plan no lleva por encima del tope: si una salida queda por encima (por el nivel de partida o por
    # tareas fijas del usuario), el plan no ha añadido energía antes de ella.
    for s in plan.salidas:
        if s.nivel_salida_kwh > p.tope_kwh + D("1e-9"):
            assert all(c.kwh == 0 for c in plan.cuartos if c.inicio < s.instante), s
    # El oportunista no lleva por encima de su nivel: si una salida queda por encima (la necesaria puede
    # superarlo, también al consolidar), los cuartos oportunistas anteriores no se pueden quitar sin partir una
    # franja o dejarla corta (están en medio o la franja quedaría por debajo de franja_min).
    techo_op = min(p.nivel_oportunista_kwh, p.tope_kwh)
    elegidos = {c.inicio for c in plan.cuartos if not any(f.en_curso and f.inicio <= c.inicio < f.fin
                                                          for f in plan.franjas)}
    for s in plan.salidas:
        if s.nivel_salida_kwh > techo_op + D("1e-9"):
            for c in plan.cuartos:
                if c.nivel == "oportunista" and c.inicio < s.instante:
                    f = next(f for f in plan.franjas if f.inicio <= c.inicio < f.fin)
                    interior = c.inicio - CUARTO in elegidos and c.inicio + CUARTO in elegidos
                    restante = f.fin - f.inicio - CUARTO
                    assert interior or timedelta(0) < restante < timedelta(minutes=p.franja_min_min), (s, c)
    # Franjas mínimas (salvo las que sirven a una salida y no tienen alternativa) y tareas.
    for f in plan.franjas:
        if f.fin - f.inicio < timedelta(minutes=p.franja_min_min):
            assert f.motivo == "necesaria"
    assert plan.tareas <= p.max_tareas or any("tareas" in a for a in plan.avisos)
