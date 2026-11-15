"""Secuencia real del 13 al 14/11/2026 (muestras-2026-11-13.log) con la referencia en Python.

Enchufe a las 20:23, carga de 03:00 a 06:00 que termina por el planificador
(fin de franja) y desconexión a las 06:59, hora de Madrid. Mismo recorrido
contra PostgreSQL, por el muestreo, en integration/test_bateria_db.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D

from muestras_log import entradas, leer, sesion_14_11

from energia_monitor import bateria


def test_fichero_leido_entero() -> None:
    lecturas = leer()
    assert len(lecturas) == 1430
    assert sum(1 for lec in lecturas if lec.modulador is None) == 8
    assert lecturas[0].instante == datetime(2026, 11, 13, 11, 45, 52, tzinfo=UTC)
    assert lecturas[-1].instante.astimezone(bateria.MADRID).strftime("%d/%m %H:%M") == "14/11 12:46"


def test_cambios_de_estado() -> None:
    e, _ = entradas()
    assert [(c.detectado_en.strftime("%d %H:%M:%S"), c.estado) for c in e.estados] == [
        ("13 11:45:52", 1), ("13 19:23:11", 4), ("14 02:00:18", 6), ("14 05:00:44", 4), ("14 05:59:17", 1)]


def test_sesion_real() -> None:
    s = sesion_14_11()
    assert (s.inicio, s.fin, s.energia_wh, s.origen_fin) == (
        datetime(2026, 11, 14, 2, 0, tzinfo=UTC), datetime(2026, 11, 14, 5, 0, 1, tzinfo=UTC), D(13667), "schedman")


def test_un_trayecto_abierto_desde_la_desconexion() -> None:
    e, ahora = entradas()
    r = bateria.recalcular(e, ahora)
    # El primer estado (libre) no tiene un enchufe anterior: no es un trayecto.
    (tr,) = r.trayectos
    assert (tr.salida, tr.llegada, tr.corto) == (datetime(2026, 11, 14, 5, 59, 17, tzinfo=UTC), None, False)
    assert tr.origen_consumo == "inicial" and tr.consumo_kwh is None
    est = bateria.estado(e, r, ahora)
    assert (est.coche, est.consumo_en_curso_kwh) == ("fuera", 25)


def test_fin_de_franja_no_es_carga_completa() -> None:
    e, ahora = entradas()
    (s,) = bateria.recalcular(e, ahora).sesiones
    assert s.cierre == "fin_franja"


def test_la_misma_sesion_por_evsm_seria_carga_completa() -> None:
    # Control de la regla: en la primera muestra tras el fin (05:00:44, 43 s después) el conector
    # está en 4. Con origen evsm, la regla la daría por completa.
    e, ahora = entradas()
    e.sesiones = [replace(e.sesiones[0], origen_fin="evsm")]
    (s,) = bateria.recalcular(e, ahora).sesiones
    assert s.cierre == "carga_completa"


def test_nivel_tras_la_carga_con_una_lectura_previa() -> None:
    e, ahora = entradas()
    e.lecturas.append(bateria.Lectura(1, datetime(2026, 11, 13, 20, 0, tzinfo=UTC), D(40)))
    r = bateria.recalcular(e, ahora)
    sesion = next(ev for ev in r.eventos if ev.tipo == "sesion")
    assert sesion.nivel_kwh == D(40) / 100 * D("91.3") + D("13.667") * D("0.86")
    # Después de la sesión no hay energía pendiente: el coche se fue sin volver a cargar.
    assert bateria.estado(e, r, ahora).energia_pendiente_kwh == 0
