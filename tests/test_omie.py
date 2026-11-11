from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from energia_monitor.http import USER_AGENT, ClienteHttp
from energia_monitor.sources.base import ErrorFuente
from energia_monitor.sources.omie import URL, FuenteOmie, parsear_marginalpdbc

FIXTURES = Path(__file__).parent / "fixtures" / "omie"


def leer_fixture(dia: date) -> str:
    return (FIXTURES / f"marginalpdbc_{dia:%Y%m%d}.1").read_text(encoding="latin-1")


def fichero_sintetico(dia: date, n: int) -> str:
    filas = [f"{dia.year};{dia.month:02d};{dia.day:02d};{i};{i}.5;{i}.25;" for i in range(1, n + 1)]
    return "MARGINALPDBC;\r\n" + "\r\n".join(filas) + "\r\n*\r\n"


def comprobar_contiguos(tramos, inicio: datetime, fin: datetime) -> None:
    assert tramos[0].inicio == inicio
    assert tramos[-1].fin == fin
    for a, b in zip(tramos, tramos[1:]):
        assert a.fin == b.inicio


# --- Parser ---------------------------------------------------------------


def test_dia_normal_cuartohorario() -> None:
    dia = date(2026, 10, 1)
    tramos = parsear_marginalpdbc(leer_fixture(dia), dia)
    assert len(tramos) == 96
    # 01/10/2026 00:00 CEST = 30/09/2026 22:00 UTC
    comprobar_contiguos(tramos, datetime(2026, 9, 30, 22, tzinfo=UTC), datetime(2026, 10, 1, 22, tzinfo=UTC))
    assert all(t.fin - t.inicio == timedelta(minutes=15) for t in tramos)
    # Primera fila del fichero: 2026;10;01;1;191.77;191.77;
    assert tramos[0].precio_eur_mwh == Decimal("191.77")
    assert tramos[-1].precio_eur_mwh == Decimal("171.38")


def test_cambio_de_hora_de_octubre_100_tramos() -> None:
    dia = date(2026, 10, 25)
    tramos = parsear_marginalpdbc(leer_fixture(dia), dia)
    assert len(tramos) == 100
    # 25 h: de 24/10 22:00 UTC (CEST) a 25/10 23:00 UTC (CET)
    comprobar_contiguos(tramos, datetime(2026, 10, 24, 22, tzinfo=UTC), datetime(2026, 10, 25, 23, tzinfo=UTC))


def test_cambio_de_hora_de_marzo_92_tramos() -> None:
    dia = date(2026, 3, 29)
    tramos = parsear_marginalpdbc(fichero_sintetico(dia, 92), dia)
    assert len(tramos) == 92
    comprobar_contiguos(tramos, datetime(2026, 3, 28, 23, tzinfo=UTC), datetime(2026, 3, 29, 22, tzinfo=UTC))


@pytest.mark.parametrize(("dia", "n"), [(date(2025, 6, 1), 24), (date(2025, 3, 30), 23), (date(2024, 10, 27), 25)])
def test_historico_horario(dia: date, n: int) -> None:
    tramos = parsear_marginalpdbc(fichero_sintetico(dia, n), dia)
    assert len(tramos) == n
    assert all(t.fin - t.inicio == timedelta(hours=1) for t in tramos)
    assert tramos[0].precio_eur_mwh == Decimal("1.25")  # columna de España


def test_mes_de_octubre_completo() -> None:
    tramos = []
    for d in range(1, 32):
        dia = date(2026, 10, d)
        tramos.extend(parsear_marginalpdbc(leer_fixture(dia), dia))
    assert len(tramos) == 2980
    media = sum(t.precio_eur_mwh for t in tramos) / len(tramos)
    assert round(media, 2) == Decimal("166.59")
    comprobar_contiguos(tramos, datetime(2026, 9, 30, 22, tzinfo=UTC), datetime(2026, 10, 31, 23, tzinfo=UTC))


@pytest.mark.parametrize(
    ("texto", "mensaje"),
    [
        ("<!DOCTYPE html><html></html>", "cabecera"),
        ("MARGINALPDBC;\n2026;10;01;1;1;1;\n", "línea final"),
        ("MARGINALPDBC;\n2026;10;02;1;1;1;\n*\n", "fila de 2026-10-02"),
        ("MARGINALPDBC;\n2026;10;01;2;1;1;\n*\n", "fuera de orden"),
        ("MARGINALPDBC;\n2026;10;01;1;abc;abc;\n*\n", "mal formada"),
        ("MARGINALPDBC;\n2026;10;01;1;1;\n*\n", "mal formada"),
    ],
)
def test_ficheros_invalidos(texto: str, mensaje: str) -> None:
    with pytest.raises(ErrorFuente, match=mensaje):
        parsear_marginalpdbc(texto, date(2026, 10, 1))


