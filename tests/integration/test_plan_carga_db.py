"""Migración 0020 (planificador, paso 3a) contra PostgreSQL: parámetros
agrupados, modo y configuración, entradas en SQL, plan efectivo y ahorro por
conexión frente a la referencia en Python (energia_monitor.plan_carga.ahorro).
Nunca se contacta con el cargador.
"""

from __future__ import annotations

import os
import random
import statistics
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import psycopg
import pytest

from energia_monitor import bateria, db, parametros
from energia_monitor.plan_carga.ahorro import CUARTO, ahorro_conexion, cuarto_de
from energia_monitor.tiempo import MADRID, limites_dia_utc

MIGRACIONES = Path(__file__).resolve().parents[2] / "db" / "migrations"

FUENTE = "plan_test"

# E3.9 con las claves añadidas al aprobar el plan del paso 3a.
GRUPOS = {
    1: ["capacidad_util_kwh", "reserva_pct", "tope_pct", "factor_bateria", "consumo_kwh_100km"],
    2: ["desconexion_min_s", "detectar_carga_completa", "energia_min_calibracion_kwh", "observaciones_calibracion",
        "umbral_carga_externa_kwh"],
    3: ["percentil_planificacion", "ventana_consumo_dias", "consumo_inicial_trayecto_kwh"],
    4: ["margen_carga_pct", "margen_salida_min", "carga_oportunista", "umbral_oportunista_pct",
        "nivel_oportunista_pct", "ventana_precio_esperado_dias", "ventana_umbral_dias"],
    5: ["ventana_disponibilidad_dias", "umbral_disponibilidad_pct", "historial_min_disponibilidad_dias"],
    6: ["potencia_carga_inicial_kw", "ventana_potencia_dias", "franja_min_min", "max_tareas_sistema",
        "horizonte_provisional_h", "horizonte_plan_h", "hora_publicacion_precios", "id_tarea_guardiana",
        "congelar_franja_min"],  # 0022
    7: ["aviso_sin_lectura_dias", "hueco_muestreo_aviso_s", "aviso_planificador_min"],
}

OBJETOS_0020 = ["bateria_parametro_grupo", "planificador_modo", "planificador_config", "plan_calculo", "plan_cuarto",
                "plan_franja", "plan_salida", "planificador_solicitud", "planificador_ciclo", "v_plan_precio",
                "v_plan_franja", "v_plan_vigente", "v_plan_efectivo", "v_plan_ahorro"]


def limpiar(conn: psycopg.Connection) -> None:
    for tabla in ("plan_calculo", "planificador_solicitud", "planificador_ciclo", "cargador_estado",
                  "cargador_muestra", "energia_tramo"):
        conn.execute(f"DELETE FROM {tabla}")  # type: ignore[arg-type]
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FUENTE,))
    conn.execute("UPDATE planificador_config SET contrato = 'vivienda', fuente = 'omie'")
    db.refrescar_precio_final(conn)


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    limpiar(conn)
    yield
    limpiar(conn)


def utc(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=UTC)


# --- Parámetros -------------------------------------------------------------------------------


def test_grupos_y_orden_de_todas_las_claves(conn: psycopg.Connection) -> None:
    filas = conn.execute("SELECT grupo, clave FROM bateria_parametro_clave ORDER BY grupo, orden").fetchall()
    assert {g: [c for gg, c in filas if gg == g] for g in GRUPOS} == GRUPOS
    assert len(filas) == sum(len(c) for c in GRUPOS.values())
    grupos = conn.execute("SELECT grupo, pregunta, avanzado, abierto_movil FROM bateria_parametro_grupo"
                          " ORDER BY grupo").fetchall()
    assert [g for g, *_ in grupos] == list(GRUPOS)
    assert all(p.startswith("¿") and p.endswith("?") for _, p, _, _ in grupos)
    assert [g for g, _, av, _ in grupos if av] == [6]
    assert [g for g, _, _, ab in grupos if ab] == [1, 4]
    assert conn.execute("SELECT array_agg(clave) FROM bateria_parametro_clave WHERE NOT editable"
                        ).fetchone() == (["id_tarea_guardiana"],)


