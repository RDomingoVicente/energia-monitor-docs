"""Paso 3c contra PostgreSQL: escritura del plan en un cargador simulado a nivel HTTP (el cliente real, con su lista
cerrada y su validación), cola de acciones, avisos, NOTIFY y CLI. Nunca se contacta con el cargador real."""

from __future__ import annotations

import io
import json
import urllib.request
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import psycopg
import pytest
from test_plan_servicio_db import AHORA, escenario, local
from test_plan_servicio_db import limpiar as limpiar_plan

from energia_monitor import db
from energia_monitor.plan_carga import acciones, escritura
from energia_monitor.plan_carga import cli as plan_cli
from energia_monitor.plan_carga import servicio as ps
from energia_monitor.viaris import ClienteViaris

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "viaris" / \
    "fw7.7.34_20261118_modules_scheduler_elements_level_cfg.json"
TABLAS = ("plan_auditoria", "plan_accion", "planificador_tarea", "plan_manual", "plan_bloqueo", "planificador_lectura",
          "planificador_aviso")


class CargadorHTTP:
    """Programador simulado detrás de urlopen: GET del programador y POST/PUT/DELETE de tareas, como el real
    (verificado el 13/11/2026: devuelven la configuración del conector)."""

    def __init__(self) -> None:
        crudo = json.loads(FIXTURE.read_text())
        self.tareas: dict[int, dict[str, Any]] = {t["id"]: t for t in crudo[0]["data"]["cfg"]["tasks"]}
        self.peticiones: list[tuple[str, str, Any]] = []
        self.fallar: list[str] = []  # métodos que fallan (HTTP 500), una vez cada uno
        self.ignorar = False  # acepta la escritura pero no la aplica

    def respuesta(self) -> list[dict[str, Any]]:
        return [{"name": "mennekes", "data": {"uid": 17, "cfg": {"tasks": list(self.tareas.values()),
                                                                 "defaultState": 1}}}]

    def __call__(self, peticion: urllib.request.Request, timeout: float) -> io.BytesIO:
        metodo, ruta = peticion.get_method(), peticion.full_url.split("192.168.1.34", 1)[1]
        cuerpo = json.loads(peticion.data) if peticion.data else None
        self.peticiones.append((metodo, ruta, cuerpo))
        if metodo in self.fallar:
            self.fallar.remove(metodo)
            raise HTTPError(peticion.full_url, 500, "error", {}, None)  # type: ignore[arg-type]
        if metodo != "GET" and not self.ignorar:
            t = cuerpo["tasks"][0]
            if metodo == "DELETE":
                self.tareas.pop(t["id"], None)
            else:
                self.tareas[t["id"]] = {"type": 0, "maxPower": -1, **t}
        return io.BytesIO(json.dumps(self.respuesta()).encode())

    def escrituras(self) -> list[tuple[str, int]]:
        return [(m, c["tasks"][0]["id"]) for m, _, c in self.peticiones if m != "GET"]


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    def borrar() -> None:
        for tabla in TABLAS:
            conn.execute(f"DELETE FROM {tabla}")  # type: ignore[arg-type]
        conn.execute("DELETE FROM planificador_modo WHERE origen <> 'usuario' OR desde > '2025-01-02Z'")
        limpiar_plan(conn)
    borrar()
    yield
    borrar()


@pytest.fixture
def cargador() -> CargadorHTTP:
    return CargadorHTTP()


def cliente(c: CargadorHTTP) -> ClienteViaris:
    return ClienteViaris("http://192.168.1.34", abrir=c)


def plan_guardado(conn: psycopg.Connection, ahora=AHORA) -> None:  # type: ignore[no-untyped-def]
    entrada, ctx, plan, ms = ps.calcular(conn, ahora)
    ps.guardar(conn, entrada, ctx, plan, "test", ms)


def activo(conn: psycopg.Connection, desde=AHORA - timedelta(hours=1)) -> None:  # type: ignore[no-untyped-def]
    conn.execute("INSERT INTO planificador_modo (desde, modo, origen) VALUES (%s, 'activo', 'usuario')", (desde,))


# --- Escritura -----------------------------------------------------------------------------------


