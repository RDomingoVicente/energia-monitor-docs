"""Configuración por variables de entorno.

La conexión a PostgreSQL usa directamente las variables de libpq
(PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD) y no pasa por aquí.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time

HORARIOS_POR_DEFECTO = (time(13, 30), time(17, 30), time(20, 30))


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

    @classmethod
    def desde_entorno(cls, env: Mapping[str, str] = os.environ) -> Config:
        def valor(nombre: str) -> str | None:
            v = env.get(nombre, "").strip()
            return v or None

        base = cls()
        desde = valor("CARGA_INICIAL_DESDE")
        horarios = valor("HORARIOS")
        intervalo = valor("INTERVALO_DESCARGAS_S")
        config = cls(
            price_source=valor("PRICE_SOURCE") or base.price_source,
            carga_inicial_desde=date.fromisoformat(desde) if desde else base.carga_inicial_desde,
            horarios=_horarios(horarios) if horarios else base.horarios,
            intervalo_descargas_s=float(intervalo) if intervalo else base.intervalo_descargas_s,
            grafana_db_password=valor("GRAFANA_DB_PASSWORD"),
        )
        if config.intervalo_descargas_s < 0:
            raise ValueError("INTERVALO_DESCARGAS_S no puede ser negativo")
        return config
