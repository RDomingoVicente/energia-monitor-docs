"""Refresco de mv_precio_final: cuándo se llama y qué devuelve (sin base de datos)."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from energia_monitor import __main__ as cli
from energia_monitor import colector, db, esios, estimacion, planificador
from energia_monitor.config import Config
from energia_monitor.db import Refresco


@contextmanager
def conexion_falsa(**_: object) -> Any:
    yield SimpleNamespace()


@pytest.fixture
def refrescos(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    llamadas: list[object] = []

    def refrescar(conn: object) -> Refresco:
        llamadas.append(conn)
        return Refresco(10, 10, 0.1)

    monkeypatch.setattr(db, "conectar", conexion_falsa)
    monkeypatch.setattr(db, "refrescar_precio_final", refrescar)
    return llamadas


# --- db.refrescar_precio_final ------------------------------------------------------------


class ConexionContadora:
    def __init__(self, filas: int, esperadas: int) -> None:
        self.sentencias: list[str] = []
        self.cuenta = (filas, esperadas)

    def execute(self, sentencia: str) -> SimpleNamespace:
        self.sentencias.append(sentencia)
        return SimpleNamespace(fetchone=lambda: self.cuenta)


def test_refresco_concurrente_con_filas_esperadas(caplog: pytest.LogCaptureFixture) -> None:
    conn = ConexionContadora(400, 400)
    with caplog.at_level(logging.INFO, logger="energia_monitor.db"):
        r = db.refrescar_precio_final(conn)  # type: ignore[arg-type]
    assert conn.sentencias[0] == "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_precio_final"
    assert r.completo and (r.filas, r.esperadas) == (400, 400)
    assert "400 filas (esperadas 400)" in caplog.text


def test_refresco_avisa_si_faltan_filas(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="energia_monitor.db"):
        r = db.refrescar_precio_final(ConexionContadora(396, 400))  # type: ignore[arg-type]
    assert not r.completo
    assert [x.levelno for x in caplog.records] == [logging.WARNING]
    assert "396 filas, pero se esperaban 400" in caplog.text


# --- CLI -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("refresco", "codigo"), [(Refresco(8, 8, 0.0), 0), (Refresco(7, 8, 0.0), 1)])
def test_refrescar_devuelve_1_si_las_filas_no_coinciden(monkeypatch: pytest.MonkeyPatch, refresco: Refresco,
                                                        codigo: int) -> None:
    monkeypatch.setattr(db, "conectar", conexion_falsa)
    monkeypatch.setattr(db, "refrescar_precio_final", lambda conn: refresco)
    assert cli.main(["refrescar"]) == codigo


def test_refrescar_con_error_devuelve_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def falla(conn: object) -> Refresco:
        raise RuntimeError("sin materializada")

    monkeypatch.setattr(db, "conectar", conexion_falsa)
    monkeypatch.setattr(db, "refrescar_precio_final", falla)
    assert cli.main(["refrescar"]) == 1


def test_esios_estimar_refresca(monkeypatch: pytest.MonkeyPatch, refrescos: list[object]) -> None:
    monkeypatch.setattr(estimacion, "actualizar", lambda conn, hoy: None)
    assert cli.main(["esios", "estimar"]) == 0
    assert len(refrescos) == 1


def test_fallo_del_refresco_tras_estimar_devuelve_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def falla(conn: object) -> Refresco:
        raise RuntimeError("bloqueo")

    monkeypatch.setattr(db, "conectar", conexion_falsa)
    monkeypatch.setattr(db, "refrescar_precio_final", falla)
    monkeypatch.setattr(estimacion, "actualizar", lambda conn, hoy: None)
    assert cli.main(["esios", "estimar"]) == 1


@pytest.mark.parametrize("fallidos", [0, 2])
def test_recargar_refresca(monkeypatch: pytest.MonkeyPatch, refrescos: list[object], fallidos: int) -> None:
    monkeypatch.setattr(cli, "crear_fuente", lambda nombre, intervalo: SimpleNamespace(nombre="omie"))
    monkeypatch.setattr(colector, "dias_incompletos", lambda conn, fuente, desde, hasta: [])
    monkeypatch.setattr(colector, "descargar_dias",
                        lambda conn, fuente, dias, etiqueta, stop: SimpleNamespace(fallidos=fallidos))
    assert cli.main(["recargar", "--desde", "2026-11-01", "--hasta", "2026-11-02"]) == (1 if fallidos else 0)
    assert len(refrescos) == 1


def test_esios_recargar_refresca(monkeypatch: pytest.MonkeyPatch, refrescos: list[object]) -> None:
    monkeypatch.setattr(cli, "crear_cliente_esios", lambda token, intervalo: object())
    monkeypatch.setattr(esios, "indicadores", lambda conn: [1001])
    monkeypatch.setattr(esios, "descargar", lambda *a, **k: SimpleNamespace(fallidos=0))
    monkeypatch.setattr(esios, "comprobar_controles", lambda conn, desde, hasta: None)
    assert cli.main(["esios", "recargar", "--desde", "2026-11-01", "--hasta", "2026-11-02"]) == 0
    assert len(refrescos) == 1


# --- Planificador ------------------------------------------------------------------------


class Parada:
    """Sustituto de threading.Event: deja pasar `ciclos` esperas sin dormir."""

    def __init__(self, ciclos: int) -> None:
        self.restantes = ciclos

    def is_set(self) -> bool:
        return self.restantes <= 0

    def wait(self, _segundos: float) -> bool:
        self.restantes -= 1
        return self.restantes < 0


@pytest.fixture
def planificador_sin_bd(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    eventos: list[str] = []
    monkeypatch.setattr(planificador, "preparar_bd", lambda conn, config: eventos.append("migrar"))
    monkeypatch.setattr(colector, "completar_desde", lambda *a: eventos.append("carga inicial"))
    monkeypatch.setattr(colector, "actualizar_hoy_y_manana", lambda *a: eventos.append("omie"))
    return eventos


@pytest.mark.parametrize("ciclos", [0, 1, 3])
def test_planificador_refresca_al_arrancar_y_tras_cada_ciclo(monkeypatch: pytest.MonkeyPatch,
                                                            planificador_sin_bd: list[str], ciclos: int) -> None:
    monkeypatch.setattr(db, "refrescar_precio_final", lambda conn: planificador_sin_bd.append("refresco"))
    planificador.ejecutar(Config.desde_entorno(), SimpleNamespace(), Parada(ciclos),  # type: ignore[arg-type]
                          conectar=conexion_falsa, reloj=lambda: datetime(2026, 11, 14, 10, tzinfo=UTC))
    assert planificador_sin_bd == ["migrar", "carga inicial", "refresco"] + ["omie", "refresco"] * ciclos


def test_planificador_sigue_si_falla_el_refresco(monkeypatch: pytest.MonkeyPatch, planificador_sin_bd: list[str],
                                                 caplog: pytest.LogCaptureFixture) -> None:
    def falla(conn: object) -> None:
        planificador_sin_bd.append("refresco")
        raise RuntimeError("bloqueo")

    monkeypatch.setattr(db, "refrescar_precio_final", falla)
    planificador.ejecutar(Config.desde_entorno(), SimpleNamespace(), Parada(2),  # type: ignore[arg-type]
                          conectar=conexion_falsa, reloj=lambda: datetime(2026, 11, 14, 10, tzinfo=UTC))
    assert planificador_sin_bd.count("omie") == 2 and planificador_sin_bd.count("refresco") == 3
    assert "fallo al refrescar mv_precio_final" in caplog.text
