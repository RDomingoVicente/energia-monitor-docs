"""Datos de ejemplo para los tests de interfaz, sin base de datos: el escenario de las maquetas
(lectura el sáb 14/11 19:42, carga la noche del 15/11, dos trayectos el lun 16/11), con textos
largos para provocar desbordamientos. Sustituye las funciones de energia_monitor.formulario.datos."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from typing import Any

from energia_monitor.formulario import datos as d

AHORA = datetime(2026, 11, 16, 18, 52, tzinfo=UTC)  # lun 16/11 19:52 en Madrid
NOTA_LARGA = "Viaje a Zaragoza por la autovía con parada larga en el área de servicio"


class ConexionFalsa:
    def __enter__(self) -> ConexionFalsa:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def estado(_conn: object = None, _ahora: object = None) -> dict[str, Any]:
    return dict(
        ahora=AHORA, capacidad_kwh=D("91.3"), nivel_kwh=D("34.7"), nivel_pct=D(38), energia_pendiente_kwh=D(0),
        ultimo_absoluto=datetime(2026, 11, 14, 18, 42, tzinfo=UTC), ultimo_absoluto_tipo="lectura", trayectos_desde=2,
        coche="en_casa", estado_conector=4, fuera_desde=None, consumo_en_curso_kwh=D(0), nivel_plan_kwh=D("34.7"),
        dudoso=False, factor_vigente=D("0.86"), factor_sugerido=D("0.84"), observaciones_factor=5,
        proxima_salida=datetime(2026, 11, 17, 8, 30, tzinfo=UTC), tipo_salida="horario", consumo_plan_kwh=D(31),
        origen_consumo_plan="historia", reserva_kwh=D("22.8"), tope_kwh=D("91.3"), objetivo_kwh=D("53.8"),
        objetivo_pct=D(59), energia_necesaria_kwh=D("19.2"), confianza="estimado",
        ultima_lectura_ts=datetime(2026, 11, 14, 18, 42, tzinfo=UTC), ultima_lectura_pct=D(52),
        kwh_cargados_desde_lectura=D("19.3"), trayectos_desde_lectura=2, kwh_trayectos_desde_lectura=D("29.2"))


def plan(_conn: object = None, _ahora: object = None) -> d.Plan:
    return d.Plan(D(31), "historia", D(25), D(100), D("91.3"))


def cabecera(_conn: object, _ahora: object, cargador: str) -> d.Cabecera:
    avisos = [d.Aviso("sin_lecturas", "aviso", "Sin lecturas", "Última lectura: 14/11 19:42.", None, "lectura"),
              d.Aviso("calibracion_sugerida", "info", "Calibración sugerida", "Eficiencia sugerida 0,840.", None,
                      "parametros")]
    return d.Cabecera(avisos, AHORA - timedelta(minutes=1), True, AHORA - timedelta(minutes=10), cargador)


def inicio(_conn: object, _ahora: object) -> d.Inicio:
    dias = []
    for i in range(7):
        f = date(2026, 11, 17) + timedelta(days=i)
        if f.weekday() >= 5:
            dias.append(d.Dia(f, None, "horario · sin salida", "horario", None, None))
        elif i == 2:
            dias.append(d.Dia(f, None, "sin salida", "sin-salida", None, None))
        elif i == 4:
            dias.append(d.Dia(f, datetime.combine(f, time(6, 0), UTC), "excepción · mínimo 80 %", "excepcion",
                              D("41.4"), D(80)))
        else:
            dias.append(d.Dia(f, datetime.combine(f, time(8, 30), UTC), "horario", "horario", D(31), D(59)))
    eventos = [d.Evento(AHORA, "conexion", None, False),
               d.Evento(AHORA - timedelta(minutes=12), "trayecto", D("-31.74"), True, D("-10.96")),
               d.Evento(AHORA - timedelta(hours=9), "trayecto", D("-14.6"), True),
               d.Evento(AHORA - timedelta(hours=37), "carga", D("15.34"), False, None, D("18.259")),
               d.Evento(AHORA - timedelta(days=2), "lectura", D(52), False)]
    # Conectado ayer y con carga terminada: el texto más largo de la casilla del coche. 4,6 kW: potencia real.
    conexion = d.Conexion(AHORA - timedelta(hours=25, minutes=52), None, AHORA - timedelta(hours=13, minutes=52))
    ini = d.Inicio(AHORA, estado(), dias, eventos, conexion, D(4600), {"estimado"}, D(25), D(100), None, True, True)
    return ini


def excepciones(_conn: object, _ahora: object) -> d.Excepciones:
    proximas = [
        d.ExcepcionProxima(1, date(2026, 11, 19), "sin_salida", None, None, None, None, None, "Teletrabajo", None, None,
                           False, False),
        d.ExcepcionProxima(2, date(2026, 11, 22), "con_salida", time(7, 0), "indicado", D(180), None, D(70), NOTA_LARGA,
                           D("41.4"), D(70), True, False),
        d.ExcepcionProxima(3, date(2026, 11, 28), "con_salida", time(6, 30), "normal", None, None, D(80), None, D(31),
                           D(80), True, False),
    ]
    pasadas = [d.ExcepcionPasada(4, date(2026, 11, 5), "con_salida", D("48.3"), D("51.9"), "cumplida"),
               d.ExcepcionPasada(5, date(2026, 10, 27), "sin_salida", D(0), D(0), "cumplida"),
               d.ExcepcionPasada(6, date(2026, 10, 24), "con_salida", None, None, "dada_de_baja")]
    return d.Excepciones(AHORA, proximas, pasadas, plan())


def excepcion(_conn: object, id_: int, activa: bool | None = None) -> dict[str, Any] | None:
    return {"id": id_, "tipo": "con_salida", "fecha": date(2026, 11, 22), "hora_salida": time(7, 0),
            "consumo_modo": "indicado", "km": D(180), "pct": None, "pct_objetivo": D(70), "nota": NOTA_LARGA,
            "anulada_en": None}


def historial(_conn: object, _ahora: object, dias: int | None, con_anuladas: bool) -> d.Historial:
    t = datetime(2026, 11, 13, 19, 15, tzinfo=UTC)
    filas = [
        d.FilaHistorial("lectura", 9, t, D(52), D(55), D(-3), True, False, None, None, None, None, None),
        d.FilaHistorial("carga_externa", 3, t - timedelta(days=5), None, None, D(31), False, False,
                        "Ionity Zaragoza, cargador rápido junto a la autovía", None, None, D("32.5"), D("14.6")),
        d.FilaHistorial("lectura", 8, t - timedelta(days=5, hours=4), D(48), D(47), D(1), True, False, NOTA_LARGA,
                        None, t - timedelta(days=5, hours=4, minutes=1), None, None),
        d.FilaHistorial("lectura", 7, t - timedelta(days=5, hours=4, minutes=1), D(18), D(47), None, False, True, None,
                        "Tecleado mal, era 48 %", None, None, None),
        d.FilaHistorial("carga_completa", None, t - timedelta(days=11), D(100), None, None, False, False, None, None,
                        None, None, None),
    ]
    return d.Historial(AHORA, dias, con_anuladas, filas, D("91.3"), D(7), t, 1)


def lectura(_conn: object, id_: int) -> dict[str, Any] | None:
    return {"id": id_, "instante": datetime(2026, 11, 13, 19, 15, tzinfo=UTC), "pct": D(52), "nota": NOTA_LARGA,
            "anulada_en": None, "motivo": None, "corrige": None}


def cargas_externas(_conn: object, _ahora: object) -> d.CargasExternas:
    filas = [d.CargaExternaFila(3, datetime(2026, 11, 8, 15, 20, tzinfo=UTC), D("32.5"), D("14.6"),
                                "Ionity Zaragoza, cargador rápido junto a la autovía", D(31), D("0.449")),
             d.CargaExternaFila(2, datetime(2026, 10, 24, 10, 5, tzinfo=UTC), None, D("9.8"), None, None, None)]
    return d.CargasExternas(AHORA, filas, D("0.86"), D("91.3"))


def carga_externa(_conn: object, id_: int) -> dict[str, Any] | None:
    return {"id": id_, "instante": datetime(2026, 11, 8, 15, 20, tzinfo=UTC), "kwh": D("32.5"), "coste_eur": D("14.6"),
            "nota": "Ionity Zaragoza", "anulada_en": None, "motivo": None, "corrige": None}


def horario(_conn: object, _ahora: object) -> d.HorarioDatos:
    dias = {1: time(9, 30), 2: time(9, 30), 3: time(9, 30), 4: time(9, 30), 5: time(7, 0), 6: None, 7: None}
    return d.HorarioDatos(dias, datetime(2026, 11, 1, 9, tzinfo=UTC), plan())


def parametros(_conn: object, _ahora: object) -> d.Parametros:
    textos = {"capacidad_util_kwh": ("Capacidad útil", "kWh", 50, 120, "91.3"),
              "reserva_pct": ("Reserva mínima", "%", 10, 60, 25), "tope_pct": ("Tope de carga", "%", 50, 100, 100),
              "factor_bateria": ("Eficiencia de carga", "factor", "0.70", "1.00", "0.86"),
              "percentil_planificacion": ("Percentil de planificación", "fracción", "0.50", "0.95", "0.75"),
              "ventana_consumo_dias": ("Ventana de consumo", "días", 7, 90, 28),
              "desconexion_min_s": ("Desconexión sin consumo", "s", 0, 14400, 1800),
              "consumo_inicial_trayecto_kwh": ("Consumo inicial por trayecto", "kWh", 1, 90, 25),
              "consumo_kwh_100km": ("Consumo medio", "kWh/100 km", 10, 40, 23),
              "energia_min_calibracion_kwh": ("Energía mínima para calibrar", "kWh", 1, 30, 5),
              "observaciones_calibracion": ("Observaciones para calibrar", "n", 1, 20, 5),
              "detectar_carga_completa": ("Detectar carga completa", "sí/no", 0, 1, 1),
              "hueco_muestreo_aviso_s": ("Aviso hueco de muestreo", "s", 300, 14400, 1800),
              "aviso_sin_lectura_dias": ("Aviso sin lecturas", "días", 1, 14, 7),
              "umbral_carga_externa_kwh": ("Umbral de carga externa", "kWh", 1, 30, 5)}
    vigentes = [d.Parametro(c, n, u, D(str(mi)), D(str(ma)),
                            "Fracción de la energía medida por el cargador que llega a la batería (pérdidas de carga). "
                            "Valor inicial estimado.",
                            "Se calibra con lecturas antes y después de cargar, sin trayectos entre ellas. No afecta al "
                            "coste: se paga lo que mide el cargador.", D(str(v)), "usuario", date(2025, 1, 1), i)
                for i, (c, (n, u, mi, ma, v)) in enumerate(textos.items(), 1)]
    cambios = [d.Cambio(20, "consumo_inicial_trayecto_kwh", date(2026, 11, 13), D(25), None,
                        "indicado por el usuario al revisar el consumo de la semana"),
               d.Cambio(19, "desconexion_min_s", date(2026, 11, 13), D(1200), D(1800), "formulario")]
    return d.Parametros(AHORA, vigentes, cambios, d.Calibracion(D("0.84"), D("0.86"), 5, D("0.81"), D("0.87")))


def parametro_registrado(_conn: object, _id: int) -> tuple[str, D, date, str] | None:
    return None


def avisos(_conn: object, _ahora: object) -> d.Avisos:
    activos = [
        d.AvisoActivo("sin_lecturas", "aviso", "Sin lecturas", "Última lectura: 14/11 19:42.", AHORA - timedelta(days=1),
                      "nivel ESTIMADO y más de aviso_sin_lectura_dias sin lecturas", "lectura"),
        d.AvisoActivo("carga_externa_posible:1", "aviso", "¿Cargaste fuera de casa?",
                      "La lectura del 16/11 19:42 muestra 12,4 kWh más de lo esperado.", AHORA,
                      "residuo de la reconciliación > umbral_carga_externa_kwh", "carga_externa"),
        d.AvisoActivo("calibracion_sugerida", "info", "Calibración sugerida",
                      "Eficiencia de carga sugerida 0.840 (vigente 0.86, 5 observación(es)).", None, "E2.6",
                      "parametros"),
    ]
    resueltos = [d.AvisoResuelto("Hueco de muestreo", "42 min sin muestras del cargador",
                                 AHORA - timedelta(days=4, minutes=42), AHORA - timedelta(days=4))]
    return d.Avisos(AHORA, activos, resueltos)


def instalar(monkeypatch: Any) -> None:
    """Sustituye las consultas por estos datos."""
    for nombre in ("estado", "plan", "cabecera", "inicio", "excepciones", "excepcion", "historial", "lectura",
                   "cargas_externas", "carga_externa", "horario", "parametros", "parametro_registrado", "avisos"):
        monkeypatch.setattr(d, nombre, globals()[nombre])
    monkeypatch.setattr(d, "version_bd", lambda _conn: "9999")
    monkeypatch.setattr(d, "lectura_guardada", lambda _conn, _id: None)
