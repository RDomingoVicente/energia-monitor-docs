"""Fase 2, etapa 1 contra PostgreSQL: persistencia del muestreo, energía por
tramo, estimación de sesiones, coste y consultas del dashboard "Energía: coche".

Muestras, sesiones y precios sintéticos; las vistas se comparan con la
referencia en Python (energia_monitor.energia). Nunca se contacta con el
cargador: los clientes son falsos.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any

import psycopg
import pytest

from energia_monitor import db, muestreo, parametros
from energia_monitor.energia import Muestra, energia_por_tramo, reparto_estimado
from energia_monitor.tiempo import limites_dia_utc
from energia_monitor.viaris import ErrorCargador, EstadoConector, Modulador, Sesion

RAIZ = Path(__file__).resolve().parents[2]
DASHBOARD = json.loads((RAIZ / "grafana" / "energia-coche.json").read_text(encoding="utf-8"))
FUENTE = "cargador_test"
QH = timedelta(minutes=15)
MIN = timedelta(minutes=1)
DIA = date(2026, 11, 5)
OBJETOS_NUEVOS = ["cargador_parametro_clave", "cargador_parametro", "cargador_muestra", "cargador_estado",
                  "cargador_sesion", "v_energia_muestra", "v_energia_tramo_medida", "v_energia_sesion_estimada",
                  "v_energia_tramo", "v_coste_tramo", "v_sesion_coste", "energia_tramo"]


def limpiar(conn: psycopg.Connection) -> None:
    for tabla in ("cargador_muestra", "cargador_estado", "cargador_sesion", "energia_tramo"):
        conn.execute(f"DELETE FROM {tabla}")  # type: ignore[arg-type]
    conn.execute("DELETE FROM cargador_parametro WHERE fuente = 'test'")
    conn.execute("UPDATE cargador_parametro SET vigencia = daterange(lower(vigencia), NULL, '[)')")
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FUENTE,))
    conn.execute("DELETE FROM serie_esios WHERE inicio >= '2026-11-05Z' AND inicio < '2026-11-06Z'")


@pytest.fixture
def limpio(conn: psycopg.Connection):
    limpiar(conn)
    yield
    limpiar(conn)


def insertar_muestras(conn: psycopg.Connection, muestras: list[Muestra], estado: int | None = None) -> None:
    """Inserción por SQL, como haría el usuario: después hay que recalcular energia_tramo."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w, estado)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [(m.instante, m.intervalo_s, m.evse_w, m.home_w, m.total_w, estado) for m in muestras],
        )
        muestreo.recalcular_tramos(conn, min(m.instante for m in muestras) - timedelta(seconds=180),
                                   max(m.instante for m in muestras))


def serie(desde: datetime, hasta: datetime, potencia: Any = lambda i: D(4400)) -> list[Muestra]:
    """Una muestra por minuto de desde a hasta; la primera sin intervalo."""
    muestras, t, i = [], desde, 0
    while t <= hasta:
        evse = potencia(i)
        muestras.append(Muestra(t, None if i == 0 else 60, evse, D("312.5"), evse + D("312.5")))
        t += MIN
        i += 1
    return muestras


def mercado(conn: psycopg.Connection, dia: date) -> None:
    inicio, fin = limites_dia_utc(dia)
    filas, t, i = [], inicio, 0
    while t < fin:
        filas.append((t, t + QH, FUENTE, D(40) + D(i % 9) * D("7.25")))
        t += QH
        i += 1
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh) VALUES (%s, %s, %s, %s)",
                        filas)
    # v_coste_tramo y v_sesion_coste leen mv_precio_final: como el colector, refrescar tras guardar.
    db.refrescar_precio_final(conn)


def sesion(conn: psycopg.Connection, inicio: datetime, fin: datetime, wh: str, id_: int | None = None) -> None:
    ident = id_ if id_ is not None else int(inicio.timestamp())
    s = Sesion(ident, "mennekes", inicio, fin, D(wh), "schedman", "evsm",
               f"{ident},x,x,mennekes,x,x,{int(inicio.timestamp())},x,schedman,x,{int(fin.timestamp())},"
               f"x,evsm,x,{wh},{wh},0,0")
    muestreo.guardar_sesiones(conn, [s])


def cerca(a: D | None, b: D | None, tolerancia: str = "1e-9") -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= D(tolerancia)


# --- Persistencia ------------------------------------------------------------------------


T0 = datetime(2026, 11, 5, 20, 0, tzinfo=UTC)
MOD = Modulador(D(4300), D(250), D(4550))