def test_valores_iniciales_iguales_a_la_referencia() -> None:
    """bateria.PARAMETROS_INICIALES repite los valores de las migraciones (los tests recargan los parámetros
    desde él): en una base recién migrada, misma clave, mismo valor desde el 1/1/2025 y dentro del rango."""
    nombre = f"em_test_ini_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{nombre}"')  # type: ignore[arg-type]
    try:
        with psycopg.connect(dbname=nombre) as nueva:
            db.migrar(nueva, MIGRACIONES)
            filas = nueva.execute(
                "SELECT k.clave, p.valor, lower(p.vigencia), upper_inf(p.vigencia), k.minimo, k.maximo"
                " FROM bateria_parametro_clave k LEFT JOIN bateria_parametro p USING (clave) ORDER BY k.clave"
            ).fetchall()
    finally:
        with psycopg.connect(autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')  # type: ignore[arg-type]
    assert [c for c, *_ in filas] == sorted(bateria.PARAMETROS_INICIALES)
    for clave, valor, desde, abierta, minimo, maximo in filas:
        referencia = bateria.PARAMETROS_INICIALES[clave]
        assert (valor, desde, abierta) == (referencia, date(2025, 1, 1), True), clave
        assert minimo <= referencia <= maximo, clave


@pytest.mark.parametrize(("clave", "valor", "admitido"), [
    ("carga_oportunista", "0", True), ("carga_oportunista", "2", False),
    ("margen_carga_pct", "0", True), ("margen_salida_min", "0", True), ("margen_salida_min", "7.5", False),
    ("franja_min_min", "30.5", False), ("max_tareas_sistema", "10", False),
    ("hora_publicacion_precios", "825", True), ("hora_publicacion_precios", "810.5", False),
    ("hora_publicacion_precios", "1081", False), ("hora_publicacion_precios", "13.5", False),
    ("umbral_oportunista_pct", "95", False),
    ("potencia_carga_inicial_kw", "7.4", True),
])
def test_valores_admitidos(conn: psycopg.Connection, clave: str, valor: str, admitido: bool) -> None:
    desde = date(2031, 1, 1)
    try:
        if admitido:
            parametros.registrar_parametro_bateria(conn, clave=clave, desde=desde, valor=D(valor), origen="usuario",
                                                   fuente="test")
            assert conn.execute("SELECT bateria_param(%s, '2031-06-01Z')", (clave,)).fetchone() == (D(valor),)
        else:
            with pytest.raises(parametros.ErrorParametro):
                parametros.registrar_parametro_bateria(conn, clave=clave, desde=desde, valor=D(valor),
                                                       origen="usuario", fuente="test")
    finally:
        conn.execute("DELETE FROM bateria_parametro WHERE clave = %s AND lower(vigencia) = %s", (clave, desde))
        conn.execute("UPDATE bateria_parametro SET vigencia = daterange(lower(vigencia), NULL, '[)')"
                     " WHERE clave = %s AND upper(vigencia) = %s", (clave, desde))


# --- Modo, configuración y permisos -----------------------------------------------------------


def test_modo_y_configuracion(conn: psycopg.Connection) -> None:
    assert conn.execute("SELECT planificador_modo_en(now()), planificador_modo_en('2024-01-01Z')").fetchone() == (
        "sombra", "sombra")
    assert conn.execute("SELECT contrato, fuente FROM planificador_config").fetchall() == [("vivienda", "omie")]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO planificador_modo (desde, modo, origen) VALUES (now(), 'automatico', 'usuario')")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO planificador_config (id, contrato, fuente) VALUES (false, 'vivienda', 'omie')")


