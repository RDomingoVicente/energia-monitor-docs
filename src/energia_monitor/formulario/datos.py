"""Consultas de la página (vistas y funciones de las migraciones 0014 a 0017).

Las altas y cambios van por energia_monitor.bateria_servicio. Aquí solo se lee,
siempre con el instante de cálculo explícito (bateria_estado(%s), ...) para que
los tests sean deterministas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from energia_monitor.tiempo import MADRID

NIVELES = ("error", "aviso", "info")


@dataclass(frozen=True)
class Aviso:
    clave: str
    nivel: str
    titulo: str
    detalle: str
    desde: datetime | None
    accion: str | None


@dataclass(frozen=True)
class Cabecera:
    avisos: list[Aviso]
    ultima_muestra: datetime | None
    cargador_ok: bool
    recalculo: datetime | None
    cargador: str  # etiqueta del chip (host de VIARIS_URL o «cargador»)

    @property
    def nivel_avisos(self) -> str | None:
        """El más grave de los activos: error > aviso > info."""
        for nivel in NIVELES:
            if any(a.nivel == nivel for a in self.avisos):
                return nivel
        return None


@dataclass(frozen=True)
class Dia:
    fecha: date
    salida: datetime | None
    origen: str  # texto de la etiqueta
    clase: str  # horario, sin-salida o excepcion
    consumo_kwh: Decimal | None
    objetivo_pct: Decimal | None


@dataclass(frozen=True)
class Evento:
    instante: datetime
    tipo: str
    valor: Decimal | None
    estimado: bool
    real: Decimal | None = None  # trayecto: parte reconciliada (negativa); igual a valor si lo está entero (0018)
    medido: Decimal | None = None  # cargas: energía medida por el cargador; valor es la estimada a batería (0019)


@dataclass(frozen=True)
class Conexion:
    """Del registro de estados del conector (cargador_estado), no de localtime del cargador (viaris-api.md)."""

    conectado_desde: datetime | None = None  # primer estado no libre tras el último 1-2
    cargando_desde: datetime | None = None  # si carga: inicio de la racha final de 5-6 (las pausas 6→5→6 no la cortan)
    sin_cargar_desde: datetime | None = None  # conectado sin cargar tras cargar en esta conexión


@dataclass(frozen=True)
class LecturaGuardada:
    pct: Decimal
    instante: datetime
    estimado_pct: Decimal | None


@dataclass
class Inicio:
    ahora: datetime
    estado: dict[str, Any]
    dias: list[Dia]
    eventos: list[Evento]
    conexion: Conexion
    potencia_w: Decimal | None
    origen_trayectos: set[str] = field(default_factory=set)
    reserva_pct: Decimal = Decimal(25)
    tope_pct: Decimal = Decimal(100)
    pct_objetivo_salida: Decimal | None = None
    aviso_sin_lecturas: bool = False
    intervalo_incoherente: bool = False
    lectura_guardada: LecturaGuardada | None = None


def version_bd(conn: psycopg.Connection) -> str | None:
    fila = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
    return fila[0] if fila else None


def cabecera(conn: psycopg.Connection, ahora: datetime, cargador: str) -> Cabecera:
    avisos = [Aviso(*f) for f in conn.execute(
        "SELECT clave, nivel, titulo, detalle, desde, accion FROM bateria_avisos(%s)", (ahora,))]
    fila = conn.execute(
        "SELECT (SELECT max(instante) FROM cargador_muestra WHERE instante <= %s),"
        " bateria_param('hueco_muestreo_aviso_s', %s),"
        " (SELECT calculado_en FROM bateria_recalculo)", (ahora, ahora)).fetchone()
    assert fila is not None
    ultima, umbral, recalculo = fila
    ok = ultima is not None and (ahora - ultima).total_seconds() <= float(umbral)
    return Cabecera(avisos, ultima, ok, recalculo, cargador)


def estado(conn: psycopg.Connection, ahora: datetime) -> dict[str, Any]:
    cur = conn.execute("SELECT * FROM bateria_estado(%s)", (ahora,))
    columnas = [c.name for c in cur.description or []]
    fila = cur.fetchone()
    assert fila is not None
    return dict(zip(columnas, fila))


# Estados: 1-2 libre, 5-6 cargando (viaris-api.md). Conectado: primer estado no libre tras el último libre.
# Cargando: si el último es 5-6, el primero de su racha final de 5-6. Sin cargar: si el último no es libre ni 5-6
# y hubo carga en esta conexión, el primer estado tras el último 5-6.
_SQL_CONEXION = """
WITH e AS (
  SELECT detectado_en AS t, estado FROM cargador_estado WHERE detectado_en <= %(a)s
), ult AS (
  SELECT estado FROM e ORDER BY t DESC LIMIT 1
), libre AS (
  SELECT max(t) AS t FROM e WHERE estado IN (1, 2)
), con AS (
  SELECT min(e.t) AS t FROM e, libre WHERE e.estado NOT IN (1, 2) AND (libre.t IS NULL OR e.t > libre.t)
), carga AS (
  SELECT max(e.t) AS t FROM e, con WHERE e.estado IN (5, 6) AND e.t >= con.t
), no_carga AS (
  SELECT max(e.t) AS t FROM e WHERE e.estado NOT IN (5, 6)
)
SELECT con.t,
       CASE WHEN ult.estado IN (5, 6) THEN
            (SELECT min(e.t) FROM e WHERE e.estado IN (5, 6) AND e.t >= con.t
               AND (no_carga.t IS NULL OR e.t > no_carga.t)) END,
       CASE WHEN ult.estado NOT IN (1, 2, 5, 6) AND carga.t IS NOT NULL THEN
            (SELECT min(e.t) FROM e WHERE e.t > carga.t) END
