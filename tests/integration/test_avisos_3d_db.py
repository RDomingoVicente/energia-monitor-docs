"""Paso 3d contra PostgreSQL: avisos nuevos en SQL (0023), catálogo aviso_tipo con correo y la vigilancia del NAS
con una base sembrada (aviso nuevo, sin cambios, resuelto y base inaccesible), como grafana_ro."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from test_plan_servicio_db import limpiar as limpiar_plan
from test_plan_servicio_db import precios

from energia_monitor import db, vigilancia
from energia_monitor.plan_carga import escritura
from energia_monitor.plan_carga.reconciliar import Aviso
from energia_monitor.tiempo import MADRID

RAIZ = Path(__file__).resolve().parents[2]
CORREO_NO_ERROR = {"escritura_fallida", "salida_no_cubierta"}


def local(d: date, hora: int, minuto: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hora, minuto, tzinfo=MADRID).astimezone(UTC)


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    def borrar() -> None:
        conn.execute("DELETE FROM planificador_aviso")
        limpiar_plan(conn)
    borrar()
    yield
    borrar()


def claves(conn: psycopg.Connection, ahora: datetime) -> dict[str, tuple[str, str]]:
    return {c: (n, d) for c, n, d in conn.execute("SELECT clave, nivel, detalle FROM bateria_avisos(%s)", (ahora,))}


def ciclo(conn: psycopg.Connection, ultimo_ok: datetime | None) -> None:
    conn.execute("INSERT INTO planificador_ciclo (id, ultimo_ok_en) VALUES (true, %s)"
                 " ON CONFLICT (id) DO UPDATE SET ultimo_ok_en = EXCLUDED.ultimo_ok_en", (ultimo_ok,))


# --- Catálogo ---------------------------------------------------------------------------------------


def _emitidos() -> dict[str, str]:
    """tipo → nivel de cada aviso que emite el código (Python y SQL de las migraciones)."""
    salida: dict[str, str] = {}
    for f in (RAIZ / "src" / "energia_monitor").rglob("*.py"):
        texto = f.read_text()
        for tipo, nivel in re.findall(r'Aviso\(\s*f?"([a-z_]+)[^"]*",\s*"(error|aviso|info)"', texto):
            salida[tipo] = nivel
    for f in (RAIZ / "db" / "migrations").glob("*.sql"):
        for tipo, nivel in re.findall(r"clave := '([a-z_]+)[^']*'[^;]*;\s*nivel := '(\w+)'", f.read_text()):
            salida[tipo] = nivel
    return salida


def test_catalogo_cubre_todos_los_avisos_con_su_nivel_y_correo(conn: psycopg.Connection) -> None:
    tipos = {t: (n, c) for t, n, c in conn.execute("SELECT tipo, nivel, correo FROM aviso_tipo")}
    emitidos = _emitidos()
    assert {"planificador_parado", "precios_sin_publicar", "salida_no_cubierta", "ajena_pisa_franja",
            "carga_sin_tarea", "guardiana_ausente", "escritura_fallida", "cargador_no_accesible"} <= set(emitidos)
    for tipo, nivel in emitidos.items():
        assert tipo in tipos, f"{tipo} sin fila en aviso_tipo"
        assert tipos[tipo][0] == nivel, tipo
    assert set(tipos) - set(emitidos) == {"bd_no_accesible"}  # el único que no sale de bateria_avisos()
    # Correo: todos los ERROR, más escritura fallida y el plan que no cubre una salida.
    for tipo, (nivel, correo) in tipos.items():
        assert correo == (nivel == "error" or tipo in CORREO_NO_ERROR), tipo


def test_v_aviso_lleva_correo_y_origen_muestreo_no_lo_borra_el_reconciliador(conn: psycopg.Connection,
                                                                              limpio: None) -> None:
    ahora = datetime.now(UTC)
    escritura.guardar_avisos(conn, ahora, [Aviso("carga_sin_tarea", "error", "Cargador distinto del plan", "x", "r",
                                                 ahora - timedelta(minutes=2))], origen="muestreo")
    escritura.guardar_avisos(conn, ahora, [Aviso("tarea_modificada:3", "aviso", "Tarea modificada", "y", "r")])
    escritura.guardar_avisos(conn, ahora, [])  # ciclo del reconciliador sin avisos
    filas = {c: (correo, desde) for c, correo, desde in conn.execute("SELECT clave, correo, desde FROM v_aviso"
                                                                     " WHERE accion = 'plan'")}
    assert set(filas) == {"carga_sin_tarea"}
    assert filas["carga_sin_tarea"][0] is True
    assert filas["carga_sin_tarea"][1] == ahora - timedelta(minutes=2)
    escritura.guardar_avisos(conn, ahora, [], origen="muestreo")
    assert not conn.execute("SELECT 1 FROM v_aviso WHERE clave = 'carga_sin_tarea'").fetchall()


# --- Avisos nuevos en SQL ------------------------------------------------------------------------------


def test_planificador_parado(conn: psycopg.Connection, limpio: None) -> None:
    t = datetime(2026, 11, 19, 9, tzinfo=UTC)
    assert "planificador_parado" not in claves(conn, t)  # el planificador no ha funcionado nunca
    ciclo(conn, t)
    assert "planificador_parado" not in claves(conn, t + timedelta(minutes=60))  # aviso_planificador_min = 60
    nivel, detalle = claves(conn, t + timedelta(minutes=61))["planificador_parado"]
    assert nivel == "error" and "19/11 10:00" in detalle
    ciclo(conn, None)
    assert "planificador_parado" in claves(conn, t)


def test_precios_del_dia_siguiente(conn: psycopg.Connection, limpio: None) -> None:
    hoy = date(2026, 11, 19)
    precios(conn, hoy, hoy)  # solo los de hoy
    ciclo(conn, local(hoy, 13, 55))
    assert not any(c.startswith("precios_sin_publicar") for c in claves(conn, local(hoy, 13, 59)))  # 13:30 + 30
    nivel, detalle = claves(conn, local(hoy, 14))["precios_sin_publicar:2026-11-20"]
    assert nivel == "aviso" and "hay 0 de 96" in detalle
    precios(conn, hoy + timedelta(days=1), hoy + timedelta(days=1))
    assert not any(c.startswith("precios_sin_publicar") for c in claves(conn, local(hoy, 14)))
    # Cambio de hora: el domingo 25/10/2026 tiene 25 horas.
    assert "hay 0 de 100" in claves(conn, local(date(2026, 10, 24), 15))["precios_sin_publicar:2026-10-25"][1]


def _plan(conn: psycopg.Connection, ahora: datetime, *, conectado: bool, prudente: bool,
          salidas: list[tuple[datetime, bool]]) -> None:
    ident = conn.execute(
        "INSERT INTO plan_calculo (ahora, motivo, modo, prudente, conectado, capacidad_kwh, horizonte_fin, duracion_ms)"
        " VALUES (%s, 'test', 'sombra', %s, %s, 60, %s, 1) RETURNING id",
        (ahora, prudente, conectado, ahora + timedelta(hours=72))).fetchone()[0]  # type: ignore[index]
    for salida, cubierta in salidas:
        conn.execute("INSERT INTO plan_salida (calculo_id, salida, tipo, objetivo_kwh, consumo_previsto_kwh,"
                     " necesaria_kwh, nivel_salida_kwh, cubierta, salto_umbral)"
                     " VALUES (%s, %s, 'horario', 40, 10, 20, 31.25, %s, false)", (ident, salida, cubierta))


def test_salida_no_cubierta_solo_con_el_coche_enchufado(conn: psycopg.Connection, limpio: None) -> None:
    hoy = date(2026, 11, 19)
    ahora = local(hoy, 22)
    s1, s2, s3 = local(hoy + timedelta(days=1), 9), local(hoy + timedelta(days=2), 9), local(hoy + timedelta(days=3), 9)
    _plan(conn, ahora, conectado=True, prudente=False, salidas=[(s1, False), (s2, True), (s3, False)])
    avisos = {c: v for c, v in claves(conn, ahora).items() if c.startswith("salida_no_cubierta")}
    assert set(avisos) == {f"salida_no_cubierta:{int(s1.timestamp())}", f"salida_no_cubierta:{int(s3.timestamp())}"}
    assert "Salida del 20/11 09:00: objetivo 40.0 kWh, el plan llega a 31.3 kWh" in avisos[
        f"salida_no_cubierta:{int(s1.timestamp())}"][1]
    # Modo prudente: solo se planifica la próxima.
    _plan(conn, ahora + timedelta(minutes=15), conectado=True, prudente=True, salidas=[(s1, False), (s2, False)])
    assert [c for c in claves(conn, ahora + timedelta(minutes=15)) if c.startswith("salida_no_cubierta")] == [
        f"salida_no_cubierta:{int(s1.timestamp())}"]
    # Coche fuera: se planifica al volver, sin aviso.
    _plan(conn, ahora + timedelta(minutes=30), conectado=False, prudente=False, salidas=[(s1, False)])
    assert not any(c.startswith("salida_no_cubierta") for c in claves(conn, ahora + timedelta(minutes=30)))


# --- Vigilancia con base sembrada ----------------------------------------------------------------------


@pytest.fixture
def como_grafana(conn: psycopg.Connection, nombre_bd: str):  # type: ignore[no-untyped-def]
    db.configurar_rol_grafana(conn, "clave-de-test")

    def conectar() -> psycopg.Connection:
        return psycopg.connect(dbname=nombre_bd, user="grafana_ro", password="clave-de-test",
                               host=os.environ["PGHOST"], autocommit=True)
    return conectar


def test_vigilancia_nuevo_sin_cambios_resuelto(conn: psycopg.Connection, limpio: None, como_grafana,  # type: ignore[no-untyped-def]
                                               tmp_path: Path) -> None:
    estado = tmp_path / "vigilancia.json"
    url = "https://nas.ejemplo:8421"
    ahora = datetime.now(UTC)
    ciclo(conn, ahora - timedelta(hours=2))  # parado
    codigo, texto = vigilancia.pasada(estado, url, ahora, como_grafana)
    assert codigo == 1
    assert "Nuevos" in texto and "ERROR · Planificador parado · desde" in texto
    assert "https://nas.ejemplo:8421/plan" in texto
    assert "Último ciclo correcto del planificador" in texto
    assert "clave-de-test" not in texto and "grafana_ro" not in texto

    assert vigilancia.pasada(estado, url, ahora + timedelta(minutes=15), como_grafana) == (0, "")  # sin cambios

    ciclo(conn, datetime.now(UTC))  # vuelve a funcionar
    assert vigilancia.pasada(estado, url, ahora + timedelta(minutes=30), como_grafana) == (0, "")  # falta una vez
    codigo, texto = vigilancia.pasada(estado, url, ahora + timedelta(minutes=45), como_grafana)
    assert codigo == 1 and "Resueltos (1):" in texto and "Planificador parado" in texto


def test_vigilancia_base_inaccesible(conn: psycopg.Connection, limpio: None, como_grafana,  # type: ignore[no-untyped-def]
                                     tmp_path: Path) -> None:
    estado = tmp_path / "vigilancia.json"
    ahora = datetime.now(UTC)
    ciclo(conn, ahora - timedelta(hours=2))
    assert vigilancia.pasada(estado, None, ahora, como_grafana)[0] == 1

    def caida() -> psycopg.Connection:
        return psycopg.connect(dbname="no_existe_vigilancia", host=os.environ["PGHOST"], user="grafana_ro",
                               password="clave-de-test", connect_timeout=5)

    codigo, texto = vigilancia.pasada(estado, None, ahora + timedelta(minutes=15), caida)
    assert codigo == 1 and "Base de datos no accesible" in texto and "Planificador parado" not in texto
    assert "no_existe_vigilancia" not in texto and "clave-de-test" not in texto
    assert vigilancia.pasada(estado, None, ahora + timedelta(minutes=30), caida) == (0, "")
    # Vuelve: el planificador sigue parado (no se repite) y la base se da por resuelta tras dos pasadas.
    assert vigilancia.pasada(estado, None, ahora + timedelta(minutes=45), como_grafana) == (0, "")
    codigo, texto = vigilancia.pasada(estado, None, ahora + timedelta(minutes=60), como_grafana)
    assert codigo == 1 and "Base de datos no accesible" in texto and "Resueltos (1):" in texto
