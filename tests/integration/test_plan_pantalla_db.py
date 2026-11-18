"""Migración 0021 (paso 3b) contra PostgreSQL: plan_ahorro con la conexión abierta y por periodo, y
plan_franjas_pasadas, frente a la referencia en Python. Nunca se contacta con el cargador."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal as D

import psycopg
from test_plan_carga_db import (  # noqa: F401 - limpio es una fixture
    CUARTO,
    calculo,
    comparar,
    estados,
    limpio,
    mercado,
    muestras,
    referencia,
    tramos,
    utc,
)

COLUMNAS = ("energia_cargador_kwh, plan_kwh, plan_cubre, referencia_incompleta, coste_real_eur, coste_plan_eur,"
            " coste_referencia_eur, sin_precio, plan_propio_kwh, resto_kwh, coste_plan_propio_eur, coste_resto_eur")


def test_conexion_abierta_hasta_la_ultima_muestra(conn: psycopg.Connection, limpio: None) -> None:
    rng = random.Random(5)
    mercado(conn, [date(2026, 11, 2), date(2026, 11, 3)], rng)
    estados(conn, [("2026-11-02T08:00", 1), ("2026-11-02T18:00", 4), ("2026-11-02T22:00", 2),
                   ("2026-11-02T23:10", 4)])  # la segunda sigue abierta
    calculo(conn, utc("2026-11-02T17:00"), {utc("2026-11-02T19:00") + i * CUARTO: D("1.1") for i in range(8)}
            | {utc("2026-11-03T00:00") + i * CUARTO: D("1.1") for i in range(4)})
    energia = {utc("2026-11-02T19:00") + i * CUARTO: D(1000) for i in range(6)}
    energia |= {utc("2026-11-03T00:00") + i * CUARTO: D(1050) for i in range(3)}
    tramos(conn, energia)
    ultima = utc("2026-11-03T00:47:30")
    muestras(conn, [(ultima, 4400, 6)])

    filas = conn.execute("SELECT inicio, fin, abierta FROM plan_ahorro('-infinity', 'infinity') ORDER BY inicio"
                         ).fetchall()
    assert filas == [(utc("2026-11-02T18:00"), utc("2026-11-02T22:00"), False),
                     (utc("2026-11-02T23:10"), ultima, True)]
    # v_plan_ahorro: las mismas, con abierta al final.
    assert conn.execute("SELECT inicio, abierta FROM v_plan_ahorro ORDER BY inicio").fetchall() == [
        (utc("2026-11-02T18:00"), False), (utc("2026-11-02T23:10"), True)]
    for inicio, fin, _ in filas:
        en_conexion = {q: wh for q, wh in energia.items() if inicio < q + CUARTO and q < fin}
        fila = conn.execute(f"SELECT {COLUMNAS} FROM plan_ahorro('-infinity', 'infinity') WHERE inicio = %s",
                            (inicio,)).fetchone()
        assert fila is not None
        # La muestra de potencia es posterior al enchufe: fuera de la ventana; potencia inicial (4,5 kW).
        comparar(fila, referencia(conn, inicio, fin, en_conexion, {}))

    # Por periodo: el fin (o la última muestra) en [desde, hasta).
    assert conn.execute("SELECT inicio FROM plan_ahorro('2026-11-02T23:00Z', 'infinity')").fetchall() == [
        (utc("2026-11-02T23:10"),)]
    assert conn.execute("SELECT inicio FROM plan_ahorro('-infinity', '2026-11-02T22:00Z')").fetchall() == []
    assert conn.execute("SELECT inicio FROM plan_ahorro('-infinity', '2026-11-02T22:00:01Z')").fetchall() == [
        (utc("2026-11-02T18:00"),)]


def test_franjas_pasadas(conn: psycopg.Connection, limpio: None) -> None:
    rng = random.Random(9)
    mercado(conn, [date(2026, 11, 2), date(2026, 11, 3)], rng)
    estados(conn, [("2026-11-02T08:00", 1), ("2026-11-02T18:00", 4), ("2026-11-03T07:00", 2)])
    t = utc("2026-11-02T17:00")
    ident = calculo(conn, t, {})
    conn.execute("DELETE FROM plan_cuarto WHERE calculo_id = %s", (ident,))
    salida = utc("2026-11-03T08:00")
    cuartos = ([(utc("2026-11-02T22:00") + i * CUARTO, "necesaria", salida) for i in range(4)]
               + [(utc("2026-11-02T23:00") + i * CUARTO, "oportunista", None) for i in range(2)]
               + [(utc("2026-11-03T02:00") + i * CUARTO, "margen", salida) for i in range(2)])
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO plan_cuarto (calculo_id, inicio, nivel, salida, kwh_cargador, precio_eur_kwh, provisional,"
            " ventana, sobre_umbral) VALUES (%s, %s, %s, %s, 1.1, 0.1, false, 'segura', false)",
            [(ident, q, n, s) for q, n, s in cuartos])
    energia = {utc("2026-11-02T22:00") + i * CUARTO: D(900) for i in range(3)}
    tramos(conn, energia)
    filas = conn.execute("SELECT * FROM plan_franjas_pasadas('2026-11-02T12:00Z', '2026-11-03T02:15Z')").fetchall()
    # La de las 02:00 aún no ha terminado a las 02:15: no sale.
    assert len(filas) == 1
    (inicio, fin, modo, plan_kwh, nec, mar, opo, salidas, provisional, real, coste, conectado) = filas[0]
    assert (inicio, fin, modo) == (utc("2026-11-02T22:00"), utc("2026-11-02T23:30"), "sombra")
    assert (plan_kwh, nec, mar, opo, salidas, provisional) == (D("6.6"), D("4.4"), 0, D("2.2"), [salida], False)
    assert real == D("2.7") and conectado
    precio = dict(conn.execute("SELECT inicio, precio_final_eur_kwh FROM v_plan_precio"))
    esperado = sum(D(900) / 1000 * D("0.96") * precio[q] for q in energia)
    assert abs(coste - esperado) < D("1e-12")
    # Sin carga real en su horario: 0 kWh; fuera de toda conexión: conectado falso.
    filas = conn.execute("SELECT real_kwh, conectado FROM plan_franjas_pasadas('-infinity', 'infinity')"
                         " WHERE inicio = %s", (utc("2026-11-03T02:00"),)).fetchall()
    assert filas == [(0, True)]


def test_permisos_0021(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    import os

    from energia_monitor import db

    db.configurar_rol_grafana(conn, "clave-de-test")
    db.configurar_rol_formulario(conn, "clave-formulario")
    datos = {"dbname": nombre_bd, "host": os.environ["PGHOST"], "autocommit": True}
    for usuario, clave in (("grafana_ro", "clave-de-test"), ("formulario_rw", "clave-formulario")):
        with psycopg.connect(user=usuario, password=clave, **datos) as c:  # type: ignore[arg-type]
            c.execute("SELECT * FROM plan_ahorro(now() - interval '30 days', now())")
            c.execute("SELECT * FROM plan_franjas_pasadas(now() - interval '7 days', now())")
            c.execute("SELECT * FROM v_plan_ahorro LIMIT 1")