FROM (SELECT 1) uno
LEFT JOIN ult ON true LEFT JOIN con ON true LEFT JOIN carga ON true LEFT JOIN no_carga ON true
"""


def inicio(conn: psycopg.Connection, ahora: datetime) -> Inicio:
    est = estado(conn, ahora)

    salidas = {f[0]: f for f in conn.execute(
        "SELECT s.fecha, s.salida, s.tipo, s.consumo_modo, s.pct_objetivo, s.consumo_previsto_kwh,"
        " s.objetivo_kwh / bateria_param('capacidad_util_kwh', s.salida) * 100, x.km, x.pct"
        " FROM bateria_salidas(%s, 7) s"
        " LEFT JOIN bateria_excepcion x ON x.fecha = s.fecha AND x.anulada_en IS NULL", (ahora,))}
    hoy = ahora.astimezone(MADRID).date()
    inicio_dias = hoy if hoy in salidas else hoy + timedelta(days=1)
    fechas = [inicio_dias + timedelta(days=i) for i in range(7)]
    sin_salida = {f[0] for f in conn.execute(
        "SELECT fecha FROM bateria_excepcion WHERE anulada_en IS NULL AND tipo = 'sin_salida'"
        " AND fecha BETWEEN %s AND %s", (fechas[0], fechas[-1]))}
    dias = []
    for d in fechas:
        s = salidas.get(d)
        if s is None:
            texto = "sin salida" if d in sin_salida else "horario · sin salida"
            dias.append(Dia(d, None, texto, "sin-salida" if d in sin_salida else "horario", None, None))
            continue
        _, salida, tipo, modo, pct_obj, previsto, objetivo_pct, km, pct_consumo = s
        if tipo == "horario":
            texto, clase = "horario", "horario"
        elif modo == "indicado" and km is not None:
            texto, clase = f"excepción · {km.normalize():f} km", "excepcion"
        elif modo == "indicado":
            texto, clase = f"excepción · {pct_consumo.normalize():f} %", "excepcion"
        elif pct_obj is not None:
            texto, clase = f"excepción · mínimo {pct_obj.normalize():f} %", "excepcion"
        else:
            texto, clase = "excepción", "excepcion"
        dias.append(Dia(d, salida, texto, clase, previsto, objetivo_pct))

    eventos = [Evento(*f) for f in conn.execute(
        "SELECT instante, tipo, valor, estimado, real_kwh, medido_kwh FROM v_bateria_evento_reciente"
        " WHERE instante <= %s"
        " ORDER BY instante DESC, CASE tipo WHEN 'conexion' THEN 0 ELSE 1 END LIMIT 5", (ahora,))]

    conexion = Conexion(*conn.execute(_SQL_CONEXION, {"a": ahora}).fetchone())  # type: ignore[misc]
    muestra = conn.execute(
        "SELECT evse_w FROM cargador_muestra WHERE instante <= %s ORDER BY instante DESC LIMIT 1", (ahora,)).fetchone()

    origenes: set[str] = set()
    if est["ultima_lectura_ts"] is not None:
        origenes = {f[0] for f in conn.execute(
            "SELECT DISTINCT origen_consumo FROM bateria_trayecto WHERE NOT corto AND llegada IS NOT NULL"
            " AND llegada > %s AND llegada <= %s", (est["ultima_lectura_ts"], ahora))}
    reserva, tope = conn.execute("SELECT bateria_param('reserva_pct', %s), bateria_param('tope_pct', %s)",
                                 (ahora, ahora)).fetchone()  # type: ignore[misc]
    primera = next((d for d in dias if d.salida is not None and d.salida == est["proxima_salida"]), None)
    pct_obj_salida = None
    if primera is not None:
        pct_obj_salida = salidas[primera.fecha][4]
    avisos = {f[0].split(":")[0] for f in conn.execute("SELECT clave FROM bateria_avisos(%s)", (ahora,))}
    return Inicio(ahora, est, dias, eventos, conexion, muestra[0] if muestra else None,
                  origenes, reserva, tope, pct_obj_salida, "sin_lecturas" in avisos,
                  "intervalo_incoherente" in avisos)


def lectura_guardada(conn: psycopg.Connection, id_: int) -> LecturaGuardada | None:
    """La lectura recién guardada (?ok=lectura&id=N), con lo que estimaba el modelo justo antes."""
    fila = conn.execute("SELECT pct, instante, estimado_pct FROM v_bateria_lectura_historial"
                        " WHERE id = %s AND anulada_en IS NULL", (id_,)).fetchone()
    return LecturaGuardada(*fila) if fila else None


def salud(conn: psycopg.Connection, ahora: datetime) -> dict[str, Any]:
    fila = conn.execute(
        "SELECT (SELECT max(version) FROM schema_migrations),"
        " (SELECT max(instante) FROM cargador_muestra WHERE instante <= %s),"
        " (SELECT calculado_en FROM bateria_recalculo)", (ahora,)).fetchone()
    assert fila is not None
    version, ultima, recalculo = fila
    return {
        "migracion": version,
        "ultima_muestra_s": None if ultima is None else int((ahora - ultima).total_seconds()),
        "recalculo": None if recalculo is None else recalculo.isoformat(),
    }


# --- Excepciones (7.2) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExcepcionProxima:
    id: int
    fecha: date
    tipo: str
    hora_salida: Any
    consumo_modo: str | None
    km: Decimal | None
    pct: Decimal | None
    pct_objetivo: Decimal | None
    nota: str | None
    previsto_kwh: Decimal | None
    objetivo_pct: Decimal | None
    minimo: bool  # el objetivo lo marca pct_objetivo
    recortado: bool  # el objetivo supera el tope


@dataclass(frozen=True)
class ExcepcionPasada:
    id: int
    fecha: date
    tipo: str
    previsto_kwh: Decimal | None
    real_kwh: Decimal | None
    estado: str  # cumplida, no_cumplida o dada_de_baja


@dataclass(frozen=True)
class Plan:
    consumo_kwh: Decimal
    origen: str  # historia o inicial
    reserva_pct: Decimal
    tope_pct: Decimal
    capacidad_kwh: Decimal


@dataclass
class Excepciones:
    ahora: datetime
    proximas: list[ExcepcionProxima]
    pasadas: list[ExcepcionPasada]
    plan: Plan


SQL_PROXIMAS = """
WITH plan AS (SELECT consumo_kwh FROM bateria_consumo_plan(%(ahora)s)),
x AS (
    SELECT x.*, (x.fecha + coalesce(x.hora_salida, time '00:00')) AT TIME ZONE 'Europe/Madrid' AS instante
    FROM bateria_excepcion x
    WHERE x.anulada_en IS NULL AND x.fecha >= (%(ahora)s AT TIME ZONE 'Europe/Madrid')::date
), c AS (
    SELECT x.*, bateria_param('capacidad_util_kwh', x.instante) AS cap,
           CASE WHEN x.tipo = 'sin_salida' THEN NULL
                WHEN x.consumo_modo = 'indicado' THEN
                    coalesce(x.pct / 100 * bateria_param('capacidad_util_kwh', x.instante),
                             x.km * bateria_param('consumo_kwh_100km', x.instante) / 100)
                ELSE plan.consumo_kwh END AS previsto
    FROM x CROSS JOIN plan
), o AS (
    SELECT c.*, bateria_param('reserva_pct', c.instante) / 100 * c.cap + c.previsto AS base,
           c.pct_objetivo / 100 * c.cap AS minimo_kwh, bateria_param('tope_pct', c.instante) / 100 * c.cap AS tope
    FROM c
)
SELECT id, fecha, tipo, hora_salida, consumo_modo, km, pct, pct_objetivo, nota, previsto,
       CASE WHEN tipo = 'con_salida' THEN least(greatest(base, minimo_kwh), tope) / cap * 100 END,
       coalesce(minimo_kwh > base, false), coalesce(greatest(base, minimo_kwh) > tope, false)