def test_guardar_muestra_con_intervalo_y_huecos(conn: psycopg.Connection, limpio: None) -> None:
    assert muestreo.guardar_muestra(conn, T0, MOD, 4) is None
    assert muestreo.guardar_muestra(conn, T0 + MIN, MOD, 5) == 60
    assert muestreo.guardar_muestra(conn, T0 + MIN, MOD, 5) is None  # repetida: no se inserta
    assert muestreo.guardar_muestra(conn, T0 + 4 * MIN, MOD, 5) == 180
    assert muestreo.guardar_muestra(conn, T0 + 8 * MIN, MOD, 5) is None  # hueco de 240 s
    filas = conn.execute("SELECT intervalo_s, estado FROM cargador_muestra ORDER BY instante").fetchall()
    assert filas == [(None, 4), (60, 5), (180, 5), (None, 5)]


def test_estado_solo_registra_cambios(conn: psycopg.Connection, limpio: None) -> None:
    def ver(t: datetime, estado: int) -> bool:
        return muestreo.registrar_estado(conn, t, EstadoConector(estado, "ev", "id1", 1793483435))

    assert ver(T0, 4)
    assert not ver(T0 + MIN, 4)
    assert ver(T0 + 2 * MIN, 6)
    assert ver(T0 + 3 * MIN, 4)
    filas = conn.execute("SELECT detectado_en, estado, estado_anterior, localtime_raw FROM cargador_estado"
                         " ORDER BY detectado_en").fetchall()
    assert filas == [(T0, 4, None, 1793483435), (T0 + 2 * MIN, 6, 4, 1793483435), (T0 + 3 * MIN, 4, 6, 1793483435)]


def test_sesiones_idempotentes(conn: psycopg.Connection, limpio: None) -> None:
    a = Sesion(1, "mennekes", T0, T0 + timedelta(hours=2), D(9000), "schedman", "evsm", "linea a")
    b = Sesion(2, "mennekes", T0 + timedelta(days=1), T0 + timedelta(days=1, hours=1), D(4000), "evsm", "evsm", "b")
    assert muestreo.guardar_sesiones(conn, [a, b]) == (2, 0)
    assert muestreo.guardar_sesiones(conn, [a, b]) == (0, 0)
    a2 = Sesion(1, "mennekes", T0, T0 + timedelta(hours=2), D(9100), "schedman", "evsm", "linea a2")
    assert muestreo.guardar_sesiones(conn, [a2, b]) == (0, 1)
    assert conn.execute("SELECT energia_wh FROM cargador_sesion WHERE id = 1").fetchone() == (D(9100),)


# --- Energía por tramo -------------------------------------------------------------------


def tramos_medidos(conn: psycopg.Connection) -> dict[datetime, tuple]:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT inicio, coche_wh, casa_wh, total_wh, cubierto_s, incompleto FROM v_energia_tramo_medida")}


def test_energia_por_tramo_igual_a_la_referencia_con_huecos(conn: psycopg.Connection, limpio: None) -> None:
    # Potencia variable, muestras a deshora (37 s) y un hueco de 5 min a mitad.
    inicio = T0 + timedelta(seconds=37)
    muestras = (serie(inicio, inicio + 40 * MIN, lambda i: D(1000) + D(i * 37 % 3300) + D("0.5"))
                + serie(inicio + 45 * MIN, inicio + 70 * MIN, lambda i: D(4200 + i)))
    insertar_muestras(conn, muestras)
    esperado = energia_por_tramo(muestras)
    obtenido = tramos_medidos(conn)
    assert set(obtenido) == set(esperado)
    for tramo, e in esperado.items():
        coche, casa, total, cubierto, incompleto = obtenido[tramo]
        assert cerca(coche, e.coche_wh) and cerca(casa, e.casa_wh) and cerca(total, e.total_wh), tramo
        assert (cubierto, incompleto) == (e.cubierto_s, e.incompleto), tramo
    # 20:00 cubre 863 s (completo); el hueco de 20:40:37 a 20:45:37 deja el de las 20:30 en 637 s y el
    # de las 20:45 en 863 s; el último acaba a las 21:10:37.
    incompletos = {t for t, fila in obtenido.items() if fila[4]}
    assert incompletos == {T0 + 2 * QH, T0 + 4 * QH}


