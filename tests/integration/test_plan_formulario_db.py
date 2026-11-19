"""Paso 3b contra PostgreSQL: pantalla Plan de carga (7.9), Próximas cargas de Inicio (7.1), Parámetros por grupos
(7.5), Ayuda (7.6) y navegación, con el rol formulario_rw. Criterios de la sección 9 de formulario-maquetas.md."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest
from test_formulario_db import CAB, CLAVE, app_rw, texto
from test_plan_servicio_db import AHORA, escenario, local
from test_plan_servicio_db import limpiar as limpiar_plan

from energia_monitor import db
from energia_monitor.formulario.app import Peticion
from energia_monitor.plan_carga import servicio as ps
from energia_monitor.plan_carga.franjas import num_tareas
from energia_monitor.plan_carga.modelo import CUARTO


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    limpiar_plan(conn)
    db.configurar_rol_formulario(conn, CLAVE)
    yield
    limpiar_plan(conn)


def get(nombre_bd: str, ahora, ruta: str) -> str:  # type: ignore[no-untyped-def]
    r = app_rw(nombre_bd, ahora).manejar(Peticion("GET", ruta, CAB))
    assert r.estado == 200, r.cuerpo[:500]
    return r.cuerpo.decode()


def guardar_plan(conn: psycopg.Connection, ahora) -> ps.Plan:  # type: ignore[name-defined, no-untyped-def]
    entrada, ctx, plan, ms = ps.calcular(conn, ahora)
    ps.guardar(conn, entrada, ctx, plan, "test", ms)
    return plan


def calculo_a_mano(conn: psycopg.Connection, ahora, franjas: list[tuple]) -> int:  # type: ignore[no-untyped-def]
    """Un cálculo con franjas dadas: (inicio, fin, nivel, salida, provisional)."""
    ident = conn.execute(
        "INSERT INTO plan_calculo (ahora, motivo, modo, prudente, conectado, nivel_inicial_kwh, capacidad_kwh,"
        " umbral_eur_kwh, horizonte_fin, duracion_ms) VALUES (%s, 'test', 'sombra', true, true, 40, 91.3, 0.15, %s, 1)"
        " RETURNING id", (ahora, ahora + timedelta(hours=72))).fetchone()[0]  # type: ignore[index]
    for n, (inicio, fin, nivel, salida, provisional) in enumerate(franjas, 1):
        q = inicio
        while q < fin:
            conn.execute("INSERT INTO plan_cuarto (calculo_id, inicio, nivel, salida, kwh_cargador, precio_eur_kwh,"
                         " provisional, ventana, sobre_umbral) VALUES (%s, %s, %s, %s, 1.125, 0.1, %s, 'segura', false)",
                         (ident, q, nivel, salida, provisional))
            q += CUARTO
        kwh = D("1.125") * int((fin - inicio) / CUARTO)
        conn.execute("INSERT INTO plan_franja (calculo_id, n, inicio, fin, motivo, salida, kwh_cargador, provisional,"
                     " tareas, en_curso) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)",
                     (ident, n, inicio, fin, nivel, salida, kwh, provisional, num_tareas(inicio, fin)))
    return ident


# --- Plan de carga ------------------------------------------------------------------------------


def test_plan_sin_calculo(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    t = texto(get(nombre_bd, AHORA, "/plan").encode())
    assert "Aún no hay plan" in t and "Sin cargas programadas." in t
    assert "Tareas en el cargador" in t and "Aún sin lectura del programador." in t


def test_plan_en_sombra(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    escenario(conn)
    plan = guardar_plan(conn, AHORA)
    html = get(nombre_bd, AHORA + timedelta(minutes=5), "/plan")
    t = texto(html.encode())
    assert "Próxima salida jue 12/11 09:00 cubierta" in t
    assert "umbral barato" in t and "modo prudente: solo con el coche enchufado" in t
    assert re.search(r"plan hasta sáb 14/11 09:00 · 1 franja · [0-9,]+ €", t)
    assert "SOMBRA" in t and "no escrita (sombra)" in t and "programada" in t
    # Desglose por nivel en la tabla: necesaria con su salida, margen y oportunista.
    assert re.search(r"necesaria [0-9,]+ · jue 09:00", t) and re.search(r"oportunista [0-9,]+", t)
    # Modo: selector con sombra marcada (se puede cambiar); las acciones sobre franjas, desactivadas en sombra.
    assert '<input type="radio" name="modo" value="sombra" checked>' in html
    assert 'name="modo" value="activo">' in html and 'action="/plan/modo" method="post"' in html
    assert 'action="/plan/franjas/eliminar"' not in html and '<fieldset class="relleno" disabled>' in html
    # Línea de tiempo: 60 barras de precio por hora en escritorio y 24 en móvil.
    escritorio, movil = html.split('class="lt lt-240"')[1], html.split('class="lt lt-96 movil"')[1]
    assert escritorio.split('class="lt lt-96')[0].count('class="lt-barra') == 60
    assert movil.count('class="lt-barra') == 24
    # La madrugada barata bajo el umbral; la tarde, sobre el umbral.
    assert 'lt-barra bajo' in html and 'lt-barra sobre' in html
    assert len(plan.franjas) == 1 and html.count('class="lt-bloque programada') == 2  # escritorio y móvil


def test_franja_que_cruza_la_medianoche(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    escenario(conn)
    calculo_a_mano(conn, AHORA, [(local(11, 23), local(12, 1), "necesaria", local(12, 9), False)])
    html = get(nombre_bd, AHORA, "/plan")
    escritorio = html.split('class="lt lt-240"')[1].split('class="lt lt-96')[0]
    assert escritorio.count('class="lt-bloque') == 1 and "23:00–01:00" in escritorio
    tabla = html.split('class="fija franjas-plan"')[1].split("</table>")[0]
    assert tabla.count("<tr>") == 2  # cabecera y una fila
    assert "2 h" in texto(tabla.encode()) and "2 tareas" in texto(tabla.encode())


@pytest.mark.parametrize(("estado", "evse", "esperado"), [(6, 4400, "en su horario · coche cargando"),
                                                         (4, 0, "en su horario · coche sin cargar")])
def test_franja_en_curso_en_sombra(conn: psycopg.Connection, nombre_bd: str, limpio: None, estado: int, evse: int,
                                   esperado: str) -> None:
    escenario(conn)
    calculo_a_mano(conn, AHORA - timedelta(hours=1), [(AHORA - timedelta(minutes=30), AHORA + timedelta(hours=2),
                                                       "necesaria", local(12, 9), False)])
    conn.execute("INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w, estado)"
                 " VALUES (%s, 60, %s, 0, 0, %s)", (AHORA - timedelta(minutes=1), evse, estado))
    html = get(nombre_bd, AHORA, "/plan")
    t = texto(html.encode())
    assert esperado in t
    # La franja en curso solo ofrece Detener, desactivado en sombra.
    fila = html.split('class="fija franjas-plan"')[1].split("</tr>")[1]
    assert ">Detener</button>" in fila and "Modificar" not in fila and "/plan/franjas/detener" not in fila
    assert 'disabled title="Cambia a activo para usarlo."' in fila


def test_ultimas_franjas_en_sombra(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    escenario(conn)
    antes = AHORA - timedelta(days=1)
    calculo_a_mano(conn, antes, [(antes + timedelta(hours=4), antes + timedelta(hours=5), "necesaria", None, False),
                                 (antes + timedelta(hours=8), antes + timedelta(hours=9), "oportunista", None, False)])
    conn.execute("INSERT INTO energia_tramo (inicio, fin, coche_wh, casa_wh, total_wh, cubierto_s, incompleto)"
                 " VALUES (%s, %s, 1000, NULL, 1000, 900, false)",
                 (antes + timedelta(hours=4), antes + timedelta(hours=4, minutes=15)))
    t = texto(get(nombre_bd, AHORA, "/plan").encode())
    assert "coincidió con carga real" in t and "sin carga real" in t
    assert "terminada" not in t and "no ejecutada" not in t


def test_franjas_despues_de_la_vista(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    escenario(conn)
    lejos = local(13, 14)  # más allá de las 60 h desde las 00:00 del miércoles 11/11
    calculo_a_mano(conn, AHORA, [(local(12, 2), local(12, 4), "necesaria", local(12, 9), False),
                                 (lejos, lejos + timedelta(hours=1), "oportunista", None, True)])
    t = texto(get(nombre_bd, AHORA, "/plan").encode())
    assert "+1 franja después: vie 13/11 14:00 ▸" in t


def test_precios_provisionales_en_la_linea(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    ahora = local(11, 12)  # antes de las 13:30: el jueves es provisional
    escenario(conn, ahora)
    guardar_plan(conn, ahora)
    html = get(nombre_bd, ahora, "/plan")
    assert 'lt-barra prov' in html and 'title="≈' in html
    html = get(nombre_bd, local(11, 14), "/plan")
    escritorio = html.split('class="lt lt-240"')[1].split('class="lt lt-96')[0]
    # Tras la publicación, solo el viernes (más allá de lo publicado) queda provisional.
    assert escritorio.count("lt-barra prov") == 12


def test_plan_por_debajo_de_300_ms(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    escenario(conn)
    guardar_plan(conn, AHORA)
    app = app_rw(nombre_bd, AHORA)
    app.manejar(Peticion("GET", "/plan", CAB))
    inicio = time.monotonic()
    assert app.manejar(Peticion("GET", "/plan", CAB)).estado == 200
    assert time.monotonic() - inicio < 0.3


# --- Inicio ---------------------------------------------------------------------------------------


def test_proximas_cargas_en_inicio(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    escenario(conn)
    t = texto(get(nombre_bd, AHORA, "/").encode())
    assert "Próximas cargas SOMBRA" in t and "Sin cargas programadas." in t
    guardar_plan(conn, AHORA)
    html = get(nombre_bd, AHORA, "/")
    t = texto(html.encode())
    assert re.search(r"jue 12/11 · 00:00–07:00 necesaria margen oportunista [0-9,]+ kWh [0-9,]+ € programada", t)
    assert 'href="/plan"' in html and 'href="/historial" class="enlace-centrado solo-movil">Historial de lecturas →' in html


# --- Parámetros, Ayuda y navegación -----------------------------------------------------------------


def test_parametros_por_grupos(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    html = get(nombre_bd, AHORA, "/parametros")
    claves = [c for (c,) in conn.execute("SELECT clave FROM bateria_parametro_clave ORDER BY grupo, orden")]
    escritorio = html.split('class="solo-escritorio grupos-escritorio"')[1].split('class="solo-movil grupos-movil')[0]
    movil = html.split('class="solo-movil grupos-movil')[1].split('aria-label="Grupos de parámetros"')[0]
    for zona in (escritorio, movil):
        posiciones = [zona.index(f'<p class="k m clave">{c}</p>') for c in claves]
        assert posiciones == sorted(posiciones)
        assert all(zona.count(f'<p class="k m clave">{c}</p>') == 1 for c in claves)
    # Grupo 6 plegado también en escritorio; en móvil, abiertos el 1 y el 4.
    assert '<details id="g-cargador" class="pn grupo-param">' in escritorio and "avanzado" in escritorio
    assert movil.count("<details") == 7 and movil.count("<details class=\"pn grupo-param\" open>") == 2
    # Calibración dentro del grupo 1, justo después de la eficiencia de carga.
    grupo1 = escritorio.split('id="g-coche"')[1].split("</section>")[0]
    assert grupo1.index("factor_bateria") < grupo1.index("Calibración sugerida") < grupo1.index("consumo_kwh_100km")
    # La guardiana: «solo CLI», sin «Cambiar» ni opción en el selector.
    fila = escritorio.split('<p class="k m clave">id_tarea_guardiana</p>')[1].split("</tr>")[0]
    assert "solo CLI" in fila and "Cambiar" not in fila
    assert 'value="id_tarea_guardiana"' not in html and '<optgroup label="1 · Coche y batería">' in html
    t = texto(html.encode())
    assert "+ guardiana = 8; 2 huecos para la app" in t and "historial actual: 0 días → modo prudente" in t
    assert "Valores técnicos que casi nunca hay que tocar." in t
    assert re.search(r"\d+ parámetros nuevos", t)  # los tests recargan todas las claves de golpe


def test_ayuda_plan_de_carga_y_grupos(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    t = texto(get(nombre_bd, AHORA, "/ayuda").encode())
    assert "Plan de carga" in t and "No edites desde la app de Orbis las franjas del planificador" in t
    assert "guardiana" in t and "oportunista" in t
    for subtitulo in ("1 · Coche y batería ¿Cómo es el coche?", "6 · Cargador y franjas ¿Cómo se escribe en el cargador?"):
        assert subtitulo in t


def test_navegacion(conn: psycopg.Connection, nombre_bd: str, limpio: None) -> None:
    html = get(nombre_bd, AHORA, "/plan")
    lateral = re.findall(r'<a href="(/[a-z-]*)"[^>]*>(?:<svg.*?</svg>)([^<]+)', html.split('class="side"')[1].split("</nav>")[0])
    assert [t for _, t in lateral][:3] == ["Inicio", "Plan de carga", "Excepciones"]
    inferior = html.split('<nav class="nav"')[1].split("</nav>")[0]
    assert re.findall(r"</svg>([^<]+)</a>", inferior) == ["Inicio", "Plan", "Excepciones", "Horario", "Ajustes"]
    assert "formulario / <span>plan</span>" in html


# --- Acciones (paso 3c) ---------------------------------------------------------------------------


def post(nombre_bd: str, ahora, ruta: str, cuerpo: str):  # type: ignore[no-untyped-def]
    from energia_monitor.formulario.app import Peticion as P

    app = app_rw(nombre_bd, ahora)
    app.espera_accion_s = 0
    return app.manejar(P("POST", ruta, {**CAB, "content-type": "application/x-www-form-urlencoded"},
                         cuerpo.encode()))


def activo(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO planificador_modo (desde, modo, origen) VALUES (%s, 'activo', 'usuario')",
                 (AHORA - timedelta(hours=1),))


@pytest.fixture
def sin_acciones(conn: psycopg.Connection) -> Iterator[None]:
    conn.execute("DELETE FROM plan_accion")
    yield
    conn.execute("DELETE FROM plan_accion")
    conn.execute("DELETE FROM planificador_modo WHERE desde > '2025-01-02Z'")


def test_en_sombra_acciones_desactivadas(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                         sin_acciones: None) -> None:
    escenario(conn)
    calculo_a_mano(conn, AHORA, [(local(12, 2), local(12, 4), "necesaria", local(12, 9), False)])
    html = get(nombre_bd, AHORA, "/plan")
    # Cargar ahora, y Modificar y Eliminar en la tabla (escritorio) y en la tarjeta (móvil).
    assert html.count('disabled title="Cambia a activo para usarlo."') == 5
    assert '<fieldset class="relleno" disabled>' in html and "Cambia a activo para usarlo." in texto(html.encode())
    assert 'action="/plan/franjas/eliminar"' not in html and 'name="modo" value="activo">' in html
    for ruta, cuerpo in (("/plan/cargar-ahora", ""), ("/plan/franjas", "dia=2026-11-12&inicio=02:00&fin=03:00"),
                         ("/plan/franjas/detener", "inicio=2026-11-12T01%3A00%3A00%2B00%3A00"),
                         ("/plan/franjas/eliminar", "inicio=2026-11-12T01%3A00%3A00%2B00%3A00&fin=x")):
        r = post(nombre_bd, AHORA, ruta, cuerpo)
        assert r.estado == 409 and "Cambia a activo para usarlo." in texto(r.cuerpo), ruta
    assert conn.execute("SELECT count(*) FROM plan_accion").fetchone() == (0,)


def test_acciones_encolan_y_validan(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                    sin_acciones: None) -> None:
    escenario(conn)
    # Activo sin confirmar: 422 y nada en la cola.
    r = post(nombre_bd, AHORA, "/plan/modo", "modo=activo")
    assert r.estado == 422 and "marca la casilla" in texto(r.cuerpo)
    assert conn.execute("SELECT count(*) FROM plan_accion").fetchone() == (0,)
    r = post(nombre_bd, AHORA, "/plan/modo", "modo=activo&confirmar=si")
    assert r.estado == 303 and r.cabeceras["Location"].startswith("/plan?ok=accion&id=")
    activo(conn)  # lo que haría el muestreo al procesar la acción
    # Franja manual: cuartos de hora, fin del día siguiente si es anterior al inicio y validación previa.
    r = post(nombre_bd, AHORA, "/plan/franjas", "dia=2026-11-12&inicio=02:10&fin=03:00")
    assert r.estado == 422 and "En cuartos de hora" in texto(r.cuerpo)
    r = post(nombre_bd, AHORA, "/plan/franjas", "dia=2026-11-11&inicio=23:30&fin=00:30")
    assert r.estado == 303
    r = post(nombre_bd, AHORA, "/plan/franjas", "dia=2026-11-11&inicio=22:00&fin=22:15")
    assert r.estado == 422 and "franja mínima" in texto(r.cuerpo)
    filas = conn.execute("SELECT tipo, datos->>'inicio', datos->>'fin', estado FROM plan_accion ORDER BY id").fetchall()
    assert [(f[0], f[3]) for f in filas] == [("modo", "pendiente"), ("anadir", "pendiente")]
    assert filas[1][1] == local(11, 23, 30).isoformat() and filas[1][2] == local(12, 0, 30).isoformat()
    # Resultado tras el PRG: pendiente, y tras procesarla, hecha.
    ident = conn.execute("SELECT max(id) FROM plan_accion").fetchone()[0]  # type: ignore[index]
    assert "enviada al cargador" in texto(get(nombre_bd, AHORA, f"/plan?ok=accion&id={ident}").encode())
    conn.execute("UPDATE plan_accion SET estado = 'hecha', resultado = 'franja manual guardada' WHERE id = %s",
                 (ident,))
    assert "Franja manual: franja manual guardada." in texto(get(nombre_bd, AHORA, f"/plan?ok=accion&id={ident}"
                                                                 ).encode())


def test_detener_fallido_ofrece_reintentar(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                           sin_acciones: None) -> None:
    escenario(conn)
    activo(conn)
    inicio = AHORA - timedelta(minutes=30)
    r = post(nombre_bd, AHORA, "/plan/franjas/detener", f"inicio={inicio.isoformat().replace('+', '%2B')}")
    assert r.estado == 303
    conn.execute("UPDATE plan_accion SET estado = 'error', resultado = 'No se pudo detener: x. Pulsa «Reintentar».'")
    ident = conn.execute("SELECT max(id) FROM plan_accion").fetchone()[0]  # type: ignore[index]
    html = get(nombre_bd, AHORA, f"/plan?ok=accion&id={ident}")
    assert 'class="reintentar"' in html and inicio.isoformat() in html
    assert post(nombre_bd, AHORA, "/plan/franjas/detener", "inicio=ayer").estado == 400


def test_modificar_rellena_el_formulario(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                                         sin_acciones: None) -> None:
    escenario(conn)
    activo(conn)
    calculo_a_mano(conn, AHORA, [(local(12, 2), local(12, 4), "necesaria", local(12, 9), False)])
    html = get(nombre_bd, AHORA, f"/plan?modificar={local(12, 2).isoformat().replace('+', '%2B')}")
    assert "Modificar franja" in html and 'value="02:00"' in html and 'value="04:00"' in html
    assert f'name="anterior_inicio" value="{local(12, 2).isoformat()}"' in html


def test_mensajes_del_ultimo_calculo_en_inicio_y_plan(conn: psycopg.Connection, nombre_bd: str,
                                                       limpio: None) -> None:
    """Coche fuera en modo prudente: Inicio dice por qué no hay cargas y Plan de carga, la hora, el motivo y los
    mensajes del último cálculo. Informativos: no son avisos de v_aviso."""
    escenario(conn, enchufado=False)
    plan = guardar_plan(conn, AHORA)
    assert not plan.franjas
    t = texto(get(nombre_bd, AHORA, "/").encode())
    assert "Sin cargas programadas: modo prudente y el coche fuera; se planificará al enchufar." in t
    html = get(nombre_bd, AHORA, "/plan")
    assert ('<p class="k texto mensajes-plan">Último cálculo 20:00 · test · Modo prudente y el coche fuera; se '
            'planificará al enchufar.</p>') in html
    assert conn.execute("SELECT count(*) FROM bateria_avisos(%s) WHERE detalle ILIKE '%%prudente%%'"
                        " OR titulo ILIKE '%%prudente%%'", (AHORA,)).fetchone() == (0,)
