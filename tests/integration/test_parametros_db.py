from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import psycopg
import pytest

from energia_monitor.parametros import ErrorParametro, listar_parametros, registrar_parametro


@pytest.fixture
def contrato_param(conn: psycopg.Connection):
    conn.execute("INSERT INTO contrato (id, descripcion, tarifa_acceso) VALUES ('param', 'test', '2.0TD')")
    yield "param"
    conn.execute("DELETE FROM contrato_parametro WHERE contrato = 'param'")
    conn.execute("DELETE FROM contrato WHERE id = 'param'")


def vigencias(conn: psycopg.Connection, clave: str = "ssaa") -> list[tuple[str, D]]:
    return conn.execute(
        "SELECT vigencia::text, valor FROM contrato_parametro WHERE contrato = 'param' AND clave = %s"
        " ORDER BY vigencia",
        (clave,),
    ).fetchall()


def registrar(conn: psycopg.Connection, desde: date, valor: str, clave: str = "ssaa", periodo: str = "*",
              origen: str = "estimado"):
    return registrar_parametro(conn, contrato="param", clave=clave, periodo=periodo, desde=desde,
                               valor=D(valor), origen=origen, fuente="test")


def test_registrar_cierra_la_vigencia_anterior(conn: psycopg.Connection, contrato_param: str) -> None:
    primero = registrar(conn, date(2026, 11, 1), "31.5")
    assert primero.cerrada is None and primero.nueva == "[2026-11-01,)"

    segundo = registrar(conn, date(2027, 1, 1), "28")
    assert segundo.cerrada == "[2026-11-01,)"
    assert vigencias(conn) == [("[2026-11-01,2027-01-01)", D("31.5")), ("[2027-01-01,)", D("28"))]


def test_registrar_en_medio_respeta_la_fila_posterior(conn: psycopg.Connection, contrato_param: str) -> None:
    registrar(conn, date(2027, 1, 1), "28")
    registrar(conn, date(2026, 11, 1), "31.5")  # anterior a todas: termina donde empieza la siguiente
    registrar(conn, date(2026, 12, 1), "30")
    assert vigencias(conn) == [
        ("[2026-11-01,2026-12-01)", D("31.5")),
        ("[2026-12-01,2027-01-01)", D("30")),
        ("[2027-01-01,)", D("28")),
    ]


def test_registrar_misma_fecha_se_rechaza(conn: psycopg.Connection, contrato_param: str) -> None:
    registrar(conn, date(2026, 11, 1), "31.5")
    with pytest.raises(ErrorParametro, match="ya hay un valor"):
        registrar(conn, date(2026, 11, 1), "40")
    assert vigencias(conn) == [("[2026-11-01,)", D("31.5"))]


def test_registrar_valida_periodo_con_el_trigger(conn: psycopg.Connection, contrato_param: str) -> None:
    with pytest.raises(psycopg.errors.RaiseException):
        registrar(conn, date(2026, 11, 1), "0.03", clave="peaje", periodo="*")
    assert vigencias(conn, "peaje") == []


def test_listar(conn: psycopg.Connection, contrato_param: str) -> None:
    registrar(conn, date(2026, 11, 1), "31.5")
    filas = listar_parametros(conn, "param", date(2026, 11, 12))
    assert filas == [("ssaa", "*", D("31.5"), "EUR/MWh", "estimado", "[2026-11-01,)", "test")]


def test_registrar_origen_no_valido(conn: psycopg.Connection, contrato_param: str) -> None:
    with pytest.raises(ErrorParametro, match="origen no válido"):
        registrar(conn, date(2026, 11, 1), "31.5", origen="norma")
    assert vigencias(conn) == []
