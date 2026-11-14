"""Comprobaciones estáticas del dashboard de Grafana."""

import json
import re
from pathlib import Path

import pytest

DASHBOARD = json.loads(
    (Path(__file__).resolve().parents[1] / "grafana" / "energia-monitor.json").read_text(encoding="utf-8")
)
PANELES = {p["title"]: p for p in DASHBOARD["panels"]}


@pytest.mark.parametrize("titulo", ["Periodo 2.0TD ahora", "Componentes del tramo actual", "Estado del cargador"])
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


def test_identificadores_unicos() -> None:
    ids = [p["id"] for p in DASHBOARD["panels"]]
    assert len(ids) == len(set(ids))


FILA_COCHE = ["Gasto del coche", "Gasto en el rango", "Precio medio pagado", "Frente al PVPC",
              "Estado del cargador", "Gasto diario del coche (30 días)", "Reparto por periodo",
              "Sesiones recientes"]


def test_fila_coche_con_sus_paneles() -> None:
    fila = PANELES["Coche"]
    assert fila["type"] == "row" and not fila["collapsed"]
    debajo = [p for p in DASHBOARD["panels"] if p["gridPos"]["y"] > fila["gridPos"]["y"]]
    assert sorted(p["title"] for p in debajo) == sorted(FILA_COCHE)
    assert all(p["gridPos"]["y"] < fila["gridPos"]["y"] for p in DASHBOARD["panels"]
               if p["id"] < fila["id"])


@pytest.mark.parametrize("titulo", FILA_COCHE)
def test_paneles_del_coche_leen_las_vistas_nuevas(titulo: str) -> None:
    sql = PANELES[titulo]["targets"][0]["rawSql"]
    assert any(v in sql for v in ("v_coste_tramo", "v_sesion_coste", "cargador_muestra"))
    if "v_coste_tramo" in sql:
        assert "consumidor = 'coche'" in sql


def test_fila_coche_sin_v_precio_final() -> None:
    # v_precio_final se calcula entera en cada consulta: la fila lee la materializada.
    for titulo in FILA_COCHE:
        assert "v_precio_final" not in PANELES[titulo]["targets"][0]["rawSql"].replace("mv_precio_final", ""), titulo
    assert "FROM mv_precio_final" in PANELES["Precio medio pagado"]["targets"][0]["rawSql"]


def test_casa_marcada_sin_verificar() -> None:
    panel = PANELES["Estado del cargador"]
    assert "Casa, sin verificar" in panel["targets"][0]["rawSql"]
    assert "sin verificar" in panel["description"]


@pytest.mark.parametrize("titulo", ["Gasto del coche", "Gasto en el rango", "Gasto diario del coche (30 días)",
                                    "Sesiones recientes"])
def test_estimado_distinguido_de_lo_medido(titulo: str) -> None:
    sql = PANELES[titulo]["targets"][0]["rawSql"]
    assert "origen_energia" in sql and "estimad" in sql.lower()


@pytest.mark.parametrize("panel_id", [11, 12, 13])
def test_paneles_pvpc_en_una_sola_base(panel_id: int) -> None:
    # Ambos lados con IEE e IVA: nunca columnas sin impuestos en estos paneles.
    panel = next(p for p in DASHBOARD["panels"] if p["id"] == panel_id)
    sql = panel["targets"][0]["rawSql"]
    assert "con IEE e IVA" in panel["title"]
    assert "sin_impuestos" not in sql
    assert re.findall(r"\b(\w+)_eur_kwh\b", sql) and set(re.findall(r"\b(\w+)_eur_kwh\b", sql)) <= {
        "precio_final", "pvpc_final", "diferencia"}
