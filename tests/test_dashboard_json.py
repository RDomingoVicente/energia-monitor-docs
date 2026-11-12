"""Comprobaciones estáticas del dashboard de Grafana."""

import json
import re
from pathlib import Path

import pytest

DASHBOARD = json.loads(
    (Path(__file__).resolve().parents[1] / "grafana" / "energia-monitor.json").read_text(encoding="utf-8")
)
PANELES = {p["title"]: p for p in DASHBOARD["panels"]}


@pytest.mark.parametrize("titulo", ["Periodo 2.0TD ahora", "Componentes del tramo actual"])
def test_paneles_stat_de_texto_muestran_campos_de_texto(titulo: str) -> None:
    # Sin "/.*/" el panel stat descarta los campos de texto y muestra "—".
    assert PANELES[titulo]["options"]["reduceOptions"]["fields"] == "/.*/"


def test_tabla_de_horas_baratas_cabe_entera() -> None:
    panel = PANELES["Horas más baratas (próximas 36 h)"]
    filas = int(re.search(r"LIMIT (\d+)", panel["targets"][0]["rawSql"]).group(1))
    # Cabecera + filas "sm" (36 px) + título, en unidades de rejilla de ~38 px.
    assert panel["gridPos"]["h"] * 38 >= 32 + 36 * (filas + 1) + 16


def test_datasource_postgresql_de_grafana_10() -> None:
    for panel in DASHBOARD["panels"]:
        assert panel["datasource"]["type"] == "grafana-postgresql-datasource"


@pytest.mark.parametrize("panel_id", [11, 12, 13])
def test_paneles_pvpc_en_una_sola_base(panel_id: int) -> None:
    # Ambos lados con IEE e IVA: nunca columnas sin impuestos en estos paneles.
    panel = next(p for p in DASHBOARD["panels"] if p["id"] == panel_id)
    sql = panel["targets"][0]["rawSql"]
    assert "con IEE e IVA" in panel["title"]
    assert "sin_impuestos" not in sql
    assert re.findall(r"\b(\w+)_eur_kwh\b", sql) and set(re.findall(r"\b(\w+)_eur_kwh\b", sql)) <= {
        "precio_final", "pvpc_final", "diferencia"}
