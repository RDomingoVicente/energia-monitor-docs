from __future__ import annotations

import os
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from energia_monitor import db, tarifa
from energia_monitor.repository import guardar_precios
from energia_monitor.sources.base import PreciosDia
from energia_monitor.sources.omie import parsear_marginalpdbc

RAIZ = Path(__file__).resolve().parents[2]
MIGRACIONES = RAIZ / "db" / "migrations"
FIXTURES = RAIZ / "tests" / "fixtures" / "omie"
OCTUBRE = [date(2026, 10, d) for d in range(1, 32)]


def precios_fixture(dia: date, version: int = 1) -> PreciosDia:
    texto = (FIXTURES / f"marginalpdbc_{dia:%Y%m%d}.1").read_text(encoding="latin-1")
    return PreciosDia(dia=dia, fuente="omie", version=version, tramos=parsear_marginalpdbc(texto, dia))


@pytest.fixture
def octubre_cargado(conn: psycopg.Connection) -> None:
    for dia in OCTUBRE:
        guardar_precios(conn, precios_fixture(dia))


# --- Migraciones ----------------------------------------------------------


def test_migraciones_aplicadas_y_repetibles(conn: psycopg.Connection, migraciones_aplicadas: list[str]) -> None:
    esperadas = [p.name for p in db.ficheros_migracion(MIGRACIONES)]
    assert migraciones_aplicadas == esperadas
    assert db.migrar(conn, MIGRACIONES) == []


# --- Parámetros del contrato ---------------------------------------------


def insertar_parametro(conn: psycopg.Connection, clave: str, periodo: str, vigencia: str,
                       valor: object, origen: str = "regulado", contrato: str = "vivienda") -> None:
    conn.execute(
        "INSERT INTO contrato_parametro (contrato, clave, periodo, vigencia, valor, origen, fuente)"
        " VALUES (%s, %s, %s, %s::daterange, %s, %s, 'test')",
        (contrato, clave, periodo, vigencia, valor, origen),
    )


def test_vigencias_solapadas_rechazadas(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.ExclusionViolation):
        insertar_parametro(conn, "rfe", "*", "[2026-06-01,2026-07-01)", 25)


def test_periodo_asterisco_solo_en_claves_sin_periodo(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.RaiseException, match="va por periodo"):
        insertar_parametro(conn, "peaje", "*", "[2030-01-01,)", 0.01)
    with pytest.raises(errors.RaiseException, match="no va por periodo"):
        insertar_parametro(conn, "rfe", "P1", "[2030-01-01,)", 20)


def test_valor_de_contrato_obligatorio(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation, match="contrato_con_valor"):
        insertar_parametro(conn, "rfe", "*", "[2030-01-01,)", None, origen="contrato")


def test_origen_no_valido(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation, match="origen_valido"):
        insertar_parametro(conn, "iva", "*", "[2030-01-01,)", 0.21, origen="norma")


def test_origen_de_los_parametros_cargados(conn: psycopg.Connection) -> None:
    filas = conn.execute(
        "SELECT origen, array_agg(DISTINCT clave ORDER BY clave) FROM contrato_parametro"
        " WHERE contrato = 'vivienda' GROUP BY origen ORDER BY origen"
    ).fetchall()
    assert filas == [
        ("contrato", ["rfe"]),
        ("estimado", ["ros", "ssaa"]),
        ("regulado", ["cargo", "fnee", "impuesto_electrico", "iva", "pagos_capacidad",
                      "peaje", "perdidas", "rom", "tasa_municipal"]),
    ]
    # El FNEE anterior a la Orden TED/133/2026 es regulado, pero sin valor.
    assert conn.execute(
        "SELECT origen, valor FROM contrato_parametro WHERE contrato = 'vivienda'"
        " AND clave = 'fnee' AND vigencia @> DATE '2026-02-01'"
    ).fetchone() == ("regulado", None)


def test_vigencia_sin_inicio_rechazada(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation):
        insertar_parametro(conn, "iva", "*", "(,2020-01-01)", 0.21)


# --- Precios --------------------------------------------------------------


