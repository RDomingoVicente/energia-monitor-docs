"""ESIOS en PostgreSQL: guardado con revisiones, SSAA real configurable,
estimación, v_precio_final, comparación con el PVPC y permisos de grafana_ro.

Se usan las respuestas reales del 10/11/2026 (tests/fixtures/esios).
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import psycopg
import pytest

from energia_monitor import db, esios, estimacion, planificador, tarifa
from energia_monitor.config import Config
from energia_monitor.sources.esios import SerieEsios, ValorEsios, parsear_indicador
from energia_monitor.tiempo import limites_dia_utc

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "esios"
DASHBOARD = json.loads((RAIZ / "grafana" / "energia-monitor.json").read_text(encoding="utf-8"))
PENINSULA = 8741
QH = timedelta(minutes=15)
DIA = date(2026, 11, 10)
FUENTE = "esios_test"
COMPONENTES = (793, 794, 2401, 798, 799, 800, 802, 1366, 2389, 2392, 2395, 2398, 10525)
CONTROLES = (10379, 10382)

# Columnas de v_precio_final publicadas en 0008: no pueden cambiar.
COLUMNAS_0008 = [
    ("inicio", "timestamp with time zone"), ("fin", "timestamp with time zone"), ("fuente", "text"),
    ("contrato", "text"), ("periodo", "text"), ("omie_eur_mwh", "numeric(10,2)"), ("ssaa", "numeric"),
    ("pagos_capacidad", "numeric"), ("ros", "numeric"), ("rom", "numeric"), ("perdidas", "numeric"),
    ("fnee", "numeric"), ("rfe", "numeric"), ("tasa_municipal", "numeric"), ("peaje", "numeric"),
    ("cargo", "numeric"), ("impuesto_electrico", "numeric"), ("iva", "numeric"),
    ("energia_eur_kwh", "numeric"), ("coste_sin_impuestos_eur_kwh", "numeric"),
    ("precio_final_eur_kwh", "numeric"), ("incluye_estimados", "boolean"),
    ("componentes_regulados", "text[]"), ("componentes_estimados", "text[]"),
    ("componentes_sin_valor", "text[]"),
]


def serie_fixture(indicador: int, dia: str = "20261110") -> SerieEsios:
    resolucion = timedelta(hours=1) if indicador in (1001, 1871) else QH
    datos = (FIXTURES / f"{indicador}_{dia}.json").read_bytes()
    return parsear_indicador(datos, indicador, PENINSULA, resolucion)


def suma_componentes(excluir: tuple[int, ...] = ()) -> dict[datetime, D]:
    suma: dict[datetime, D] = defaultdict(D)
    for i in COMPONENTES:
        if i not in excluir:
            for v in serie_fixture(i).valores:
                suma[v.inicio] += v.valor
    return dict(suma)


def limpiar(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM serie_esios")
    conn.execute("DELETE FROM ssaa_estimacion")
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FUENTE,))
    conn.execute("DELETE FROM contrato_ssaa_componente WHERE contrato LIKE 'ssaa_test%%'")
    conn.execute("DELETE FROM contrato_parametro WHERE contrato LIKE 'ssaa_test%%'")
    conn.execute("DELETE FROM contrato WHERE id LIKE 'ssaa_test%%'")


@pytest.fixture
def limpio(conn: psycopg.Connection):
    limpiar(conn)
    yield
    limpiar(conn)


@pytest.fixture
def dia_cargado(conn: psycopg.Connection, limpio: None) -> None:
    for i in COMPONENTES + CONTROLES + (1871, 1001):
        esios.guardar_serie(conn, serie_fixture(i))


def mercado(conn: psycopg.Connection, dia: date, precio: str = "100") -> None:
    inicio, fin = limites_dia_utc(dia)
    filas, t = [], inicio
    while t < fin:
        filas.append((t, t + QH, FUENTE, D(precio)))
        t += QH
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh) VALUES (%s, %s, %s, %s)",
                        filas)


def contrato_con_definicion(conn: psycopg.Connection, nombre: str, incluidos: dict[int, bool]) -> None:
    conn.execute("INSERT INTO contrato (id, descripcion, tarifa_acceso) VALUES (%s, 'test', '2.0TD')", (nombre,))
    for indicador, incluido in incluidos.items():
        conn.execute(
            "INSERT INTO contrato_ssaa_componente (contrato, indicador, vigencia, incluido)"
            " VALUES (%s, %s, '[2026-01-01,)', %s)", (nombre, indicador, incluido),
        )


# --- Esquema ------------------------------------------------------------------


def test_catalogo_y_definicion_por_defecto(conn: psycopg.Connection) -> None:
    usos = dict(conn.execute(
        "SELECT uso, array_agg(id ORDER BY id) FROM esios_indicador GROUP BY uso").fetchall())
    assert usos == {
        "ssaa_componente": sorted(COMPONENTES),
        "ssaa_control": list(CONTROLES),
        "ssaa_prevision": [1871],
        "pvpc": [1001],
    }
    filas = conn.execute(
        "SELECT indicador, incluido, vigencia::text, nota IS NOT NULL FROM contrato_ssaa_componente"
        " WHERE contrato = 'vivienda' ORDER BY indicador").fetchall()
    assert [f[0] for f in filas] == sorted(COMPONENTES)
    assert all(incluido and vigencia == "[2026-01-01,)" for _, incluido, vigencia, _ in filas)
    # La duda sobre los desvíos queda documentada en la definición.
    assert [f[0] for f in filas if f[3]] == [799, 800]


def test_definicion_solo_admite_componentes(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("INSERT INTO contrato_ssaa_componente (contrato, indicador, vigencia, incluido)"
                     " VALUES ('vivienda', 10379, '[2030-01-01,)', true)")
    with pytest.raises(psycopg.errors.ExclusionViolation):
        conn.execute("INSERT INTO contrato_ssaa_componente (contrato, indicador, vigencia, incluido)"
                     " VALUES ('vivienda', 799, '[2027-01-01,)', false)")


def test_columnas_de_v_precio_final_conservadas(conn: psycopg.Connection) -> None:
    columnas = conn.execute(
        "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute"
        " WHERE attrelid = 'v_precio_final'::regclass AND attnum > 0 ORDER BY attnum").fetchall()
    assert columnas[:len(COLUMNAS_0008)] == COLUMNAS_0008
    assert columnas[len(COLUMNAS_0008):] == [
        ("ssaa_origen", "text"), ("ssaa_metodo", "text"), ("estado_componentes", "jsonb")]


def test_jit_desactivado_en_la_base_de_datos(nombre_bd: str, migraciones_aplicadas: list[str]) -> None:
    with psycopg.connect(dbname=nombre_bd) as nueva:
        assert nueva.execute("SHOW jit").fetchone() == ("off",)


def test_grafana_ro_conserva_select(conn: psycopg.Connection, nombre_bd: str, dia_cargado: None) -> None:
    objetos = ["v_precio_final", "v_ssaa_real", "v_comparacion_pvpc", "serie_esios", "esios_indicador",
               "contrato_ssaa_componente", "ssaa_estimacion"]
    for objeto in objetos:
        assert conn.execute("SELECT has_table_privilege('grafana_ro', %s, 'SELECT')",
                            (objeto,)).fetchone() == (True,), objeto
    mercado(conn, DIA)
    db.configurar_rol_grafana(conn, "clave-de-test")
    with psycopg.connect(dbname=nombre_bd, user="grafana_ro", password="clave-de-test",
                         host=os.environ["PGHOST"], autocommit=True) as ro:
        for objeto in objetos:
            ro.execute(f"SELECT * FROM {objeto} LIMIT 1")
        n = ro.execute("SELECT count(*) FROM v_precio_final WHERE contrato = 'vivienda' AND fuente = %s"
                       " AND ssaa_origen = 'real'", (FUENTE,)).fetchone()
        assert n == (96,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("DELETE FROM serie_esios")


# --- Guardado y revisiones -----------------------------------------------------


def test_guardado_idempotente_con_primer_valor(conn: psycopg.Connection, limpio: None) -> None:
    s = serie_fixture(799)
    assert esios.guardar_serie(conn, s).nuevos == 96
    assert esios.guardar_serie(conn, s) == esios.Guardado(nuevos=0, cambios=[])

    primero = s.valores[0]
    revisada = SerieEsios(799, PENINSULA, s.publicado_en + timedelta(days=1),  # type: ignore[operator]
                          (ValorEsios(primero.inicio, primero.fin, primero.valor + 1),))
    guardado = esios.guardar_serie(conn, revisada)
    assert guardado.cambios == [esios.Cambio(primero.inicio, primero.valor, primero.valor + 1)]

    # Una descarga con publicación anterior no sobrescribe.
    antigua = SerieEsios(799, PENINSULA, s.publicado_en - timedelta(days=1),  # type: ignore[operator]
                         (ValorEsios(primero.inicio, primero.fin, D("-99")),))
    assert esios.guardar_serie(conn, antigua).cambios == []

    valor, primer_valor, modificado = conn.execute(
        "SELECT valor, primer_valor, modificado_en FROM serie_esios WHERE indicador = 799 AND inicio = %s",
        (primero.inicio,)).fetchone()  # type: ignore[misc]
    assert (valor, primer_valor) == (primero.valor + 1, primero.valor)
    assert modificado is not None
    assert [(i, d) for i, d, *_ in esios.revisiones(conn)] == [(799, DIA)]


def test_controles_de_suma(conn: psycopg.Connection, dia_cargado: None) -> None:
    assert esios.comprobar_controles(conn, DIA, DIA) == {10379: [], 10382: []}
    conn.execute("UPDATE serie_esios SET valor = valor + 1 WHERE indicador = 2392 AND inicio = %s",
                 (datetime(2026, 11, 10, 12, tzinfo=UTC),))
    fallos = esios.comprobar_controles(conn, DIA, DIA)
    assert [t for t, *_ in fallos[10382]] == [datetime(2026, 11, 10, 12, tzinfo=UTC)]


def test_estado_de_la_prevision(conn: psycopg.Connection, limpio: None) -> None:
    def insertar(dia: date, valor: str, primer_valor: str) -> None:
        inicio, _ = limites_dia_utc(dia)
        conn.execute(
            "INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor,"
            "                         primera_descarga, descargado_en)"
            " VALUES (1871, 8741, %s, %s + interval '1 hour', %s, %s, %s, %s, %s)",
            (inicio, inicio, D(valor), inicio, D(primer_valor),
             inicio - timedelta(hours=3), inicio + timedelta(days=3)),
        )

    for d in range(1, 7):
        insertar(date(2026, 11, d), "20", "20")
    assert esios.estado_prevision(conn) == esios.EstadoPrevision("pendiente", 6, 0)
    insertar(date(2026, 11, 7), "20", "20")
    assert esios.estado_prevision(conn).estado == "verificada"
    insertar(date(2026, 11, 8), "21", "20")
    assert esios.estado_prevision(conn) == esios.EstadoPrevision("corregida", 8, 1)


# --- SSAA real ------------------------------------------------------------------


def ssaa_real(conn: psycopg.Connection, contrato: str) -> dict[datetime, D]:
    inicio, fin = limites_dia_utc(DIA)
    return dict(conn.execute(
        "SELECT inicio, ssaa_eur_mwh FROM v_ssaa_real WHERE contrato = %s AND inicio >= %s AND inicio < %s",
        (contrato, inicio, fin)).fetchall())


def test_ssaa_real_es_la_suma_de_los_componentes(conn: psycopg.Connection, dia_cargado: None) -> None:
    real = ssaa_real(conn, "vivienda")
    assert real == suma_componentes()
    # Coincide con 10379 + 10382 + 10525 salvo redondeo.
    totales = suma_componentes(excluir=tuple(set(COMPONENTES) - {10525}))
    for control in CONTROLES:
        for v in serie_fixture(control).valores:
            totales[v.inicio] += v.valor
    assert max(abs(real[t] - totales[t]) for t in real) <= D("0.05")
    assert D(19) < sum(real.values()) / len(real) < D(32)  # media del 10/11: 31,89 €/MWh


def test_definicion_configurable(conn: psycopg.Connection, dia_cargado: None) -> None:
    contrato_con_definicion(conn, "ssaa_test_sin_desvios", {i: i not in (799, 800) for i in COMPONENTES})
    assert ssaa_real(conn, "ssaa_test_sin_desvios") == suma_componentes(excluir=(799, 800))
    # Cerrar la vigencia de un componente lo saca de la suma desde esa fecha.
    conn.execute("UPDATE contrato_ssaa_componente SET vigencia = '[2026-01-01,2026-11-10)'"
                 " WHERE contrato = 'ssaa_test_sin_desvios' AND indicador = 2392")
    assert ssaa_real(conn, "ssaa_test_sin_desvios") == suma_componentes(excluir=(799, 800, 2392))


def test_sin_todos_los_componentes_no_hay_real(conn: psycopg.Connection, dia_cargado: None) -> None:
    conn.execute("DELETE FROM serie_esios WHERE indicador = 2389 AND inicio = %s",
                 (datetime(2026, 11, 10, 12, tzinfo=UTC),))
    real = ssaa_real(conn, "vivienda")
    assert len(real) == 95
    assert datetime(2026, 11, 10, 12, tzinfo=UTC) not in real


def test_dia_incompleto_hasta_d_mas_2(conn: psycopg.Connection, dia_cargado: None) -> None:
    # Publicado el 11/11 (D+1): tiempo real y desvíos aún parciales.
    conn.execute("UPDATE serie_esios SET publicado_en = '2026-11-11 07:08Z' WHERE indicador = 794")
    assert ssaa_real(conn, "vivienda") == {}
    conn.execute("UPDATE serie_esios SET publicado_en = '2026-11-12 07:08Z' WHERE indicador = 794")
    assert len(ssaa_real(conn, "vivienda")) == 96


# --- v_precio_final ------------------------------------------------------------


def filas_vista(conn: psycopg.Connection, dia: date) -> list[dict]:
    inicio, fin = limites_dia_utc(dia)
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        return cur.execute(
            "SELECT * FROM v_precio_final WHERE contrato = 'vivienda' AND fuente = %s"
            " AND inicio >= %s AND inicio < %s ORDER BY inicio", (FUENTE, inicio, fin)).fetchall()


def parametros_de_fila(fila: dict) -> tarifa.ParametrosTramo:
    return tarifa.ParametrosTramo(**{k: fila[k] for k in tarifa.ParametrosTramo.__dataclass_fields__})


def comprobar_formula(fila: dict) -> None:
    with localcontext() as ctx:
        ctx.prec = 60
        p = parametros_de_fila(fila)
        assert fila["energia_eur_kwh"] == tarifa.energia_eur_kwh(fila["omie_eur_mwh"], p)
        assert fila["precio_final_eur_kwh"] == tarifa.precio_final_eur_kwh(fila["omie_eur_mwh"], p)


def test_vista_usa_el_ssaa_real(conn: psycopg.Connection, dia_cargado: None) -> None:
    mercado(conn, DIA)
    filas = filas_vista(conn, DIA)
    real = suma_componentes()
    assert len(filas) == 96
    for fila in filas:
        assert fila["ssaa"] == real[fila["inicio"]]
        assert (fila["ssaa_origen"], fila["ssaa_metodo"]) == ("real", "esios")
        assert fila["componentes_estimados"] == ["ros"]
        assert fila["componentes_sin_valor"] == []
        assert fila["incluye_estimados"] is True
        assert fila["estado_componentes"] == {
            "omie": "real", "ssaa": "real", "ros": "estimado", "rfe": "contrato",
            **{k: "regulado" for k in ("cargo", "fnee", "impuesto_electrico", "iva", "pagos_capacidad",
                                        "peaje", "perdidas", "rom", "tasa_municipal")},
        }
        comprobar_formula(fila)


def test_vista_usa_estimacion_y_despues_el_parametro(conn: psycopg.Connection, limpio: None) -> None:
    dia = date(2026, 11, 12)
    mercado(conn, dia)
    inicio, _ = limites_dia_utc(dia)
    conn.execute("INSERT INTO ssaa_estimacion (contrato, inicio, fin, valor, metodo)"
                 " VALUES ('vivienda', %s, %s, 18.5, 'perfil_14d')", (inicio, inicio + QH))
    primera, segunda, *_ = filas_vista(conn, dia)

    assert (primera["ssaa"], primera["ssaa_origen"], primera["ssaa_metodo"]) == (D("18.5"), "estimado", "perfil_14d")
    assert primera["componentes_estimados"] == ["ros", "ssaa"]
    assert primera["estado_componentes"]["ssaa"] == "estimado"
    comprobar_formula(primera)

    # Sin real ni estimación: parámetro estimado de contrato_parametro (0007).
    assert (segunda["ssaa"], segunda["ssaa_origen"], segunda["ssaa_metodo"]) == (D("31.5"), "estimado", "parametro")
    assert segunda["componentes_estimados"] == ["ros", "ssaa"]
    comprobar_formula(segunda)


def test_vista_sin_ssaa_antes_de_los_parametros(conn: psycopg.Connection, limpio: None) -> None:
    mercado(conn, date(2026, 2, 10))
    fila = filas_vista(conn, date(2026, 2, 10))[40]
    assert fila["ssaa"] is None and fila["ssaa_origen"] is None and fila["ssaa_metodo"] is None
    assert "ssaa" in fila["componentes_sin_valor"]
    assert "ssaa" not in fila["estado_componentes"]


def test_tramo_horario_usa_la_media_de_los_cuartos(conn: psycopg.Connection, dia_cargado: None) -> None:
    inicio = datetime(2026, 11, 10, 9, tzinfo=UTC)
    conn.execute("INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh)"
                 " VALUES (%s, %s, %s, 100)", (inicio, inicio + timedelta(hours=1), FUENTE))
    real = suma_componentes()
    esperado = (sum(real[inicio + i * QH] for i in range(4)) / 4).quantize(D("0.0001"))
    (fila,) = filas_vista(conn, DIA)
    assert (fila["ssaa"], fila["ssaa_metodo"]) == (esperado, "esios")


# --- Comparación con el PVPC ------------------------------------------------------


def test_comparacion_pvpc(conn: psycopg.Connection, dia_cargado: None) -> None:
    mercado(conn, DIA)
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        filas = cur.execute("SELECT * FROM v_comparacion_pvpc WHERE contrato = 'vivienda' AND fuente = %s"
                            " ORDER BY inicio", (FUENTE,)).fetchall()
    pvpc = {v.inicio: v.valor for v in serie_fixture(1001).valores}
    vista = filas_vista(conn, DIA)
    assert len(filas) == 24
    for fila in filas:
        cuartos = [v for v in vista if fila["inicio"] <= v["inicio"] < fila["fin"]]
        assert len(cuartos) == 4
        impuestos = (1 + cuartos[0]["impuesto_electrico"]) * (1 + cuartos[0]["iva"])
        assert fila["pvpc_sin_impuestos_eur_kwh"] == pvpc[fila["inicio"]] / 1000
        assert fila["pvpc_final_eur_kwh"] == pvpc[fila["inicio"]] / 1000 * impuestos
        assert fila["precio_final_eur_kwh"] == sum(c["precio_final_eur_kwh"] for c in cuartos) / 4
        assert fila["diferencia_eur_kwh"] == fila["precio_final_eur_kwh"] - fila["pvpc_final_eur_kwh"]
        assert fila["ssaa_estimado"] is False


def test_comparacion_solo_horas_completas(conn: psycopg.Connection, dia_cargado: None) -> None:
    mercado(conn, DIA)
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s AND inicio = '2026-11-10 10:15Z'", (FUENTE,))
    n = conn.execute("SELECT count(*) FROM v_comparacion_pvpc WHERE contrato = 'vivienda' AND fuente = %s",
                     (FUENTE,)).fetchone()
    assert n == (23,)


# --- Estimación en la base de datos -------------------------------------------------


def cargar_real_sintetico(conn: psycopg.Connection, desde: date, hasta: date, valor: str) -> None:
    """10525 como único componente del contrato de prueba, publicado el 01/03/2027."""
    filas = []
    for d in range((hasta - desde).days + 1):
        inicio, fin = limites_dia_utc(desde + timedelta(days=d))
        t = inicio
        while t < fin:
            filas.append((t, t + QH, D(valor)))
            t += QH
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor)"
            " VALUES (10525, 8741, %s, %s, %s, '2027-03-01Z', %s)",
            [(a, b, v, v) for a, b, v in filas],
        )


def test_actualizar_estimaciones(conn: psycopg.Connection, limpio: None) -> None:
    contrato_con_definicion(conn, "ssaa_test_estimacion", {10525: True})
    cargar_real_sintetico(conn, date(2027, 1, 20), date(2027, 2, 14), "12.5")
    hoy = date(2027, 2, 17)
    mercado(conn, date(2027, 2, 14))   # con real: no se estima
    mercado(conn, date(2027, 2, 18))   # mañana: se estima

    resultado = estimacion.actualizar(conn, hoy)

    assert resultado["ssaa_test_estimacion"] == {"perfil_14d": 96}
    assert resultado["vivienda"] == {}  # sin componentes de vivienda cargados
    filas = conn.execute("SELECT min(inicio), count(*), min(valor), max(valor), min(metodo)"
                         " FROM ssaa_estimacion WHERE contrato = 'ssaa_test_estimacion'").fetchone()
    assert filas == (limites_dia_utc(date(2027, 2, 18))[0], 96, D("12.5"), D("12.5"), "perfil_14d")


def test_actualizar_estimaciones_con_prevision_verificada(conn: psycopg.Connection, limpio: None) -> None:
    contrato_con_definicion(conn, "ssaa_test_estimacion", {10525: True})
    cargar_real_sintetico(conn, date(2027, 1, 20), date(2027, 2, 14), "12.5")
    inicio, _ = limites_dia_utc(date(2027, 1, 20))
    _, fin = limites_dia_utc(date(2027, 2, 18))
    t, filas = inicio, []
    while t < fin:
        # Publicada la víspera y comprobada tres días después sin cambios.
        filas.append((t, t + timedelta(hours=1), D(25), t, D(25), t - timedelta(hours=3), t + timedelta(days=3)))
        t += timedelta(hours=1)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO serie_esios (indicador, geo_id, inicio, fin, valor, publicado_en, primer_valor,"
            " primera_descarga, descargado_en) VALUES (1871, 8741, %s, %s, %s, %s, %s, %s, %s)", filas)
    mercado(conn, date(2027, 2, 18))

    assert esios.estado_prevision(conn).estado == "verificada"
    resultado = estimacion.actualizar(conn, date(2027, 2, 17))
    assert resultado["ssaa_test_estimacion"] == {"prevision_ree": 96}
    # Factor real / previsión = 12,5 / 25 = 0,5.
    assert conn.execute("SELECT DISTINCT valor FROM ssaa_estimacion WHERE contrato = 'ssaa_test_estimacion'"
                        ).fetchall() == [(D("12.5"),)]


# --- Colector con un cliente falso ----------------------------------------------------


class ClienteFixtures:
    """Sirve el 10/11/2026 de los fixtures; el resto de días, sin valores."""

    def __init__(self) -> None:
        self.pedidos: list[tuple[int, datetime, datetime]] = []

    def serie(self, indicador: int, geo_id: int, resolucion: timedelta, desde: datetime, hasta: datetime):
        self.pedidos.append((indicador, desde, hasta))
        s = serie_fixture(indicador)
        valores = tuple(v for v in s.valores if desde <= v.inicio < hasta)
        return SerieEsios(indicador, geo_id, s.publicado_en if valores else None, valores)


def test_completar_y_actualizar(conn: psycopg.Connection, limpio: None) -> None:
    cliente = ClienteFixtures()
    resumen = esios.completar_desde(conn, cliente, DIA, date(2026, 11, 10))
    # 17 indicadores, un rango (10 y 11/11) por indicador.
    assert resumen.peticiones == 17 and resumen.fallidos == []
    assert {(a, b) for _, a, b in cliente.pedidos} == {
        (limites_dia_utc(DIA)[0], limites_dia_utc(date(2026, 11, 11))[1])}
    assert conn.execute("SELECT count(*) FROM serie_esios").fetchone() == (15 * 96 + 2 * 24,)

    # Segundo arranque: solo falta mañana.
    cliente.pedidos.clear()
    esios.completar_desde(conn, cliente, DIA, date(2026, 11, 10))
    assert {(a, b) for _, a, b in cliente.pedidos} == {limites_dia_utc(date(2026, 11, 11))}

    cliente.pedidos.clear()
    actualizado = esios.actualizar(conn, cliente, date(2026, 11, 12), dias_atras=3)
    assert actualizado.peticiones == 17 and actualizado.cambios == 0


def test_ejecutar_con_esios(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATIONS_DIR", str(RAIZ / "db" / "migrations"))
    stop = threading.Event()

    class ClienteQuePara(ClienteFixtures):
        def serie(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if len(self.pedidos) == 16:
                stop.set()
            return super().serie(*args, **kwargs)

    class SinPrecios:
        nombre = FUENTE

        def precios_dia(self, dia: date) -> None:
            return None

    cliente = ClienteQuePara()
    config = Config(carga_inicial_desde=date(2026, 11, 12), esios_carga_desde=DIA)
    planificador.ejecutar(config, SinPrecios(), stop,  # type: ignore[arg-type]
                          conectar=lambda: psycopg.connect(dbname=nombre_bd, autocommit=True),
                          reloj=lambda: datetime(2026, 11, 10, 12, 0, tzinfo=UTC), cliente_esios=cliente)
    assert len(cliente.pedidos) == 17
    assert conn.execute("SELECT count(DISTINCT indicador) FROM serie_esios").fetchone() == (17,)


# --- Dashboard --------------------------------------------------------------------------


@pytest.mark.parametrize("panel_id", [11, 12, 13, 14, 15])
def test_consultas_de_los_paneles_nuevos(conn: psycopg.Connection, dia_cargado: None, panel_id: int) -> None:
    mercado(conn, DIA)
    panel = next(p for p in DASHBOARD["panels"] if p["id"] == panel_id)
    ahora = "'2026-11-10 12:05Z'::timestamptz"
    sql = (panel["targets"][0]["rawSql"]
           .replace("$contrato", "vivienda").replace("$fuente", FUENTE).replace("now()", ahora)
           .replace("$__timeFilter(inicio)", "inicio BETWEEN '2026-11-01Z' AND '2026-11-12Z'"))
    assert "$" not in sql
    filas = conn.execute(sql).fetchall()
    assert filas and all(v is not None for v in filas[0])
