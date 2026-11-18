"""Comandos del planificador que hablan con el cargador (paso 3c). Se ejecutan en el contenedor muestreo, que es
el que tiene VIARIS_URL:  docker compose exec muestreo python -m energia_monitor planificador ...

- modo activo|sombra|pausa: encola el cambio (lo aplica el muestreo al momento).
- tareas: lectura del programador con cada tarea clasificada (guardiana, propia o ajena).
- reconciliar --en-seco: lo que escribiría ahora, sin escribir nada.
- limpiar --confirmar: pasa a sombra y borra las tareas propias salvo la guardiana.
- guardiana --id N --confirmar: traslada la guardiana a otro id (crea la nueva antes de borrar la vieja).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor import parametros
from energia_monitor.plan_carga import acciones, escritura
from energia_monitor.plan_carga.reconciliar import GUARDIANA, Operacion, Propia, _coincide, anotar_choques, cuerpo, \
    es_guardiana, fecha_guardiana, reconciliar
from energia_monitor.tiempo import MADRID
from energia_monitor.viaris import ClienteViaris, Tarea

DIAS = ("D", "L", "M", "X", "J", "V", "S")
AVISO_SOLO_GUARDIANA = ("Con solo la guardiana activa el cargador no carga fuera de ella: el coche no cargará hasta "
                        "que vuelvas a activo o actives tus tareas en la app.")


def _programacion(t: Tarea) -> str:
    franja = f"{t.hour_min // 60:02d}:{t.hour_min % 60:02d} + {t.duracion} min"
    if t.dia_mes:
        return f"{t.dia_mes[0]:02d}/{t.dia_mes[1]:02d} {franja}"
    if t.weekday == 0 and t.day == 0 and t.month == 0:
        return f"todos los días {franja}"
    dias = "".join(d for i, d in enumerate(DIAS) if t.weekday & (1 << i)) or "?"
    return f"semanal {dias} {franja}"


def modo(conn: psycopg.Connection, nuevo: str, nota: str | None) -> list[str]:
    acciones.encolar(conn, "modo", {"modo": nuevo, "nota": nota or "CLI"})
    lineas = [f"Modo {nuevo} encolado: el muestreo lo aplica al momento y reconcilia el cargador."]
    if nuevo == "activo":
        lineas.append("En activo el planificador escribe sus franjas en el cargador. Comprueba con "
                      "«planificador tareas» y en la app.")
    return lineas


def tareas(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime) -> list[str]:
    leidas = escritura.leer(conn, cliente, ahora)
    if leidas is None:
        return ["No se pudo leer el programador del cargador."]
    registradas = {p.id: p for p in escritura.propias(conn)}
    gid = escritura.guardiana_id(conn, ahora)
    lineas = [f"Programador del cargador ({len(leidas)} de 10 tareas):"]
    for t in sorted(leidas, key=lambda x: x.id):
        if t.id == gid:
            papel = "guardiana" + ("" if es_guardiana(t) else " (¡no es la guardiana!)")
        elif t.id in registradas:
            papel = f"propia ({registradas[t.id].estado})"
        else:
            papel = "ajena (app u otra)"
        lineas.append(f"  {t.id:>2}  {'activa  ' if t.activa else 'inactiva'}  {_programacion(t):<32} {papel}")
    libres = [i for i in range(1, 11) if i not in {t.id for t in leidas}]
    lineas.append(f"Libres: {', '.join(map(str, libres)) or 'ninguno'}.")
    if not any(t.activa for t in leidas):
        lineas.append("AVISO: ninguna tarea activa: el cargador carga libremente al enchufar.")
    return lineas


def en_seco(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime) -> list[str]:
    leidas = escritura.leer(conn, cliente, ahora)
    if leidas is None:
        return ["No se pudo leer el programador del cargador."]
    modo_ = conn.execute("SELECT planificador_modo_en(%s)", (ahora,)).fetchone()[0]  # type: ignore[index]
    congelar = timedelta(minutes=int(escritura._param(conn, "congelar_franja_min", ahora)))
    gid = escritura.guardiana_id(conn, ahora)
    propias = escritura.propias(conn)
    r = reconciliar(ahora, "activo" if modo_ == "sombra" else modo_, gid, leidas, propias,
                    escritura.deseadas_actuales(conn, ahora), congelar)
    operaciones = anotar_choques(r.operaciones, leidas, (p.id for p in propias), gid)
    if modo_ == "sombra":
        cabecera = (f"En seco (modo sombra): lo que escribiría en activo con tus tareas de la app desactivadas: "
                    f"{len(operaciones)} escritura(s).")
    else:
        cabecera = f"En seco (modo {modo_}): {len(operaciones)} escritura(s)."
    lineas = [cabecera]
    for op in operaciones:
        t = op.cuerpo["tasks"][0]
        detalle = ""
        if "initTime" in t:
            it = t["initTime"]
            franja = it["timeList"][0]
            detalle = (f" {it['day'].bit_length():02d}/{it['month'].bit_length():02d} "
                       f"{franja['hourMin'] // 60:02d}:{franja['hourMin'] % 60:02d} + {franja['duration']} min"
                       f" activa={t['active']}")
        lineas.append(f"  {op.metodo:<6} tarea {op.id:>2}{detalle} · {op.motivo}")
    for a in r.avisos:
        lineas.append(f"AVISO ({a.nivel}): {a.titulo}. {a.detalle}")
    return lineas


def limpiar(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime) -> list[str]:
    """Pasa a sombra y borra, bajo el lock del cargador, solo las tareas registradas en planificador_tarea como
    franja propia que siguen en el cargador tal como se escribieron. Nunca la guardiana (papel guardiana, ni
    ninguna tarea igual a ella), ni las ajenas (no están registradas), ni una propia cambiada fuera (se informa)."""
    conn.execute("INSERT INTO planificador_modo (desde, modo, origen, nota) VALUES (%s, 'sombra', 'usuario', %s)",
                 (ahora, "planificador limpiar"))
    conn.execute("SELECT pg_advisory_lock(%s)", (escritura.LOCK_CARGADOR,))
    lineas: list[str] = []
    try:
        leidas = {t.id: t for t in escritura.leer(conn, cliente, ahora) or []}
        if not leidas:
            return ["Modo sombra. No se pudo leer el programador: no se borra nada."]
        gid = escritura.guardiana_id(conn, ahora)
        ops = []
        for p in escritura.propias(conn):
            t = leidas.get(p.id)
            if p.papel != "franja" or p.id == gid or t is None or es_guardiana(t):
                continue
            if not _coincide(t, p):
                lineas.append(f"Tarea {p.id}: no está como la escribió el planificador; no se borra (revísala en la app).")
                continue
            ops.append(Operacion("DELETE", p.id, {"tasks": [{"id": p.id}]}, "planificador limpiar", None))
        hechas, fallo = escritura.escribir(conn, cliente, ahora, ops, False)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (escritura.LOCK_CARGADOR,))
    lineas.insert(0, f"Modo sombra. Borradas {hechas} de {len(ops)} tareas propias"
                     + (f" ({', '.join(str(o.id) for o in ops)})." if ops else "."))
    if fallo:
        lineas.append(f"ERROR: {fallo}")
    lineas.append(AVISO_SOLO_GUARDIANA)
    return lineas


def guardiana(conn: psycopg.Connection, cliente: ClienteViaris, ahora: datetime, nuevo: int) -> list[str]:
    """Crea la guardiana en el id nuevo (libre), la confirma, cambia id_tarea_guardiana y borra la vieja."""
    if not 1 <= nuevo <= 10:
        return ["El id debe estar entre 1 y 10."]
    conn.execute("SELECT pg_advisory_lock(%s)", (escritura.LOCK_CARGADOR,))
    try:
        leidas = {t.id: t for t in escritura.leer(conn, cliente, ahora) or []}
        if not leidas:
            return ["No se pudo leer el programador del cargador."]
        viejo = escritura.guardiana_id(conn, ahora)
        if nuevo == viejo:
            return [f"La guardiana ya usa el id {nuevo}."]
        if nuevo in leidas:
            return [f"El id {nuevo} está ocupado en el cargador: elige uno libre."]
        fecha = fecha_guardiana(ahora)
        # La vieja deja de ser «la» guardiana en la tabla (índice único) pero sigue activa hasta el final.
        conn.execute("UPDATE planificador_tarea SET papel = 'franja' WHERE id_tarea = %s AND papel = 'guardiana'",
                     (viejo,))
        nueva = Propia(nuevo, "guardiana", "guardiana", fecha, GUARDIANA[2], GUARDIANA[3], True)
        hechas, fallo = escritura.escribir(conn, cliente, ahora, [
            Operacion("POST", nuevo, cuerpo(nuevo, fecha, 0, 1, True), "trasladar la guardiana", nueva)], False)
        if fallo:
            conn.execute("UPDATE planificador_tarea SET papel = 'guardiana' WHERE id_tarea = %s", (viejo,))
            return [f"ERROR: no se pudo crear la guardiana nueva: {fallo}. La vieja ({viejo}) sigue."]
        parametros.registrar_parametro_bateria(conn, clave="id_tarea_guardiana", desde=ahora.astimezone(MADRID).date(),
                                               valor=Decimal(nuevo), origen="usuario", fuente="planificador guardiana --id")
        lineas = [f"Guardiana nueva en el id {nuevo}; id_tarea_guardiana = {nuevo} desde hoy."]
        if viejo in leidas and es_guardiana(leidas[viejo]):
            _, fallo = escritura.escribir(conn, cliente, ahora, [
                Operacion("DELETE", viejo, {"tasks": [{"id": viejo}]}, "trasladar la guardiana", None)], False)
            lineas.append(f"ERROR al borrar la vieja ({viejo}): {fallo}" if fallo else f"Borrada la vieja ({viejo}).")
        else:
            conn.execute("DELETE FROM planificador_tarea WHERE id_tarea = %s", (viejo,))
            lineas.append(f"El id {viejo} no era la guardiana en el cargador: no se toca.")
        return lineas
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (escritura.LOCK_CARGADOR,))

