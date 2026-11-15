"""Servidor HTTPS real y healthcheck con la CA de pruebas (tests/fixtures/tls). Sin base de datos.

Un certificado caducado, con otro nombre o ausente deja el servicio como no
sano: el healthcheck falla en el TLS (o el servidor no arranca).
"""

from __future__ import annotations

import http.client
import os
import shutil
import ssl
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from energia_monitor.formulario.app import App, ConfigApp, Respuesta
from energia_monitor.formulario.salud import comprobar
from energia_monitor.formulario.servidor import ErrorCertificado, ServidorTLS, crear_servidor

TLS = Path(__file__).resolve().parent / "fixtures" / "tls"
pytestmark = pytest.mark.red_local
CA = str(TLS / "ca.crt")
NOMBRE = "formulario.test"


class AppSana(App):
    """La app real, con /salud siempre bien: aquí solo se prueba el TLS."""

    def salud(self) -> Respuesta:
        return Respuesta(200, b'{"estado": "ok"}', "application/json")


def certificados(destino: Path, crt: str) -> Path:
    destino.mkdir(exist_ok=True)
    shutil.copyfile(TLS / crt, destino / "server.crt")
    shutil.copyfile(TLS / "servidor.key", destino / "server.key")
    return destino


@pytest.fixture
def en_marcha(tmp_path: Path) -> Iterator[tuple[ServidorTLS, Path]]:
    directorio = certificados(tmp_path / "certs", "valido.crt")
    servidor = crear_servidor(AppSana(ConfigApp(f"{NOMBRE}:8421")), directorio, 0, "127.0.0.1")
    hilo = threading.Thread(target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    hilo.start()
    yield servidor, directorio
    servidor.shutdown()
    servidor.server_close()


def arrancar(directorio: Path) -> ServidorTLS:
    servidor = crear_servidor(AppSana(ConfigApp(f"{NOMBRE}:8421")), directorio, 0, "127.0.0.1")
    threading.Thread(target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return servidor


def puerto(servidor: ServidorTLS) -> int:
    return servidor.server_address[1]


def test_certificado_valido_sano(en_marcha: tuple[ServidorTLS, Path]) -> None:
    servidor, _ = en_marcha
    r = comprobar(NOMBRE, puerto(servidor), cafile=CA)
    assert r.ok, r.detalle


@pytest.mark.parametrize(("crt", "motivo"), [("caducado.crt", "expired"), ("otro_nombre.crt", "Hostname mismatch")])
def test_certificado_caducado_o_con_otro_nombre_no_sano(tmp_path: Path, crt: str, motivo: str) -> None:
    servidor = arrancar(certificados(tmp_path / "certs", crt))
    try:
        r = comprobar(NOMBRE, puerto(servidor), cafile=CA)
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert not r.ok and r.detalle.startswith("certificado no válido") and motivo in r.detalle


def test_sin_la_ca_del_certificado_no_sano(en_marcha: tuple[ServidorTLS, Path]) -> None:
    # Con las CA del sistema (las del healthcheck real), la CA de pruebas no vale.
    servidor, _ = en_marcha
    r = comprobar(NOMBRE, puerto(servidor))
    assert not r.ok and "certificado no válido" in r.detalle


def test_certificado_ausente_no_arranca(tmp_path: Path) -> None:
    vacio = tmp_path / "certs"
    vacio.mkdir()
    with pytest.raises(ErrorCertificado, match="falta el certificado"):
        crear_servidor(AppSana(ConfigApp(f"{NOMBRE}:8421")), vacio, 0, "127.0.0.1")
    # Sin servidor, el healthcheck no conecta.
    r = comprobar(NOMBRE, 1, cafile=CA, espera_s=1)
    assert not r.ok and r.detalle.startswith("sin conexión TLS")


def test_clave_que_no_corresponde_no_arranca(tmp_path: Path) -> None:
    directorio = certificados(tmp_path / "certs", "valido.crt")
    shutil.copyfile(TLS / "ca.crt", directorio / "server.key")
    with pytest.raises(ErrorCertificado, match="no se puede cargar"):
        crear_servidor(AppSana(ConfigApp(f"{NOMBRE}:8421")), directorio, 0, "127.0.0.1")


def test_recarga_el_certificado_renovado(en_marcha: tuple[ServidorTLS, Path]) -> None:
    servidor, directorio = en_marcha
    assert comprobar(NOMBRE, puerto(servidor), cafile=CA).ok
    # «Renovación» con un certificado caducado: las conexiones nuevas ya lo usan, sin reiniciar.
    shutil.copyfile(TLS / "caducado.crt", directorio / "server.crt")
    futuro = time.time() + 5
    os.utime(directorio / "server.crt", (futuro, futuro))
    r = comprobar(NOMBRE, puerto(servidor), cafile=CA)
    assert not r.ok and "expired" in r.detalle


def test_host_y_cabeceras_por_https(en_marcha: tuple[ServidorTLS, Path]) -> None:
    servidor, _ = en_marcha
    ctx = ssl.create_default_context(cafile=CA)
    ctx.check_hostname = False  # se conecta a 127.0.0.1; el nombre ya se prueba arriba
    conn = http.client.HTTPSConnection("127.0.0.1", puerto(servidor), context=ctx, timeout=5)
    conn.request("GET", "/", headers={"Host": "otro.test:8421"})
    r = conn.getresponse()
    assert r.status == 421 and "default-src 'self'" in (r.getheader("Content-Security-Policy") or "")
    r.read()
    conn.request("GET", "/estatico/estilo.css", headers={"Host": f"{NOMBRE}:8421"})
    r = conn.getresponse()
    assert r.status == 200 and b".pn" in r.read()
    conn.close()


def test_la_marca_red_local_no_abre_otra_red() -> None:
    import socket

    with pytest.raises(AssertionError, match="conexión de red en un test"):
        socket.create_connection(("192.168.1.34", 80), timeout=1)