def test_dia_del_cambio_de_hora(conn: psycopg.Connection, limpio: None) -> None:
    inicio, fin = limites_dia_utc(date(2026, 10, 25))
    insertar_muestras(conn, serie(inicio, fin, lambda i: D(1000)))
    fila = conn.execute(
        "SELECT count(*), sum(coche_wh), bool_or(incompleto) FROM v_energia_tramo_medida"
        " WHERE (inicio AT TIME ZONE 'Europe/Madrid')::date = '2026-10-25'"
    ).fetchone()
    assert fila == (100, D(25_000), False)  # 25 h a 1 kW


# --- Casa desconocida durante la carga (0013) ----------------------------------------------


def test_casa_desconocida_en_tramos_con_carga(conn: psycopg.Connection, limpio: None) -> None:
    # Sin carga hasta las 20:20 y cargando desde las 20:21: como en el registro del 14/11/2026,
    # home_w = 0 y total_w por debajo de evse_w durante la carga.
    muestras = [Muestra(T0 + i * MIN, None if i == 0 else 60,
                        D(0) if i <= 20 else D(4400),
                        D(350) if i <= 20 else D(0),
                        D(350) if i <= 20 else D(4182))
                for i in range(61)]
    insertar_muestras(conn, muestras)
    obtenido = tramos_medidos(conn)
    esperado = energia_por_tramo(muestras)
    assert set(obtenido) == set(esperado) == {T0, T0 + QH, T0 + 2 * QH, T0 + 3 * QH}
    for tramo, e in esperado.items():
        coche, casa, total, *_ = obtenido[tramo]
        assert cerca(coche, e.coche_wh) and cerca(casa, e.casa_wh) and cerca(total, e.total_wh), tramo
    assert obtenido[T0][1] == D("87.5")              # 15 min a 350 W
    assert obtenido[T0 + QH][1] is None              # mixto: carga desde las 20:20
    assert obtenido[T0 + 2 * QH][1] is None          # todo cargando: desconocida, no 0
    assert obtenido[T0 + QH][2] > 0                  # total se mantiene
    # Sin fila de casa (ni coste 0) en los tramos con carga.
    mercado(conn, DIA)
    casa = {r[0] for r in conn.execute("SELECT inicio FROM v_coste_tramo WHERE consumidor = 'casa'"
                                       " AND fuente = %s", (FUENTE,))}
    assert casa == {T0}


# --- energia_tramo mantenida por el muestreo (0013) ----------------------------------------