def test_activo_escribe_el_plan_y_es_idempotente(conn: psycopg.Connection, limpio: None,
                                                  cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA)
    assert r.leida and not r.en_seco and r.fallo is None and r.escritas >= 1
    # Guardiana adoptada (sin escribir), franjas nuevas en ids libres; las tareas 5 y 6 no se tocan.
    assert all(i not in (5, 6, 9) for _, i in cargador.escrituras())
    filas = conn.execute("SELECT id_tarea, papel, estado FROM planificador_tarea ORDER BY id_tarea").fetchall()
    assert (9, "guardiana", "confirmada") in filas and all(e == "confirmada" for _, _, e in filas)
    assert conn.execute("SELECT count(*) FROM plan_auditoria WHERE resultado = 'ok' AND NOT en_seco").fetchone() == (
        r.escritas,)
    # Tras cada escritura, un GET de confirmación.
    metodos = [m for m, _, _ in cargador.peticiones]
    assert all(metodos[i + 1] == "GET" for i, m in enumerate(metodos) if m != "GET")
    antes = len(cargador.escrituras())
    r2 = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA + timedelta(minutes=1))
    assert r2.escritas == 0 and len(cargador.escrituras()) == antes


def test_sombra_solo_en_seco(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    plan_guardado(conn)
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA)
    assert r.en_seco and r.escritas == 0 and cargador.escrituras() == []
    assert r.operaciones and conn.execute("SELECT count(*) FROM plan_auditoria WHERE en_seco").fetchone() == (
        len(r.operaciones),)
    assert conn.execute("SELECT id_tarea, papel FROM planificador_tarea").fetchall() == [(9, "guardiana")]


def test_pendiente_antes_del_post_y_fallo(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    cargador.fallar = ["POST"]
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA)
    assert r.fallo is not None and r.escritas == 0
    # La tarea se registró antes del POST: queda en error con el motivo, y el aviso de escritura fallida.
    assert conn.execute("SELECT estado FROM planificador_tarea WHERE papel = 'franja'").fetchall() == [("error",)]
    assert conn.execute("SELECT clave FROM v_aviso WHERE clave = 'escritura_fallida' AND accion = 'plan'").fetchall()
    # Ciclo siguiente: el registro sin tarea se olvida sin aviso y se vuelve a escribir.
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA + timedelta(minutes=1))
    assert r.fallo is None and r.escritas >= 1
    assert not [a for a in r.avisos if a.clave.startswith("tarea_desaparecida")]


def test_escritura_no_confirmada(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    cargador.ignorar = True  # responde 200 pero no la aplica
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA)
    assert r.fallo is not None and "no quedó como se escribió" in r.fallo
    assert conn.execute("SELECT count(*) FROM plan_auditoria WHERE resultado = 'error'").fetchone() == (1,)


def test_sin_guardiana_se_crea_primero(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    del cargador.tareas[9]
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA)
    assert cargador.escrituras() == [("POST", 9)]
    assert {"guardiana_ausente", "ninguna_tarea_activa"} <= {a.clave for a in r.avisos}
    assert {c for (c,) in conn.execute("SELECT clave FROM v_aviso")} >= {"guardiana_ausente"}


