"""Consultas del dashboard de Grafana ejecutadas contra PostgreSQL.

Se usan las consultas tal como están en grafana/energia-precios.json: se
sustituyen las variables ($contrato, $fuente), la macro $__timeFilter y now()
por valores fijos, y se comprueba que la tabla de horas baratas coincide con
la curva de precio final.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import psycopg
import pytest

from energia_monitor.repository import guardar_precios
from energia_monitor.sources.base import PreciosDia
from energia_monitor.sources.omie import parsear_marginalpdbc

RAIZ = Path(__file__).resolve().parents[2]
DASHBOARD = json.loads((RAIZ / "grafana" / "energia-precios.json").read_text(encoding="utf-8"))
FIXTURES = RAIZ / "tests" / "fixtures" / "omie"
FUENTE = "dashboard"


def sql_panel(titulo: str) -> str:
    panel = next(p for p in DASHBOARD["panels"] if p["title"] == titulo)
    return panel["targets"][0]["rawSql"]


def preparar(sql: str, ahora: datetime, desde: datetime | None = None, hasta: datetime | None = None) -> str:
    """Sustituye lo que Grafana resolvería antes de enviar la consulta."""
    def lit(t: datetime) -> str:
        return f"'{t.isoformat()}'::timestamptz"

    sql = sql.replace("$contrato", "vivienda").replace("$fuente", FUENTE)
    sql = sql.replace("now()", lit(ahora))
    if desde is not None and hasta is not None:
        sql = sql.replace("$__timeFilter(inicio)", f"inicio BETWEEN {lit(desde)} AND {lit(hasta)}")
    assert "$" not in sql, sql
    return sql


@pytest.fixture
def octubre(conn: psycopg.Connection):
    for d in range(1, 32):
        dia = datetime(2026, 10, d).date()
        texto = (FIXTURES / f"marginalpdbc_{dia:%Y%m%d}.1").read_text(encoding="latin-1")
        guardar_precios(conn, PreciosDia(dia, FUENTE, 1, parsear_marginalpdbc(texto, dia)))
    yield
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FUENTE,))


def medias_horarias_de_la_curva(conn: psycopg.Connection, ahora: datetime) -> dict[datetime, D]:
    """Curva del dashboard agregada por hora completa en Python (mismo criterio que la tabla)."""
    curva = conn.execute(
        preparar(sql_panel("Precio final (€/kWh)"), ahora,
                 ahora - timedelta(hours=1), ahora + timedelta(hours=37))
    ).fetchall()
    por_hora: dict[datetime, list[D]] = defaultdict(list)
    for inicio, precio in curva:
        por_hora[inicio.astimezone(UTC).replace(minute=0)].append(precio)
    # Horas completas que aún no han terminado y empiezan antes de ahora + 36 h.
    por_hora = {h: v for h, v in por_hora.items()
                if h + timedelta(hours=1) > ahora and h < ahora + timedelta(hours=36)}
    assert all(len(v) == 4 for v in por_hora.values())
    with localcontext() as ctx:
        ctx.prec = 60
        return {h: sum(v) / 4 for h, v in por_hora.items()}


def filas_tabla(conn: psycopg.Connection, ahora: datetime, limite: int | None = None) -> list[tuple]:
    sql = sql_panel("Horas más baratas (próximas 36 h)")
    if limite is not None:
        assert "LIMIT 12" in sql
        sql = sql.replace("LIMIT 12", f"LIMIT {limite}")
    return conn.execute(preparar(sql, ahora)).fetchall()


def test_tabla_igual_a_curva_en_el_cambio_de_hora(conn: psycopg.Connection, octubre: None) -> None:
    # 25/10/2026 00:00 en Madrid (CEST): el día tiene 25 horas.
    ahora = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    esperado = medias_horarias_de_la_curva(conn, ahora)
    assert len(esperado) == 36  # las dos 02:00 locales no se mezclan

    completa = filas_tabla(conn, ahora, limite=100)
    assert len(completa) == 36
    for hora, _periodo, _omie, precio in completa:
        assert abs(precio - esperado[hora.astimezone(UTC)]) < D("1e-15"), hora

    # La tabla real son las 12 más baratas de esas mismas horas, en orden.
    tabla = filas_tabla(conn, ahora)
    mas_baratas = sorted(esperado, key=lambda h: (esperado[h], h))[:12]
    assert [h.astimezone(UTC) for h, *_ in tabla] == mas_baratas


def test_tabla_incluye_la_hora_en_curso_y_excluye_las_pasadas(conn: psycopg.Connection, octubre: None) -> None:
    ahora = datetime(2026, 10, 12, 12, 20, tzinfo=UTC)  # 14:20 en Madrid
    horas = {h.astimezone(UTC) for h, *_ in filas_tabla(conn, ahora, limite=100)}
    assert datetime(2026, 10, 12, 12, 0, tzinfo=UTC) in horas       # en curso, como hora completa
    assert datetime(2026, 10, 12, 11, 0, tzinfo=UTC) not in horas   # ya pasada
    assert max(horas) == datetime(2026, 10, 14, 0, 0, tzinfo=UTC)   # empieza antes de ahora + 36 h
    assert len(horas) == 37
    # Cada hora de la tabla es la media de sus cuatro cuartos, como en la curva.
    esperado = medias_horarias_de_la_curva(conn, ahora)
    for hora, _periodo, _omie, precio in filas_tabla(conn, ahora, limite=100):
        assert abs(precio - esperado[hora.astimezone(UTC)]) < D("1e-15"), hora

