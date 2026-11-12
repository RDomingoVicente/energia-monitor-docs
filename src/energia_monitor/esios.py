"""Series de ESIOS en PostgreSQL: catálogo, guardado, descargas y controles.

- Los indicadores salen de la tabla esios_indicador (migración 0009).
- serie_esios guarda el primer valor visto de cada tramo: así se detecta y se
  mide cuándo y cuánto revisa REE cada serie.
- Los componentes de SSAA se contrastan con los totales que publica REE
  (10379 y 10382): una diferencia indica un componente nuevo sin guardar.
- La previsión de SSAA del PVPC (1871) solo se usa en la estimación cuando se
  ha comprobado que REE no corrige el valor publicado la víspera.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import psycopg

from energia_monitor.errores import ErrorAutenticacion, ErrorFuente
from energia_monitor.sources.esios import SerieEsios
from energia_monitor.tiempo import MADRID, dias, limites_dia_utc

log = logging.getLogger(__name__)

# Máximo de días por petición (una respuesta de 31 días de un indicador
# cuartohorario de una zona ocupa ~0,7 MB).
MAX_DIAS_POR_PETICION = 31
# Ventanas de la actualización diaria y de la recarga semanal.
DIAS_ACTUALIZACION = 10
DIAS_RECARGA_SEMANAL = 90
# Suma de componentes frente al total publicado: hasta 9 valores redondeados
# a 2 decimales (error máximo observado en 2026: 0,02 €/MWh).
TOLERANCIA_CONTROL = Decimal("0.05")
# Previsión 1871: días comprobados sin cambios antes de darla por buena.
DIAS_VERIFICACION_PREVISION = 7
TOLERANCIA_REVISION = Decimal("0.005")
DIAS_HASTA_RECOMPROBAR = 2


class Cliente(Protocol):
    def serie(self, indicador: int, geo_id: int, resolucion: timedelta,
              desde: datetime, hasta: datetime) -> SerieEsios: ...


@dataclass(frozen=True)
class Indicador:
    id: int
    geo_id: int
    resolucion: timedelta
    uso: str
    control: int | None


def indicadores(conn: psycopg.Connection, usos: Iterable[str] | None = None) -> list[Indicador]:
    filas = conn.execute(
        "SELECT id, geo_id, resolucion, uso, control FROM esios_indicador ORDER BY id"
    ).fetchall()
    todos = [Indicador(*fila) for fila in filas]
    if usos is None:
        return todos
    filtro = set(usos)
    return [i for i in todos if i.uso in filtro]


# --- Guardado ------------------------------------------------------------

# Una descarga con publicación anterior a la guardada no sobrescribe.
UPSERT_SERIE = """
INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (indicador, geo_id, inicio) DO UPDATE SET
    fin           = EXCLUDED.fin,
    valor         = EXCLUDED.valor,
    publicado_en  = EXCLUDED.publicado_en,
    descargado_en = now(),
    modificado_en = CASE WHEN serie_esios.valor <> EXCLUDED.valor THEN now()
                         ELSE serie_esios.modificado_en END
