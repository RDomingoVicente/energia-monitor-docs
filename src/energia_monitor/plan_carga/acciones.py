"""Acciones del formulario sobre el plan (paso 3c, E3.4 y E3.5): cola en plan_accion que procesa el muestreo.

La tabla es la cola: el formulario inserta (y NOTIFY planificador despierta al muestreo), el muestreo procesa las
pendientes al arrancar, en cada ciclo y al recibir el aviso. El formulario valida antes de encolar (422) con
validar_manual; el muestreo vuelve a validar al procesar, con la lectura del programador más reciente.

- modo: registra el modo (sombra, activo, pausa) desde ahora.
- anadir / modificar: franja manual fijada (modificar anula la anterior y, si era automática, la bloquea).
- eliminar: futura; manual se anula, automática se bloquea hasta su fin original.
- detener: franja en curso; desactiva sus tareas al momento (con un reintento inmediato) y bloquea el resto.
- cargar_ahora: franja manual desde ahora hasta el objetivo de la próxima salida (o el nivel oportunista).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from energia_monitor.plan_carga import escritura
from energia_monitor.plan_carga.franjas import num_tareas, solapa
from energia_monitor.plan_carga.reconciliar import Operacion, Propia, cuerpo, ocurrencias
from energia_monitor.tiempo import MADRID
from energia_monitor.viaris import ClienteViaris, Tarea

log = logging.getLogger(__name__)

MODOS = ("sombra", "activo", "pausa")
# Acciones sobre franjas: solo fuera de sombra (en sombra no se escribe nada, ni por acción manual).
SOLO_FUERA_DE_SOMBRA = ("anadir", "modificar", "eliminar", "detener", "cargar_ahora")
EN_SOMBRA = "Cambia a activo para usarlo."
CARGAR_AHORA_MAX = timedelta(hours=12)


class ErrorAccion(Exception):
    pass


DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _texto_tarea(t: Tarea, a: datetime, b: datetime, propia: bool) -> str:
    """«la tarea 5 de la app (mar 23:00–23:59)»."""
    la, lb = a.astimezone(MADRID), b.astimezone(MADRID)
    quien = "propia" if propia else "de la app"
    return f"la tarea {t.id} {quien} ({DIAS[la.weekday()]} {la:%H:%M}–{lb:%H:%M})"


def validar_manual(inicio: datetime, fin: datetime, ahora: datetime, lectura: Sequence[Tarea],
                   propias: Sequence[Propia], manuales: Sequence[tuple[datetime, datetime]], franja_min: int,
                   max_tareas: int, guardiana: int, ignorar: tuple[datetime, datetime] | None = None) -> str | None:
    """Error para el usuario, o None. Sin solapes con otra tarea activa (propia, de la app o manual: E3.5), fin
    posterior al inicio, al menos franja_min_min y sin pasar de max_tareas_sistema."""
    if fin <= inicio:
        return "El fin debe ser posterior al inicio."
    if fin <= ahora:
        return "La franja ya ha terminado."
    if fin - inicio < timedelta(minutes=franja_min):
        return f"Una franja dura al menos {franja_min} min (franja mínima)."
    registradas = {p.id: p for p in propias}
    for t in lectura:
        if not t.activa or t.id == guardiana:
            continue
        for a, b in ocurrencias(t, inicio - timedelta(days=1), fin + timedelta(days=1)):
            if ignorar is not None and t.id in registradas and solapa(a, b, *ignorar):
                continue
            if solapa(a, b, inicio, fin):
                return f"Choca con {_texto_tarea(t, a, b, t.id in registradas)}."
    for a, b in manuales:
        if (ignorar is None or not solapa(a, b, *ignorar)) and solapa(a, b, inicio, fin):
            return "Choca con otra franja manual."
    usadas = sum(1 for p in propias if p.papel == "franja") + num_tareas(inicio, fin)
    if usadas > max_tareas:
        return f"No caben más franjas: el planificador usa como mucho {max_tareas} tareas."
    return None


def encolar(conn: psycopg.Connection, tipo: str, datos: dict[str, Any]) -> int:
    fila = conn.execute("INSERT INTO plan_accion (tipo, datos) VALUES (%s, %s) RETURNING id",
                        (tipo, Jsonb(datos))).fetchone()
    assert fila is not None
    return int(fila[0])


def _instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto)


def _manuales(conn: psycopg.Connection, ahora: datetime) -> list[tuple[datetime, datetime]]:
    return [(a, b) for a, b in conn.execute(
        "SELECT inicio, fin FROM plan_manual WHERE anulada_en IS NULL AND fin > %s", (ahora,))]


def validar_en_bd(conn: psycopg.Connection, inicio: datetime, fin: datetime, ahora: datetime,
                  ignorar: tuple[datetime, datetime] | None = None) -> str | None:
    lectura = escritura.ultima_lectura(conn) or []
    return validar_manual(inicio, fin, ahora, lectura, escritura.propias(conn), _manuales(conn, ahora),
                          int(escritura._param(conn, "franja_min_min", ahora)),
                          int(escritura._param(conn, "max_tareas_sistema", ahora)),
                          escritura.guardiana_id(conn, ahora), ignorar)


def _quitar(conn: psycopg.Connection, inicio: datetime, fin: datetime, motivo: str, ahora: datetime) -> None:
    """Anula la franja manual que empieza en inicio o, si es automática, la bloquea hasta su fin original."""
    anuladas = conn.execute("UPDATE plan_manual SET anulada_en = %s WHERE anulada_en IS NULL AND inicio = %s"
                            " RETURNING id", (ahora, inicio)).fetchall()
    if not anuladas:
        conn.execute("INSERT INTO plan_bloqueo (inicio, fin, motivo) VALUES (%s, %s, %s)",
                     (max(inicio, ahora), fin, motivo))


def _cargar_ahora_fin(conn: psycopg.Connection, ahora: datetime) -> datetime:
    """Desde ahora hasta el objetivo de la próxima salida o, si ya está cubierto, el nivel oportunista."""
    fila = conn.execute("SELECT nivel_kwh, capacidad_kwh, energia_necesaria_kwh FROM bateria_estado(%s)",
                        (ahora,)).fetchone()
    assert fila is not None
    nivel, capacidad, necesaria = fila
    factor = Decimal(escritura._param(conn, "factor_bateria", ahora))
    if necesaria is None or necesaria <= 0:
        objetivo = Decimal(escritura._param(conn, "nivel_oportunista_pct", ahora)) / 100 * capacidad
        necesaria = max(Decimal(0), objetivo - (nivel or Decimal(0)))
    potencia = Decimal(escritura._param(conn, "potencia_carga_inicial_kw", ahora))
    fila = conn.execute("SELECT potencia_kw FROM plan_potencia_hora(%s) WHERE hora = extract(hour FROM %s"
                        " AT TIME ZONE 'Europe/Madrid')::integer", (ahora, ahora)).fetchone()
    if fila is not None:
        potencia = fila[0]
    minutos = math.ceil(necesaria / factor / potencia * 60 / 15) * 15 if necesaria > 0 else 0
    minimo = int(escritura._param(conn, "franja_min_min", ahora))
    return ahora + min(timedelta(minutes=max(minutos, minimo)), CARGAR_AHORA_MAX)


def _detener(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime, inicio: datetime,
             accion_id: int) -> None:
    """Desactiva al momento las tareas de la franja en curso (reintento inmediato si falla) y la bloquea."""
    tareas = [p for p in escritura.propias(conn) if p.papel == "franja" and p.franja_inicio == inicio]
    if not tareas:
        raise ErrorAccion("No hay ninguna franja propia que empiece a esa hora.")
    lectura = {t.id: t for t in escritura.leer(conn, cliente, ahora) or []}
    fin = max(p.franja_fin or p.fin for p in tareas)
    for p in tareas:
        if p.fin <= ahora or p.id not in lectura:
            continue
        op = Operacion("PUT", p.id, cuerpo(p.id, p.fecha, p.hora_min, p.duracion, False, lectura[p.id].datos),
                       "detener la franja", Propia(**{**p.__dict__, "activa": False}))
        for intento in (1, 2):
            hechas, fallo = escritura.escribir(conn, cliente, ahora, [op], False, accion_id)
            if not fallo:
                break
            if intento == 2:
                raise ErrorAccion(f"No se pudo detener: {fallo}. Pulsa «Reintentar».")
    _quitar(conn, inicio, fin, "detenida", ahora)


def procesar(conn: psycopg.Connection, cliente: ClienteViaris | None, ahora: datetime) -> int:
    """Procesa las acciones pendientes en orden. Devuelve cuántas se han procesado."""
    n = 0
    for ident, tipo, datos in conn.execute(
            "SELECT id, tipo, datos FROM plan_accion WHERE estado = 'pendiente' ORDER BY id").fetchall():
        try:
            resultado = _aplicar(conn, cliente, ahora, ident, tipo, datos)
            conn.execute("UPDATE plan_accion SET estado = 'hecha', procesada_en = %s, intentos = intentos + 1,"
                         " resultado = %s WHERE id = %s", (ahora, resultado, ident))
        except ErrorAccion as e:
            conn.execute("UPDATE plan_accion SET estado = 'error', procesada_en = %s, intentos = intentos + 1,"
                         " resultado = %s WHERE id = %s", (ahora, str(e), ident))
            log.warning("acción %d (%s): %s", ident, tipo, e)
        n += 1
    if n:
        conn.execute("SELECT solicitar_plan('acción del formulario')")
    return n


def _aplicar(conn: psycopg.Connection, cliente: ClienteViaris | None, ahora: datetime, ident: int, tipo: str,
             datos: dict[str, Any]) -> str:
    if tipo in SOLO_FUERA_DE_SOMBRA:
        modo = conn.execute("SELECT planificador_modo_en(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
        if modo == "sombra":
            raise ErrorAccion(f"En sombra no se escribe en el cargador. {EN_SOMBRA}")
    if tipo == "modo":
        modo = datos.get("modo")
        if modo not in MODOS:
            raise ErrorAccion(f"Modo no válido: {modo!r}.")
        conn.execute("INSERT INTO planificador_modo (desde, modo, origen, nota) VALUES (%s, %s, 'usuario', %s)",
                     (ahora, modo, datos.get("nota") or "formulario"))
        return f"modo {modo} desde {ahora:%H:%M}"
    if tipo in ("anadir", "modificar"):
        inicio, fin = _instante(datos["inicio"]), _instante(datos["fin"])
        anterior = (_instante(datos["anterior_inicio"]), _instante(datos["anterior_fin"])) if tipo == "modificar" \
            else None
        error = validar_en_bd(conn, inicio, fin, ahora, anterior)
        if error:
            raise ErrorAccion(error)
        if anterior is not None:
            _quitar(conn, anterior[0], anterior[1], "eliminada", ahora)
        conn.execute("INSERT INTO plan_manual (inicio, fin, origen, nota) VALUES (%s, %s, 'manual', %s)",
                     (inicio, fin, datos.get("nota")))
        return "franja manual guardada"
    if tipo == "eliminar":
        inicio, fin = _instante(datos["inicio"]), _instante(datos["fin"])
        if inicio <= ahora:
            raise ErrorAccion("La franja ya ha empezado: solo se puede detener.")
        _quitar(conn, inicio, fin, "eliminada", ahora)
        return "franja eliminada"
    if tipo == "detener":
        if cliente is None:
            raise ErrorAccion("Sin cargador configurado (VIARIS_URL).")
        _detener(conn, cliente, ahora, _instante(datos["inicio"]), ident)
        return "franja detenida"
    if tipo == "cargar_ahora":
        fin = _cargar_ahora_fin(conn, ahora)
        error = validar_en_bd(conn, ahora, fin, ahora)
        if error:
            raise ErrorAccion(error)
        conn.execute("INSERT INTO plan_manual (inicio, fin, origen, nota) VALUES (%s, %s, 'cargar_ahora', %s)",
                     (ahora, fin, "Cargar ahora"))
        return f"cargar ahora hasta las {fin:%H:%M}"
    raise ErrorAccion(f"Acción desconocida: {tipo}.")