def guardado_igual_al_calculo(conn: psycopg.Connection) -> bool:
    c = muestreo.coherencia_tramos(conn, datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    return c.distintos == 0


def test_muestra_que_cruza_el_cuarto_de_hora_recalcula_los_dos_tramos(conn: psycopg.Connection,
                                                                     limpio: None) -> None:
    base = datetime(2026, 11, 5, 10, 0, 30, tzinfo=UTC)
    for i in range(14):  # 10:00:30 a 10:13:30
        muestreo.guardar_muestra(conn, base + i * MIN, MOD, 6)
    antes = conn.execute("SELECT inicio, cubierto_s FROM energia_tramo").fetchall()
    assert antes == [(base.replace(second=0), D(780))]
    # 10:14:30 -> 10:15:30 cubre 30 s de cada tramo.
    muestreo.guardar_muestra(conn, base + 14 * MIN, MOD, 6)
    muestreo.guardar_muestra(conn, base + 15 * MIN, MOD, 6)
    filas = dict(conn.execute("SELECT inicio, cubierto_s FROM energia_tramo ORDER BY inicio").fetchall())
    assert filas == {base.replace(second=0): D(870), base.replace(minute=15, second=0): D(30)}
    coche = dict(conn.execute("SELECT inicio, coche_wh FROM energia_tramo").fetchall())
    assert cerca(coche[base.replace(minute=15, second=0)], MOD.evse_w * 30 / 3600)
    assert guardado_igual_al_calculo(conn)


def test_muestra_repetida_no_duplica_energia(conn: psycopg.Connection, limpio: None) -> None:
    for i in range(5):
        muestreo.guardar_muestra(conn, T0 + i * MIN, MOD, 6)
    antes = conn.execute("SELECT * FROM energia_tramo").fetchall()
    muestreo.guardar_muestra(conn, T0 + 4 * MIN, MOD, 6)
    assert muestreo.recalcular_tramos(conn, T0, T0 + 4 * MIN) == 1
    assert conn.execute("SELECT * FROM energia_tramo").fetchall() == antes
    assert cerca(conn.execute("SELECT coche_wh FROM energia_tramo").fetchone()[0], MOD.evse_w * 240 / 3600)  # type: ignore[index]


def test_tras_un_hueco_la_muestra_no_cuenta(conn: psycopg.Connection, limpio: None) -> None:
    muestreo.guardar_muestra(conn, T0, MOD, 6)
    muestreo.guardar_muestra(conn, T0 + MIN, MOD, 6)
    muestreo.guardar_muestra(conn, T0 + 6 * MIN, MOD, 6)  # hueco de 5 min: intervalo NULL
    assert conn.execute("SELECT cubierto_s FROM energia_tramo").fetchall() == [(D(60),)]
    assert guardado_igual_al_calculo(conn)


def test_ciclo_del_muestreador_mantiene_energia_tramo(conn: psycopg.Connection, limpio: None) -> None:
    ahora = [T0 - 30 * MIN]
    m = muestreo.Muestreador(ClienteFalso(), lambda: ahora[0])  # type: ignore[arg-type]
    for _ in range(40):
        m.ciclo(conn)
        ahora[0] += MIN
    assert conn.execute("SELECT count(*) FROM energia_tramo").fetchone() == (3,)
    assert guardado_igual_al_calculo(conn)


def test_estado_avisa_si_energia_tramo_no_coincide_y_recalcular_lo_corrige(
        conn: psycopg.Connection, nombre_bd: str, limpio: None, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    from energia_monitor import __main__ as cli

    monkeypatch.setattr(db, "conectar", lambda **_: psycopg.connect(dbname=nombre_bd, autocommit=True))
    monkeypatch.delenv("VIARIS_URL", raising=False)
    ahora = datetime.now(UTC).replace(microsecond=0)
    for i in range(30, 0, -1):
        muestreo.guardar_muestra(conn, ahora - i * MIN, MOD, 6)
    assert cli.main(["cargador", "estado"]) == 0
    assert "coinciden con las muestras" in capsys.readouterr().out

    # Muestras insertadas por SQL sin recalcular y un tramo alterado a mano.
    conn.execute("INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w)"
                 " VALUES (%s, 60, 0, 300, 300)", (ahora,))
    conn.execute("UPDATE energia_tramo SET coche_wh = coche_wh + 1 WHERE inicio = (SELECT min(inicio)"
                 " FROM energia_tramo)")
    assert cli.main(["cargador", "estado"]) == 1
    salida = capsys.readouterr().out
    assert "AVISO: energía por tramo" in salida and "cargador recalcular --desde" in salida

    desde = (ahora - timedelta(days=1)).astimezone(muestreo.MADRID).date()
    assert cli.main(["cargador", "recalcular", "--desde", desde.isoformat()]) == 0
    assert "energia_tramo recalculada" in capsys.readouterr().out
    assert cli.main(["cargador", "estado"]) == 0
    assert guardado_igual_al_calculo(conn)


# --- Estimación de sesiones anteriores al muestreo (E1.7) --------------------------------


def test_estimacion_igual_a_la_referencia(conn: psycopg.Connection, limpio: None) -> None:
    ini = datetime(2026, 11, 4, 21, 52, 13, tzinfo=UTC)
    sesion(conn, ini, ini + timedelta(hours=10), "41250")                               # cabe: 4,5 kW
    sesion(conn, ini + timedelta(days=1), ini + timedelta(days=1, minutes=50), "9000")  # no cabe: uniforme
    filas = conn.execute("SELECT sesion, inicio, coche_wh, potencia_w FROM v_energia_sesion_estimada").fetchall()
    for s_ini, s_fin, wh in ((ini, ini + timedelta(hours=10), D(41250)),
                             (ini + timedelta(days=1), ini + timedelta(days=1, minutes=50), D(9000))):
        esperado = reparto_estimado(s_ini, s_fin, wh, D(4500))
        obtenido = {f[1]: f[2] for f in filas if f[0] == int(s_ini.timestamp())}
        assert set(obtenido) == set(esperado)
        assert all(cerca(obtenido[t], esperado[t], "1e-6") for t in esperado)
        assert cerca(sum(obtenido.values()), wh, "1e-6")
    assert {f[3] for f in filas} == {D(4500)}


def test_estimado_nunca_convive_con_un_tramo_medido(conn: psycopg.Connection, limpio: None) -> None:
    ini = datetime(2026, 11, 4, 23, 0, tzinfo=UTC)
    fin = ini + timedelta(hours=2, minutes=5)  # 01:05
    sesion(conn, ini, fin, "9300")  # 2 h 4 min a 4,5 kW: llega al tramo de las 01:00
    insertar_muestras(conn, serie(fin + 2 * MIN, fin + 30 * MIN, lambda i: D(0)))  # desde las 01:07
    tramos = conn.execute("SELECT inicio, origen FROM v_energia_tramo ORDER BY inicio").fetchall()
    inicios = [t[0] for t in tramos]
    assert len(inicios) == len(set(inicios))
    origen = dict(tramos)
    assert origen[datetime(2026, 11, 5, 1, 0, tzinfo=UTC)] == "muestreo"
    assert origen[datetime(2026, 11, 5, 0, 45, tzinfo=UTC)] == "estimado_sesion"
    assert origen[ini] == "estimado_sesion"


def test_sesion_con_muestras_no_se_estima(conn: psycopg.Connection, limpio: None) -> None:
    ini = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)
    insertar_muestras(conn, serie(ini + timedelta(minutes=30), ini + timedelta(minutes=31)))
    sesion(conn, ini, ini + timedelta(hours=3), "12000")
    assert conn.execute("SELECT count(*) FROM v_energia_sesion_estimada").fetchone() == (0,)


def test_potencia_de_la_estimacion_es_un_parametro(conn: psycopg.Connection, limpio: None) -> None:
    parametros.registrar_parametro_cargador(conn, clave="potencia_max_estimacion_w", desde=date(2026, 11, 1),
                                            valor=D(3000), origen="estimado", fuente="test")
    ini = datetime(2026, 11, 4, 21, 0, tzinfo=UTC)
    sesion(conn, ini, ini + timedelta(hours=10), "6000")
    fila = conn.execute("SELECT count(*), max(fin), min(potencia_w) FROM v_energia_sesion_estimada").fetchone()
    assert fila == (8, ini + timedelta(hours=2), D(3000))


# --- Coste ---------------------------------------------------------------------------------


def precios(conn: psycopg.Connection) -> dict[datetime, tuple]:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT inicio, precio_final_eur_kwh, periodo, impuesto_electrico, iva FROM v_precio_final"
        " WHERE contrato = 'vivienda' AND fuente = %s", (FUENTE,))}


