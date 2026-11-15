"""Comprobaciones estáticas del dashboard de Grafana."""

import json
import re
from pathlib import Path

import pytest

GRAFANA = Path(__file__).resolve().parents[1] / "grafana"
DASHBOARDS = {f.name: json.loads(f.read_text(encoding="utf-8")) for f in sorted(GRAFANA.glob("*.json"))}
PRECIOS = DASHBOARDS["energia-precios.json"]
COCHE = DASHBOARDS["energia-coche.json"]
BATERIA = DASHBOARDS["energia-bateria.json"]
TODOS_LOS_PANELES = [p for d in DASHBOARDS.values() for p in d["panels"]]
PANELES = {p["title"]: p for p in TODOS_LOS_PANELES}


@pytest.mark.parametrize("titulo", ["Periodo 2.0TD ahora", "Componentes del tramo actual", "Estado del cargador",
                                    "Nivel ahora", "Próxima salida"])
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

PANELES_BATERIA = ["Nivel ahora", "Próxima salida", "Objetivo de salida", "Energía necesaria", "Avisos activos",
                   "Nivel de batería (7 días)", "Acierto de la estimación (30 días)", "Estimado frente a leído (7 días)",
                   "Próximas salidas (7 días)", "Trayectos recientes", "Fuera de casa en el rango",
                   "Cargas externas (90 días)"]


def test_dashboards_por_tema() -> None:
    assert set(DASHBOARDS) == {"energia-precios.json", "energia-coche.json", "energia-bateria.json"}
    # Precios conserva el uid del dashboard original: Grafana lo sobrescribe al importarlo.
    assert (PRECIOS["uid"], PRECIOS["title"]) == ("energia-monitor", "Energía: precios")
    assert (COCHE["uid"], COCHE["title"]) == ("energia-coche", "Energía: coche")
    # El enlace «Grafana» del formulario apunta a /d/energia-bateria.
    assert (BATERIA["uid"], BATERIA["title"]) == ("energia-bateria", "Energía: batería")
    uids = [d["uid"] for d in DASHBOARDS.values()]
    assert len(uids) == len(set(uids))


def test_paneles_repartidos_sin_perder_ninguno() -> None:
    assert [p["title"] for p in PRECIOS["panels"]] == PANELES_PRECIOS
    assert [p["title"] for p in COCHE["panels"]] == FILA_COCHE
    # Mismos ids que en el dashboard único (los cita la documentación) y sin filas.
    assert [p["id"] for p in PRECIOS["panels"]] == list(range(1, 16))
    assert [p["id"] for p in COCHE["panels"]] == list(range(17, 25))
    assert [p["title"] for p in BATERIA["panels"]] == PANELES_BATERIA
    assert [p["id"] for p in BATERIA["panels"]] == list(range(25, 37))
    assert min(p["gridPos"]["y"] for p in BATERIA["panels"]) == 0
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


# --- Energía: batería (paso 4) --------------------------------------------------------------------

ESTILO = (Path(__file__).resolve().parents[1] / "src" / "energia_monitor" / "formulario" / "estatico"
          / "estilo.css").read_text(encoding="utf-8")
FUENTES_BATERIA = {"bateria_nivel", "bateria_sesion", "bateria_lectura", "bateria_trayecto", "bateria_carga_externa",
                   "v_bateria_estado", "v_bateria_salida", "v_bateria_lectura_historial", "v_aviso",
                   "v_coste_carga_externa"}


def _sql(panel_o_anotacion: dict) -> str:
    targets = panel_o_anotacion.get("targets") or [panel_o_anotacion["target"]]
    return "\n".join(t["rawSql"] for t in targets)


def _fuentes(sql: str) -> set[str]:
    sql = re.sub(r"extract\(\w+ FROM", "", sql)  # extract(isodow FROM ...) no es una tabla
    return set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)\b(?!\s*\()", sql)) - {"puntos", "p", "n", "x", "d", "lim",
                                                                                    "casa", "fuera", "desde"}


@pytest.mark.parametrize("objeto", BATERIA["panels"] + BATERIA["annotations"]["list"],
                         ids=lambda o: o.get("title") or o["name"])