def test_el_cargador_sin_respuesta_no_escribe(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    cargador.fallar = ["GET"]
    r = escritura.reconciliar_y_escribir(conn, cliente(cargador), AHORA)
    assert not r.leida and cargador.escrituras() == []
    assert conn.execute("SELECT ok FROM planificador_lectura").fetchone() == (False,)


def test_ajenas_activas_se_respetan_y_cuentan(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    """En activo, la tarea 6 de la app (miércoles 00:00–08:00) activa: el plan no la pisa y descuenta su energía."""
    escenario(conn, local(17, 20))  # martes 17/11 20:00: salida el miércoles 18 a las 09:00
    activo(conn, local(17, 19))
    cargador.tareas[6]["active"] = 1
    escritura.leer(conn, cliente(cargador), local(17, 20))
    entrada, _, plan, _ = ps.calcular(conn, local(17, 20))
    assert any(o.origen == "ajena" and o.tarea == 6 for o in entrada.ocupados) and entrada.respetar_ajenas
    assert not any(local(18, 0) <= c.inicio < local(18, 8) for c in plan.cuartos)


# --- Acciones del formulario ------------------------------------------------------------------------


def test_notify_al_encolar(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    import os

    with psycopg.connect(dbname=nombre_bd, host=os.environ["PGHOST"], autocommit=True) as escucha:
        escucha.execute("LISTEN planificador")
        ident = acciones.encolar(conn, "modo", {"modo": "pausa"})
        avisos = list(escucha.notifies(timeout=5, stop_after=1))
    assert [n.payload for n in avisos] == [str(ident)]


def test_en_sombra_las_acciones_no_escriben(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    """Con una franja propia escrita en activo y vuelta a sombra: Detener, Cargar ahora, añadir y eliminar se
    rechazan al procesar y no llega ninguna escritura al cargador."""
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    cl = cliente(cargador)
    escritura.reconciliar_y_escribir(conn, cl, AHORA)
    inicio, fin = conn.execute("SELECT franja_inicio, franja_fin FROM planificador_tarea WHERE papel = 'franja'"
                               " ORDER BY franja_inicio LIMIT 1").fetchone()  # type: ignore[misc]
    conn.execute("INSERT INTO planificador_modo (desde, modo, origen) VALUES (%s, 'sombra', 'usuario')",
                 (AHORA + timedelta(minutes=1),))
    antes = len(cargador.escrituras())
    for tipo, datos in (("detener", {"inicio": inicio.isoformat()}), ("cargar_ahora", {}),
                        ("anadir", {"inicio": local(13, 2).isoformat(), "fin": local(13, 3).isoformat()}),
                        ("eliminar", {"inicio": inicio.isoformat(), "fin": fin.isoformat()})):
        acciones.encolar(conn, tipo, datos)
    acciones.procesar(conn, cl, inicio + timedelta(minutes=20))
    assert {(e, r) for e, r in conn.execute("SELECT estado, resultado FROM plan_accion")} == {
        ("error", "En sombra no se escribe en el cargador. Cambia a activo para usarlo.")}
    assert len(cargador.escrituras()) == antes
    assert conn.execute("SELECT count(*) FROM plan_manual").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM plan_bloqueo").fetchone() == (0,)


def test_modo_y_franja_manual(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    cl = cliente(cargador)
    escritura.leer(conn, cl, AHORA)
    acciones.encolar(conn, "modo", {"modo": "activo"})
    acciones.encolar(conn, "anadir", {"inicio": local(12, 2).isoformat(), "fin": local(12, 3).isoformat()})
    acciones.encolar(conn, "anadir", {"inicio": local(12, 2, 30).isoformat(), "fin": local(12, 4).isoformat()})
    assert acciones.procesar(conn, cl, AHORA) == 3
    estados = conn.execute("SELECT tipo, estado, resultado FROM plan_accion ORDER BY id").fetchall()
    assert [e for _, e, _ in estados] == ["hecha", "hecha", "error"] and estados[2][2] == "Choca con otra franja manual."
    assert conn.execute("SELECT planificador_modo_en(%s)", (AHORA + timedelta(seconds=1),)).fetchone() == ("activo",)
    # La manual se escribe como tarea propia de origen manual.
    plan_guardado(conn, AHORA + timedelta(seconds=1))
    escritura.reconciliar_y_escribir(conn, cl, AHORA + timedelta(seconds=1))
    assert conn.execute("SELECT count(*) FROM planificador_tarea WHERE origen = 'manual'").fetchone() == (1,)


def test_manual_que_choca_con_la_app(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn, local(17, 12))
    cargador.tareas[5]["active"] = 1  # martes 23:00–23:59
    escritura.leer(conn, cliente(cargador), local(17, 12))
    error = acciones.validar_en_bd(conn, local(17, 22, 30), local(17, 23, 30), local(17, 12))
    assert error == "Choca con la tarea 5 de la app (mar 23:00–23:59)."
    assert acciones.validar_en_bd(conn, local(17, 22), local(17, 22, 15), local(17, 12)) == \
        "Una franja dura al menos 30 min (franja mínima)."


def test_eliminar_y_detener(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    cl = cliente(cargador)
    escritura.reconciliar_y_escribir(conn, cl, AHORA)
    inicio, fin = conn.execute("SELECT franja_inicio, franja_fin FROM planificador_tarea WHERE papel = 'franja'"
                               " ORDER BY franja_inicio LIMIT 1").fetchone()  # type: ignore[misc]
    # Detener en curso: desactiva al momento y bloquea el resto de la franja.
    dentro = inicio + timedelta(minutes=20)
    acciones.encolar(conn, "detener", {"inicio": inicio.isoformat()})
    acciones.procesar(conn, cl, dentro)
    assert conn.execute("SELECT estado FROM plan_accion").fetchone() == ("hecha",)
    de_la_franja = [i for (i,) in conn.execute("SELECT id_tarea FROM planificador_tarea WHERE franja_inicio = %s",
                                                (inicio,))]
    assert de_la_franja and all(cargador.tareas[i]["active"] == 0 for i in de_la_franja)
    assert cargador.tareas[9]["active"] == 1  # la guardiana, intacta
    assert conn.execute("SELECT motivo, fin FROM plan_bloqueo").fetchall() == [("detenida", fin)]
    # Detener que falla dos veces: error con «Reintentar».
    cargador.fallar = ["PUT", "PUT"]
    acciones.encolar(conn, "detener", {"inicio": inicio.isoformat()})
    conn.execute("UPDATE planificador_tarea SET activa = true WHERE papel = 'franja'")
    for t in cargador.tareas.values():
        t["active"] = 1
    acciones.procesar(conn, cl, dentro)
    assert "Reintentar" in conn.execute("SELECT resultado FROM plan_accion ORDER BY id DESC LIMIT 1").fetchone()[0]  # type: ignore[index]


def test_formulario_solo_encola(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    import os

    db.configurar_rol_formulario(conn, "clave-formulario")
    with psycopg.connect(dbname=nombre_bd, user="formulario_rw", password="clave-formulario",
                         host=os.environ["PGHOST"], autocommit=True) as rw:
        acciones.encolar(rw, "modo", {"modo": "pausa"})
        for sql in ("INSERT INTO planificador_tarea (id_tarea, papel, origen, fecha, hour_min, duracion, activa, estado)"
                    " VALUES (1, 'franja', 'manual', '2026-11-18', 0, 30, true, 'pendiente')",
                    "INSERT INTO plan_manual (inicio, fin, origen) VALUES (now(), now() + interval '1 hour', 'manual')",
                    "UPDATE plan_accion SET estado = 'hecha'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rw.execute(sql)  # type: ignore[arg-type]


# --- CLI --------------------------------------------------------------------------------------------


def test_cli_tareas_en_seco_limpiar_y_guardiana(conn: psycopg.Connection, limpio: None, cargador: CargadorHTTP) -> None:
    escenario(conn)
    activo(conn)
    plan_guardado(conn)
    cl = cliente(cargador)
    lineas = plan_cli.tareas(conn, cl, AHORA)
    assert any("9  activa    31/12 00:00 + 1 min" in x and "guardiana" in x for x in lineas)
    assert any(" 5  inactiva  semanal M 23:00 + 59 min" in x and "ajena" in x for x in lineas)
    seco = plan_cli.en_seco(conn, cl, AHORA)
    assert seco[0].startswith("En seco (modo activo") and any("POST" in x for x in seco)
    assert cargador.escrituras() == []
    escritura.reconciliar_y_escribir(conn, cl, AHORA)
    # Una propia cambiada desde la app no se borra; se informa.
    propias = [i for (i,) in conn.execute("SELECT id_tarea FROM planificador_tarea WHERE papel = 'franja'"
                                          " ORDER BY id_tarea")]
    cambiada = propias[0]
    cargador.tareas[cambiada]["initTime"]["timeList"][0]["duration"] += 15
    lineas = plan_cli.limpiar(conn, cl, AHORA + timedelta(minutes=1))
    assert set(cargador.tareas) == {5, 6, 9, cambiada} and "no cargará" in lineas[-1]
    assert any(f"Tarea {cambiada}: no está como la escribió" in x for x in lineas)
    assert [(m, i) for m, i in cargador.escrituras() if m == "DELETE"] == [("DELETE", i) for i in propias[1:]]
    del cargador.tareas[cambiada]
    assert conn.execute("SELECT planificador_modo_en(%s)", (AHORA + timedelta(minutes=2),)).fetchone() == ("sombra",)
    lineas = plan_cli.guardiana(conn, cl, AHORA + timedelta(minutes=2), 6)
    assert lineas == ["El id 6 está ocupado en el cargador: elige uno libre."]
    lineas = plan_cli.guardiana(conn, cl, AHORA + timedelta(minutes=2), 10)
    assert set(cargador.tareas) == {5, 6, 10} and "Borrada la vieja (9)." in lineas
    assert conn.execute("SELECT bateria_param('id_tarea_guardiana', %s)", (AHORA + timedelta(minutes=3),)).fetchone() \
        == (10,)
    # Deja el parámetro como estaba: fuera el valor nuevo y la vigencia anterior, abierta otra vez.
    conn.execute("DELETE FROM bateria_parametro WHERE clave = 'id_tarea_guardiana'"
                 " AND fuente = 'planificador guardiana --id'")
    conn.execute("UPDATE bateria_parametro SET vigencia = daterange(lower(vigencia), NULL, '[)')"
                 " WHERE clave = 'id_tarea_guardiana' AND lower(vigencia) = (SELECT max(lower(vigencia))"
                 " FROM bateria_parametro WHERE clave = 'id_tarea_guardiana')")
    assert conn.execute("SELECT bateria_param('id_tarea_guardiana', %s)", (AHORA,)).fetchone() == (9,)
