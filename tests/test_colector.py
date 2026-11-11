import logging
import threading
from datetime import UTC, date, datetime, time
from typing import Any

import pytest

from energia_monitor.colector import descargar_dias
from energia_monitor.config import Config
from energia_monitor.errores import ErrorFuente
from energia_monitor.http import Limitador
from energia_monitor.planificador import proxima_ejecucion
from energia_monitor.sources.base import PreciosDia
from energia_monitor.tiempo import MADRID

HORARIOS = (time(13, 30), time(17, 30), time(20, 30))


@pytest.mark.parametrize(
    ("ahora_madrid", "esperada_madrid"),
    [
        (datetime(2026, 11, 12, 9, 0), datetime(2026, 11, 12, 13, 30)),
        (datetime(2026, 11, 12, 13, 30), datetime(2026, 11, 12, 17, 30)),  # estrictamente posterior
        (datetime(2026, 11, 12, 18, 0), datetime(2026, 11, 12, 20, 30)),
        (datetime(2026, 11, 12, 21, 0), datetime(2026, 11, 13, 13, 30)),
        # Cambio de hora de octubre: 13:30 CET del 25/10 = 12:30 UTC.
        (datetime(2026, 10, 24, 22, 0), datetime(2026, 10, 25, 13, 30)),
    ],
)
def test_proxima_ejecucion(ahora_madrid: datetime, esperada_madrid: datetime) -> None:
    ahora = ahora_madrid.replace(tzinfo=MADRID).astimezone(UTC)
    siguiente = proxima_ejecucion(ahora, HORARIOS)
    assert siguiente == esperada_madrid.replace(tzinfo=MADRID)


def test_proxima_ejecucion_en_dia_de_25_horas() -> None:
    siguiente = proxima_ejecucion(datetime(2026, 10, 25, 0, 0, tzinfo=UTC), HORARIOS)
    assert siguiente.astimezone(UTC) == datetime(2026, 10, 25, 12, 30, tzinfo=UTC)


def test_limitador_espera_el_intervalo() -> None:
    ahora = [100.0]
    esperas: list[float] = []

    def dormir(s: float) -> None:
        esperas.append(s)
        ahora[0] += s

    limitador = Limitador(1.0, reloj=lambda: ahora[0], dormir=dormir)
    limitador.esperar()          # primera: sin espera
    ahora[0] += 0.25
    limitador.esperar()          # faltan 0,75 s
    ahora[0] += 5
    limitador.esperar()          # ya pasó el intervalo
    assert esperas == [0.75]


def test_config_por_defecto() -> None:
    config = Config.desde_entorno({})
    assert config == Config()
    assert config.carga_inicial_desde == date(2025, 10, 1)
    assert config.horarios == HORARIOS
    assert config.intervalo_descargas_s == 1.0
    assert config.grafana_db_password is None


def test_config_desde_entorno() -> None:
    config = Config.desde_entorno({
        "PRICE_SOURCE": "omie",
        "CARGA_INICIAL_DESDE": "2026-01-01",
        "HORARIOS": "20:30, 13:15",
        "INTERVALO_DESCARGAS_S": "2.5",
        "GRAFANA_DB_PASSWORD": "x",
    })
    assert config.carga_inicial_desde == date(2026, 1, 1)
    assert config.horarios == (time(13, 15), time(20, 30))
    assert config.intervalo_descargas_s == 2.5
    assert config.grafana_db_password == "x"


class FuenteFalsa:
    nombre = "falsa"

    def __init__(self, respuestas: dict[date, Any]) -> None:
        self.respuestas = respuestas
        self.pedidos: list[date] = []

    def precios_dia(self, dia: date) -> PreciosDia | None:
        self.pedidos.append(dia)
        r = self.respuestas.get(dia)
        if isinstance(r, Exception):
            raise r
        return r


def precios_vacios(dia: date) -> PreciosDia:
    return PreciosDia(dia=dia, fuente="falsa", version=1, tramos=())


def test_descargar_dias_resume_y_registra_avance(caplog: pytest.LogCaptureFixture) -> None:
    dias = [date(2026, 10, d) for d in range(1, 13)]
    respuestas: dict[date, Any] = {d: precios_vacios(d) for d in dias[:10]}
    respuestas[dias[10]] = ErrorFuente("caído")  # dias[11] sin publicar
    guardados: list[date] = []
    caplog.set_level(logging.INFO, logger="energia_monitor.colector")

    resumen = descargar_dias(None, FuenteFalsa(respuestas), dias, "carga inicial",  # type: ignore[arg-type]
                             guardar=lambda _c, p: guardados.append(p.dia))

    assert resumen.guardados == dias[:10] == guardados
    assert resumen.fallidos == [dias[10]]
    assert resumen.no_publicados == [dias[11]]
    mensajes = [r.getMessage() for r in caplog.records]
    assert "carga inicial: 10/12 días (83%), último 2026-10-10" in mensajes
    assert "carga inicial: 12/12 días (100%), último 2026-10-12" in mensajes


def test_descargar_dias_se_detiene_con_stop() -> None:
    stop = threading.Event()
    dias = [date(2026, 10, d) for d in range(1, 6)]

    def guardar(_c: object, p: PreciosDia) -> None:
        if p.dia == dias[1]:
            stop.set()

    fuente = FuenteFalsa({d: precios_vacios(d) for d in dias})
    resumen = descargar_dias(None, fuente, dias, "carga inicial", stop, guardar=guardar)  # type: ignore[arg-type]
    assert resumen.interrumpido
    assert fuente.pedidos == dias[:2]