def test_coste_por_tramo_en_numeric(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, DIA)
    ini = datetime(2026, 11, 5, 9, 58, 20, tzinfo=UTC)
    # Sin carga hasta las 10:18:20: la casa tiene coste en los tramos de las 9:45 y las 10:00.
    muestras = serie(ini, ini + timedelta(hours=2), lambda i: D(0) if i <= 20 else D(4100) + D(i % 5) * D("33.3"))
    insertar_muestras(conn, muestras)
    energia = energia_por_tramo(muestras)
    precio = precios(conn)
    filas = conn.execute(
        "SELECT inicio, consumidor, energia_wh_bruta, factor, factor_origen, energia_kwh, coste_eur, periodo,"
        " coste_pvpc_eur, origen_energia, incompleto, ssaa_estimado FROM v_coste_tramo"
        " WHERE contrato = 'vivienda' AND fuente = %s ORDER BY inicio, consumidor", (FUENTE,)).fetchall()
    assert len(filas) == len(energia) + sum(e.casa_wh is not None for e in energia.values())
    assert {f[0] for f in filas if f[1] == "casa"} == {ini.replace(minute=45, second=0), ini.replace(minute=0,
                                                       hour=10, second=0)}
    with localcontext() as ctx:
        ctx.prec = 80
        for inicio, consumidor, bruta, factor, f_origen, kwh, coste, periodo, pvpc, origen, incompleto, ssaa_est in filas:
            e = energia[inicio]
            esperado_wh = e.coche_wh if consumidor == "coche" else e.casa_wh
            assert esperado_wh is not None
            esperado_factor = D("0.96") if consumidor == "coche" else D(1)
            assert cerca(bruta, esperado_wh)
            assert factor == esperado_factor
            assert f_origen == ("estimado" if consumidor == "coche" else None)
            assert cerca(kwh, esperado_wh * esperado_factor / 1000, "1e-12")
            assert cerca(coste, esperado_wh * esperado_factor / 1000 * precio[inicio][0], "1e-12")
            assert periodo == precio[inicio][1]
            assert pvpc is None  # sin serie del PVPC
            assert origen == "muestreo" and incompleto == e.incompleto
            assert ssaa_est  # SSAA del parámetro estimado


