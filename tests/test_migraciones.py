"""Las migraciones publicadas son inmutables (desde el commit 5d79781).

Si este test falla, no edites la migración: revierte el cambio y crea una
migración nueva. Al publicar una migración nueva, añade aquí su hash.
"""

import hashlib
from pathlib import Path

import pytest

from energia_monitor.db import ficheros_migracion

MIGRACIONES = Path(__file__).resolve().parents[1] / "db" / "migrations"

PUBLICADAS = {
    "0001_precios.sql": "c27c6477c7b7b34b3fcf94ac07824c15c5eb645cf5bd0da6b9f1601db8522924",
    "0002_contrato.sql": "09c5616cfa63e84a344915b706163b66ae65fe0abc5c74a536cc0512b2675aca",
    "0003_parametros_estimados.sql": "b97d08ad0e80cb15ba9f1114c80d91804bdf4dbd6790f8fd5048601108990c40",
    "0004_festivos_periodos.sql": "f1a71126b52a1dfadc255fc5b23537b68bd11159b2e79f95089bb56adf7b0219",
    "0005_vista_precio_final.sql": "1fc3f4b84a063a223729c8d7bdd69e342e1d90816972cc69c164de0dc642684a",
    "0006_grafana_ro.sql": "f785b72920a06129200ba9704c24c51b035cae07fdc20441f9b4416e12fb28a2",
    "0007_ssaa_vigencia_abierta.sql": "7e0c82bc0e991ff242620ebda1a6ce8c917c05a54692e24c4fcc34abd3442313",
}


@pytest.mark.parametrize(("nombre", "sha256"), sorted(PUBLICADAS.items()))
def test_migracion_publicada_sin_cambios(nombre: str, sha256: str) -> None:
    contenido = (MIGRACIONES / nombre).read_bytes()
    assert hashlib.sha256(contenido).hexdigest() == sha256, (
        f"{nombre} está publicada y no puede modificarse: crea una migración nueva"
    )


def test_todas_las_migraciones_registradas() -> None:
    assert {p.name for p in ficheros_migracion(MIGRACIONES)} == set(PUBLICADAS)
