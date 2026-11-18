"""Planificador contra PostgreSQL (paso 3a): entrada desde la base de datos, plan guardado, ciclo en el
muestreo, solicitudes de recálculo y CLI. Nunca se contacta con el cargador."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import psycopg
import pytest

from energia_monitor import bateria_servicio, db, muestreo
from energia_monitor.plan_carga import informe
from energia_monitor.plan_carga import servicio as ps
from energia_monitor.plan_carga.modelo import CUARTO
from energia_monitor.tiempo import MADRID, limites_dia_utc
from energia_monitor.viaris import EstadoConector, Modulador, Sesion

FUENTE = "plan_srv"
# Miércoles 11/11/2026 a las 20:00 en Madrid.
AHORA = datetime(2026, 11, 11, 20, tzinfo=MADRID).astimezone(UTC)
TABLAS = ("plan_calculo", "planificador_solicitud", "planificador_ciclo", "cargador_estado", "cargador_muestra",
          "energia_tramo", "cargador_sesion", "bateria_lectura", "bateria_horario", "bateria_excepcion",
          "bateria_carga_externa", "aviso_historial")


def limpiar(conn: psycopg.Connection) -> None:
    for tabla in TABLAS:
        conn.execute(f"DELETE FROM {tabla}")  # type: ignore[arg-type]
    conn.execute("DELETE FROM precio_mercado WHERE fuente = %s", (FUENTE,))
    conn.execute("UPDATE planificador_config SET contrato = 'vivienda', fuente = 'omie'")
    db.refrescar_precio_final(conn)
    conn.execute("DELETE FROM planificador_solicitud")
    conn.execute("SELECT recalcular_bateria()")


@pytest.fixture
def limpio(conn: psycopg.Connection) -> Iterator[None]:
    limpiar(conn)
    yield
    limpiar(conn)


def local(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 11, dia, hora, minuto, tzinfo=MADRID).astimezone(UTC)


def precios(conn: psycopg.Connection, desde: date, hasta: date) -> None:
    """Noche barata (00–07 h: 20 €/MWh), día caro (120 €/MWh); el jueves de madrugada aún más barata."""
    filas = []
    dia = desde
    while dia <= hasta:
        t, fin = limites_dia_utc(dia)
        while t < fin:
            h = t.astimezone(MADRID).hour
            filas.append((t, t + CUARTO, FUENTE, D(10) if h < 7 and dia.isoweekday() == 4 else D(20) if h < 7 else D(120)))
            t += CUARTO
        dia += timedelta(days=1)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO precio_mercado (inicio, fin, fuente, precio_eur_mwh) VALUES (%s, %s, %s, %s)",
                        filas)
    conn.execute("UPDATE planificador_config SET fuente = %s", (FUENTE,))
    db.refrescar_precio_final(conn)


def escenario(conn: psycopg.Connection, ahora: datetime = AHORA, *, pct: int = 40, enchufado: bool = True) -> None:
    precios(conn, ahora.astimezone(MADRID).date() - timedelta(days=30), ahora.astimezone(MADRID).date() + timedelta(days=1))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO bateria_horario (dia_iso, hora_salida) VALUES (%s, '09:00')",
                        [(d,) for d in range(1, 8)])
    conn.execute("INSERT INTO bateria_lectura (instante, pct, origen) VALUES (%s, %s, 'cli')",
                 (ahora - timedelta(hours=3), pct))
    conn.execute("INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES ('mennekes', %s, 2)",
                 (ahora - timedelta(hours=4),))
    if enchufado:
        conn.execute("INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES ('mennekes', %s, 4)",
                     (ahora - timedelta(hours=2),))
    conn.execute("SELECT recalcular_bateria(%s)", (ahora,))


def disponibilidad_aprendida(conn: psycopg.Connection, dias: int = 25) -> None:
    """Una muestra por cuarto durante días: enchufado de 19:00 a 08:00 locales, libre el resto."""
    filas = []
    fin = AHORA - timedelta(hours=6)
    t = fin - timedelta(days=dias)
    while t < fin:
        h = t.astimezone(MADRID).hour
        filas.append((t + timedelta(minutes=1), 4 if h >= 19 or h < 8 else 1))
        t += CUARTO
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO cargador_muestra (instante, intervalo_s, evse_w, home_w, total_w, estado)"
                        " VALUES (%s, 60, 0, 300, 300, %s)", filas)


# --- Entrada y plan ------------------------------------------------------------------------------


def test_plan_en_modo_prudente(conn: psycopg.Connection, limpio: None) -> None:
    escenario(conn)
    entrada, ctx, plan, ms = ps.calcular(conn, AHORA)
    assert ctx.modo == "sombra" and entrada.prudente and ctx.dias_historial == 0 and entrada.conectado
    assert entrada.nivel_kwh == D("91.3") * D("0.40")
    assert [s.instante for s in entrada.salidas] == [local(12, 9), local(13, 9), local(14, 9)]
    assert entrada.horizonte_fin == local(14, 9)
    # Prudente: solo la próxima salida y la ventana segura (hasta las 9:00 del jueves).
    assert plan.salidas[0].cubierta and all(c.inicio < local(12, 9) for c in plan.cuartos)
    # Necesaria en la madrugada barata del jueves; la potencia sin muestras es la inicial (4,5 kW).
    assert all(local(12, 0) <= c.inicio < local(12, 7) for c in plan.cuartos)
    assert {c.kwh for c in plan.cuartos} == {D("1.125")}
    # Necesaria, su margen del 10 % y, con lo barato que sobra, oportunista: toda la madrugada.
    assert {c.nivel for c in plan.cuartos} == {"necesaria", "margen", "oportunista"} and len(plan.cuartos) == 28
    assert sum(c.kwh for c in plan.cuartos if c.nivel == "margen") * D("0.86") >= D("0.1") * sum(
        c.kwh for c in plan.cuartos if c.nivel == "necesaria") * D("0.86")
    assert ms < 2000


def test_precios_provisionales_hasta_la_hora_de_publicacion(conn: psycopg.Connection, limpio: None) -> None:
    ahora = local(11, 13)
    escenario(conn, ahora)
    antes, _ = ps.cargar_entrada(conn, ahora)
    despues, _ = ps.cargar_entrada(conn, local(11, 13, 30))
    manana = local(12, 3)
    assert next(c for c in antes.cuartos if c.inicio == manana).provisional
    assert not next(c for c in despues.cuartos if c.inicio == manana).provisional
    # El umbral: 60 % de la media de los 7 días completos anteriores.
    filas = conn.execute("SELECT avg(precio_final_eur_kwh) FROM v_plan_precio WHERE inicio >= %s AND inicio < %s",
                         (limites_dia_utc(date(2026, 11, 4))[0], limites_dia_utc(date(2026, 11, 11))[0])).fetchone()
    assert filas is not None and antes.umbral == D(60) / 100 * filas[0]


def test_disponibilidad_aprendida_usa_la_ventana_probable(conn: psycopg.Connection, limpio: None) -> None:
    ahora = local(11, 17)
    escenario(conn, ahora, enchufado=False)
    disponibilidad_aprendida(conn)
    entrada, ctx, plan, _ = ps.calcular(conn, ahora)
    assert not entrada.prudente and ctx.dias_historial >= 21 and not entrada.conectado
    assert plan.cuartos and {c.ventana for c in plan.cuartos} == {"probable"}
    assert all(s.cubierta for s in plan.salidas)


def test_guardar_y_vistas(conn: psycopg.Connection, limpio: None) -> None:
    escenario(conn)
    entrada, ctx, plan, ms = ps.calcular(conn, AHORA)
    ident = ps.guardar(conn, entrada, ctx, plan, "test", ms)
    assert conn.execute("SELECT count(*) FROM plan_cuarto WHERE calculo_id = %s", (ident,)).fetchone() == (
        len(plan.cuartos),)
    assert conn.execute("SELECT count(*) FROM plan_salida WHERE calculo_id = %s", (ident,)).fetchone() == (
        len(plan.salidas),)
    vigente = conn.execute("SELECT inicio, fin, motivo, kwh_cargador, precio_medio_eur_kwh, coste_eur, tareas"
                           " FROM v_plan_vigente ORDER BY n").fetchall()
    assert len(vigente) == len(plan.franjas) >= 1
    for fila, f in zip(vigente, plan.franjas, strict=True):
        precio, coste = informe.coste_franja(plan, f, ctx.factor_contador)
        assert fila[:4] == (f.inicio, f.fin, f.motivo, f.kwh) and fila[6] == f.tareas
        assert abs(fila[4] - precio) < D("1e-12") and abs(fila[5] - coste) < D("1e-12")  # type: ignore[operator]
    assert conn.execute("SELECT ultimo_calculo_id FROM planificador_ciclo").fetchone() == (ident,)


def test_franja_en_curso_se_conserva(conn: psycopg.Connection, limpio: None) -> None:
    escenario(conn)
    entrada, ctx, plan, ms = ps.calcular(conn, AHORA)
    ps.guardar(conn, entrada, ctx, plan, "test", ms)
    primera = plan.franjas[0]
    dentro = primera.inicio + timedelta(minutes=20)
    # Precios nuevos que harían preferir otras horas: la franja en curso no se mueve.
    conn.execute("UPDATE precio_mercado SET precio_eur_mwh = 500 WHERE fuente = %s AND inicio >= %s AND inicio < %s",
                 (FUENTE, primera.inicio, primera.fin))
    db.refrescar_precio_final(conn)
    _, _, nuevo, _ = ps.calcular(conn, dentro)
    curso = [f for f in nuevo.franjas if f.en_curso]
    assert [(f.inicio, f.fin) for f in curso] == [(primera.inicio, primera.fin)]


# --- Ciclo, solicitudes y muestreo ---------------------------------------------------------------


def test_ciclo(conn: psycopg.Connection, limpio: None, monkeypatch: pytest.MonkeyPatch) -> None:
    escenario(conn)
    ciclo = ps.Ciclo()
    assert ciclo.ejecutar(conn, AHORA) is not None  # arranque
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=1)) is None
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=2), "estado 6") is not None
    ps.solicitar(conn, "lectura")
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=3)) is not None
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=4)) is None
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=18)) is not None  # periódico
    motivos = [m for (m,) in conn.execute("SELECT motivo FROM plan_calculo ORDER BY id")]
    assert motivos == ["arranque", "estado 6", "solicitud: lectura", "periódico"]

    # Un fallo no se propaga, queda registrado y el evento se reintenta en el ciclo siguiente.
    def falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("prueba")

    monkeypatch.setattr(ps, "calcular", falla)
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=19), "estado 4") is None
    assert conn.execute("SELECT ultimo_error FROM planificador_ciclo").fetchone() == ("RuntimeError: prueba",)
    monkeypatch.undo()
    assert ciclo.ejecutar(conn, AHORA + timedelta(minutes=20)) is not None
    assert conn.execute("SELECT motivo FROM plan_calculo ORDER BY id DESC LIMIT 1").fetchone() == ("estado 4",)


def test_solicitudes_de_bateria_y_precios(conn: psycopg.Connection, limpio: None) -> None:
    bateria_servicio.recalcular(conn, AHORA)
    assert conn.execute("SELECT motivo FROM planificador_solicitud").fetchone() == ("cambio en la batería",)
    db.refrescar_precio_final(conn)
    assert conn.execute("SELECT motivo FROM planificador_solicitud").fetchone() == ("precios",)


class ClienteCambiante:
    base = "http://cargador-falso"

    def __init__(self) -> None:
        self.estado = 4

    def modulador(self) -> Modulador:
        return Modulador(D(0), D(300), D(300))

    def estado_conector(self) -> EstadoConector:
        return EstadoConector(self.estado, None, None, None)

    def historico(self) -> list[Sesion]:
        return []


def test_muestreador_planifica_en_cada_cambio_de_estado(conn: psycopg.Connection, limpio: None) -> None:
    escenario(conn)
    ahora = [AHORA]
    cliente = ClienteCambiante()
    m = muestreo.Muestreador(cliente, lambda: ahora[0], plan=ps.Ciclo())  # type: ignore[arg-type]
    for minuto in range(6):
        cliente.estado = 6 if minuto == 3 else 4
        m.ciclo(conn)
        ahora[0] += timedelta(minutes=1)
    motivos = [m for (m,) in conn.execute("SELECT motivo FROM plan_calculo ORDER BY id")]
    # El estado 4 ya estaba registrado (sin evento): arranca; después, un cálculo por cada cambio.
    assert motivos == ["arranque", "estado 6", "estado 4"]


# --- CLI -------------------------------------------------------------------------------------------


def test_cli_plan_y_simular(conn: psycopg.Connection, nombre_bd: str, limpio: None,
                            monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from energia_monitor import __main__ as cli

    monkeypatch.setattr(db, "conectar", lambda **_: psycopg.connect(dbname=nombre_bd, autocommit=True))
    escenario(conn)
    # La noche del 11 al 12: carga real de 23:00 a 02:00 (caro antes de las 00:00) y desconexión a las 08:30.
    conn.execute("INSERT INTO cargador_estado (conector, detectado_en, estado) VALUES ('mennekes', %s, 1)",
                 (local(12, 8, 30),))
    with conn.cursor() as cur:
        q = local(11, 23)
        while q < local(12, 2):
            cur.execute("INSERT INTO energia_tramo (inicio, fin, coche_wh, casa_wh, total_wh, cubierto_s, incompleto)"
                        " VALUES (%s, %s, 1100, NULL, 1100, 900, false)", (q, q + CUARTO))
            q += CUARTO
    assert cli.main(["planificador", "plan", "--instante", "2026-11-11 20:00"]) == 0
    salida = capsys.readouterr().out
    assert "Plan a las mié 11/11 20:00 · modo sombra" in salida and "modo prudente" in salida
    assert "jue 12/11 09:00" in salida and "necesaria (jue 12/11 09:00)" in salida
    assert "[se planifica al volver a enchufar]" in salida and "NO CUBIERTA" not in salida
    assert conn.execute("SELECT count(*) FROM plan_calculo").fetchone() == (0,)  # no guarda

    assert cli.main(["planificador", "simular", "--dia", "2026-11-11", "--desde", "20:00"]) == 0
    salida = capsys.readouterr().out
    assert "Conexión mié 11/11 18:00 – jue 12/11 08:30: 13,20 kWh medidos" in salida
    for nombre in ("real", "plan", "referencia"):
        assert f"  {nombre}" in salida
    assert "ahorro frente a la referencia" in salida
    assert cli.main(["planificador", "simular", "--dia", "2026-11-09"]) == 1  # sin enchufe ese día
    assert cli.main(["planificador", "plan", "--instante", "2026-11-11 25:00"]) == 1
