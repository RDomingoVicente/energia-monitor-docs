"""deploy/vigilancia.sh con un docker falso: deja pasar la salida y el código de la vigilancia y avisa una sola vez
cuando docker no puede ejecutarla (y otra al recuperarse)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "vigilancia.sh"
CABECERA = "energia-monitor · vigilancia"

DOCKER_FALSO = """#!/bin/sh
echo "$@" >> "$FALSO_DIR/argumentos"
cat "$FALSO_DIR/salida" 2>/dev/null
cat "$FALSO_DIR/errores" >&2 2>/dev/null
exit "$(cat "$FALSO_DIR/codigo")"
"""

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="deploy/vigilancia.sh no está en la imagen")


class Entorno:
    def __init__(self, raiz: Path) -> None:
        self.dir = raiz / "energia-monitor"
        (self.dir / "vigilancia").mkdir(parents=True)
        self.falso = raiz / "falso"
        self.falso.mkdir()
        docker = self.falso / "docker"
        docker.write_text(DOCKER_FALSO)
        docker.chmod(0o755)

    def ejecutar(self, codigo: int, salida: str = "", errores: str = "", *args: str) -> tuple[int, str]:
        (self.falso / "codigo").write_text(str(codigo))
        (self.falso / "salida").write_text(salida)
        (self.falso / "errores").write_text(errores)
        entorno = {**os.environ, "DOCKER": str(self.falso / "docker"), "VIGILANCIA_DIR": str(self.dir),
                   "FALSO_DIR": str(self.falso)}
        r = subprocess.run(["sh", str(SCRIPT), *args], env=entorno, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout

    @property
    def marca(self) -> Path:
        return self.dir / "vigilancia" / "docker-fallo"

    def argumentos(self) -> list[str]:
        return (self.falso / "argumentos").read_text().splitlines()


@pytest.fixture
def e(tmp_path: Path) -> Entorno:
    return Entorno(tmp_path)


def test_sin_cambios_y_con_cambios_pasan_tal_cual(e: Entorno) -> None:
    assert e.ejecutar(0, errores="[+] Creating 1/1\n") == (0, "")
    texto = f"{CABECERA} · 19/11/2026 10:00\n\nNuevos (1):\n- ERROR · Planificador parado\n"
    assert e.ejecutar(1, texto, "[+] Creating 1/1\n") == (1, texto)
    assert e.argumentos()[0] == "compose run --rm --no-deps -T vigilancia python -m energia_monitor vigilancia"


def test_prueba_se_pasa_a_la_vigilancia(e: Entorno) -> None:
    codigo, salida = e.ejecutar(1, f"{CABECERA} · prueba\n", "", "--prueba")
    assert codigo == 1 and salida.startswith(CABECERA)
    assert e.argumentos()[-1].endswith("vigilancia --prueba")


def test_fallo_de_docker_se_avisa_una_vez_y_al_recuperarse(e: Entorno) -> None:
    codigo, salida = e.ejecutar(125, "", "Error response from daemon: no such image\n")
    assert codigo == 1 and "no se pudo ejecutar (docker compose run, código 125)" in salida
    assert "no such image" in salida and e.marca.exists()
    assert e.ejecutar(125, "", "otra vez\n") == (0, "")
    codigo, salida = e.ejecutar(0)
    assert codigo == 1 and "vuelve a ejecutarse con normalidad" in salida and not e.marca.exists()
    assert e.ejecutar(0) == (0, "")


def test_un_1_sin_cabecera_es_un_fallo(e: Entorno) -> None:
    """docker compose también devuelve 1 en sus propios errores (servicio inexistente, .env mal...)."""
    codigo, salida = e.ejecutar(1, "", "no such service: vigilancia\n")
    assert codigo == 1 and "código 1" in salida and e.marca.exists()


def test_recuperacion_con_cambios_los_incluye(e: Entorno) -> None:
    e.ejecutar(125)
    codigo, salida = e.ejecutar(1, f"{CABECERA} · 19/11/2026 10:15\n\nNuevos (1):\n")
    assert codigo == 1 and "vuelve a ejecutarse" in salida and "Nuevos (1):" in salida
