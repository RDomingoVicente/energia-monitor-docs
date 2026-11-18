"""Reconciliador del plan con el programador del cargador (paso 3c), sin red ni base de datos.

Parte de la lectura real del 18/11/2026 (tareas 5 y 6 del usuario, semanales e inactivas, y 9, la guardiana) y
de un cargador simulado en memoria que aplica POST, PUT y DELETE como el real (verificado el 13/11/2026).
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from energia_monitor.plan_carga.reconciliar import (
    Deseada,
    Operacion,
    Propia,
    cuerpo,
    deseadas,
    ocurrencias,
    reconciliar,
)
from energia_monitor.tiempo import MADRID
from energia_monitor.viaris import Tarea, parsear_programador, validar_escritura

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "viaris" / \
    "fw7.7.34_20261118_modules_scheduler_elements_level_cfg.json"
AHORA = datetime(2026, 11, 18, 15, 0, tzinfo=MADRID).astimezone(UTC)  # miércoles 15:00
CONGELAR = timedelta(minutes=30)


def local(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 11, dia, hora, minuto, tzinfo=MADRID).astimezone(UTC)


def leer() -> list[Tarea]:
    return parsear_programador(json.loads(FIXTURE.read_text()))


class Cargador:
    """Programador simulado: tareas por id; las escrituras sustituyen la tarea entera, como el real."""

    def __init__(self, tareas: list[Tarea]) -> None:
        self.tareas = {t.id: t.datos for t in tareas}

    def lectura(self) -> list[Tarea]:
        return parsear_programador([{"name": "mennekes", "data": {"cfg": {"tasks": list(self.tareas.values())}}}])

    def aplicar(self, op: Operacion) -> None:
        tarea = op.cuerpo["tasks"][0]
        if op.metodo == "DELETE":
            del self.tareas[tarea["id"]]
            return
        if op.metodo == "POST":
            assert tarea["id"] not in self.tareas, "POST sobre un id ocupado (sin verificar en el real)"
        completa: dict[str, Any] = {"type": 0, **tarea}
        completa["initTime"] = {**tarea["initTime"],
                                "timeList": [{"maxPower": 0, "phases": 0, **tarea["initTime"]["timeList"][0]}]}
        self.tareas[tarea["id"]] = completa


def aplicar(c: Cargador, r: Any, propias: dict[int, Propia]) -> None:
    """Como el servicio: adopta, olvida, registra pendiente antes del POST, valida y escribe."""
    for p in r.adoptar:
        propias[p.id] = p
    for i in r.olvidar:
        propias.pop(i, None)
    for op in r.operaciones:
        if op.metodo == "POST" and op.registro is not None:
            propias[op.id] = op.registro  # pendiente antes del POST
        validar_escritura(op.metodo, op.cuerpo, frozenset(propias))
        c.aplicar(op)
        if op.registro is None:
            propias.pop(op.id, None)
        else:
            propias[op.id] = Propia(**{**op.registro.__dict__, "estado": "confirmada"})


def plan(*franjas: tuple[datetime, datetime]) -> list[Deseada]:
    return deseadas((a, b, "automatica") for a, b in franjas)


def franja_propia(i: int, inicio: datetime, fin: datetime, activa: bool = True) -> tuple[Propia, dict[str, Any]]:
    d = deseadas([(inicio, fin, "automatica")])[0]
    p = Propia(i, "franja", "automatica", d.fecha, d.hora_min, d.duracion, activa, inicio, fin)
    return p, cuerpo(i, d.fecha, d.hora_min, d.duracion, activa)["tasks"][0]


# --- Casos -------------------------------------------------------------------------------------------


def test_primer_ciclo_adopta_la_guardiana_y_escribe_el_plan() -> None:
    c = Cargador(leer())
    propias: dict[int, Propia] = {}
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [], plan((local(18, 23), local(19, 2)), (local(19, 3), local(19, 5))),
                    CONGELAR)
    assert [p.id for p in r.adoptar] == [9] and r.avisos == ()
    # La de 23:00 a 02:00 se parte en la medianoche: 3 tareas en los ids libres 1, 2 y 3.
    assert [(o.metodo, o.id) for o in r.operaciones] == [("POST", 1), ("POST", 2), ("POST", 3)]
    horarios = [(o.cuerpo["tasks"][0]["initTime"]["day"], o.cuerpo["tasks"][0]["initTime"]["timeList"][0])
                for o in r.operaciones]
    assert horarios == [(1 << 17, {"hourMin": 1380, "duration": 60}), (1 << 18, {"hourMin": 0, "duration": 120}),
                        (1 << 18, {"hourMin": 180, "duration": 120})]
    aplicar(c, r, propias)
    # Las ajenas siguen igual y el segundo ciclo no escribe nada (idempotente).
    assert c.tareas[5] == {t.id: t.datos for t in leer()}[5] and c.tareas[6] == {t.id: t.datos for t in leer()}[6]
    r2 = reconciliar(AHORA, "activo", 9, c.lectura(), list(propias.values()),
                     plan((local(18, 23), local(19, 2)), (local(19, 3), local(19, 5))), CONGELAR)
    assert r2.operaciones == () and r2.avisos == ()


def test_sin_guardiana_solo_se_crea_la_guardiana() -> None:
    c = Cargador([t for t in leer() if t.id != 9])
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [], plan((local(18, 23), local(19, 2))), CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("POST", 9)]
    assert {a.clave for a in r.avisos} == {"guardiana_ausente", "ninguna_tarea_activa"}
    t = r.operaciones[0].cuerpo["tasks"][0]
    assert (t["active"], t["initTime"]) == (1, {"day": 1 << 30, "month": 1 << 11, "weekday": 0,
                                                "timeList": [{"hourMin": 0, "duration": 1}]})


def test_guardiana_inactiva_se_activa_antes_que_nada() -> None:
    lectura = leer()
    g = lectura[2]
    inactiva = Tarea(9, False, 0, 0, g.day, g.month, 0, 1, {**g.datos, "active": 0})
    r = reconciliar(AHORA, "activo", 9, [*lectura[:2], inactiva],
                    [Propia(9, "guardiana", "guardiana", date(2026, 12, 31), 0, 1, True)],
                    plan((local(18, 23), local(19, 2))), CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("PUT", 9)]
    assert r.operaciones[0].cuerpo["tasks"][0]["active"] == 1
    # PUT con la tarea completa leída, sin los campos calculados.
    assert "_initLocaltime" not in json.dumps(r.operaciones[0].cuerpo) and \
        r.operaciones[0].cuerpo["tasks"][0]["maxPower"] == -1
    assert {a.clave for a in r.avisos} == {"guardiana_inactiva", "ninguna_tarea_activa"}


def test_id_de_la_guardiana_ocupado_no_se_crea_encima() -> None:
    lectura = leer()
    prueba = Tarea(9, True, 0, 0, 1 << 16, 1 << 10, 1380, 60, {"id": 9})  # la de prueba del 17/11
    r = reconciliar(AHORA, "activo", 9, [*lectura[:2], prueba], [], plan((local(18, 23), local(19, 2))), CONGELAR)
    assert r.operaciones == () and [a.clave for a in r.avisos] == ["guardiana_ocupada"]
    assert r.avisos[0].nivel == "error" and "planificador guardiana --id N" in r.avisos[0].detalle


def test_terminadas_se_borran_y_las_caducadas_avisan() -> None:
    hecha, dato = franja_propia(1, local(18, 2), local(18, 6))
    reciente, dato2 = franja_propia(2, local(18, 13), local(18, 14, 45))
    c = Cargador(leer())
    c.tareas[1], c.tareas[2] = dato, dato2
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [hecha, reciente], [], CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("DELETE", 1), ("DELETE", 2)]
    assert [a.clave for a in r.avisos] == ["tarea_caducada:1"]  # la de las 14:45 aún no lleva 30 min terminada
    # También en pausa: borrarlas es obligatorio.
    r = reconciliar(AHORA, "pausa", 9, c.lectura(), [hecha, reciente], [], CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("DELETE", 1), ("DELETE", 2)]


def test_en_curso_y_congelada_no_se_tocan() -> None:
    curso, d1 = franja_propia(1, local(18, 14), local(18, 16))
    pronto, d2 = franja_propia(2, local(18, 15, 15), local(18, 16, 30))  # empieza en 15 min
    luego, d3 = franja_propia(3, local(18, 15, 45), local(18, 17))  # empieza en 45 min
    c = Cargador(leer())
    c.tareas.update({1: d1, 2: d2, 3: d3})
    # El plan ya no quiere ninguna: la en curso y la congelada se quedan; la de dentro de 45 min se borra.
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [curso, pronto, luego], [], CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("DELETE", 3)]
    # Con 0 minutos de congelación, la de dentro de 15 min también se puede borrar.
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [curso, pronto, luego], [], timedelta(0))
    assert [(o.metodo, o.id) for o in r.operaciones] == [("DELETE", 2), ("DELETE", 3)]


def test_modificada_desde_la_app_se_corrige() -> None:
    p, dato = franja_propia(1, local(18, 23), local(18, 23, 59))
    dato = {**dato, "initTime": {**dato["initTime"], "day": 0, "month": 0, "weekday": 8}}  # la app: semanal
    c = Cargador(leer())
    c.tareas[1] = dato
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [p], plan((local(18, 23), local(18, 23, 59))), CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("PUT", 1)]
    assert r.operaciones[0].cuerpo["tasks"][0]["initTime"]["weekday"] == 0
    assert [a.clave for a in r.avisos] == ["tarea_modificada:1"]


def test_pendiente_sin_tarea_se_olvida_sin_aviso() -> None:
    pendiente = Propia(1, "franja", "automatica", date(2026, 11, 18), 1380, 60, True, estado="pendiente")
    perdida = Propia(2, "franja", "automatica", date(2026, 11, 18), 1200, 60, True)
    r = reconciliar(AHORA, "activo", 9, leer(), [pendiente, perdida], plan((local(18, 23), local(19, 0))), CONGELAR)
    assert set(r.olvidar) == {1, 2} and [a.clave for a in r.avisos] == ["tarea_desaparecida:2"]
    assert [(o.metodo, o.id) for o in r.operaciones] == [("POST", 3)]  # 1 y 2 aún figuran como ocupados este ciclo


def test_pausa_desactiva_las_futuras_y_no_crea() -> None:
    curso, d1 = franja_propia(1, local(18, 14), local(18, 16))
    futura, d2 = franja_propia(2, local(18, 23), local(18, 23, 59))
    c = Cargador(leer())
    c.tareas.update({1: d1, 2: d2})
    r = reconciliar(AHORA, "pausa", 9, c.lectura(), [curso, futura], plan((local(19, 2), local(19, 4))), CONGELAR)
    assert [(o.metodo, o.id, o.cuerpo["tasks"][0].get("active")) for o in r.operaciones] == [("PUT", 2, 0)]


def test_mueve_en_vez_de_borrar_y_crear() -> None:
    p, dato = franja_propia(1, local(18, 23), local(18, 23, 59))
    c = Cargador(leer())
    c.tareas[1] = dato
    r = reconciliar(AHORA, "activo", 9, c.lectura(), [p], plan((local(19, 1), local(19, 3))), CONGELAR)
    assert [(o.metodo, o.id) for o in r.operaciones] == [("PUT", 1)]
    assert r.operaciones[0].cuerpo["tasks"][0]["initTime"]["timeList"][0] == {"hourMin": 60, "duration": 120}


def test_sin_ids_libres() -> None:
    lectura = leer() + [Tarea(i, False, 0, 32, 0, 0, 60, 30, {"id": i}) for i in (1, 2, 3, 4, 7, 8, 10)]
    r = reconciliar(AHORA, "activo", 9, lectura, [], plan((local(18, 23), local(19, 2))), CONGELAR)
    assert r.operaciones == () and [a.clave for a in r.avisos] == ["sin_huecos"]


def test_ocurrencias_de_las_tareas_ajenas() -> None:
    martes, miercoles = leer()[0], leer()[1]
    semana = (local(16, 0), local(23, 0))
    assert ocurrencias(martes, *semana) == [(local(17, 23), local(17, 23, 59))]
    assert ocurrencias(miercoles, *semana) == [(local(18, 0), local(18, 8))]
    diaria = Tarea(1, True, 0, 0, 0, 0, 120, 60, {})
    assert len(ocurrencias(diaria, *semana)) == 7
    con_fecha = Tarea(2, True, 0, 0, 1 << 18, 1 << 10, 1380, 60, {})  # 19/11
    assert ocurrencias(con_fecha, *semana) == [(local(19, 23), local(20, 0))]


# --- Propiedades con planes aleatorios en el tiempo -----------------------------------------------------


@pytest.mark.parametrize("semilla", range(40), ids=lambda s: f"semilla={s}")
def test_propiedades_en_el_tiempo(semilla: int) -> None:
    """Cada 15 min, un plan nuevo al azar; el cargador simulado aplica cada tanda. Siempre: las ajenas no se
    tocan, la guardiana nunca se desactiva ni se borra, nunca queda el cargador sin tareas activas, ninguna
    escritura incumple los formatos verificados, y un segundo ciclo sin cambios no escribe nada."""
    rng = random.Random(semilla)
    c = Cargador(leer())
    ajenas = {i: c.tareas[i] for i in (5, 6)}
    propias: dict[int, Propia] = {}
    modo = "activo"
    for paso in range(60):
        ahora = AHORA + timedelta(minutes=15 * paso)
        if rng.random() < 0.05:
            modo = rng.choice(("activo", "pausa", "activo"))
        # Franjas disjuntas, como las da el planificador.
        franjas: list[tuple[datetime, datetime]] = []
        for _ in range(rng.randint(0, 4)):
            a = ahora + timedelta(minutes=15 * rng.randint(-4, 150))
            b = a + timedelta(minutes=15 * rng.randint(1, 24))
            if all(b <= x or a >= y for x, y in franjas):
                franjas.append((a, b))
        deseadas_ = plan(*franjas)
        r = reconciliar(ahora, modo, 9, c.lectura(), list(propias.values()), deseadas_, CONGELAR)
        for op in r.operaciones:
            assert op.id not in ajenas
            if op.id == 9:
                assert op.metodo != "DELETE" and op.cuerpo["tasks"][0]["active"] == 1
        aplicar(c, r, propias)
        assert any(t.activa for t in c.lectura())
        assert {i: c.tareas[i] for i in (5, 6)} == ajenas
        assert len(c.tareas) <= 10
        r2 = reconciliar(ahora, modo, 9, c.lectura(), list(propias.values()), deseadas_, CONGELAR)
        # Sin cambios entre medias: nada que escribir (salvo terminadas que aún no se podían borrar, ninguna).
        assert r2.operaciones == (), (paso, r2.operaciones)
