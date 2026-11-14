"""Comprobaciones estáticas del dashboard de Grafana."""

import json
import re
from pathlib import Path

import pytest

GRAFANA = Path(__file__).resolve().parents[1] / "grafana"
DASHBOARDS = {f.name: json.loads(f.read_text(encoding="utf-8")) for f in sorted(GRAFANA.glob("*.json"))}
PRECIOS = DASHBOARDS["energia-precios.json"]
COCHE = DASHBOARDS["energia-coche.json"]
TODOS_LOS_PANELES = [p for d in DASHBOARDS.values() for p in d["panels"]]
PANELES = {p["title"]: p for p in TODOS_LOS_PANELES}


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
    for panel in TODOS_LOS_PANELES:
        assert panel["datasource"]["type"] == "grafana-postgresql-datasource"


@pytest.mark.parametrize("nombre", DASHBOARDS)
def test_identificadores_unicos(nombre: str) -> None:
    ids = [p["id"] for p in DASHBOARDS[nombre]["panels"]]
    assert len(ids) == len(set(ids))


# --- Un dashboard por tema, enlazados entre sí (E2.10) --------------------------------------

PANELES_PRECIOS = ["Precio final ahora", "OMIE ahora", "Periodo 2.0TD ahora", "Precio final medio",
                   "Precio final (€/kWh)", "OMIE (€/MWh)", "Horas más baratas (próximas 36 h)",
                   "Precio final: media diaria (€/kWh)", "Mes en curso (media hasta ahora)",
                   "Componentes del tramo actual",
                   "PVPC 2.0TD frente a mi precio (€/kWh, ambos con IEE e IVA)",
                   "PVPC: mes en curso (€/kWh, ambos con IEE e IVA)",
                   "Diferencia diaria con el PVPC (€/kWh, ambos con IEE e IVA)",
                   "Estado de los componentes (tramo actual)", "Origen de SSAA por día"]

FILA_COCHE = ["Gasto del coche", "Gasto en el rango", "Precio medio pagado", "Frente al PVPC",
              "Estado del cargador", "Gasto diario del coche (30 días)", "Reparto por periodo",
              "Sesiones recientes"]


def test_dashboards_por_tema() -> None:
    assert set(DASHBOARDS) == {"energia-precios.json", "energia-coche.json"}
    # Precios conserva el uid del dashboard original: Grafana lo sobrescribe al importarlo.
    assert (PRECIOS["uid"], PRECIOS["title"]) == ("energia-monitor", "Energía: precios")
    assert (COCHE["uid"], COCHE["title"]) == ("energia-coche", "Energía: coche")
    uids = [d["uid"] for d in DASHBOARDS.values()]
    assert len(uids) == len(set(uids))


def test_paneles_repartidos_sin_perder_ninguno() -> None:
    assert [p["title"] for p in PRECIOS["panels"]] == PANELES_PRECIOS
    assert [p["title"] for p in COCHE["panels"]] == FILA_COCHE
    # Mismos ids que en el dashboard único (los cita la documentación) y sin filas.
    assert [p["id"] for p in PRECIOS["panels"]] == list(range(1, 16))
    assert [p["id"] for p in COCHE["panels"]] == list(range(17, 25))
    assert not [p for p in TODOS_LOS_PANELES if p["type"] == "row"]
    assert min(p["gridPos"]["y"] for p in COCHE["panels"]) == 0


@pytest.mark.parametrize("nombre", DASHBOARDS)
def test_enlazados_por_etiqueta_con_rango_y_variables(nombre: str) -> None:
    d = DASHBOARDS[nombre]
    assert "energia" in d["tags"]
    enlaces = [e for e in d["links"] if e["type"] == "dashboards"]
    assert len(enlaces) == 1
    assert enlaces[0]["tags"] == ["energia"]
    assert enlaces[0]["keepTime"] and enlaces[0]["includeVars"]


@pytest.mark.parametrize("nombre", DASHBOARDS)
def test_mismas_variables_rango_y_zona(nombre: str) -> None:
    # includeVars solo lleva las variables que existen con el mismo nombre en el destino.
    d = DASHBOARDS[nombre]
    assert [v["name"] for v in d["templating"]["list"]] == ["ds", "contrato", "fuente"]
    assert d["time"] == {"from": "now/d", "to": "now+1d/d"}
    assert d["timezone"] == "Europe/Madrid"


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
    # Durante la carga homePower = 0: la casa sale vacía, no 0.
    assert "CASE WHEN evse_w > 0 THEN NULL ELSE home_w END" in panel["targets"][0]["rawSql"]
    assert "total_w - evse_w" not in panel["targets"][0]["rawSql"]


@pytest.mark.parametrize("titulo", ["Gasto del coche", "Gasto en el rango", "Gasto diario del coche (30 días)",
                                    "Sesiones recientes"])
def test_estimado_distinguido_de_lo_medido(titulo: str) -> None:
    sql = PANELES[titulo]["targets"][0]["rawSql"]
    assert "origen_energia" in sql and "estimad" in sql.lower()


@pytest.mark.parametrize("panel_id", [11, 12, 13])
def test_paneles_pvpc_en_una_sola_base(panel_id: int) -> None:
    # Ambos lados con IEE e IVA: nunca columnas sin impuestos en estos paneles.
    panel = next(p for p in PRECIOS["panels"] if p["id"] == panel_id)
    sql = panel["targets"][0]["rawSql"]
    assert "con IEE e IVA" in panel["title"]
    assert "sin_impuestos" not in sql
    assert re.findall(r"\b(\w+)_eur_kwh\b", sql) and set(re.findall(r"\b(\w+)_eur_kwh\b", sql)) <= {
        "precio_final", "pvpc_final", "diferencia"}


@pytest.mark.parametrize("panel", [p for p in TODOS_LOS_PANELES if p["type"] == "timeseries"],
                         ids=lambda p: str(p["id"]))
def test_grafica_temporal_sin_filtro_de_rango_fija_su_eje(panel: dict) -> None:
    # El eje de una gráfica temporal es el rango del selector (hoy, por defecto): si la consulta
    # calcula su propio periodo, el panel debe fijar el suyo o los días anteriores quedan fuera.
    sql = " ".join(t.get("rawSql", "") for t in panel["targets"])
    if "$__timeFilter" not in sql:
        assert panel.get("timeFrom"), panel["title"]


def test_gasto_diario_con_eje_de_30_dias() -> None:
    assert PANELES["Gasto diario del coche (30 días)"]["timeFrom"] == "30d"
