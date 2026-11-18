"""Batería (fase 2, etapa 2): recálculo, lecturas y estado sobre PostgreSQL.

El cálculo está en la migración 0014 (recalcular_bateria y bateria_estado);
la referencia en Python para los tests, en energia_monitor.bateria. Aquí solo
se llama al SQL y se da formato al texto de `bateria estado`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg

from energia_monitor.plan_carga import servicio as plan_servicio
from energia_monitor.tiempo import MADRID

log = logging.getLogger(__name__)

AVISO = "AVISO:"
DIAS_SEMANA = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


class ErrorLectura(Exception):
    pass


class ErrorExcepcion(Exception):
    """Excepción no válida; el formulario la devuelve como 422 con este mensaje junto al campo `campo`."""

    def __init__(self, mensaje: str, campo: str | None = None) -> None:
        super().__init__(mensaje)
        self.campo = campo


DIAS_LARGOS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def recalcular(conn: psycopg.Connection, ahora: datetime | None = None) -> int:
    """recalcular_bateria() y actualizar_avisos(): rehace eventos, trayectos e intervalos y abre o
    cierra los avisos del historial. Devuelve cuántos eventos hay."""
    if ahora is None:
        fila = conn.execute("SELECT recalcular_bateria()").fetchone()
    else:
        fila = conn.execute("SELECT recalcular_bateria(%s)", (ahora,)).fetchone()
    assert fila is not None
    actualizar_avisos(conn, ahora)
    # Lectura, excepción, horario, carga externa o parámetro: el planificador recalcula en el ciclo
    # siguiente del muestreo (etapa 3). El propio muestreo también pasa por aquí; su ciclo ya planifica.
    plan_servicio.solicitar_sin_fallar(conn, "cambio en la batería")
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


def _insertar_lectura(conn: psycopg.Connection, instante: datetime, pct: Decimal, origen: str, nota: str | None,
                      ahora: datetime, corrige: int | None = None) -> int:
    if not Decimal(0) <= pct <= Decimal(100):
        raise ErrorLectura(f"porcentaje fuera de 0 a 100: {pct}")
    if instante > ahora:
        raise ErrorLectura(f"la lectura no puede ser futura ({_local(instante)})")
    try:
        with conn.transaction():
            fila = conn.execute(
                "INSERT INTO bateria_lectura (instante, pct, origen, nota, corrige) VALUES (%s, %s, %s, %s, %s)"
                " RETURNING id",
                (instante, pct, origen, nota, corrige),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise ErrorLectura(f"ya hay una lectura el {_local(instante)}; anúlala o elige otra hora") from e
    assert fila is not None
    return int(fila[0])


def registrar_lectura(conn: psycopg.Connection, *, instante: datetime, pct: Decimal, origen: str,
                      nota: str | None = None, ahora: datetime | None = None) -> int:
    """Alta de una lectura y recálculo, en una transacción. Devuelve su id."""
    ahora = ahora or datetime.now(UTC)
    with conn.transaction():
        ident = _insertar_lectura(conn, instante, pct, origen, nota, ahora)
        recalcular(conn, ahora)
    return ident


def _anular_lectura(conn: psycopg.Connection, id_: int, motivo: str) -> None:
    if not motivo.strip():
        raise ErrorLectura("anular exige un motivo")
    fila = conn.execute("UPDATE bateria_lectura SET anulada_en = now(), motivo = %s"
                        " WHERE id = %s AND anulada_en IS NULL RETURNING id", (motivo.strip(), id_)).fetchone()
    if fila is None:
        raise ErrorLectura(f"no hay ninguna lectura activa con id {id_}")


def anular_lectura(conn: psycopg.Connection, id_: int, motivo: str, ahora: datetime | None = None) -> None:
    """Baja lógica con motivo y recálculo. Las lecturas no se borran."""
    with conn.transaction():
        _anular_lectura(conn, id_, motivo)
        recalcular(conn, ahora)


def corregir_lectura(conn: psycopg.Connection, id_: int, *, instante: datetime, pct: Decimal, origen: str,
                     nota: str | None = None, ahora: datetime | None = None) -> int:
    """Corregir (formulario-maquetas.md, 7.4): anula la lectura con motivo «corregida» y da de alta la
    nueva (corrige = id), en una transacción. Si el alta falla, la anterior sigue activa. Devuelve el id nuevo."""
    ahora = ahora or datetime.now(UTC)
    with conn.transaction():
        _anular_lectura(conn, id_, "corregida")
        nuevo = _insertar_lectura(conn, instante, pct, origen, nota, ahora, corrige=id_)
        recalcular(conn, ahora)
    return nuevo


def actualizar_avisos_sin_fallar(conn: psycopg.Connection) -> None:
    """Para el muestreo, en cada ciclo: un fallo se registra y no detiene el servicio."""
    try:
        actualizar_avisos(conn)
    except Exception:
        log.exception("fallo al actualizar los avisos; se reintentará en el siguiente ciclo")


# --- Excepciones (E2.7, 0015) -------------------------------------------------------------


@dataclass(frozen=True)
class NuevaExcepcion:
    tipo: str  # con_salida o sin_salida
    fecha: date
    hora_salida: time | None = None
    consumo_modo: str | None = None  # normal o indicado
    km: Decimal | None = None
    pct: Decimal | None = None
    pct_objetivo: Decimal | None = None
    nota: str | None = None


def _validar_excepcion(conn: psycopg.Connection, x: NuevaExcepcion) -> None:
    """Lo que no comprueba la base de datos: sin_salida solo en un día con salida en el horario.

    Los campos de cada tipo los comprueban los CHECK de bateria_excepcion; aquí se
    repiten para dar un mensaje legible antes de llegar a ellos.
    """
    if x.tipo == "sin_salida":
        if any(v is not None for v in (x.hora_salida, x.consumo_modo, x.km, x.pct, x.pct_objetivo)):
            raise ErrorExcepcion("sin salida: solo se indica la fecha")
        fila = conn.execute("SELECT hora_salida FROM bateria_horario WHERE dia_iso = %s",
                            (x.fecha.isoweekday(),)).fetchone()
        if fila is None or fila[0] is None:
            # Texto de formulario-maquetas.md, 7.2.
            raise ErrorExcepcion(f"El {DIAS_LARGOS[x.fecha.weekday()]} {x.fecha:%d/%m} ya no tiene salida según "
                                 "tu horario", "fecha")
    elif x.tipo == "con_salida":
        if x.hora_salida is None:
            raise ErrorExcepcion("con salida: falta la hora de salida", "hora")
        if x.consumo_modo == "normal":
            if x.km is not None or x.pct is not None:
                raise ErrorExcepcion("consumo normal: no se indican km ni %", "consumo")
        elif x.consumo_modo == "indicado":
            if (x.km is None) == (x.pct is None):
                raise ErrorExcepcion("con «Indicar consumo», indica km o %, uno de los dos", "consumo")
        else:
            raise ErrorExcepcion(f"modo de consumo no válido: {x.consumo_modo!r} (normal o indicado)")
    else:
        raise ErrorExcepcion(f"tipo no válido: {x.tipo!r} (con_salida o sin_salida)")


def _insertar_excepcion(conn: psycopg.Connection, x: NuevaExcepcion) -> int:
    _validar_excepcion(conn, x)
    try:
        with conn.transaction():
            fila = conn.execute(
                "INSERT INTO bateria_excepcion (tipo, fecha, hora_salida, consumo_modo, km, pct, pct_objetivo, nota)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (x.tipo, x.fecha, x.hora_salida, x.consumo_modo, x.km, x.pct, x.pct_objetivo, x.nota),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise ErrorExcepcion(f"ya hay una excepción activa el {x.fecha:%d/%m/%Y}; modifícala", "fecha") from e
    except psycopg.errors.CheckViolation as e:
        raise ErrorExcepcion(f"excepción no válida: {e.diag.constraint_name}") from e
    assert fila is not None
    return int(fila[0])


def registrar_excepcion(conn: psycopg.Connection, x: NuevaExcepcion, ahora: datetime | None = None) -> int:
    """Alta de una excepción y recálculo, en una transacción. Devuelve su id."""
    with conn.transaction():
        id_ = _insertar_excepcion(conn, x)
        recalcular(conn, ahora)
    return id_


def anular_excepcion(conn: psycopg.Connection, id_: int, ahora: datetime | None = None) -> None:
    """Baja lógica y recálculo."""
    with conn.transaction():
        fila = conn.execute("UPDATE bateria_excepcion SET anulada_en = now() WHERE id = %s AND anulada_en IS NULL"
                            " RETURNING id", (id_,)).fetchone()
        if fila is None:
            raise ErrorExcepcion(f"no hay ninguna excepción activa con id {id_}")
        recalcular(conn, ahora)


def modificar_excepcion(conn: psycopg.Connection, id_: int, x: NuevaExcepcion, ahora: datetime | None = None) -> int:
    """«Modificar»: baja de la anterior y alta de la nueva en la misma transacción. Si el alta
    falla, la anterior sigue activa. Devuelve el id nuevo."""
    with conn.transaction():
        fila = conn.execute("UPDATE bateria_excepcion SET anulada_en = now() WHERE id = %s AND anulada_en IS NULL"
                            " RETURNING id", (id_,)).fetchone()
        if fila is None:
            raise ErrorExcepcion(f"no hay ninguna excepción activa con id {id_}")
        nuevo = _insertar_excepcion(conn, x)
        recalcular(conn, ahora)
    return nuevo


# --- Horario (7.3) --------------------------------------------------------------------------------


def guardar_horario(conn: psycopg.Connection, dias: dict[int, time | None], ahora: datetime | None = None) -> None:
    """Los 7 días (1 = lunes; None: sin salida) en una transacción, sin vigencia: se aplican desde ya."""
    if sorted(dias) != list(range(1, 8)):
        raise ValueError("el horario necesita los 7 días (1 a 7)")
    with conn.transaction():
        for dia, hora in sorted(dias.items()):
            conn.execute(
                "INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (%s, %s)"
                " ON CONFLICT (dia_iso) DO UPDATE SET hora_salida = EXCLUDED.hora_salida, actualizado_en = now()",
                (dia, hora))
        recalcular(conn, ahora)


# --- Cargas externas (0016) --------------------------------------------------------------------


class ErrorCargaExterna(Exception):
    """Carga externa no válida; el formulario la devuelve como 422 con este mensaje junto al campo `campo`."""

    def __init__(self, mensaje: str, campo: str | None = None) -> None:
        super().__init__(mensaje)
        self.campo = campo


@dataclass(frozen=True)
class NuevaCargaExterna:
    instante: datetime
    kwh: Decimal | None = None
    coste_eur: Decimal | None = None
    nota: str | None = None


def _validar_carga_externa(conn: psycopg.Connection, x: NuevaCargaExterna, ahora: datetime) -> None:
    if x.instante > ahora:
        raise ErrorCargaExterna(f"la carga externa no puede ser futura ({_local(x.instante)})", "hora")
    if x.kwh is not None and not Decimal(0) <= x.kwh <= Decimal(150):
        raise ErrorCargaExterna(f"energía fuera de 0 a 150 kWh: {x.kwh}", "kwh")
    if x.coste_eur is not None and x.coste_eur < 0:
        raise ErrorCargaExterna(f"coste negativo: {x.coste_eur}", "coste_eur")
    # Desconectado de casa: el último estado del conector hasta ese instante es libre (1, 2) y antes
    # estuvo conectado (como un trayecto, E2.4). Se mira cargador_estado, no bateria_trayecto, para no
    # depender de un recálculo.
    fila = conn.execute(
        "SELECT estado FROM cargador_estado WHERE detectado_en <= %s AND estado BETWEEN 1 AND 8"
        " ORDER BY detectado_en DESC LIMIT 1", (x.instante,)).fetchone()
    antes = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM cargador_estado WHERE detectado_en < %s AND estado BETWEEN 3 AND 8)",
        (x.instante,)).fetchone()
    if fila is None or fila[0] not in (1, 2) or not (antes and antes[0]):
        raise ErrorCargaExterna("A esa hora el coche estaba conectado en casa", "hora")


def _insertar_carga_externa(conn: psycopg.Connection, x: NuevaCargaExterna, origen: str, ahora: datetime,
                            corrige: int | None = None) -> int:
    _validar_carga_externa(conn, x, ahora)
    try:
        with conn.transaction():
            fila = conn.execute(
                "INSERT INTO bateria_carga_externa (instante, kwh, coste_eur, nota, origen, corrige)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (x.instante, x.kwh, x.coste_eur, x.nota, origen, corrige),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise ErrorCargaExterna(f"ya hay una carga externa el {_local(x.instante)}", "hora") from e
    except psycopg.errors.CheckViolation as e:
        raise ErrorCargaExterna(f"carga externa no válida: {e.diag.constraint_name}") from e
    assert fila is not None
    return int(fila[0])


def registrar_carga_externa(conn: psycopg.Connection, x: NuevaCargaExterna, origen: str,
                            ahora: datetime | None = None) -> int:
    """Alta de una carga fuera de casa y recálculo, en una transacción. Devuelve su id."""
    ahora = ahora or datetime.now(UTC)
    with conn.transaction():
        id_ = _insertar_carga_externa(conn, x, origen, ahora)
        recalcular(conn, ahora)
    return id_


def _anular_carga(conn: psycopg.Connection, id_: int, motivo: str) -> None:
    if not motivo.strip():
        raise ErrorCargaExterna("anular exige un motivo")
    fila = conn.execute("UPDATE bateria_carga_externa SET anulada_en = now(), motivo = %s"
                        " WHERE id = %s AND anulada_en IS NULL RETURNING id", (motivo, id_)).fetchone()
    if fila is None:
        raise ErrorCargaExterna(f"no hay ninguna carga externa activa con id {id_}")


def anular_carga_externa(conn: psycopg.Connection, id_: int, motivo: str, ahora: datetime | None = None) -> None:
    with conn.transaction():
        _anular_carga(conn, id_, motivo)
        recalcular(conn, ahora)


def corregir_carga_externa(conn: psycopg.Connection, id_: int, x: NuevaCargaExterna, origen: str,
                           ahora: datetime | None = None) -> int:
    """Corregir: anula la anterior con motivo «corregida» y da de alta la nueva (corrige = id), en una
    transacción. Si el alta falla, la anterior sigue activa. Devuelve el id nuevo."""
    ahora = ahora or datetime.now(UTC)
    with conn.transaction():
        _anular_carga(conn, id_, "corregida")
        nuevo = _insertar_carga_externa(conn, x, origen, ahora, corrige=id_)
        recalcular(conn, ahora)
    return nuevo


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
            f"Batería: {e['nivel_pct']:.0f} % ({_kwh(e['nivel_kwh'])}) {CONFIANZA[e['confianza']]}"
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
            f"Próxima salida: {_local(e['proxima_salida'])}"
            f" ({'excepción' if e['tipo_salida'] == 'con_salida' else 'horario'}); consumo previsto"
            f" {_kwh(e['consumo_plan_kwh'])} ({e['origen_consumo_plan']}), reserva {_kwh(e['reserva_kwh'])};"
            f" energía necesaria {_kwh(e['energia_necesaria_kwh'])}"
        )
    sugerido = (f"; sugerido {e['factor_sugerido']:.3f} ({e['observaciones_factor']} observación(es))"
                if e["factor_sugerido"] is not None else "; sin observaciones para sugerir otro")
    lineas.append(f"Factor de batería: vigente {e['factor_vigente']}{sugerido}")
    lineas.extend(f"{AVISO} [{a.nivel}] {a.titulo}: {a.detalle}" for a in avisos(conn, ahora))
    return lineas


CONFIANZA = {"leido": "LEÍDO", "leido_carga": "LEÍDO + CARGA", "estimado": "ESTIMADO"}


@dataclass(frozen=True)
class Aviso:
    clave: str
    nivel: str  # error, aviso o info
    titulo: str
    detalle: str
    desde: datetime | None
    regla: str
    accion: str | None


def avisos(conn: psycopg.Connection, ahora: datetime) -> list[Aviso]:
    """bateria_avisos(): los avisos activos (catálogo de formulario-maquetas.md, 7.7)."""
    return [Aviso(*f) for f in conn.execute("SELECT * FROM bateria_avisos(%s)", (ahora,))]


def actualizar_avisos(conn: psycopg.Connection, ahora: datetime | None = None) -> int:
    """actualizar_avisos(): abre y cierra filas de aviso_historial. Devuelve los activos."""
    if ahora is None:
        fila = conn.execute("SELECT actualizar_avisos()").fetchone()
    else:
        fila = conn.execute("SELECT actualizar_avisos(%s)", (ahora,)).fetchone()
    assert fila is not None
    return int(fila[0])