def test_upsert_idempotente_y_por_version(conn: psycopg.Connection) -> None:
    dia = date(2026, 10, 1)
    guardar_precios(conn, precios_fixture(dia, version=2))
    guardar_precios(conn, precios_fixture(dia, version=2))

    def estado() -> tuple[int, int, D]:
        fila = conn.execute(
            "SELECT count(*), max(version_fichero), min(precio_eur_mwh) FROM precio_mercado"
            " WHERE fuente = 'omie' AND inicio >= '2026-09-30 22:00Z' AND inicio < '2026-10-01 22:00Z'"
        ).fetchone()
        assert fila is not None
        return fila

    assert estado()[:2] == (96, 2)

    def con_precio(version: int, precio: str) -> PreciosDia:
        base = precios_fixture(dia, version=version)
        return replace(base, tramos=tuple(replace(t, precio_eur_mwh=D(precio)) for t in base.tramos))

    try:
        # Una versión anterior no sobrescribe.
        guardar_precios(conn, con_precio(1, "-1"))
        assert estado()[1] == 2
        assert estado()[2] > 0

        # Una posterior sí.
        guardar_precios(conn, con_precio(3, "-2"))
        assert estado() == (96, 3, D("-2.00"))
    finally:
        conn.execute("DELETE FROM precio_mercado WHERE fuente = 'omie'")


def test_octubre_completo(conn: psycopg.Connection, octubre_cargado: None) -> None:
    n, media = conn.execute(
        "SELECT count(*), round(avg(precio_eur_mwh), 2) FROM precio_mercado"
        " WHERE fuente = 'omie' AND inicio >= '2026-09-30 22:00Z' AND inicio < '2026-10-31 23:00Z'"
    ).fetchone()  # type: ignore[misc]
    assert n == 2980
    assert media == D("166.59")


# --- Periodos -------------------------------------------------------------


def test_periodo_sql_igual_a_python(conn: psycopg.Connection, octubre_cargado: None) -> None:
    festivos = {f for (f,) in conn.execute("SELECT fecha FROM festivo")}
    assert date(2026, 10, 12) in festivos
    assert date(2026, 4, 3) not in festivos  # Viernes Santo
    filas = conn.execute(
        "SELECT inicio, periodo_2td(inicio) FROM precio_mercado"
        " WHERE fuente = 'omie' AND inicio >= '2026-09-30 22:00Z' AND inicio < '2026-10-31 23:00Z'"
    ).fetchall()
    assert len(filas) == 2980
    for inicio, periodo in filas:
        assert periodo == tarifa.periodo_2td(inicio, festivos), inicio


# --- Vista de precio final ------------------------------------------------


PARAMETROS_PRUEBA = {
    "P1": tarifa.ParametrosTramo(
        ssaa=D("12.50"), pagos_capacidad=D("0.000800"), ros=D("0.30"), rom=D("0.0407"),
        perdidas=D("0.167"), fnee=D("2.4374639"), rfe=D("20"), tasa_municipal=D("0.015"),
        peaje=D("0.033261"), cargo=D("0.064292"), impuesto_electrico=D("0.0511269632"), iva=D("0.21")),
    "P2": tarifa.ParametrosTramo(
        ssaa=D("12.50"), pagos_capacidad=D("0.000133"), ros=D("0.30"), rom=D("0.0407"),
        perdidas=D("0.163"), fnee=D("2.4374639"), rfe=D("20"), tasa_municipal=D("0.015"),
        peaje=D("0.016409"), cargo=D("0.012858"), impuesto_electrico=D("0.0511269632"), iva=D("0.21")),
    "P3": tarifa.ParametrosTramo(
        ssaa=D("12.50"), pagos_capacidad=D("0"), ros=D("0.30"), rom=D("0.0407"),
        perdidas=D("0.180"), fnee=D("2.4374639"), rfe=D("20"), tasa_municipal=D("0.015"),
        peaje=D("0.000077"), cargo=D("0.003215"), impuesto_electrico=D("0.0511269632"), iva=D("0.21")),
}
POR_PERIODO = {"pagos_capacidad", "perdidas", "peaje", "cargo"}


