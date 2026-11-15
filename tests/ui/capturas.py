"""Capturas de cada pantalla junto a su maqueta, en 1280×900 y 390×844 (formulario-maquetas.md, 9).

Solo en local, para revisar a ojo que la implementación reproduce las maquetas:
    docker build -f tests/ui/Dockerfile --target capturas --output type=local,dest=capturas .
Deja en capturas/ un PNG por pantalla y tamaño (NN-pantalla-tamano-impl.png y -maqueta.png).
El servidor HTTPS real con el certificado de pruebas y los datos de datos_falsos.py, sin base de datos.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import datos_falsos

from energia_monitor.formulario.app import App, ConfigApp
from energia_monitor.formulario.servidor import crear_servidor

RAIZ = Path(__file__).resolve().parents[2]
TLS = RAIZ / "tests" / "fixtures" / "tls"
MAQUETAS = RAIZ / "docs" / "maquetas"
PUERTO = 18444
# (prefijo de la maqueta, ruta en la implementación)
PANTALLAS = [("01a-inicio-leido", "/"), ("02-excepciones", "/excepciones"), ("03-horario", "/horario"),
             ("04-historial", "/historial"), ("05-parametros", "/parametros"), ("06-ayuda", "/ayuda"),
             ("07-avisos", "/avisos"), ("08-carga-externa", "/carga-externa")]
TAMANOS = {"escritorio": (1280, 900), "movil": (390, 844)}


def main(destino: Path) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    mp = pytest.MonkeyPatch()
    datos_falsos.instalar(mp)
    certs = Path(__file__).resolve().parent / ".certs"
    certs.mkdir(exist_ok=True)
    (certs / "server.crt").write_bytes((TLS / "valido.crt").read_bytes())
    (certs / "server.key").write_bytes((TLS / "servidor.key").read_bytes())
    app = App(ConfigApp(f"127.0.0.1:{PUERTO}", "https://grafana.ejemplo/d/energia-bateria", "viaris 192.168.1.34"),
              datos_falsos.ConexionFalsa, lambda: datos_falsos.AHORA, migracion_esperada="0000")  # type: ignore[arg-type]
    srv = crear_servidor(app, certs, PUERTO, "127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for maqueta, ruta in PANTALLAS:
                for tam, (ancho, alto) in TAMANOS.items():
                    ctx = b.new_context(viewport={"width": ancho, "height": alto}, ignore_https_errors=True)
                    p = ctx.new_page()
                    p.goto(f"https://127.0.0.1:{PUERTO}{ruta}")
                    p.screenshot(path=destino / f"{maqueta}-{tam}-impl.png", full_page=True)
                    html = MAQUETAS / f"{maqueta}-{tam}.html"
                    if html.exists():
                        p.goto(html.as_uri())
                        p.screenshot(path=destino / f"{maqueta}-{tam}-maqueta.png", full_page=True)
                    ctx.close()
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()
        mp.undo()


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("capturas"))