FROM o
ORDER BY fecha
"""


def plan(conn: psycopg.Connection, ahora: datetime) -> Plan:
    fila = conn.execute(
        "SELECT p.consumo_kwh, p.origen, bateria_param('reserva_pct', %(a)s), bateria_param('tope_pct', %(a)s),"
        " bateria_param('capacidad_util_kwh', %(a)s) FROM bateria_consumo_plan(%(a)s) p", {"a": ahora}).fetchone()
    assert fila is not None
    return Plan(*fila)


def excepciones(conn: psycopg.Connection, ahora: datetime) -> Excepciones:
    proximas = [ExcepcionProxima(*f) for f in conn.execute(SQL_PROXIMAS, {"ahora": ahora})]
    pasadas = [ExcepcionPasada(*f) for f in conn.execute(
        "SELECT id, fecha, tipo, previsto_kwh, real_kwh, estado FROM v_bateria_excepcion_pasada ORDER BY fecha DESC")]
    return Excepciones(ahora, proximas, pasadas, plan(conn, ahora))


def excepcion(conn: psycopg.Connection, id_: int, activa: bool | None = None) -> dict[str, Any] | None:
    cur = conn.execute("SELECT id, tipo, fecha, hora_salida, consumo_modo, km, pct, pct_objetivo, nota, anulada_en"
                       " FROM bateria_excepcion WHERE id = %s", (id_,))
    fila = cur.fetchone()
    if fila is None:
        return None
    x = dict(zip([c.name for c in cur.description or []], fila))
    if activa is not None and (x["anulada_en"] is None) != activa:
        return None
    return x


# --- Historial (7.4) ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FilaHistorial:
    tipo: str  # lectura, carga_externa o carga_completa
    id: int | None
    instante: datetime
    pct: Decimal | None  # leído (lectura y carga completa)
    estimado_pct: Decimal | None
    diferencia_pts: Decimal | None  # solo en lecturas comparables; carga externa: pts sumados
    comparable: bool
    anulada: bool
    nota: str | None
    motivo: str | None
    corrige_instante: datetime | None
    kwh: Decimal | None  # carga externa
    coste_eur: Decimal | None


@dataclass
class Historial:
    ahora: datetime
    dias: int | None  # None: todo
    con_anuladas: bool
    filas: list[FilaHistorial]
    capacidad_kwh: Decimal
    aviso_dias: Decimal
    ultima_lectura: datetime | None
    anuladas_periodo: int


SQL_HISTORIAL = """
SELECT 'lectura', l.id, l.instante, l.pct, l.estimado_pct,
       CASE WHEN l.comparable THEN l.diferencia_pts END, l.comparable, l.anulada_en IS NOT NULL, l.nota, l.motivo,
       c.instante, NULL::numeric, NULL::numeric
