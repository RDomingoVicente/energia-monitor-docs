"""Escritura del plan en el cargador (paso 3c): lectura del programador, reconciliación y escrituras confirmadas.

Solo lo usa el servicio muestreo (único proceso que habla con el cargador), bajo un advisory lock para que nunca
escriban dos a la vez. Cada tarea propia se registra como pendiente antes del POST; tras cada escritura, GET del
programador para confirmarla. Toda escritura queda en plan_auditoria; en sombra, en seco (lo que haría). Una
escritura fallida corta la tanda: se reintenta en el ciclo siguiente.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from energia_monitor.plan_carga.modelo import Ocupado
from energia_monitor.plan_carga.reconciliar import (
    anotar_choques,
    Aviso,
    Deseada,
    Operacion,
    Propia,
    cubierto,
    deseadas,
    ocurrencias,
    reconciliar,
)
from energia_monitor.viaris import ClienteViaris, CuerpoNoPermitido, ErrorCargador, Tarea, parsear_programador

log = logging.getLogger(__name__)

LOCK_CARGADOR = 7_265_305


@dataclass(frozen=True)
class Resultado:
    leida: bool
    en_seco: bool
    escritas: int
    fallo: str | None
    avisos: tuple[Aviso, ...]
    operaciones: tuple[Operacion, ...]


def _param(conn: psycopg.Connection, clave: str, ahora: datetime) -> Any:
    fila = conn.execute("SELECT bateria_param(%s, %s)", (clave, ahora)).fetchone()
    assert fila is not None
    return fila[0]


def guardiana_id(conn: psycopg.Connection, ahora: datetime) -> int:
    return int(_param(conn, "id_tarea_guardiana", ahora))


def propias(conn: psycopg.Connection) -> list[Propia]:
    return [Propia(*f) for f in conn.execute(
        "SELECT id_tarea, papel, origen, fecha, hour_min, duracion, activa, franja_inicio, franja_fin, estado"
        " FROM planificador_tarea ORDER BY id_tarea")]


def leer(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime) -> list[Tarea] | None:
    """GET del programador; la lectura (o su error) queda en planificador_lectura."""
    try:
        tareas = cliente.tareas()
    except ErrorCargador as e:
        conn.execute("INSERT INTO planificador_lectura (id, leida_en, ok, tareas, error) VALUES (true, %s, false, NULL, %s)"
                     " ON CONFLICT (id) DO UPDATE SET leida_en = EXCLUDED.leida_en, ok = false, error = EXCLUDED.error",
                     (ahora, str(e)[:300]))
        return None
    conn.execute("INSERT INTO planificador_lectura (id, leida_en, ok, tareas, error) VALUES (true, %s, true, %s, NULL)"
                 " ON CONFLICT (id) DO UPDATE SET leida_en = EXCLUDED.leida_en, ok = true, tareas = EXCLUDED.tareas,"
                 " error = NULL", (ahora, Jsonb([t.datos for t in tareas])))
    return tareas


def lectura_con_fecha(conn: psycopg.Connection) -> tuple[datetime, list[Tarea]] | None:
    """(leida_en, tareas) de la última lectura correcta del programador; None si no la hay o falló."""
    fila = conn.execute("SELECT leida_en FROM planificador_lectura WHERE ok").fetchone()
    tareas = ultima_lectura(conn)
    return None if fila is None or tareas is None else (fila[0], tareas)


def carga_cubierta(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime,
                   desde: datetime | None) -> bool | None:
    """¿Alguna tarea activa cubre la carga de ahora? (aviso «Cargador distinto del plan», 0023).

    Con la última lectura del programador; si no la cubre y esa lectura es anterior a `desde` (inicio de la
    racha sin tarea, o ahora), se vuelve a leer (GET) para no avisar por una tarea creada después. None: sin
    lectura, no se sabe."""
    lc = lectura_con_fecha(conn)
    if lc is not None and cubierto(lc[1], ahora):
        return True
    if lc is None or lc[0] < (desde or ahora):
        tareas = leer(conn, cliente, ahora)
        return None if tareas is None else cubierto(tareas, ahora)
    return False


def ultima_lectura(conn: psycopg.Connection) -> list[Tarea] | None:
    fila = conn.execute("SELECT ok, tareas FROM planificador_lectura").fetchone()
    if fila is None or not fila[0]:
        return None
    return parsear_programador([{"name": "mennekes", "data": {"cfg": {"tasks": fila[1]}}}])


def ocupados(conn: psycopg.Connection, ahora: datetime, hasta: datetime) -> list[Ocupado]:
    """Entrada del planificador (E3.4): tareas ajenas activas de la última lectura, franjas manuales, bloqueos y
    franjas propias congeladas (empiezan antes de congelar_franja_min)."""
    salida: list[Ocupado] = []
    registradas = {p.id: p for p in propias(conn)}
    gid = guardiana_id(conn, ahora)
    for t in ultima_lectura(conn) or []:
        if t.activa and t.id not in registradas and t.id != gid:
            salida += [Ocupado(a, b, "ajena", t.id) for a, b in ocurrencias(t, ahora, hasta)]
    salida += [Ocupado(a, b, "manual") for a, b in conn.execute(
        "SELECT inicio, fin FROM plan_manual WHERE anulada_en IS NULL AND fin > %s", (ahora,))]
    salida += [Ocupado(a, b, "bloqueo") for a, b in conn.execute(
        "SELECT inicio, fin FROM plan_bloqueo WHERE fin > %s", (ahora,))]
    congelar = timedelta(minutes=int(_param(conn, "congelar_franja_min", ahora)))
    salida += [Ocupado(p.inicio, p.fin, "congelada", p.id) for p in registradas.values()
               if p.papel == "franja" and ahora <= p.inicio < ahora + congelar]
    return salida


def deseadas_actuales(conn: psycopg.Connection, ahora: datetime) -> list[Deseada]:
    """Franjas del último cálculo dentro de horizonte_provisional_h y franjas manuales, partidas en tareas."""
    horizonte = ahora + timedelta(hours=int(_param(conn, "horizonte_provisional_h", ahora)))
    franjas = [(i, f, "automatica") for i, f in conn.execute(
        "SELECT f.inicio, f.fin FROM plan_franja f WHERE f.calculo_id = (SELECT id FROM plan_calculo"
        " ORDER BY ahora DESC, id DESC LIMIT 1) AND f.fin > %s AND f.inicio < %s", (ahora, horizonte))]
    franjas += [(i, f, "manual") for i, f in conn.execute(
        "SELECT inicio, fin FROM plan_manual WHERE anulada_en IS NULL AND fin > %s", (ahora,))]
    return deseadas(franjas)


def guardar_avisos(conn: psycopg.Connection, ahora: datetime, avisos: Sequence[Aviso],
                   origen: str = "reconciliar") -> None:
    """Los avisos de este origen en planificador_aviso = los de este ciclo (conserva el «desde» de los que siguen).
    Origen reconciliar: los del reconciliador; muestreo: «Cargador distinto del plan» (0023)."""
    with conn.transaction():
        conn.execute("DELETE FROM planificador_aviso WHERE origen = %s AND NOT (clave = ANY(%s))",
                     (origen, [a.clave for a in avisos]))
        for a in avisos:
            conn.execute("INSERT INTO planificador_aviso (clave, nivel, titulo, detalle, desde, regla, origen)"
                         " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (clave) DO UPDATE SET nivel = EXCLUDED.nivel,"
                         " titulo = EXCLUDED.titulo, detalle = EXCLUDED.detalle, regla = EXCLUDED.regla,"
                         " origen = EXCLUDED.origen",
                         (a.clave, a.nivel, a.titulo, a.detalle, a.desde or ahora, a.regla, origen))


def _registrar(conn: psycopg.Connection, p: Propia, estado: str, error: str | None = None) -> None:
    conn.execute(
        "INSERT INTO planificador_tarea (id_tarea, papel, origen, fecha, hour_min, duracion, activa, franja_inicio,"
        " franja_fin, estado, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (id_tarea) DO UPDATE SET papel = EXCLUDED.papel, origen = EXCLUDED.origen,"
        " fecha = EXCLUDED.fecha, hour_min = EXCLUDED.hour_min, duracion = EXCLUDED.duracion,"
        " activa = EXCLUDED.activa, franja_inicio = EXCLUDED.franja_inicio, franja_fin = EXCLUDED.franja_fin,"
        " estado = EXCLUDED.estado, error = EXCLUDED.error, actualizada_en = now()",
        (p.id, p.papel, p.origen, p.fecha, p.hora_min, p.duracion, p.activa, p.franja_inicio, p.franja_fin,
         estado, error))


def _auditar(conn: psycopg.Connection, ahora: datetime, op: Operacion, en_seco: bool, resultado: str,
             respuesta: Any = None, accion_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO plan_auditoria (instante, metodo, id_tarea, cuerpo, en_seco, resultado, respuesta, motivo,"
        " accion_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (ahora, op.metodo, op.id, Jsonb(op.cuerpo), en_seco, resultado,
         None if respuesta is None else Jsonb(respuesta), op.motivo, accion_id))


def _confirmada(op: Operacion, lectura: Sequence[Tarea]) -> bool:
    """Tras la escritura, la tarea está (o ya no está) como se quería."""
    t = next((x for x in lectura if x.id == op.id), None)
    if op.metodo == "DELETE":
        return t is None
    if t is None or op.registro is None:
        return False
    p = op.registro
    return t.dia_mes == (p.fecha.day, p.fecha.month) and (t.hour_min, t.duracion, t.activa) == (
        p.hora_min, p.duracion, p.activa)


def escribir(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime, operaciones: Sequence[Operacion],
             en_seco: bool, accion_id: int | None = None) -> tuple[int, str | None]:
    """Aplica una tanda: (escrituras confirmadas, fallo). En seco solo se audita."""
    if en_seco:
        for op in operaciones:
            _auditar(conn, ahora, op, True, "en_seco", accion_id=accion_id)
        return 0, None
    hechas = 0
    for op in operaciones:
        if op.metodo == "POST" and op.registro is not None:
            _registrar(conn, op.registro, "pendiente")  # antes del POST: si se cae aquí, la tarea ya es propia
        ids = frozenset(i for (i,) in conn.execute("SELECT id_tarea FROM planificador_tarea"))
        try:
            respuesta = cliente.escribir(op.metodo, op.cuerpo, ids)
        except (ErrorCargador, CuerpoNoPermitido) as e:
            _auditar(conn, ahora, op, False, "error", {"error": str(e)[:300]}, accion_id)
            if op.registro is not None:
                _registrar(conn, op.registro, "error", str(e)[:300])
            return hechas, f"{op.metodo} de la tarea {op.id}: {e}"
        confirmacion = leer(conn, cliente, ahora)
        if confirmacion is None or not _confirmada(op, confirmacion):
            _auditar(conn, ahora, op, False, "error", _resumen(respuesta), accion_id)
            motivo = "sin lectura de confirmación" if confirmacion is None else "el cargador no quedó como se escribió"
            if op.registro is not None:
                _registrar(conn, op.registro, "error", motivo)
            return hechas, f"{op.metodo} de la tarea {op.id}: {motivo}"
        _auditar(conn, ahora, op, False, "ok", _resumen(respuesta), accion_id)
        if op.registro is None:
            conn.execute("DELETE FROM planificador_tarea WHERE id_tarea = %s", (op.id,))
        else:
            _registrar(conn, op.registro, "confirmada")
        hechas += 1
    return hechas, None


def _resumen(respuesta: Any) -> Any:
    """La respuesta de una escritura es la configuración completa del conector: se guarda tal cual (sin
    credenciales: es el programador), recortada si fuera enorme."""
    texto = json.dumps(respuesta)
    return respuesta if len(texto) <= 20000 else {"recortada": texto[:20000]}


def reconciliar_y_escribir(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime,
                           modo: str | None = None, accion_id: int | None = None) -> Resultado:
    """Un ciclo: lee el programador, reconcilia con el plan y escribe (en sombra, en seco), bajo el lock."""
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_CARGADOR,))
    try:
        if modo is None:
            modo = conn.execute("SELECT planificador_modo_en(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
        lectura = leer(conn, cliente, ahora)
        if lectura is None:
            aviso = Aviso("programador_sin_lectura", "aviso", "No se pudo leer el programador del cargador",
                          "Sin lectura no se escribe nada; se reintenta en el ciclo siguiente.", "GET del programador")
            guardar_avisos(conn, ahora, [aviso])
            return Resultado(False, modo == "sombra", 0, "sin lectura del programador", (aviso,), ())
        congelar = timedelta(minutes=int(_param(conn, "congelar_franja_min", ahora)))
        r = reconciliar(ahora, modo if modo != "sombra" else "activo", guardiana_id(conn, ahora), lectura,
                        propias(conn), deseadas_actuales(conn, ahora), congelar)
        en_seco = modo == "sombra"
        for p in r.adoptar:  # solo base de datos: la guardiana ya existe en el cargador
            _registrar(conn, p, "confirmada")
        if not en_seco:
            if r.olvidar:
                conn.execute("DELETE FROM planificador_tarea WHERE id_tarea = ANY(%s)", (list(r.olvidar),))
        operaciones = r.operaciones
        if en_seco:
            # El plan de sombra no respeta las tareas de la app: en la auditoría se anota con cuáles chocaría.
            operaciones = anotar_choques(operaciones, lectura, (p.id for p in propias(conn)),
                                         guardiana_id(conn, ahora))
        hechas, fallo = escribir(conn, cliente, ahora, operaciones, en_seco, accion_id)
        avisos = list(r.avisos)
        if fallo:
            avisos.append(Aviso("escritura_fallida", "aviso", "Escritura fallida en el cargador", fallo,
                                "escritura o confirmación fallida"))
        guardar_avisos(conn, ahora, avisos)
        if fallo:
            log.warning("planificador: %s", fallo)
        elif hechas:
            log.info("planificador: %d escritura(s) confirmadas en el cargador", hechas)
        return Resultado(True, en_seco, hechas, fallo, tuple(avisos), operaciones)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_CARGADOR,))

