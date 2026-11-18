"""Entrada y resultado del planificador (E3.2 a E3.5). Decimal, instantes en UTC.

Unidades: energías en kWh. `kwh` de un cuarto o una franja es energía del
contador del cargador; a la batería llega × factor_bateria (E3.4). Niveles,
objetivos y consumos de las salidas son energía en la batería.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

CUARTO = timedelta(minutes=15)

# Niveles por prioridad (E3.4); en_curso: la franja que ya ha empezado (intocable).
NIVELES = ("en_curso", "necesaria", "margen", "oportunista")
PRIORIDAD = {n: i for i, n in enumerate(NIVELES)}


@dataclass(frozen=True)
class Parametros:
    capacidad_kwh: Decimal
    tope_kwh: Decimal
    factor_bateria: Decimal
    margen_carga_pct: Decimal
    margen_salida_min: int
    carga_oportunista: bool
    nivel_oportunista_kwh: Decimal
    umbral_disponibilidad_pct: Decimal
    franja_min_min: int
    max_tareas: int


@dataclass(frozen=True)
class Cuarto:
    """Cuarto de hora del horizonte. precio None: ni publicado ni esperado.

    probabilidad: de que el coche esté enchufado (E3.3); None sin perfil.
    potencia_kw: la de su hora local (E3.2).
    """

    inicio: datetime
    precio: Decimal | None
    provisional: bool
    probabilidad: Decimal | None
    potencia_kw: Decimal

    @property
    def fin(self) -> datetime:
        return self.inicio + CUARTO


@dataclass(frozen=True)
class Salida:
    instante: datetime
    tipo: str
    objetivo_kwh: Decimal
    consumo_kwh: Decimal


@dataclass(frozen=True)
class Ocupado:
    """Otra tarea activa del cargador (E3.4, sin solapes).

    origen: «ajena» (app de Orbis u otra no registrada) o «manual» (fijada en el
    formulario, 3c). tarea: id en el cargador.
    """

    inicio: datetime
    fin: datetime
    origen: str
    tarea: int | None = None


@dataclass(frozen=True)
class Entrada:
    ahora: datetime
    parametros: Parametros
    nivel_kwh: Decimal  # nivel previsto ahora, con el consumo del trayecto en curso descontado
    conectado: bool
    prudente: bool  # menos historial que historial_min_disponibilidad_dias (E3.3)
    umbral: Decimal | None  # €/kWh; None sin días completos de precio
    cuartos: tuple[Cuarto, ...]  # del cuarto actual al final del horizonte, consecutivos
    salidas: tuple[Salida, ...]  # dentro del horizonte, en orden
    horizonte_fin: datetime
    ocupados: tuple[Ocupado, ...] = ()
    en_curso: FranjaEnCurso | None = None
    respetar_ajenas: bool = False  # sombra: False (se planifica sin ellas y se anotan los choques)


@dataclass(frozen=True)
class CuartoPlan:
    inicio: datetime
    nivel: str
    salida: datetime | None
    kwh: Decimal
    precio: Decimal | None
    provisional: bool
    ventana: str  # segura o probable
    sobre_umbral: bool


@dataclass(frozen=True)
class FranjaEnCurso:
    """Franja del plan anterior que contiene el instante: se conserva tal cual (E3.4), con sus cuartos."""

    inicio: datetime
    fin: datetime
    cuartos: tuple[CuartoPlan, ...]


@dataclass(frozen=True)
class Franja:
    inicio: datetime
    fin: datetime
    motivo: str
    salida: datetime | None
    kwh: Decimal
    provisional: bool
    tareas: int
    en_curso: bool
    choca_con: tuple[int, ...] = ()
    # kWh del contador por (nivel, salida), por prioridad y salida: («necesaria», jue 09:30, 8,0),
    # («oportunista», None, 24,7). motivo es el primero.
    desglose: tuple[tuple[str, datetime | None, Decimal], ...] = ()


@dataclass(frozen=True)
class SalidaPlan:
    instante: datetime
    tipo: str
    objetivo_kwh: Decimal
    consumo_kwh: Decimal
    necesaria_kwh: Decimal  # a batería, lo que faltaba sin el plan
    nivel_salida_kwh: Decimal  # previsto al salir, con el plan
    cubierta: bool
    salto_umbral: bool


@dataclass(frozen=True)
class Plan:
    cuartos: tuple[CuartoPlan, ...]
    franjas: tuple[Franja, ...]
    salidas: tuple[SalidaPlan, ...]
    avisos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tareas(self) -> int:
        return sum(f.tareas for f in self.franjas)