FROM v_bateria_lectura_historial l
LEFT JOIN bateria_lectura c ON c.id = l.corrige
WHERE l.instante <= %(ahora)s AND (%(desde)s::timestamptz IS NULL OR l.instante >= %(desde)s)
UNION ALL
SELECT 'carga_externa', x.id, x.instante, NULL, NULL,
       x.kwh * bateria_param('factor_bateria', x.instante) / bateria_param('capacidad_util_kwh', x.instante) * 100,
       false, x.anulada_en IS NOT NULL, x.nota, x.motivo, NULL, x.kwh, x.coste_eur
FROM bateria_carga_externa x
WHERE x.instante <= %(ahora)s AND (%(desde)s::timestamptz IS NULL OR x.instante >= %(desde)s)
UNION ALL
SELECT 'carga_completa', NULL, s.fin, bateria_param('tope_pct', s.fin), NULL, NULL, false, false, NULL, NULL,
       NULL, NULL, NULL
FROM bateria_sesion s
WHERE s.cierre = 'carga_completa' AND s.fin <= %(ahora)s AND (%(desde)s::timestamptz IS NULL OR s.fin >= %(desde)s)
ORDER BY 3 DESC
"""


def historial(conn: psycopg.Connection, ahora: datetime, dias: int | None, con_anuladas: bool) -> Historial:
    desde = None if dias is None else ahora - timedelta(days=dias)
    filas = [FilaHistorial(*f) for f in conn.execute(SQL_HISTORIAL, {"ahora": ahora, "desde": desde})]
    anuladas = sum(1 for x in filas if x.anulada and x.tipo == "lectura")
    if not con_anuladas:
        filas = [x for x in filas if not x.anulada]
    fila = conn.execute(
        "SELECT bateria_param('capacidad_util_kwh', %(a)s), bateria_param('aviso_sin_lectura_dias', %(a)s),"
        " (SELECT max(instante) FROM bateria_lectura WHERE anulada_en IS NULL AND instante <= %(a)s)",
        {"a": ahora}).fetchone()
    assert fila is not None
    return Historial(ahora, dias, con_anuladas, filas, fila[0], fila[1], fila[2], anuladas)


def lectura(conn: psycopg.Connection, id_: int) -> dict[str, Any] | None:
    cur = conn.execute("SELECT id, instante, pct, nota, anulada_en, motivo, corrige FROM bateria_lectura WHERE id = %s",
                       (id_,))
    fila = cur.fetchone()
    return None if fila is None else dict(zip([c.name for c in cur.description or []], fila))


# --- Carga externa (7.8) -----------------------------------------------------------------------


@dataclass(frozen=True)
class CargaExternaFila:
    id: int
    instante: datetime
    kwh: Decimal | None
    coste_eur: Decimal | None
    nota: str | None
    pts: Decimal | None
    eur_kwh: Decimal | None


@dataclass
class CargasExternas:
    ahora: datetime
    filas: list[CargaExternaFila]
    factor: Decimal
    capacidad_kwh: Decimal


def cargas_externas(conn: psycopg.Connection, ahora: datetime) -> CargasExternas:
    filas = [CargaExternaFila(*f) for f in conn.execute(
        "SELECT id, instante, kwh, coste_eur, nota, pts, eur_kwh FROM v_coste_carga_externa"
        " WHERE instante <= %(a)s AND instante > %(a)s - interval '90 days' ORDER BY instante DESC", {"a": ahora})]
    fila = conn.execute("SELECT bateria_param('factor_bateria', %(a)s), bateria_param('capacidad_util_kwh', %(a)s)",
                        {"a": ahora}).fetchone()
    assert fila is not None
    return CargasExternas(ahora, filas, fila[0], fila[1])


def carga_externa(conn: psycopg.Connection, id_: int) -> dict[str, Any] | None:
    cur = conn.execute("SELECT id, instante, kwh, coste_eur, nota, anulada_en, motivo, corrige"
                       " FROM bateria_carga_externa WHERE id = %s", (id_,))
    fila = cur.fetchone()
    return None if fila is None else dict(zip([c.name for c in cur.description or []], fila))


# --- Horario (7.3) ---------------------------------------------------------------------------------


@dataclass
class HorarioDatos:
    dias: dict[int, Any]  # 1 = lunes: hora o None
    modificado: datetime | None
    plan: Plan


def horario(conn: psycopg.Connection, ahora: datetime) -> HorarioDatos:
    filas = conn.execute("SELECT dia_iso, hora_salida, actualizado_en FROM bateria_horario").fetchall()
    dias: dict[int, Any] = {d: None for d in range(1, 8)}
    for dia, hora, _ in filas:
        dias[dia] = hora
    modificado = max((f[2] for f in filas), default=None)
    return HorarioDatos(dias, modificado, plan(conn, ahora))


# --- Parámetros (7.5) y Ayuda (7.6) ---------------------------------------------------------------

ORDEN_PARAMETROS = ("capacidad_util_kwh", "reserva_pct", "tope_pct", "factor_bateria", "percentil_planificacion",
                    "ventana_consumo_dias", "desconexion_min_s", "consumo_inicial_trayecto_kwh", "consumo_kwh_100km",
                    "energia_min_calibracion_kwh", "observaciones_calibracion", "detectar_carga_completa",
                    "hueco_muestreo_aviso_s", "aviso_sin_lectura_dias", "umbral_carga_externa_kwh")


@dataclass(frozen=True)
class Parametro:
    clave: str
    nombre: str
    unidad: str
    minimo: Decimal
    maximo: Decimal
    descripcion: str
    efecto: str
    valor: Decimal
    origen: str
    desde: date
    id: int


@dataclass(frozen=True)
class Cambio:
    id: int
    clave: str
    desde: date
    valor: Decimal
    anterior: Decimal | None
    nota: str


@dataclass(frozen=True)
class Calibracion:
    sugerido: Decimal | None
    vigente: Decimal
    observaciones: int
    minimo: Decimal | None
    maximo: Decimal | None


@dataclass
class Parametros:
    ahora: datetime
    vigentes: list[Parametro]
    cambios: list[Cambio]
    calibracion: Calibracion

    def de(self, clave: str) -> Parametro:
        return next(p for p in self.vigentes if p.clave == clave)


def parametros(conn: psycopg.Connection, ahora: datetime) -> Parametros:
    hoy = ahora.astimezone(MADRID).date()
    vigentes = [Parametro(*f) for f in conn.execute(
        "SELECT k.clave, k.nombre, k.unidad, k.minimo, k.maximo, k.descripcion, k.efecto, p.valor, p.origen,"
        " lower(p.vigencia), p.id FROM bateria_parametro_clave k JOIN bateria_parametro p"
        " ON p.clave = k.clave AND p.vigencia @> %s::date", (hoy,))]
    orden = {c: i for i, c in enumerate(ORDEN_PARAMETROS)}
    vigentes.sort(key=lambda p: orden.get(p.clave, len(orden)))
    cambios = [Cambio(*f) for f in conn.execute(
        "SELECT p.id, p.clave, lower(p.vigencia), p.valor, a.valor, coalesce(p.nota, p.fuente)"
        " FROM bateria_parametro p LEFT JOIN LATERAL (SELECT q.valor FROM bateria_parametro q"
        " WHERE q.clave = p.clave AND lower(q.vigencia) < lower(p.vigencia) ORDER BY lower(q.vigencia) DESC LIMIT 1) a"
        " ON true ORDER BY p.id DESC LIMIT 5")]
    fila = conn.execute(
        "SELECT s.factor, s.observaciones, bateria_param('factor_bateria', %(a)s),"
        " (SELECT min(o.factor_observado) FROM (SELECT c.factor_observado FROM v_bateria_calibracion c"
        "   WHERE c.hasta <= %(a)s ORDER BY c.hasta DESC LIMIT s.observaciones) o),"
        " (SELECT max(o.factor_observado) FROM (SELECT c.factor_observado FROM v_bateria_calibracion c"
        "   WHERE c.hasta <= %(a)s ORDER BY c.hasta DESC LIMIT s.observaciones) o)"
        " FROM bateria_factor_sugerido(%(a)s) s", {"a": ahora}).fetchone()
    assert fila is not None
    return Parametros(ahora, vigentes, cambios, Calibracion(fila[0], fila[2], fila[1], fila[3], fila[4]))


def parametro_registrado(conn: psycopg.Connection, id_: int) -> tuple[str, Decimal, date, str] | None:
    fila = conn.execute("SELECT p.clave, p.valor, lower(p.vigencia), k.unidad FROM bateria_parametro p"
                        " JOIN bateria_parametro_clave k USING (clave) WHERE p.id = %s", (id_,)).fetchone()
    return None if fila is None else (fila[0], fila[1], fila[2], fila[3])


# --- Avisos (7.7) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AvisoActivo:
    clave: str
    nivel: str
    titulo: str
    detalle: str
    desde: datetime | None
    regla: str
    accion: str | None


@dataclass(frozen=True)
class AvisoResuelto:
    titulo: str
    detalle: str | None
    inicio: datetime
    fin: datetime


@dataclass
class Avisos:
    ahora: datetime
    activos: list[AvisoActivo]
    resueltos: list[AvisoResuelto]


def avisos(conn: psycopg.Connection, ahora: datetime) -> Avisos:
    activos = [AvisoActivo(*f) for f in conn.execute(
        "SELECT a.clave, a.nivel, a.titulo, a.detalle, coalesce(a.desde, h.inicio), a.regla, a.accion"
        " FROM bateria_avisos(%s) a LEFT JOIN aviso_historial h ON h.clave = a.clave AND h.fin IS NULL", (ahora,))]
    orden = {n: i for i, n in enumerate(NIVELES)}
    activos.sort(key=lambda a: orden.get(a.nivel, 9))
    resueltos = [AvisoResuelto(*f) for f in conn.execute(
        "SELECT titulo, detalle, inicio, fin FROM aviso_historial WHERE fin IS NOT NULL AND fin <= %(a)s"
        " AND fin > %(a)s - interval '7 days' ORDER BY fin DESC", {"a": ahora})]
    return Avisos(ahora, activos, resueltos)
