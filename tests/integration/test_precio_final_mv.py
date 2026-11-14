"""mv_precio_final (0012): copia exacta de v_precio_final tras el refresco y
vistas que la leen con los mismos resultados que sus versiones anteriores."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import psycopg
import pytest

from energia_monitor import __main__ as cli
from energia_monitor import db, muestreo
from energia_monitor.energia import Muestra
from energia_monitor.tiempo import limites_dia_utc
from energia_monitor.viaris import Sesion

MIGRACIONES = Path(__file__).resolve().parents[2] / "db" / "migrations"
FUENTE = "mv_test"
QH = timedelta(minutes=15)
DIA_CAMBIO_HORA = date(2026, 10, 25)  # 25 horas en Madrid


def limpiar(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FUENTE,))
    conn.execute("DELETE FROM cargador_muestra")
    conn.execute("DELETE FROM cargador_sesion")
    conn.execute("DELETE FROM serie_esios WHERE inicio >= '2026-10-23Z' AND inicio < '2026-10-26Z'")
    db.refrescar_precio_final(conn)


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    limpiar(conn)
    yield
    limpiar(conn)


def mercado(conn: psycopg.Connection, dia: date) -> None:
    inicio, fin = limites_dia_utc(dia)
    filas, t, i = [], inicio, 0
    while t < fin:
        filas.append((t, t + QH, FUENTE, D(35) + D(i % 11) * D("6.5")))
        t += QH
        i += 1
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh) VALUES (%s, %s, %s, %s)",
                        filas)


def pvpc_horario(conn: psycopg.Connection, dia: date) -> None:
    indicador, geo = conn.execute("SELECT id, geo_id FROM esios_indicador WHERE uso = 'pvpc'").fetchone()  # type: ignore[misc]
    inicio, fin = limites_dia_utc(dia)
    filas, h = [], inicio
    while h < fin:
        valor = D(120) + D(h.hour) * D("3.75")
        filas.append((indicador, geo, h, h + timedelta(hours=1), valor, h, valor))
        h += timedelta(hours=1)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s)", filas)


def muestras_cada_minuto(conn: psycopg.Connection, desde: datetime, hasta: datetime) -> None:
    filas, t, i = [], desde, 0
    while t <= hasta:
        m = Muestra(t, None if i == 0 else 60, D(4000) + D(i % 7) * 50, D(300), D(4300) + D(i % 7) * 50)
        filas.append((m.instante, m.intervalo_s, m.evse_w, m.home_w, m.total_w))
        t += timedelta(minutes=1)
        i += 1
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w)"
                        " VALUES (%s, %s, %s, %s, %s)", filas)


def sesion(conn: psycopg.Connection, inicio: datetime, fin: datetime, wh: str) -> None:
    ident = int(inicio.timestamp())
    muestreo.guardar_sesiones(conn, [Sesion(ident, "mennekes", inicio, fin, D(wh), "schedman", "evsm", f"{ident}")])


def diferencias(conn: psycopg.Connection, a: str, b: str, columnas: str = "*") -> tuple[int, int]:
    """Filas de a que no están en b y de b que no están en a."""
    n_ab = conn.execute(f"SELECT count(*) FROM (SELECT {columnas} FROM {a} EXCEPT ALL"  # type: ignore[arg-type]
                        f" SELECT {columnas} FROM {b}) d").fetchone()
    n_ba = conn.execute(f"SELECT count(*) FROM (SELECT {columnas} FROM {b} EXCEPT ALL"  # type: ignore[arg-type]
                        f" SELECT {columnas} FROM {a}) d").fetchone()
    return n_ab[0], n_ba[0]  # type: ignore[index]


def vista_anterior(conn: psycopg.Connection, migracion: str, vista: str) -> str:
    """Crea una vista temporal con la definición de `vista` en una migración publicada."""
    texto = (MIGRACIONES / migracion).read_text(encoding="utf-8")
    m = re.search(rf"CREATE VIEW {vista} AS\n(.*?);\n\nCOMMENT ON VIEW {vista}", texto, re.S)
    assert m, f"{vista} no encontrada en {migracion}"
    nombre = f"{vista}_anterior"
    conn.execute(f"CREATE OR REPLACE TEMP VIEW {nombre} AS {m.group(1)}")  # type: ignore[arg-type]
    return nombre


# --- Copia exacta -----------------------------------------------------------------------


def test_materializada_igual_a_la_vista_tras_el_refresco(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, DIA_CAMBIO_HORA)
    # Sin refrescar, la materializada no tiene los tramos nuevos.
    assert diferencias(conn, "v_precio_final", "mv_precio_final")[0] == 100 * conn.execute(
        "SELECT count(*) FROM contrato").fetchone()[0]  # type: ignore[index]
    refresco = db.refrescar_precio_final(conn)
    assert refresco.completo and refresco.filas == conn.execute("SELECT count(*) FROM v_precio_final").fetchone()[0]  # type: ignore[index]
    assert diferencias(conn, "v_precio_final", "mv_precio_final") == (0, 0)
    # Las bajas también se propagan.
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s AND inicio = '2026-10-25 01:00Z'", (FUENTE,))
    assert db.refrescar_precio_final(conn).completo
    assert diferencias(conn, "v_precio_final", "mv_precio_final") == (0, 0)


def test_columnas_e_indices(conn: psycopg.Connection) -> None:
    columnas = """SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute
                  WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum"""
    assert (conn.execute(columnas, ("mv_precio_final",)).fetchall()
            == conn.execute(columnas, ("v_precio_final",)).fetchall())
    indices = {r[0]: r[1] for r in conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'mv_precio_final'")}
    assert "UNIQUE" in indices["mv_precio_final_clave"] and "(contrato, fuente, inicio)" in indices["mv_precio_final_clave"]
    assert "date_bin" in indices["mv_precio_final_hora"]


def test_cli_parametro_de_contrato_refresca(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "conectar", lambda **_: psycopg.connect(dbname=nombre_bd, autocommit=True))
    dia = date(2030, 1, 2)
    mercado(conn, dia)
    assert cli.main(["refrescar"]) == 0
    original = conn.execute("SELECT vigencia FROM contrato_parametro WHERE contrato = 'vivienda' AND clave = 'iva'"
                            " AND vigencia @> %s", (dia,)).fetchone()[0]  # type: ignore[index]
    try:
        assert cli.main(["parametro", "registrar", "--clave", "iva", "--desde", "2030-01-01", "--valor", "0.5",
                         "--origen", "regulado", "--fuente", "test"]) == 0
        ivas = {r[0] for r in conn.execute(
            "SELECT iva FROM mv_precio_final WHERE contrato = 'vivienda' AND fuente = %s", (FUENTE,))}
        assert ivas == {D("0.5")}
        assert diferencias(conn, "v_precio_final", "mv_precio_final") == (0, 0)
    finally:
        conn.execute("DELETE FROM contrato_parametro WHERE contrato = 'vivienda' AND clave = 'iva' AND fuente = 'test'")
        conn.execute("UPDATE contrato_parametro SET vigencia = %s WHERE contrato = 'vivienda' AND clave = 'iva'"
                     " AND lower(vigencia) = lower(%s::daterange)", (original, original))


def test_cli_parametro_del_cargador_no_refresca(monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection,
                                                nombre_bd: str) -> None:
    monkeypatch.setattr(db, "conectar", lambda **_: psycopg.connect(dbname=nombre_bd, autocommit=True))
    llamadas: list[object] = []
    monkeypatch.setattr(db, "refrescar_precio_final", llamadas.append)
    try:
        assert cli.main(["parametro", "registrar", "--ambito", "cargador", "--clave", "factor_contador",
                         "--desde", "2030-01-01", "--valor", "0.97", "--origen", "estimado", "--fuente", "test"]) == 0
    finally:
        conn.execute("DELETE FROM cargador_parametro WHERE fuente = 'test'")
        conn.execute("UPDATE cargador_parametro SET vigencia = daterange(lower(vigencia), NULL, '[)')")
    assert llamadas == []


def test_grafana_ro_lee_la_materializada(conn: psycopg.Connection, nombre_bd: str) -> None:
    assert conn.execute("SELECT has_table_privilege('grafana_ro', 'mv_precio_final', 'SELECT')").fetchone() == (True,)
    db.configurar_rol_grafana(conn, "clave-de-test")
    with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password="clave-de-test",
                         host=os.environ["PGHOST"], autocommit=True) as ro:
        ro.execute("SELECT * FROM mv_precio_final LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("REFRESH MATERIALIZED VIEW mv_precio_final")


# --- Mismos resultados que las definiciones anteriores --------------------------------------


@pytest.fixture
def dia_con_carga(conn: psycopg.Connection, limpio: None) -> None:
    """Día del cambio de hora con precios, PVPC horario, dos sesiones medidas
    (una cruza la hora repetida) y una estimada."""
    for dia in (DIA_CAMBIO_HORA - timedelta(days=1), DIA_CAMBIO_HORA):
        mercado(conn, dia)
        pvpc_horario(conn, dia)
    a = datetime(2026, 10, 24, 23, 37, 10, tzinfo=UTC)  # 01:37 CEST: cruza 02:00-03:00 dos veces
    muestras_cada_minuto(conn, a, a + timedelta(hours=3, minutes=10))
    sesion(conn, a + timedelta(seconds=30), a + timedelta(hours=3), "12800")
    sesion(conn, datetime(2026, 10, 25, 9, 5, tzinfo=UTC), datetime(2026, 10, 25, 10, 20, tzinfo=UTC), "3300")
    sesion(conn, datetime(2026, 10, 24, 3, 0, tzinfo=UTC), datetime(2026, 10, 24, 8, 0, tzinfo=UTC), "9000")
    db.refrescar_precio_final(conn)


def test_coste_por_tramo_igual_que_en_0011(conn: psycopg.Connection, dia_con_carga: None) -> None:
    anterior = vista_anterior(conn, "0011_energia_coste.sql", "v_coste_tramo")
    filas = conn.execute("SELECT count(*), count(coste_pvpc_eur), count(DISTINCT origen_energia)"
                         " FROM v_coste_tramo WHERE fuente = %s", (FUENTE,)).fetchone()
    assert filas is not None and filas[0] > 0 and filas[1] == filas[0] and filas[2] == 2
    assert diferencias(conn, "v_coste_tramo", anterior) == (0, 0)


def test_coste_por_sesion_igual_que_en_0011(conn: psycopg.Connection, dia_con_carga: None) -> None:
    anterior = vista_anterior(conn, "0011_energia_coste.sql", "v_sesion_coste")
    assert conn.execute("SELECT count(DISTINCT id) FROM v_sesion_coste WHERE fuente = %s",
                        (FUENTE,)).fetchone() == (3,)
    assert diferencias(conn, "v_sesion_coste", anterior) == (0, 0)


def test_comparacion_pvpc_igual_que_en_0009(conn: psycopg.Connection, dia_con_carga: None) -> None:
    anterior = vista_anterior(conn, "0009_esios.sql", "v_comparacion_pvpc")
    inicio, fin = limites_dia_utc(DIA_CAMBIO_HORA)
    assert conn.execute("SELECT count(*) FROM v_comparacion_pvpc WHERE contrato = 'vivienda' AND fuente = %s"
                        " AND inicio >= %s AND inicio < %s", (FUENTE, inicio, fin)).fetchone() == (25,)
    assert diferencias(conn, "v_comparacion_pvpc", anterior) == (0, 0)


def test_pvpc_de_la_hora_utc_en_el_cambio_de_hora(conn: psycopg.Connection, dia_con_carga: None) -> None:
    filas = conn.execute(
        "SELECT c.inicio, c.pvpc_final_eur_kwh, s.valor / 1000 * (1 + c_iva.iee) * (1 + c_iva.iva)"
        " FROM v_coste_tramo c"
        " JOIN serie_esios s ON s.inicio = date_trunc('hour', c.inicio AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
        " JOIN esios_indicador i ON i.id = s.indicador AND i.geo_id = s.geo_id AND i.uso = 'pvpc'"
        " CROSS JOIN LATERAL (SELECT impuesto_electrico AS iee, iva FROM mv_precio_final p"
        "   WHERE p.contrato = c.contrato AND p.fuente = c.fuente AND p.inicio = c.inicio) c_iva"
        " WHERE c.fuente = %s AND c.contrato = 'vivienda' AND c.consumidor = 'coche'"
        "   AND c.inicio >= '2026-10-25 00:00Z' AND c.inicio < '2026-10-25 01:00Z'", (FUENTE,)).fetchall()
    # 00:00-01:00 UTC es la primera de las dos 02:00-03:00 de Madrid: 4 tramos, cada uno con su hora UTC.
    assert len(filas) == 4
    assert all(pvpc == esperado for _, pvpc, esperado in filas)
