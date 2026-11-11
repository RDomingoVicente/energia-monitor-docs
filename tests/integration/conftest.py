"""Tests contra un PostgreSQL real.

Se ejecutan solo si PGHOST está definido (variables estándar de libpq). Cada
sesión crea una base de datos temporal, aplica las migraciones y la borra al
terminar. El usuario de PG* debe poder crear bases de datos y roles.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from energia_monitor import db

RAIZ = Path(__file__).resolve().parents[2]
MIGRACIONES = RAIZ / "db" / "migrations"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)
            if not os.environ.get("PGHOST"):
                item.add_marker(pytest.mark.skip(reason="PGHOST no definido: sin PostgreSQL"))


@pytest.fixture(scope="session")
def nombre_bd() -> Iterator[str]:
    nombre = f"em_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{nombre}"')
    try:
        yield nombre
    finally:
        with psycopg.connect(autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')


@pytest.fixture(scope="session")
def migraciones_aplicadas(nombre_bd: str) -> list[str]:
    with psycopg.connect(dbname=nombre_bd) as conn:
        return db.migrar(conn, MIGRACIONES)


@pytest.fixture
def conn(nombre_bd: str, migraciones_aplicadas: list[str]) -> Iterator[psycopg.Connection]:
    with psycopg.connect(dbname=nombre_bd, autocommit=True) as c:
        yield c
