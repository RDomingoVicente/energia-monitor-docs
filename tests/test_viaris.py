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
    LECTURAS,
    RUTA_EVSM,
    RUTA_HISTORICO,
    RUTA_MODULADOR,
    ClienteViaris,
    ErrorCargador,
    RutaNoPermitida,
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


def test_lista_cerrada_solo_get_de_esta_etapa() -> None:
    assert LECTURAS == {("GET", RUTA_MODULADOR), ("GET", RUTA_EVSM), ("GET", RUTA_HISTORICO)}


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
    "/modules/scheduler/elements/mennekes",
    "/modules/scheduler/elements?level=cfg",
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