def test_permisos(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    db.configurar_rol_grafana(conn, "clave-de-test")
    db.configurar_rol_formulario(conn, "clave-formulario")
    datos = {"dbname": nombre_bd, "host": os.environ["PGHOST"], "autocommit": True}
    with psycopg.connect(user="grafana_ro", password="clave-de-test", **datos) as ro:  # type: ignore[arg-type]
        for objeto in OBJETOS_0020:
            ro.execute(f"SELECT * FROM {objeto} LIMIT 1")  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("SELECT solicitar_plan('grafana')")
    with psycopg.connect(user="formulario_rw", password="clave-formulario", **datos) as rw:  # type: ignore[arg-type]
        for objeto in OBJETOS_0020:
            rw.execute(f"SELECT * FROM {objeto} LIMIT 1")  # type: ignore[arg-type]
        rw.execute("SELECT solicitar_plan('lectura')")
        rw.execute("SELECT solicitar_plan('horario')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rw.execute("INSERT INTO plan_calculo (ahora, motivo, modo, prudente, conectado, capacidad_kwh,"
                       " horizonte_fin, duracion_ms) VALUES (now(), 'x', 'sombra', true, false, 91.3, now(), 0)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rw.execute("INSERT INTO planificador_modo (desde, modo, origen) VALUES (now(), 'activo', 'usuario')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rw.execute("UPDATE planificador_config SET fuente = 'x'")
    assert conn.execute("SELECT motivo FROM planificador_solicitud").fetchall() == [("horario",)]


# --- Entradas en SQL -------------------------------------------------------------------------------


def estados(conn: psycopg.Connection, filas: list[tuple[str, int]]) -> None:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES ('mennekes', %s, %s)",
                        [(utc(t), e) for t, e in filas])


def test_conexiones(conn: psycopg.Connection, limpio: None) -> None:
    estados(conn, [("2026-11-15T10:00", 1), ("2026-11-15T17:00", 4), ("2026-11-15T23:00", 6),
                   ("2026-11-15T23:01", 5), ("2026-11-16T02:00", 4), ("2026-11-16T07:00", 2),
                   ("2026-11-16T07:30", 1), ("2026-11-16T18:00", 4)])
    assert conn.execute("SELECT * FROM plan_conexiones('-infinity', 'infinity')").fetchall() == [
        (utc("2026-11-15T17:00"), utc("2026-11-16T07:00")), (utc("2026-11-16T18:00"), None)]
    assert conn.execute("SELECT * FROM plan_conexiones('2026-11-16T08:00Z', 'infinity')").fetchall() == [
        (utc("2026-11-16T18:00"), None)]
    assert conn.execute("SELECT * FROM plan_conexiones('-infinity', '2026-11-15T17:00Z')").fetchall() == []


def muestras(conn: psycopg.Connection, filas: list[tuple[datetime, int, int | None]]) -> None:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w, estado)"
                        " VALUES (%s, 60, %s, 0, 0, %s)", filas)


def test_cuartos_observados(conn: psycopg.Connection, limpio: None) -> None:
    t = utc("2026-11-16T20:00")
    muestras(conn, [(t + timedelta(minutes=1), 0, 1), (t + timedelta(minutes=5), 0, 4),  # mezcla: no enchufado
                    (t + timedelta(minutes=16), 0, 4), (t + timedelta(minutes=20), 4400, 6),
                    (t + timedelta(minutes=25), 0, None),  # sin estado: no cuenta
                    (t + timedelta(minutes=47), 0, None)])  # cuarto solo sin estado: no observado
    assert conn.execute("SELECT * FROM plan_cuartos_observados(%s, %s)", (t, t + timedelta(hours=1))).fetchall() == [
        (t, False, 2), (t + CUARTO, True, 2)]


def test_potencia_por_hora_local(conn: psycopg.Connection, limpio: None) -> None:
    # 25/10/2026: la hora 02 local se repite (cambio de hora); las dos cuentan como hora 2.
    muestras(conn, [(utc("2026-10-25T00:10"), 4400, 6), (utc("2026-10-25T01:10"), 4200, 6),
                    (utc("2026-10-25T01:11"), 4300, 6), (utc("2026-10-25T01:12"), 0, 6),
                    (utc("2026-10-25T01:13"), 3000, 5), (utc("2026-10-25T20:00"), 4100, 6),
                    (utc("2026-09-01T20:00"), 1000, 6)])  # fuera de la ventana de 28 días
    filas = conn.execute("SELECT * FROM plan_potencia_hora('2026-10-26Z')").fetchall()
    assert len(filas) == 24
    por_hora = {h: (p, n, o) for h, p, n, o in filas}
    assert por_hora[2] == (D(4300) / 1000, 3, "medida")  # 4400, 4200 y 4300 → 4300
    assert por_hora[21] == (D("4.1"), 1, "medida")
    assert por_hora[3] == (D("4.5"), 0, "inicial")
    assert sum(1 for _, _, o in por_hora.values() if o == "medida") == 2


# --- Plan efectivo y ahorro --------------------------------------------------------------------