@pytest.fixture
def contrato_prueba(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT 1 FROM contrato WHERE id = 'prueba'").fetchone():
        return
    conn.execute("INSERT INTO contrato (id, descripcion, tarifa_acceso) VALUES ('prueba', 'test', '2.0TD')")
    vigencia = "[2026-10-01,2026-11-01)"
    for clave in tarifa.ParametrosTramo.__dataclass_fields__:
        if clave in POR_PERIODO:
            for periodo, p in PARAMETROS_PRUEBA.items():
                insertar_parametro(conn, clave, periodo, vigencia, getattr(p, clave), contrato="prueba")
        else:
            insertar_parametro(conn, clave, "*", vigencia, getattr(PARAMETROS_PRUEBA["P1"], clave),
                               origen="contrato" if clave == "rfe" else "regulado", contrato="prueba")


def test_vista_igual_a_referencia_python(conn: psycopg.Connection, octubre_cargado: None,
                                         contrato_prueba: None) -> None:
    filas = conn.execute(
        "SELECT periodo, omie_eur_mwh, energia_eur_kwh, coste_sin_impuestos_eur_kwh,"
        "       precio_final_eur_kwh, componentes_sin_valor, componentes_estimados,"
        "       incluye_estimados"
        " FROM v_precio_final WHERE contrato = 'prueba' AND fuente = 'omie'"
        "   AND inicio >= '2026-09-30 22:00Z' AND inicio < '2026-10-31 23:00Z'"
    ).fetchall()
    assert len(filas) == 2980
    with localcontext() as ctx:
        ctx.prec = 60
        for periodo, omie, energia, coste, final, pendientes, estimados, incluye in filas:
            p = PARAMETROS_PRUEBA[periodo]
            assert isinstance(final, D)  # numeric, no float
            assert energia == tarifa.energia_eur_kwh(omie, p)
            assert coste == tarifa.coste_sin_impuestos_eur_kwh(omie, p)
            assert final == tarifa.precio_final_eur_kwh(omie, p)
            assert pendientes == []
            assert estimados == []
            assert incluye is False


def test_vista_vivienda_octubre_con_estimados(conn: psycopg.Connection, octubre_cargado: None) -> None:
    fila = conn.execute(
        "SELECT count(*), count(precio_final_eur_kwh),"
        "       array_agg(DISTINCT array_to_string(componentes_regulados, ',')),"
        "       array_agg(DISTINCT array_to_string(componentes_estimados, ',')),"
        "       array_agg(DISTINCT array_to_string(componentes_sin_valor, ',')),"
        "       bool_and(incluye_estimados)"
        " FROM v_precio_final WHERE contrato = 'vivienda' AND fuente = 'omie'"
        "   AND inicio >= '2026-09-30 22:00Z' AND inicio < '2026-10-31 23:00Z'"
    ).fetchone()
    regulados = "cargo,fnee,impuesto_electrico,iva,pagos_capacidad,peaje,perdidas,rom,tasa_municipal"
    assert fila == (2980, 2980, [regulados], ["ros,ssaa"], [""], True)


@pytest.mark.parametrize(
    ("inicio", "pendientes"),
    [
        # Sin parámetros del contrato antes de 2026.
        ("2025-11-03 09:00Z", {"cargo", "fnee", "impuesto_electrico", "iva", "pagos_capacidad",
                               "peaje", "rfe", "rom", "ros", "ssaa", "tasa_municipal"}),
        # Febrero de 2026: sin estimado de SSAA ni FNEE anterior a la Orden TED/133/2026.
        ("2026-02-10 09:00Z", {"fnee", "ssaa"}),
    ],
)
def test_vista_null_si_falta_parametro(conn: psycopg.Connection, inicio: str, pendientes: set[str]) -> None:
    conn.execute(
        "INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh)"
        " VALUES (%s::timestamptz, %s::timestamptz + interval '15 min', 'test', 100)",
        (inicio, inicio),
    )
    try:
        final, faltan = conn.execute(
            "SELECT precio_final_eur_kwh, componentes_sin_valor FROM v_precio_final"
            " WHERE contrato = 'vivienda' AND fuente = 'test'"
        ).fetchone()  # type: ignore[misc]
        assert final is None
        assert set(faltan) == pendientes
    finally:
        conn.execute("DELETE FROM precio_mercado WHERE fuente = 'test'")


# --- Rol de Grafana -------------------------------------------------------


def test_grafana_ro_solo_lectura(conn: psycopg.Connection, nombre_bd: str, octubre_cargado: None) -> None:
    db.configurar_rol_grafana(conn, "clave-de-test")
    conn.execute("CREATE TABLE tabla_futura (x int)")
    try:
        assert conn.execute("SELECT has_table_privilege('grafana_ro', 'tabla_futura', 'SELECT')").fetchone() == (True,)
        with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password="clave-de-test",
                             host=os.environ["PGHOST"], autocommit=True) as ro:
            assert ro.execute("SELECT count(*) FROM v_precio_final WHERE contrato = 'vivienda'").fetchone()
            with pytest.raises(errors.InsufficientPrivilege):
                ro.execute("DELETE FROM precio_mercado")
    finally:
        conn.execute("DROP TABLE tabla_futura")


def test_ssaa_de_noviembre_con_vigencia_abierta(conn: psycopg.Connection) -> None:
    filas = conn.execute(
        "SELECT vigencia::text, valor FROM contrato_parametro"
        " WHERE contrato = 'vivienda' AND clave = 'ssaa' ORDER BY vigencia"
    ).fetchall()
    assert filas == [("[2026-10-01,2026-11-01)", D("31.5")), ("[2026-11-01,)", D("31.5"))]
