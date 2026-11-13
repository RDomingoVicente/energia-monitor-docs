"""Registro de parámetros de contrato con vigencia, sin publicar otra versión.

registrar_parametro(..., desde=D) hace en una transacción:
  1. Si hay una fila de la misma clave/periodo vigente en D que empezó antes,
     cierra su vigencia en D.
  2. Inserta el nuevo valor desde D hasta el inicio de la siguiente fila
     posterior, o con vigencia abierta si no la hay.
Los valores históricos nunca se modifican.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg


ORIGENES = ("contrato", "regulado", "estimado")


class ErrorParametro(Exception):
    pass


@dataclass(frozen=True)
class Registro:
    cerrada: str | None  # vigencia anterior que se ha cerrado, si la había
    nueva: str  # vigencia de la fila insertada


def registrar_parametro(
    conn: psycopg.Connection,
    *,
    contrato: str,
    clave: str,
    periodo: str,
    desde: date,
    valor: Decimal,
    origen: str,
    fuente: str,
    nota: str | None = None,
) -> Registro:
    if not fuente.strip():
        raise ErrorParametro("la fuente es obligatoria")
    if origen not in ORIGENES:
        raise ErrorParametro(f"origen no válido: {origen!r} (usa {', '.join(ORIGENES)})")
    with conn.transaction():
        filtro = (contrato, clave, periodo)
        conn.execute(
            "SELECT 1 FROM contrato_parametro WHERE contrato = %s AND clave = %s AND periodo = %s FOR UPDATE",
            filtro,
        )
        actual = conn.execute(
            "SELECT id, lower(vigencia), vigencia::text FROM contrato_parametro"
            " WHERE contrato = %s AND clave = %s AND periodo = %s AND vigencia @> %s::date",
            (*filtro, desde),
        ).fetchone()
        cerrada = None
        if actual is not None:
            id_actual, inicio_actual, texto_actual = actual
            if inicio_actual == desde:
                raise ErrorParametro(
                    f"ya hay un valor de {clave}/{periodo} que empieza el {desde} ({texto_actual});"
                    " regístralo con otra fecha o corrígelo en SQL"
                )
            conn.execute(
                "UPDATE contrato_parametro SET vigencia = daterange(lower(vigencia), %s::date, '[)')"
                " WHERE id = %s",
                (desde, id_actual),
            )
            cerrada = texto_actual
        siguiente = conn.execute(
            "SELECT min(lower(vigencia)) FROM contrato_parametro"
            " WHERE contrato = %s AND clave = %s AND periodo = %s AND lower(vigencia) > %s::date",
            (*filtro, desde),
        ).fetchone()
        hasta = siguiente[0] if siguiente else None
        fila = conn.execute(
            "INSERT INTO contrato_parametro (contrato, clave, periodo, vigencia, valor, origen, fuente, nota)"
            " VALUES (%s, %s, %s, daterange(%s::date, %s::date, '[)'), %s, %s, %s, %s)"
            " RETURNING vigencia::text",
            (*filtro, desde, hasta, valor, origen, fuente, nota),
        ).fetchone()
        assert fila is not None
        return Registro(cerrada=cerrada, nueva=fila[0])


def listar_parametros(conn: psycopg.Connection, contrato: str, fecha: date) -> list[tuple]:
    """Parámetros vigentes en la fecha: (clave, periodo, valor, unidad, origen, vigencia, fuente)."""
    return conn.execute(
        "SELECT cp.clave, cp.periodo, cp.valor, pc.unidad, cp.origen, cp.vigencia::text, cp.fuente"
        " FROM contrato_parametro cp JOIN parametro_clave pc USING (clave)"
        " WHERE cp.contrato = %s AND cp.vigencia @> %s::date"
        " ORDER BY cp.clave, cp.periodo",
        (contrato, fecha),
    ).fetchall()


# --- Parámetros del cargador (cargador_parametro, migración 0010) -------------------


def registrar_parametro_cargador(
    conn: psycopg.Connection,
    *,
    clave: str,
    desde: date,
    valor: Decimal,
    origen: str,
    fuente: str,
    nota: str | None = None,
) -> Registro:
    """Como registrar_parametro, para los parámetros del cargador (sin contrato ni periodo)."""
    if not fuente.strip():
        raise ErrorParametro("la fuente es obligatoria")
    if origen not in ORIGENES:
        raise ErrorParametro(f"origen no válido: {origen!r} (usa {', '.join(ORIGENES)})")
    with conn.transaction():
        conn.execute("SELECT 1 FROM cargador_parametro WHERE clave = %s FOR UPDATE", (clave,))
        actual = conn.execute(
            "SELECT id, lower(vigencia), vigencia::text FROM cargador_parametro"
            " WHERE clave = %s AND vigencia @> %s::date",
            (clave, desde),
        ).fetchone()
        cerrada = None
        if actual is not None:
            id_actual, inicio_actual, texto_actual = actual
            if inicio_actual == desde:
                raise ErrorParametro(
                    f"ya hay un valor de {clave} que empieza el {desde} ({texto_actual});"
                    " regístralo con otra fecha o corrígelo en SQL"
                )
            conn.execute(
                "UPDATE cargador_parametro SET vigencia = daterange(lower(vigencia), %s::date, '[)')"
                " WHERE id = %s",
                (desde, id_actual),
            )
            cerrada = texto_actual
        siguiente = conn.execute(
            "SELECT min(lower(vigencia)) FROM cargador_parametro WHERE clave = %s AND lower(vigencia) > %s::date",
            (clave, desde),
        ).fetchone()
        hasta = siguiente[0] if siguiente else None
        fila = conn.execute(
            "INSERT INTO cargador_parametro (clave, vigencia, valor, origen, fuente, nota)"
            " VALUES (%s, daterange(%s::date, %s::date, '[)'), %s, %s, %s, %s)"
            " RETURNING vigencia::text",
            (clave, desde, hasta, valor, origen, fuente, nota),
        ).fetchone()
        assert fila is not None
        return Registro(cerrada=cerrada, nueva=fila[0])


def listar_parametros_cargador(conn: psycopg.Connection, fecha: date) -> list[tuple]:
    """Parámetros del cargador vigentes en la fecha: (clave, '*', valor, unidad, origen, vigencia, fuente)."""
    return conn.execute(
        "SELECT cp.clave, '*', cp.valor, k.unidad, cp.origen, cp.vigencia::text, cp.fuente"
        " FROM cargador_parametro cp JOIN cargador_parametro_clave k USING (clave)"
        " WHERE cp.vigencia @> %s::date ORDER BY cp.clave",
        (fecha,),
    ).fetchall()