def test_bateria_solo_lee_las_vistas_de_bateria(objeto: dict) -> None:
    sql = _sql(objeto)
    assert _fuentes(sql) <= FUENTES_BATERIA, _fuentes(sql)
    # Nada que escriba o recalcule: grafana_ro no puede ejecutarlo y el panel fallaría.
    for funcion in ("recalcular_bateria", "actualizar_avisos", "bateria_estimar_trayecto"):
        assert funcion not in sql


def _hex_css(variable: str) -> str:
    return re.search(rf"--{variable}: (#[0-9A-F]{{6}})", ESTILO).group(1)


def test_confianza_con_los_colores_del_formulario() -> None:
    panel = PANELES["Nivel ahora"]
    override = next(o for o in panel["fieldConfig"]["overrides"] if o["matcher"]["options"] == "Confianza")
    mapeo = next(p for p in override["properties"] if p["id"] == "mappings")["value"][0]["options"]
    # .tag.leido (--ok), .tag.leido_carga (--acento), .tag.estimado (--aviso).
    assert {k: (v["text"], v["color"]) for k, v in mapeo.items()} == {
        "leido": ("LEÍDO", _hex_css("ok")), "leido_carga": ("LEÍDO + CARGA", _hex_css("acento")),
        "estimado": ("ESTIMADO", _hex_css("aviso"))}


def test_anotaciones_regiones_y_puntos_solo_en_la_grafica_del_nivel() -> None:
    nivel = PANELES["Nivel de batería (7 días)"]["id"]
    anotaciones = {a["name"]: a for a in BATERIA["annotations"]["list"]}
    assert set(anotaciones) == {"Cargas en casa", "Trayectos", "Cargas externas y carga completa"}
    for nombre, a in anotaciones.items():
        assert a["filter"] == {"exclude": False, "ids": [nivel]}, nombre
        regiones = "timeend" in a["target"]["rawSql"]
        # Sesiones y trayectos: región de inicio a fin; cargas externas y carga completa: puntos.
        assert regiones == (nombre in ("Cargas en casa", "Trayectos")), nombre


@pytest.mark.parametrize("titulo", ["Nivel de batería (7 días)", "Estimado frente a leído (7 días)"])
def test_graficas_de_bateria_fijas_a_7_dias(titulo: str) -> None:
    # Rango común «hoy y mañana»; estas dos gráficas fijan el suyo con Relative time.
    assert PANELES[titulo]["timeFrom"] == "7d"


def test_lecturas_como_puntos_en_la_grafica_del_nivel() -> None:
    panel = PANELES["Nivel de batería (7 días)"]
    lectura = next(o for o in panel["fieldConfig"]["overrides"] if o["matcher"]["options"] == "Lectura (%)")
    assert {"id": "custom.drawStyle", "value": "points"} in lectura["properties"]
    assert "FROM bateria_lectura" in _sql(panel) and "anulada_en IS NULL" in _sql(panel)


# --- Coste fuera de casa en «Energía: coche» -------------------------------------------------------


@pytest.mark.parametrize("titulo", ["Gasto del coche", "Gasto en el rango", "Precio medio pagado",
                                    "Gasto diario del coche (30 días)"])
def test_gasto_del_coche_con_casa_y_fuera(titulo: str) -> None:
    sql = _sql(PANELES[titulo])
    assert "v_coste_tramo" in sql and "v_coste_carga_externa" in sql
    assert re.search(r'"[^"]*([Ff]uera)[^"]*"', sql)


@pytest.mark.parametrize("titulo", ["Gasto en el rango", "Precio medio pagado"])
def test_euros_por_kwh_sin_mezclar_casa_y_fuera(titulo: str) -> None:
    alias = re.findall(r'AS "([^"]*€/kWh[^"]*)"', _sql(PANELES[titulo]))
    # Cada €/kWh es de casa, de fuera o la media simple del precio: ninguno de los dos juntos.
    assert alias and all(("casa" in a.lower()) + ("fuera" in a.lower()) + ("media simple" in a.lower()) == 1
                         for a in alias), alias


def test_frente_al_pvpc_solo_casa() -> None:
    assert "v_coste_carga_externa" not in _sql(PANELES["Frente al PVPC"])