def test_numero_de_tramos_incoherente() -> None:
    dia = date(2026, 10, 1)
    with pytest.raises(ErrorFuente, match="no encaja"):
        parsear_marginalpdbc(fichero_sintetico(dia, 95), dia)


# --- Selección de versión -------------------------------------------------


class HttpFalso:
    def __init__(self, respuestas: dict[str, bytes]) -> None:
        self.respuestas = respuestas
        self.pedidas: list[str] = []

    def get(self, url: str) -> bytes | None:
        self.pedidas.append(url)
        return self.respuestas.get(url)


def test_usa_la_version_mas_alta() -> None:
    dia = date(2026, 10, 1)
    v1 = leer_fixture(dia).encode("latin-1")
    v2 = v1.replace(b"2026;10;01;1;191.77;191.77;", b"2026;10;01;1;191.77;150.00;")
    http = HttpFalso({URL.format(dia=dia, version=1): v1, URL.format(dia=dia, version=2): v2})
    precios = FuenteOmie(http).precios_dia(dia)
    assert precios is not None
    assert precios.version == 2
    assert precios.fuente == "omie"
    assert precios.tramos[0].precio_eur_mwh == Decimal("150.00")
    assert http.pedidas == [URL.format(dia=dia, version=v) for v in (1, 2, 3)]


def test_dia_no_publicado() -> None:
    assert FuenteOmie(HttpFalso({})).precios_dia(date(2026, 11, 13)) is None


def test_url_verificada() -> None:
    assert URL.format(dia=date(2026, 11, 12), version=1) == (
        "https://www.omie.es/es/file-download?parents=marginalpdbc&filename=marginalpdbc_20261112.1"
    )


# --- Cliente HTTP ---------------------------------------------------------


class RespuestaFalsa:
    def __init__(self, cuerpo: bytes) -> None:
        self.cuerpo = cuerpo

    def read(self) -> bytes:
        return self.cuerpo

    def __enter__(self) -> "RespuestaFalsa":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class AbridorFalso:
    def __init__(self, resultados: list[object]) -> None:
        self.resultados = resultados
        self.llamadas: list[tuple[object, float]] = []

    def __call__(self, peticion, timeout: float):
        self.llamadas.append((peticion, timeout))
        r = self.resultados.pop(0)
        if isinstance(r, Exception):
            raise r
        return RespuestaFalsa(r)  # type: ignore[arg-type]


def http_error(codigo: int) -> HTTPError:
    return HTTPError("https://x", codigo, "error", {}, None)  # type: ignore[arg-type]


def test_cliente_reintenta_con_espera_exponencial() -> None:
    esperas: list[float] = []
    abrir = AbridorFalso([URLError("caído"), http_error(503), b"ok"])
    cliente = ClienteHttp(timeout=7, reintentos=3, espera_inicial=5, dormir=esperas.append, abrir=abrir)
    assert cliente.get("https://x") == b"ok"
    assert esperas == [5, 10]
    peticion, timeout = abrir.llamadas[0]
    assert timeout == 7
    assert peticion.get_header("User-agent") == USER_AGENT


def test_cliente_404_es_none_sin_reintentos() -> None:
    esperas: list[float] = []
    abrir = AbridorFalso([http_error(404)])
    assert ClienteHttp(dormir=esperas.append, abrir=abrir).get("https://x") is None
    assert esperas == []


def test_cliente_agota_reintentos() -> None:
    esperas: list[float] = []
    abrir = AbridorFalso([TimeoutError(), TimeoutError(), TimeoutError()])
    with pytest.raises(ErrorFuente, match="3 intentos"):
        ClienteHttp(reintentos=3, espera_inicial=1, dormir=esperas.append, abrir=abrir).get("https://x")
    assert esperas == [1, 2]


def test_cliente_4xx_no_reintenta() -> None:
    abrir = AbridorFalso([http_error(403)])
    with pytest.raises(ErrorFuente, match="HTTP 403"):
        ClienteHttp(dormir=lambda _: None, abrir=abrir).get("https://x")
