"""Cliente HTTP del cargador Orbis Viaris (API local, docs/viaris-api.md).

El cargador es un equipo en producción sin autenticación. Todo acceso pasa por
ClienteViaris, que solo admite los pares (método, ruta) de LECTURAS (los GET
del muestreo y, desde el paso 3c, el del programador) y de ESCRITURAS (POST,
PUT y DELETE de las tareas del conector, paso 3c). La comparación es exacta
(ruta y query) y se hace antes de abrir ninguna conexión. Cada escritura se
valida además contra los formatos verificados el 13/11/2026: una tarea por
petición, con fecha, sin cruzar la medianoche, y solo con ids propios (doble
cerrojo con el reconciliador). Sin redirecciones (podrían llevar a otra ruta),
sin proxies y sin reintentos: el muestreo lo vuelve a intentar en el ciclo
siguiente.
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
RUTA_PROGRAMADOR = "/modules/scheduler/elements?level=cfg"
CONECTOR = "mennekes"
RUTA_TAREAS = f"/modules/scheduler/elements/{CONECTOR}"

# Listas cerradas. Ampliarlas solo cuando lo pida la tarea en curso.
LECTURAS: frozenset[tuple[str, str]] = frozenset({
    ("GET", RUTA_MODULADOR),
    ("GET", RUTA_EVSM),
    ("GET", RUTA_HISTORICO),
    ("GET", RUTA_PROGRAMADOR),  # paso 3c
})
ESCRITURAS: frozenset[tuple[str, str]] = frozenset({
    ("POST", RUTA_TAREAS),  # crear (id libre)
    ("PUT", RUTA_TAREAS),  # modificar o activar (tarea completa)
    ("DELETE", RUTA_TAREAS),  # borrar ({"tasks":[{"id":N}]})
})

TIMEOUT_S = 5.0
MAX_TAREAS = 10
# Campos de una tarea que se envían al crearla o modificarla (los de la tarea leída, sin los calculados por el
# cargador que empiezan por «_»).
CAMPOS_TAREA = frozenset({"id", "user", "group", "priority", "initTime", "active", "type", "maxPower",
                          "solarPriority", "minCurrent", "validFrom", "validTo", "chargeRelated"})
CAMPOS_FRANJA = frozenset({"hourMin", "duration", "maxPower", "phases"})
CAMPOS_HISTORICO = 18
USER_AGENT = f"energia-monitor/{__version__}"


class RutaNoPermitida(Exception):
    """Método o ruta fuera de la lista cerrada del cliente."""


class ErrorCargador(ErrorFuente):
    """El cargador no responde o su respuesta no se puede interpretar."""


class CuerpoNoPermitido(Exception):
    """Escritura con un cuerpo fuera de los formatos verificados o sobre una tarea que no es propia."""


@dataclass(frozen=True)
class Tarea:
    """Una tarea del programador (GET /modules/scheduler/elements?level=cfg)."""

    id: int
    activa: bool
    tipo: int
    weekday: int
    day: int
    month: int
    hour_min: int
    duracion: int
    datos: dict[str, Any]  # la tarea tal cual llegó, para modificarla completa

    @property
    def con_fecha(self) -> bool:
        return self.weekday == 0 and _un_bit(self.day, 31) and _un_bit(self.month, 12)

    @property
    def dia_mes(self) -> tuple[int, int] | None:
        """(día, mes) de una tarea con fecha; None si es semanal o diaria."""
        if not self.con_fecha:
            return None
        return self.day.bit_length(), self.month.bit_length()


def _un_bit(mascara: int, maximo: int) -> bool:
    return 0 < mascara < (1 << maximo) and mascara & (mascara - 1) == 0


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

    def pedir(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None) -> Any:
        """Petición validada contra LECTURAS (sin cuerpo) o ESCRITURAS (con cuerpo ya validado por
        validar_escritura); devuelve el JSON decodificado."""
        if cuerpo is None and (metodo, ruta) not in LECTURAS:
            raise RutaNoPermitida(f"{metodo} {ruta} no está en la lista cerrada del cliente del cargador")
        if cuerpo is not None and (metodo, ruta) not in ESCRITURAS:
            raise RutaNoPermitida(f"{metodo} {ruta} no admite escrituras en el cliente del cargador")
        cabeceras = {"User-Agent": USER_AGENT}
        datos = None
        if cuerpo is not None:
            cabeceras["Content-Type"] = "application/json; charset=utf-8"
            datos = json.dumps(cuerpo, separators=(",", ":")).encode()
        peticion = urllib.request.Request(self.base + ruta, data=datos, method=metodo, headers=cabeceras)
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

    def tareas(self) -> list[Tarea]:
        """Tareas del programador del conector (paso 3c)."""
        return parsear_programador(self.pedir("GET", RUTA_PROGRAMADOR))

    def escribir(self, metodo: str, cuerpo: dict[str, Any], propios: frozenset[int]) -> Any:
        """POST, PUT o DELETE de una tarea propia. Valida antes de conectar; la confirmación (GET) la hace
        quien llama."""
        validar_escritura(metodo, cuerpo, propios)
        return self.pedir(metodo, RUTA_TAREAS, cuerpo)


def validar_escritura(metodo: str, cuerpo: Any, propios: frozenset[int]) -> None:
    """Solo los formatos verificados (docs/viaris-api.md, 13/11/2026), una tarea por petición, tarea propia.

    - DELETE: exactamente {"tasks":[{"id":N}]}.
    - POST y PUT: tarea de carga (type 0 si viene) con fecha (weekday 0, día y mes de un solo bit), una franja
      que no cruza la medianoche, active 0 o 1 y solo los campos conocidos.
    """
    if metodo not in ("POST", "PUT", "DELETE"):
        raise RutaNoPermitida(f"{metodo} no es una escritura del programador")
    if not isinstance(cuerpo, dict) or set(cuerpo) != {"tasks"} or not isinstance(cuerpo["tasks"], list) \
            or len(cuerpo["tasks"]) != 1 or not isinstance(cuerpo["tasks"][0], dict):
        raise CuerpoNoPermitido("se escribe una sola tarea por petición: {\"tasks\": [{...}]}")
    tarea = cuerpo["tasks"][0]
    ident = tarea.get("id")
    if not _entero(ident) or not 1 <= ident <= MAX_TAREAS:
        raise CuerpoNoPermitido(f"id de tarea no válido: {ident!r}")
    if ident not in propios:
        raise CuerpoNoPermitido(f"la tarea {ident} no es propia: no se toca")
    if metodo == "DELETE":
        if set(tarea) != {"id"}:
            raise CuerpoNoPermitido("DELETE solo lleva el id de la tarea")
        return
    if not set(tarea) <= CAMPOS_TAREA:
        raise CuerpoNoPermitido(f"campos no admitidos: {sorted(set(tarea) - CAMPOS_TAREA)}")
    if tarea.get("type", 0) != 0:
        raise CuerpoNoPermitido("solo tareas de carga (type 0)")
    if tarea.get("active") not in (0, 1) or isinstance(tarea.get("active"), bool):
        raise CuerpoNoPermitido("active debe ser 0 o 1")
    inicio = tarea.get("initTime")
    if not isinstance(inicio, dict) or set(inicio) != {"day", "month", "weekday", "timeList"}:
        raise CuerpoNoPermitido("initTime debe llevar day, month, weekday y timeList")
    if inicio["weekday"] != 0 or not _entero(inicio["day"]) or not _entero(inicio["month"]) \
            or not _un_bit(inicio["day"], 31) or not _un_bit(inicio["month"], 12):
        raise CuerpoNoPermitido("solo tareas con fecha: weekday 0 y day y month de un solo bit")
    lista = inicio["timeList"]
    if not isinstance(lista, list) or len(lista) != 1 or not isinstance(lista[0], dict) \
            or not set(lista[0]) <= CAMPOS_FRANJA or not {"hourMin", "duration"} <= set(lista[0]):
        raise CuerpoNoPermitido("timeList debe llevar una sola franja con hourMin y duration")
    hora, duracion = lista[0]["hourMin"], lista[0]["duration"]
    if not _entero(hora) or not _entero(duracion) or not 0 <= hora < 1440 or not 1 <= duracion <= 1440 - hora:
        raise CuerpoNoPermitido("la franja debe estar dentro del día: una tarea no cruza la medianoche")


def _entero(valor: object) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


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


def parsear_programador(datos: Any) -> list[Tarea]:
    """GET /modules/scheduler/elements?level=cfg: tareas del conector mennekes (fixture del 18/11/2026)."""
    try:
        elemento = next(e for e in datos if e.get("name") == CONECTOR)
        crudas = elemento["data"]["cfg"]["tasks"]
    except (StopIteration, KeyError, TypeError, AttributeError) as e:
        raise ErrorCargador(f"respuesta del programador sin tareas del conector {CONECTOR}") from e
    if not isinstance(crudas, list):
        raise ErrorCargador("tasks del programador no es una lista")
    tareas = []
    for t in crudas:
        try:
            inicio = t["initTime"]
            franja = inicio["timeList"][0]
            campos = (t["id"], t["active"], t.get("type", 0), inicio["weekday"], inicio["day"], inicio["month"],
                      franja["hourMin"], franja["duration"])
        except (KeyError, IndexError, TypeError) as e:
            raise ErrorCargador(f"tarea del programador mal formada ({e})") from e
        if not all(_entero(c) for c in campos):
            raise ErrorCargador(f"tarea del programador con campos no enteros: {t.get('id')!r}")
        ident, activa, tipo, weekday, day, month, hora, duracion = campos
        tareas.append(Tarea(ident, activa == 1, tipo, weekday, day, month, hora, duracion, t))
    return tareas


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