def test_coste_con_pvpc(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, DIA)
    hora = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    insertar_muestras(conn, serie(hora, hora + timedelta(hours=1)))
    indicador, geo = conn.execute("SELECT id, geo_id FROM esios_indicador WHERE uso = 'pvpc'").fetchone()  # type: ignore[misc]
    conn.execute("INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor)"
                 " VALUES (%s, %s, %s, %s, 150.5, %s, 150.5)", (indicador, geo, hora, hora + timedelta(hours=1), hora))
    precio = precios(conn)
    filas = conn.execute("SELECT inicio, energia_kwh, coste_pvpc_eur FROM v_coste_tramo WHERE contrato = 'vivienda'"
                         " AND fuente = %s AND consumidor = 'coche' ORDER BY inicio", (FUENTE,)).fetchall()
    with localcontext() as ctx:
        ctx.prec = 80
        for inicio, kwh, pvpc in filas:
            if hora <= inicio < hora + timedelta(hours=1):
                _, _, iee, iva = precio[inicio]
                assert cerca(pvpc, kwh * D("150.5") / 1000 * (1 + iee) * (1 + iva), "1e-12")
            else:
                assert pvpc is None


def test_factor_del_contador_con_vigencia(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, DIA)
    insertar_muestras(conn, serie(datetime(2026, 11, 5, 10, 0, tzinfo=UTC), datetime(2026, 11, 5, 10, 30, tzinfo=UTC)))
    registro = parametros.registrar_parametro_cargador(conn, clave="factor_contador", desde=DIA, valor=D("0.95"),
                                                       origen="estimado", fuente="test")
    assert registro.cerrada == "[2026-01-01,)" and registro.nueva == "[2026-11-05,)"
    factores = {r[0] for r in conn.execute(
        "SELECT factor FROM v_coste_tramo WHERE consumidor = 'coche' AND fuente = %s", (FUENTE,))}
    assert factores == {D("0.95")}
    assert [f[0] for f in parametros.listar_parametros_cargador(conn, DIA)] == [
        "factor_contador", "potencia_max_estimacion_w"]


def test_coste_de_sesiones_que_comparten_cuarto_de_hora(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, DIA)
    a_ini, a_fin = datetime(2026, 11, 5, 10, 0, tzinfo=UTC), datetime(2026, 11, 5, 10, 7, 30, tzinfo=UTC)
    b_ini, b_fin = datetime(2026, 11, 5, 10, 8, tzinfo=UTC), datetime(2026, 11, 5, 10, 40, tzinfo=UTC)
    muestras = serie(a_ini - 2 * MIN, b_fin + 5 * MIN, lambda i: D(3000) + D(i * 10))
    insertar_muestras(conn, muestras)
    sesion(conn, a_ini, a_fin, "420")
    sesion(conn, b_ini, b_fin, "2100")
    precio = precios(conn)
    filas = {r[0]: r[1:] for r in conn.execute(
        "SELECT inicio, origen_energia, energia_wh_bruta, energia_kwh, coste_eur, eur_kwh, diferencia_pct"
        " FROM v_sesion_coste WHERE contrato = 'vivienda' AND fuente = %s", (FUENTE,))}
    with localcontext() as ctx:
        ctx.prec = 80
        for ini, fin, wh in ((a_ini, a_fin, D(420)), (b_ini, b_fin, D(2100))):
            propias = [m for m in muestras if ini <= m.instante < fin]
            energia = energia_por_tramo(propias)
            origen, bruta, kwh, coste, eur_kwh, dif = filas[ini]
            esperado_wh = sum(e.coche_wh for e in energia.values())
            esperado_coste = sum(e.coche_wh * D("0.96") / 1000 * precio[t][0] for t, e in energia.items())
            assert origen == "muestreo"
            assert cerca(bruta, esperado_wh)
            assert cerca(kwh, esperado_wh * D("0.96") / 1000, "1e-12")
            assert cerca(coste, esperado_coste, "1e-12")
            assert cerca(eur_kwh, esperado_coste / (esperado_wh * D("0.96") / 1000), "1e-12")
            assert cerca(dif, (esperado_wh - wh) * 100 / wh, "1e-9")
    # El tramo de las 10:00 lo comparten las dos sesiones: cada muestra cuenta una sola vez.
    total_tramo = conn.execute("SELECT coche_wh FROM v_energia_tramo WHERE inicio = %s", (a_ini,)).fetchone()[0]  # type: ignore[index]
    parte_a = sum(e.coche_wh for t, e in energia_por_tramo(
        [m for m in muestras if a_ini <= m.instante < a_fin]).items() if t == a_ini)
    parte_b = sum(e.coche_wh for t, e in energia_por_tramo(
        [m for m in muestras if b_ini <= m.instante < b_fin]).items() if t == a_ini)
    assert parte_a > 0 and parte_b > 0
    assert cerca(parte_a + parte_b, total_tramo)


