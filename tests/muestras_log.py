"""Lectura de tests/fixtures/viaris/muestras-2026-11-13.log y un cliente falso que la reproduce.

Una línea por minuto: "instante_UTC JSON_modulador JSON_evsm" (el stat de
GET /modules/modulator?level=stat y el de GET /modules/evsm/elements), del
13/11/2026 12:45 al 14/11/2026 12:46, hora de Madrid. Si la lectura falló, solo
el instante. La primera línea es "nohup: ignoring input". Firmware 7.7.34.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from energia_monitor import bateria
from energia_monitor.muestreo import intervalo_desde
from energia_monitor.viaris import (
    ErrorCargador,
    EstadoConector,
    Modulador,
    Sesion,
    parsear_evsm,
    parsear_linea_historico,
    parsear_modulador,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "viaris" / "muestras-2026-11-13.log"

# La sesión del 14/11/2026 tal como la guardó el muestreo en el NAS (cargador_sesion.linea):
# de 03:00 a 06:00, hora de Madrid, 13 667 Wh, termina por el planificador (schedman).
LINEA_SESION_14_11 = ("1794621600,1,1,mennekes,1,6000,1794621600,,schedman,6966610,1794632401,,schedman,"
                      "6980277,13667,13667,0,0")


@dataclass(frozen=True)
class Lectura:
    instante: datetime
    modulador: Modulador | None
    estado: EstadoConector | None


def leer(ruta: Path = FIXTURE) -> list[Lectura]:
    lecturas = []
    decodificador = json.JSONDecoder()
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea[:1].isdigit():
            continue  # "nohup: ignoring input"
        instante_txt, _, resto = linea.partition(" ")
        instante = datetime.fromisoformat(instante_txt.replace("Z", "+00:00"))
        resto = resto.strip()
        if not resto:
            lecturas.append(Lectura(instante, None, None))
            continue
        mod, fin = decodificador.raw_decode(resto)
        evsm, _ = decodificador.raw_decode(resto[fin:].strip())
        lecturas.append(Lectura(instante, parsear_modulador({"stat": mod}), parsear_evsm([{"stat": evsm}])))
    return lecturas


def sesion_14_11() -> Sesion:
    s = parsear_linea_historico(LINEA_SESION_14_11)
    assert s is not None
    return s


def entradas(hasta: datetime | None = None) -> tuple[bateria.Entradas, datetime]:
    """Lo que guardaría el muestreo hasta `hasta` (incluido): muestras con su intervalo, cambios de estado
    y la sesión del 14/11 si ya había terminado. Devuelve también el instante de la última línea."""
    e = bateria.Entradas()
    anterior = None
    ultimo_estado = None
    lecturas = [lec for lec in leer() if hasta is None or lec.instante <= hasta]
    for lec in lecturas:
        if lec.modulador is not None:
            e.muestras.append(bateria.MuestraEstado(lec.instante, intervalo_desde(anterior, lec.instante),
                                                    lec.modulador.evse_w, lec.estado.estado if lec.estado else None))
            anterior = lec.instante
        if lec.estado is not None and lec.estado.estado != ultimo_estado:
            e.estados.append(bateria.CambioEstado("mennekes", lec.instante, lec.estado.estado))
            ultimo_estado = lec.estado.estado
    s = sesion_14_11()
    if lecturas[-1].instante > s.fin:
        e.sesiones.append(bateria.SesionCargador(s.conector, s.id, s.inicio, s.fin, s.energia_wh, s.origen_fin))
    return e, lecturas[-1].instante


class ClienteLog:
    """Cliente del cargador que contesta con la línea del minuto actual del fichero.

    El histórico devuelve la sesión del 14/11 una vez terminada, como el cargador.
    """

    def __init__(self, lecturas: list[Lectura]) -> None:
        self.lecturas = lecturas
        self.i = 0
        self.base = "http://cargador-falso"

    @property
    def actual(self) -> Lectura:
        return self.lecturas[self.i]

    def estado_conector(self) -> EstadoConector:
        if self.actual.estado is None:
            raise ErrorCargador("sin respuesta (línea vacía del fichero)")
        return self.actual.estado

    def modulador(self) -> Modulador:
        if self.actual.modulador is None:
            raise ErrorCargador("sin respuesta (línea vacía del fichero)")
        return self.actual.modulador

    def historico(self) -> list[Sesion]:
        s = sesion_14_11()
        return [s] if self.actual.instante > s.fin else []
