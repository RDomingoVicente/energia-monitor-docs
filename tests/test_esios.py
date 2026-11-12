"""Fuente ESIOS: parser con respuestas reales, cliente HTTP, controles y planificación.

Las respuestas de tests/fixtures/esios son reales (api.esios.ree.es,
12/11/2026) y no contienen el token: la API no lo devuelve.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from energia_monitor import esios, planificador
from energia_monitor.config import Config
from energia_monitor.errores import ErrorAutenticacion, ErrorFuente
from energia_monitor.http import ClienteHttp
from energia_monitor.sources import crear_cliente_esios
from energia_monitor.sources.esios import ClienteEsios, parsear_indicador
from energia_monitor.tiempo import MADRID

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "esios"
PENINSULA = 8741
QH = timedelta(minutes=15)
H = timedelta(hours=1)
RESTRICCIONES = (793, 794, 2401)
PROCESOS_OS = (798, 799, 800, 802, 1366, 2389, 2392, 2395, 2398)


def fixture(nombre: str) -> bytes:
    return (FIXTURES / nombre).read_bytes()


def serie(indicador: int, dia: str = "20261110", resolucion: timedelta = QH):
    return parsear_indicador(fixture(f"{indicador}_{dia}.json"), indicador, PENINSULA, resolucion)


# --- Parser ----------------------------------------------------------------


def test_dia_cuartohorario() -> None:
    s = serie(793)
    assert len(s.valores) == 96
    assert s.valores[0].inicio == datetime(2026, 11, 9, 23, 0, tzinfo=UTC)
    assert s.valores[0].fin - s.valores[0].inicio == QH
    assert s.valores[-1].inicio == datetime(2026, 11, 10, 22, 45, tzinfo=UTC)
    assert s.valores[0].valor == D("14.15")
    assert all(isinstance(v.valor, D) for v in s.valores)
    assert s.publicado_en == datetime(2026, 11, 12, 7, 8, 20, tzinfo=UTC)


def test_cambio_de_hora_de_octubre_100_cuartos() -> None:
    s = serie(793, "20261025")
    assert len(s.valores) == 100
    assert s.valores[-1].fin - s.valores[0].inicio == timedelta(hours=25)


def test_cambio_de_hora_de_marzo_23_horas() -> None:
    s = serie(1871, "20260329", H)
    assert len(s.valores) == 23
    assert s.valores[0].inicio == datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
    assert s.valores[-1].fin == datetime(2026, 3, 29, 22, 0, tzinfo=UTC)


def test_filtra_la_zona() -> None:
    todas = parsear_indicador(fixture("1001_20261110_todas_zonas.json"), 1001, PENINSULA, H)
    solo = serie(1001, resolucion=H)
    assert len(todas.valores) == 24
    assert todas.valores == solo.valores
    # El PVPC incluye peajes y cargos: a las 00:00 (P3) es 181,88 €/MWh.
    assert solo.valores[0].valor == D("181.88")


def test_resolucion_distinta_de_la_esperada() -> None:
    with pytest.raises(ErrorFuente, match="resolución"):
        parsear_indicador(fixture("1871_20261110.json"), 1871, PENINSULA, QH)


def test_indicador_distinto_del_pedido() -> None:
    with pytest.raises(ErrorFuente, match="en lugar de 794"):
        parsear_indicador(fixture("793_20261110.json"), 794, PENINSULA, QH)


@pytest.mark.parametrize("cuerpo", [b"", b"{}", b'{"indicator": {"id": 793}}', b"<html>"])
def test_respuesta_no_reconocida(cuerpo: bytes) -> None:
    with pytest.raises(ErrorFuente):
        parsear_indicador(cuerpo, 793, PENINSULA, QH)


def test_tramo_repetido() -> None:
    datos = json.loads(fixture("793_20261110.json"))
    datos["indicator"]["values"].append(datos["indicator"]["values"][0])
    with pytest.raises(ErrorFuente, match="repetido"):
        parsear_indicador(json.dumps(datos).encode(), 793, PENINSULA, QH)


def test_sin_valores() -> None:
    datos = json.loads(fixture("793_20261110.json"))
    datos["indicator"]["values"] = []
    datos["indicator"]["values_updated_at"] = None
    s = parsear_indicador(json.dumps(datos).encode(), 793, PENINSULA, QH)
    assert s.valores == () and s.publicado_en is None


def test_los_fixtures_no_contienen_credenciales() -> None:
    for fichero in FIXTURES.iterdir():
        texto = fichero.read_text(encoding="utf-8").lower()
        for patron in ("x-api-key", "token", "authorization"):
            assert patron not in texto, fichero.name


# --- Identidades de los componentes (fixtures reales) ----------------------


def valores(indicador: int) -> dict[datetime, D]:
    return {v.inicio: v.valor for v in serie(indicador).valores}


@pytest.mark.parametrize(("total", "componentes"), [(10379, RESTRICCIONES), (10382, PROCESOS_OS)])
def test_componentes_suman_el_total(total: int, componentes: tuple[int, ...]) -> None:
    assert esios.discrepancias(valores(total), [valores(c) for c in componentes]) == []


def test_falta_un_componente_se_detecta() -> None:
    # Sin desvíos (799), el 10/11 no cuadra: es lo que avisaría de un componente nuevo.
    componentes = [valores(c) for c in PROCESOS_OS if c != 799]
    assert esios.discrepancias(valores(10382), componentes)


# --- Cliente --------------------------------------------------------------------


class Respuesta(io.BytesIO):
    def __enter__(self) -> Respuesta:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AbrirFalso:
    def __init__(self, respuestas: list[Any]) -> None:
        self.respuestas = respuestas
        self.peticiones: list[Any] = []

    def __call__(self, peticion: Any, timeout: float) -> Respuesta:
        self.peticiones.append(peticion)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return Respuesta(r)


def test_url_y_cabeceras_verificadas() -> None:
    abrir = AbrirFalso([fixture("793_20261110.json")])
    cliente = ClienteEsios("secreto-de-prueba", ClienteHttp(abrir=abrir, dormir=lambda _s: None))
    desde = datetime(2026, 11, 9, 23, tzinfo=UTC)
    s = cliente.serie(793, PENINSULA, QH, desde, desde + timedelta(days=1))
    assert len(s.valores) == 96
    (peticion,) = abrir.peticiones
    assert peticion.full_url == (
        "https://api.esios.ree.es/indicators/793"
        "?start_date=2026-11-09T23:00:00Z&end_date=2026-11-10T22:59:59Z&geo_ids[]=8741"
    )
    assert peticion.get_header("X-api-key") == "secreto-de-prueba"
    assert "secreto" not in peticion.full_url
    assert "secreto" not in repr(cliente)


def test_403_no_se_reintenta() -> None:
    error = HTTPError("https://api.esios.ree.es/indicators/793", 403, "Forbidden", {},  # type: ignore[arg-type]
                      io.BytesIO(fixture("error_403.json")))
    abrir = AbrirFalso([error, fixture("793_20261110.json")])
    cliente = ClienteEsios("x", ClienteHttp(abrir=abrir, dormir=lambda _s: None))
    desde = datetime(2026, 11, 9, 23, tzinfo=UTC)
    with pytest.raises(ErrorAutenticacion, match="HTTP 403"):
        cliente.serie(793, PENINSULA, QH, desde, desde + timedelta(days=1))
    assert len(abrir.peticiones) == 1


def test_valores_fuera_del_rango_pedido() -> None:
    abrir = AbrirFalso([fixture("793_20261110.json")])
    cliente = ClienteEsios("x", ClienteHttp(abrir=abrir))
    desde = datetime(2026, 11, 10, 0, tzinfo=UTC)
    with pytest.raises(ErrorFuente, match="fuera del rango"):
        cliente.serie(793, PENINSULA, QH, desde, desde + timedelta(hours=12))


def test_sin_token_no_hay_cliente() -> None:
    assert crear_cliente_esios(None) is None
    assert crear_cliente_esios("") is None
    assert isinstance(crear_cliente_esios("abc"), ClienteEsios)


# --- Descargas ----------------------------------------------------------------------


def test_rangos_contiguos() -> None:
    d = date(2026, 1, 1)
    fechas = [d + timedelta(days=i) for i in range(40)] + [date(2026, 3, 1), date(2026, 3, 3)]
    assert esios.rangos_contiguos(fechas) == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 9)),
        (date(2026, 3, 1), date(2026, 3, 1)),
        (date(2026, 3, 3), date(2026, 3, 3)),
    ]
    assert esios.rangos_contiguos([]) == []


class ClienteFalso:
    def __init__(self, fallar: dict[int, Exception] | None = None) -> None:
        self.pedidos: list[tuple[int, datetime, datetime]] = []
        self.fallar = fallar or {}

    def serie(self, indicador: int, geo_id: int, resolucion: timedelta, desde: datetime, hasta: datetime):
        self.pedidos.append((indicador, desde, hasta))
        if indicador in self.fallar:
            raise self.fallar[indicador]
        return serie(indicador, resolucion=resolucion)


IND = {i: esios.Indicador(i, PENINSULA, QH, "ssaa_componente", None) for i in (793, 794, 798)}


def test_descargar_sigue_tras_un_fallo_y_cuenta_revisiones() -> None:
    cliente = ClienteFalso({794: ErrorFuente("caído")})
    rango = [(date(2026, 11, 10), date(2026, 11, 10))]

    def guardar(_conn: object, s: Any) -> esios.Guardado:
        cambios = [esios.Cambio(s.valores[0].inicio, D(0), s.valores[0].valor)] if s.indicador == 798 else []
        return esios.Guardado(nuevos=len(s.valores) - len(cambios), cambios=cambios)

    resumen = esios.descargar(None, cliente, {IND[793]: rango, IND[794]: rango, IND[798]: rango},  # type: ignore[arg-type]
                              "prueba", guardar=guardar)
    assert [p[0] for p in cliente.pedidos] == [793, 794, 798]
    assert cliente.pedidos[0][1:] == (datetime(2026, 11, 9, 23, tzinfo=UTC), datetime(2026, 11, 10, 23, tzinfo=UTC))
    assert resumen.fallidos == [(794, date(2026, 11, 10), date(2026, 11, 10))]
    assert resumen.valores == 192 and resumen.cambios == 1
    assert dict(resumen.revisados) == {798: {date(2026, 11, 10)}}


def test_descargar_propaga_el_token_rechazado() -> None:
    cliente = ClienteFalso({793: ErrorAutenticacion("HTTP 403")})
    with pytest.raises(ErrorAutenticacion):
        esios.descargar(None, cliente, {IND[793]: [(date(2026, 11, 10),) * 2],  # type: ignore[arg-type]
                                        IND[794]: [(date(2026, 11, 10),) * 2]}, "prueba")
    assert len(cliente.pedidos) == 1


# --- Planificación y configuración ---------------------------------------------------


PLANES = {"omie": (time(13, 30), time(17, 30), time(20, 30)), "esios": (time(8, 30), time(21, 0), time(22, 0))}


@pytest.mark.parametrize(
    ("ahora_madrid", "esperada_madrid", "tareas"),
    [
        (datetime(2026, 11, 12, 7, 0), datetime(2026, 11, 12, 8, 30), ["esios"]),
        (datetime(2026, 11, 12, 9, 0), datetime(2026, 11, 12, 13, 30), ["omie"]),
        (datetime(2026, 11, 12, 20, 30), datetime(2026, 11, 12, 21, 0), ["esios"]),
        (datetime(2026, 11, 12, 21, 30), datetime(2026, 11, 12, 22, 0), ["esios"]),
        (datetime(2026, 11, 12, 23, 0), datetime(2026, 11, 13, 8, 30), ["esios"]),
    ],
)
def test_proximas_tareas(ahora_madrid: datetime, esperada_madrid: datetime, tareas: list[str]) -> None:
    siguiente, quien = planificador.proximas_tareas(ahora_madrid.replace(tzinfo=MADRID), PLANES)
    assert siguiente == esperada_madrid.replace(tzinfo=MADRID)
    assert quien == tareas


def test_proximas_tareas_coincidentes_y_sin_esios() -> None:
    a_la_vez = {"omie": (time(21, 0),), "esios": (time(21, 0),)}
    _, quien = planificador.proximas_tareas(datetime(2026, 11, 12, 12, tzinfo=MADRID), a_la_vez)
    assert quien == ["esios", "omie"]
    _, quien = planificador.proximas_tareas(datetime(2026, 11, 12, 7, tzinfo=MADRID),
                                            {"omie": PLANES["omie"], "esios": ()})
    assert quien == ["omie"]


def test_recarga_semanal_el_domingo_a_primera_hora() -> None:
    horarios = PLANES["esios"]
    assert planificador.es_recarga_semanal(datetime(2026, 11, 15, 8, 30, tzinfo=MADRID), horarios)
    assert not planificador.es_recarga_semanal(datetime(2026, 11, 15, 21, 0, tzinfo=MADRID), horarios)
    assert not planificador.es_recarga_semanal(datetime(2026, 11, 14, 8, 30, tzinfo=MADRID), horarios)


def test_token_rechazado_desactiva_esios() -> None:
    def tarea() -> None:
        raise ErrorAutenticacion("HTTP 403")

    cliente = object()
    assert planificador._tarea_esios(tarea, cliente, "prueba") is None  # type: ignore[arg-type]

    def fallo() -> None:
        raise ErrorFuente("caído")

    assert planificador._tarea_esios(fallo, cliente, "prueba") is cliente  # type: ignore[arg-type]


def test_config_esios() -> None:
    base = Config.desde_entorno({})
    assert base.esios_token is None
    assert base.esios_carga_desde == date(2026, 1, 1)
    assert base.esios_horarios == (time(8, 30), time(21, 0), time(22, 0))
    config = Config.desde_entorno({
        "ESIOS_TOKEN": "secreto-de-prueba",
        "ESIOS_CARGA_DESDE": "2026-06-01",
        "ESIOS_HORARIOS": "21:15, 09:00",
    })
    assert config.esios_token == "secreto-de-prueba"
    assert config.esios_carga_desde == date(2026, 6, 1)
    assert config.esios_horarios == (time(9, 0), time(21, 15))
    assert "secreto" not in repr(config)
