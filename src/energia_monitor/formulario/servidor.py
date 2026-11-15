"""Servidor HTTPS del formulario con la biblioteca estándar (http.server + ssl).

- TLS 1.2 o superior con server.crt y server.key del directorio de certificados
  (montado en solo lectura). Si falta alguno, el servicio no arranca.
- Si cambia la fecha de modificación de los ficheros (renovación con deploy_ct),
  el certificado se recarga para las conexiones nuevas, sin reiniciar.
- El handshake se hace en el hilo de cada conexión; 10 s de espera por conexión
  y 16 KB de cuerpo como máximo.
"""

from __future__ import annotations

import logging
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from energia_monitor.formulario.app import MAX_CUERPO, App, Peticion, Respuesta

log = logging.getLogger(__name__)

ESPERA_S = 10


class ErrorCertificado(Exception):
    pass


class CertificadoTLS:
    """Contexto TLS que se rehace cuando cambian server.crt o server.key."""

    def __init__(self, directorio: Path) -> None:
        self.crt = directorio / "server.crt"
        self.key = directorio / "server.key"
        self._firma: tuple[float, float] | None = None
        self._contexto: ssl.SSLContext | None = None
        self._cerrojo = threading.Lock()
        self.contexto()  # falla al arrancar si no hay certificado

    def _cargar(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(self.crt, self.key)
        except (OSError, ssl.SSLError) as e:
            raise ErrorCertificado(f"no se puede cargar {self.crt} / {self.key}: {e}") from e
        return ctx

    def contexto(self) -> ssl.SSLContext:
        try:
            firma = (self.crt.stat().st_mtime, self.key.stat().st_mtime)
        except OSError as e:
            if self._contexto is None:
                raise ErrorCertificado(f"falta el certificado: {e}") from e
            return self._contexto  # renovación a medias: se sigue con el anterior
        with self._cerrojo:
            if firma != self._firma:
                try:
                    self._contexto = self._cargar()
                    if self._firma is not None:
                        log.info("certificado TLS recargado (%s)", self.crt)
                    self._firma = firma
                except ErrorCertificado:
                    if self._contexto is None:
                        raise
                    log.exception("certificado nuevo no válido; se sigue con el anterior")
            assert self._contexto is not None
            return self._contexto


def manejador(app: App) -> type[BaseHTTPRequestHandler]:
    class Manejador(BaseHTTPRequestHandler):
        server_version = "energia-monitor"
        sys_version = ""
        timeout = ESPERA_S
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            self.request.settimeout(ESPERA_S)
            self.request.do_handshake()
            super().setup()

        def log_message(self, formato: str, *args: object) -> None:
            log.debug("%s %s", self.address_string(), formato % args)

        def _atender(self, metodo: str) -> None:
            longitud = int(self.headers.get("Content-Length") or 0)
            if longitud > MAX_CUERPO:
                self._enviar(Respuesta(413, b"Peticion demasiado grande", "text/plain"), cerrar=True)
                return
            cuerpo = self.rfile.read(longitud) if longitud else b""
            cabeceras = {k.lower(): v for k, v in self.headers.items()}
            r = app.manejar(Peticion(metodo, self.path, cabeceras, cuerpo))
            log.info("%s %s %d", metodo, self.path.split("?")[0], r.estado)
            self._enviar(r, sin_cuerpo=metodo == "HEAD")

        def _enviar(self, r: Respuesta, sin_cuerpo: bool = False, cerrar: bool = False) -> None:
            self.send_response(r.estado)
            self.send_header("Content-Type", r.tipo)
            self.send_header("Content-Length", str(len(r.cuerpo)))
            for k, v in r.cabeceras.items():
                self.send_header(k, v)
            if cerrar:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if not sin_cuerpo:
                self.wfile.write(r.cuerpo)

        def do_GET(self) -> None:  # noqa: N802
            self._atender("GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._atender("HEAD")

        def do_POST(self) -> None:  # noqa: N802
            self._atender("POST")

    return Manejador


class ServidorTLS(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, direccion: tuple[str, int], app: App, certificado: CertificadoTLS) -> None:
        self.certificado = certificado
        super().__init__(direccion, manejador(app))

    def get_request(self) -> tuple[socket.socket, object]:
        sock, direccion = self.socket.accept()
        tls = self.certificado.contexto().wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return tls, direccion

    def handle_error(self, request: object, client_address: object) -> None:
        # Handshakes fallidos, clientes que cortan...: una línea, sin traza.
        log.info("conexión de %s descartada: %s", client_address, sys.exc_info()[1])


def crear_servidor(app: App, directorio_certificados: Path, puerto: int, host: str = "0.0.0.0") -> ServidorTLS:
    """Carga el certificado (ErrorCertificado si falta o no vale) y abre el puerto."""
    return ServidorTLS((host, puerto), app, CertificadoTLS(directorio_certificados))


def servir(app: App, directorio_certificados: Path, puerto: int, stop: threading.Event) -> None:
    with crear_servidor(app, directorio_certificados, puerto) as servidor:
        log.info("formulario en https://0.0.0.0:%d (certificado %s)", servidor.server_address[1],
                 servidor.certificado.crt)
        hilo = threading.Thread(target=servidor.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
        hilo.start()
        stop.wait()
        servidor.shutdown()
