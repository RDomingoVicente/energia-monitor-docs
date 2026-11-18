"""Consultas de Plan de carga (7.9) y del panel «Próximas cargas» de Inicio (7.1), paso 3b: solo lectura.

Lee el último cálculo del planificador (plan_calculo, v_plan_vigente, plan_cuarto, plan_salida), el ahorro
(plan_ahorro, 0021), las franjas pasadas (plan_franjas_pasadas, 0021) y, para la línea de tiempo, precios
(v_plan_precio) y disponibilidad (plan_cuartos_observados), con las mismas reglas que el planificador
(plan_carga.precios y plan_carga.disponibilidad). El dinero sale de SQL; aquí solo se suma por periodo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor.plan_carga import disponibilidad, precios
from energia_monitor.plan_carga.ahorro import cuarto_de
from energia_monitor.plan_carga.franjas import num_tareas
from energia_monitor.plan_carga.modelo import CUARTO, PRIORIDAD
from energia_monitor.tiempo import MADRID

# Escala fija de la línea de tiempo (maqueta 01d).
ESCALA_EUR_KWH = Decimal("0.60")


@dataclass(frozen=True)
class FranjaPlan:
    inicio: datetime
    fin: datetime
    kwh: Decimal
    provisional: bool
    tareas: int
    en_curso: bool
    precio_medio: Decimal | None
    coste: Decimal | None
    desglose: tuple[tuple[str, datetime | None, Decimal], ...]  # (nivel, salida, kWh del cargador)
    ejecucion: str  # programada · en su horario · coche cargando · …
    progreso_kwh: Decimal | None = None  # en curso: kWh reales del cargador desde su inicio
    manual: bool = False  # fijada desde el formulario (o Cargar ahora)
    estado_cargador: str = "no escrita (sombra)"  # E3.5: pendiente, confirmada, error · motivo


@dataclass(frozen=True)
class FranjaPasada:
    inicio: datetime
    fin: datetime
    sombra: bool
    plan_kwh: Decimal
    desglose: tuple[tuple[str, Decimal], ...]
    real_kwh: Decimal
    coste_real: Decimal | None
    conectado: bool

    @property
    def resultado(self) -> str:
        """En sombra la franja no se escribió: no se dice «terminada» sino si coincidió con carga real (7.9)."""
        if self.sombra:
            return "coincidió con carga real" if self.real_kwh > 0 else "sin carga real"
        if self.real_kwh > 0:
            return "terminada"
        return "no ejecutada · coche no enchufado" if not self.conectado else "no ejecutada · sin energía"


@dataclass(frozen=True)
class Ahorro:
    periodo: str
    conexiones: int
    real: Decimal
    plan: Decimal
    resto: Decimal
    referencia: Decimal
    sin_precio: int

    @property
    def ahorro(self) -> Decimal:
        return self.referencia - self.real


@dataclass(frozen=True)
class TareaCargador:
    """Una fila del panel «Tareas en el cargador» (de la última lectura del programador)."""

    id: int
    origen: str  # planificador | manual | app Orbis | guardiana
    programacion: str
    estado: str  # activa | en curso | inactiva


@dataclass(frozen=True)
class Accion:
    id: int
    tipo: str
    estado: str  # pendiente | hecha | error
    resultado: str | None
    datos: dict[str, object]


@dataclass(frozen=True)
class SalidaPlan:
    instante: datetime
    objetivo_kwh: Decimal
    cubierta: bool


@dataclass(frozen=True)
class Hora:
    inicio: datetime
    precio: Decimal | None
    provisional: bool


@dataclass(frozen=True)
class Linea:
    """Línea de tiempo: [desde, hasta) en cuartos de hora."""

    desde: datetime
    hasta: datetime
    ahora: datetime
    umbral: Decimal | None
    horas: list[Hora]
    franjas: list[FranjaPlan]
    coche: list[tuple[datetime, datetime, str]]  # (inicio, fin, «enchufado» | «probable»)
    salidas: list[datetime]
    despues: list[FranjaPlan]  # franjas fuera de la vista, después

    @property
    def cuartos(self) -> int:
        return int((self.hasta - self.desde) / CUARTO)

    def indice(self, t: datetime) -> int:
        """Cuarto (0..cuartos) de un instante, recortado a la vista."""
        return max(0, min(self.cuartos, int((t - self.desde) / CUARTO)))


@dataclass
class PlanCarga:
    ahora: datetime
    modo: str
    calculado: datetime | None  # ahora del último cálculo; None: aún no hay plan
    prudente: bool = False
    conectado: bool = False
    capacidad_kwh: Decimal = Decimal(0)
    factor_bateria: Decimal = Decimal(1)
    nivel_kwh: Decimal | None = None
    nivel_tras_curso_kwh: Decimal | None = None
    umbral: Decimal | None = None
    umbral_pct: Decimal = Decimal(0)
    ventana_umbral_dias: int = 7
    umbral_disponibilidad_pct: Decimal = Decimal(80)
    max_tareas: int = 7
    horizonte_fin: datetime | None = None
    salidas: list[SalidaPlan] = field(default_factory=list)
    franjas: list[FranjaPlan] = field(default_factory=list)
    pasadas: list[FranjaPasada] = field(default_factory=list)
    ahorro: list[Ahorro] = field(default_factory=list)
    linea: Linea | None = None
    linea_movil: Linea | None = None
    avisos: list[str] = field(default_factory=list)
    tareas_cargador: list[TareaCargador] | None = None  # None: aún sin lectura del programador
    lectura_en: datetime | None = None
    libres: list[int] = field(default_factory=list)
    ultima_accion: Accion | None = None

    @property
    def sombra(self) -> bool:
        return self.modo == "sombra"

    @property
    def coste_total(self) -> Decimal | None:
        costes = [f.coste for f in self.franjas]
        return None if any(c is None for c in costes) else sum(costes, Decimal(0))  # type: ignore[arg-type]

    @property
    def coste_provisional(self) -> Decimal:
        return sum((f.coste or Decimal(0) for f in self.franjas if f.provisional), Decimal(0))


def _param(conn: psycopg.Connection, clave: str, ahora: datetime) -> Decimal:
    fila = conn.execute("SELECT bateria_param(%s, %s)", (clave, ahora)).fetchone()
    assert fila is not None
    return fila[0]


def _estado_coche(conn: psycopg.Connection, ahora: datetime) -> tuple[bool, bool]:
    """(conectado, cargando): último estado del conector y última muestra (estado 6 con potencia)."""
    estado = conn.execute("SELECT estado FROM cargador_estado WHERE detectado_en <= %s ORDER BY detectado_en DESC"
                          " LIMIT 1", (ahora,)).fetchone()
    muestra = conn.execute("SELECT estado, evse_w FROM cargador_muestra WHERE instante <= %s ORDER BY instante DESC"
                           " LIMIT 1", (ahora,)).fetchone()
    conectado = estado is not None and estado[0] not in (1, 2)
    cargando = muestra is not None and muestra[0] == 6 and muestra[1] > 0
    return conectado, cargando


def _ejecucion(f_inicio: datetime, f_fin: datetime, ahora: datetime, sombra: bool, cargando: bool) -> str:
    if not f_inicio <= ahora < f_fin:
        return "programada"
    if sombra:
        return "en su horario · coche cargando" if cargando else "en su horario · coche sin cargar"
    return "cargando" if cargando else "en pausa"


def _estado_cargador(propias: list[tuple[int, datetime | None, str, str | None]], inicio: datetime,
                     sombra: bool) -> str:
    """E3.5 a partir de planificador_tarea (las tareas de la franja: una, o dos si cruza la medianoche)."""
    estados = [(e, err) for _, fi, e, err in propias if fi == inicio]
    if not estados:
        return "no escrita (sombra)" if sombra else "pendiente"
    if any(e == "error" for e, _ in estados):
        return "error · " + next(err or "" for e, err in estados if e == "error")
    if any(e == "pendiente" for e, _ in estados):
        return "pendiente"
    return "confirmada"


def _manuales(conn: psycopg.Connection, ahora: datetime, sombra: bool, cargando: bool,
              ya: list[FranjaPlan]) -> list[FranjaPlan]:
    """Franjas manuales activas y franjas propias del cargador que el plan ya no contiene (en curso o congeladas):
    también se muestran, porque están (o estarán) en el cargador."""
    propias = conn.execute("SELECT id_tarea, franja_inicio, estado, error FROM planificador_tarea"
                           " WHERE papel = 'franja'").fetchall()
    potencia = conn.execute("SELECT bateria_param('potencia_carga_inicial_kw', %s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    salida = []
    vistas = {f.inicio for f in ya}
    filas = [(i, f, "manual", True) for i, f in conn.execute(
        "SELECT inicio, fin FROM plan_manual WHERE anulada_en IS NULL AND fin > %s ORDER BY inicio", (ahora,))]
    filas += [(i, f, "en el cargador", False) for i, f in conn.execute(
        "SELECT DISTINCT franja_inicio, franja_fin FROM planificador_tarea WHERE papel = 'franja'"
        " AND origen = 'automatica' AND franja_fin > %s", (ahora,)) if i is not None]
    for inicio, fin, nivel, manual in filas:
        if inicio in vistas:
            continue
        vistas.add(inicio)
        kwh = potencia * Decimal((fin - inicio).total_seconds()) / 3600
        salida.append(FranjaPlan(inicio, fin, kwh, False, num_tareas(inicio, fin),
                                 inicio <= ahora < fin, None, None, ((nivel, None, kwh),),
                                 _ejecucion(inicio, fin, ahora, sombra, cargando), None, manual,
                                 _estado_cargador(propias, inicio, sombra)))
    return salida


def _franjas(conn: psycopg.Connection, calculo_id: int, ahora: datetime, sombra: bool,
             cargando: bool) -> list[FranjaPlan]:
    cuartos = conn.execute("SELECT inicio, nivel, salida, kwh_cargador FROM plan_cuarto WHERE calculo_id = %s",
                           (calculo_id,)).fetchall()
    propias = conn.execute("SELECT id_tarea, franja_inicio, estado, error FROM planificador_tarea"
                           " WHERE papel = 'franja'").fetchall()
    lista = []
    for inicio, fin, kwh, provisional, tareas, en_curso, precio, coste in conn.execute(
            "SELECT inicio, fin, kwh_cargador, provisional, tareas, en_curso, precio_medio_eur_kwh, coste_eur"
            " FROM v_plan_franja WHERE calculo_id = %s AND fin > %s ORDER BY inicio", (calculo_id, ahora)):
        por_nivel: dict[tuple[str, datetime | None], Decimal] = {}
        for q, nivel, salida, k in cuartos:
            if inicio <= q < fin:
                por_nivel[(nivel, salida)] = por_nivel.get((nivel, salida), Decimal(0)) + k
        maximo = datetime.max.replace(tzinfo=inicio.tzinfo)
        desglose = tuple((n, s, k) for (n, s), k in sorted(por_nivel.items(),
                                                            key=lambda x: (PRIORIDAD[x[0][0]], x[0][1] or maximo)))
        progreso = None
        if inicio <= ahora < fin:
            fila = conn.execute("SELECT coalesce(sum(coche_wh), 0) / 1000 FROM energia_tramo WHERE inicio >= %s"
                                " AND inicio < %s", (inicio, ahora)).fetchone()
            progreso = fila[0] if fila else None
        lista.append(FranjaPlan(inicio, fin, kwh, provisional, tareas, en_curso, precio, coste, desglose,
                                _ejecucion(inicio, fin, ahora, sombra, cargando), progreso, False,
                                _estado_cargador(propias, inicio, sombra)))
    lista += _manuales(conn, ahora, sombra, cargando, lista)
    return sorted(lista, key=lambda f: f.inicio)


def _linea(conn: psycopg.Connection, ahora: datetime, desde: datetime, horas: int, franjas: list[FranjaPlan],
           salidas: list[SalidaPlan], umbral: Decimal | None, prudente: bool) -> Linea:
    hasta = desde + timedelta(hours=horas)
    ventana = int(_param(conn, "ventana_precio_esperado_dias", ahora))
    publicacion = int(_param(conn, "hora_publicacion_precios", ahora))
    filas = conn.execute("SELECT inicio, precio_final_eur_kwh FROM v_plan_precio WHERE inicio >= %s AND inicio < %s",
                         (min(desde, ahora) - timedelta(days=ventana), hasta)).fetchall()
    publicados = dict(filas)
    perfil = precios.perfil_esperado([(i, p) for i, p in filas if i < ahora], ahora, ventana)
    lista_horas = []
    for k in range(horas):
        h0 = desde + timedelta(hours=k)
        valores, provisional = [], False
        for i in range(4):
            q = h0 + i * CUARTO
            if q < ahora and q in publicados:
                valores.append(publicados[q])
                continue
            p, prov = precios.precio_cuarto(q, ahora, publicados, perfil, publicacion)
            provisional = provisional or prov
            if p is not None:
                valores.append(p)
        lista_horas.append(Hora(h0, sum(valores, Decimal(0)) / len(valores) if valores else None, provisional))

    coche: list[tuple[datetime, datetime, str]] = []
    # La conexión abierta cuenta como enchufado hasta la próxima salida (ventana segura).
    proxima = min((s.instante for s in salidas if s.instante > ahora), default=hasta)
    for ini, fin in conn.execute("SELECT inicio, fin FROM plan_conexiones(%s, %s)", (desde, hasta)):
        a, b = max(ini, desde), min(fin or proxima, hasta)
        if a < b:
            coche.append((a, b, "enchufado"))
    if not prudente:
        # Futuro no enchufado: tramos probables, con el mismo perfil que el planificador.
        ventana_d = int(_param(conn, "ventana_disponibilidad_dias", ahora))
        umbral_d = _param(conn, "umbral_disponibilidad_pct", ahora)
        perfil_d = disponibilidad.perfil(
            [(i, c) for i, c, _ in conn.execute("SELECT * FROM plan_cuartos_observados(%s, %s)",
                                                (ahora - timedelta(days=ventana_d), ahora))])
        ocupado = max((b for _, b, _ in coche), default=ahora)
        q = max(cuarto_de(ahora) + CUARTO, cuarto_de(ocupado))
        inicio_tramo = None
        while q < hasta:
            prob = perfil_d.get(disponibilidad.clave(q))
            si = prob is not None and prob * 100 >= umbral_d and q >= ocupado
            if si and inicio_tramo is None:
                inicio_tramo = q
            if not si and inicio_tramo is not None:
                coche.append((inicio_tramo, q, "probable"))
                inicio_tramo = None
            q += CUARTO
        if inicio_tramo is not None:
            coche.append((inicio_tramo, hasta, "probable"))
    return Linea(desde, hasta, ahora, umbral, lista_horas,
                 [f for f in franjas if f.inicio < hasta and f.fin > desde], coche,
                 [s.instante for s in salidas if desde <= s.instante < hasta],
                 [f for f in franjas if f.inicio >= hasta])


def _ahorro(conn: psycopg.Connection, ahora: datetime) -> list[Ahorro]:
    hoy = datetime.combine(ahora.astimezone(MADRID).date(), datetime.min.time(), tzinfo=MADRID)
    filas = []
    for periodo, desde in (("hoy", hoy), ("7 días", hoy - timedelta(days=6)), ("30 días", hoy - timedelta(days=29))):
        fila = conn.execute(
            "SELECT count(*), coalesce(sum(coste_real_eur), 0), coalesce(sum(coste_plan_eur), 0),"
            " coalesce(sum(coste_resto_eur), 0), coalesce(sum(coste_referencia_eur), 0),"
            " count(*) FILTER (WHERE sin_precio) FROM plan_ahorro(%s, %s)",
            (desde, ahora + timedelta(seconds=1))).fetchone()
        assert fila is not None
        filas.append(Ahorro(periodo, int(fila[0]), fila[1], fila[2], fila[3], fila[4], int(fila[5])))
    return filas


def plan_carga(conn: psycopg.Connection, ahora: datetime) -> PlanCarga:
    modo = conn.execute("SELECT planificador_modo_en(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    calc = conn.execute(
        "SELECT id, ahora, prudente, capacidad_kwh, umbral_eur_kwh, horizonte_fin, avisos FROM plan_calculo"
        " WHERE ahora <= %s ORDER BY ahora DESC, id DESC LIMIT 1", (ahora,)).fetchone()
    d = PlanCarga(ahora, modo, None)
    d.pasadas = [FranjaPasada(i, f, m == "sombra", pk, tuple((n, k) for n, k in (("necesaria", nk), ("margen", mk),
                                                                                  ("oportunista", ok)) if k),
                              rk, cr, con)
                 for i, f, m, pk, nk, mk, ok, _s, _p, rk, cr, con in conn.execute(
                     "SELECT * FROM plan_franjas_pasadas(%s, %s) ORDER BY inicio DESC",
                     (ahora - timedelta(days=7), ahora))]
    d.ahorro = _ahorro(conn, ahora)
    _tareas_cargador(conn, d)
    if calc is None:
        return d
    ident, d.calculado, d.prudente, d.capacidad_kwh, d.umbral, d.horizonte_fin, avisos = calc
    d.avisos = list(avisos)
    d.factor_bateria = _param(conn, "factor_bateria", ahora)
    d.umbral_pct = _param(conn, "umbral_oportunista_pct", ahora)
    d.ventana_umbral_dias = int(_param(conn, "ventana_umbral_dias", ahora))
    d.umbral_disponibilidad_pct = _param(conn, "umbral_disponibilidad_pct", ahora)
    d.max_tareas = int(_param(conn, "max_tareas_sistema", ahora))
    d.conectado, cargando = _estado_coche(conn, ahora)
    estado = conn.execute("SELECT nivel_kwh FROM bateria_estado(%s)", (ahora,)).fetchone()
    d.nivel_kwh = estado[0] if estado else None
    d.salidas = [SalidaPlan(*f) for f in conn.execute(
        "SELECT salida, objetivo_kwh, cubierta FROM plan_salida WHERE calculo_id = %s AND salida > %s ORDER BY salida",
        (ident, ahora))]
    d.franjas = _franjas(conn, ident, ahora, d.sombra, cargando)
    if d.nivel_kwh is not None:
        curso = next((f for f in d.franjas if f.inicio <= ahora < f.fin), None)
        resto = Decimal(0)
        if curso is not None:
            resto = sum((k for q, k in conn.execute(
                "SELECT inicio, kwh_cargador FROM plan_cuarto WHERE calculo_id = %s AND inicio >= %s AND inicio < %s",
                (ident, cuarto_de(ahora), curso.fin)) if q + CUARTO > ahora), Decimal(0))
        d.nivel_tras_curso_kwh = min(d.nivel_kwh + resto * d.factor_bateria, d.capacidad_kwh)
    # En UTC: 60 h son siempre 240 cuartos, también los días de cambio de hora.
    hoy = datetime.combine(ahora.astimezone(MADRID).date(), datetime.min.time(), tzinfo=MADRID).astimezone(UTC)
    d.linea = _linea(conn, ahora, hoy, 60, d.franjas, d.salidas, d.umbral, d.prudente)
    hora = ahora.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    d.linea_movil = _linea(conn, ahora, hora, 24, d.franjas, d.salidas, d.umbral, d.prudente)
    return d


DIAS_SEMANA = ("D", "L", "M", "X", "J", "V", "S")


def _tareas_cargador(conn: psycopg.Connection, d: PlanCarga) -> None:
    """Panel «Tareas en el cargador» desde la última lectura (planificador_lectura)."""
    from energia_monitor.viaris import parsear_programador

    fila = conn.execute("SELECT leida_en, ok, tareas FROM planificador_lectura").fetchone()
    if fila is None or not fila[1]:
        return
    d.lectura_en = fila[0]
    leidas = parsear_programador([{"name": "mennekes", "data": {"cfg": {"tasks": fila[2]}}}])
    registradas = dict(conn.execute("SELECT id_tarea, origen FROM planificador_tarea"))
    gid = int(_param(conn, "id_tarea_guardiana", d.ahora))
    filas = []
    for t in sorted(leidas, key=lambda x: x.id):
        franja = f"{t.hour_min // 60:02d}:{t.hour_min % 60:02d}"
        fin_min = t.hour_min + t.duracion
        if t.dia_mes:
            programacion = f"{t.dia_mes[0]:02d}/{t.dia_mes[1]:02d} {franja}"
        elif t.weekday == 0 and t.day == 0 and t.month == 0:
            programacion = f"todos los días {franja}"
        else:
            programacion = "".join(x for i, x in enumerate(DIAS_SEMANA) if t.weekday & (1 << i)) + f" {franja}"
        programacion += f" · {t.duracion} min" if t.duracion < 60 else f" · {t.duracion // 60} h" + (
            f" {t.duracion % 60} min" if t.duracion % 60 else "")
        if t.id == gid:
            origen = "guardiana"
        elif t.id in registradas:
            origen = "manual" if registradas[t.id] == "manual" else "planificador"
        else:
            origen = "app Orbis"
        local = d.ahora.astimezone(MADRID)
        hoy = (t.dia_mes is None or t.dia_mes == (local.day, local.month))
        en_curso = t.activa and hoy and t.hour_min <= local.hour * 60 + local.minute < fin_min and origen != "app Orbis"
        filas.append(TareaCargador(t.id, origen, programacion, "en curso" if en_curso else
                                   ("activa" if t.activa else "inactiva")))
    d.tareas_cargador = filas
    d.libres = [i for i in range(1, 11) if i not in {t.id for t in leidas}]


def accion(conn: psycopg.Connection, ident: int) -> Accion | None:
    fila = conn.execute("SELECT id, tipo, estado, resultado, datos FROM plan_accion WHERE id = %s", (ident,)).fetchone()
    return None if fila is None else Accion(*fila)


def proximas_cargas(conn: psycopg.Connection, ahora: datetime, n: int = 3) -> tuple[str, list[FranjaPlan]]:
    """(modo, las n siguientes franjas del último cálculo) para Inicio."""
    modo = conn.execute("SELECT planificador_modo_en(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    calc = conn.execute("SELECT id FROM plan_calculo WHERE ahora <= %s ORDER BY ahora DESC, id DESC LIMIT 1",
                        (ahora,)).fetchone()
    if calc is None:
        return modo, []
    _, cargando = _estado_coche(conn, ahora)
    return modo, _franjas(conn, calc[0], ahora, modo == "sombra", cargando)[:n]
