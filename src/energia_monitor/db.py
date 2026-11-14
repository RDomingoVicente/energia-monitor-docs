"""Conexión a PostgreSQL (variables estándar de libpq) y migraciones."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

log = logging.getLogger(__name__)

PATRON_MIGRACION = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Identificador arbitrario del advisory lock que serializa las migraciones.
LOCK_MIGRACIONES = 7_265_301


def directorio_migraciones() -> Path:
    return Path(os.environ.get("MIGRATIONS_DIR", "db/migrations"))


def conectar(**kwargs: object) -> psycopg.Connection:
    """Conexión con PGHOST, PGPORT, PGDATABASE, PGUSER y PGPASSWORD.

    En autocommit: cada operación que lo necesita abre su propia transacción.
    """
    kwargs.setdefault("autocommit", True)
    return psycopg.connect(**kwargs)  # type: ignore[arg-type]


def ficheros_migracion(directorio: Path) -> list[Path]:
    ficheros = sorted(p for p in directorio.iterdir() if PATRON_MIGRACION.match(p.name))
    versiones = [p.name[:4] for p in ficheros]
    if len(versiones) != len(set(versiones)):
        raise RuntimeError(f"versiones de migración duplicadas en {directorio}")
    return ficheros


def migrar(conn: psycopg.Connection, directorio: Path | None = None) -> list[str]:
    """Aplica en orden las migraciones pendientes, cada una en su transacción.

    Devuelve los nombres de las migraciones aplicadas.
    """
    directorio = directorio or directorio_migraciones()
    aplicadas: list[str] = []
    conn.autocommit = True
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_MIGRACIONES,))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     text PRIMARY KEY,
                nombre      text NOT NULL,
                aplicada_en timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        hechas = {fila[0] for fila in conn.execute("SELECT version FROM schema_migrations")}
        for fichero in ficheros_migracion(directorio):
            version = fichero.name[:4]
            if version in hechas:
                continue
            log.info("aplicando migración %s", fichero.name)
            with conn.transaction():
                # Sin parámetros: psycopg envía el fichero tal cual y admite
                # varias sentencias.
                conn.execute(fichero.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                conn.execute(
                    "INSERT INTO schema_migrations (version, nombre) VALUES (%s, %s)",
                    (version, fichero.name),
                )
            aplicadas.append(fichero.name)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_MIGRACIONES,))
    return aplicadas


def configurar_rol_grafana(conn: psycopg.Connection, password: str) -> None:
    """Habilita el login de grafana_ro con la contraseña de GRAFANA_DB_PASSWORD."""
    if not password:
        raise ValueError("contraseña vacía para grafana_ro")
    conn.execute(
        sql.SQL("ALTER ROLE grafana_ro WITH LOGIN PASSWORD {}").format(sql.Literal(password))
    )


@dataclass(frozen=True)
class Refresco:
    filas: int
    esperadas: int
    segundos: float

    @property
    def completo(self) -> bool:
        return self.filas == self.esperadas


SQL_FILAS_PRECIO_FINAL = """
SELECT (SELECT count(*) FROM mv_precio_final),
       (SELECT count(*) FROM precio_mercado) * (SELECT count(*) FROM contrato)
"""


def refrescar_precio_final(conn: psycopg.Connection) -> Refresco:
    """Refresca mv_precio_final sin bloquear las lecturas y comprueba sus filas.

    Esperadas: una por tramo de precio_mercado y contrato, como v_precio_final.
    Hace falta tras cualquier cambio en sus datos de origen (docs/parametros.md).
    """
    inicio = time.monotonic()
    conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_precio_final")
    segundos = time.monotonic() - inicio
    fila = conn.execute(SQL_FILAS_PRECIO_FINAL).fetchone()
    assert fila is not None
    refresco = Refresco(int(fila[0]), int(fila[1]), segundos)
    if refresco.completo:
        log.info("mv_precio_final refrescada en %.1f s: %d filas (esperadas %d)",
                 segundos, refresco.filas, refresco.esperadas)
    else:
        log.warning("mv_precio_final refrescada en %.1f s con %d filas, pero se esperaban %d"
                    " (tramos de precio_mercado por contratos)", segundos, refresco.filas, refresco.esperadas)
    return refresco
