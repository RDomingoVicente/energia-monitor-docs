"""Consultas de «Energía: batería» y del coste fuera de casa de «Energía: coche», contra PostgreSQL y como grafana_ro.

Se ejecutan tal como están en grafana/*.json, sustituyendo lo que Grafana resolvería ($__timeFilter,
$__timeFrom/To, $contrato, $fuente) y now() por el instante del escenario. Las vistas que leen now() por
dentro (v_bateria_estado, v_bateria_salida, v_aviso) se cambian por las mismas funciones con ese instante.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path

import psycopg
import pytest
from escenarios_bateria import Constructor, con_intervalos, t
from test_bateria_db import cargar, limpiar

from energia_monitor import db
from energia_monitor.formulario import datos

RAIZ = Path(__file__).resolve().parents[2]
BATERIA = json.loads((RAIZ / "grafana" / "energia-bateria.json").read_text(encoding="utf-8"))
COCHE = json.loads((RAIZ / "grafana" / "energia-coche.json").read_text(encoding="utf-8"))
PANELES = {p["title"]: p for d in (BATERIA, COCHE) for p in d["panels"]}
ANOTACIONES = {a["name"]: a for a in BATERIA["annotations"]["list"]}
CLAVE = "clave-de-test"
AHORA = t(14)  # lun 16/11 15:00 en Madrid
DESDE, HASTA = AHORA - timedelta(days=7), AHORA

# Las vistas con now() dentro, con el instante del escenario (definiciones de 0016).
VISTAS = {
    "v_bateria_estado": "bateria_estado({a})",
    "v_aviso": "bateria_avisos({a})",
    "v_bateria_salida": ("(SELECT s.*, CASE WHEN s.salida = e.proxima_salida THEN e.energia_necesaria_kwh END"
                         " AS energia_necesaria_kwh FROM bateria_salidas({a}, 7) s CROSS JOIN bateria_estado({a}) e)"
                         " AS vs"),
}


def cerca(a: D, b: D) -> bool:
    """numeric y Decimal dividen con precisiones distintas."""
    return abs(a - b) < D("1e-15")


def lit(instante: datetime) -> str:
    return f"'{instante.isoformat()}'::timestamptz"


def preparar(sql: str, desde: datetime = DESDE, hasta: datetime = HASTA) -> str:
    for vista, sustituto in VISTAS.items():
        sql = re.sub(rf"\b{vista}\b", sustituto.format(a=lit(AHORA)), sql)
    sql = re.sub(r"\$__timeFilter\((\w+(?:\.\w+)?)\)", rf"\1 BETWEEN {lit(desde)} AND {lit(hasta)}", sql)
    sql = sql.replace("$__timeFrom()", lit(desde)).replace("$__timeTo()", lit(hasta))
    sql = sql.replace("$contrato", "vivienda").replace("$fuente", "test").replace("now()", lit(AHORA))
    assert "$" not in sql and "now()" not in sql, sql
    return sql


@pytest.fixture
def historia(conn: psycopg.Connection) -> Iterator[None]:
    """Lectura 40 %, carga en casa de 02:00 a 04:00 UTC, trayecto de 06:00 a 12:00 con una carga externa
    (30 kWh, 14,60 €) a las 09:00, lectura 50 % a las 13:00; horario de lunes a viernes a las 09:00."""
    limpiar(conn)
    c = (Constructor().estado(t(-10), 4).lectura(t(1), 40)
         .sesion(t(2), t(4), 20000, "schedman", 7000)
         .trayecto(t(6), t(12)).carga_externa(t(9), 30)
         .lectura(t(13), 50)
         .muestras(t(0), t(14))
         .horario(lun=time(9), mar=time(9), mie=time(9), jue=time(9), vie=time(9)))
    cargar(conn, con_intervalos(c.e))
    conn.execute("UPDATE bateria_carga_externa SET coste_eur = 14.6, nota = 'Ionity'")
    conn.execute("SELECT recalcular_bateria(%s)", (AHORA,))
    db.configurar_rol_grafana(conn, CLAVE)
    yield
    limpiar(conn)


@pytest.fixture
def ro(nombre_bd: str, historia: None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password=CLAVE, host=os.environ["PGHOST"],
                         autocommit=True) as c:
        yield c


def filas(ro: psycopg.Connection, sql: str, **rango: datetime) -> tuple[list[str], list[tuple]]:
    cur = ro.execute(preparar(sql, **rango))  # type: ignore[arg-type]
    return [d.name for d in cur.description or []], cur.fetchall()


def fila(ro: psycopg.Connection, titulo: str, ref: int = 0) -> dict:
    nombres, todas = filas(ro, PANELES[titulo]["targets"][ref]["rawSql"])
    assert len(todas) == 1
    return dict(zip(nombres, todas[0]))


CONSULTAS = ([(p["title"], i, tg["rawSql"]) for p in BATERIA["panels"] for i, tg in enumerate(p["targets"])]
             + [(a["name"], 0, a["target"]["rawSql"]) for a in BATERIA["annotations"]["list"]]
             + [(p["title"], 0, p["targets"][0]["rawSql"]) for p in COCHE["panels"]
                if "v_coste_carga_externa" in p["targets"][0]["rawSql"]])


@pytest.mark.parametrize(("titulo", "ref", "sql"), CONSULTAS, ids=[f"{c[0]}-{c[1]}" for c in CONSULTAS])
def test_cada_consulta_funciona_como_grafana_ro(ro: psycopg.Connection, titulo: str, ref: int, sql: str) -> None:
    filas(ro, sql)


def test_linea_del_nivel(ro: psycopg.Connection, conn: psycopg.Connection) -> None:
    nombres, puntos = filas(ro, PANELES["Nivel de batería (7 días)"]["targets"][0]["rawSql"])
    assert nombres == ["time", "Nivel (%)", "Previsto (%)"]
    assert [p[0] for p in puntos] == sorted(p[0] for p in puntos)
    # Cada evento con su nivel, en orden, y la línea termina en el nivel de ahora.
    eventos = conn.execute("SELECT instante, nivel_pct FROM bateria_nivel ORDER BY n").fetchall()
    assert [p for p in [(x[0], x[1]) for x in puntos] if p in eventos] == eventos
    nivel = {p[0]: p[1] for p in puntos}
    ahora = conn.execute("SELECT nivel_pct FROM bateria_estado(%s)", (AHORA,)).fetchone()[0]
    assert puntos[-1][0] == AHORA and puntos[-1][1] == ahora
    # Plana hasta que empieza la carga: en su inicio, el nivel de la lectura anterior.
    assert nivel[t(2)] == D(40)
    # Y antes de la salida del trayecto, el nivel tras la carga.
    tras_carga = conn.execute("SELECT nivel_pct FROM bateria_nivel WHERE tipo = 'sesion'").fetchone()[0]
    assert nivel[t(6)] == tras_carga


def test_linea_del_nivel_empieza_en_el_ultimo_nivel_anterior_al_rango(ro: psycopg.Connection) -> None:
    sql = PANELES["Nivel de batería (7 días)"]["targets"][0]["rawSql"]
    _, puntos = filas(ro, sql, desde=t(3), hasta=AHORA)
    # El primer punto es el principio del rango con el nivel del último evento anterior (la lectura del 40 %).
    assert puntos[0][0] == t(3) and puntos[0][1] == D(40)


def test_lecturas_como_puntos(ro: psycopg.Connection) -> None:
    _, lecturas = filas(ro, PANELES["Nivel de batería (7 días)"]["targets"][1]["rawSql"])
    assert lecturas == [(t(1), D(40)), (t(13), D(50))]


def test_acierto_igual_que_la_pagina_historial(ro: psycopg.Connection, conn: psycopg.Connection) -> None:
    h = datos.historial(conn, AHORA, 30, False)
    difs = [x.diferencia_pts for x in h.filas if x.tipo == "lectura" and x.comparable and not x.anulada]
    assert difs
    f = fila(ro, "Acierto de la estimación (30 días)")
    assert f["Lecturas comparables"] == len(difs)
    assert cerca(f["Error medio (pts)"], sum(difs, D(0)) / len(difs))
    assert cerca(f["Error absoluto medio (pts)"], sum((abs(x) for x in difs), D(0)) / len(difs))


def test_estado_salida_y_avisos(ro: psycopg.Connection, conn: psycopg.Connection) -> None:
    e = conn.execute("SELECT nivel_pct, confianza, objetivo_pct, energia_necesaria_kwh FROM bateria_estado(%s)",
                     (AHORA,)).fetchone()
    assert fila(ro, "Nivel ahora") == {"Nivel (%)": e[0], "Nivel (kWh)": fila(ro, "Nivel ahora")["Nivel (kWh)"],
                                       "Confianza": e[1]}
    assert e[1] == "leido"
    assert fila(ro, "Objetivo de salida")["Objetivo (%)"] == e[2]
    assert fila(ro, "Energía necesaria")["Energía necesaria (kWh)"] == e[3]
    # Próxima salida: martes 17 a las 09:00 (Madrid), del horario.
    salida = fila(ro, "Próxima salida")
    assert (salida["Salida"], salida["Origen"]) == ("mar 17/11 09:00", "Horario")
    avisos = fila(ro, "Avisos activos")
    n = conn.execute("SELECT count(*) FROM bateria_avisos(%s)", (AHORA,)).fetchone()[0]
    assert sum(avisos.values()) == n


def test_proximas_salidas(ro: psycopg.Connection) -> None:
    nombres, todas = filas(ro, PANELES["Próximas salidas (7 días)"]["targets"][0]["rawSql"])
    salidas = [dict(zip(nombres, f)) for f in todas]
    assert [s["Salida"] for s in salidas][:2] == ["mar 17/11 09:00", "mié 18/11 09:00"]
    # Solo la primera lleva la energía necesaria.
    assert salidas[0]["Energía necesaria (kWh)"] is not None
    assert all(s["Energía necesaria (kWh)"] is None for s in salidas[1:])


def test_trayectos_recientes(ro: psycopg.Connection, conn: psycopg.Connection) -> None:
    nombres, todas = filas(ro, PANELES["Trayectos recientes"]["targets"][0]["rawSql"])
    (tr,) = [dict(zip(nombres, f)) for f in todas]
    consumo = conn.execute("SELECT consumo_kwh FROM bateria_trayecto WHERE NOT corto").fetchone()[0]
    assert (tr["Salida"], tr["Llegada"], tr["Duración (h)"]) == ("lun 16/11 07:00", "lun 16/11 13:00", 6)
    assert tr["Consumo (kWh)"] == consumo and tr["Origen"] == "Reconciliado con lecturas"
    # Reconciliado entero: lo real es todo el consumo.
    assert tr["Reales (kWh)"] == consumo


def test_trayecto_partido_reales_menor_que_el_consumo(nombre_bd: str, conn: psycopg.Connection) -> None:
    """El caso del NAS: primera lectura con el coche fuera. «Reales» es solo el tramo reconciliado."""
    limpiar(conn)
    c = Constructor().estado(t(-10), 4).trayecto(t(8), t(17)).lectura(t(15.5), 87).lectura(t(17.25), 75)
    cargar(conn, con_intervalos(c.e))
    conn.execute("SELECT recalcular_bateria(%s)", (t(18),))  # después de la llegada (t 17) y de la segunda lectura
    db.configurar_rol_grafana(conn, CLAVE)
    try:
        with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password=CLAVE, host=os.environ["PGHOST"],
                             autocommit=True) as ro:
            nombres, todas = filas(ro, PANELES["Trayectos recientes"]["targets"][0]["rawSql"])
        (tr,) = [dict(zip(nombres, f)) for f in todas]
        real = conn.execute("SELECT - real_kwh FROM v_bateria_evento_reciente WHERE tipo = 'trayecto'").fetchone()[0]
        assert cerca(tr["Reales (kWh)"], real) and cerca(real, D("10.956"))
        assert tr["Consumo (kWh)"] > tr["Reales (kWh)"] and tr["Origen"] == "Inicial (parámetro)"
    finally:
        limpiar(conn)


def test_anotaciones(ro: psycopg.Connection) -> None:
    _, sesiones = filas(ro, ANOTACIONES["Cargas en casa"]["target"]["rawSql"])
    assert [(s[0], s[1]) for s in sesiones] == [(t(2), t(4))]
    # Primero la energía medida y entre paréntesis la estimada a batería (20 kWh × 0,86).
    assert sesiones[0][2] == "Carga en casa: 20,0 kWh (17,2 a batería)"
    _, trayectos = filas(ro, ANOTACIONES["Trayectos"]["target"]["rawSql"])
    assert [(x[0], x[1]) for x in trayectos] == [(t(6), t(12))] and trayectos[0][2].startswith("Trayecto: −")
    _, puntos = filas(ro, ANOTACIONES["Cargas externas y carga completa"]["target"]["rawSql"])
    assert puntos == [(t(9), "Carga externa: 30,0 kWh · 14,60 € · Ionity", "carga_externa")]


# --- Fuera de casa -----------------------------------------------------------------------------------


def test_fuera_de_casa_en_los_dos_dashboards(ro: psycopg.Connection) -> None:
    eur, kwh = D("14.6"), D(30)
    bateria = fila(ro, "Fuera de casa en el rango")
    assert (bateria["Coste (€)"], bateria["Energía (kWh)"], bateria["Cargas"]) == (eur, kwh, 1)
    assert cerca(bateria["Medio (€/kWh)"], eur / kwh)
    rango = fila(ro, "Gasto en el rango")
    assert (rango["Fuera (€)"], rango["Fuera (kWh)"]) == (eur, kwh) and cerca(rango["Medio fuera (€/kWh)"], eur / kwh)
    # Sin precios para la casa: su €/kWh queda vacío, no toma el de fuera.
    assert rango["Medio casa (€/kWh)"] is None
    precio = fila(ro, "Precio medio pagado")
    assert cerca(precio["Fuera (€/kWh)"], eur / kwh) and precio["Casa (€/kWh)"] is None
    gasto = fila(ro, "Gasto del coche")
    assert (gasto["Hoy fuera (€)"], gasto["Mes fuera (€)"], gasto["Hoy fuera (kWh)"]) == (eur, eur, kwh)
    _, diario = filas(ro, PANELES["Gasto diario del coche (30 días)"]["targets"][0]["rawSql"])
    assert [(d[3], d[4]) for d in diario] == [(eur, kwh)]


def test_vistas_sustituidas_leen_now(conn: psycopg.Connection) -> None:
    # Si alguna deja de depender de now(), la sustitución de arriba sobra o se queda corta.
    for vista in VISTAS:
        definicion = conn.execute("SELECT pg_get_viewdef(%s::regclass)", (vista,)).fetchone()[0]
        assert "now()" in definicion, vista
