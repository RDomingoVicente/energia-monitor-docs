"""Configuración del servicio formulario, desde el entorno (deploy/.env)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ConfigFormulario:
    host: str  # FORMULARIO_HOST: nombre:puerto con el que se llega (rdv-nas.tail1aac93.ts.net:8421)
    certificados: Path  # FORMULARIO_CERTIFICADOS: server.crt y server.key (montado en solo lectura)
    puerto: int  # FORMULARIO_PUERTO: puerto dentro del contenedor (8443)
    grafana_url: str | None  # FORMULARIO_GRAFANA_URL: enlace «Grafana» del menú; vacío, sin enlace
    cargador: str  # etiqueta del chip del cargador: host de VIARIS_URL (la página no lo consulta)

    @property
    def nombre(self) -> str:
        return self.host.rsplit(":", 1)[0]


def desde_entorno(env: Mapping[str, str] = os.environ) -> ConfigFormulario:
    def valor(nombre: str) -> str | None:
        v = env.get(nombre, "").strip()
        return v or None

    host = valor("FORMULARIO_HOST")
    if not host:
        raise ValueError("falta FORMULARIO_HOST (nombre:puerto del tailnet, p. ej. equipo.tailnet.ts.net:8421)")
    puerto = valor("FORMULARIO_PUERTO") or "8443"
    if not puerto.isdigit():
        raise ValueError(f"FORMULARIO_PUERTO no es un número: {puerto!r}")
    viaris = valor("VIARIS_URL")
    cargador = f"viaris {urlsplit(viaris).hostname}" if viaris and urlsplit(viaris).hostname else "cargador"
    return ConfigFormulario(host, Path(valor("FORMULARIO_CERTIFICADOS") or "/certs"), int(puerto),
                            valor("FORMULARIO_GRAFANA_URL"), cargador)
