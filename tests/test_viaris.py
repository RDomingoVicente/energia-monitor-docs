"""Cliente del cargador: lista cerrada, errores y lectura de respuestas reales.

Las respuestas reales están en tests/fixtures/viaris/ (firmware 7.7.34; fecha
en el nombre). Nunca se contacta con el cargador.
"""

from __future__ import annotations

import io
import json
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from energia_monitor import viaris
from energia_monitor.viaris import (
    ESCRITURAS,
    LECTURAS,
    RUTA_EVSM,
    RUTA_HISTORICO,
    RUTA_MODULADOR,
    RUTA_PROGRAMADOR,
    RUTA_TAREAS,
    ClienteViaris,
    CuerpoNoPermitido,
    ErrorCargador,
    RutaNoPermitida,
    parsear_programador,
    validar_escritura,
    parsear_evsm,
    parsear_historico,
    parsear_linea_historico,
    parsear_modulador,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viaris"


def fixtures(sufijo: str) -> list[Path]:
    encontrados = sorted(FIXTURES.glob(f"*_{sufijo}.json"))
    assert encontrados, f"falta la respuesta real *_{sufijo}.json en {FIXTURES}"
    return encontrados


def cargar(ruta: Path) -> Any:
    return json.loads(ruta.read_text(encoding="utf-8"))


class Abridor:
    """Sustituto de urlopen: registra las peticiones y devuelve un cuerpo fijo."""

    def __init__(self, cuerpo: bytes = b"{}", error: Exception | None = None) -> None:
        self.cuerpo = cuerpo
        self.error = error
        self.peticiones: list[tuple[urllib.request.Request, float]] = []

    def __call__(self, peticion: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.peticiones.append((peticion, timeout))
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.cuerpo)


# --- Lista cerrada ---------------------------------------------------------------


def test_lista_cerrada_de_esta_etapa() -> None:
    # Paso 3c: el GET del programador y POST, PUT y DELETE de las tareas del conector; nada más.
    assert LECTURAS == {("GET", RUTA_MODULADOR), ("GET", RUTA_EVSM), ("GET", RUTA_HISTORICO),
                        ("GET", "/modules/scheduler/elements?level=cfg")}
    assert ESCRITURAS == {(m, "/modules/scheduler/elements/mennekes") for m in ("POST", "PUT", "DELETE")}
    assert RUTA_PROGRAMADOR in {r for _, r in LECTURAS} and RUTA_TAREAS == "/modules/scheduler/elements/mennekes"


@pytest.mark.parametrize("metodo", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "get", "GET "])
@pytest.mark.parametrize("ruta", [RUTA_MODULADOR, RUTA_EVSM, RUTA_HISTORICO])
def test_rechaza_metodos_distintos_de_get(metodo: str, ruta: str) -> None:
    abridor = Abridor()
    with pytest.raises(RutaNoPermitida):
        ClienteViaris("http://192.168.1.34", abrir=abridor).pedir(metodo, ruta)
    assert abridor.peticiones == []


@pytest.mark.parametrize("ruta", [
    "/reset/sys",
    "/modules/network",
    "/modules/network?level=all",
    "/modules/mqtt_user?level=all",
    "/modules/scheduler/elements/mennekes",  # solo para escrituras
    "/modules/scheduler/elements?level=all",
    "/modules/scheduler/elements",
    "/device",
    "/elements",
    "/elements?level=stat",
    "/modules/modulator",
    "/modules/modulator?level=all",
    "/modules/modulator?level=stat&x=1",
    "/modules/evsm/elements/",
    "/modules/evsm/elements?level=all",
    "/MODULES/evsm/elements",
    "/modules/evsm/../network",
    "//modules/evsm/elements",
    "modules/evsm/elements",
    "http://otro/modules/evsm/elements",
    "",
])
def test_rechaza_rutas_fuera_de_la_lista(ruta: str) -> None:
    abridor = Abridor()
    with pytest.raises(RutaNoPermitida):
        ClienteViaris("http://192.168.1.34", abrir=abridor).pedir("GET", ruta)
    assert abridor.peticiones == []


def test_peticion_get_con_timeout_de_5_s() -> None:
    abridor = Abridor(b'{"stat": {"evsePower": 0, "homePower": 250, "totalPower": 250}}')
    m = ClienteViaris("http://192.168.1.34/", abrir=abridor).modulador()
    assert m.home_w == Decimal(250)
    peticion, timeout = abridor.peticiones[0]
    assert peticion.full_url == "http://192.168.1.34/modules/modulator?level=stat"
    assert peticion.get_method() == "GET"
    assert peticion.data is None
    assert timeout == 5


@pytest.mark.parametrize("url", ["https://192.168.1.34", "192.168.1.34", "http://192.168.1.34/api",
                                 "http://u:p@192.168.1.34", "http://192.168.1.34?x=1", "http://"])
