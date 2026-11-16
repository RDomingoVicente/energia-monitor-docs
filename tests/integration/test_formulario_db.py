"""Formulario contra PostgreSQL, con el rol formulario_rw: Inicio, lectura (PRG y 422), 503 y tiempos."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, time as hora, timedelta
from decimal import Decimal as D

import psycopg
import pytest
from escenarios_bateria import ESCENARIOS
from test_bateria_db import cargar, limpiar

from energia_monitor import db
from energia_monitor.formulario.app import App, ConfigApp, Peticion
from energia_monitor.tiempo import MADRID as ZONA

HOST = "formulario.test:8421"
CAB = {"host": HOST, "origin": f"https://{HOST}"}
CLAVE = "clave-formulario-tests"


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    limpiar(conn)
    db.configurar_rol_formulario(conn, CLAVE)
    yield
    limpiar(conn)


def app_rw(nombre_bd: str, ahora: datetime) -> App:
    def conectar() -> psycopg.Connection:
        return psycopg.connect(dbname=nombre_bd, user="formulario_rw", password=CLAVE, host=os.environ["PGHOST"],
                               autocommit=True)
    return App(ConfigApp(HOST, cargador="viaris 192.168.1.34"), conectar, lambda: ahora)


def escenario(conn: psycopg.Connection, nombre: str) -> datetime:
    e, ahora = ESCENARIOS[nombre]()
    for dia in range(1, 6):
        e.horario[dia] = hora(9, 0)
    cargar(conn, e)
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    return ahora


def texto(cuerpo: bytes) -> str:
    html = cuerpo.decode()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<svg.*?</svg>", "", html)))


@pytest.mark.parametrize(("nombre", "etiqueta"), [
    ("confianza_leido", "LEÍDO"), ("confianza_leido_carga", "LEÍDO + CARGA"),
    ("confianza_estimado_tras_trayecto", "ESTIMADO"), ("coche_fuera", "ESTIMADO")])
def test_inicio_por_estado(conn: psycopg.Connection, nombre_bd: str, limpio: None, nombre: str, etiqueta: str) -> None:
    ahora = escenario(conn, nombre)
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/", CAB))
    assert r.estado == 200, texto(r.cuerpo)
    t = texto(r.cuerpo)
    assert f"Nivel {etiqueta} " in t
    assert "Próximos 7 días" in t and "Anotar lectura" in t


def test_inicio_sin_lecturas(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = datetime(2026, 11, 16, 12, tzinfo=UTC)
    t = texto(app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/", CAB)).cuerpo)
    assert "Aún no hay nivel: anota el % que marca el coche." in t
    assert "Cargador no accesible" not in t  # el aviso va al contador, no al texto de Inicio
    assert "aviso" in t  # chip «N avisos»


def test_lectura_prg_y_mensaje_desde_la_bd(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_estimado_tras_trayecto")  # lun 16/11 10:00 en Madrid
    app = app_rw(nombre_bd, ahora)
    r = app.manejar(Peticion("POST", "/lectura", CAB, b"pct=48&fecha=2026-11-16&hora=09:30"))
    assert r.estado == 303
    destino = r.cabeceras["Location"]
    assert re.fullmatch(r"/\?ok=lectura&id=\d+", destino)
    assert conn.execute("SELECT pct, origen, instante FROM bateria_lectura WHERE id = %s",
                        (int(destino.rsplit("=", 1)[1]),)).fetchone() == (
        48, "formulario", datetime(2026, 11, 16, 8, 30, tzinfo=UTC))
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert re.search(r"Lectura guardada: 48 % a las 09:30\. El modelo estimaba \d+ % \(diferencia \+\d+ pts\)", t)
    assert "Nivel LEÍDO 48 %" in t


@pytest.mark.parametrize("consulta", [
    "/?ok=Tu+coche+est%C3%A1+lleno&id=1",
    "/?ok=lectura&id=<script>",
    "/?ok=lectura&id=999999",
    "/?ok=otra&id=1",
    "/?ok=lectura&id=1&mensaje=Lectura+guardada%3A+99+%25",
])
def test_ok_arbitrario_no_se_muestra(conn: psycopg.Connection, nombre_bd: str, limpio: None, consulta: str) -> None:
    ahora = escenario(conn, "confianza_leido")
    conn.execute("UPDATE bateria_lectura SET anulada_en = now(), motivo = 'test'")  # el id 1 ya no está activo
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", consulta, CAB))  # aún sin recalcular: no falla
    assert r.estado == 200
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", consulta, CAB))
    t = texto(r.cuerpo)
    assert r.estado == 200
    assert "Lectura guardada" not in t and "lleno" not in t and "<script>" not in r.cuerpo.decode()


@pytest.mark.parametrize(("cuerpo", "campo", "mensaje"), [
    (b"pct=101&fecha=2026-11-16&hora=09:30", "pct", "Indica un porcentaje entero de 0 a 100."),
    (b"pct=50.5&fecha=2026-11-16&hora=09:30", "pct", "Indica un porcentaje entero de 0 a 100."),
    (b"pct=&fecha=2026-11-16&hora=09:30", "pct", "Indica un porcentaje entero de 0 a 100."),
    (b"pct=50&fecha=2026-03-29&hora=02:30", "hora", "no existe en Madrid"),
    (b"pct=50&fecha=2026-11-16&hora=", "hora", "Indica la fecha y la hora de la lectura."),
    (b"pct=50&fecha=2026-11-16&hora=11:00", "general", "La lectura no puede ser futura"),
])
def test_lectura_no_valida_422(conn: psycopg.Connection, nombre_bd: str, limpio: None, cuerpo: bytes, campo: str,
                               mensaje: str) -> None:
    ahora = escenario(conn, "confianza_leido")  # lun 16/11 05:00 UTC
    antes = conn.execute("SELECT count(*) FROM bateria_lectura").fetchone()
    r = app_rw(nombre_bd, ahora + timedelta(hours=5)).manejar(Peticion("POST", "/lectura", CAB, cuerpo))
    assert r.estado == 422
    html = r.cuerpo.decode()
    assert mensaje in html
    if campo != "general":
        assert f'aria-describedby="error-{campo}"' in html
    # Formulario relleno con lo enviado.
    fecha = re.search(rb"fecha=([\d-]+)", cuerpo).group(1).decode()  # type: ignore[union-attr]
    assert f'value="{fecha}"' in html
    assert conn.execute("SELECT count(*) FROM bateria_lectura").fetchone() == antes


def test_lectura_en_hora_ambigua_toma_la_primera(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = datetime(2026, 10, 26, 12, tzinfo=UTC)
    r = app_rw(nombre_bd, ahora).manejar(Peticion("POST", "/lectura", CAB, b"pct=50&fecha=2026-10-25&hora=02:30"))
    assert r.estado == 303
    assert conn.execute("SELECT instante FROM bateria_lectura").fetchone() == (
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),)  # 02:30 aún en horario de verano (UTC+2)


def test_migracion_pendiente_503(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, datetime.now(UTC))
    app.migracion_esperada = "9999"
    r = app.manejar(Peticion("GET", "/", CAB))
    assert r.estado == 503 and "Migración pendiente" in r.cuerpo.decode()
    assert app.manejar(Peticion("GET", "/salud", {})).estado == 503


def test_salud_como_formulario_rw(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    r = app_rw(nombre_bd, datetime.now(UTC)).manejar(Peticion("GET", "/salud", {}))
    assert r.estado == 200 and b'"estado": "ok"' in r.cuerpo


def test_paginas_por_debajo_de_300_ms(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_secuencia")
    app = app_rw(nombre_bd, ahora)
    app.manejar(Peticion("GET", "/", CAB))  # primera: planes en caché
    inicio = time.perf_counter()
    for _ in range(5):
        assert app.manejar(Peticion("GET", "/", CAB)).estado == 200
    assert (time.perf_counter() - inicio) / 5 < 0.3


# --- Excepciones (7.2) ------------------------------------------------------------------------------

# lun 16/11/2026 12:00 en Madrid; horario: lunes a viernes a las 9.
MEDIODIA = datetime(2026, 11, 16, 11, tzinfo=UTC)


def con_horario(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (1, '09:00'), (2, '09:00'), (3, '09:00'),"
                 " (4, '09:00'), (5, '09:00'), (6, NULL)")


def post(app: App, ruta: str, cuerpo: str) -> tuple[int, str, str]:
    r = app.manejar(Peticion("POST", ruta, CAB, cuerpo.encode()))
    return r.estado, r.cabeceras.get("Location", ""), r.cuerpo.decode()


def test_alta_de_excepciones_y_mensaje(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    con_horario(conn)
    app = app_rw(nombre_bd, MEDIODIA)
    estado, destino, _ = post(app, "/excepciones", "tipo=con_salida&fecha=2026-11-22&hora=07:00&consumo=indicado"
                                                   "&km=180&pct=&pct_objetivo=70&nota=Viaje+a+Zaragoza")
    assert estado == 303 and re.fullmatch(r"/excepciones\?ok=excepcion&id=\d+", destino)
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert "Excepción guardada: dom 22/11, con salida a las 07:00." in t
    # 180 km × 23 kWh/100 km = 41,4 kWh; objetivo max(25 % + 45 %, 70 %) = 70 %.
    # En escritorio la nota va en una fila propia bajo la de la excepción (formulario-maquetas.md, 7.2).
    assert "dom 22/11 con salida 07:00 41,4 kWh indicado · 180 km 70 % Modificar Dar de baja Viaje a Zaragoza" in t
    # Consumo normal: km y % se ignoran aunque lleguen (sin JavaScript se envían igual).
    estado, _, _ = post(app, "/excepciones", "tipo=con_salida&fecha=2026-11-28&hora=06:30&consumo=normal&km=50&pct=9"
                                             "&pct_objetivo=80")
    assert estado == 303
    assert conn.execute("SELECT consumo_modo, km, pct, pct_objetivo FROM bateria_excepcion WHERE fecha = '2026-11-28'"
                        ).fetchone() == ("normal", None, None, 80)
    t = texto(app.manejar(Peticion("GET", "/excepciones", CAB)).cuerpo)
    assert "sáb 28/11 con salida 06:30 25,0 kWh normal · P75 80 % mínimo" in t
    # Sin salida en un día con salida.
    assert post(app, "/excepciones", "tipo=sin_salida&fecha=2026-11-19&nota=Teletrabajo")[0] == 303
    t = texto(app.manejar(Peticion("GET", "/excepciones", CAB)).cuerpo)
    assert "jue 19/11 sin salida — 0,0 kWh — Modificar Dar de baja Teletrabajo" in t and "3 activas · 1 por fecha" in t


@pytest.mark.parametrize(("cuerpo", "campo", "mensaje"), [
    ("tipo=sin_salida&fecha=2026-11-21", "fecha", "El sábado 21/11 ya no tiene salida según tu horario."),
    ("tipo=sin_salida&fecha=2026-11-22", "fecha", "El domingo 22/11 ya no tiene salida según tu horario."),
    ("tipo=con_salida&fecha=2026-11-22&hora=07:00&consumo=indicado&km=100&pct=20", "consumo",
     "Con «Indicar consumo», indica km o %, uno de los dos."),
    ("tipo=con_salida&fecha=2026-11-22&hora=07:00&consumo=indicado&km=&pct=", "consumo",
     "Con «Indicar consumo», indica km o %, uno de los dos."),
    ("tipo=con_salida&fecha=2026-11-22&hora=&consumo=normal", "hora", "Indica la hora de salida."),
    ("tipo=con_salida&fecha=&hora=07:00&consumo=normal", "fecha", "Indica la fecha."),
    ("tipo=con_salida&fecha=2026-11-22&hora=07:00&consumo=normal&pct_objetivo=120", "pct_objetivo",
     "Indica un nivel entero de 1 a 100"),
    ("tipo=con_salida&fecha=2026-11-23&hora=07:00&consumo=normal", "fecha", "Ya hay una excepción activa el 23/11/2026"),
])
def test_excepcion_no_valida_422(conn: psycopg.Connection, nombre_bd: str, limpio: None, cuerpo: str, campo: str,
                                 mensaje: str) -> None:
    con_horario(conn)
    conn.execute("INSERT INTO bateria_excepcion (tipo, fecha) VALUES ('sin_salida', '2026-11-23')")
    estado, _, html = post(app_rw(nombre_bd, MEDIODIA), "/excepciones", cuerpo)
    assert estado == 422
    assert mensaje in html and f'aria-describedby="error-x-{campo}"' in html
    assert conn.execute("SELECT count(*) FROM bateria_excepcion").fetchone() == (1,)
    # El formulario vuelve relleno.
    fecha = re.search(r"fecha=([\d-]*)", cuerpo).group(1)  # type: ignore[union-attr]
    assert f'name="fecha" class="inp" type="date" required value="{fecha}"' in html


def test_modificar_y_dar_de_baja(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    con_horario(conn)
    app = app_rw(nombre_bd, MEDIODIA)
    post(app, "/excepciones", "tipo=con_salida&fecha=2026-11-22&hora=07:00&consumo=indicado&km=180&nota=Zaragoza")
    viejo = conn.execute("SELECT id FROM bateria_excepcion").fetchone()[0]  # type: ignore[index]
    html = app.manejar(Peticion("GET", f"/excepciones?modificar={viejo}", CAB)).cuerpo.decode()
    assert "Modificar excepción" in html and f'action="/excepciones/{viejo}/modificar"' in html
    assert 'value="180"' in html and 'value="Zaragoza"' in html and 'value="07:00"' in html
    estado, destino, _ = post(app, f"/excepciones/{viejo}/modificar",
                              "tipo=con_salida&fecha=2026-11-22&hora=06:00&consumo=indicado&pct=30&nota=Zaragoza")
    assert estado == 303
    assert conn.execute("SELECT id, anulada_en IS NULL, hora_salida FROM bateria_excepcion ORDER BY id").fetchall() == [
        (viejo, False, hora(7)), (viejo + 1, True, hora(6))]
    # Una modificación no válida deja la excepción como estaba.
    assert post(app, f"/excepciones/{viejo + 1}/modificar", "tipo=con_salida&fecha=2026-11-22&hora=06:00"
                                                           "&consumo=indicado")[0] == 422
    assert conn.execute("SELECT count(*) FROM bateria_excepcion WHERE anulada_en IS NULL").fetchone() == (1,)
    estado, destino, _ = post(app, f"/excepciones/{viejo + 1}/baja", "")
    assert estado == 303 and destino == f"/excepciones?ok=baja&id={viejo + 1}"
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert "Excepción del dom 22/11 dada de baja." in t and "No hay excepciones próximas" in t
    assert post(app, f"/excepciones/{viejo + 1}/baja", "")[0] == 422  # ya dada de baja


def test_objetivo_por_encima_del_tope_avisa(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    con_horario(conn)
    app = app_rw(nombre_bd, MEDIODIA)
    post(app, "/excepciones", "tipo=con_salida&fecha=2026-11-22&hora=07:00&consumo=indicado&km=500")
    t = texto(app.manejar(Peticion("GET", "/excepciones", CAB)).cuerpo)
    assert "Objetivo por encima del tope: el del dom 22/11 se recorta al tope (100 %)." in t
    assert "115,0 kWh indicado · 500 km 100 %" in t


def test_excepciones_pasadas(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    hoy = datetime.now(UTC).astimezone(ZONA).date()
    conn.execute("INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo, km) VALUES"
                 " ('con_salida', %s, '08:00', 'indicado', 100)", (hoy - timedelta(days=3),))
    conn.execute("INSERT INTO bateria_excepcion (tipo, fecha, anulada_en) VALUES ('sin_salida', %s, now())",
                 (hoy - timedelta(days=2),))
    t = texto(app_rw(nombre_bd, datetime.now(UTC)).manejar(Peticion("GET", "/excepciones", CAB)).cuerpo)
    assert "Pasadas · 30 días" in t
    assert re.search(r"con salida 23,0 kWh 0,0 kWh −100 % no cumplida", t)
    assert re.search(r"sin salida — — — dada de baja", t)


def test_ok_de_excepciones_arbitrario_no_se_muestra(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, MEDIODIA)
    for consulta in ("/excepciones?ok=Todo+listo&id=1", "/excepciones?ok=excepcion&id=7", "/excepciones?ok=baja&id=x"):
        t = texto(app.manejar(Peticion("GET", consulta, CAB)).cuerpo)
        assert "Excepción guardada" not in t and "Todo listo" not in t and "Excepción del" not in t


# --- Historial (7.4) --------------------------------------------------------------------------------


def test_historial_indicadores_y_tabla(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_secuencia")  # 40 % y, tras carga y trayecto, 42 % (estimado 31,46 %)
    t = texto(app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/historial", CAB)).cuerpo)
    assert "Error medio · 30 d +10,5 pts media con signo: el coche suele tener más de lo estimado" in t
    assert "Error absoluto medio 10,5 pts ≈ 9,6 kWh" in t
    assert "Lecturas válidas 1 1 comparable · 0 de carga completa" in t
    assert "Última lectura hace 1 h umbral de aviso: 7 días" in t
    assert re.search(r"lun 16/11 10:00 42 % 31 % \+11 pts usuario válida", t)
    assert re.search(r"lun 16/11 02:00 40 % — — usuario válida", t)


def test_corregir_y_anular_lectura(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_secuencia")
    app = app_rw(nombre_bd, ahora)
    ultima = conn.execute("SELECT id FROM bateria_lectura ORDER BY instante DESC LIMIT 1").fetchone()[0]  # type: ignore[index]
    html = app.manejar(Peticion("GET", f"/historial?corregir={ultima}", CAB)).cuerpo.decode()
    assert "Corregir lectura" in html and f'action="/lecturas/{ultima}/corregir"' in html
    assert 'value="42"' in html and 'value="2026-11-16"' in html and 'value="10:00"' in html
    # Corrección que no valida: la lectura sigue activa.
    estado, _, html = post(app, f"/lecturas/{ultima}/corregir", "pct=142&fecha=2026-11-16&hora=10:00")
    assert estado == 422 and "Indica un porcentaje entero de 0 a 100." in html
    assert conn.execute("SELECT anulada_en FROM bateria_lectura WHERE id = %s", (ultima,)).fetchone() == (None,)
    # Corrección a la misma hora: nueva lectura que corrige a la anterior.
    estado, destino, _ = post(app, f"/lecturas/{ultima}/corregir", "pct=44&fecha=2026-11-16&hora=10:00&nota=app")
    assert estado == 303 and re.fullmatch(r"/historial\?ok=corregida&id=\d+", destino)
    nuevo = int(destino.rsplit("=", 1)[1])
    assert conn.execute("SELECT pct, corrige, nota FROM bateria_lectura WHERE id = %s", (nuevo,)).fetchone() == (
        44, ultima, "app")
    assert conn.execute("SELECT motivo FROM bateria_lectura WHERE id = %s", (ultima,)).fetchone() == ("corregida",)
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert "Lectura corregida: 44 % el lun 16/11 10:00; la anterior queda anulada." in t
    assert "app · corrige a 10:00" in t
    # Anular pide motivo.
    assert post(app, f"/lecturas/{nuevo}/anular", "motivo=")[0] == 422
    estado, destino, _ = post(app, f"/lecturas/{nuevo}/anular", "motivo=Tecleado+mal")
    assert estado == 303 and destino == f"/historial?ok=anulada&id={nuevo}"
    t = texto(app.manejar(Peticion("GET", destino + "&anuladas=1", CAB)).cuerpo)
    assert "Lectura del lun 16/11 10:00 anulada." in t and "Tecleado mal" in t
    assert post(app, f"/lecturas/{nuevo}/anular", "motivo=otra")[0] == 404


def test_historial_con_carga_externa_y_filtros(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "externa_con_kwh")
    conn.execute("UPDATE bateria_carga_externa SET coste_eur = 14.6, nota = 'Ionity'")
    app = app_rw(nombre_bd, ahora)
    t = texto(app.manejar(Peticion("GET", "/historial", CAB)).cuerpo)
    assert re.search(r"lun 16/11 06:00 — — \+28 pts carga externa 30,0 kWh Ionity · 14,60 €", t)
    assert "/carga-externa?corregir=1" in app.manejar(Peticion("GET", "/historial", CAB)).cuerpo.decode()
    # 90 días y todo enlazan al mismo listado.
    for periodo in ("90", "todo"):
        assert app.manejar(Peticion("GET", f"/historial?periodo={periodo}", CAB)).estado == 200


# --- Carga externa (7.8) ----------------------------------------------------------------------------


def con_trayecto(conn: psycopg.Connection) -> datetime:
    """Lectura 20 % y el coche fuera de las 03:00 a las 11:00 (Madrid) del lun 16/11; ahora, las 13:00."""
    e, _ = ESCENARIOS["residuo_sin_carga_externa"]()
    e.lecturas = e.lecturas[:1]
    cargar(conn, e)
    ahora = datetime(2026, 11, 16, 12, tzinfo=UTC)
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    return ahora


def test_registrar_carga_externa_por_la_pagina(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, con_trayecto(conn))
    estado, destino, _ = post(app, "/carga-externa", "fecha=2026-11-16&hora=06:00&kwh=28.4&coste_eur=12.75"
                                                     "&nota=Zaragoza")
    assert estado == 303 and re.fullmatch(r"/carga-externa\?ok=externa&id=\d+", destino)
    assert conn.execute("SELECT kwh, coste_eur, nota, origen, instante FROM bateria_carga_externa").fetchone() == (
        D("28.4"), D("12.75"), "Zaragoza", "formulario", datetime(2026, 11, 16, 5, tzinfo=UTC))
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert "Carga externa guardada: 28,4 kWh el lun 16/11 06:00." in t
    assert "90 días · 1 carga · 12,75 €" in t
    assert re.search(r"lun 16/11 06:00 28,4 kWh nivel \+27 pts 12,75 € 0,449 €/kWh Zaragoza", t)
    # Sin kWh también vale.
    assert post(app, "/carga-externa", "fecha=2026-11-16&hora=08:00&kwh=&coste_eur=9.80")[0] == 303
    assert "Sin kWh: intervalo fuera de la estadística" in texto(
        app.manejar(Peticion("GET", "/carga-externa", CAB)).cuerpo)


@pytest.mark.parametrize(("cuerpo", "campo", "mensaje"), [
    ("fecha=2026-11-16&hora=02:00&kwh=20", "hora", "A esa hora el coche estaba conectado en casa."),
    ("fecha=2026-11-16&hora=12:00&kwh=20", "hora", "A esa hora el coche estaba conectado en casa."),
    ("fecha=2026-11-16&hora=06:00&kwh=151", "kwh", "Indica la energía de 0 a 150 kWh"),
    ("fecha=2026-11-16&hora=06:00&kwh=20.55", "kwh", "con un decimal como máximo"),
    ("fecha=2026-11-16&hora=06:00&kwh=20&coste_eur=-1", "coste_eur", "Indica el coste en euros"),
    ("fecha=2026-11-16&hora=15:00&kwh=20", "hora", "La carga externa no puede ser futura"),
])
def test_carga_externa_no_valida_422(conn: psycopg.Connection, nombre_bd: str, limpio: None, cuerpo: str, campo: str,
                                     mensaje: str) -> None:
    estado, _, html = post(app_rw(nombre_bd, con_trayecto(conn)), "/carga-externa", cuerpo)
    assert estado == 422 and mensaje in html and f'aria-describedby="error-e-{campo}"' in html
    assert conn.execute("SELECT count(*) FROM bateria_carga_externa").fetchone() == (0,)


def test_corregir_y_anular_carga_externa(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, con_trayecto(conn))
    post(app, "/carga-externa", "fecha=2026-11-16&hora=06:00&kwh=28.4&coste_eur=12.75")
    viejo = conn.execute("SELECT id FROM bateria_carga_externa").fetchone()[0]  # type: ignore[index]
    html = app.manejar(Peticion("GET", f"/carga-externa?corregir={viejo}", CAB)).cuerpo.decode()
    assert "Corregir carga externa" in html and 'value="28.4"' in html and 'value="12.75"' in html
    assert "28,4 kWh × eficiencia 0,86 =" in texto(html.encode())
    estado, destino, _ = post(app, f"/carga-externa/{viejo}/corregir", "fecha=2026-11-16&hora=06:30&kwh=30")
    assert estado == 303 and "ok=externa_corregida" in destino
    assert conn.execute("SELECT id, motivo, corrige FROM bateria_carga_externa ORDER BY id").fetchall() == [
        (viejo, "corregida", None), (viejo + 1, None, viejo)]
    assert post(app, f"/carga-externa/{viejo + 1}/anular", "motivo=")[0] == 422
    estado, destino, _ = post(app, f"/carga-externa/{viejo + 1}/anular", "motivo=No+cargué")
    assert estado == 303
    assert "Carga externa del lun 16/11 06:30 anulada." in texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert post(app, f"/carga-externa/{viejo + 1}/anular", "motivo=otra")[0] == 404


# --- Horario (7.3) ----------------------------------------------------------------------------------


def test_horario_guardar_y_semana_tipo(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, MEDIODIA)
    t = texto(app.manejar(Peticion("GET", "/horario", CAB)).cuerpo)
    assert "Aún sin guardar." in t and "0 salidas" in t
    cuerpo = "&".join([f"sale_{d}=1&hora_{d}=09:30" for d in range(1, 5)] + ["sale_5=1&hora_5=07:00", "hora_6=10:00"])
    estado, destino, _ = post(app, "/horario", cuerpo)
    assert estado == 303 and destino == "/horario?ok=horario"
    assert conn.execute("SELECT dia_iso, hora_salida FROM bateria_horario ORDER BY dia_iso").fetchall() == [
        (1, hora(9, 30)), (2, hora(9, 30)), (3, hora(9, 30)), (4, hora(9, 30)), (5, hora(7)), (6, None), (7, None)]
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert "Horario guardado: L–J 09:30 · V 07:00." in t
    # Sin historia: consumo inicial de 25 kWh por día de salida; objetivo 25 % + 27 % = 52 %.
    assert "Lunes 25,0 kWh 52 %" in t and "Sábado 0,0 kWh —" in t
    assert "5 salidas 125,0 kWh/semana previstos · 137 % de batería" in t
    assert "Última modificación:" in t


def test_horario_sin_hora_422(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    estado, _, html = post(app_rw(nombre_bd, MEDIODIA), "/horario", "sale_1=1&hora_1=&sale_2=1&hora_2=25:00")
    assert estado == 422
    assert 'aria-describedby="error-h-1"' in html and 'aria-describedby="error-h-2"' in html
    assert "el horario no se ha guardado" in html
    assert conn.execute("SELECT count(*) FROM bateria_horario").fetchone() == (0,)


def test_resumen_de_semana() -> None:
    from energia_monitor.formulario.pantallas.horario import resumen_semana

    assert resumen_semana({1: hora(9), 2: hora(9), 3: hora(9), 5: hora(7)}) == "L–X 09:00 · V 07:00"
    assert resumen_semana({1: hora(9), 2: hora(9), 6: hora(10)}) == "L M 09:00 · S 10:00"
    assert resumen_semana({}) == "sin salidas"


# --- Parámetros (7.5) -------------------------------------------------------------------------------


def test_parametros_vigentes_en_unidades_de_pantalla(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    t = texto(app_rw(nombre_bd, MEDIODIA).manejar(Peticion("GET", "/parametros", CAB)).cuerpo)
    # Los tests recargan los parámetros con origen «usuario» (test_bateria_db.cargar_parametros).
    for fila in ("Capacidad útil capacidad_util_kwh 91,3 kWh 50–120 usuario 01/01/2025",
                 "Eficiencia de carga factor_bateria 0,86 0,70–1,00 usuario",
                 "Percentil de planificación percentil_planificacion 0,75 (P75) 0,50–0,95",
                 "Desconexión sin consumo desconexion_min_s 30 min 0–4 h",
                 "Aviso hueco de muestreo hueco_muestreo_aviso_s 30 min 5 min–4 h",
                 "Aviso sin lecturas aviso_sin_lectura_dias 7 días 1–14",
                 "Detectar carga completa detectar_carga_completa sí sí / no",
                 "Observaciones para calibrar observaciones_calibracion 5 1–20"):
        assert fila in t, fila


def test_registrar_parametro_desde_la_pagina(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, MEDIODIA)
    html = app.manejar(Peticion("GET", "/parametros?clave=desconexion_min_s", CAB)).cuerpo.decode()
    assert '<option value="desconexion_min_s" selected>' in html and "Valor nuevo (min)" in html
    # En minutos: 20 min = 1200 s.
    estado, destino, _ = post(app, "/parametros", "clave=desconexion_min_s&valor=20&desde=2026-11-20&nota=prueba")
    assert estado == 303 and destino.startswith("/parametros?ok=parametro&id=")
    assert conn.execute("SELECT valor, origen, fuente, nota FROM bateria_parametro WHERE clave = 'desconexion_min_s'"
                        " AND lower(vigencia) = '2026-11-20'").fetchone() == (1200, "usuario", "formulario", "prueba")
    t = texto(app.manejar(Peticion("GET", destino, CAB)).cuerpo)
    assert "desconexion_min_s = 20 min desde el 20/11/2026. La estimación se ha recalculado." in t
    assert "20/11/2026 desconexion_min_s 30 min → 20 min prueba" in t
    limpiar_parametros(conn)


@pytest.mark.parametrize(("cuerpo", "campo", "mensaje"), [
    ("clave=reserva_pct&valor=5&desde=2026-11-20", "valor", "Reserva_pct fuera de rango: 5 (admitido de 10 a 60)."),
    ("clave=reserva_pct&valor=treinta&desde=2026-11-20", "valor", "Indica un número (%)."),
    ("clave=reserva_pct&valor=30&desde=2026-11-15", "desde", "Desde la página, el valor nuevo vale desde hoy o después."),
    ("clave=aviso_sin_lectura_dias&valor=1,5&desde=2026-11-20", "valor", "Valor no válido para aviso_sin_lectura_dias"),
    ("clave=no_existe&valor=1&desde=2026-11-20", "clave", "Elige un parámetro de la lista."),
])
def test_parametro_no_valido_422(conn: psycopg.Connection, nombre_bd: str, limpio: None, cuerpo: str, campo: str,
                                 mensaje: str) -> None:
    antes = conn.execute("SELECT count(*) FROM bateria_parametro").fetchone()
    estado, _, html = post(app_rw(nombre_bd, MEDIODIA), "/parametros", cuerpo)
    assert estado == 422 and mensaje in html and f'aria-describedby="error-p-{campo}"' in html
    assert conn.execute("SELECT count(*) FROM bateria_parametro").fetchone() == antes


def test_calibracion_sugerida_se_registra(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    e, ahora = ESCENARIOS["calibracion"]()
    # Factor observado 0,9 (como en test_aviso_de_factor_sugerido).
    e.lecturas[-1] = e.lecturas[-1].__class__(e.lecturas[-1].id, e.lecturas[-1].instante,
                                              (D(20) / 100 * D("91.3") + 27) / D("91.3") * 100)
    cargar(conn, e)
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    app = app_rw(nombre_bd, ahora)
    t = texto(app.manejar(Peticion("GET", "/parametros", CAB)).cuerpo)
    assert "observado 0,90 · vigente 0,86 n = 1 carga · rango 0,90–0,90" in t
    assert "Registrar 0,90 como valor nuevo" in t
    estado, _, _ = post(app, "/parametros", "clave=factor_bateria&valor=0.90&desde=2026-11-16"
                                            "&nota=calibración+sugerida+(1+observación)")
    assert estado == 303
    assert conn.execute("SELECT valor, origen FROM bateria_parametro WHERE clave = 'factor_bateria'"
                        " AND lower(vigencia) = '2026-11-16'").fetchone() == (D("0.90"), "estimado")
    limpiar_parametros(conn)


def limpiar_parametros(conn: psycopg.Connection) -> None:
    from test_bateria_db import cargar_parametros

    from energia_monitor import bateria

    cargar_parametros(conn, bateria.Parametros())


# --- Avisos (7.7) -----------------------------------------------------------------------------------


def test_avisos_activos_y_resueltos(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    e, ahora = ESCENARIOS["residuo_sin_carga_externa"]()
    cargar(conn, e)
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    conn.execute("SELECT actualizar_avisos(%s)", (ahora,))
    # Un aviso ya resuelto hace 2 días y otro de hace 10 (fuera de los 7 días).
    conn.execute("INSERT INTO aviso_historial (clave, nivel, titulo, detalle, inicio, fin) VALUES"
                 " ('hueco_muestreo', 'aviso', 'Hueco de muestreo', '42 min sin muestras', %s, %s),"
                 " ('viejo', 'aviso', 'Viejo', NULL, %s, %s)",
                 (ahora - timedelta(days=2, minutes=42), ahora - timedelta(days=2),
                  ahora - timedelta(days=10, hours=1), ahora - timedelta(days=10)))
    app = app_rw(nombre_bd, ahora)
    r = app.manejar(Peticion("GET", "/avisos", CAB))
    t = texto(r.cuerpo)
    assert r.estado == 200
    assert re.search(r"Activos · \d+", t)
    # Orden: error primero (sin muestras del cargador), después los avisos.
    assert t.index("ERROR Cargador no accesible") < t.index("AVISO ¿Cargaste fuera de casa?")
    assert "Registrar carga externa →" in t and "Revisar en Historial →" in t
    assert "hueco de muestreo 42 min sin muestras resuelto" in t and "viejo" not in t.split("Resueltos")[1]
    assert "Carga completa dudosa" in t  # catálogo


def test_contador_de_avisos_en_la_cabecera(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    e, ahora = ESCENARIOS["residuo_sin_carga_externa"]()
    cargar(conn, e)
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))
    n = conn.execute("SELECT count(*) FROM bateria_avisos(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    html = app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/avisos", CAB)).cuerpo.decode()
    assert f'<span class="contador error" aria-label="{n} activos">{n}</span>' in html  # menú (el más grave: error)
    assert f'class="chip error">' in html and f"{n} avisos</a>" in html
    assert f'aria-label="Avisos: {n} activos"' in html  # campana del móvil


# --- Ayuda (7.6) ------------------------------------------------------------------------------------


def test_ayuda_con_valores_vigentes(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = escenario(conn, "confianza_leido")  # nivel 50 %, sin historia: consumo inicial 25 kWh
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/ayuda", CAB))
    t = texto(r.cuerpo)
    assert r.estado == 200
    assert "1 pt = capacidad útil 91,3 kWh / 100 = 0,913 kWh" in t
    assert "objetivo de salida = reserva 25 % + 27 pts = 52 %" in t
    assert "nivel actual = 50 %" in t and "energía necesaria = (52 − 50) pts × 0,913" in t
    # Textos de los parámetros desde bateria_parametro_clave, y el glosario con los valores vigentes.
    assert "Eficiencia de carga factor_bateria 0,86 0,70–1,00 Fracción de la energía medida" in t
    assert "Periodo desconectado de al menos desconexion_min_s (30 min)" in t
    html = r.cuerpo.decode()
    assert html.count("<details") == 5 and html.count("<details class=\"pn\" open>") == 2  # móvil


@pytest.mark.parametrize(("q", "esta", "no_esta"), [
    ("P75", "Glosario", "Estados del nivel"),
    ("eficiencia", "factor_bateria", "Horario Primera salida"),
    ("ESTIMADO", "ESTIMADO Ha habido trayectos", "capacidad_util_kwh"),
    ("tramo inexistente", "Nada en la ayuda contiene «tramo inexistente».", "Glosario"),
    ("<script>", "Nada en la ayuda contiene «&lt;script&gt;».", "<script>"),
])
def test_busqueda_en_la_ayuda(conn: psycopg.Connection, nombre_bd: str, limpio: None, q: str, esta: str,
                              no_esta: str) -> None:
    from urllib.parse import quote

    html = app_rw(nombre_bd, MEDIODIA).manejar(Peticion("GET", f"/ayuda?q={quote(q)}", CAB)).cuerpo.decode()
    assert esta in (html if "<" in esta else texto(html.encode()))
    assert no_esta not in (html if no_esta == "<script>" else texto(html.encode()).split("Ayuda Qué calcula")[1])


def test_todas_las_pantallas_responden(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    app = app_rw(nombre_bd, MEDIODIA)
    for ruta in ("/", "/excepciones", "/horario", "/historial", "/parametros", "/ayuda", "/avisos", "/carga-externa"):
        r = app.manejar(Peticion("GET", ruta, CAB))
        assert r.estado == 200, ruta
        html = r.cuerpo.decode()
        assert "llega en la v0.5.5" not in html
        assert "style=" not in html and not re.search(r"https?://", html), ruta


# --- Estado del coche: conectado, cargando y sin cargar (v0.5.7) --------------------------------------


def test_conectado_cargando_y_sin_cargar_desde(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    """Los cambios de estado del NAS del 15-16/11 (UTC): salida 07:59, conexión 17:00:14, carga de 01:00:58 a
    05:00:58 con una pausa (5) de un minuto. «Desde» sale de cargador_estado, no de localtime."""
    estados = [(datetime(2026, 11, 14, 20, 0, tzinfo=UTC), 4), (datetime(2026, 11, 15, 7, 59, 8, tzinfo=UTC), 1),
               (datetime(2026, 11, 15, 17, 0, 14, tzinfo=UTC), 4), (datetime(2026, 11, 16, 1, 0, 58, tzinfo=UTC), 6),
               (datetime(2026, 11, 16, 4, 6, 58, tzinfo=UTC), 5), (datetime(2026, 11, 16, 4, 7, 58, tzinfo=UTC), 6),
               (datetime(2026, 11, 16, 5, 0, 58, tzinfo=UTC), 4)]
    conn.cursor().executemany("INSERT INTO cargador_estado (conector, detectado_en, estado)"
                              " VALUES ('mennekes', %s, %s)", estados)

    def coche(ahora: datetime) -> str:
        return texto(app_rw(nombre_bd, ahora).manejar(Peticion("GET", "/", CAB)).cuerpo)

    # Recién conectado, sin carga en esta conexión.
    assert "Conectado desde 18:00 · evsm 4 · sin carga" in coche(datetime(2026, 11, 15, 19, tzinfo=UTC))
    # Cargando: desde el inicio de la carga; la pausa 6→5→6 no la corta y no sale «conectado desde».
    t = coche(datetime(2026, 11, 16, 4, 30, tzinfo=UTC))
    assert "Cargando desde 02:00 · evsm 6" in t and "ayer 18:00" not in t
    # Tras la carga: desde cuándo está conectado y, aparte, desde cuándo no carga.
    assert "Conectado desde ayer 18:00 · sin cargar desde 06:00 · evsm 4" in coche(
        datetime(2026, 11, 16, 9, tzinfo=UTC))
    # Una desconexión y otra conexión empiezan de cero.
    conn.cursor().executemany("INSERT INTO cargador_estado (conector, detectado_en, estado)"
                              " VALUES ('mennekes', %s, %s)",
                              [(datetime(2026, 11, 16, 9, 30, tzinfo=UTC), 1),
                               (datetime(2026, 11, 16, 12, tzinfo=UTC), 4)])
    assert "Conectado desde 13:00 · evsm 4 · sin carga" in coche(datetime(2026, 11, 16, 13, tzinfo=UTC))


def test_inicio_trayecto_partido_por_la_primera_lectura(conn: psycopg.Connection, nombre_bd: str,
                                                        limpio: None) -> None:
    from escenarios_bateria import Constructor, con_intervalos, t

    c = Constructor().estado(t(-10), 4).trayecto(t(8), t(17)).lectura(t(15.5), 87).lectura(t(17.25), 75)
    cargar(conn, con_intervalos(c.e))
    conn.execute("SELECT recalcular_bateria(%s)", (t(18),))
    total = conn.execute("SELECT consumo_kwh FROM bateria_trayecto").fetchone()[0]  # type: ignore[index]
    tx = texto(app_rw(nombre_bd, t(18)).manejar(Peticion("GET", "/", CAB)).cuerpo)
    assert f"trayecto est. −{str(round(total, 1)).replace('.', ',')} kWh (11,0 reales)" in tx
