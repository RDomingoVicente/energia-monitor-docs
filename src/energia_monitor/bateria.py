"""Referencia en Python del nivel de batería y la previsión (migraciones 0014 a 0017).

El servicio calcula en PostgreSQL (recalcular_bateria y las funciones
bateria_*). Este módulo replica el mismo algoritmo con Decimal para validar
el SQL en los tests; no lo usa el servicio. Especificación: docs/fase2.md,
"Etapa 2: batería y previsión".

Resumen del algoritmo:

1. Sesiones: se clasifica su fin (carga completa, fin de franja...).
2. Trayectos: de conectado (3..8) a libre (1, 2) y vuelta, según los cambios
   de estado.
3. Eventos: sesiones (en su fin), cargas completas, trayectos (en su
   llegada), cargas externas y lecturas. Una sesión o un trayecto que
   contiene un valor absoluto se parte en él: la sesión por la energía del
   modulador y el trayecto por tiempo. Un trayecto también se parte en cada
   carga externa (0016). Orden: (instante, orden, ref) con orden 1 sesión,
   2 carga completa, 3 trayecto, 4 carga externa y 5 lectura.
4. Primera pasada, con consumos estimados: nivel previsto y, al llegar a
   cada valor absoluto, reconciliación del intervalo que cierra.
5. Segunda pasada: nivel con los consumos reconciliados.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from energia_monitor.tiempo import MADRID

CERO = Decimal(0)
CIEN = Decimal(100)
MIL = Decimal(1000)
SEG_HORA = Decimal(3600)
MARGEN_CARGA_COMPLETA = timedelta(seconds=180)
CONECTADO = range(3, 9)
LIBRE = (1, 2)
ORDEN = {"sesion": 1, "carga_completa": 2, "trayecto": 3, "carga_externa": 4, "lectura": 5}

# Valores iniciales de las migraciones 0014, 0016, 0020 y 0022, vigentes desde el 1/1/2025.
PARAMETROS_INICIALES: dict[str, Decimal] = {
    "capacidad_util_kwh": Decimal("91.3"),
    "reserva_pct": Decimal(25),
    "tope_pct": Decimal(100),
    "factor_bateria": Decimal("0.86"),
    "percentil_planificacion": Decimal("0.75"),
    "ventana_consumo_dias": Decimal(28),
    "desconexion_min_s": Decimal(1800),
    "consumo_inicial_trayecto_kwh": Decimal(25),
    "consumo_kwh_100km": Decimal(23),
    "energia_min_calibracion_kwh": Decimal(5),
    "observaciones_calibracion": Decimal(5),
    "detectar_carga_completa": Decimal(1),
    "aviso_sin_lectura_dias": Decimal(7),
    "hueco_muestreo_aviso_s": Decimal(1800),
    "umbral_carga_externa_kwh": Decimal(5),  # 0016
    # 0020: planificador de carga (etapa 3, E3.9).
    "carga_oportunista": Decimal(1),
    "umbral_oportunista_pct": Decimal(60),
    "nivel_oportunista_pct": Decimal(100),
    "margen_carga_pct": Decimal(10),
    "margen_salida_min": Decimal(30),
    "potencia_carga_inicial_kw": Decimal("4.5"),
    "ventana_disponibilidad_dias": Decimal(28),
    "umbral_disponibilidad_pct": Decimal(80),
    "historial_min_disponibilidad_dias": Decimal(21),
    "horizonte_provisional_h": Decimal(48),
    "franja_min_min": Decimal(30),
    "max_tareas_sistema": Decimal(7),
    "id_tarea_guardiana": Decimal(9),
    "aviso_planificador_min": Decimal(60),
    "ventana_precio_esperado_dias": Decimal(28),
    "ventana_umbral_dias": Decimal(7),
    "ventana_potencia_dias": Decimal(28),
    "horizonte_plan_h": Decimal(72),
    "hora_publicacion_precios": Decimal(810),  # minutos desde las 00:00 (13:30)
    "congelar_franja_min": Decimal(30),  # 0022
    "aviso_precios_min": Decimal(30),  # 0023
}


# --- Entradas ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Parametro:
    clave: str
    desde: date
    hasta: date | None  # exclusivo
    valor: Decimal


class Parametros:
    """Parámetros con vigencia por fecha local, como bateria_param()."""

    def __init__(self, filas: Iterable[Parametro] | None = None) -> None:
        if filas is None:
            filas = [Parametro(c, date(2025, 1, 1), None, v) for c, v in PARAMETROS_INICIALES.items()]
        self._filas: dict[str, list[Parametro]] = defaultdict(list)
        for f in filas:
            self._filas[f.clave].append(f)

    def filas(self) -> list[Parametro]:
        return [f for filas in self._filas.values() for f in filas]

    def valor(self, clave: str, instante: datetime) -> Decimal:
        dia = instante.astimezone(MADRID).date()
        for f in self._filas[clave]:
            if f.desde <= dia and (f.hasta is None or dia < f.hasta):
                return f.valor
        raise KeyError(f"parámetro de batería {clave} sin valor el {dia}")


@dataclass(frozen=True)
class Lectura:
    id: int
    instante: datetime
    pct: Decimal


@dataclass(frozen=True)
class CargaExterna:
    """bateria_carga_externa (0016); kwh None si no se sabe."""

    id: int
    instante: datetime
    kwh: Decimal | None


@dataclass(frozen=True)
class SesionCargador:
    conector: str
    id: int
    inicio: datetime
    fin: datetime
    energia_wh: Decimal
    origen_fin: str | None


@dataclass(frozen=True)
class MuestraEstado:
    instante: datetime
    intervalo_s: int | None
    evse_w: Decimal
    estado: int | None


@dataclass(frozen=True)
class CambioEstado:
    conector: str
    detectado_en: datetime
    estado: int


@dataclass(frozen=True)
class Excepcion:
    """bateria_excepcion (0015): con_salida (hora_salida, consumo_modo normal o indicado con km o pct,
    pct_objetivo opcional) o sin_salida (solo la fecha)."""

    tipo: str
    fecha: date
    hora_salida: time | None = None
    consumo_modo: str | None = None
    km: Decimal | None = None
    pct: Decimal | None = None
    pct_objetivo: Decimal | None = None

    @property
    def indicado(self) -> bool:
        return self.tipo == "con_salida" and self.consumo_modo == "indicado"


@dataclass
class Entradas:
    parametros: Parametros = field(default_factory=Parametros)
    lecturas: list[Lectura] = field(default_factory=list)  # solo las no anuladas
    sesiones: list[SesionCargador] = field(default_factory=list)
    muestras: list[MuestraEstado] = field(default_factory=list)
    estados: list[CambioEstado] = field(default_factory=list)
    excepciones: list[Excepcion] = field(default_factory=list)  # solo las activas
    horario: dict[int, time | None] = field(default_factory=dict)  # 1 = lunes
    cargas_externas: list[CargaExterna] = field(default_factory=list)  # solo las activas


# --- Salidas -------------------------------------------------------------------------------


@dataclass
class SesionClasificada:
    conector: str
    id: int
    inicio: datetime
    fin: datetime
    energia_wh: Decimal
    factor: Decimal
    cierre: str


@dataclass
class Trayecto:
    conector: str
    salida: datetime
    llegada: datetime | None
    corto: bool
    viaje_largo: bool
    consumo_estimado_kwh: Decimal | None = None
    consumo_kwh: Decimal | None = None
    origen_consumo: str | None = None
    reconciliado_en: datetime | None = None


@dataclass
class Evento:
    instante: datetime
    orden: int
    ref: str
    tipo: str
    conector: str | None = None
    sesion_id: int | None = None
    trayecto_salida: datetime | None = None
    lectura_id: int | None = None
    carga_externa_id: int | None = None
    peso: Decimal | None = None
    contador_kwh: Decimal | None = None
    absoluto_kwh: Decimal | None = None
    delta_estimado_kwh: Decimal | None = None
    delta_kwh: Decimal | None = None
    nivel_estimado_kwh: Decimal | None = None
    estimado_previo_kwh: Decimal | None = None
    nivel_kwh: Decimal | None = None
    nivel_pct: Decimal | None = None
    n: int = 0


@dataclass
class Intervalo:
    desde: datetime
    hasta: datetime
    a1_kwh: Decimal
    a2_kwh: Decimal
    sesiones_kwh: Decimal
    contador_kwh: Decimal
    trayectos: int
    trayectos_cortos: int
    consumo_estimado_kwh: Decimal
    consumo_real_kwh: Decimal
    estado: str
    huecos: bool
    factor_observado: Decimal | None
    cargas_externas: int = 0
    externa_kwh: Decimal = CERO  # con factor_bateria; las cargas sin kWh no suman


@dataclass
class Resultado:
    sesiones: list[SesionClasificada]
    trayectos: list[Trayecto]
    eventos: list[Evento]
    intervalos: list[Intervalo]


# --- Estadística ---------------------------------------------------------------------------


def percentil_cont(valores: Iterable[Decimal], p: Decimal) -> Decimal | None:
    """percentile_cont de PostgreSQL: interpolación lineal entre los dos valores vecinos."""
    v = sorted(valores)
    if not v:
        return None
    pos = Decimal(p) * (len(v) - 1)
    bajo = int(math.floor(pos))
    alto = int(math.ceil(pos))
    return v[bajo] + (v[alto] - v[bajo]) * (pos - bajo)


def mediana(valores: Iterable[Decimal]) -> Decimal | None:
    return percentil_cont(valores, Decimal("0.5"))


def hay_hueco(muestras: Sequence[MuestraEstado], desde: datetime, hasta: datetime, umbral_s: Decimal) -> bool:
    """bateria_hueco(): tramo sin muestras de más de umbral_s (>= 180) entre desde y hasta.

    Como en el SQL, dentro del intervalo solo se miran las muestras tras un hueco (intervalo_s None,
    que el muestreo pone cuando han pasado más de 180 s), además de los dos extremos.
    """
    dentro = sorted((m for m in muestras if desde < m.instante < hasta), key=lambda m: m.instante)
    if not dentro:
        return _segundos(desde, hasta) > umbral_s
    if _segundos(desde, dentro[0].instante) > umbral_s or _segundos(dentro[-1].instante, hasta) > umbral_s:
        return True
    return any(m.intervalo_s is None and _segundos(anterior.instante, m.instante) > umbral_s
               for anterior, m in zip(dentro, dentro[1:]))


def _segundos(a: datetime, b: datetime) -> Decimal:
    return Decimal(str((b - a).total_seconds()))


def _acotar(x: Decimal, cap: Decimal) -> Decimal:
    return min(max(x, CERO), cap)


def dia_local(instante: datetime) -> date:
    return instante.astimezone(MADRID).date()


# --- Recálculo -----------------------------------------------------------------------------


def clasificar_sesiones(e: Entradas, ahora: datetime) -> list[SesionClasificada]:
    p = e.parametros
    muestras = sorted(e.muestras, key=lambda m: m.instante)
    salida = []
    for s in e.sesiones:
        if s.origen_fin == "schedman":
            cierre = "fin_franja"
        elif p.valor("detectar_carga_completa", s.fin) == 0:
            cierre = "sin_regla"
        elif s.origen_fin != "evsm":
            cierre = "otro"
        elif ahora < s.fin + MARGEN_CARGA_COMPLETA:
            cierre = "pendiente"
        else:
            siguiente = next((m for m in muestras if s.fin < m.instante <= s.fin + MARGEN_CARGA_COMPLETA), None)
            estado = siguiente.estado if siguiente else None
            if estado in (4, 8):
                cierre = "carga_completa"
            elif estado in LIBRE:
                cierre = "desconexion"
            elif siguiente is None and (not muestras or s.fin < muestras[0].instante):
                cierre = "sin_muestreo"  # 0017: terminó antes de que empezara el muestreo
            else:
                cierre = "dudoso"
        salida.append(SesionClasificada(s.conector, s.id, s.inicio, s.fin, s.energia_wh,
                                        p.valor("factor_bateria", s.fin), cierre))
    return salida


def detectar_trayectos(e: Entradas, ahora: datetime) -> list[Trayecto]:
    p = e.parametros
    por_conector: dict[str, list[CambioEstado]] = defaultdict(list)
    for c in sorted(e.estados, key=lambda c: c.detectado_en):
        if c.detectado_en <= ahora and (c.estado in CONECTADO or c.estado in LIBRE):
            por_conector[c.conector].append(c)
    dias_largos = {x.fecha for x in e.excepciones if x.indicado}
    trayectos = []
    for conector, cambios in por_conector.items():
        transiciones: list[tuple[datetime, str]] = []
        antes = None
        for c in cambios:
            clase = "c" if c.estado in CONECTADO else "l"
            if antes is not None and antes != clase:
                transiciones.append((c.detectado_en, clase))
            antes = clase
        for i, (t, clase) in enumerate(transiciones):
            if clase != "l":
                continue
            llegada = next((u for u, k in transiciones[i + 1:] if k == "c"), None)
            corto = llegada is not None and _segundos(t, llegada) < p.valor("desconexion_min_s", t)
            largo = not corto and dia_local(t) in dias_largos
            trayectos.append(Trayecto(conector, t, llegada, corto, largo))
    for t in trayectos:
        if t.corto:
            t.consumo_estimado_kwh, t.origen_consumo = CERO, "cero"
    return sorted(trayectos, key=lambda t: (t.conector, t.salida))


def _ref_trayecto(t: Trayecto) -> str:
    return f"trayecto:{t.conector}:{round(t.salida.timestamp())}"


def _ws(muestras: Sequence[MuestraEstado], desde: datetime, hasta: datetime) -> Decimal:
    return sum((m.evse_w * m.intervalo_s for m in muestras
                if m.intervalo_s is not None and desde < m.instante <= hasta), CERO)


def construir_eventos(e: Entradas, sesiones: list[SesionClasificada], trayectos: list[Trayecto],
                      ahora: datetime) -> list[Evento]:
    p = e.parametros
    lecturas = [lec for lec in e.lecturas if lec.instante <= ahora]
    externas = [x for x in e.cargas_externas if x.instante <= ahora]
    cortes = sorted({lec.instante for lec in lecturas} | {s.fin for s in sesiones if s.cierre == "carga_completa"})
    cortes_trayecto = sorted(set(cortes) | {x.instante for x in externas})
    eventos: list[Evento] = []
    for s in sesiones:
        puntos = [s.inicio] + [c for c in cortes if s.inicio < c < s.fin] + [s.fin]
        partes = list(zip(puntos, puntos[1:]))
        ws = [_ws(e.muestras, a, b) for a, b in partes]
        total = sum(ws, CERO)
        for (a, b), w_s in zip(partes, ws):
            if total > 0:
                w = w_s / total
            elif s.fin > s.inicio:
                w = _segundos(a, b) / _segundos(s.inicio, s.fin)
            else:
                w = Decimal(1)
            eventos.append(Evento(b, 1, f"sesion:{s.conector}:{s.id}", "sesion", s.conector, s.id,
                                  peso=w, contador_kwh=s.energia_wh / MIL * w,
                                  delta_estimado_kwh=s.energia_wh / MIL * s.factor * w))
    for t in trayectos:
        puntos = [t.salida] + [c for c in cortes_trayecto if c > t.salida and (t.llegada is None or c < t.llegada)]
        if t.llegada is not None:
            puntos.append(t.llegada)
        for a, b in zip(puntos, puntos[1:]):
            eventos.append(Evento(b, 3, _ref_trayecto(t), "trayecto", t.conector, trayecto_salida=t.salida,
                                  peso=_segundos(a, b) / _segundos(t.salida, puntos[-1])))
    for s in sesiones:
        if s.cierre == "carga_completa":
            eventos.append(Evento(s.fin, 2, f"carga_completa:{s.conector}:{s.id}", "carga_completa", s.conector,
                                  s.id, absoluto_kwh=p.valor("tope_pct", s.fin) / CIEN
                                  * p.valor("capacidad_util_kwh", s.fin)))
    for x in externas:
        eventos.append(Evento(x.instante, 4, f"carga_externa:{x.id}", "carga_externa", carga_externa_id=x.id,
                              delta_estimado_kwh=None if x.kwh is None
                              else x.kwh * p.valor("factor_bateria", x.instante)))
    for lec in lecturas:
        eventos.append(Evento(lec.instante, 5, f"lectura:{lec.id}", "lectura", lectura_id=lec.id,
                              absoluto_kwh=lec.pct / CIEN * p.valor("capacidad_util_kwh", lec.instante)))
    eventos.sort(key=lambda ev: (ev.instante, ev.orden, ev.ref))
    for i, ev in enumerate(eventos, 1):
        ev.n = i
    return eventos


def estimar_trayecto(e: Entradas, t: Trayecto, trayectos: list[Trayecto], ahora: datetime) -> Decimal:
    """bateria_estimar_trayecto(): guarda y devuelve el consumo estimado."""
    p = e.parametros
    if t.corto:
        est, origen = CERO, "cero"
    elif t.viaje_largo:
        dia = dia_local(t.salida)
        x = next(x for x in e.excepciones if x.indicado and x.fecha == dia)
        declarado = consumo_declarado(p, x, t.salida)
        del_dia = [o for o in trayectos if o.conector == t.conector and o.viaje_largo and dia_local(o.salida) == dia]
        total = sum((_segundos(o.salida, o.llegada or ahora) for o in del_dia), CERO)
        dur = _segundos(t.salida, t.llegada or ahora)
        est = declarado * dur / total if total > 0 else declarado / len(del_dia)
        origen = "declarado"
    else:
        ventana = timedelta(days=int(p.valor("ventana_consumo_dias", t.salida)))
        reales = [o.consumo_kwh for o in trayectos
                  if o.conector == t.conector
                  and o.origen_consumo == "reconciliado" and not o.corto and not o.viaje_largo
                  and o.reconciliado_en is not None and o.reconciliado_en <= t.salida
                  and t.salida - ventana <= o.salida < t.salida]
        m = mediana([r for r in reales if r is not None])
        if m is None:
            est, origen = p.valor("consumo_inicial_trayecto_kwh", t.salida), "inicial"
        else:
            est, origen = m, "estimado"
    t.consumo_estimado_kwh, t.origen_consumo = est, origen
    return est


def recalcular(e: Entradas, ahora: datetime) -> Resultado:
    p = e.parametros
    sesiones = clasificar_sesiones(e, ahora)
    trayectos = detectar_trayectos(e, ahora)
    por_clave = {(t.conector, t.salida): t for t in trayectos}
    eventos = construir_eventos(e, sesiones, trayectos, ahora)
    intervalos: list[Intervalo] = []

    # Primera pasada.
    nivel: Decimal | None = None
    a1: Evento | None = None
    s_kwh = c_kwh = x_kwh = CERO
    externas = sin_kwh = 0
    for ev in eventos:
        cap = p.valor("capacidad_util_kwh", ev.instante)
        if ev.tipo == "sesion":
            assert ev.delta_estimado_kwh is not None and ev.contador_kwh is not None
            if nivel is not None:
                nivel = _acotar(nivel + ev.delta_estimado_kwh, cap)
            if a1 is not None:
                s_kwh += ev.delta_estimado_kwh
                c_kwh += ev.contador_kwh
            ev.nivel_estimado_kwh = nivel
        elif ev.tipo == "carga_externa":
            if ev.delta_estimado_kwh is not None and nivel is not None:
                nivel = _acotar(nivel + ev.delta_estimado_kwh, cap)
            if a1 is not None:
                externas += 1
                if ev.delta_estimado_kwh is None:
                    sin_kwh += 1
                else:
                    x_kwh += ev.delta_estimado_kwh
            ev.nivel_estimado_kwh = nivel
        elif ev.tipo == "trayecto":
            assert ev.peso is not None and ev.trayecto_salida is not None and ev.conector is not None
            t = por_clave[(ev.conector, ev.trayecto_salida)]
            est = t.consumo_estimado_kwh
            if est is None:
                est = estimar_trayecto(e, t, trayectos, ahora)
            ev.delta_estimado_kwh = -est * ev.peso
            if nivel is not None:
                nivel = _acotar(nivel - est * ev.peso, cap)
            ev.nivel_estimado_kwh = nivel
        else:
            assert ev.absoluto_kwh is not None
            ev.estimado_previo_kwh = nivel
            ev.nivel_estimado_kwh = ev.absoluto_kwh
            if a1 is not None:
                assert a1.absoluto_kwh is not None
                intervalos.append(_reconciliar(e, a1, ev, eventos, por_clave, s_kwh, c_kwh, cap,
                                               externas, sin_kwh, x_kwh))
            nivel = ev.absoluto_kwh
            a1 = ev
            s_kwh = c_kwh = x_kwh = CERO
            externas = sin_kwh = 0

    for t in trayectos:
        if t.consumo_estimado_kwh is None:
            estimar_trayecto(e, t, trayectos, ahora)

    # Segunda pasada.
    nivel = None
    for ev in eventos:
        cap = p.valor("capacidad_util_kwh", ev.instante)
        if ev.tipo in ("lectura", "carga_completa"):
            nivel = ev.absoluto_kwh
        else:
            if ev.delta_kwh is None:
                ev.delta_kwh = ev.delta_estimado_kwh
            # Solo una carga externa sin kWh queda sin delta: no cambia el nivel.
            if ev.delta_kwh is not None and nivel is not None:
                nivel = _acotar(nivel + ev.delta_kwh, cap)
        ev.nivel_kwh = nivel
        ev.nivel_pct = None if nivel is None else nivel / cap * CIEN

    for t in trayectos:
        if t.llegada is not None and t.origen_consumo != "reconciliado":
            partes = [ev for ev in eventos if ev.tipo == "trayecto" and ev.trayecto_salida == t.salida
                      and ev.conector == t.conector]
            t.consumo_kwh = sum((-ev.delta_kwh for ev in partes if ev.delta_kwh is not None), CERO)
    return Resultado(sesiones, trayectos, eventos, intervalos)


def _reconciliar(e: Entradas, a1: Evento, a2: Evento, eventos: list[Evento],
                 por_clave: dict[tuple[str, datetime], Trayecto], s_kwh: Decimal, c_kwh: Decimal,
                 cap: Decimal, externas: int, sin_kwh: int, x_kwh: Decimal) -> Intervalo:
    p = e.parametros
    assert a1.absoluto_kwh is not None and a2.absoluto_kwh is not None
    partes = [ev for ev in eventos if ev.tipo == "trayecto" and a1.n < ev.n < a2.n]
    largas = [ev for ev in partes if not por_clave[(ev.conector, ev.trayecto_salida)].corto]  # type: ignore[index]
    cortas = len(partes) - len(largas)
    est = sum((-ev.delta_estimado_kwh for ev in largas), CERO)  # type: ignore[operator]
    real = a1.absoluto_kwh + s_kwh + x_kwh - a2.absoluto_kwh
    huecos = hay_hueco(e.muestras, a1.instante, a2.instante, p.valor("hueco_muestreo_aviso_s", a2.instante))
    factor = None
    if sin_kwh:
        # Carga externa sin kWh (0016): no se reparte nada ni se marca incoherente; sus trayectos
        # se quedan sin reconciliar y por eso fuera de la mediana y del P75.
        estado = "carga_externa_sin_kwh"
    elif not largas:
        # Sin trayectos que repartir. Con carga, la diferencia es error del factor (E2.6), no
        # incoherencia; sin carga, el nivel no puede subir.
        if real > cap or (c_kwh == 0 and real < 0):
            estado = "incoherente"
        else:
            estado = "sin_trayectos"
            if (cortas == 0 and not huecos and externas == 0 and c_kwh > 0
                    and c_kwh >= p.valor("energia_min_calibracion_kwh", a2.instante)):
                observado = (a2.absoluto_kwh - a1.absoluto_kwh) / c_kwh
                if 0 < observado <= 1:
                    factor = observado
    elif real < 0 or real > cap:
        estado = "incoherente"
    else:
        estado = "reconciliado"
        for ev in largas:
            assert ev.delta_estimado_kwh is not None
            ev.delta_kwh = -(real / len(largas) if est == 0 else real * (-ev.delta_estimado_kwh) / est)
        for t in {(ev.conector, ev.trayecto_salida) for ev in largas}:
            tr = por_clave[t]  # type: ignore[index]
            todas = [ev for ev in eventos if ev.tipo == "trayecto" and ev.conector == tr.conector
                     and ev.trayecto_salida == tr.salida]
            if tr.llegada is not None and all(ev.delta_kwh is not None for ev in todas):
                tr.origen_consumo = "reconciliado"
                tr.reconciliado_en = a2.instante
                tr.consumo_kwh = sum((-ev.delta_kwh for ev in todas), CERO)  # type: ignore[operator]
    return Intervalo(a1.instante, a2.instante, a1.absoluto_kwh, a2.absoluto_kwh, s_kwh, c_kwh, len(largas),
                     cortas, est, real, estado, huecos, factor, externas, x_kwh)


# --- Previsión -----------------------------------------------------------------------------


def local_a_utc(dia: date, hora: time) -> datetime:
    """(fecha + hora) AT TIME ZONE 'Europe/Madrid' de PostgreSQL.

    Hora inexistente (cambio de marzo): con el desfase anterior al cambio.
    Hora ambigua (cambio de octubre): la segunda vez, ya en horario de invierno.
    """
    ingenua = datetime.combine(dia, hora)
    f0 = ingenua.replace(tzinfo=MADRID, fold=0)
    f1 = ingenua.replace(tzinfo=MADRID, fold=1)
    if f0.utcoffset() == f1.utcoffset():
        return f0.astimezone(UTC)
    existe = f0.astimezone(UTC).astimezone(MADRID).replace(tzinfo=None) == ingenua
    return (f1 if existe else f0).astimezone(UTC)


@dataclass(frozen=True)
class Salida:
    fecha: date
    salida: datetime
    tipo: str  # horario o con_salida
    consumo_modo: str  # normal o indicado
    pct_objetivo: Decimal | None
    consumo_declarado_kwh: Decimal | None
    consumo_previsto_kwh: Decimal  # el declarado o, en modo normal, el P75 de bateria_consumo_plan(desde)
    objetivo_kwh: Decimal  # min(tope, objetivo_sin_tope_kwh)
    objetivo_sin_tope_kwh: Decimal  # max(reserva + consumo previsto, pct_objetivo), en kWh


def consumo_declarado(p: Parametros, x: Excepcion, instante: datetime) -> Decimal:
    """Consumo indicado: pct de capacidad_util_kwh o km por consumo_kwh_100km."""
    if x.pct is not None:
        return x.pct / CIEN * p.valor("capacidad_util_kwh", instante)
    assert x.km is not None
    return x.km * p.valor("consumo_kwh_100km", instante) / CIEN


def salidas(e: Entradas, desde: datetime, dias: int, trayectos: Iterable[Trayecto]) -> list[Salida]:
    """bateria_salidas(): horario semanal y excepciones, del día local de desde a dias días después.

    Objetivo de cada salida = min(tope, max(reserva + consumo previsto, pct_objetivo)), con los
    parámetros vigentes el día de la salida.
    """
    p = e.parametros
    plan, _, _ = consumo_plan(e, trayectos, desde)
    hoy = dia_local(desde)
    excepciones = {x.fecha: x for x in e.excepciones}
    resultado = []
    for i in range(dias + 1):
        d = hoy + timedelta(days=i)
        x = excepciones.get(d)
        if x is not None and x.tipo == "sin_salida":
            continue
        hora = x.hora_salida if x is not None else e.horario.get(d.isoweekday())
        if hora is None:
            continue
        instante = local_a_utc(d, hora)
        if instante <= desde:
            continue
        tipo, modo, pct_objetivo, declarado = "horario", "normal", None, None
        if x is not None:
            assert x.consumo_modo is not None
            tipo, modo, pct_objetivo = x.tipo, x.consumo_modo, x.pct_objetivo
            declarado = consumo_declarado(p, x, instante) if x.indicado else None
        previsto = declarado if declarado is not None else plan
        cap = p.valor("capacidad_util_kwh", instante)
        sin_tope = p.valor("reserva_pct", instante) / CIEN * cap + previsto
        if pct_objetivo is not None:
            sin_tope = max(sin_tope, pct_objetivo / CIEN * cap)
        objetivo = min(sin_tope, p.valor("tope_pct", instante) / CIEN * cap)
        resultado.append(Salida(d, instante, tipo, modo, pct_objetivo, declarado, previsto, objetivo, sin_tope))
    return sorted(resultado, key=lambda s: s.salida)


@dataclass(frozen=True)
class ConsumoDia:
    fecha: date
    trayectos: int
    consumo_kwh: Decimal | None
    cuenta: bool


def consumo_por_dia(trayectos: Iterable[Trayecto]) -> list[ConsumoDia]:
    """v_bateria_consumo_dia."""
    por_dia: dict[date, list[Trayecto]] = defaultdict(list)
    for t in trayectos:
        if not t.corto:
            por_dia[dia_local(t.salida)].append(t)
    resultado = []
    for d, ts in sorted(por_dia.items()):
        consumos = [t.consumo_kwh for t in ts if t.consumo_kwh is not None]
        reconciliado = all(t.origen_consumo == "reconciliado" for t in ts)
        resultado.append(ConsumoDia(d, len(ts), sum(consumos, CERO) if consumos else None,
                                    reconciliado and not any(t.viaje_largo for t in ts)))
    return resultado


def consumo_plan(e: Entradas, trayectos: Iterable[Trayecto], ahora: datetime) -> tuple[Decimal, int, str]:
    """bateria_consumo_plan(): (kWh, días usados, 'historia' o 'inicial')."""
    p = e.parametros
    hoy = dia_local(ahora)
    ventana = int(p.valor("ventana_consumo_dias", ahora))
    dias = [c.consumo_kwh for c in consumo_por_dia(trayectos)
            if c.cuenta and hoy - timedelta(days=ventana) < c.fecha <= hoy and c.consumo_kwh is not None]
    valor = percentil_cont(dias, p.valor("percentil_planificacion", ahora))
    if valor is None:
        return p.valor("consumo_inicial_trayecto_kwh", ahora), 0, "inicial"
    return valor, len(dias), "historia"


def factor_sugerido(e: Entradas, intervalos: Iterable[Intervalo], ahora: datetime) -> tuple[Decimal | None, int]:
    """bateria_factor_sugerido(): mediana de las últimas observaciones."""
    n = int(e.parametros.valor("observaciones_calibracion", ahora))
    obs = sorted((i for i in intervalos if i.factor_observado is not None and i.hasta <= ahora),
                 key=lambda i: i.hasta, reverse=True)[:n]
    return mediana([i.factor_observado for i in obs if i.factor_observado is not None]), len(obs)


@dataclass(frozen=True)
class Estado:
    nivel_kwh: Decimal | None
    energia_pendiente_kwh: Decimal
    coche: str
    consumo_en_curso_kwh: Decimal
    nivel_plan_kwh: Decimal | None
    proxima_salida: datetime | None
    consumo_plan_kwh: Decimal | None
    energia_necesaria_kwh: Decimal | None
    # 0016
    confianza: str = "estimado"  # leido, leido_carga o estimado
    ultima_lectura_ts: datetime | None = None
    ultima_lectura_pct: Decimal | None = None
    kwh_cargados_desde_lectura: Decimal | None = None
    trayectos_desde_lectura: int | None = None
    kwh_trayectos_desde_lectura: Decimal | None = None
    objetivo_kwh: Decimal | None = None


def estado(e: Entradas, r: Resultado, ahora: datetime) -> Estado:
    """Lo numérico de bateria_estado(): nivel ahora, próxima salida y energía necesaria."""
    p = e.parametros
    cap = p.valor("capacidad_util_kwh", ahora)
    factor = p.valor("factor_bateria", ahora)
    pasados = [ev for ev in r.eventos if ev.instante <= ahora]
    base = pasados[-1] if pasados else None
    absolutos = [ev for ev in pasados if ev.tipo in ("lectura", "carga_completa")]
    ultimo_abs = absolutos[-1].instante if absolutos else None
    abs_ev = absolutos[-1] if absolutos else None
    fines = [s.fin for s in e.sesiones if s.fin <= ahora]
    candidatos = [x for x in (ultimo_abs, max(fines) if fines else None) if x is not None]
    limite = max(candidatos) if candidatos else None
    wh = sum((m.evse_w * m.intervalo_s for m in e.muestras
              if m.intervalo_s is not None and m.instante <= ahora and (limite is None or m.instante > limite)),
             CERO) / SEG_HORA
    pendiente = wh / MIL * factor
    nivel = None
    if base is not None and base.nivel_kwh is not None:
        nivel = _acotar(base.nivel_kwh + pendiente, cap)

    cambios = sorted((c for c in e.estados if c.detectado_en <= ahora), key=lambda c: c.detectado_en)
    ultimo = cambios[-1].estado if cambios else None
    if ultimo is None:
        coche = "desconocido"
    elif ultimo in LIBRE:
        coche = "fuera"
    elif ultimo in (5, 6):
        coche = "cargando"
    elif ultimo in CONECTADO:
        coche = "en_casa"
    else:
        coche = "desconocido"
    en_curso = CERO
    if coche == "fuera":
        abiertos = sorted((t for t in r.trayectos if t.llegada is None), key=lambda t: t.salida)
        if abiertos and (ultimo_abs is None or ultimo_abs <= abiertos[-1].salida):
            en_curso = abiertos[-1].consumo_estimado_kwh or CERO
    nivel_plan = None if nivel is None else max(nivel - en_curso, CERO)

    proximas = salidas(e, ahora, 7, r.trayectos)
    prox = proximas[0] if proximas else None
    plan, _, _ = consumo_plan(e, r.trayectos, ahora)
    consumo = prox.consumo_previsto_kwh if prox is not None else plan
    necesaria = None
    if prox is not None and nivel_plan is not None:
        necesaria = max(prox.objetivo_kwh - nivel_plan, CERO)

    # Confianza del nivel (0016): desde la última lectura, solo cargas (leido_carga) o nada (leido).
    lecturas = sorted((lec for lec in e.lecturas if lec.instante <= ahora), key=lambda lec: lec.instante)
    ultima = lecturas[-1] if lecturas else None
    cargados = trayectos_desde = kwh_trayectos = None
    if ultima is not None:
        ev_lec = next(ev for ev in pasados if ev.tipo == "lectura" and ev.lectura_id == ultima.id)
        despues = [ev for ev in pasados if ev.n > ev_lec.n]
        cargados = sum((ev.delta_kwh for ev in despues
                        if ev.tipo in ("sesion", "carga_externa") and ev.delta_kwh is not None), CERO) + pendiente
        kwh_trayectos = sum((-ev.delta_kwh for ev in despues if ev.tipo == "trayecto" and ev.delta_kwh is not None),
                            CERO)
        trayectos_desde = sum(1 for t in r.trayectos if not t.corto and t.llegada is not None
                              and ultima.instante < t.llegada <= ahora)
    confianza = "estimado"
    if abs_ev is not None and abs_ev.tipo == "lectura" and coche != "fuera":
        despues_abs = [ev for ev in pasados if ev.n > abs_ev.n]
        con_trayecto = any(ev.tipo == "trayecto" and not por_salida(r, ev).corto for ev in despues_abs)
        if not con_trayecto:
            cargas = any(ev.tipo in ("sesion", "carga_externa") and ev.delta_kwh is not None and ev.delta_kwh > 0
                         for ev in despues_abs)
            confianza = "leido_carga" if cargas or pendiente > 0 else "leido"
    return Estado(nivel, pendiente, coche, en_curso, nivel_plan, prox.salida if prox else None, consumo, necesaria,
                  confianza, ultima.instante if ultima else None, ultima.pct if ultima else None, cargados,
                  trayectos_desde, kwh_trayectos, prox.objetivo_kwh if prox else None)


def por_salida(r: Resultado, ev: Evento) -> Trayecto:
    return next(t for t in r.trayectos if t.conector == ev.conector and t.salida == ev.trayecto_salida)
