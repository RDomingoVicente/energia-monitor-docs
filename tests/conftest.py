"""Configuración común de los tests.

Ningún test abre conexiones de red desde Python: el cargador real, ESIOS y
OMIE nunca se contactan. PostgreSQL (tests de integración) va por libpq, que
no usa el módulo socket de Python.
"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def sin_red(monkeypatch: pytest.MonkeyPatch) -> None:
    def prohibido(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"conexión de red en un test: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.setattr(socket.socket, "connect_ex", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)
