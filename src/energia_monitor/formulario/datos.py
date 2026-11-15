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
    coche_desde: datetime | None
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


def inicio(conn: psycopg.Connection, ahora: datetime) -> Inicio:
    cur = conn.execute("SELECT * FROM bateria_estado(%s)", (ahora,))
    columnas = [c.name for c in cur.description or []]
    fila = cur.fetchone()
    assert fila is not None
    estado = dict(zip(columnas, fila))

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
        "SELECT instante, tipo, valor, estimado FROM v_bateria_evento_reciente WHERE instante <= %s"
        " ORDER BY instante DESC, CASE tipo WHEN 'conexion' THEN 0 ELSE 1 END LIMIT 5", (ahora,))]

    coche = conn.execute(
        "SELECT detectado_en FROM cargador_estado WHERE detectado_en <= %s ORDER BY detectado_en DESC LIMIT 1",
        (ahora,)).fetchone()
    muestra = conn.execute(
        "SELECT evse_w FROM cargador_muestra WHERE instante <= %s ORDER BY instante DESC LIMIT 1", (ahora,)).fetchone()

    origenes: set[str] = set()
    if estado["ultima_lectura_ts"] is not None:
        origenes = {f[0] for f in conn.execute(
            "SELECT DISTINCT origen_consumo FROM bateria_trayecto WHERE NOT corto AND llegada IS NOT NULL"
            " AND llegada > %s AND llegada <= %s", (estado["ultima_lectura_ts"], ahora))}
    reserva, tope = conn.execute("SELECT bateria_param('reserva_pct', %s), bateria_param('tope_pct', %s)",
                                 (ahora, ahora)).fetchone()  # type: ignore[misc]
    primera = next((d for d in dias if d.salida is not None and d.salida == estado["proxima_salida"]), None)
    pct_obj_salida = None
    if primera is not None:
        pct_obj_salida = salidas[primera.fecha][4]
    avisos = {f[0].split(":")[0] for f in conn.execute("SELECT clave FROM bateria_avisos(%s)", (ahora,))}
    return Inicio(ahora, estado, dias, eventos, coche[0] if coche else None, muestra[0] if muestra else None,
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