def calculo(conn: psycopg.Connection, ahora: datetime, cuartos: dict[datetime, D]) -> int:
    fila = conn.execute(
        "INSERT INTO plan_calculo (ahora, motivo, modo, prudente, conectado, capacidad_kwh, horizonte_fin,"
        " duracion_ms) VALUES (%s, 'test', 'sombra', false, true, 91.3, %s, 1) RETURNING id",
        (ahora, ahora + timedelta(hours=72))).fetchone()
    assert fila is not None
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO plan_cuarto (calculo_id, inicio, nivel, kwh_cargador, precio_eur_kwh, provisional, ventana,"
            " sobre_umbral) VALUES (%s, %s, 'necesaria', %s, 0.1, false, 'segura', false)",
            [(fila[0], q, k) for q, k in cuartos.items()])
    return fila[0]


def test_plan_efectivo(conn: psycopg.Connection, limpio: None) -> None:
    t = utc("2026-11-16T22:00")
    a = calculo(conn, t, {t + CUARTO * i: D("1.1") for i in range(0, 8)})
    b = calculo(conn, t + timedelta(minutes=37), {t + CUARTO * i: D("1.2") for i in range(2, 4)})
    filas = conn.execute("SELECT inicio, calculo_id FROM v_plan_efectivo ORDER BY inicio").fetchall()
    # a hasta las 22:37 (cuartos 22:00, 22:15 y 22:30); b desde entonces: 22:45 (sus 22:30 ya había empezado).
    assert filas == [(t, a), (t + CUARTO, a), (t + 2 * CUARTO, a), (t + 3 * CUARTO, b)]


def mercado(conn: psycopg.Connection, dias: list[date], rng: random.Random) -> None:
    filas = []
    for dia in dias:
        inicio, fin = limites_dia_utc(dia)
        while inicio < fin:
            filas.append((inicio, inicio + CUARTO, FUENTE, D(rng.randint(-500, 25000)) / 100))
            inicio += CUARTO
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh) VALUES (%s, %s, %s, %s)",
                        filas)
    conn.execute("UPDATE planificador_config SET fuente = %s", (FUENTE,))
    db.refrescar_precio_final(conn)


def tramos(conn: psycopg.Connection, energia: dict[datetime, D]) -> None:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO energia_tramo (inicio, fin, coche_wh, casa_wh, total_wh, cubierto_s, incompleto)"
                        " VALUES (%s, %s, %s, NULL, %s, 900, false)",
                        [(q, q + CUARTO, wh, wh) for q, wh in energia.items()])


def referencia(conn: psycopg.Connection, inicio: datetime, fin: datetime, energia: dict[datetime, D],
               potencia_muestras: dict[int, list[int]]):
    efectivo = {q: k for q, k in conn.execute("SELECT inicio, kwh_cargador FROM v_plan_efectivo")}
    precio = {q: p for q, p in conn.execute("SELECT inicio, precio_final_eur_kwh FROM v_plan_precio")}
    potencia = {h: D(statistics.median(potencia_muestras[h])) / 1000 if h in potencia_muestras else D("4.5")
                for h in range(24)}
    return ahorro_conexion(inicio, fin, energia, efectivo, potencia, precio, lambda _d: D("0.96"))


def comparar(fila: tuple, esperado) -> None:  # type: ignore[no-untyped-def]
    (energia, plan, cubre, ref_incompleta, real, coste_plan, coste_ref, sin_precio,
     propio, resto, coste_propio, coste_resto) = fila
    tol = D("1e-9")
    assert abs(energia - esperado.energia_cargador_kwh) < tol
    assert abs(plan - esperado.plan_kwh) < tol
    assert abs(propio - esperado.plan_propio_kwh) < tol and abs(resto - esperado.resto_kwh) < tol
    assert abs(propio + resto - energia) < tol
    if not esperado.sin_precio:
        assert abs(coste_propio - esperado.coste_plan_propio_eur) < tol
        assert abs(coste_resto - esperado.coste_resto_eur) < tol
        assert abs(coste_propio + coste_resto - coste_plan) < tol
    assert cubre == esperado.plan_cubre
    assert ref_incompleta == (esperado.referencia_kwh < esperado.energia_cargador_kwh - tol)
    assert sin_precio == esperado.sin_precio
    for sql, py in ((real, esperado.coste_real_eur), (coste_plan, esperado.coste_plan_eur),
                    (coste_ref, esperado.coste_referencia_eur)):
        assert abs(sql - py) < tol


