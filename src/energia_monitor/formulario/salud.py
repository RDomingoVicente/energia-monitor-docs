"""Healthcheck del contenedor: GET /salud por HTTPS verificando el certificado real.

Se conecta a 127.0.0.1 pero valida el certificado con el nombre del tailnet
(FORMULARIO_HOST) y las CA del sistema (el certificado de Tailscale es de Let's
Encrypt). Un certificado caducado, con otro nombre o ausente hace fallar el TLS:
el servicio queda como no sano.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from dataclasses import dataclass


@dataclass(frozen=True)
class Resultado:
    ok: bool
    detalle: str


def comprobar(nombre: str, puerto: int, direccion: str = "127.0.0.1", cafile: str | None = None,
              espera_s: float = 5) -> Resultado:
    ctx = ssl.create_default_context(cafile=cafile)
    try:
        with socket.create_connection((direccion, puerto), timeout=espera_s) as crudo, \
                ctx.wrap_socket(crudo, server_hostname=nombre) as tls:
            conn = http.client.HTTPConnection(nombre, puerto, timeout=espera_s)
            conn.sock = tls
            conn.request("GET", "/salud", headers={"Host": f"{nombre}:{puerto}"})
            r = conn.getresponse()
            cuerpo = r.read().decode("utf-8", errors="replace")
    except ssl.SSLCertVerificationError as e:
        return Resultado(False, f"certificado no válido: {e.verify_message}")
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        return Resultado(False, f"sin conexión TLS: {e}")
    try:
        estado = json.loads(cuerpo).get("estado")
    except ValueError:
        estado = None
    if r.status == 200 and estado == "ok":
        return Resultado(True, cuerpo)
    return Resultado(False, f"HTTP {r.status}: {cuerpo[:300]}")
