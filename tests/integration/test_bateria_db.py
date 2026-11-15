"""Fase 2, etapa 2 contra PostgreSQL: recalcular_bateria() y las funciones de
previsión frente a la referencia en Python (energia_monitor.bateria).

Mismos escenarios que tests/test_bateria.py (tests/escenarios_bateria.py) y
historias aleatorias. Nunca se contacta con el cargador.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import psycopg
import pytest
from escenarios_bateria import ESCENARIOS, aleatorio
from muestras_log import ClienteLog, entradas, leer

from energia_monitor import bateria, bateria_servicio, db, parametros
from energia_monitor.muestreo import Muestreador

MIGRACIONES = Path(__file__).resolve().parents[2] / "db" / "migrations"
TABLAS_USUARIO = ("bateria_lectura", "bateria_excepcion", "bateria_horario")
TABLAS_CARGADOR = ("cargador_muestra", "cargador_estado", "cargador_sesion", "energia_tramo")


def limpiar(conn: psycopg.Connection) -> None:
    for tabla in (*TABLAS_USUARIO, *TABLAS_CARGADOR):
        conn.execute(f"DELETE FROM {tabla}")  # type: ignore[arg-type]
    cargar_parametros(conn, bateria.Parametros())
    conn.execute("SELECT recalcular_bateria()")


@pytest.fixture
def limpio(conn: psycopg.Connection):
    limpiar(conn)
    yield
    limpiar(conn)


def cargar_parametros(conn: psycopg.Connection, p: bateria.Parametros) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM bateria_parametro")
        for f in p.filas():
            conn.execute(
                "INSERT INTO bateria_parametro (clave, vigencia, valor, origen, fuente)"
                " VALUES (%s, daterange(%s, %s, '[)'), %s, 'usuario', 'test')",
                (f.clave, f.desde, f.hasta, f.valor),
            )


def cargar(conn: psycopg.Connection, e: bateria.Entradas) -> None:
    """Las entradas de la referencia, en las tablas que lee recalcular_bateria()."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO bateria_lectura (id, instante, pct, origen) OVERRIDING SYSTEM VALUE"
            " VALUES (%s, %s, %s, 'cli')",
            [(lec.id, lec.instante, lec.pct) for lec in e.lecturas],
        )
        cur.executemany(
            "INSERT INTO cargador_sesion (conector, id, inicio, fin, energia_wh, origen_inicio, origen_fin, linea)"
            " VALUES (%s, %s, %s, %s, %s, 'schedman', %s, 'test')",
            [(s.conector, s.id, s.inicio, s.fin, s.energia_wh, s.origen_fin) for s in e.sesiones],
        )
        cur.executemany(
            "INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w, estado)"
            " VALUES (%s, %s, %s, 0, %s, %s)",
            [(m.instante, m.intervalo_s, m.evse_w, m.evse_w, m.estado) for m in e.muestras],
        )
        cur.executemany(
            "INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES (%s, %s, %s)",
            [(c.conector, c.detectado_en, c.estado) for c in e.estados],
        )
        cur.executemany(
            "INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo, km, pct, pct_objetivo)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(x.tipo, x.fecha, x.hora_salida, x.consumo_modo, x.km, x.pct, x.pct_objetivo) for x in e.excepciones],
        )
        cur.executemany(
            "INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (%s, %s)",
            list(e.horario.items()),
        )
    if any(f.clave and f not in bateria.Parametros().filas() for f in e.parametros.filas()):
        cargar_parametros(conn, e.parametros)


def cerca(a: Any, b: Any, tol: str = "1e-9") -> bool:
    if isinstance(a, (D, float, int)) and isinstance(b, (D, float, int)) and not isinstance(a, bool):
        return abs(D(str(a)) - D(str(b))) <= D(tol)
    return a == b


def iguales(sql: list[tuple], ref: list[tuple], que: str) -> None:
    assert len(sql) == len(ref), f"{que}: {len(sql)} filas en SQL y {len(ref)} en la referencia"
    for fila_sql, fila_ref in zip(sql, ref):
        assert len(fila_sql) == len(fila_ref)
        assert all(cerca(a, b) for a, b in zip(fila_sql, fila_ref)), f"{que}:\n  SQL {fila_sql}\n  ref {fila_ref}"


def comparar(conn: psycopg.Connection, e: bateria.Entradas, ahora: datetime) -> bateria.Resultado:
    cargar(conn, e)
    return verificar(conn, e, ahora)


