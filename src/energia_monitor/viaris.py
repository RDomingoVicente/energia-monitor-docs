"""Cliente HTTP del cargador Orbis Viaris (API local, docs/viaris-api.md).

El cargador es un equipo en producción sin autenticación. Todo acceso pasa por
ClienteViaris, que solo admite los pares (método, ruta) de LECTURAS: en la
etapa 1, únicamente los GET que usa el muestreo. La comparación es exacta
(ruta y query) y se hace antes de abrir ninguna conexión. Sin redirecciones
(podrían llevar a otra ruta), sin proxies y sin reintentos: el muestreo lo
vuelve a intentar en el siguiente ciclo.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from energia_monitor import __version__
from energia_monitor.errores import ErrorFuente

log = logging.getLogger(__name__)

RUTA_MODULADOR = "/modules/modulator?level=stat"
RUTA_EVSM = "/modules/evsm/elements"
RUTA_HISTORICO = "/modules/modulator/historic"

# Lista cerrada. Ampliarla solo cuando lo pida la tarea en curso.
LECTURAS: frozenset[tuple[str, str]] = frozenset({
    ("GET", RUTA_MODULADOR),
    ("GET", RUTA_EVSM),
    ("GET", RUTA_HISTORICO),
})

TIMEOUT_S = 5.0
CONECTOR = "mennekes"
CAMPOS_HISTORICO = 18
USER_AGENT = f"energia-monitor/{__version__}"


class RutaNoPermitida(Exception):
    """Método o ruta fuera de la lista cerrada del cliente."""


class ErrorCargador(ErrorFuente):
    """El cargador no responde o su respuesta no se puede interpretar."""


@dataclass(frozen=True)
class Modulador:
    evse_w: Decimal
    home_w: Decimal
    total_w: Decimal


@dataclass(frozen=True)
class EstadoConector:
    estado: int
    evento: str | None
    id_carga: str | None
    localtime_raw: int | None


@dataclass(frozen=True)
class Sesion:
    id: int
    conector: str
    inicio: datetime
    fin: datetime
    energia_wh: Decimal
    origen_inicio: str
    origen_fin: str
    linea: str


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        return None  # urllib eleva HTTPError con el 3xx


def _abridor() -> Callable[..., Any]:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SinRedirecciones()).open


def _url_base(url: str) -> str:
    partes = urlsplit(url.strip())
    if partes.scheme != "http" or not partes.hostname:
        raise ValueError("VIARIS_URL debe ser http://host[:puerto]")
    if partes.path not in ("", "/") or partes.query or partes.fragment or partes.username:
        raise ValueError("VIARIS_URL no admite ruta, query, fragmento ni credenciales")
    return f"http://{partes.netloc}"


class ClienteViaris:
    def __init__(self, url: str, abrir: Callable[..., Any] | None = None, timeout: float = TIMEOUT_S) -> None:
        self.base = _url_base(url)
        self.timeout = timeout
        self._abrir = abrir or _abridor()

    def pedir(self, metodo: str, ruta: str) -> Any:
        """Petición validada contra LECTURAS; devuelve el JSON decodificado."""
        if (metodo, ruta) not in LECTURAS:
            raise RutaNoPermitida(f"{metodo} {ruta} no está en la lista cerrada del cliente del cargador")
        peticion = urllib.request.Request(self.base + ruta, method=metodo, headers={"User-Agent": USER_AGENT})
        try:
            with self._abrir(peticion, timeout=self.timeout) as respuesta:
                cuerpo = respuesta.read()
        except HTTPError as e:
            raise ErrorCargador(f"HTTP {e.code} en {metodo} {ruta}") from e
        except (URLError, TimeoutError, OSError) as e:
            raise ErrorCargador(f"sin respuesta en {metodo} {ruta}: {e}") from e
        try:
            return json.loads(cuerpo)
        except ValueError as e:
            raise ErrorCargador(f"respuesta no JSON en {metodo} {ruta}") from e

    def modulador(self) -> Modulador:
        return parsear_modulador(self.pedir("GET", RUTA_MODULADOR))

    def estado_conector(self) -> EstadoConector:
        return parsear_evsm(self.pedir("GET", RUTA_EVSM))

    def historico(self) -> list[Sesion]:
        return parsear_historico(self.pedir("GET", RUTA_HISTORICO))


# --- Interpretación de las respuestas --------------------------------------------


def _decimal(valor: object, campo: str) -> Decimal:
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        raise ErrorCargador(f"{campo} no numérico: {valor!r}")
    try:
        return Decimal(str(valor))
    except InvalidOperation as e:
        raise ErrorCargador(f"{campo} no numérico: {valor!r}") from e


def parsear_modulador(datos: Any) -> Modulador:
    """GET /modules/modulator?level=stat: potencias en W de stat."""
    try:
        stat = datos["stat"]
        return Modulador(
            evse_w=_decimal(stat["evsePower"], "evsePower"),
            home_w=_decimal(stat["homePower"], "homePower"),
            total_w=_decimal(stat["totalPower"], "totalPower"),
        )
    except (KeyError, TypeError) as e:
        raise ErrorCargador(f"respuesta del modulador sin el campo {e}") from e


def _texto(valor: object) -> str | None:
    return None if valor is None else str(valor)


def parsear_evsm(datos: Any) -> EstadoConector:
    """GET /modules/evsm/elements: stat.state del primer (único) conector."""
    try:
        stat = datos[0]["stat"]
        estado = stat["state"]
    except (KeyError, IndexError, TypeError) as e:
        raise ErrorCargador(f"respuesta de evsm sin stat.state ({e})") from e
    if isinstance(estado, bool) or not isinstance(estado, int):
        raise ErrorCargador(f"stat.state no entero: {estado!r}")
    localtime = stat.get("localtime")
    return EstadoConector(
        estado=estado,
        evento=_texto(stat.get("event")),
        id_carga=_texto(stat.get("idCharge")),
        localtime_raw=localtime if isinstance(localtime, int) and not isinstance(localtime, bool) else None,
    )


def _instante(segundos: str) -> datetime:
    return datetime.fromtimestamp(int(segundos), tz=UTC)


def parsear_linea_historico(linea: str) -> Sesion | None:
    """Una línea de 18 campos; None si no es una sesión cerrada válida."""
    campos = [c.strip() for c in linea.split(",")]
    if len(campos) != CAMPOS_HISTORICO:
        return None
    try:
        inicio, fin = int(campos[6]), int(campos[10])
        sesion = Sesion(
            id=int(campos[0]),
            conector=campos[3],
            inicio=_instante(campos[6]),
            fin=_instante(campos[10]),
            energia_wh=Decimal(campos[14]),
            origen_inicio=campos[8],
            origen_fin=campos[12],
            linea=linea.strip(),
        )
    except (ValueError, InvalidOperation, OverflowError):
        return None
    if inicio <= 0 or fin < inicio or sesion.energia_wh < 0:
        return None
    return sesion


def parsear_historico(datos: Any) -> list[Sesion]:
    """GET /modules/modulator/historic: body con una sesión por línea."""
    try:
        cuerpo = datos["body"]
    except (KeyError, TypeError) as e:
        raise ErrorCargador("respuesta del histórico sin body") from e
    if not isinstance(cuerpo, str):
        raise ErrorCargador("body del histórico no es texto")
    sesiones = []
    for linea in cuerpo.splitlines():
        if not linea.strip():
            continue
        sesion = parsear_linea_historico(linea)
        if sesion is None:
            log.warning("línea del histórico descartada: %r", linea[:200])
        else:
            sesiones.append(sesion)
    return sesiones
