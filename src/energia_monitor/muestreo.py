"""Muestreo continuo del cargador (fase 2, etapa 1).

Proceso separado del colector de precios (servicio `muestreo` del compose):
cada INTERVALO_S lee el modulador y el estado del conector, guarda la muestra
y los cambios de estado; cada INTERVALO_HISTORICO lee el histórico de
sesiones. Si el cargador no responde se registra el error y se reintenta en
el siguiente ciclo (E1.9); el proceso no se detiene.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor import db
from energia_monitor.tiempo import MADRID, limites_dia_utc
from energia_monitor.viaris import CONECTOR, ClienteViaris, ErrorCargador, EstadoConector, Modulador, Sesion

log = logging.getLogger(__name__)

INTERVALO_S = 60
# Más de este hueco entre muestras no se rellena: la muestra lleva intervalo NULL.
INTERVALO_MAX_S = 180
INTERVALO_HISTORICO = timedelta(minutes=15)
# `cargador estado` compara energia_tramo con las muestras en este periodo.
DIAS_COHERENCIA = timedelta(days=2)
# Prefijo de las líneas de `cargador estado` que hacen devolver 1 al comando.
AVISO = "AVISO:"


def intervalo_desde(anterior: datetime | None, instante: datetime) -> int | None:
    """Segundos desde la muestra anterior; None si no la hay o el hueco pasa de 180 s."""
    if anterior is None:
        return None
    segundos = round((instante - anterior).total_seconds())
    if segundos < 1 or segundos > INTERVALO_MAX_S:
        return None
    return segundos


# --- Persistencia ----------------------------------------------------------------


def ultima_muestra(conn: psycopg.Connection) -> datetime | None:
    fila = conn.execute("SELECT max(instante) FROM cargador_muestra").fetchone()
    return fila[0] if fila else None


def guardar_muestra(conn: psycopg.Connection, instante: datetime, modulador: Modulador,
                    estado: int | None) -> int | None:
    """Inserta la muestra (idempotente por instante) y devuelve su intervalo.

    En la misma transacción recalcula energia_tramo en los tramos que cubre
    la muestra, [instante - intervalo, instante): uno, o dos si cruza el
    cuarto de hora.
    """
    with conn.transaction():
        intervalo = intervalo_desde(ultima_muestra(conn), instante)
        conn.execute(
            "INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w, estado)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (instante) DO NOTHING",
            (instante, intervalo, modulador.evse_w, modulador.home_w, modulador.total_w, estado),
        )
        if intervalo is not None:
            recalcular_tramos(conn, instante - timedelta(seconds=intervalo), instante)
    return intervalo


def recalcular_tramos(conn: psycopg.Connection, desde: datetime, hasta: datetime) -> int:
    """Recalcula desde las muestras los tramos que se solapan con [desde, hasta).

    Devuelve cuántos tramos con energía quedan en el rango.
    """
    fila = conn.execute("SELECT recalcular_energia_tramo(%s, %s)", (desde, hasta)).fetchone()
    return int(fila[0]) if fila else 0


@dataclass(frozen=True)
class Coherencia:
    tramos: int
    distintos: int


SQL_COHERENCIA = """
WITH r AS (
    SELECT date_bin('15 minutes', %(desde)s::timestamptz, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS d,
           %(hasta)s::timestamptz AS h
),
guardado AS (SELECT e.* FROM energia_tramo e, r WHERE e.inicio >= r.d AND e.inicio < r.h),
calculado AS (SELECT c.* FROM r, energia_tramo_calculada(r.d, r.h) c)
SELECT (SELECT count(*) FROM calculado),
       (SELECT count(*) FROM (
            (SELECT * FROM guardado EXCEPT ALL SELECT * FROM calculado)
            UNION ALL
            (SELECT * FROM calculado EXCEPT ALL SELECT * FROM guardado)) x)
"""


def coherencia_tramos(conn: psycopg.Connection, desde: datetime, hasta: datetime) -> Coherencia:
    """Compara energia_tramo con el cálculo desde cargador_muestra (tramos con inicio en el rango)."""
    fila = conn.execute(SQL_COHERENCIA, {"desde": desde, "hasta": hasta}).fetchone()
    assert fila is not None
    return Coherencia(int(fila[0]), int(fila[1]))


def registrar_estado(conn: psycopg.Connection, instante: datetime, estado: EstadoConector,
                     conector: str = CONECTOR) -> bool:
    """Guarda el estado si difiere del último registrado. True si es un cambio."""
    fila = conn.execute(
        "SELECT estado FROM cargador_estado WHERE conector = %s ORDER BY detectado_en DESC LIMIT 1",
        (conector,),
    ).fetchone()
    anterior = fila[0] if fila else None
    if anterior == estado.estado:
        return False
    conn.execute(
        "INSERT INTO cargador_estado (conector, detectado_en, estado, estado_anterior, evento, id_carga,"
        " localtime_raw) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (conector, instante, estado.estado, anterior, estado.evento, estado.id_carga, estado.localtime_raw),
    )
    log.info("estado del conector %s: %s -> %s", conector, anterior, estado.estado)
    return True


UPSERT_SESION = """
INSERT INTO cargador_sesion (conector, id, inicio, fin, energia_wh, origen_inicio, origen_fin, linea)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (conector, id) DO UPDATE SET
    inicio = EXCLUDED.inicio, fin = EXCLUDED.fin, energia_wh = EXCLUDED.energia_wh,
    origen_inicio = EXCLUDED.origen_inicio, origen_fin = EXCLUDED.origen_fin,
    linea = EXCLUDED.linea, actualizada_en = now()
WHERE cargador_sesion.linea IS DISTINCT FROM EXCLUDED.linea
RETURNING (xmax = 0) AS nueva
"""


def guardar_sesiones(conn: psycopg.Connection, sesiones: Iterable[Sesion]) -> tuple[int, int]:
    """Upsert de las sesiones. Devuelve (nuevas, modificadas)."""
    nuevas = modificadas = 0
    with conn.transaction():
        for s in sesiones:
            fila = conn.execute(UPSERT_SESION, (s.conector, s.id, s.inicio, s.fin, s.energia_wh,
                                                s.origen_inicio, s.origen_fin, s.linea)).fetchone()
            if fila is None:
                continue
            if fila[0]:
                nuevas += 1
            else:
                modificadas += 1
    return nuevas, modificadas


# --- Ciclo y bucle ---------------------------------------------------------------


class _AvisoFallos:
    """Registra el primer fallo de una lectura y la recuperación, sin repetir cada minuto."""

    def __init__(self, nombre: str, repetir_cada: int = 15) -> None:
        self.nombre = nombre
        self.repetir_cada = repetir_cada
        self.fallos = 0

    def fallo(self, error: Exception) -> None:
        self.fallos += 1
        if self.fallos == 1 or self.fallos % self.repetir_cada == 0:
            log.warning("cargador: fallo en %s (%d seguido(s)): %s", self.nombre, self.fallos, error)

    def exito(self) -> None:
        if self.fallos:
            log.info("cargador: %s recuperado tras %d fallo(s)", self.nombre, self.fallos)
        self.fallos = 0


@dataclass
class Muestreador:
    cliente: ClienteViaris
    reloj: Callable[[], datetime] = lambda: datetime.now(UTC)
    ultimo_historico: datetime | None = None

    def __post_init__(self) -> None:
        self._aviso_modulador = _AvisoFallos("modulador")
        self._aviso_evsm = _AvisoFallos("estado del conector")
        self._aviso_historico = _AvisoFallos("histórico", repetir_cada=4)

    def ciclo(self, conn: psycopg.Connection) -> None:
        """Una muestra y, si toca, el histórico. Los fallos del cargador no se propagan."""
        instante = self.reloj().replace(microsecond=0)
        estado: EstadoConector | None = None
        try:
            estado = self.cliente.estado_conector()
            self._aviso_evsm.exito()
        except ErrorCargador as e:
            self._aviso_evsm.fallo(e)
        try:
            modulador = self.cliente.modulador()
            self._aviso_modulador.exito()
        except ErrorCargador as e:
            self._aviso_modulador.fallo(e)
        else:
            guardar_muestra(conn, instante, modulador, estado.estado if estado else None)
        if estado is not None:
            registrar_estado(conn, instante, estado)
        if self.ultimo_historico is None or instante - self.ultimo_historico >= INTERVALO_HISTORICO:
            self.leer_historico(conn, instante)

    def leer_historico(self, conn: psycopg.Connection, instante: datetime) -> None:
        try:
            sesiones = self.cliente.historico()
        except ErrorCargador as e:
            self._aviso_historico.fallo(e)
            return
        self._aviso_historico.exito()
        nuevas, modificadas = guardar_sesiones(conn, sesiones)
        self.ultimo_historico = instante
        if nuevas or modificadas:
            log.info("histórico del cargador: %d sesión(es) nuevas y %d modificadas de %d leídas",
                     nuevas, modificadas, len(sesiones))


def ejecutar(
    cliente: ClienteViaris | None,
    stop: threading.Event,
    conectar: Callable[[], psycopg.Connection] = db.conectar,
    reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Servicio de muestreo. Sin cliente (VIARIS_URL vacía) queda inactivo hasta la parada."""
    if cliente is None:
        log.info("muestreo del cargador desactivado (sin VIARIS_URL): en espera hasta la parada")
        stop.wait()
        return
    with conectar() as conn:
        aplicadas = db.migrar(conn)
        log.info("migraciones aplicadas: %s", ", ".join(aplicadas) if aplicadas else "ninguna pendiente")
    log.info("muestreo del cargador en %s cada %d s", cliente.base, INTERVALO_S)
    muestreador = Muestreador(cliente, reloj)
    while not stop.is_set():
        inicio = reloj()
        try:
            with conectar() as conn:
                muestreador.ciclo(conn)
        except Exception:
            # Fallo de base de datos u otro imprevisto: siguiente ciclo.
            log.exception("fallo en el ciclo de muestreo")
        transcurrido = (reloj() - inicio).total_seconds()
        stop.wait(max(INTERVALO_S - transcurrido, 1))


# --- Diagnóstico -----------------------------------------------------------------


@dataclass(frozen=True)
class Hueco:
    desde: datetime
    hasta: datetime


def huecos_del_dia(conn: psycopg.Connection, ahora: datetime) -> list[Hueco]:
    """Tramos sin muestras válidas en el día local de `ahora`, hasta `ahora`."""
    inicio_dia, _ = limites_dia_utc(ahora.astimezone(MADRID).date())
    filas = conn.execute(
        "SELECT instante, intervalo_s FROM cargador_muestra WHERE instante >= %s AND instante <= %s"
        " ORDER BY instante",
        (inicio_dia, ahora),
    ).fetchall()
    huecos: list[Hueco] = []
    cubierto_hasta = inicio_dia
    for instante, intervalo in filas:
        desde = instante - timedelta(seconds=intervalo) if intervalo else instante
        if desde > cubierto_hasta:
            huecos.append(Hueco(cubierto_hasta, desde))
        cubierto_hasta = max(cubierto_hasta, instante)
    if (ahora - cubierto_hasta).total_seconds() > INTERVALO_MAX_S:
        huecos.append(Hueco(cubierto_hasta, ahora))
    return huecos


def estado(conn: psycopg.Connection, cliente: ClienteViaris | None, ahora: datetime) -> list[str]:
    """Texto del comando `cargador estado`."""
    lineas: list[str] = []
    if cliente is None:
        lineas.append("Cargador: VIARIS_URL no definida (muestreo desactivado)")
    else:
        try:
            m = cliente.modulador()
            lineas.append(f"Cargador {cliente.base}: alcanzable (coche {m.evse_w} W, {_casa(m.evse_w, m.home_w)},"
                          f" total {m.total_w} W)")
        except ErrorCargador as e:
            lineas.append(f"Cargador {cliente.base}: NO alcanzable ({e})")

    fila = conn.execute(
        "SELECT instante, intervalo_s, evse_w, home_w, total_w, estado FROM cargador_muestra"
        " ORDER BY instante DESC LIMIT 1"
    ).fetchone()
    if fila is None:
        lineas.append("Última muestra: ninguna")
    else:
        instante, intervalo, evse, home, total, est = fila
        edad = round((ahora - instante).total_seconds())
        lineas.append(f"Última muestra: {instante.astimezone(MADRID):%Y-%m-%d %H:%M:%S} (hace {edad} s),"
                      f" coche {evse} W, {_casa(evse, home)}, total {total} W, estado {est}")

    n, ultima_ini, ultima_fin, ultima_wh = conn.execute(
        "SELECT count(*), max(inicio), (array_agg(fin ORDER BY inicio DESC))[1],"
        " (array_agg(energia_wh ORDER BY inicio DESC))[1] FROM cargador_sesion"
    ).fetchone()  # type: ignore[misc]
    if n:
        lineas.append(f"Sesiones guardadas: {n}; última {ultima_ini.astimezone(MADRID):%Y-%m-%d %H:%M}"
                      f" a {ultima_fin.astimezone(MADRID):%H:%M}, {ultima_wh} Wh")
    else:
        lineas.append("Sesiones guardadas: ninguna")

    huecos = huecos_del_dia(conn, ahora)
    if huecos:
        lineas.append(f"Huecos de hoy: {len(huecos)}")
        for h in huecos:
            minutos = (h.hasta - h.desde).total_seconds() / 60
            lineas.append(f"  {h.desde.astimezone(MADRID):%H:%M:%S} a {h.hasta.astimezone(MADRID):%H:%M:%S}"
                          f" ({minutos:.1f} min)")
    else:
        lineas.append("Huecos de hoy: ninguno")

    c = coherencia_tramos(conn, ahora - DIAS_COHERENCIA, ahora)
    if c.distintos:
        desde = (ahora - DIAS_COHERENCIA).astimezone(MADRID).date()
        log.warning("energia_tramo no coincide con las muestras en %d tramo(s) de los últimos %d días",
                    c.distintos, DIAS_COHERENCIA.days)
        lineas.append(f"{AVISO} energía por tramo: {c.distintos} tramo(s) de los últimos {DIAS_COHERENCIA.days}"
                      f" días no coinciden con las muestras; corregir con `cargador recalcular --desde {desde}`")
    else:
        lineas.append(f"Energía por tramo (últimos {DIAS_COHERENCIA.days} días): {c.tramos} tramo(s),"
                      " coinciden con las muestras")
    return lineas


def _casa(evse_w: Decimal, home_w: Decimal) -> str:
    # Durante la carga homePower es 0 (verificado el 14/11/2026): no es la casa.
    if evse_w > 0:
        return "casa desconocida (cargando)"
    return f"casa {home_w} W"
