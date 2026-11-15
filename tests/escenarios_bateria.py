"""Escenarios de batería compartidos por los tests de la referencia en Python
(test_bateria.py) y los del SQL (integration/test_bateria_db.py).

Cada escenario devuelve las entradas y el instante del cálculo. Lunes
16/11/2026 en UTC; Madrid va una hora por delante.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D

from energia_monitor.bateria import (
    CambioEstado,
    Entradas,
    Excepcion,
    Lectura,
    MuestraEstado,
    SesionCargador,
)
from energia_monitor.muestreo import intervalo_desde

CON = "mennekes"
T0 = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)
MIN = timedelta(minutes=1)


def t(horas: float) -> datetime:
    return T0 + timedelta(hours=horas)


@dataclass
class Constructor:
    """Entradas escritas como una historia: enchufes, cargas, salidas y lecturas."""

    e: Entradas = field(default_factory=Entradas)
    _id_lectura: int = 0

    def lectura(self, instante: datetime, pct: str | int) -> Constructor:
        self._id_lectura += 1
        self.e.lecturas.append(Lectura(self._id_lectura, instante, D(pct)))
        return self

    def estado(self, instante: datetime, estado: int) -> Constructor:
        self.e.estados.append(CambioEstado(CON, instante, estado))
        return self

    def trayecto(self, salida: datetime, llegada: datetime | None) -> Constructor:
        """Desconexión y reconexión (si llegada no es None)."""
        self.estado(salida, 1)
        if llegada is not None:
            self.estado(llegada, 4)
        return self

    def sesion(self, inicio: datetime, fin: datetime, wh: str | int, origen_fin: str = "schedman",
               potencia_w: str | int | None = None, estado_muestras: int = 6) -> Constructor:
        """Sesión del histórico; con potencia_w, además, una muestra por minuto durante la carga."""
        ident = int(inicio.timestamp())
        self.e.sesiones.append(SesionCargador(CON, ident, inicio, fin, D(wh), origen_fin))
        if potencia_w is not None:
            self.muestras(inicio, fin, potencia_w, estado_muestras)
        return self

    def muestras(self, desde: datetime, hasta: datetime, potencia_w: str | int = 0, estado: int | None = 4,
                 paso: timedelta = MIN) -> Constructor:
        """Muestras con instante en (desde, hasta], cada `paso`, cubriendo cada una el paso anterior."""
        existentes = {m.instante for m in self.e.muestras}
        instante = desde + paso
        while instante <= hasta:
            if instante not in existentes:
                self.e.muestras.append(MuestraEstado(instante, int(paso.total_seconds()), D(potencia_w), estado))
            instante += paso
        return self

    def muestra(self, instante: datetime, estado: int | None, potencia_w: str | int = 0) -> Constructor:
        self.e.muestras.append(MuestraEstado(instante, 60, D(potencia_w), estado))
        return self

    def excepcion(self, x: Excepcion) -> Constructor:
        self.e.excepciones.append(x)
        return self

    def horario(self, **dias: time | None) -> Constructor:
        nombres = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
        for nombre, hora in dias.items():
            self.e.horario[nombres.index(nombre) + 1] = hora
        return self


Escenario = Callable[[], tuple[Entradas, datetime]]
ESCENARIOS: dict[str, Escenario] = {}


def con_intervalos(e: Entradas) -> Entradas:
    """Muestras sin instantes repetidos y con intervalo_s como lo pone el muestreo (None tras >180 s)."""
    por_instante = {m.instante: m for m in e.muestras}
    anterior = None
    muestras = []
    for instante in sorted(por_instante):
        m = por_instante[instante]
        muestras.append(MuestraEstado(instante, intervalo_desde(anterior, instante), m.evse_w, m.estado))
        anterior = instante
    e.muestras = muestras
    return e


def escenario(f: Escenario) -> Escenario:
    def normalizado() -> tuple[Entradas, datetime]:
        e, ahora = f()
        return con_intervalos(e), ahora

    ESCENARIOS[f.__name__] = normalizado
    return normalizado


@escenario
def sin_lectura() -> tuple[Entradas, datetime]:
    c = Constructor().estado(t(-10), 4).sesion(t(2), t(5), 12000)
    return c.e, t(12)


@escenario
def lectura_y_sesion() -> tuple[Entradas, datetime]:
    c = Constructor().estado(t(-10), 4).lectura(t(1), 50).sesion(t(2), t(5), 10000)
    return c.e, t(12)


@escenario
def acotado_al_tope() -> tuple[Entradas, datetime]:
    c = Constructor().estado(t(-10), 4).lectura(t(1), 95).sesion(t(2), t(5), 20000)
    return c.e, t(12)


@escenario
def trayecto_reconciliado() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(6), 80)
         .trayecto(t(8), t(10)).lectura(t(11), 40))
    return c.e, t(12)


@escenario
def varios_trayectos() -> tuple[Entradas, datetime]:
    # A con el consumo inicial (25 kWh); B, de viaje largo declarado (20 % = 18,26 kWh).
    c = (Constructor().estado(t(-10), 4).lectura(t(6), 90)
         .trayecto(t(8), t(10))
         .trayecto(t(32), t(40))
         .excepcion(Excepcion("viaje_largo", date(2026, 11, 17), time(9, 0), consumo_pct=D(20)))
         .lectura(t(41), "46.2"))
    return c.e, t(42)


@escenario
def desconexion_corta() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(6), 60)
         .trayecto(t(8), t(8) + 10 * MIN).lectura(t(11), 60))
    return c.e, t(12)


@escenario
def intervalo_incoherente() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(6), 30)
         .trayecto(t(8), t(10)).lectura(t(11), 50))
    return c.e, t(12)


@escenario
def lectura_retroactiva() -> tuple[Entradas, datetime]:
    # La lectura de las 3 h se registra después que la de las 11 h (ids en otro orden).
    c = (Constructor().estado(t(-10), 4).lectura(t(11), 40)
         .sesion(t(4), t(6), 15000).trayecto(t(8), t(10)).lectura(t(3), 50))
    return c.e, t(12)


@escenario
def sesion_abierta() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(1), 50)
         .muestras(t(0), t(2), 0, 4)
         .estado(t(2), 6).muestras(t(2), t(3), 4400, 6))
    return c.e, t(3)


@escenario
def lectura_durante_trayecto() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(6), 80)
         .trayecto(t(8), t(12)).lectura(t(10), 60).lectura(t(13), 45))
    return c.e, t(14)


@escenario
def lectura_durante_trayecto_abierto() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(6), 80)
         .trayecto(t(8), None).lectura(t(10), 60))
    return c.e, t(11)


@escenario
def lectura_durante_carga() -> tuple[Entradas, datetime]:
    # 1 h a 4000 W antes de la lectura y 2 h a 4000 W después.
    c = (Constructor().estado(t(-10), 4).lectura(t(0), 30)
         .muestras(t(-1), t(2), 0, 4)
         .sesion(t(2), t(5), 12000, potencia_w=4000).lectura(t(3), 34).lectura(t(6), 45)
         .muestras(t(5), t(7), 0, 4))
    return c.e, t(7)


@escenario
def carga_completa() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(0), 40)
         .sesion(t(2), t(5), 40000, origen_fin="evsm")
         .muestra(t(5) + 40 * timedelta(seconds=1), 4))
    return c.e, t(6)


@escenario
def fin_de_franja_y_dudosa() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(0), 40)
         .sesion(t(2), t(3), 4000, origen_fin="schedman").muestra(t(3) + MIN, 4)
         .sesion(t(4), t(5), 4000, origen_fin="evsm")  # sin muestra posterior: dudosa
         .sesion(t(6), t(7), 4000, origen_fin="evsm").muestra(t(7) + MIN, 1)  # desconexión
         .sesion(t(8), t(9), 4000, origen_fin="evsm"))  # recién terminada: pendiente
    return c.e, t(9) + MIN


@escenario
def calibracion() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(0), 20)
         .muestras(t(0), t(2), 0, 4)
         .sesion(t(2), t(5), 30000, potencia_w=10000)
         .muestras(t(5), t(6), 0, 4)
         .lectura(t(6), "48.26"))
    return c.e, t(7)


@escenario
def estimacion_por_mediana() -> tuple[Entradas, datetime]:
    """Tres trayectos reconciliados (10, 20 y 30 kWh): el cuarto se estima con la mediana."""
    c = Constructor().estado(t(-10), 4).lectura(t(0), 90)
    for i, kwh in enumerate((10, 20, 30)):
        base = 24 * i
        c.trayecto(t(base + 8), t(base + 10))
        c.lectura(t(base + 11), str(D(90) - D(kwh) / D("91.3") * 100))
        c.lectura(t(base + 12), 90)  # cargado por arte de magia: sin sesión, intervalo incoherente
    c.trayecto(t(80), t(82))
    return c.e, t(83)


@escenario
def prevision() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(1), 50)
         .horario(lun=time(9, 0), mar=time(9, 0), mie=time(9, 0), jue=time(9, 0), vie=time(7, 0))
         .excepcion(Excepcion("sin_coche", date(2026, 11, 16)))
         .excepcion(Excepcion("otra_hora", date(2026, 11, 17), time(6, 30))))
    return c.e, t(2)


@escenario
def coche_fuera() -> tuple[Entradas, datetime]:
    c = (Constructor().estado(t(-10), 4).lectura(t(1), 50)
         .horario(lun=time(20, 0), mar=time(9, 0))
         .trayecto(t(5), None))
    return c.e, t(6)


def aleatorio(semilla: int) -> tuple[Entradas, datetime]:
    """Historia de varios días con enchufes, cargas, salidas y lecturas al azar."""
    rnd = random.Random(semilla)
    c = Constructor()
    instante = T0 + timedelta(minutes=rnd.randrange(0, 600))
    c.estado(instante - H, 4)
    conectado = True
    for _ in range(rnd.randrange(6, 16)):
        instante += timedelta(minutes=rnd.randrange(10, 400))
        accion = rnd.random()
        if conectado and accion < 0.35:
            fin = instante + timedelta(minutes=rnd.randrange(30, 300))
            wh = rnd.randrange(0, 30000)
            origen = rnd.choice(["schedman", "evsm", "evsm"])
            potencia = rnd.choice([None, rnd.randrange(1000, 4600)])
            c.sesion(instante, fin, wh, origen, potencia)
            if origen == "evsm" and rnd.random() < 0.7:
                c.muestra(fin + timedelta(seconds=rnd.randrange(1, 200)), rnd.choice([1, 4, 8, None]))
            instante = fin
        elif conectado and accion < 0.6:
            c.estado(instante, rnd.choice([1, 2]))
            conectado = False
        elif not conectado and accion < 0.7:
            c.estado(instante, rnd.choice([3, 4, 7]))
            conectado = True
        else:
            c.lectura(instante + timedelta(seconds=rnd.randrange(0, 59)), str(rnd.randrange(0, 1001) / D(10)))
        if rnd.random() < 0.2:
            # Lectura dentro de lo que esté pasando (trayecto o sesión).
            c.lectura(instante - timedelta(minutes=rnd.randrange(1, 9)), rnd.randrange(5, 95))
    if rnd.random() < 0.3:
        dia = (T0 + timedelta(days=rnd.randrange(0, 4))).date()
        if rnd.random() < 0.5:
            c.excepcion(Excepcion("viaje_largo", dia, time(8, 0), consumo_km=D(rnd.randrange(50, 400))))
        else:
            c.excepcion(Excepcion("viaje_largo", dia, time(8, 0), consumo_pct=D(rnd.randrange(10, 80))))
    # Instantes de lectura únicos (índice único parcial).
    vistos: set[datetime] = set()
    unicas = []
    for lec in c.e.lecturas:
        if lec.instante not in vistos:
            vistos.add(lec.instante)
            unicas.append(lec)
    c.e.lecturas = unicas
    return con_intervalos(c.e), instante + H