WHERE EXCLUDED.publicado_en >= serie_esios.publicado_en
"""


@dataclass(frozen=True)
class Cambio:
    inicio: datetime
    anterior: Decimal
    nuevo: Decimal


@dataclass
class Guardado:
    nuevos: int = 0
    cambios: list[Cambio] = field(default_factory=list)


def guardar_serie(conn: psycopg.Connection, serie: SerieEsios) -> Guardado:
    """Upsert de los valores de una serie en una transacción; devuelve altas y cambios."""
    resultado = Guardado()
    if not serie.valores or serie.publicado_en is None:
        return resultado
    with conn.transaction(), conn.cursor() as cur:
        existentes: dict[datetime, tuple[Decimal, datetime]] = {
            inicio: (valor, publicado)
            for inicio, valor, publicado in cur.execute(
                "SELECT inicio, valor, publicado_en FROM serie_esios"
                " WHERE indicador = %s AND geo_id = %s AND inicio >= %s AND inicio <= %s",
                (serie.indicador, serie.geo_id, serie.valores[0].inicio, serie.valores[-1].inicio),
            )
        }
        for v in serie.valores:
            previo = existentes.get(v.inicio)
            if previo is None:
                resultado.nuevos += 1
            elif serie.publicado_en >= previo[1] and previo[0] != v.valor:
                resultado.cambios.append(Cambio(v.inicio, previo[0], v.valor))
        cur.executemany(UPSERT_SERIE, [
            (serie.indicador, serie.geo_id, v.inicio, v.fin, v.valor, serie.publicado_en, v.valor)
            for v in serie.valores
        ])
    return resultado


# --- Descargas -----------------------------------------------------------


def rangos_contiguos(fechas: Iterable[date], max_dias: int = MAX_DIAS_POR_PETICION) -> list[tuple[date, date]]:
    """Agrupa fechas en rangos [desde, hasta] contiguos de como mucho max_dias."""
    rangos: list[tuple[date, date]] = []
    for dia in sorted(set(fechas)):
        if rangos:
            desde, hasta = rangos[-1]
            if dia == hasta + timedelta(days=1) and (dia - desde).days < max_dias:
                rangos[-1] = (desde, dia)
                continue
        rangos.append((dia, dia))
    return rangos


@dataclass
class ResumenEsios:
    peticiones: int = 0
    valores: int = 0
    nuevos: int = 0
    # indicador -> días con valores revisados por REE
    revisados: dict[int, set[date]] = field(default_factory=lambda: defaultdict(set))
    cambios: int = 0
    fallidos: list[tuple[int, date, date]] = field(default_factory=list)
    interrumpido: bool = False


def descargar(
    conn: psycopg.Connection,
    cliente: Cliente,
    rangos: Mapping[Indicador, Sequence[tuple[date, date]]],
    etiqueta: str,
    stop: threading.Event | None = None,
    guardar: Callable[[psycopg.Connection, SerieEsios], Guardado] = guardar_serie,
) -> ResumenEsios:
    """Descarga y guarda los rangos de días (de Madrid) de cada indicador.

    Un fallo en una petición no detiene el resto, salvo que el token sea
    rechazado (ErrorAutenticacion se propaga).
    """
    resumen = ResumenEsios()
    total = sum(len(r) for r in rangos.values())
    if total == 0:
        log.info("ESIOS %s: nada pendiente", etiqueta)
        return resumen
    log.info("ESIOS %s: %d petición(es) en %d indicador(es)", etiqueta, total, len(rangos))
    for indicador, lista in rangos.items():
        for desde, hasta in lista:
            if stop is not None and stop.is_set():
                resumen.interrumpido = True
                log.warning("ESIOS %s: interrumpida tras %d/%d peticiones", etiqueta, resumen.peticiones, total)
                return resumen
            inicio, _ = limites_dia_utc(desde)
            _, fin = limites_dia_utc(hasta)
            resumen.peticiones += 1
            try:
                serie = cliente.serie(indicador.id, indicador.geo_id, indicador.resolucion, inicio, fin)
            except ErrorAutenticacion:
                raise
            except ErrorFuente as e:
                resumen.fallidos.append((indicador.id, desde, hasta))
                log.warning("ESIOS %s: fallo en %d (%s a %s): %s", etiqueta, indicador.id, desde, hasta, e)
                continue
            guardado = guardar(conn, serie)
            resumen.valores += len(serie.valores)
            resumen.nuevos += guardado.nuevos
            resumen.cambios += len(guardado.cambios)
            for cambio in guardado.cambios:
                resumen.revisados[indicador.id].add(cambio.inicio.astimezone(MADRID).date())
            if resumen.peticiones % 20 == 0:
                log.info("ESIOS %s: %d/%d peticiones", etiqueta, resumen.peticiones, total)
    for indicador_id, fechas in sorted(resumen.revisados.items()):
        log.info("ESIOS %s: REE ha revisado el indicador %d en %d día(s): %s",
                 etiqueta, indicador_id, len(fechas), ", ".join(map(str, sorted(fechas))))
    log.info("ESIOS %s: %d valores (%d nuevos, %d revisados), %d petición(es) con error",
             etiqueta, resumen.valores, resumen.nuevos, resumen.cambios, len(resumen.fallidos))
    return resumen


SQL_COBERTURA = """
SELECT (inicio AT TIME ZONE 'Europe/Madrid')::date AS dia, sum(fin - inicio) AS cubierto
FROM serie_esios
WHERE indicador = %s AND geo_id = %s AND inicio >= %s AND inicio < %s
GROUP BY 1
"""


def dias_incompletos(conn: psycopg.Connection, indicador: Indicador, desde: date, hasta: date) -> list[date]:
    """Días de [desde, hasta] cuyos tramos guardados no cubren el día completo."""
    if hasta < desde:
        return []
    inicio, _ = limites_dia_utc(desde)
    _, fin = limites_dia_utc(hasta)
    cobertura: dict[date, timedelta] = dict(
        conn.execute(SQL_COBERTURA, (indicador.id, indicador.geo_id, inicio, fin)).fetchall()
    )
    faltan = []
    for dia in dias(desde, hasta):
        a, b = limites_dia_utc(dia)
        if cobertura.get(dia) != b - a:
            faltan.append(dia)
    return faltan


def completar_desde(conn: psycopg.Connection, cliente: Cliente, desde: date, hoy: date,
                    stop: threading.Event | None = None) -> ResumenEsios:
    """Al arrancar: descarga los días sin datos entre desde y mañana (carga inicial)."""
    rangos = {
        ind: rangos_contiguos(dias_incompletos(conn, ind, desde, hoy + timedelta(days=1)))
        for ind in indicadores(conn)
    }
    rangos = {ind: r for ind, r in rangos.items() if r}
    return descargar(conn, cliente, rangos, "arranque", stop)


def actualizar(conn: psycopg.Connection, cliente: Cliente, hoy: date, dias_atras: int = DIAS_ACTUALIZACION,
               stop: threading.Event | None = None, etiqueta: str = "actualización") -> ResumenEsios:
    """Vuelve a descargar todos los indicadores de hoy - dias_atras a mañana."""
    rango = rangos_contiguos(dias(hoy - timedelta(days=dias_atras), hoy + timedelta(days=1)))
    resumen = descargar(conn, cliente, {ind: rango for ind in indicadores(conn)}, etiqueta, stop)
    comprobar_controles(conn, hoy - timedelta(days=dias_atras), hoy + timedelta(days=1))
    return resumen


# --- Controles de suma -----------------------------------------------------


def discrepancias(total: Mapping[datetime, Decimal], componentes: Sequence[Mapping[datetime, Decimal]],
                  tolerancia: Decimal = TOLERANCIA_CONTROL) -> list[tuple[datetime, Decimal, Decimal]]:
    """Tramos con todos los componentes en los que su suma se aparta del total."""
    salida = []
    for inicio, valor in sorted(total.items()):
        if all(inicio in c for c in componentes):
            suma = sum((c[inicio] for c in componentes), Decimal(0))
            if abs(valor - suma) > tolerancia:
                salida.append((inicio, valor, suma))
    return salida


def comprobar_controles(conn: psycopg.Connection, desde: date, hasta: date) -> dict[int, list]:
    """Avisa si la suma de los componentes guardados no cuadra con el total de REE."""
    inicio, _ = limites_dia_utc(desde)
    _, fin = limites_dia_utc(hasta)
    todos = indicadores(conn)
    resultado: dict[int, list] = {}

    def valores(ind: Indicador) -> dict[datetime, Decimal]:
        return dict(conn.execute(
            "SELECT inicio, valor FROM serie_esios WHERE indicador = %s AND geo_id = %s"
            " AND inicio >= %s AND inicio < %s",
            (ind.id, ind.geo_id, inicio, fin),
        ).fetchall())

    for control in (i for i in todos if i.uso == "ssaa_control"):
        componentes = [valores(i) for i in todos if i.control == control.id]
        fallos = discrepancias(valores(control), componentes)
        resultado[control.id] = fallos
        if fallos:
            t, total, suma = fallos[0]
            log.warning(
                "ESIOS: la suma de los componentes de %d no cuadra con el total en %d tramo(s)"
                " (p. ej., %s: total %s, suma %s). ¿Ha añadido REE un componente nuevo?",
                control.id, len(fallos), t.isoformat(), total, suma,
            )
    return resultado


# --- Revisiones de la previsión 1871 ------------------------------------------


@dataclass(frozen=True)
class EstadoPrevision:
    # pendiente: aún sin días suficientes; verificada: sin correcciones;
    # corregida: REE ha cambiado algún valor publicado la víspera.
    estado: str
    dias_verificados: int
    corregidos: int


SQL_ESTADO_PREVISION = """
SELECT
    count(DISTINCT (s.inicio AT TIME ZONE 'Europe/Madrid')::date)
        FILTER (WHERE s.descargado_en >= s.inicio + %(recomprobar)s) AS dias_verificados,
    count(*) FILTER (WHERE abs(s.valor - s.primer_valor) > %(tolerancia)s) AS corregidos