def test_sesion_estimada_con_coste(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, DIA)
    ini = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
    sesion(conn, ini, ini + timedelta(hours=4), "9000")
    precio = precios(conn)
    fila = conn.execute("SELECT origen_energia, energia_kwh, coste_eur, diferencia_pct FROM v_sesion_coste"
                        " WHERE fuente = %s", (FUENTE,)).fetchone()
    assert fila is not None
    esperado = sum(wh * D("0.96") / 1000 * precio[t][0]
                   for t, wh in reparto_estimado(ini, ini + timedelta(hours=4), D(9000), D(4500)).items())
    assert fila[0] == "estimado_sesion"
    assert cerca(fila[1], D("8.64"), "1e-6")
    assert cerca(fila[2], esperado, "1e-6")
    assert fila[3] is None


# --- Permisos de Grafana -------------------------------------------------------------------


def test_grafana_ro_lee_los_objetos_nuevos(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    from energia_monitor import db

    for objeto in OBJETOS_NUEVOS:
        assert conn.execute("SELECT has_table_privilege('grafana_ro', %s, 'SELECT')", (objeto,)).fetchone() == (
            True,), objeto
    db.configurar_rol_grafana(conn, "clave-de-test")
    with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password="clave-de-test",
                         host=os.environ["PGHOST"], autocommit=True) as ro:
        for objeto in OBJETOS_NUEVOS:
            ro.execute(f"SELECT * FROM {objeto} LIMIT 1")  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("DELETE FROM cargador_muestra")


# --- Muestreador, diagnóstico y servicio ---------------------------------------------------


class ClienteFalso:
    base = "http://cargador-falso"

    def __init__(self, stop: threading.Event | None = None, ciclos: int = 1) -> None:
        self.stop = stop
        self.ciclos = ciclos
        self.caido = False

    def modulador(self) -> Modulador:
        if self.caido:
            raise ErrorCargador("sin respuesta")
        self.ciclos -= 1
        if self.stop is not None and self.ciclos <= 0:
            self.stop.set()
        return MOD

    def estado_conector(self) -> EstadoConector:
        return EstadoConector(5, None, None, None)

    def historico(self) -> list[Sesion]:
        return [Sesion(7, "mennekes", T0, T0 + timedelta(hours=1), D(4300), "schedman", "evsm", "linea 7")]


def test_ciclo_del_muestreador(conn: psycopg.Connection, limpio: None) -> None:
    ahora = [T0]
    m = muestreo.Muestreador(ClienteFalso(), lambda: ahora[0])  # type: ignore[arg-type]
    m.ciclo(conn)
    ahora[0] += MIN
    m.ciclo(conn)
    assert conn.execute("SELECT count(*), max(intervalo_s) FROM cargador_muestra").fetchone() == (2, 60)
    assert conn.execute("SELECT count(*) FROM cargador_estado").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM cargador_sesion").fetchone() == (1,)


