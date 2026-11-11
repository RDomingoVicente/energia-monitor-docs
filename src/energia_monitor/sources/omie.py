"""Fuente OMIE: ficheros diarios marginalpdbc (precio marginal del mercado diario).

Verificado contra www.omie.es (11/11/2026):
  GET https://www.omie.es/es/file-download?parents=marginalpdbc&filename=marginalpdbc_YYYYMMDD.V
  - Responde 302 a la misma ruta y después 200 con el fichero de texto.
  - Si la versión V no existe, 404.
  - Formato: cabecera "MARGINALPDBC;", filas "año;mes;día;periodo;precio_PT;precio_ES;"
    y una línea final "*". Precios en €/MWh con punto decimal.
  - Mercado cuartohorario: 96 periodos (92/100 en los cambios de hora).
    Históricos anteriores al 01/10/2025: horarios (24, o 23/25).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from energia_monitor.http import ClienteHttp
from energia_monitor.sources.base import ErrorFuente, PrecioTramo, PreciosDia
from energia_monitor.tiempo import limites_dia_utc

URL = "https://www.omie.es/es/file-download?parents=marginalpdbc&filename=marginalpdbc_{dia:%Y%m%d}.{version}"
CABECERA = "MARGINALPDBC"
RESOLUCIONES = (timedelta(minutes=15), timedelta(minutes=60))


class Descargador(Protocol):
    def get(self, url: str) -> bytes | None: ...


def parsear_marginalpdbc(texto: str, dia: date) -> tuple[PrecioTramo, ...]:
    """Interpreta un fichero marginalpdbc y devuelve sus tramos en UTC (precio España)."""
    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    if not lineas or not lineas[0].startswith(CABECERA):
        raise ErrorFuente(f"fichero OMIE de {dia} sin cabecera {CABECERA}")

    precios: list[Decimal] = []
    for linea in lineas[1:]:
        if linea == "*":
            break
        campos = linea.split(";")
        if len(campos) < 6:
            raise ErrorFuente(f"fila OMIE mal formada: {linea!r}")
        try:
            fecha = date(int(campos[0]), int(campos[1]), int(campos[2]))
            periodo = int(campos[3])
            precio = Decimal(campos[5].replace(",", "."))
        except (ValueError, InvalidOperation) as e:
            raise ErrorFuente(f"fila OMIE mal formada: {linea!r}") from e
        if fecha != dia:
            raise ErrorFuente(f"fila de {fecha} en el fichero de {dia}")
        if periodo != len(precios) + 1:
            raise ErrorFuente(f"periodo {periodo} fuera de orden en el fichero de {dia}")
        precios.append(precio)
    else:
        raise ErrorFuente(f"fichero OMIE de {dia} sin línea final '*'")

    inicio_dia, fin_dia = limites_dia_utc(dia)
    duracion_dia = fin_dia - inicio_dia
    for resolucion in RESOLUCIONES:
        if len(precios) * resolucion == duracion_dia:
            break
    else:
        raise ErrorFuente(
            f"{len(precios)} periodos en el fichero de {dia}: no encaja con un día de {duracion_dia}"
        )

    return tuple(
        PrecioTramo(
            inicio=inicio_dia + i * resolucion,
            fin=inicio_dia + (i + 1) * resolucion,
            precio_eur_mwh=precio,
        )
        for i, precio in enumerate(precios)
    )


class FuenteOmie:
    """Precios del mercado diario desde los ficheros públicos de OMIE (sin token)."""

    nombre = "omie"

    def __init__(self, http: Descargador | None = None, max_version: int = 9) -> None:
        self._http = http if http is not None else ClienteHttp()
        self._max_version = max_version

    def precios_dia(self, dia: date) -> PreciosDia | None:
        # Se usa la versión publicada más alta (.1, .2, ...): las siguientes
        # corrigen a las anteriores.
        version_final: int | None = None
        contenido_final: bytes | None = None
        for version in range(1, self._max_version + 1):
            contenido = self._http.get(URL.format(dia=dia, version=version))
            if contenido is None:
                break
            version_final, contenido_final = version, contenido
        if version_final is None or contenido_final is None:
            return None
        texto = contenido_final.decode("latin-1")
        return PreciosDia(
            dia=dia,
            fuente=self.nombre,
            version=version_final,
            tramos=parsear_marginalpdbc(texto, dia),
        )