FROM esios_indicador i
JOIN serie_esios s ON s.indicador = i.id AND s.geo_id = i.geo_id
WHERE i.uso = 'ssaa_prevision'
  -- Solo valores vistos antes de empezar el tramo: los publicados la víspera.
  AND s.primera_descarga < s.inicio
"""


def estado_prevision(conn: psycopg.Connection) -> EstadoPrevision:
    dias_verificados, corregidos = conn.execute(SQL_ESTADO_PREVISION, {
        "recomprobar": timedelta(days=DIAS_HASTA_RECOMPROBAR),
        "tolerancia": TOLERANCIA_REVISION,
    }).fetchone()  # type: ignore[misc]
    if corregidos:
        estado = "corregida"
    elif dias_verificados >= DIAS_VERIFICACION_PREVISION:
        estado = "verificada"
    else:
        estado = "pendiente"
    return EstadoPrevision(estado, dias_verificados, corregidos)


SQL_REVISIONES = """
SELECT s.indicador, (s.inicio AT TIME ZONE 'Europe/Madrid')::date AS dia,
       count(*) AS tramos, max(abs(s.valor - s.primer_valor)) AS max_cambio,
       min(s.primera_descarga) AS primera_descarga, max(s.modificado_en) AS modificado_en
FROM serie_esios s
WHERE s.modificado_en IS NOT NULL AND abs(s.valor - s.primer_valor) > %s
GROUP BY 1, 2
ORDER BY 1, 2
"""


def revisiones(conn: psycopg.Connection) -> list[tuple]:
    """Días en los que REE ha cambiado valores ya descargados, por indicador."""
    return conn.execute(SQL_REVISIONES, (TOLERANCIA_REVISION,)).fetchall()