def test_url_del_cargador_sin_ruta(url: str) -> None:
    with pytest.raises(ValueError):
        ClienteViaris(url, abrir=Abridor())


@pytest.mark.parametrize("error", [
    HTTPError("http://192.168.1.34/x", 500, "error", {}, None),  # type: ignore[arg-type]
    HTTPError("http://192.168.1.34/x", 302, "redirección", {}, None),  # type: ignore[arg-type]
    URLError("sin ruta"),
    TimeoutError("timed out"),
    ConnectionRefusedError(),
])
def test_fallos_de_red_como_error_del_cargador(error: Exception) -> None:
    with pytest.raises(ErrorCargador):
        ClienteViaris("http://192.168.1.34", abrir=Abridor(error=error)).estado_conector()


def test_respuesta_no_json() -> None:
    with pytest.raises(ErrorCargador, match="no JSON"):
        ClienteViaris("http://192.168.1.34", abrir=Abridor(b"<html>")).historico()


def test_abridor_sin_redirecciones_ni_proxies() -> None:
    abrir = viaris._abridor()
    manejadores = abrir.__self__.handlers  # type: ignore[attr-defined]
    # ProxyHandler({}) sustituye al de las variables de entorno y no se registra.
    assert all(h.proxies == {} for h in manejadores if isinstance(h, urllib.request.ProxyHandler))
    redirecciones = [h for h in manejadores if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert redirecciones and all(isinstance(h, viaris._SinRedirecciones) for h in redirecciones)
    assert redirecciones[0].redirect_request() is None


# --- Respuestas reales -----------------------------------------------------------


def test_modulador_real() -> None:
    for ruta in fixtures("modules_modulator_level_stat"):
        m = parsear_modulador(cargar(ruta))
        assert m.evse_w >= 0 and m.total_w >= 0, ruta.name


def test_evsm_real() -> None:
    for ruta in fixtures("modules_evsm_elements"):
        e = parsear_evsm(cargar(ruta))
        assert e.estado in range(1, 34), ruta.name


def test_historico_real() -> None:
    for ruta in fixtures("modules_modulator_historic"):
        datos = cargar(ruta)
        sesiones = parsear_historico(datos)
        lineas = [lin for lin in datos["body"].splitlines() if lin.strip()]
        assert sesiones and len(sesiones) == len(lineas), ruta.name
        for s in sesiones:
            assert s.conector == viaris.CONECTOR
            assert s.id == int(s.inicio.timestamp())  # el identificador es el inicio
            assert s.inicio <= s.fin
            assert s.origen_inicio and s.origen_fin


def test_marca_de_tiempo_del_historico_en_utc() -> None:
    # 1793483435 = 31/10/2026 22:50:35 en Madrid (docs/viaris-api.md).
    campos = ["0"] * 18
    campos[0] = campos[6] = "1793483435"
    campos[3], campos[8], campos[10], campos[12], campos[14] = "mennekes", "schedman", "1793500000", "evsm", "41250"
    s = parsear_linea_historico(",".join(campos))
    assert s is not None
    assert s.inicio == datetime(2026, 10, 31, 21, 50, 35, tzinfo=UTC)
    assert s.energia_wh == Decimal(41250)


@pytest.mark.parametrize("linea", [
    "1,2,3",                                   # campos de menos
    ",".join(["x"] * 18),                      # no numérico
    ",".join(["0"] * 18),                      # inicio 0: sesión sin datos
])
def test_lineas_del_historico_no_validas(linea: str) -> None:
    assert parsear_linea_historico(linea) is None


def test_historico_con_fin_anterior_al_inicio_se_descarta() -> None:
    campos = ["0"] * 18
    campos[0] = campos[6] = "1793483435"
    campos[10] = "1793483000"
    assert parsear_linea_historico(",".join(campos)) is None


@pytest.mark.parametrize("datos", [{}, {"stat": {}}, {"stat": {"evsePower": "x", "homePower": 1, "totalPower": 1}},
                                   [], None])
def test_modulador_mal_formado(datos: Any) -> None:
    with pytest.raises(ErrorCargador):
        parsear_modulador(datos)


@pytest.mark.parametrize("datos", [[], {}, [{"stat": {}}], [{"stat": {"state": "4"}}], None])
def test_evsm_mal_formado(datos: Any) -> None:
    with pytest.raises(ErrorCargador):
        parsear_evsm(datos)


# --- Programador y escrituras (paso 3c) -------------------------------------------------


def test_programador_real() -> None:
    """Respuesta real del 18/11/2026: tareas 5 y 6 del usuario (semanales, inactivas) y 9, la guardiana."""
    for ruta in fixtures("modules_scheduler_elements_level_cfg"):
        tareas = parsear_programador(cargar(ruta))
        assert [(x.id, x.activa, x.weekday, x.hour_min, x.duracion) for x in tareas] == [
            (5, False, 4, 1380, 59), (6, False, 8, 0, 480), (9, True, 0, 0, 1)]
        assert [x.dia_mes for x in tareas] == [None, None, (31, 12)]


def tarea(**cambios: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": 3, "active": 1, "user": "", "group": 0, "priority": 1,
                            "initTime": {"day": 1 << 18, "month": 1 << 10, "weekday": 0,
                                         "timeList": [{"hourMin": 1380, "duration": 60}]}}
    base.update(cambios)
    return base


def test_escritura_valida_con_json_y_sin_campos_calculados() -> None:
    abridor = Abridor(b"[]")
    cliente = ClienteViaris("http://192.168.1.34", abrir=abridor)
    cliente.escribir("POST", {"tasks": [tarea()]}, frozenset({3}))
    cliente.escribir("DELETE", {"tasks": [{"id": 3}]}, frozenset({3}))
    (post, _), (delete, _) = abridor.peticiones
    assert post.get_method() == "POST" and post.full_url == "http://192.168.1.34/modules/scheduler/elements/mennekes"
    assert post.get_header("Content-type") == "application/json; charset=utf-8"
    assert json.loads(post.data) == {"tasks": [tarea()]}
    assert delete.get_method() == "DELETE" and json.loads(delete.data) == {"tasks": [{"id": 3}]}


@pytest.mark.parametrize(("metodo", "cuerpo", "mensaje"), [
    ("POST", {"tasks": [tarea(), tarea(id=4)]}, "una sola tarea"),
    ("POST", {"tasks": [tarea(id=11)]}, "id de tarea no válido"),
    ("POST", {"tasks": [tarea(id=True)]}, "id de tarea no válido"),
    ("PUT", {"tasks": [tarea(id=5)]}, "no es propia"),
    ("DELETE", {"tasks": [{"id": 5}]}, "no es propia"),
    ("DELETE", {"tasks": [{"id": 3, "active": 0}]}, "solo lleva el id"),
    ("POST", {"tasks": [tarea(type=1)]}, "type 0"),
    ("POST", {"tasks": [tarea(active=2)]}, "active"),
    ("POST", {"tasks": [tarea(_initLocaltime=0)]}, "campos no admitidos"),
    ("POST", {"tasks": [tarea(initTime={"day": 0, "month": 0, "weekday": 0,
                                        "timeList": [{"hourMin": 0, "duration": 60}]})]}, "con fecha"),
    ("POST", {"tasks": [tarea(initTime={"day": 1 << 18, "month": 1 << 10, "weekday": 32,
                                        "timeList": [{"hourMin": 0, "duration": 60}]})]}, "con fecha"),
    ("POST", {"tasks": [tarea(initTime={"day": 3, "month": 1 << 10, "weekday": 0,
                                        "timeList": [{"hourMin": 0, "duration": 60}]})]}, "con fecha"),
    ("POST", {"tasks": [tarea(initTime={"day": 1 << 18, "month": 1 << 10, "weekday": 0,
                                        "timeList": [{"hourMin": 1380, "duration": 540}]})]}, "medianoche"),
    ("POST", {"tasks": [tarea(initTime={"day": 1 << 18, "month": 1 << 10, "weekday": 0,
                                        "timeList": [{"hourMin": 60, "duration": 60}, {"hourMin": 600,
                                                                                       "duration": 60}]})]},
     "una sola franja"),
    ("POST", {"tasks": [tarea()], "x": 1}, "una sola tarea"),
])
def test_escrituras_no_permitidas(metodo: str, cuerpo: dict[str, Any], mensaje: str) -> None:
    abridor = Abridor()
    with pytest.raises(CuerpoNoPermitido, match=mensaje):
        ClienteViaris("http://192.168.1.34", abrir=abridor).escribir(metodo, cuerpo, frozenset({3}))
    assert abridor.peticiones == []


@pytest.mark.parametrize(("metodo", "ruta"), [("POST", "/reset/sys"), ("PUT", "/modules/network"),
                                              ("PUT", "/device"), ("POST", RUTA_PROGRAMADOR),
                                              ("PATCH", RUTA_TAREAS), ("GET", RUTA_TAREAS)])
def test_cuerpo_solo_en_las_rutas_de_escritura(metodo: str, ruta: str) -> None:
    abridor = Abridor()
    with pytest.raises(RutaNoPermitida):
        ClienteViaris("http://192.168.1.34", abrir=abridor).pedir(metodo, ruta, {"tasks": [tarea()]})
    with pytest.raises(RutaNoPermitida):
        validar_escritura("PATCH", {"tasks": [tarea()]}, frozenset({3}))
    assert abridor.peticiones == []


def test_la_guardiana_es_una_escritura_valida() -> None:
    """31/12 a las 00:00 durante 1 minuto, con id 9 propio."""
    validar_escritura("PUT", {"tasks": [tarea(id=9, initTime={"day": 1 << 30, "month": 1 << 11, "weekday": 0,
                                                           "timeList": [{"hourMin": 0, "duration": 1}]})]},
                      frozenset({9}))
