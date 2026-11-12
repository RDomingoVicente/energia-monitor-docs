"""Cliente de la API de ESIOS (REE): series por indicador.

Verificado contra https://api.esios.ree.es (12/11/2026):
  GET /indicators/{id}?start_date=AAAA-MM-DDTHH:MM:SSZ&end_date=...Z&geo_ids[]=8741
  - Autenticación con la cabecera x-api-key (también vale
    Authorization: Token token="..."). Sin token, el catálogo /indicators
    responde 403 {"Status":403,"message":"Forbidden"}.
  - start_date y end_date en UTC con sufijo Z, ambos extremos incluidos.
  - Respuesta: {"indicator": {"id", "name", "tiempo": [{"name": "Quince minutos" | "Hora"}],
    "values_updated_at": "2026-11-12T08:08:20.000+01:00",
    "values": [{"value", "datetime", "datetime_utc", "geo_id", "geo_name", ...}]}}
  - Valores en €/MWh con 2 decimales; geo_id 8741 = Península.

Los indicadores que se descargan están en la tabla esios_indicador (migración
0009), no en el código.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from energia_monitor.errores import ErrorFuente

URL_BASE = "https://api.esios.ree.es"
ACCEPT = "application/json; application/vnd.esios-api-v1+json"
RESOLUCIONES = {"Quince minutos": timedelta(minutes=15), "Hora": timedelta(hours=1)}


class Descargador(Protocol):
    def get(self, url: str, cabeceras: Mapping[str, str] | None = None) -> bytes | None: ...


@dataclass(frozen=True)
class ValorEsios:
    """Valor de un tramo [inicio, fin) en UTC, en €/MWh."""

    inicio: datetime
    fin: datetime
    valor: Decimal


@dataclass(frozen=True)
class SerieEsios:
    indicador: int
    geo_id: int
    # values_updated_at de REE; None si la respuesta no trae valores.
    publicado_en: datetime | None
    valores: tuple[ValorEsios, ...]


def _instante(texto: str) -> datetime:
    instante = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    if instante.tzinfo is None:
        raise ErrorFuente(f"fecha sin zona horaria en ESIOS: {texto!r}")
    return instante.astimezone(UTC)


def parsear_indicador(datos: bytes, indicador: int, geo_id: int, resolucion: timedelta) -> SerieEsios:
    """Interpreta la respuesta de /indicators/{id} y se queda con la zona geo_id."""
    try:
        # parse_float=Decimal: sin pasar por float.
        cuerpo: Any = json.loads(datos, parse_float=Decimal)
        ind = cuerpo["indicator"]
        valores_json = ind["values"]
    except (ValueError, KeyError, TypeError) as e:
        raise ErrorFuente(f"respuesta de ESIOS no reconocida para el indicador {indicador}") from e
    if ind.get("id") != indicador:
        raise ErrorFuente(f"ESIOS devolvió el indicador {ind.get('id')} en lugar de {indicador}")
    tiempos = {t.get("name") for t in ind.get("tiempo") or []}
    resoluciones = {RESOLUCIONES.get(t) for t in tiempos}
    if resoluciones != {resolucion}:
        raise ErrorFuente(f"indicador {indicador}: resolución {sorted(map(str, tiempos))} y se esperaba {resolucion}")

    valores: dict[datetime, ValorEsios] = {}
    for v in valores_json:
        if v.get("geo_id") != geo_id:
            continue
        try:
            inicio = _instante(v["datetime_utc"])
            valor = Decimal(v["value"])
        except (KeyError, TypeError, ValueError, ArithmeticError) as e:
            raise ErrorFuente(f"valor de ESIOS mal formado en el indicador {indicador}: {v!r}") from e
        if inicio in valores:
            raise ErrorFuente(f"indicador {indicador}: tramo {inicio.isoformat()} repetido")
        valores[inicio] = ValorEsios(inicio, inicio + resolucion, valor)

    publicado = ind.get("values_updated_at")
    if valores and not publicado:
        raise ErrorFuente(f"indicador {indicador} sin values_updated_at")
    return SerieEsios(
        indicador=indicador,
        geo_id=geo_id,
        publicado_en=_instante(publicado) if publicado else None,
        valores=tuple(valores[k] for k in sorted(valores)),
    )


def _fecha_api(instante: datetime) -> str:
    return instante.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClienteEsios:
    """Descarga de series de ESIOS. El token solo viaja en la cabecera x-api-key."""

    def __init__(self, token: str, http: Descargador) -> None:
        if not token:
            raise ValueError("ESIOS_TOKEN vacío")
        self._token = token
        self._http = http

    def __repr__(self) -> str:
        return "ClienteEsios(token=***)"

    @staticmethod
    def url(indicador: int, geo_id: int, desde: datetime, hasta: datetime) -> str:
        """URL de los tramos que empiezan en [desde, hasta)."""
        return (
            f"{URL_BASE}/indicators/{indicador}"
            f"?start_date={_fecha_api(desde)}"
            f"&end_date={_fecha_api(hasta - timedelta(seconds=1))}"
            f"&geo_ids[]={geo_id}"
        )

    def serie(self, indicador: int, geo_id: int, resolucion: timedelta,
              desde: datetime, hasta: datetime) -> SerieEsios:
        cuerpo = self._http.get(
            self.url(indicador, geo_id, desde, hasta),
            cabeceras={"x-api-key": self._token, "Accept": ACCEPT},
        )
        if cuerpo is None:
            raise ErrorFuente(f"ESIOS: indicador {indicador} no encontrado (404)")
        serie = parsear_indicador(cuerpo, indicador, geo_id, resolucion)
        fuera = [v for v in serie.valores if not desde <= v.inicio < hasta]
        if fuera:
            raise ErrorFuente(f"indicador {indicador}: {len(fuera)} tramo(s) fuera del rango pedido")
        return serie
