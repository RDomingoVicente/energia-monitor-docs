"""Configuración por variables de entorno.

La conexión a PostgreSQL usa directamente las variables de libpq
(PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD) y no pasa por aquí.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time

HORARIOS_POR_DEFECTO = (time(13, 30), time(17, 30), time(20, 30))
# ESIOS: 08:30 tras la actualización diaria de REE (~08:10); 21:00 tras la
# publicación del PVPC y la previsión 1871 del día siguiente (~20:45) y 22:00
# como reintento.
HORARIOS_ESIOS_POR_DEFECTO = (time(8, 30), time(21, 0), time(22, 0))


def _horarios(texto: str) -> tuple[time, ...]:
    horarios = sorted({time.fromisoformat(parte.strip()) for parte in texto.split(",") if parte.strip()})
    if not horarios:
        raise ValueError("HORARIOS vacío")
    return tuple(horarios)


@dataclass(frozen=True)
class Config:
    price_source: str = "omie"
    # Primer día de la carga inicial de precios de mercado.
    carga_inicial_desde: date = date(2025, 10, 1)
    # Horas (Europe/Madrid) de la actualización diaria: la primera tras la
    # publicación de OMIE y el resto como reintentos.
    horarios: tuple[time, ...] = HORARIOS_POR_DEFECTO
    # Separación mínima entre peticiones a la fuente.
    intervalo_descargas_s: float = 1.0
    grafana_db_password: str | None = None
    # Sin token, el colector no usa ESIOS. Fuera del repr para no mostrarlo.
    esios_token: str | None = field(default=None, repr=False)
    esios_carga_desde: date = date(2026, 1, 1)
    esios_horarios: tuple[time, ...] = HORARIOS_ESIOS_POR_DEFECTO
    # Dirección del cargador (http://host). Sin ella, el muestreo queda desactivado.
    viaris_url: str | None = None

    @classmethod
    def desde_entorno(cls, env: Mapping[str, str] = os.environ) -> Config:
        def valor(nombre: str) -> str | None:
            v = env.get(nombre, "").strip()
            return v or None

        base = cls()
        desde = valor("CARGA_INICIAL_DESDE")
        horarios = valor("HORARIOS")
        intervalo = valor("INTERVALO_DESCARGAS_S")
        esios_desde = valor("ESIOS_CARGA_DESDE")
        esios_horarios = valor("ESIOS_HORARIOS")
        config = cls(
            price_source=valor("PRICE_SOURCE") or base.price_source,
            carga_inicial_desde=date.fromisoformat(desde) if desde else base.carga_inicial_desde,
            horarios=_horarios(horarios) if horarios else base.horarios,
            intervalo_descargas_s=float(intervalo) if intervalo else base.intervalo_descargas_s,
            grafana_db_password=valor("GRAFANA_DB_PASSWORD"),
            esios_token=valor("ESIOS_TOKEN"),
            esios_carga_desde=date.fromisoformat(esios_desde) if esios_desde else base.esios_carga_desde,
            esios_horarios=_horarios(esios_horarios) if esios_horarios else base.esios_horarios,
            viaris_url=valor("VIARIS_URL"),
        )
        if config.intervalo_descargas_s < 0:
            raise ValueError("INTERVALO_DESCARGAS_S no puede ser negativo")
        return config
