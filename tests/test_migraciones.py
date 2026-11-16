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
    "0008_origen_parametros.sql": "ad2bfe703acc763e6676bd02a9a405b061041277346fedf220758098a6b373b7",
    "0009_esios.sql": "6b999db8f2ac55395084f34da9928cecb7b911abb43cc263711d284b28e2c2ba",
    "0010_cargador.sql": "486f9b9db6d26fe8031336b4e1aeff305df1b5bc169990e55c82d3b730bb5440",
    "0011_energia_coste.sql": "13f63836764a3ff425656c2c1da2cb1b127dfb2a04e4ef9d95cea5221e5ea20d",
    "0012_precio_final_materializado.sql": "dfd9feb9f3b93f36c9c9fecd1e2a242b86a66305a1d8e03d64498fcdd4ca1ab6",
    "0013_energia_tramo.sql": "8b3be0b2eff22a5161ffe7660b2c09123d3e61b142279d1640ce525f61d7759c",
    "0014_bateria.sql": "64ea3b208719ce6e82ca4a4d08b6a129462ab76ffd1b5bb47c9acc6970cfea97",
    "0015_excepcion_salida.sql": "5e7f953ff5a88afabcc2f3645d50e5e88b63d57ad7c79a139f402046ba532122",
    "0016_formulario_modelo.sql": "fd8c0dd2393f9ae3ef4143d380d678d676f277dcfd8adb6e1229ead54ec3bf25",
    "0017_sesion_sin_muestreo.sql": "90e44f2c42f4f054884c61c5ec89a725086cc0f06a551eb5b9cb0b4af37e710c",
    "0018_evento_trayecto_partido.sql": "b6ee2147588392b4561a6ae5946a3431f08e06e9b18c1b0326d1d9659f0823ab",
}


@pytest.mark.parametrize(("nombre", "sha256"), sorted(PUBLICADAS.items()))
def test_migracion_publicada_sin_cambios(nombre: str, sha256: str) -> None:
    contenido = (MIGRACIONES / nombre).read_bytes()
    assert hashlib.sha256(contenido).hexdigest() == sha256, (
        f"{nombre} está publicada y no puede modificarse: crea una migración nueva"
    )


def test_todas_las_migraciones_registradas() -> None:
    assert {p.name for p in ficheros_migracion(MIGRACIONES)} == set(PUBLICADAS)
