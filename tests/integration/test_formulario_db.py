"""Formulario contra PostgreSQL, con el rol formulario_rw: Inicio, lectura (PRG y 422), 503 y tiempos."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, time as hora, timedelta

import psycopg
import pytest
from escenarios_bateria import ESCENARIOS
from test_bateria_db import cargar, limpiar

from energia_monitor import db
from energia_monitor.formulario.app import App, ConfigApp, Peticion

HOST = "formulario.test:8421"
CAB = {"host": HOST, "origin": f"https://{HOST}"}
CLAVE = "clave-formulario-tests"


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    limpiar(conn)
    db.configurar_rol_formulario(conn, CLAVE)
    yield
    limpiar(conn)


def app_rw(nombre_bd: str, ahora: datetime) -> App:
    def conectar() -> psycopg.Connection:
        return psycopg.connect(dbname=nombre_bd, user="formulario_rw", password=CLAVE, host=os.environ["PGHOST"],
                               autocommit=True)
    return App(ConfigApp(HOST, cargador="viaris 192.168.1.34"), conectar, lambda: ahora)


def escenario(conn: psycopg.Connection, nombre: str) -> datetime:
    e, ahora = ESCENARIOS[nombre]()
    for dia in range(1, 6):
        e.horario[dia] = hora(9, 0)
    cargar(conn, e)
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    return ahora


def texto(cuerpo: bytes) -> str:
    html = cuerpo.decode()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<svg.*?</svg>", "", html)))


@pytest.mark.parametrize(("nombre", "etiqueta"), [
    ("confianza_leido", "LEÍDO"), ("confianza_leido_carga", "LEÍDO + CARGA"),
    ("confianza_estimado_tras_trayecto", "ESTIMADO"), ("coche_fuera", "ESTIMADO")])
def test_inicio_por_estado(conn: psycopg.Connection, nombre_bd: str, limpio: None, nombre: str, etiqueta: str) -> None:
    ahora = escenario(conn, nombre)
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/", CAB))
    assert r.estado == 200, texto(r.cuerpo)
    t = texto(r.cuerpo)
    assert f"Nivel {etiqueta} " in t
    assert "Próximos 7 días" in t and "Anotar lectura" in t


def test_inicio_sin_lecturas(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = datetime(2026, 11, 16, 12, tzinfo=UTC)
    t = texto(app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/", CAB)).cuerpo)
    assert "Aún no hay nivel: anota el % que marca el coche." in t
    assert "Cargador no accesible" not in t  # el aviso va al contador, no al texto de Inicio
    assert "aviso" in t  # chip «N avisos»


def test_lectura_prg_y_mensaje_desde_la_bd(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_estimado_tras_trayecto")  # lun 16/11 10:00 en Madrid
    app = app_rw(nombre_bd, ahora)
    r = app.manejar(Peticion("POST", "/lectura", CAB, b"pct=48&fecha=2026-11-16&hora=09:30"))
    assert r.estado == 303
    destino = r.cabeceras["Location"]
    assert re.fullmatch(r"/\?ok=lectura&id=\d+", destino)
    assert conn.execute("SELECT pct, origen, instante FROM bateria_lectura WHERE id = %s",
                        (int(destino.rsplit("=", 1)[1]),)).fetchone() == (
        48, "formulario", datetime(2026, 11, 16, 8, 30, tzinfo=UTC))
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert re.search(r"Lectura guardada: 48 % a las 09:30\. El modelo estimaba \d+ % \(diferencia \+\d+ pts\)", t)
    assert "Nivel LEÍDO 48 %" in t


@pytest.mark.parametrize("consulta", [
    "/?ok=Tu+coche+est%C3%A1+lleno&id=1",
    "/?ok=lectura&id=<script>",
    "/?ok=lectura&id=999999",
    "/?ok=otra&id=1",
    "/?ok=lectura&id=1&mensaje=Lectura+guardada%3A+99+%25",
])
def test_ok_arbitrario_no_se_muestra(conn: psycopg.Connection, nombre_bd: str, limpio: None, consulta: str) -> None:
    ahora = escenario(conn, "confianza_leido")
    conn.execute("UPDATE bateria_lectura SET anulada_en = now(), motivo = 'test'")  # el id 1 ya no está activo
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", consulta, CAB))  # aún sin recalcular: no falla
    assert r.estado == 200
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", consulta, CAB))
    t = texto(r.cuerpo)
    assert r.estado == 200
    assert "Lectura guardada" not in t and "lleno" not in t and "<script>" not in r.cuerpo.decode()


@pytest.mark.parametrize(("cuerpo", "campo", "mensaje"), [
    (b"pct=101&fecha=2026-11-16&hora=09:30", "pct", "Indica un porcentaje entero de 0 a 100."),
    (b"pct=50.5&fecha=2026-11-16&hora=09:30", "pct", "Indica un porcentaje entero de 0 a 100."),
    (b"pct=&fecha=2026-11-16&hora=09:30", "pct", "Indica un porcentaje entero de 0 a 100."),
    (b"pct=50&fecha=2026-03-29&hora=02:30", "hora", "no existe en Madrid"),
    (b"pct=50&fecha=2026-11-16&hora=", "hora", "Indica la fecha y la hora de la lectura."),
    (b"pct=50&fecha=2026-11-16&hora=11:00", "general", "La lectura no puede ser futura"),
])
def test_lectura_no_valida_422(conn: psycopg.Connection, nombre_bd: str, limpio: None, cuerpo: bytes, campo: str,
                               mensaje: str) -> None:
    ahora = escenario(conn, "confianza_leido")  # lun 16/11 05:00 UTC
    antes = conn.execute("SELECT count(*) FROM bateria_lectura").fetchone()
    r = app_rw(nombre_bd, ahora + timedelta(hours=5)).manejar(Peticion("POST", "/lectura", CAB, cuerpo))
    assert r.estado == 422
    html = r.cuerpo.decode()
    assert mensaje in html
    if campo != "general":
        assert f'aria-describedby="error-{campo}"' in html
    # Formulario relleno con lo enviado.
    fecha = re.search(rb"fecha=([\d-]+)", cuerpo).group(1).decode()  # type: ignore[union-attr]
    assert f'value="{fecha}"' in html
    assert conn.execute("SELECT count(*) FROM bateria_lectura").fetchone() == antes


def test_lectura_en_hora_ambigua_toma_la_primera(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = datetime(2026, 10, 26, 12, tzinfo=UTC)
    r = app_rw(nombre_bd, ahora).manejar(Peticion("POST", "/lectura", CAB, b"pct=50&fecha=2026-10-25&hora=02:30"))
    assert r.estado == 303
    assert conn.execute("SELECT instante FROM bateria_lectura").fetchone() == (
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),)  # 02:30 aún en horario de verano (UTC+2)


def test_migracion_pendiente_503(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, datetime.now(UTC))
    app.migracion_esperada = "9999"
    r = app.manejar(Peticion("GET", "/", CAB))
    assert r.estado == 503 and "Migración pendiente" in r.cuerpo.decode()
    assert app.manejar(Peticion("GET", "/salud", {})).estado == 503


def test_salud_como_formulario_rw(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    r = app_rw(nombre_bd, datetime.now(UTC)).manejar(Peticion("GET", "/salud", {}))
    assert r.estado == 200 and b'"estado": "ok"' in r.cuerpo


def test_paginas_por_debajo_de_300_ms(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_secuencia")
    app = app_rw(nombre_bd, ahora)
    app.manejar(Peticion("GET", "/", CAB))  # primera: planes en caché
    inicio = time.perf_counter()
    for _ in range(5):
        assert app.manejar(Peticion("GET", "/", CAB)).estado == 200
    assert (time.perf_counter() - inicio) / 5 < 0.3
