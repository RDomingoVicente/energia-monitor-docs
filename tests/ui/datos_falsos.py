"""Datos de ejemplo para los tests de interfaz, sin base de datos: el escenario de las maquetas
(lectura el sáb 14/11 19:42, carga la noche del 15/11, dos trayectos el lun 16/11), con textos
largos para provocar desbordamientos. Sustituye las funciones de energia_monitor.formulario.datos."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from typing import Any

from energia_monitor.formulario import datos as d
from energia_monitor.formulario import plan_datos as pd
from energia_monitor.tiempo import MADRID

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
              "umbral_carga_externa_kwh": ("Umbral de carga externa", "kWh", 1, 30, 5),
              # 0020: planificador de carga.
              "carga_oportunista": ("Carga oportunista", "sí/no", 0, 1, 1),
              "umbral_oportunista_pct": ("Umbral barato", "%", 20, 90, 60),
              "nivel_oportunista_pct": ("Nivel oportunista", "%", 50, 100, 100),
              "margen_carga_pct": ("Margen de carga", "%", 0, 30, 10),
              "margen_salida_min": ("Margen antes de salir", "min", 0, 120, 30),
              "potencia_carga_inicial_kw": ("Potencia de carga inicial", "kW", "1.4", 11, "4.5"),
              "ventana_disponibilidad_dias": ("Ventana de disponibilidad", "días", 7, 90, 28),
              "umbral_disponibilidad_pct": ("Umbral de disponibilidad", "%", 50, 100, 80),
              "historial_min_disponibilidad_dias": ("Historial mínimo", "días", 7, 60, 21),
              "horizonte_provisional_h": ("Horizonte escrito", "h", 24, 72, 48),
              "franja_min_min": ("Franja mínima", "min", 15, 120, 30),
              "max_tareas_sistema": ("Franjas máximas", "n", 1, 9, 7),
              "id_tarea_guardiana": ("Tarea guardiana", "n", 1, 10, 9),
              "aviso_planificador_min": ("Aviso planificador parado", "min", 15, 360, 60),
              "ventana_precio_esperado_dias": ("Ventana del precio esperado", "días", 7, 90, 28),
              "ventana_umbral_dias": ("Ventana del umbral", "días", 3, 28, 7),
              "ventana_potencia_dias": ("Ventana de potencia", "días", 7, 90, 28),
              "horizonte_plan_h": ("Horizonte del plan", "h", 24, 96, 72),
              "hora_publicacion_precios": ("Publicación de precios", "hora", 720, 1080, 810)}
    # Grupo y orden de la 0020 (E3.9).
    grupos = {1: ("capacidad_util_kwh", "reserva_pct", "tope_pct", "factor_bateria", "consumo_kwh_100km"),
              2: ("desconexion_min_s", "detectar_carga_completa", "energia_min_calibracion_kwh",
                  "observaciones_calibracion", "umbral_carga_externa_kwh"),
              3: ("percentil_planificacion", "ventana_consumo_dias", "consumo_inicial_trayecto_kwh"),
              4: ("margen_carga_pct", "margen_salida_min", "carga_oportunista", "umbral_oportunista_pct",
                  "nivel_oportunista_pct", "ventana_precio_esperado_dias", "ventana_umbral_dias"),
              5: ("ventana_disponibilidad_dias", "umbral_disponibilidad_pct", "historial_min_disponibilidad_dias"),
              6: ("potencia_carga_inicial_kw", "ventana_potencia_dias", "franja_min_min", "max_tareas_sistema",
                  "horizonte_provisional_h", "horizonte_plan_h", "hora_publicacion_precios", "id_tarea_guardiana"),
              7: ("aviso_sin_lectura_dias", "hueco_muestreo_aviso_s", "aviso_planificador_min")}
    grupo_de = {c: g for g, claves in grupos.items() for c in claves}
    textos = {c: textos[c] for g in grupos for c in grupos[g]}
    vigentes = [d.Parametro(c, n, u, D(str(mi)), D(str(ma)),
                            "Fracción de la energía medida por el cargador que llega a la batería (pérdidas de carga). "
                            "Valor inicial estimado.",
                            "Se calibra con lecturas antes y después de cargar, sin trayectos entre ellas. No afecta al "
                            "coste: se paga lo que mide el cargador.", D(str(v)), "usuario", date(2025, 1, 1), i,
                            grupo_de[c], c != "id_tarea_guardiana", NOTAS.get(c))
                for i, (c, (n, u, mi, ma, v)) in enumerate(textos.items(), 1)]
    grupos_ = [d.Grupo(1, "Coche y batería", "¿Cómo es el coche?", False, True),
               d.Grupo(2, "Estimación del nivel", "¿Cómo se calcula el % sin lecturas?", False, False),
               d.Grupo(3, "Previsión de consumo", "¿Cuánto voy a gastar?", False, False),
               d.Grupo(4, "Estrategia de carga", "¿Cuánto cargar y a qué precio?", False, True),
               d.Grupo(5, "Disponibilidad del coche", "¿Cuándo estará enchufado?", False, False),
               d.Grupo(6, "Cargador y franjas", "¿Cómo se escribe en el cargador?", True, False),
               d.Grupo(7, "Avisos y vigilancia", "¿Cuándo me avisa?", False, False)]
    notas = {"umbral_oportunista_pct": "de la media de 7 días · hoy 0,211 €/kWh",
             "historial_min_disponibilidad_dias": "historial actual: 4 días → modo prudente",
             "max_tareas_sistema": "+ guardiana = 8; 2 huecos para la app"}
    cambios = [d.Cambio(20, "consumo_inicial_trayecto_kwh", date(2026, 11, 13), D(25), None,
                        "indicado por el usuario al revisar el consumo de la semana"),
               d.Cambio(19, "desconexion_min_s", date(2026, 11, 13), D(1200), D(1800), "formulario"),
               d.Cambio(18, "carga_oportunista", date(2025, 1, 1), D(1), None, "Especificación de la etapa 3 (E3.9)", 14)]
    return d.Parametros(AHORA, vigentes, cambios, d.Calibracion(D("0.84"), D("0.86"), 5, D("0.81"), D("0.87")),
                        grupos_, notas)


NOTAS = {"observaciones_calibracion": "cargas necesarias para sugerir la eficiencia",
         "margen_carga_pct": "sobre la energía necesaria; solo bajo el umbral",
         "ventana_umbral_dias": "días completos para la media del umbral",
         "nivel_oportunista_pct": "nunca por encima del tope",
         "ventana_precio_esperado_dias": "para los cuartos sin precio publicado",
         "potencia_carga_inicial_kw": "solo sin sesiones medidas", "ventana_potencia_dias": "mediana por hora local",
         "horizonte_plan_h": "o hasta la última salida conocida",
         "hora_publicacion_precios": "precios de D+1 publicados desde esta hora de D",
         "aviso_planificador_min": "correo desde el NAS"}


# --- Plan de carga (maqueta 01d): franja en curso, otra provisional y una fuera de la vista ---------------------

def _franjas() -> list[pd.FranjaPlan]:
    madrid = AHORA.astimezone(MADRID)
    hoy = madrid.replace(hour=0, minute=0, second=0, microsecond=0)
    salida = (hoy + timedelta(days=1, hours=9, minutes=30)).astimezone(UTC)
    curso = pd.FranjaPlan((hoy + timedelta(hours=17)).astimezone(UTC), (hoy + timedelta(hours=23)).astimezone(UTC),
                          D("27.0"), False, 1, True, D("0.154"), D("4.86"),
                          (("necesaria", salida, D("7.4")), ("margen", salida, D("0.7")), ("oportunista", None, D("18.9"))),
                          "en su horario · coche cargando", D("12.4"))
    noche = pd.FranjaPlan((hoy + timedelta(days=1, hours=23)).astimezone(UTC),
                          (hoy + timedelta(days=2, hours=2, minutes=30)).astimezone(UTC), D("15.8"), True, 2, False,
                          D("0.231"), D("4.09"), (("necesaria", salida + timedelta(days=1), D("15.8")),), "programada")
    lejos = pd.FranjaPlan((hoy + timedelta(days=2, hours=14)).astimezone(UTC),
                          (hoy + timedelta(days=2, hours=15)).astimezone(UTC), D("4.5"), True, 1, False, D("0.190"),
                          D("0.86"), (("oportunista", None, D("4.5")),), "programada")
    return [curso, noche, lejos]


def _linea(desde: datetime, horas: int, franjas: list[pd.FranjaPlan], salidas: list[datetime]) -> pd.Linea:
    precios_ = [D("0.31"), D("0.30"), D("0.29"), D("0.22"), D("0.16"), D("0.13"), D("0.15"), D("0.24"), D("0.38"),
                D("0.44"), D("0.39"), D("0.33"), D("0.32"), D("0.35"), D("0.41"), D("0.33"), D("0.27"), D("0.25"),
                D("0.23"), D("0.30"), D("0.52"), D("0.58"), D("0.49"), D("0.37")]
    horas_ = [pd.Hora(desde + timedelta(hours=k), precios_[k % 24], k >= horas - 14) for k in range(horas)]
    hasta = desde + timedelta(hours=horas)
    coche = [(desde, min(salidas[0], hasta), "enchufado"),
             (min(salidas[0] + timedelta(hours=9), hasta), min(salidas[0] + timedelta(hours=23), hasta), "probable")]
    return pd.Linea(desde, hasta, AHORA, D("0.211"), horas_, [f for f in franjas if f.inicio < hasta and f.fin > desde],
                    [c for c in coche if c[0] < c[1]], [s for s in salidas if desde <= s < hasta],
                    [f for f in franjas if f.inicio >= hasta])


def plan_carga(_conn: object, _ahora: object) -> pd.PlanCarga:
    franjas = _franjas()
    salidas = [pd.SalidaPlan(franjas[0].desglose[0][1], D("53.9"), True),  # type: ignore[arg-type]
               pd.SalidaPlan(franjas[1].desglose[0][1], D("53.9"), True)]  # type: ignore[arg-type]
    hoy = AHORA.astimezone(MADRID).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    hora = AHORA.replace(minute=0, second=0, microsecond=0)
    instantes = [s.instante for s in salidas]
    pasadas = [pd.FranjaPasada(AHORA - timedelta(days=1, hours=16), AHORA - timedelta(days=1, hours=12), True, D("18.0"),
                               (("necesaria", D("6.0")), ("oportunista", D("12.0"))), D("17.6"), D("2.71"), True),
               pd.FranjaPasada(AHORA - timedelta(days=2, hours=15), AHORA - timedelta(days=2, hours=13), True, D("9.0"),
                               (("oportunista", D("9.0")),), D("0"), None, False)]
    ahorro = [pd.Ahorro("hoy", 1, D("1.86"), D("1.52"), D("0.10"), D("3.10"), 0),
              pd.Ahorro("7 días", 5, D("14.20"), D("11.95"), D("0.80"), D("22.40"), 0),
              pd.Ahorro("30 días", 5, D("14.20"), D("11.95"), D("0.80"), D("22.40"), 0)]
    return pd.PlanCarga(AHORA, "sombra", AHORA - timedelta(minutes=4), True, True, D("91.3"), D("0.86"), D("50.2"),
                        D("71.5"), D("0.211"), D(60), 7, D(80), 7, franjas[-1].fin, salidas, franjas, pasadas, ahorro,
                        _linea(hoy, 60, franjas, instantes), _linea(hora, 24, franjas, instantes))


def proximas_cargas(_conn: object, _ahora: object, n: int = 3) -> tuple[str, list[pd.FranjaPlan]]:
    return "sombra", _franjas()[:n]


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
    monkeypatch.setattr(pd, "plan_carga", plan_carga)
    monkeypatch.setattr(pd, "proximas_cargas", proximas_cargas)
    monkeypatch.setattr(d, "lectura_guardada", lambda _conn, _id: None)