CONSULTA_AHORRO = ("SELECT energia_cargador_kwh, plan_kwh, plan_cubre, referencia_incompleta, coste_real_eur,"
                   " coste_plan_eur, coste_referencia_eur, sin_precio, plan_propio_kwh, resto_kwh,"
                   " coste_plan_propio_eur, coste_resto_eur FROM v_plan_ahorro WHERE inicio = %s")


def test_ahorro_noche_del_cambio_de_hora(conn: psycopg.Connection, limpio: None) -> None:
    """Conexión de 21:07 a 08:52 locales del 24 al 25/10/2026 (25 h), con dos cálculos y potencia medida."""
    rng = random.Random(25)
    mercado(conn, [date(2026, 10, 24), date(2026, 10, 25)], rng)
    potencia_muestras = {22: [4400, 4420, 4380], 3: [4100, 4200]}
    muestras(conn, [(datetime(2026, 10, 20, h, m, tzinfo=MADRID).astimezone(UTC), w, 6)
                    for h, ws in potencia_muestras.items() for m, w in enumerate(ws)])
    inicio, fin = utc("2026-10-24T19:07:10"), utc("2026-10-25T07:52:00")
    estados(conn, [("2026-10-24T09:00", 1), ("2026-10-24T10:00", 4), ("2026-10-24T12:00", 1),  # antes del 1.er cálculo
                   ("2026-10-24T19:07:10", 4), ("2026-10-24T23:00", 6), ("2026-10-25T02:00", 4),
                   ("2026-10-25T07:52", 1), ("2026-10-25T18:00", 4)])  # la última, abierta: no sale
    t1, t2 = utc("2026-10-24T18:30"), utc("2026-10-24T23:10")
    calculo(conn, t1, {utc("2026-10-24T19:00") + CUARTO * i: D("1.1") for i in range(6)}  # 19:00–20:30, la 1.ª partida
            | {utc("2026-10-25T01:00") + CUARTO * i: D("1.1") for i in range(4)})  # ya no efectivos
    calculo(conn, t2, {utc("2026-10-25T00:00") + CUARTO * i: D("1.125") for i in range(3)}
            | {utc("2026-10-25T07:45"): D("1.125")})  # la última, partida por la desconexión
    energia = {utc("2026-10-24T23:00") + CUARTO * i: D(1100 + rng.randint(0, 60)) for i in range(12)}
    tramos(conn, energia)

    filas = conn.execute("SELECT inicio, fin, fecha FROM v_plan_ahorro").fetchall()
    assert filas == [(inicio, fin, date(2026, 10, 24))]
    esperado = referencia(conn, inicio, fin, energia, potencia_muestras)
    assert esperado.plan_kwh < esperado.energia_cargador_kwh  # el resto va a los primeros cuartos libres
    comparar(conn.execute(CONSULTA_AHORRO, (inicio,)).fetchone(), esperado)  # type: ignore[arg-type]
    # El real coincide con v_coste_tramo (contrato y fuente de planificador_config).
    real = conn.execute("SELECT sum(coste_eur) FROM v_coste_tramo WHERE consumidor = 'coche' AND contrato = 'vivienda'"
                        " AND fuente = %s AND inicio >= %s AND inicio < %s", (FUENTE, inicio, fin)).fetchone()
    assert real is not None and abs(real[0] - esperado.coste_real_eur) < D("1e-9")  # type: ignore[operator]


@pytest.mark.parametrize("semilla", range(12))
def test_ahorro_aleatorio(conn: psycopg.Connection, limpio: None, semilla: int) -> None:
    rng = random.Random(semilla)
    mercado(conn, [date(2026, 11, 2), date(2026, 11, 3)], rng)
    inicio = utc("2026-11-02T17:00") + timedelta(seconds=rng.randint(0, 4 * 3600))
    fin = inicio + timedelta(seconds=rng.randint(1800, 14 * 3600))
    estados(conn, [("2026-11-02T08:00", 1)])
    estados(conn, [(inicio.isoformat()[:19], 4), (fin.isoformat()[:19], 2)])
    cuartos = []
    q = cuarto_de(inicio)
    while q < fin:
        cuartos.append(q)
        q += CUARTO
    plan = {q: D(rng.randint(500, 1150)) / 1000 for q in rng.sample(cuartos, rng.randint(0, len(cuartos)))}
    calculo(conn, utc("2026-11-02T16:00"), plan)
    energia = {q: D(rng.randint(0, 1150)) for q in rng.sample(cuartos, rng.randint(0, len(cuartos)))}
    tramos(conn, energia)
    if rng.random() < 0.2:  # un cuarto sin precio: costes sin valor
        conn.execute("DELETE FROM precio_mercado WHERE fuente = %s AND inicio = %s", (FUENTE, rng.choice(cuartos)))
        db.refrescar_precio_final(conn)
    esperado = referencia(conn, inicio.replace(microsecond=0), fin.replace(microsecond=0), energia, {})
    fila = conn.execute(CONSULTA_AHORRO, (inicio.replace(microsecond=0),)).fetchone()
    assert fila is not None
    if esperado.sin_precio:
        assert fila[4:7] == (None, None, None) and fila[7] is True and fila[10:] == (None, None)
    else:
        comparar(fila, esperado)