def verificar(conn: psycopg.Connection, e: bateria.Entradas, ahora: datetime) -> bateria.Resultado:
    """Recalcula en SQL y compara todas las tablas derivadas y la previsión con la referencia."""
    n = conn.execute("SELECT recalcular_bateria(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    r = bateria.recalcular(e, ahora)
    assert n == len(r.eventos)

    iguales(conn.execute("SELECT conector, id, inicio, fin, energia_wh, factor, cierre FROM bateria_sesion"
                         " ORDER BY conector, id").fetchall(),
            [(s.conector, s.id, s.inicio, s.fin, s.energia_wh, s.factor, s.cierre)
             for s in sorted(r.sesiones, key=lambda s: (s.conector, s.id))], "sesiones")
    iguales(conn.execute("SELECT conector, salida, llegada, corto, viaje_largo, consumo_estimado_kwh, consumo_kwh,"
                         " origen_consumo, reconciliado_en FROM bateria_trayecto ORDER BY conector, salida").fetchall(),
            [(t.conector, t.salida, t.llegada, t.corto, t.viaje_largo, t.consumo_estimado_kwh, t.consumo_kwh,
              t.origen_consumo, t.reconciliado_en) for t in r.trayectos], "trayectos")
    iguales(conn.execute("SELECT n, instante, orden, ref, tipo, conector, sesion_id, trayecto_salida, lectura_id,"
                         " peso, contador_kwh, absoluto_kwh, delta_estimado_kwh, delta_kwh, nivel_estimado_kwh,"
                         " estimado_previo_kwh, nivel_kwh, nivel_pct FROM bateria_nivel ORDER BY n").fetchall(),
            [(ev.n, ev.instante, ev.orden, ev.ref, ev.tipo, ev.conector, ev.sesion_id, ev.trayecto_salida,
              ev.lectura_id, ev.peso, ev.contador_kwh, ev.absoluto_kwh, ev.delta_estimado_kwh, ev.delta_kwh,
              ev.nivel_estimado_kwh, ev.estimado_previo_kwh, ev.nivel_kwh, ev.nivel_pct) for ev in r.eventos],
            "eventos")
    iguales(conn.execute("SELECT desde, hasta, a1_kwh, a2_kwh, sesiones_kwh, contador_kwh, trayectos,"
                         " trayectos_cortos, consumo_estimado_kwh, consumo_real_kwh, estado, huecos,"
                         " factor_observado FROM bateria_intervalo ORDER BY desde").fetchall(),
            [(i.desde, i.hasta, i.a1_kwh, i.a2_kwh, i.sesiones_kwh, i.contador_kwh, i.trayectos,
              i.trayectos_cortos, i.consumo_estimado_kwh, i.consumo_real_kwh, i.estado, i.huecos,
              i.factor_observado) for i in r.intervalos], "intervalos")

    # Previsión y estado.
    iguales(conn.execute("SELECT fecha, salida, tipo, consumo_modo, pct_objetivo, consumo_declarado_kwh"
                         " FROM bateria_salidas(%s, 7)", (ahora,)).fetchall(),
            [(s.fecha, s.salida, s.tipo, s.consumo_modo, s.pct_objetivo, s.consumo_declarado_kwh)
             for s in bateria.salidas(e, ahora, 7)], "salidas")
    iguales(conn.execute("SELECT * FROM bateria_consumo_plan(%s)", (ahora,)).fetchall(),
            [bateria.consumo_plan(e, r.trayectos, ahora)], "consumo para planificar")
    iguales(conn.execute("SELECT * FROM bateria_factor_sugerido(%s)", (ahora,)).fetchall(),
            [bateria.factor_sugerido(e, r.intervalos, ahora)], "factor sugerido")
    est = bateria.estado(e, r, ahora)
    iguales(conn.execute("SELECT nivel_kwh, energia_pendiente_kwh, coche, consumo_en_curso_kwh, nivel_plan_kwh,"
                         " proxima_salida, consumo_plan_kwh, energia_necesaria_kwh FROM bateria_estado(%s)",
                         (ahora,)).fetchall(),
            [(est.nivel_kwh, est.energia_pendiente_kwh, est.coche, est.consumo_en_curso_kwh, est.nivel_plan_kwh,
              est.proxima_salida, est.consumo_plan_kwh, est.energia_necesaria_kwh)], "estado")
    return r


@pytest.mark.parametrize("nombre", sorted(ESCENARIOS))
def test_escenario_igual_que_la_referencia(conn: psycopg.Connection, limpio: None, nombre: str) -> None:
    e, ahora = ESCENARIOS[nombre]()
    comparar(conn, e, ahora)


@pytest.mark.parametrize("semilla", range(60))
def test_historia_aleatoria_igual_que_la_referencia(conn: psycopg.Connection, limpio: None, semilla: int) -> None:
    e, ahora = aleatorio(semilla)
    comparar(conn, e, ahora)


# --- Secuencia real del 13 al 14/11/2026, por el muestreo -----------------------------------------


def entradas_desde_bd(conn: psycopg.Connection) -> bateria.Entradas:
    """Lo que hay en las tablas, como entradas de la referencia."""
    e = bateria.Entradas()
    e.lecturas = [bateria.Lectura(*f) for f in conn.execute(
        "SELECT id, instante, pct FROM bateria_lectura WHERE anulada_en IS NULL ORDER BY id")]
    e.sesiones = [bateria.SesionCargador(*f) for f in conn.execute(
        "SELECT conector, id, inicio, fin, energia_wh, origen_fin FROM cargador_sesion")]
    e.muestras = [bateria.MuestraEstado(*f) for f in conn.execute(
        "SELECT instante, intervalo_s, evse_w, estado FROM cargador_muestra ORDER BY instante")]
    e.estados = [bateria.CambioEstado(*f) for f in conn.execute(
        "SELECT conector, detectado_en, estado FROM cargador_estado ORDER BY detectado_en")]
    e.excepciones = [bateria.Excepcion(*f) for f in conn.execute(
        "SELECT tipo, fecha, hora_salida, consumo_modo, km, pct, pct_objetivo FROM bateria_excepcion"
        " WHERE anulada_en IS NULL ORDER BY fecha")]
    e.horario = dict(conn.execute("SELECT dia_iso, hora_salida FROM bateria_horario").fetchall())
    return e


def reproducir(conn: psycopg.Connection, nombre_bd: str, hasta: datetime | None = None) -> ClienteLog:
    cliente = ClienteLog(leer())
    m = Muestreador(cliente, lambda: cliente.actual.instante)  # type: ignore[arg-type]
    for i, lec in enumerate(cliente.lecturas):
        if hasta is not None and lec.instante > hasta:
            break
        cliente.i = i
        m.ciclo(conn)
    return cliente


def test_secuencia_real_por_el_muestreo(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    cliente = reproducir(conn, nombre_bd)
    ahora = cliente.actual.instante
    assert conn.execute("SELECT count(*) FROM cargador_muestra").fetchone() == (1422,)
    # El muestreo recalculó al cambiar el estado y al leer la sesión: sin recalcular aquí.
    assert conn.execute("SELECT cierre FROM bateria_sesion").fetchall() == [("fin_franja",)]
    assert conn.execute("SELECT salida, llegada, corto, origen_consumo FROM bateria_trayecto").fetchall() == [
        (datetime(2026, 11, 14, 5, 59, 17, tzinfo=UTC), None, False, "inicial")]
    verificar(conn, entradas_desde_bd(conn), ahora)


LECTURA_13_11 = bateria.Lectura(1, datetime(2026, 11, 13, 20, 0, tzinfo=UTC), D(40))


def test_secuencia_real_con_lectura_y_estado(conn: psycopg.Connection, limpio: None) -> None:
    # Lectura la noche del 13 (40 %) y estado justo antes de salir, el 14 a las 06:58 (Madrid).
    e, ahora = entradas(hasta=datetime(2026, 11, 14, 5, 58, tzinfo=UTC))
    e.lecturas.append(LECTURA_13_11)
    comparar(conn, e, ahora)
    fila = conn.execute("SELECT nivel_kwh, coche, energia_pendiente_kwh FROM bateria_estado(%s)", (ahora,)).fetchone()
    assert fila == (D(40) / 100 * D("91.3") + D("13.667") * D("0.86"), "en_casa", 0)


def test_sesion_en_curso_suma_lo_que_marca_el_modulador(conn: psycopg.Connection, limpio: None) -> None:
    # A las 04:00 (Madrid) de la carga: la sesión aún no está en el histórico.
    e, ahora = entradas(hasta=datetime(2026, 11, 14, 3, 0, tzinfo=UTC))
    assert not e.sesiones
    e.lecturas.append(LECTURA_13_11)
    comparar(conn, e, ahora)
    wh = conn.execute("SELECT sum(evse_w * intervalo_s) / 3600 FROM cargador_muestra WHERE instante > %s",
                      (datetime(2026, 11, 13, 20, 0, tzinfo=UTC),)).fetchone()[0]  # type: ignore[index]
    assert 4000 < wh < 4700  # una hora a ~4,5 kW
    coche, pendiente, nivel = conn.execute(
        "SELECT coche, energia_pendiente_kwh, nivel_kwh FROM bateria_estado(%s)", (ahora,)).fetchone()  # type: ignore[misc]
    assert coche == "cargando"
    assert cerca(pendiente, wh / 1000 * D("0.86"))
    assert cerca(nivel, D(40) / 100 * D("91.3") + wh / 1000 * D("0.86"))


# --- Lecturas, estado y CLI -------------------------------------------------------------------------


def usar_bd(monkeypatch: pytest.MonkeyPatch, nombre_bd: str) -> Any:
    from energia_monitor import __main__ as cli

    monkeypatch.setattr(db, "conectar", lambda **_: psycopg.connect(dbname=nombre_bd, autocommit=True))
    return cli


def test_cli_lectura_y_estado(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                              monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    cli = usar_bd(monkeypatch, nombre_bd)
    for dia in range(1, 8):
        conn.execute("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (%s, '09:00')", (dia,))
    conn.execute("INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES ('mennekes', now(), 4)")
    assert cli.main(["bateria", "estado"]) == 1
    assert "AVISO: sin lecturas" in capsys.readouterr().out

    assert cli.main(["bateria", "lectura", "--pct", "62"]) == 0
    salida = capsys.readouterr().out
    assert "lectura 1 registrada: 62 %" in salida
    assert "Batería: 62 % (56.6 kWh) estimado" in salida
    assert "Coche: en casa (estado del conector 4)" in salida
    assert "Próxima salida:" in salida and "energía necesaria" in salida
    assert "AVISO" not in salida
    assert cli.main(["bateria", "estado"]) == 0


@pytest.mark.parametrize(("argumentos", "mensaje"), [
    (["--pct", "101"], "fuera de 0 a 100"),
    (["--pct", "50", "--instante", "2099-01-01 10:00"], "no puede ser futura"),
    (["--pct", "50", "--instante", "2026-03-29 02:30"], "no existe en Madrid"),
    (["--pct", "50", "--instante", "ayer"], "instante no válido"),
])
def test_cli_lectura_no_valida(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                               monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                               argumentos: list[str], mensaje: str) -> None:
    cli = usar_bd(monkeypatch, nombre_bd)
    assert cli.main(["bateria", "lectura", *argumentos]) == 2
    assert mensaje in capsys.readouterr().err
    assert conn.execute("SELECT count(*) FROM bateria_lectura").fetchone() == (0,)


def test_cli_lectura_repetida_y_hora_ambigua(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                             monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    cli = usar_bd(monkeypatch, nombre_bd)
    assert cli.main(["bateria", "lectura", "--pct", "50", "--instante", "2026-10-25 02:30"]) == 0
    assert "ambigua" in capsys.readouterr().out
    # La primera vez, aún en horario de verano (+02:00).
    assert conn.execute("SELECT instante FROM bateria_lectura").fetchone() == (
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),)
    assert cli.main(["bateria", "lectura", "--pct", "55", "--instante", "2026-10-25 02:30"]) == 2
    assert "ya hay una lectura" in capsys.readouterr().err


def test_lectura_anulada_no_cuenta(conn: psycopg.Connection, limpio: None) -> None:
    ahora = datetime(2026, 11, 16, 12, tzinfo=UTC)
    bateria_servicio.registrar_lectura(conn, instante=ahora - timedelta(hours=2), pct=D(50), origen="cli", ahora=ahora)
    ident = bateria_servicio.registrar_lectura(conn, instante=ahora - timedelta(hours=1), pct=D(30), origen="cli",
                                               ahora=ahora)
    conn.execute("UPDATE bateria_lectura SET anulada_en = now(), motivo = 'error de lectura' WHERE id = %s",
                 (ident,))
    bateria_servicio.recalcular(conn, ahora)
    assert conn.execute("SELECT nivel_kwh FROM bateria_estado(%s)", (ahora,)).fetchone() == (D(50) / 100 * D("91.3"),)
    # Anular exige motivo.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE bateria_lectura SET anulada_en = now() WHERE anulada_en IS NULL")


def recientes(horas_atras: float) -> datetime:
    """Instantes relativos al reloj real: el CLI calcula con now()."""
    return datetime.now(UTC).replace(microsecond=0) - timedelta(hours=horas_atras)


def test_cli_recalcular(conn: psycopg.Connection, nombre_bd: str, limpio: None, monkeypatch: pytest.MonkeyPatch,
                        capsys: pytest.CaptureFixture[str]) -> None:
    cli = usar_bd(monkeypatch, nombre_bd)
    conn.execute("INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES"
                 " ('mennekes', %s, 4), ('mennekes', %s, 1), ('mennekes', %s, 4)",
                 (recientes(10), recientes(5), recientes(3)))
    conn.execute("INSERT INTO bateria_lectura (instante, pct, origen) VALUES (%s, 80, 'cli'), (%s, 40, 'cli')",
                 (recientes(6), recientes(2)))
    assert cli.main(["bateria", "recalcular"]) == 0
    assert "batería recalculada: 3 evento(s)" in capsys.readouterr().out
    assert conn.execute("SELECT origen_consumo FROM bateria_trayecto").fetchall() == [("reconciliado",)]


# --- Avisos -----------------------------------------------------------------------------------------


def avisos(conn: psycopg.Connection, ahora: datetime) -> list[str]:
    return conn.execute("SELECT avisos FROM bateria_estado(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]


def test_aviso_de_intervalo_incoherente(conn: psycopg.Connection, limpio: None) -> None:
    e, ahora = ESCENARIOS["intervalo_incoherente"]()
    comparar(conn, e, ahora)
    assert any(a.startswith("intervalo incoherente del 16/11 07:00 al 16/11 12:00") for a in avisos(conn, ahora))


def test_aviso_de_hueco_de_muestreo(conn: psycopg.Connection, limpio: None) -> None:
    e, ahora = ESCENARIOS["calibracion"]()
    comparar(conn, e, ahora)
    # La última muestra es de la hora 6 y ahora es la 7: 60 min sin muestras desde la lectura.
    assert "hueco de muestreo desde el último valor absoluto: nivel dudoso" in avisos(conn, ahora)
    assert conn.execute("SELECT dudoso FROM bateria_estado(%s)", (ahora,)).fetchone() == (True,)
    temprano = ahora - timedelta(minutes=45)
    assert conn.execute("SELECT dudoso FROM bateria_estado(%s)", (temprano,)).fetchone() == (False,)


def test_aviso_de_factor_sugerido(conn: psycopg.Connection, limpio: None) -> None:
    e, ahora = ESCENARIOS["calibracion"]()
    # Factor observado 0,9 frente al 0,86 vigente.
    e.lecturas[-1] = bateria.Lectura(e.lecturas[-1].id, e.lecturas[-1].instante,
                                     (D(20) / 100 * D("91.3") + 27) / D("91.3") * 100)
    comparar(conn, e, ahora)
    assert "factor sugerido 0.900 (vigente 0.86, 1 observación(es))" in avisos(conn, ahora)


def test_aviso_de_sesion_dudosa_y_lectura_antigua(conn: psycopg.Connection, limpio: None) -> None:
    e, ahora = ESCENARIOS["fin_de_franja_y_dudosa"]()
    comparar(conn, e, ahora)
    assert any(a.startswith("sesión terminada el 16/11 06:00 sin muestra posterior") for a in avisos(conn, ahora))
    assert any(a.startswith("sin lecturas desde el 16/11/2026 01:00") for a in avisos(conn, ahora + timedelta(days=8)))


# --- Parámetros -------------------------------------------------------------------------------------


def test_parametro_de_bateria_nuevo_recalcula(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    cli = usar_bd(monkeypatch, nombre_bd)
    conn.execute("INSERT INTO bateria_lectura (instante, pct, origen) VALUES (%s, 50, 'cli')", (recientes(6),))
    conn.execute("INSERT INTO cargador_sesion (conector, id, inicio, fin, energia_wh, origen_fin, linea)"
                 " VALUES ('mennekes', 1, %s, %s, 10000, 'schedman', 'test')", (recientes(5), recientes(2)))
    desde = (recientes(6) - timedelta(days=1)).astimezone(bateria.MADRID).date().isoformat()
    assert cli.main(["parametro", "registrar", "--ambito", "bateria", "--clave", "factor_bateria",
                     "--desde", desde, "--valor", "0.9", "--origen", "estimado", "--fuente", "calibración"]) == 0
    nivel = conn.execute("SELECT nivel_kwh FROM bateria_nivel ORDER BY n DESC LIMIT 1").fetchone()[0]  # type: ignore[index]
    assert nivel == D(50) / 100 * D("91.3") + 10 * D("0.9")
    assert cli.main(["parametro", "listar", "--ambito", "bateria"]) == 0


@pytest.mark.parametrize(("clave", "valor", "origen", "mensaje"), [
    ("detectar_carga_completa", "2", "usuario", "valor no válido"),
    ("percentil_planificacion", "75", "usuario", "valor no válido"),
    ("tope_pct", "120", "usuario", "valor no válido"),
    ("capacidad_util_kwh", "90", "inventado", "origen no válido"),
    ("no_existe", "1", "usuario", "clave desconocida"),
])
def test_parametro_de_bateria_no_valido(conn: psycopg.Connection, limpio: None, clave: str, valor: str,
                                        origen: str, mensaje: str) -> None:
    with pytest.raises(parametros.ErrorParametro, match=mensaje):
        parametros.registrar_parametro_bateria(conn, clave=clave, desde=datetime(2026, 12, 1).date(),
                                               valor=D(valor), origen=origen, fuente="test")


# --- Datos del usuario y permisos ----------------------------------------------------------------------


def test_una_excepcion_activa_por_fecha(conn: psycopg.Connection, limpio: None) -> None:
    conn.execute("INSERT INTO bateria_excepcion (tipo, fecha) VALUES ('sin_salida', '2026-11-20')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo)"
                     " VALUES ('con_salida', '2026-11-20', '07:00', 'normal')")
    conn.execute("UPDATE bateria_excepcion SET anulada_en = now() WHERE fecha = '2026-11-20'")
    conn.execute("INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo)"
                 " VALUES ('con_salida', '2026-11-20', '07:00', 'normal')")


@pytest.mark.parametrize("valores", [
    "('con_salida', '07:00', 'normal', NULL, NULL, NULL)",
    "('con_salida', '07:00', 'indicado', 200, NULL, 80)",
    "('con_salida', '07:00', 'indicado', NULL, 30, NULL)",
    "('sin_salida', NULL, NULL, NULL, NULL, NULL)",
])
def test_combinaciones_validas_de_excepcion(conn: psycopg.Connection, limpio: None, valores: str) -> None:
    conn.execute("INSERT INTO bateria_excepcion (tipo, hora_salida, consumo_modo, km, pct, pct_objetivo, fecha)"
                 f" VALUES {valores[:-1]}, '2026-11-20')")  # type: ignore[arg-type]


@pytest.mark.parametrize("valores", [
    "('sin_coche', NULL, NULL, NULL, NULL, NULL)",                 # tipos de 0014
    "('viaje_largo', '07:00', 'indicado', 200, NULL, NULL)",
    "('con_salida', NULL, 'normal', NULL, NULL, NULL)",            # sin hora
    "('con_salida', '07:00', NULL, NULL, NULL, NULL)",             # sin modo
    "('con_salida', '07:00', 'otro', NULL, NULL, NULL)",
    "('con_salida', '07:00', 'normal', 200, NULL, NULL)",          # normal con km
    "('con_salida', '07:00', 'normal', NULL, 30, NULL)",           # normal con pct
    "('con_salida', '07:00', 'indicado', NULL, NULL, NULL)",       # indicado sin km ni pct
    "('con_salida', '07:00', 'indicado', 200, 30, NULL)",          # km y pct a la vez
    "('con_salida', '07:00', 'normal', NULL, NULL, 0)",            # pct_objetivo fuera de rango
    "('sin_salida', '07:00', NULL, NULL, NULL, NULL)",             # sin_salida con hora
    "('sin_salida', NULL, 'normal', NULL, NULL, NULL)",
    "('sin_salida', NULL, NULL, NULL, NULL, 80)",
])
def test_campos_de_cada_tipo_de_excepcion(conn: psycopg.Connection, limpio: None, valores: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO bateria_excepcion (tipo, hora_salida, consumo_modo, km, pct, pct_objetivo, fecha)"
                     f" VALUES {valores[:-1]}, '2026-11-20')")  # type: ignore[arg-type]


OBJETOS_BATERIA = ["bateria_parametro_clave", "bateria_parametro", "bateria_lectura", "bateria_horario",
                   "bateria_excepcion", "bateria_sesion", "bateria_trayecto", "bateria_nivel", "bateria_intervalo",
                   "v_bateria_consumo_dia", "v_bateria_calibracion", "v_bateria_estado", "v_bateria_salida",
                   "v_bateria_evento"]


def test_grafana_ro_lee_la_bateria_y_no_recalcula(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    nombres = {f[0] for f in conn.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'v')"
        " AND relname ~ '^v?_?bateria_'")}
    assert nombres == set(OBJETOS_BATERIA)
    e, ahora = ESCENARIOS["prevision"]()
    comparar(conn, e, ahora)
    db.configurar_rol_grafana(conn, "clave-de-test")
    with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password="clave-de-test",
                         host=os.environ["PGHOST"], autocommit=True) as ro:
        for objeto in OBJETOS_BATERIA:
            ro.execute(f"SELECT * FROM {objeto} LIMIT 1")  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("SELECT recalcular_bateria()")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("INSERT INTO bateria_lectura (instante, pct, origen) VALUES (now(), 50, 'cli')")


def test_horario_por_sql(conn: psycopg.Connection, limpio: None) -> None:
    conn.execute("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (1, %s)", (time(9, 0),))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (8, '09:00')")


# --- Excepciones con_salida / sin_salida (0015) ----------------------------------------------------


def test_conversion_de_las_excepciones_de_0014(tmp_path: Path) -> None:
    """Base de datos aparte: migraciones hasta la 0014, excepciones con los tipos antiguos y la 0015."""
    hasta_0014 = tmp_path / "migraciones"
    hasta_0014.mkdir()
    for f in sorted(MIGRACIONES.glob("*.sql")):
        if f.name[:4] <= "0014":
            (hasta_0014 / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    nombre = f"em_test_conv_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{nombre}"')  # type: ignore[arg-type]
    try:
        with psycopg.connect(dbname=nombre, autocommit=True) as c:
            assert db.migrar(c, hasta_0014)[-1].startswith("0014")
            c.execute("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (1, '09:00'), (2, '09:00'),"
                      " (3, '09:00')")
            c.execute(
                "INSERT INTO bateria_excepcion (tipo, fecha, hora, pct_objetivo, consumo_pct, consumo_km, nota,"
                " anulada_en) VALUES"
                " ('sin_coche',      '2026-11-16', NULL,    NULL, NULL, NULL, 'a', NULL),"
                " ('otra_hora',      '2026-11-17', '06:30', NULL, NULL, NULL, 'b', NULL),"
                " ('nivel_objetivo', '2026-11-18', '07:00', 80,   NULL, NULL, 'c', NULL),"
                " ('viaje_largo',    '2026-11-19', '08:00', NULL, NULL, 350,  'd', NULL),"
                " ('viaje_largo',    '2026-11-20', '08:00', NULL, 30,   NULL, 'e', NULL),"
                " ('viaje_largo',    '2026-11-17', '05:00', NULL, 20,   NULL, 'f', now())")
            antes = c.execute("SELECT id, fecha, nota, alta_en, anulada_en FROM bateria_excepcion"
                              " ORDER BY id").fetchall()
            assert db.migrar(c, MIGRACIONES) == [f.name for f in sorted(MIGRACIONES.glob("*.sql"))
                                                 if f.name[:4] > "0014"]
            assert c.execute("SELECT id, fecha, nota, alta_en, anulada_en FROM bateria_excepcion"
                             " ORDER BY id").fetchall() == antes
            assert c.execute("SELECT nota, tipo, hora_salida, consumo_modo, km, pct, pct_objetivo"
                             " FROM bateria_excepcion ORDER BY id").fetchall() == [
                ("a", "sin_salida", None, None, None, None, None),
                ("b", "con_salida", time(6, 30), "normal", None, None, None),
                ("c", "con_salida", time(7), "normal", None, None, D(80)),
                ("d", "con_salida", time(8), "indicado", D(350), None, None),
                ("e", "con_salida", time(8), "indicado", None, D(30), None),
                ("f", "con_salida", time(5), "indicado", None, D(20), None),
            ]
            # Las salidas tras la conversión: lunes sin salida, martes 6:30, miércoles 7:00 con objetivo 80 %...
            desde = datetime(2026, 11, 15, 23, 0, tzinfo=UTC)
            assert c.execute("SELECT fecha, tipo, consumo_modo, pct_objetivo, round(consumo_declarado_kwh, 3)"
                             " FROM bateria_salidas(%s, 4)", (desde,)).fetchall() == [
                (date(2026, 11, 17), "con_salida", "normal", None, None),
                (date(2026, 11, 18), "con_salida", "normal", D(80), None),
                (date(2026, 11, 19), "con_salida", "indicado", None, D("80.500")),  # 350 km × 23/100
                (date(2026, 11, 20), "con_salida", "indicado", None, D("27.390")),  # 30 % de 91,3
            ]
    finally:
        with psycopg.connect(autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')  # type: ignore[arg-type]


def _horario(conn: psycopg.Connection) -> None:
    # Lunes a viernes a las 9; sábado y domingo sin salida.
    conn.execute("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (1, '09:00'), (2, '09:00'), (3, '09:00'),"
                 " (4, '09:00'), (5, '09:00'), (6, NULL)")


AHORA = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
NX = bateria_servicio.NuevaExcepcion


@pytest.mark.parametrize("x", [
    NX("con_salida", date(2026, 11, 16), time(6, 30), "normal"),
    NX("con_salida", date(2026, 11, 16), time(6, 30), "indicado", km=D(200)),
    NX("con_salida", date(2026, 11, 16), time(6, 30), "indicado", pct=D(40), pct_objetivo=D(90)),
    NX("sin_salida", date(2026, 11, 16)),
], ids=["con_salida_normal", "con_salida_km", "con_salida_pct", "sin_salida"])
def test_registrar_excepcion(conn: psycopg.Connection, limpio: None, x: bateria_servicio.NuevaExcepcion) -> None:
    # Lunes 16 a las 3:00 en Madrid; el horario sale a las 9.
    _horario(conn)
    bateria_servicio.registrar_lectura(conn, instante=AHORA - timedelta(hours=1), pct=D(50), origen="cli", ahora=AHORA)
    id_ = bateria_servicio.registrar_excepcion(conn, x, AHORA)
    assert conn.execute("SELECT tipo, fecha, hora_salida, consumo_modo, km, pct, pct_objetivo FROM bateria_excepcion"
                        " WHERE id = %s", (id_,)).fetchone() == (
        x.tipo, x.fecha, x.hora_salida, x.consumo_modo, x.km, x.pct, x.pct_objetivo)
    # El estado ya la tiene en cuenta (recalculado en la misma transacción) y coincide con la referencia.
    e = entradas_desde_bd(conn)
    est = bateria.estado(e, bateria.recalcular(e, AHORA), AHORA)
    fila = conn.execute("SELECT proxima_salida, consumo_plan_kwh, energia_necesaria_kwh FROM bateria_estado(%s)",
                        (AHORA,)).fetchone()
    assert fila is not None
    assert all(cerca(a, b) for a, b in zip(fila, (est.proxima_salida, est.consumo_plan_kwh,
                                                  est.energia_necesaria_kwh)))
    salida_local = fila[0].astimezone(bateria.MADRID)
    assert (salida_local.day, salida_local.time()) == ((17, time(9)) if x.tipo == "sin_salida" else (16, time(6, 30)))


@pytest.mark.parametrize("dia", [date(2026, 11, 21), date(2026, 11, 22)], ids=["sabado_null", "domingo_sin_fila"])
def test_sin_salida_en_dia_sin_salida_se_rechaza(conn: psycopg.Connection, limpio: None, dia: date) -> None:
    _horario(conn)
    with pytest.raises(bateria_servicio.ErrorExcepcion, match="el horario no tiene salida"):
        bateria_servicio.registrar_excepcion(conn, NX("sin_salida", dia), AHORA)
    assert conn.execute("SELECT count(*) FROM bateria_excepcion").fetchone() == (0,)


@pytest.mark.parametrize(("x", "mensaje"), [
    (NX("con_salida", date(2026, 11, 17), time(7), "indicado", km=D(200), pct=D(30)), "uno de los dos"),
    (NX("con_salida", date(2026, 11, 17), time(7), "indicado"), "uno de los dos"),
    (NX("con_salida", date(2026, 11, 17), time(7), "normal", km=D(200)), "no se indican"),
    (NX("con_salida", date(2026, 11, 17), None, "normal"), "falta la hora"),
    (NX("sin_salida", date(2026, 11, 17), time(7)), "solo se indica la fecha"),
    (NX("con_salida", date(2026, 11, 17), time(7), "normal", pct_objetivo=D(0)), "excepción no válida"),
], ids=["km_y_pct", "indicado_vacio", "normal_con_km", "sin_hora", "sin_salida_con_hora", "objetivo_cero"])
def test_excepcion_no_valida(conn: psycopg.Connection, limpio: None, x: bateria_servicio.NuevaExcepcion,
                             mensaje: str) -> None:
    _horario(conn)
    with pytest.raises(bateria_servicio.ErrorExcepcion, match=mensaje):
        bateria_servicio.registrar_excepcion(conn, x, AHORA)
    assert conn.execute("SELECT count(*) FROM bateria_excepcion").fetchone() == (0,)


def test_segunda_excepcion_en_la_misma_fecha(conn: psycopg.Connection, limpio: None) -> None:
    _horario(conn)
    bateria_servicio.registrar_excepcion(conn, NX("sin_salida", date(2026, 11, 17)), AHORA)
    with pytest.raises(bateria_servicio.ErrorExcepcion, match="ya hay una excepción activa"):
        bateria_servicio.registrar_excepcion(conn, NX("con_salida", date(2026, 11, 17), time(7), "normal"), AHORA)


def test_modificar_es_baja_y_alta(conn: psycopg.Connection, limpio: None) -> None:
    _horario(conn)
    viejo = bateria_servicio.registrar_excepcion(conn, NX("sin_salida", date(2026, 11, 17)), AHORA)
    nuevo = bateria_servicio.modificar_excepcion(
        conn, viejo, NX("con_salida", date(2026, 11, 17), time(7), "indicado", km=D(300)), AHORA)
    assert conn.execute("SELECT id, anulada_en IS NULL FROM bateria_excepcion ORDER BY id").fetchall() == [
        (viejo, False), (nuevo, True)]
    assert conn.execute("SELECT tipo FROM bateria_salidas(%s, 1) WHERE fecha = '2026-11-17'",
                        (AHORA,)).fetchall() == [("con_salida",)]


def test_modificar_con_alta_no_valida_deja_la_anterior(conn: psycopg.Connection, limpio: None) -> None:
    _horario(conn)
    viejo = bateria_servicio.registrar_excepcion(conn, NX("con_salida", date(2026, 11, 17), time(7), "normal"), AHORA)
    for nueva in (NX("sin_salida", date(2026, 11, 21)),                                       # validación
                  NX("con_salida", date(2026, 11, 17), time(7), "normal", pct_objetivo=D(0))):  # CHECK
        with pytest.raises(bateria_servicio.ErrorExcepcion):
            bateria_servicio.modificar_excepcion(conn, viejo, nueva, AHORA)
        assert conn.execute("SELECT id, anulada_en FROM bateria_excepcion").fetchall() == [(viejo, None)]


def test_anular_excepcion(conn: psycopg.Connection, limpio: None) -> None:
    _horario(conn)
    id_ = bateria_servicio.registrar_excepcion(conn, NX("sin_salida", date(2026, 11, 17)), AHORA)
    bateria_servicio.anular_excepcion(conn, id_, AHORA)
    with pytest.raises(bateria_servicio.ErrorExcepcion, match="no hay ninguna excepción activa"):
        bateria_servicio.anular_excepcion(conn, id_, AHORA)
