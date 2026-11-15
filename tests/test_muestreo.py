"""Muestreo del cargador sin base de datos ni red: intervalos y resiliencia."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from energia_monitor import muestreo
from energia_monitor.muestreo import Muestreador, intervalo_desde
from energia_monitor.viaris import ErrorCargador, EstadoConector, Modulador

T0 = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(("segundos", "esperado"), [
    (60, 60), (59.6, 60), (1, 1), (180, 180), (180.4, 180), (181, None), (600, None), (0, None), (-5, None),
])
def test_intervalo_desde(segundos: float, esperado: int | None) -> None:
    assert intervalo_desde(T0, T0 + timedelta(seconds=segundos)) == esperado


def test_primera_muestra_sin_intervalo() -> None:
    assert intervalo_desde(None, T0) is None


class ClienteFalso:
    def __init__(self) -> None:
        self.fallar: set[str] = set()
        self.llamadas: list[str] = []

    def _leer(self, nombre: str, valor: Any) -> Any:
        self.llamadas.append(nombre)
        if nombre in self.fallar:
            raise ErrorCargador(f"sin respuesta en {nombre}")
        return valor

    def modulador(self) -> Modulador:
        return self._leer("modulador", Modulador(Decimal(4300), Decimal(200), Decimal(4500)))

    def estado_conector(self) -> EstadoConector:
        return self._leer("evsm", EstadoConector(5, None, None, None))

    def historico(self) -> list:
        return self._leer("historico", [])


@pytest.fixture
def registro(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    llamadas: list[tuple] = []
    monkeypatch.setattr(muestreo, "guardar_muestra",
                        lambda conn, instante, m, estado: llamadas.append(("muestra", instante, estado)))
    monkeypatch.setattr(muestreo, "registrar_estado",
                        lambda conn, instante, e: llamadas.append(("estado", instante, e.estado)))
    monkeypatch.setattr(muestreo, "guardar_sesiones",
                        lambda conn, sesiones: llamadas.append(("sesiones", len(sesiones))) or (0, 0))
    monkeypatch.setattr(muestreo, "hay_sesiones_pendientes", lambda conn: False)
    monkeypatch.setattr(muestreo.bateria_servicio, "recalcular_sin_fallar",
                        lambda conn, motivo: llamadas.append(("bateria", motivo)))
    return llamadas


def reloj_desde(inicio: datetime) -> tuple[list[datetime], Any]:
    ahora = [inicio]
    return ahora, lambda: ahora[0]


def test_ciclo_completo_y_historico_cada_15_min(registro: list[tuple]) -> None:
    ahora, reloj = reloj_desde(T0.replace(microsecond=123456))
    cliente = ClienteFalso()
    m = Muestreador(cliente, reloj)  # type: ignore[arg-type]
    for _ in range(16):
        m.ciclo(conn=None)  # type: ignore[arg-type]
        ahora[0] += timedelta(seconds=60)
    assert cliente.llamadas.count("historico") == 2  # minuto 0 y minuto 15
    muestras = [r for r in registro if r[0] == "muestra"]
    assert len(muestras) == 16
    assert muestras[0][1] == T0  # instante sin microsegundos
    assert all(r[2] == 5 for r in muestras)


def test_fallo_del_modulador_no_detiene_el_ciclo(registro: list[tuple], caplog: pytest.LogCaptureFixture) -> None:
    _, reloj = reloj_desde(T0)
    cliente = ClienteFalso()
    cliente.fallar = {"modulador"}
    with caplog.at_level(logging.WARNING):
        Muestreador(cliente, reloj).ciclo(conn=None)  # type: ignore[arg-type]
    assert [r[0] for r in registro] == ["estado", "sesiones"]
    assert "fallo en modulador" in caplog.text


def test_fallo_de_evsm_guarda_la_muestra_sin_estado(registro: list[tuple]) -> None:
    _, reloj = reloj_desde(T0)
    cliente = ClienteFalso()
    cliente.fallar = {"evsm"}
    Muestreador(cliente, reloj).ciclo(conn=None)  # type: ignore[arg-type]
    assert registro[0] == ("muestra", T0, None)
    assert not any(r[0] == "estado" for r in registro)


def test_fallo_del_historico_se_reintenta_en_el_siguiente_ciclo(registro: list[tuple]) -> None:
    ahora, reloj = reloj_desde(T0)
    cliente = ClienteFalso()
    cliente.fallar = {"historico"}
    m = Muestreador(cliente, reloj)  # type: ignore[arg-type]
    m.ciclo(conn=None)  # type: ignore[arg-type]
    cliente.fallar = set()
    ahora[0] += timedelta(seconds=60)
    m.ciclo(conn=None)  # type: ignore[arg-type]
    assert cliente.llamadas.count("historico") == 2
    assert ("sesiones", 0) in registro


def test_cargador_caido_avisa_sin_repetir_cada_minuto(registro: list[tuple],
                                                      caplog: pytest.LogCaptureFixture) -> None:
    ahora, reloj = reloj_desde(T0)
    cliente = ClienteFalso()
    cliente.fallar = {"modulador", "evsm", "historico"}
    m = Muestreador(cliente, reloj)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            m.ciclo(conn=None)  # type: ignore[arg-type]
            ahora[0] += timedelta(seconds=60)
    assert caplog.text.count("fallo en modulador") == 1
    cliente.fallar = set()
    with caplog.at_level(logging.INFO):
        m.ciclo(conn=None)  # type: ignore[arg-type]
    assert "modulador recuperado tras 10 fallo(s)" in caplog.text


def test_sin_viaris_url_queda_en_espera_hasta_la_parada() -> None:
    stop = threading.Event()
    stop.set()

    def conectar() -> Any:
        raise AssertionError("sin VIARIS_URL no se conecta a la base de datos")

    muestreo.ejecutar(None, stop, conectar=conectar)


def test_recalcular_con_rango_invertido(monkeypatch: pytest.MonkeyPatch) -> None:
    from energia_monitor import __main__ as cli
    from energia_monitor import db

    monkeypatch.setattr(db, "conectar", lambda **_: pytest.fail("no debe conectar"))
    assert cli.main(["cargador", "recalcular", "--desde", "2026-11-05", "--hasta", "2026-11-04"]) == 2


# --- Disparadores del recálculo de la batería (E2.1, E2.4) ----------------------------------


def test_cambio_de_estado_recalcula_la_bateria(registro: list[tuple], monkeypatch: pytest.MonkeyPatch) -> None:
    _, reloj = reloj_desde(T0)
    monkeypatch.setattr(muestreo, "registrar_estado",
                        lambda conn, instante, e: registro.append(("estado", instante, e.estado)) or True)
    Muestreador(ClienteFalso(), reloj).ciclo(conn=None)  # type: ignore[arg-type]
    assert ("bateria", "cambio de estado del conector") in registro


def test_sin_cambio_de_estado_no_recalcula(registro: list[tuple]) -> None:
    _, reloj = reloj_desde(T0)
    Muestreador(ClienteFalso(), reloj).ciclo(conn=None)  # type: ignore[arg-type]
    assert not [r for r in registro if r[0] == "bateria"]


@pytest.mark.parametrize(("nuevas", "pendientes", "motivo"), [
    ((1, 0), False, "sesiones nuevas o modificadas"),
    ((0, 1), False, "sesiones nuevas o modificadas"),
    ((0, 0), True, "sesión pendiente de clasificar"),
    ((0, 0), False, None),
])
def test_historico_recalcula_con_sesiones_nuevas_o_pendientes(
        registro: list[tuple], monkeypatch: pytest.MonkeyPatch, nuevas: tuple[int, int], pendientes: bool,
        motivo: str | None) -> None:
    _, reloj = reloj_desde(T0)
    monkeypatch.setattr(muestreo, "guardar_sesiones", lambda conn, sesiones: nuevas)
    monkeypatch.setattr(muestreo, "hay_sesiones_pendientes", lambda conn: pendientes)
    Muestreador(ClienteFalso(), reloj).leer_historico(conn=None, instante=T0)  # type: ignore[arg-type]
    assert [r[1] for r in registro if r[0] == "bateria"] == ([motivo] if motivo else [])


def test_fallo_del_recalculo_no_detiene_el_muestreo(caplog: pytest.LogCaptureFixture) -> None:
    class ConexionRota:
        def execute(self, *args: object) -> None:
            raise RuntimeError("base de datos caída")

    with caplog.at_level(logging.ERROR):
        muestreo.bateria_servicio.recalcular_sin_fallar(ConexionRota(), "prueba")  # type: ignore[arg-type]
    assert "fallo al recalcular la batería (prueba)" in caplog.text
