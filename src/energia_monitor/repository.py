"""Persistencia de precios en PostgreSQL."""

from __future__ import annotations

import psycopg

from energia_monitor.sources.base import PreciosDia

# Upsert por (inicio, fuente). Una versión de fichero anterior no sobrescribe
# a una posterior ya guardada.
UPSERT_PRECIO = """
INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh, version_fichero, descargado_en)
VALUES (%s, %s, %s, %s, %s, now())
ON CONFLICT (inicio, fuente) DO UPDATE SET
    fin             = EXCLUDED.fin,
    precio_eur_mwh  = EXCLUDED.precio_eur_mwh,
    version_fichero = EXCLUDED.version_fichero,
    descargado_en   = EXCLUDED.descargado_en
WHERE precio_mercado.version_fichero IS NULL
   OR EXCLUDED.version_fichero IS NULL
   OR EXCLUDED.version_fichero >= precio_mercado.version_fichero
"""


def guardar_precios(conn: psycopg.Connection, precios: PreciosDia) -> None:
    """Inserta o actualiza los tramos de un día en una sola transacción."""
    filas = [
        (t.inicio, t.fin, precios.fuente, t.precio_eur_mwh, precios.version)
        for t in precios.tramos
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(UPSERT_PRECIO, filas)