def test_cli_hora_en_hhmm(conn: psycopg.Connection, nombre_bd: str, monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture[str]) -> None:
    from energia_monitor import __main__ as cli

    monkeypatch.setattr(db, "conectar", lambda **_: psycopg.connect(dbname=nombre_bd, autocommit=True))
    base = ["parametro", "registrar", "--ambito", "bateria", "--clave", "hora_publicacion_precios",
            "--desde", "2031-01-01", "--origen", "usuario", "--fuente", "test"]
    try:
        assert cli.main([*base, "--valor", "13.5"]) == 1  # no es HH:MM
        assert cli.main([*base, "--valor", "19:00"]) == 1  # fuera de rango
        assert cli.main([*base, "--valor", "14:15"]) == 0
        assert conn.execute("SELECT valor FROM bateria_parametro WHERE clave = 'hora_publicacion_precios'"
                            " AND lower(vigencia) = '2031-01-01'").fetchone() == (855,)
        capsys.readouterr()
        assert cli.main(["parametro", "listar", "--ambito", "bateria", "--fecha", "2031-02-01"]) == 0
        assert any("hora_publicacion_precios" in linea and " 14:15 " in linea
                   for linea in capsys.readouterr().out.splitlines())
    finally:
        conn.execute("DELETE FROM bateria_parametro WHERE clave = 'hora_publicacion_precios'"
                     " AND lower(vigencia) = '2031-01-01'")
        conn.execute("UPDATE bateria_parametro SET vigencia = daterange(lower(vigencia), NULL, '[)')"
                     " WHERE clave = 'hora_publicacion_precios' AND upper(vigencia) = '2031-01-01'")


def test_resto_en_los_cuartos_libres_mas_baratos(conn: psycopg.Connection, limpio: None) -> None:
    """El plan tenía 1 kWh y se cargaron 3: los 2 que faltan van a los cuartos libres más baratos, no a los
    primeros desde el enchufe; plan propio y resto, aparte."""
    rng = random.Random(1)
    mercado(conn, [date(2026, 11, 2), date(2026, 11, 3)], rng)
    inicio, fin = utc("2026-11-02T18:00"), utc("2026-11-02T20:00")
    estados(conn, [("2026-11-02T08:00", 1), ("2026-11-02T18:00", 4), ("2026-11-02T20:00", 2)])
    cuartos = [inicio + i * CUARTO for i in range(8)]
    calculo(conn, utc("2026-11-02T17:00"), {cuartos[0]: D(1)})
    energia = {q: D(375) for q in cuartos}  # 3 kWh
    tramos(conn, energia)
    precio = {q: p for q, p in conn.execute("SELECT inicio, precio_final_eur_kwh FROM v_plan_precio")}
    fila = conn.execute(CONSULTA_AHORRO, (inicio,)).fetchone()
    assert fila is not None
    esperado = referencia(conn, inicio, fin, energia, {})
    comparar(fila, esperado)
    assert (esperado.plan_propio_kwh, esperado.resto_kwh) == (D(1), D(2))
    libres = sorted(cuartos[1:], key=lambda q: precio[q])
    # 2 kWh a 4,5 kW: un cuarto entero (1,125) y 0,875 del siguiente más barato.
    coste_resto = (D("1.125") * precio[libres[0]] + D("0.875") * precio[libres[1]]) * D("0.96")
    assert abs(esperado.coste_resto_eur - coste_resto) < D("1e-12")  # type: ignore[operator]
