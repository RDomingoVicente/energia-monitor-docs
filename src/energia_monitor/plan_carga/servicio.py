"""Planificador contra la base de datos: lee la entrada, guarda el plan y decide cuándo recalcular.

Corre en el servicio `muestreo` (fase2.md, «Decisiones del paso 3a»): cada 15
minutos, en cada cambio de estado del conector o sesión nueva y cuando hay una
solicitud (`solicitar_plan`, del formulario, el CLI y el colector). En el paso
3a solo hay modo sombra: no lee el programador ni escribe en el cargador, así
que no hay tareas ajenas que respetar ni choques que anotar.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor.plan_carga import disponibilidad, precios
from energia_monitor.plan_carga.ahorro import Ahorro, ahorro_conexion, cuarto_de
from energia_monitor.plan_carga.algoritmo import planificar
from energia_monitor.plan_carga.modelo import (
    CUARTO,
    Cuarto,
    CuartoPlan,
    Entrada,
    FranjaEnCurso,
    Ocupado,
    Parametros,
    Plan,
    Salida,
)
from energia_monitor.tiempo import MADRID

log = logging.getLogger(__name__)

PERIODO = timedelta(minutes=15)


@dataclass(frozen=True)
class Contexto:
    """Lo que acompaña a la entrada para guardarla y mostrarla."""

    modo: str
    dias_historial: int
    capacidad_kwh: Decimal
    factor_contador: Decimal | None
    sin_nivel: bool


def parametros_vigentes(conn: psycopg.Connection, instante: datetime) -> dict[str, Decimal]:
    dia = instante.astimezone(MADRID).date()
    return dict(conn.execute("SELECT clave, valor FROM bateria_parametro WHERE vigencia @> %s::date", (dia,)))


def _conectado(conn: psycopg.Connection, instante: datetime) -> bool:
    fila = conn.execute("SELECT estado FROM cargador_estado WHERE detectado_en <= %s ORDER BY detectado_en DESC"
                        " LIMIT 1", (instante,)).fetchone()
    return fila is not None and fila[0] not in (1, 2)


def _en_curso(conn: psycopg.Connection, instante: datetime) -> FranjaEnCurso | None:
    """Franja del último cálculo anterior que contiene el instante."""
    fila = conn.execute(
        "SELECT f.calculo_id, f.inicio, f.fin FROM plan_franja f"
        " WHERE f.calculo_id = (SELECT id FROM plan_calculo WHERE ahora <= %(t)s ORDER BY ahora DESC, id DESC LIMIT 1)"
        " AND f.inicio <= %(t)s AND f.fin > %(t)s", {"t": instante}).fetchone()
    if fila is None:
        return None
    cuartos = tuple(CuartoPlan(*c) for c in conn.execute(
        "SELECT inicio, nivel, salida, kwh_cargador, precio_eur_kwh, provisional, ventana, sobre_umbral"
        " FROM plan_cuarto WHERE calculo_id = %s AND inicio >= %s AND inicio < %s ORDER BY inicio", fila))
    return FranjaEnCurso(fila[1], fila[2], cuartos) if cuartos else None


def cargar_entrada(conn: psycopg.Connection, ahora: datetime, *, con_en_curso: bool = True,
                   ocupados: Sequence[Ocupado] = (), respetar_ajenas: bool = False) -> tuple[Entrada, Contexto]:
    p = parametros_vigentes(conn, ahora)
    estado = conn.execute("SELECT capacidad_kwh, nivel_plan_kwh FROM bateria_estado(%s)", (ahora,)).fetchone()
    assert estado is not None
    capacidad, nivel = estado
    horizonte_h = int(p["horizonte_plan_h"])
    limite = ahora + timedelta(hours=horizonte_h)
    salidas = tuple(Salida(s, t, o, c) for s, t, o, c in conn.execute(
        "SELECT salida, tipo, objetivo_kwh, consumo_previsto_kwh FROM bateria_salidas(%s, %s)"
        " WHERE salida <= %s ORDER BY salida", (ahora, math.ceil(horizonte_h / 24) + 1, limite)))
    horizonte_fin = cuarto_de(salidas[-1].instante if salidas else limite)

    ventana_precio = max(int(p["ventana_precio_esperado_dias"]), int(p["ventana_umbral_dias"]) + 1)
    filas = conn.execute("SELECT inicio, precio_final_eur_kwh FROM v_plan_precio WHERE inicio >= %s AND inicio < %s",
                         (ahora - timedelta(days=ventana_precio), horizonte_fin + CUARTO)).fetchall()
    historico = [(i, pr) for i, pr in filas if i < ahora]
    publicados = dict(filas)
    perfil_precio = precios.perfil_esperado(historico, ahora, int(p["ventana_precio_esperado_dias"]))
    umbral = precios.umbral(historico, ahora, int(p["ventana_umbral_dias"]), p["umbral_oportunista_pct"])

    observados = [(i, c) for i, c, _ in conn.execute(
        "SELECT * FROM plan_cuartos_observados(%s, %s)",
        (ahora - timedelta(days=int(p["ventana_disponibilidad_dias"])), ahora))]
    perfil_disp = disponibilidad.perfil(observados)
    dias = disponibilidad.dias_historial(observados)
    potencia = {h: kw for h, kw, _, _ in conn.execute("SELECT * FROM plan_potencia_hora(%s)", (ahora,))}

    cuartos = []
    q = cuarto_de(ahora)
    while q < horizonte_fin:
        precio, provisional = precios.precio_cuarto(q, ahora, publicados, perfil_precio,
                                                    int(p["hora_publicacion_precios"]))
        cuartos.append(Cuarto(q, precio, provisional, perfil_disp.get(disponibilidad.clave(q)),
                              potencia[q.astimezone(MADRID).hour]))
        q += CUARTO

    parametros = Parametros(
        capacidad_kwh=capacidad, tope_kwh=p["tope_pct"] / 100 * capacidad, factor_bateria=p["factor_bateria"],
        margen_carga_pct=p["margen_carga_pct"], margen_salida_min=int(p["margen_salida_min"]),
        carga_oportunista=p["carga_oportunista"] == 1,
        nivel_oportunista_kwh=p["nivel_oportunista_pct"] / 100 * capacidad,
        umbral_disponibilidad_pct=p["umbral_disponibilidad_pct"], franja_min_min=int(p["franja_min_min"]),
        max_tareas=int(p["max_tareas_sistema"]))
    modo = conn.execute("SELECT planificador_modo_en(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    factor = conn.execute("SELECT valor FROM cargador_parametro WHERE clave = 'factor_contador'"
                          " AND vigencia @> %s::date", (ahora.astimezone(MADRID).date(),)).fetchone()
    entrada = Entrada(
        ahora=ahora, parametros=parametros, nivel_kwh=nivel if nivel is not None else Decimal(0),
        conectado=_conectado(conn, ahora), prudente=dias < int(p["historial_min_disponibilidad_dias"]),
        umbral=umbral, cuartos=tuple(cuartos), salidas=salidas if nivel is not None else (),
        horizonte_fin=horizonte_fin, ocupados=tuple(ocupados),
        en_curso=_en_curso(conn, ahora) if con_en_curso else None, respetar_ajenas=respetar_ajenas)
    return entrada, Contexto(modo, dias, capacidad, factor[0] if factor else None, nivel is None)


def calcular(conn: psycopg.Connection, ahora: datetime, **kw: object) -> tuple[Entrada, Contexto, Plan, Decimal]:
    """Entrada, plan y milisegundos (lectura y cálculo)."""
    inicio = time.monotonic()
    entrada, ctx = cargar_entrada(conn, ahora, **kw)  # type: ignore[arg-type]
    plan = planificar(entrada)
    if ctx.sin_nivel:
        plan = Plan(plan.cuartos, plan.franjas, plan.salidas,
                    ("Sin nivel de batería (ninguna lectura): no se planifica para las salidas.", *plan.avisos))
    return entrada, ctx, plan, Decimal(round((time.monotonic() - inicio) * 1000, 1))


def guardar(conn: psycopg.Connection, entrada: Entrada, ctx: Contexto, plan: Plan, motivo: str,
            duracion_ms: Decimal) -> int:
    with conn.transaction():
        fila = conn.execute(
            "INSERT INTO plan_calculo (ahora, motivo, modo, prudente, conectado, nivel_inicial_kwh, capacidad_kwh,"
            " umbral_eur_kwh, horizonte_fin, salidas_no_cubiertas, avisos, duracion_ms)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (entrada.ahora, motivo, ctx.modo, entrada.prudente, entrada.conectado,
             None if ctx.sin_nivel else entrada.nivel_kwh, ctx.capacidad_kwh, entrada.umbral, entrada.horizonte_fin,
             sum(1 for s in plan.salidas if not s.cubierta), list(plan.avisos), duracion_ms)).fetchone()
        assert fila is not None
        ident = fila[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO plan_cuarto (calculo_id, inicio, nivel, salida, kwh_cargador, precio_eur_kwh,"
                " provisional, ventana, sobre_umbral) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(ident, c.inicio, c.nivel, c.salida, c.kwh, c.precio, c.provisional, c.ventana, c.sobre_umbral)
                 for c in plan.cuartos])
            cur.executemany(
                "INSERT INTO plan_franja (calculo_id, n, inicio, fin, motivo, salida, kwh_cargador, provisional,"
                " tareas, en_curso, choca_con) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(ident, n, f.inicio, f.fin, f.motivo, f.salida, f.kwh, f.provisional, f.tareas, f.en_curso,
                  list(f.choca_con)) for n, f in enumerate(plan.franjas, 1)])
            cur.executemany(
                "INSERT INTO plan_salida (calculo_id, salida, tipo, objetivo_kwh, consumo_previsto_kwh,"
                " necesaria_kwh, nivel_salida_kwh, cubierta, salto_umbral) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(ident, s.instante, s.tipo, s.objetivo_kwh, s.consumo_kwh, s.necesaria_kwh, s.nivel_salida_kwh,
                  s.cubierta, s.salto_umbral) for s in plan.salidas])
        conn.execute(
            "INSERT INTO planificador_ciclo (id, ultimo_ok_en, ultimo_calculo_id) VALUES (true, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET ultimo_ok_en = EXCLUDED.ultimo_ok_en,"
            " ultimo_calculo_id = EXCLUDED.ultimo_calculo_id", (datetime.now(UTC), ident))
    return ident


def registrar_error(conn: psycopg.Connection, error: BaseException) -> None:
    conn.execute(
        "INSERT INTO planificador_ciclo (id, ultimo_error_en, ultimo_error) VALUES (true, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET ultimo_error_en = EXCLUDED.ultimo_error_en,"
        " ultimo_error = EXCLUDED.ultimo_error", (datetime.now(UTC), f"{type(error).__name__}: {error}"[:500]))


def solicitar(conn: psycopg.Connection, motivo: str) -> None:
    """Pide un recálculo al ciclo del muestreo (lo atiende en menos de un minuto)."""
    conn.execute("SELECT solicitar_plan(%s)", (motivo,))


def solicitar_sin_fallar(conn: psycopg.Connection, motivo: str) -> None:
    """Como solicitar, sin propagar errores: pedir un plan no debe tumbar la acción que lo pide."""
    try:
        solicitar(conn, motivo)
    except psycopg.Error:
        log.exception("no se pudo pedir el recálculo del plan (%s)", motivo)


# --- Ciclo dentro del muestreo ------------------------------------------------------------------


class Ciclo:
    """Decide y hace los recálculos del plan. Nunca propaga un fallo al muestreo."""

    def __init__(self) -> None:
        self.ultimo: datetime | None = None  # ahora del último cálculo de este proceso
        self.calculado: datetime | None = None  # hora real de ese cálculo (para comparar con las solicitudes)
        self.pendiente: str | None = None  # evento cuyo cálculo falló: se reintenta en el ciclo siguiente
        self._fallos = 0

    def toca(self, conn: psycopg.Connection, ahora: datetime, evento: str | None) -> str | None:
        if evento or self.pendiente:
            return evento or self.pendiente
        if self.ultimo is None:
            fila = conn.execute("SELECT max(ahora) FROM plan_calculo").fetchone()
            self.ultimo = fila[0] if fila else None
            if self.ultimo is None:
                return "arranque"
        if ahora - self.ultimo >= PERIODO:
            return "periódico"
        fila = conn.execute("SELECT solicitado_en, motivo FROM planificador_solicitud").fetchone()
        if fila is not None and fila[0] > (self.calculado or self.ultimo):
            return f"solicitud: {fila[1]}"
        return None

    def ejecutar(self, conn: psycopg.Connection, ahora: datetime, evento: str | None = None) -> int | None:
        motivo = evento
        try:
            motivo = self.toca(conn, ahora, evento)
            if motivo is None:
                return None
            entrada, ctx, plan, ms = calcular(conn, ahora)
            ident = guardar(conn, entrada, ctx, plan, motivo, ms)
            self.ultimo, self.calculado = ahora, datetime.now(UTC)
            self.pendiente = None
            if self._fallos:
                log.info("planificador recuperado tras %d fallo(s)", self._fallos)
            self._fallos = 0
            log.debug("plan %d (%s): %d franja(s), %s ms", ident, motivo, len(plan.franjas), ms)
            return ident
        except Exception as e:  # noqa: BLE001 - el muestreo sigue pase lo que pase
            self._fallos += 1
            self.pendiente = motivo or "reintento"
            if self._fallos == 1 or self._fallos % 15 == 0:
                log.exception("fallo del planificador (%d seguidos)", self._fallos)
            try:
                registrar_error(conn, e)
            except psycopg.Error:
                pass
            # Reintenta en el ciclo siguiente del muestreo, sin esperar al periodo.
            return None


# --- Simulación sobre un día pasado -------------------------------------------------------------


@dataclass(frozen=True)
class Simulacion:
    entrada: Entrada
    contexto: Contexto
    plan: Plan
    milisegundos: Decimal
    conexion: tuple[datetime, datetime] | None
    ahorro: Ahorro | None


def instante_de_simulacion(conn: psycopg.Connection, dia: date, desde: str | None) -> datetime | None:
    """Con --desde, esa hora local del día; si no, el primer enchufe de ese día desde las 12:00."""
    if desde:
        hora, minuto = (int(x) for x in desde.split(":"))
        return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=MADRID).astimezone(UTC)
    mediodia = datetime(dia.year, dia.month, dia.day, 12, tzinfo=MADRID).astimezone(UTC)
    fila = conn.execute("SELECT min(inicio) FROM plan_conexiones(%s, %s) WHERE inicio >= %s",
                        (mediodia, mediodia + timedelta(hours=12), mediodia)).fetchone()
    return fila[0] if fila else None


def simular(conn: psycopg.Connection, ahora: datetime) -> Simulacion:
    """Plan calculado en `ahora` (sin la franja en curso de cálculos guardados) y su ahorro en la conexión
    que contiene ese instante, con la referencia en Python de v_plan_ahorro. No guarda nada."""
    entrada, ctx, plan, ms = calcular(conn, ahora, con_en_curso=False)
    fila = conn.execute("SELECT inicio, fin FROM plan_conexiones(%s, %s) WHERE fin IS NOT NULL ORDER BY inicio"
                        " LIMIT 1", (ahora, ahora + timedelta(seconds=1))).fetchone()
    if fila is None:
        return Simulacion(entrada, ctx, plan, ms, None, None)
    inicio, fin = fila
    energia = dict(conn.execute("SELECT inicio, coche_wh FROM energia_tramo WHERE inicio >= %s AND inicio < %s",
                                (cuarto_de(inicio), fin)))
    precio = dict(conn.execute("SELECT inicio, precio_final_eur_kwh FROM v_plan_precio WHERE inicio >= %s"
                               " AND inicio < %s", (cuarto_de(inicio), fin)))
    potencia = {h: kw for h, kw, _, _ in conn.execute("SELECT * FROM plan_potencia_hora(%s)", (inicio,))}
    factores = dict(conn.execute(
        "SELECT d::date, (SELECT valor FROM cargador_parametro WHERE clave = 'factor_contador'"
        " AND vigencia @> d::date) FROM generate_series(%s::date, %s::date, interval '1 day') d",
        (inicio.astimezone(MADRID).date(), fin.astimezone(MADRID).date())))
    plan_kwh = {c.inicio: c.kwh for c in plan.cuartos}
    ahorro = ahorro_conexion(inicio, fin, energia, plan_kwh, potencia, precio, factores.get)
    return Simulacion(entrada, ctx, plan, ms, (inicio, fin), ahorro)
