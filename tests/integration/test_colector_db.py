from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest

from energia_monitor import colector, planificador
from energia_monitor.config import Config
from energia_monitor.sources.base import PreciosDia
from energia_monitor.sources.omie import parsear_marginalpdbc

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "omie"


class FuenteOctubre:
    """Sirve los ficheros reales de octubre de 2026; el resto, sin publicar."""

    nombre = "prueba_colector"

    def __init__(self, parar_en: date | None = None, stop: threading.Event | None = None) -> None:
        self.pedidos: list[date] = []
        self.parar_en = parar_en
        self.stop = stop

    def precios_dia(self, dia: date) -> PreciosDia | None:
        self.pedidos.append(dia)
        if self.stop is not None and dia == self.parar_en:
            self.stop.set()
        fichero = FIXTURES / f"marginalpdbc_{dia:%Y%m%d}.1"
        if not fichero.exists():
            return None
        tramos = parsear_marginalpdbc(fichero.read_text(encoding="latin-1"), dia)
        return PreciosDia(dia=dia, fuente=self.nombre, version=1, tramos=tramos)


@pytest.fixture
def limpio(conn: psycopg.Connection):
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FuenteOctubre.nombre,))
    yield
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FuenteOctubre.nombre,))


def test_dias_incompletos(conn: psycopg.Connection, limpio: None) -> None:
    fuente = FuenteOctubre()
    for d in (24, 25, 26):  # el 25 tiene 100 tramos (cambio de hora)
        precios = fuente.precios_dia(date(2026, 10, d))
        assert precios is not None
        if d == 26:
            precios = PreciosDia(precios.dia, precios.fuente, 1, precios.tramos[:-1])  # falta un tramo
        colector.guardar_precios(conn, precios)
    faltan = colector.dias_incompletos(conn, fuente.nombre, date(2026, 10, 23), date(2026, 10, 27))
    assert faltan == [date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27)]


def test_arranque_con_tabla_vacia_y_despues(conn: psycopg.Connection, limpio: None) -> None:
    fuente = FuenteOctubre()
    hoy = date(2026, 10, 30)

    primera = colector.completar_desde(conn, fuente, date(2026, 10, 1), hoy)
    assert len(primera.guardados) == 31
    assert colector.dias_incompletos(conn, fuente.nombre, date(2026, 10, 1), date(2026, 10, 31)) == []

    # Segundo arranque: nada que descargar.
    fuente.pedidos.clear()
    segunda = colector.completar_desde(conn, fuente, date(2026, 10, 1), hoy)
    assert fuente.pedidos == []
    assert segunda.guardados == []

    # Día siguiente: hoy completo y mañana (01/11) sin publicar.
    tercera = colector.actualizar_hoy_y_manana(conn, fuente, date(2026, 10, 31))
    assert fuente.pedidos == [date(2026, 11, 1)]
    assert tercera.no_publicados == [date(2026, 11, 1)]


def test_ejecutar_arranca_y_para(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATIONS_DIR", str(RAIZ / "db" / "migrations"))
    stop = threading.Event()
    fuente = FuenteOctubre(parar_en=date(2026, 10, 31), stop=stop)
    config = Config(carga_inicial_desde=date(2026, 10, 1), grafana_db_password="otra-clave")

    planificador.ejecutar(
        config,
        fuente,
        stop,
        conectar=lambda: psycopg.connect(dbname=nombre_bd, autocommit=True),
        reloj=lambda: datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
    )

    assert fuente.pedidos == [date(2026, 10, d) for d in range(1, 32)]
    n = conn.execute("SELECT count(*) FROM precio_mercado WHERE fuente = %s", (fuente.nombre,)).fetchone()
    assert n == (2980,)
    assert conn.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'grafana_ro'").fetchone() == (True,)
