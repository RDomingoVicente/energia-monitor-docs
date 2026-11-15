"""Configuración común de los tests.

Ningún test abre conexiones de red desde Python: el cargador real, ESIOS y
OMIE nunca se contactan. PostgreSQL (tests de integración) va por libpq, que
no usa el módulo socket de Python. Los tests marcados `red_local` (servidor
HTTPS del formulario) pueden conectar solo con 127.0.0.1.
"""

from __future__ import annotations

import socket

import pytest

LOCAL = ("127.0.0.1", "::1", "localhost")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "red_local: permite conexiones solo con 127.0.0.1 (servidor de pruebas)")


@pytest.fixture(autouse=True)
def sin_red(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    local = request.node.get_closest_marker("red_local") is not None
    connect, connect_ex, create_connection = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def es_local(direccion: object) -> bool:
        return local and isinstance(direccion, tuple) and direccion[0] in LOCAL

    def prohibido(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"conexión de red en un test: {args!r}")

    def guardia(original):  # type: ignore[no-untyped-def]
        def envoltura(sock: socket.socket, direccion: object, *args: object, **kwargs: object) -> object:
            if es_local(direccion):
                return original(sock, direccion, *args, **kwargs)
            prohibido(direccion)
            return None
        return envoltura

    def crear(direccion: object, *args: object, **kwargs: object) -> object:
        if es_local(direccion):
            return create_connection(direccion, *args, **kwargs)  # type: ignore[arg-type]
        prohibido(direccion)
        return None

    monkeypatch.setattr(socket.socket, "connect", guardia(connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guardia(connect_ex))
    monkeypatch.setattr(socket, "create_connection", crear)
