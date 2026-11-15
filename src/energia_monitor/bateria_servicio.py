"""Batería (fase 2, etapa 2): recálculo, lecturas y estado sobre PostgreSQL.

El cálculo está en la migración 0014 (recalcular_bateria y bateria_estado);
la referencia en Python para los tests, en energia_monitor.bateria. Aquí solo
se llama al SQL y se da formato al texto de `bateria estado`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor.tiempo import MADRID

log = logging.getLogger(__name__)

AVISO = "AVISO:"
DIAS_SEMANA = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


class ErrorLectura(Exception):
    pass


def recalcular(conn: psycopg.Connection, ahora: datetime | None = None) -> int:
    """recalcular_bateria(): rehace eventos, trayectos e intervalos. Devuelve cuántos eventos hay."""
    if ahora is None:
        fila = conn.execute("SELECT recalcular_bateria()").fetchone()
    else:
        fila = conn.execute("SELECT recalcular_bateria(%s)", (ahora,)).fetchone()
    assert fila is not None
    return int(fila[0])


def recalcular_sin_fallar(conn: psycopg.Connection, motivo: str) -> None:
    """Para el muestreo: un fallo del recálculo se registra y no detiene el servicio."""
    try:
        n = recalcular(conn)
    except Exception:
        log.exception("fallo al recalcular la batería (%s); se reintentará en el siguiente cambio", motivo)
        return
    log.info("batería recalculada (%s): %d evento(s)", motivo, n)


@dataclass(frozen=True)
class InstanteLocal:
    instante: datetime
    ambigua: bool


def instante_local(texto: str) -> InstanteLocal:
    """'AAAA-MM-DD HH:MM' en hora de Madrid a UTC.

    Una hora que no existe (cambio de marzo) es un error; una ambigua (cambio
    de octubre) se toma como la primera vez, aún en horario de verano.
    """
    try:
        ingenua = datetime.strptime(texto.strip(), "%Y-%m-%d %H:%M")
    except ValueError as e:
        raise ErrorLectura(f"instante no válido: {texto!r} (usa AAAA-MM-DD HH:MM, hora de Madrid)") from e
    local = ingenua.replace(tzinfo=MADRID, fold=0)
    if local.astimezone(UTC).astimezone(MADRID).replace(tzinfo=None) != ingenua:
        raise ErrorLectura(f"{texto} no existe en Madrid (cambio al horario de verano)")
    ambigua = ingenua.replace(tzinfo=MADRID, fold=1).utcoffset() != local.utcoffset()
    return InstanteLocal(local.astimezone(UTC), ambigua)


def registrar_lectura(conn: psycopg.Connection, *, instante: datetime, pct: Decimal, origen: str,
                      nota: str | None = None, ahora: datetime | None = None) -> int:
    """Alta de una lectura y recálculo, en una transacción. Devuelve su id."""
    ahora = ahora or datetime.now(UTC)
    if not Decimal(0) <= pct <= Decimal(100):
        raise ErrorLectura(f"porcentaje fuera de 0 a 100: {pct}")
    if instante > ahora:
        raise ErrorLectura(f"la lectura no puede ser futura ({_local(instante)})")
    try:
        with conn.transaction():
            fila = conn.execute(
                "INSERT INTO bateria_lectura (instante, pct, origen, nota) VALUES (%s, %s, %s, %s) RETURNING id",
                (instante, pct, origen, nota),
            ).fetchone()
            recalcular(conn, ahora)
    except psycopg.errors.UniqueViolation as e:
        raise ErrorLectura(f"ya hay una lectura el {_local(instante)}; anúlala o elige otra hora") from e
    assert fila is not None
    return int(fila[0])


# --- Texto de `bateria estado` -----------------------------------------------------------


def _local(instante: datetime) -> str:
    local = instante.astimezone(MADRID)
    return f"{DIAS_SEMANA[local.weekday()]} {local:%d/%m %H:%M}"


def _hace(delta: timedelta) -> str:
    minutos = int(delta.total_seconds() // 60)
    if minutos < 60:
        return f"{minutos} min"
    horas, minutos = divmod(minutos, 60)
    if horas < 48:
        return f"{horas} h {minutos} min"
    return f"{horas // 24} días"


def _kwh(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:.1f} kWh"


def estado(conn: psycopg.Connection, ahora: datetime) -> list[str]:
    """Texto de `bateria estado`; las líneas de aviso empiezan por AVISO."""
    fila = conn.execute("SELECT * FROM bateria_estado(%s)", (ahora,)).fetchone()
    assert fila is not None
    columnas = [c.name for c in conn.execute("SELECT * FROM bateria_estado(%s) LIMIT 0", (ahora,)).description or []]
    e = dict(zip(columnas, fila))
    lineas: list[str] = []
    if e["nivel_kwh"] is None:
        lineas.append("Batería: sin nivel (no hay ninguna lectura)")
    else:
        origen = "lectura" if e["ultimo_absoluto_tipo"] == "lectura" else "carga completa"
        lineas.append(
            f"Batería: {e['nivel_pct']:.0f} % ({_kwh(e['nivel_kwh'])}) estimado"
            f"{' (dudoso)' if e['dudoso'] else ''}; último valor absoluto: {origen} del"
            f" {_local(e['ultimo_absoluto'])} (hace {_hace(ahora - e['ultimo_absoluto'])}),"
            f" {e['trayectos_desde']} trayecto(s) estimado(s) desde entonces"
        )
        if e["energia_pendiente_kwh"]:
            lineas.append(f"  incluye {_kwh(e['energia_pendiente_kwh'])} entregados en la sesión en curso")
    if e["coche"] == "fuera":
        lineas.append(f"Coche: fuera desde el {_local(e['fuera_desde'])}; consumo estimado del trayecto"
                      f" {_kwh(e['consumo_en_curso_kwh'])}")
    else:
        texto = {"en_casa": "en casa", "cargando": "cargando", "desconocido": "estado desconocido"}[e["coche"]]
        lineas.append(f"Coche: {texto} (estado del conector {e['estado_conector']})")
    if e["proxima_salida"] is None:
        lineas.append("Próxima salida: ninguna en 7 días (horario y excepciones)")
    else:
        lineas.append(
            f"Próxima salida: {_local(e['proxima_salida'])} ({e['tipo_salida']}); consumo previsto"
            f" {_kwh(e['consumo_plan_kwh'])} ({e['origen_consumo_plan']}), reserva {_kwh(e['reserva_kwh'])};"
            f" energía necesaria {_kwh(e['energia_necesaria_kwh'])}"
        )
    sugerido = (f"; sugerido {e['factor_sugerido']:.3f} ({e['observaciones_factor']} observación(es))"
                if e["factor_sugerido"] is not None else "; sin observaciones para sugerir otro")
    lineas.append(f"Factor de batería: vigente {e['factor_vigente']}{sugerido}")
    lineas.extend(f"{AVISO} {a}" for a in e["avisos"])
    return lineas
