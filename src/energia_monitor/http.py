"""Cliente HTTP mínimo (stdlib) con timeout, User-Agent propio y reintentos."""

from __future__ import annotations

import logging
import time
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError

from energia_monitor import __version__
from energia_monitor.errores import ErrorAutenticacion, ErrorFuente

log = logging.getLogger(__name__)

USER_AGENT = f"energia-monitor/{__version__} (colector de precios del mercado electrico)"


class Limitador:
    """Garantiza un intervalo mínimo entre peticiones consecutivas."""

    def __init__(
        self,
        intervalo: float,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.intervalo = intervalo
        self._reloj = reloj
        self._dormir = dormir
        self._ultima: float | None = None

    def esperar(self) -> None:
        if self._ultima is not None:
            pendiente = self._ultima + self.intervalo - self._reloj()
            if pendiente > 0:
                self._dormir(pendiente)
        self._ultima = self._reloj()


class ClienteHttp:
    """GET con reintentos y espera exponencial.

    Devuelve el cuerpo, o None si el recurso no existe (404). Los errores
    transitorios (red, timeout, 429 y 5xx) se reintentan; el resto falla
    (401 y 403 con ErrorAutenticacion). Las cabeceras adicionales (p. ej., un
    token) nunca se escriben en el log ni en los mensajes de error.
    """

    def __init__(
        self,
        timeout: float = 30,
        reintentos: int = 3,
        espera_inicial: float = 5,
        user_agent: str = USER_AGENT,
        dormir: Callable[[float], None] = time.sleep,
        abrir: Callable[..., Any] = urllib.request.urlopen,
        limitador: Limitador | None = None,
    ) -> None:
        if reintentos < 1:
            raise ValueError("reintentos debe ser >= 1")
        self.timeout = timeout
        self.reintentos = reintentos
        self.espera_inicial = espera_inicial
        self.user_agent = user_agent
        self._dormir = dormir
        self._abrir = abrir
        self._limitador = limitador

    def get(self, url: str, cabeceras: Mapping[str, str] | None = None) -> bytes | None:
        ultimo_error: Exception | None = None
        for intento in range(1, self.reintentos + 1):
            if self._limitador is not None:
                self._limitador.esperar()
            peticion = urllib.request.Request(url, headers={**(cabeceras or {}), "User-Agent": self.user_agent})
            try:
                with self._abrir(peticion, timeout=self.timeout) as respuesta:
                    return bytes(respuesta.read())
            except HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (401, 403):
                    raise ErrorAutenticacion(f"HTTP {e.code} en {url}: credenciales rechazadas") from e
                if e.code != 429 and e.code < 500:
                    raise ErrorFuente(f"HTTP {e.code} en {url}") from e
                ultimo_error = e
            except (URLError, TimeoutError, ConnectionError) as e:
                ultimo_error = e
            if intento < self.reintentos:
                espera = self.espera_inicial * 2 ** (intento - 1)
                log.warning("fallo en %s (%s); reintento %d en %.0f s", url, ultimo_error, intento, espera)
                self._dormir(espera)
        raise ErrorFuente(f"sin respuesta válida de {url} tras {self.reintentos} intentos") from ultimo_error