def test_servicio_de_muestreo(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    stop = threading.Event()
    muestreo.ejecutar(ClienteFalso(stop), stop,  # type: ignore[arg-type]
                      conectar=lambda: psycopg.connect(dbname=nombre_bd, autocommit=True))
    assert conn.execute("SELECT count(*) FROM cargador_muestra").fetchone() == (1,)


def test_estado_y_huecos_del_dia(conn: psycopg.Connection, limpio: None) -> None:
    ahora = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    inicio_dia, _ = limites_dia_utc(DIA)
    insertar_muestras(conn, serie(inicio_dia, inicio_dia + timedelta(hours=2)), estado=4)
    insertar_muestras(conn, serie(inicio_dia + timedelta(hours=3), ahora - 2 * MIN), estado=4)
    sesion(conn, T0 - timedelta(days=1), T0 - timedelta(days=1) + timedelta(hours=2), "9000")
    huecos = muestreo.huecos_del_dia(conn, ahora)
    assert huecos == [muestreo.Hueco(inicio_dia + timedelta(hours=2), inicio_dia + timedelta(hours=3))]

    cliente = ClienteFalso()
    lineas = muestreo.estado(conn, cliente, ahora)  # type: ignore[arg-type]
    assert lineas[0].startswith("Cargador http://cargador-falso: alcanzable")
    assert "hace 120 s" in lineas[1]
    assert lineas[2].startswith("Sesiones guardadas: 1;")
    assert lineas[3] == "Huecos de hoy: 1"
    assert lineas[-1].startswith("Energía por tramo (últimos 2 días):") and "coinciden" in lineas[-1]
    cliente.caido = True
    assert "NO alcanzable" in muestreo.estado(conn, cliente, ahora)[0]  # type: ignore[arg-type]
    assert "VIARIS_URL no definida" in muestreo.estado(conn, None, ahora)[0]
    # Muestreo parado: el final del día también es un hueco.
    assert len(muestreo.huecos_del_dia(conn, ahora + timedelta(minutes=10))) == 2


# --- Consultas de "Energía: coche" ------------------------------------------------------------


def sql_panel(titulo: str, ahora: datetime, desde: datetime, hasta: datetime) -> str:
    panel = next(p for p in DASHBOARD["panels"] if p["title"] == titulo)
    sql = (panel["targets"][0]["rawSql"]
           .replace("$contrato", "vivienda").replace("$fuente", FUENTE)
           .replace("now()", f"'{ahora.isoformat()}'::timestamptz"))
    sql = re.sub(r"\$__timeFilter\((\w+)\)", rf"\1 BETWEEN '{desde.isoformat()}' AND '{hasta.isoformat()}'", sql)
    assert "$" not in sql
    return sql


@pytest.mark.parametrize("titulo", ["Gasto del coche", "Gasto en el rango", "Precio medio pagado", "Frente al PVPC",
                                    "Estado del cargador", "Gasto diario del coche (30 días)", "Reparto por periodo",
                                    "Sesiones recientes"])
def test_consultas_de_la_fila_coche(conn: psycopg.Connection, limpio: None, titulo: str) -> None:
    mercado(conn, DIA)
    mercado(conn, DIA - timedelta(days=1))
    ini = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    insertar_muestras(conn, serie(ini, ini + timedelta(hours=2)), estado=6)
    sesion(conn, ini + MIN, ini + timedelta(hours=1, minutes=30), "6000")
    sesion(conn, datetime(2026, 11, 4, 1, 0, tzinfo=UTC), datetime(2026, 11, 4, 6, 0, tzinfo=UTC), "9000")
    indicador, geo = conn.execute("SELECT id, geo_id FROM esios_indicador WHERE uso = 'pvpc'").fetchone()  # type: ignore[misc]
    conn.execute("INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor)"
                 " VALUES (%s, %s, %s, %s, 150, %s, 150)", (indicador, geo, ini, ini + timedelta(hours=1), ini))
    ahora = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    cur = conn.execute(sql_panel(titulo, ahora, datetime(2026, 11, 3, 23, 0, tzinfo=UTC), ahora))
    nombres = [d.name for d in cur.description or []]
    filas = cur.fetchall()
    if titulo == "Gasto diario del coche (30 días)":
        # Un día solo estimado no tiene "Medido" y viceversa; la energía siempre. Sin cargas externas, nada fuera.
        assert [(f[1] is None, f[2] is None, f[3]) for f in filas] == [(True, False, None), (False, True, None)], filas
        assert cerca(filas[0][4], D("8.64"), "1e-6") and cerca(filas[1][4], D("8.448"))
        return
    if titulo == "Estado del cargador":
        # Cargando: la casa es desconocida (homePower = 0 durante la carga).
        assert filas == [("Cargando", D(4400), None, 3600)]
        return
    # Sin cargas externas, el €/kWh de fuera queda vacío (no 0 ni el de casa); el resto, con valor.
    vacios = {n for n in nombres if "fuera" in n.lower() and "€/kWh" in n}
    assert filas and all(v is not None for n, v in zip(nombres, filas[0]) if n not in vacios), filas
    assert all(v is None for n, v in zip(nombres, filas[0]) if n in vacios), filas
    if titulo == "Gasto del coche":
        f = dict(zip(nombres, filas[0]))
        assert f["Hoy casa (kWh)"] > 0 and cerca(f["Mes casa estimado (kWh)"], D("8.64"), "1e-6")
        assert f["Mes casa (kWh)"] > f["Hoy casa (kWh)"]
        assert (f["Hoy fuera (€)"], f["Mes fuera (€)"], f["Mes fuera (kWh)"]) == (0, 0, 0)
    if titulo == "Sesiones recientes":
        assert [f[5] for f in filas] == ["Medida", "Estimada"]
