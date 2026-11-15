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
from psycopg import sql


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


# --- Parámetros del cargador (0010) y de la batería (0014) -------------------------------

# Tablas con el mismo patrón (clave, vigencia, valor, origen, fuente, nota) y sus orígenes
# admitidos. Los nombres de tabla salen solo de aquí.
ORIGENES_BATERIA = ("contrato", "regulado", "estimado", "usuario", "ficha_tecnica")
TABLAS_SIMPLES = {
    "cargador": ("cargador_parametro", "cargador_parametro_clave", ORIGENES),
    "bateria": ("bateria_parametro", "bateria_parametro_clave", ORIGENES_BATERIA),
}


def _registrar_simple(
    conn: psycopg.Connection, ambito: str, *, clave: str, desde: date, valor: Decimal, origen: str,
    fuente: str, nota: str | None,
) -> Registro:
    tabla, tabla_clave, origenes = TABLAS_SIMPLES[ambito]
    if not fuente.strip():
        raise ErrorParametro("la fuente es obligatoria")
    if origen not in origenes:
        raise ErrorParametro(f"origen no válido: {origen!r} (usa {', '.join(origenes)})")
    t = sql.Identifier(tabla)
    with conn.transaction():
        if conn.execute(sql.SQL("SELECT 1 FROM {} WHERE clave = %s").format(sql.Identifier(tabla_clave)),
                        (clave,)).fetchone() is None:
            raise ErrorParametro(f"clave desconocida: {clave!r}")
        conn.execute(sql.SQL("SELECT 1 FROM {} WHERE clave = %s FOR UPDATE").format(t), (clave,))
        actual = conn.execute(
            sql.SQL("SELECT id, lower(vigencia), vigencia::text FROM {} WHERE clave = %s AND vigencia @> %s::date")
            .format(t),
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
                sql.SQL("UPDATE {} SET vigencia = daterange(lower(vigencia), %s::date, '[)') WHERE id = %s")
                .format(t),
                (desde, id_actual),
            )
            cerrada = texto_actual
        siguiente = conn.execute(
            sql.SQL("SELECT min(lower(vigencia)) FROM {} WHERE clave = %s AND lower(vigencia) > %s::date").format(t),
            (clave, desde),
        ).fetchone()
        hasta = siguiente[0] if siguiente else None
        try:
            with conn.transaction():
                fila = conn.execute(
                    sql.SQL("INSERT INTO {} (clave, vigencia, valor, origen, fuente, nota)"
                            " VALUES (%s, daterange(%s::date, %s::date, '[)'), %s, %s, %s, %s)"
                            " RETURNING vigencia::text").format(t),
                    (clave, desde, hasta, valor, origen, fuente, nota),
                ).fetchone()
        except psycopg.errors.CheckViolation as e:
            if e.diag.constraint_name == "bateria_parametro_en_rango":
                # Rango de bateria_parametro_clave (0016): el mensaje ya trae el rango admitido.
                raise ErrorParametro(str(e.diag.message_primary)) from e
            raise ErrorParametro(f"valor no válido para {clave}: {valor} ({e.diag.constraint_name})") from e
        assert fila is not None
        return Registro(cerrada=cerrada, nueva=fila[0])


def _listar_simple(conn: psycopg.Connection, ambito: str, fecha: date) -> list[tuple]:
    tabla, tabla_clave, _ = TABLAS_SIMPLES[ambito]
    return conn.execute(
        sql.SQL("SELECT p.clave, '*', p.valor, k.unidad, p.origen, p.vigencia::text, p.fuente"
                " FROM {} p JOIN {} k USING (clave) WHERE p.vigencia @> %s::date ORDER BY p.clave")
        .format(sql.Identifier(tabla), sql.Identifier(tabla_clave)),
        (fecha,),
    ).fetchall()


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
    return _registrar_simple(conn, "cargador", clave=clave, desde=desde, valor=valor, origen=origen,
                             fuente=fuente, nota=nota)


def listar_parametros_cargador(conn: psycopg.Connection, fecha: date) -> list[tuple]:
    """Parámetros del cargador vigentes en la fecha: (clave, '*', valor, unidad, origen, vigencia, fuente)."""
    return _listar_simple(conn, "cargador", fecha)


def registrar_parametro_bateria(
    conn: psycopg.Connection,
    *,
    clave: str,
    desde: date,
    valor: Decimal,
    origen: str,
    fuente: str,
    nota: str | None = None,
) -> Registro:
    """Parámetros de la batería (E2.8). Quien llama recalcula el nivel después."""
    return _registrar_simple(conn, "bateria", clave=clave, desde=desde, valor=valor, origen=origen,
                             fuente=fuente, nota=nota)


def listar_parametros_bateria(conn: psycopg.Connection, fecha: date) -> list[tuple]:
    return _listar_simple(conn, "bateria", fecha)
