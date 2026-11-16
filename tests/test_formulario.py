"""Formulario sin base de datos: HTML, formato, seguridad de la app y pantalla de Inicio."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import psycopg
import pytest

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.app import CSP, App, ConfigApp, Peticion
from energia_monitor.formulario.datos import Conexion, Dia, Evento, Inicio, LecturaGuardada
from energia_monitor.formulario.html import Html, esc, h
from energia_monitor.formulario.pantallas import inicio

HOST = "formulario.test:8421"
AHORA = datetime(2026, 11, 16, 18, 30, tzinfo=UTC)  # lun 16/11 19:30 en Madrid


def sin_bd() -> psycopg.Connection:
    raise psycopg.OperationalError("sin base de datos en los tests unitarios")


def app() -> App:
    return App(ConfigApp(HOST), sin_bd, lambda: AHORA, migracion_esperada="0017")


# --- HTML y formato ------------------------------------------------------------------------------


def test_escapa_por_defecto() -> None:
    assert h("<p>{}</p>", "<script>alert(1)</script>") == "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"
    assert h('<a href="{}">', '" onmouseover="x') == '<a href="&quot; onmouseover=&quot;x">'
    assert h("<p>{}</p>", Html("<b>fijo</b>")) == "<p><b>fijo</b></p>"
    assert esc(None) == ""


@pytest.mark.parametrize(("valor", "texto"), [
    (D("47.45"), "47,5"), (D("-9.24"), "−9,2"), (D("-0.04"), "0,0"), (0, "0,0"), (D("1234.5"), "1234,5")])
def test_numero(valor: D, texto: str) -> None:
    assert f.numero(valor) == texto


def test_fechas_en_madrid() -> None:
    assert f.dia_hora(datetime(2026, 11, 16, 8, 30, tzinfo=UTC)) == "lun 16/11 09:30"
    assert f.dia_hora(datetime(2026, 7, 16, 7, 30, tzinfo=UTC)) == "jue 16/07 09:30"  # horario de verano
    assert f.cuando(datetime(2026, 11, 16, 8, 0, tzinfo=UTC), AHORA) == "hoy 09:00"
    assert f.cuando(datetime(2026, 11, 15, 8, 0, tzinfo=UTC), AHORA) == "ayer 09:00"
    assert f.pts(D("-3.4")) == "−3 pts" and f.pts(D(7)) == "+7 pts" and f.pts(D("-7"), signo=False) == "7 pts"


# --- Seguridad (antes de tocar la base de datos) ---------------------------------------------------


def test_host_distinto_421() -> None:
    r = app().manejar(Peticion("GET", "/", {"host": "otro.ts.net:8421"}))
    assert r.estado == 421
    r = app().manejar(Peticion("GET", "/", {}))
    assert r.estado == 421


@pytest.mark.parametrize(("cabeceras", "estado"), [
    ({"origin": "https://otro.test"}, 403),
    ({"origin": "http://formulario.test:8421"}, 403),
    ({"referer": "https://otro.test/"}, 403),
    ({"referer": "https://formulario.test:8421.evil.test/"}, 403),
    ({}, 403),
    ({"origin": "https://formulario.test:8421"}, 503),  # pasa la comprobación; falla la base de datos
    ({"referer": "https://formulario.test:8421/"}, 503),
])
def test_post_de_otro_origen_403(cabeceras: dict[str, str], estado: int) -> None:
    r = app().manejar(Peticion("POST", "/lectura", {"host": HOST, **cabeceras}, b"pct=50"))
    assert r.estado == estado


def test_cabeceras_de_seguridad_en_todas_las_respuestas() -> None:
    for p in (Peticion("GET", "/", {"host": "x"}), Peticion("GET", "/estatico/estilo.css", {"host": HOST}),
              Peticion("GET", "/salud", {}), Peticion("GET", "/no-existe", {"host": HOST})):
        r = app().manejar(p)
        assert r.cabeceras["Content-Security-Policy"] == CSP
        assert r.cabeceras["X-Content-Type-Options"] == "nosniff"
        assert r.cabeceras["Referrer-Policy"] == "same-origin"
    assert "default-src 'self'" in CSP and "unsafe" not in CSP


def test_cuerpo_demasiado_grande_413() -> None:
    r = app().manejar(Peticion("POST", "/lectura", {"host": HOST, "origin": f"https://{HOST}"}, b"x" * 20000))
    assert r.estado == 413


def test_metodo_no_admitido_y_404() -> None:
    assert app().manejar(Peticion("PUT", "/", {"host": HOST})).estado == 405
    assert app().manejar(Peticion("GET", "/no-existe", {"host": HOST})).estado == 404


def test_hoja_de_estilos_sin_recursos_externos() -> None:
    r = app().manejar(Peticion("GET", "/estatico/estilo.css", {"host": HOST}))
    css = r.cuerpo.decode()
    assert r.estado == 200 and r.tipo.startswith("text/css")
    assert "@import" not in css and "url(" not in css and "http" not in css
    assert ".x-100{left:100%}" in css and "@media (max-width: 899px)" in css


def test_iconos_en_la_raiz_sin_recursos_externos() -> None:
    firmas = {"/favicon.ico": ("image/x-icon", b"\x00\x00\x01\x00"), "/apple-touch-icon.png": ("image/png", b"\x89PNG"),
              "/favicon.svg": ("image/svg+xml", b"<svg")}
    for ruta, (tipo, inicio) in firmas.items():
        r = app().manejar(Peticion("GET", ruta, {"host": HOST}))
        assert r.estado == 200 and r.tipo == tipo and r.cuerpo.startswith(inicio), ruta
        assert r.cabeceras["Content-Security-Policy"] == CSP
    svg = app().manejar(Peticion("GET", "/favicon.svg", {"host": HOST})).cuerpo.decode()
    assert "<script" not in svg and "href" not in svg and "CC BY-SA 4.0" in svg
    # Lista cerrada: nada más del directorio estático, ni con otro método o Host.
    assert app().manejar(Peticion("GET", "/estatico/favicon.svg", {"host": HOST})).estado == 404
    assert app().manejar(Peticion("POST", "/favicon.ico", {"host": HOST, "origin": f"https://{HOST}"})).estado == 404
    assert app().manejar(Peticion("GET", "/favicon.ico", {"host": "otro"})).estado == 421


def test_sin_base_de_datos_503() -> None:
    r = app().manejar(Peticion("GET", "/", {"host": HOST}))
    assert r.estado == 503 and "Base de datos no accesible" in r.cuerpo.decode()
    r = app().manejar(Peticion("GET", "/salud", {"host": "cualquiera"}))  # /salud: sin comprobar Host
    cuerpo = json.loads(r.cuerpo)
    assert r.estado == 503 and cuerpo["estado"] == "error" and cuerpo["bd"] is False


# --- Inicio sin base de datos ------------------------------------------------------------------------


def estado(**cambios: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        ahora=AHORA, capacidad_kwh=D("91.3"), nivel_kwh=D("47.476"), nivel_pct=D(52),
        energia_pendiente_kwh=D(0), ultimo_absoluto=AHORA - timedelta(minutes=48), ultimo_absoluto_tipo="lectura",
        trayectos_desde=0, coche="en_casa", estado_conector=4, fuera_desde=None, consumo_en_curso_kwh=D(0),
        nivel_plan_kwh=D("47.476"), dudoso=False, factor_vigente=D("0.86"), factor_sugerido=None,
        observaciones_factor=0, proxima_salida=datetime(2026, 11, 17, 8, 30, tzinfo=UTC), tipo_salida="horario",
        consumo_plan_kwh=D(31), origen_consumo_plan="historia", reserva_kwh=D("22.825"), tope_kwh=D("91.3"),
        objetivo_kwh=D("53.825"), objetivo_pct=D("58.95"), energia_necesaria_kwh=D("6.349"), confianza="leido",
        ultima_lectura_ts=AHORA - timedelta(minutes=48), ultima_lectura_pct=D(52), kwh_cargados_desde_lectura=D(0),
        trayectos_desde_lectura=0, kwh_trayectos_desde_lectura=D(0))
    base.update(cambios)
    return base


def datos_inicio(**cambios: object) -> Inicio:
    dias = [Dia(date(2026, 11, 17), datetime(2026, 11, 17, 8, 30, tzinfo=UTC), "horario", "horario", D(31), D(59)),
            Dia(date(2026, 11, 18), None, "sin salida", "sin-salida", None, None)]
    eventos = [Evento(AHORA - timedelta(minutes=48), "lectura", D(52), False),
               Evento(AHORA - timedelta(hours=2), "trayecto", D("-9.2"), True)]
    return Inicio(AHORA, estado(**cambios), dias, eventos, Conexion(AHORA - timedelta(minutes=50)), D(0))


def texto(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<svg.*?</svg>", "", html)))


def test_casilla_nivel_leido() -> None:
    t = texto(inicio.pantalla(datos_inicio()))
    assert "LEÍDO 52 % 47,5 / 91,3 kWh" in t
    assert "leído hoy 18:42" in t  # 17:42 UTC
    assert "Nivel leído por ti hoy a las 18:42. Objetivo = reserva 25 % + consumo previsto 34 % (31 kWh, P75)." in t
    assert "6,3 kWh" in t and "objetivo 59 % · faltan 7 pts" in t


def test_casilla_nivel_leido_mas_carga() -> None:
    d = datos_inicio(confianza="leido_carga", nivel_pct=D(73), nivel_kwh=D("66.6"), nivel_plan_kwh=D("66.6"),
                     kwh_cargados_desde_lectura=D("19.3"), energia_necesaria_kwh=D(0), coche="cargando",
                     estado_conector=6)
    html = inicio.pantalla(d)
    t = texto(html)
    assert "LEÍDO + CARGA 73 %" in t and "52 % leído 16/11 18:42 +19,3 kWh a batería" in t
    assert "cubierto" in t and "+14 pts de margen (≈13 kWh)" in t
    assert 'class="seg cargado x-52 w-21"' in html  # verde de la lectura (52) al nivel (73)


def test_casilla_nivel_estimado() -> None:
    d = datos_inicio(confianza="estimado", nivel_pct=D(38), nivel_kwh=D("34.7"), nivel_plan_kwh=D("34.7"),
                     kwh_cargados_desde_lectura=D("19.3"), trayectos_desde_lectura=2,
                     kwh_trayectos_desde_lectura=D("29.2"), energia_necesaria_kwh=D("19.1"))
    d.origen_trayectos = {"estimado"}
    t = texto(inicio.pantalla(d))
    assert "ESTIMADO 38 %" in t and "73 % tras carga − 2 trayectos" in t
    assert "Después: carga +19,3 kWh a batería y 2 trayectos estimados (−29,2 kWh, mediana)." in t
    assert "objetivo 59 % · faltan 21 pts" in t


def test_sin_nivel_y_coche_fuera() -> None:
    t = texto(inicio.pantalla(datos_inicio(nivel_kwh=None, nivel_pct=None, nivel_plan_kwh=None, confianza="estimado",
                                           ultima_lectura_ts=None, ultima_lectura_pct=None,
                                           energia_necesaria_kwh=None)))
    assert "Aún no hay nivel: anota el % que marca el coche." in t and "sin nivel" in t
    t = texto(inicio.pantalla(datos_inicio(coche="fuera", estado_conector=1,
                                           fuera_desde=AHORA - timedelta(hours=10))))
    assert "Fuera" in t and "desde 09:30 · nivel a la salida" in t


def test_conectado_cargando_y_sin_cargar() -> None:
    # Como en el NAS el 15-16/11: conexión ayer a las 18:00 (Madrid), carga de 02:00 a 06:00.
    conectado = datetime(2026, 11, 15, 17, 0, 14, tzinfo=UTC)
    d = datos_inicio()
    d.conexion = Conexion(conectado)
    assert "Conectado desde ayer 18:00 · evsm 4 · sin carga" in texto(inicio.pantalla(d))
    d.conexion = Conexion(conectado, None, datetime(2026, 11, 16, 5, 0, 58, tzinfo=UTC))
    assert "Conectado desde ayer 18:00 · sin cargar desde 06:00 · evsm 4" in texto(inicio.pantalla(d))
    # Mientras carga, sin «conectado desde».
    d = datos_inicio(coche="cargando", estado_conector=6)
    d.conexion, d.potencia_w = Conexion(conectado, datetime(2026, 11, 16, 1, 0, 58, tzinfo=UTC)), D(4600)
    t = texto(inicio.pantalla(d))
    assert "Cargando desde 02:00 · evsm 6 · 4,6 kW" in t and "ayer 18:00" not in t


def test_trayecto_partido_muestra_el_total_y_lo_real() -> None:
    d = datos_inicio()
    d.eventos = [Evento(AHORA - timedelta(hours=1), "trayecto", D("-31.74"), True, D("-10.96")),
                 Evento(AHORA - timedelta(hours=2), "trayecto", D("-11.2"), False, D("-11.2")),
                 Evento(AHORA - timedelta(hours=3), "trayecto", D("-9.2"), True, None)]
    t = texto(inicio.pantalla(d))
    assert "trayecto est. −31,7 kWh (11,0 reales)" in t
    assert "trayecto −11,2 kWh" in t and "11,2 reales" not in t  # reconciliado entero: sin paréntesis
    assert "trayecto est. −9,2 kWh" in t and "−9,2 kWh (" not in t


def test_cargas_con_energia_medida_y_a_bateria() -> None:
    d = datos_inicio()
    d.eventos = [Evento(AHORA - timedelta(hours=1), "carga", D("15.33756"), False, None, D("18.259")),
                 Evento(AHORA - timedelta(hours=2), "carga_externa", D("25.8"), False, None, D(30)),
                 Evento(AHORA - timedelta(hours=3), "carga_externa", None, False, None, None)]
    t = texto(inicio.pantalla(d))
    assert "carga 18,3 kWh (15,3 a batería)" in t
    assert "carga externa 30,0 kWh (25,8 a batería)" in t and "carga externa sin kWh" in t


def test_banner_de_lectura_desde_los_datos() -> None:
    d = datos_inicio()
    d.lectura_guardada = LecturaGuardada(D(52), AHORA - timedelta(minutes=48), D("55.2"))
    t = texto(inicio.pantalla(d))
    assert "Lectura guardada: 52 % a las 18:42. El modelo estimaba 55 % (diferencia −3 pts);" in t


def test_formulario_relleno_con_errores() -> None:
    form = inicio.FormLectura("<b>", "2026-11-16", "25:00", {"pct": "Indica un porcentaje entero de 0 a 100."})
    html = inicio.pantalla(datos_inicio(), form)
    assert 'value="&lt;b&gt;" aria-invalid="true"' in html
    assert "Indica un porcentaje entero de 0 a 100." in html
    assert 'placeholder="0–100"' in html


def test_proximos_dias_y_eventos() -> None:
    t = texto(inicio.pantalla(datos_inicio()))
    assert "mar 17/11 09:30 horario 31,0 kWh 59 %" in t
    assert "mié 18/11 — sin salida 0,0 kWh —" in t
    assert "trayecto est. −9,2 kWh" in t and "lectura 52 %" in t


def test_bloques_de_escritorio_y_movil() -> None:
    html = inicio.pantalla(datos_inicio())
    # Escritorio (maqueta 01a): resumen de 4 indicadores, Batería, Próximos 7 días, lectura, eventos.
    orden = [html.index(x) for x in ("Nivel</p>", "Estado del coche", "Próxima salida", "Energía necesaria",
                                      "Próximos 7 días", "Anotar lectura", "Últimos eventos")]
    assert orden == sorted(orden)
    # Móvil: orden con orden-N (banner, batería, coche y necesita, lectura, próximos días).
    for clase in ("bateria-movil solo-movil orden-2", "coche-necesita solo-movil orden-3", "pn orden-4",
                  "solo-movil orden-5"):
        assert clase in html
    assert "style=" not in html  # la CSP no admite estilos en línea
    assert not re.search(r"https?://", html)


# --- Compose (deploy/ no está en la imagen de tests: se salta allí) -------------------------------------


def test_servicio_formulario_en_el_compose() -> None:
    from pathlib import Path

    compose = Path(__file__).resolve().parents[1] / "deploy" / "docker-compose.yml"
    if not compose.exists():
        pytest.skip("deploy/ no se copia a la imagen de tests")
    texto_compose = compose.read_text(encoding="utf-8")
    bloque = texto_compose[texto_compose.index("  formulario:"):texto_compose.index("\nnetworks:")]
    assert "container_name: energia-formulario" in bloque
    assert '"${FORMULARIO_BIND:?' in bloque and ':8421:8443"' in bloque  # solo en la IP del tailnet
    assert "./certs:/certs:ro" in bloque
    assert "PGUSER: formulario_rw" in bloque
    assert '["CMD", "python", "-m", "energia_monitor", "formulario", "salud"]' in bloque
    assert "funnel" not in bloque.lower().replace("nunca con tailscale funnel", "")
