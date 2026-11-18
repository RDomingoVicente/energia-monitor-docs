"""Reconciliación del plan con el programador del cargador (paso 3c; «Plan del paso 3c» de docs/fase2.md).

Función pura: recibe la lectura del programador, las tareas propias registradas y las tareas que quiere el
plan, y devuelve las escrituras (en orden) y los avisos. No escribe nada: eso lo hace el servicio, que registra
cada tarea como pendiente antes del POST y confirma cada escritura con un GET.

Reglas:
- La guardiana va primero. Nunca se desactiva ni se borra. Si falta, se crea; si está inactiva, se activa; si
  su id lo ocupa otra tarea que no es propia, no se crea encima (aviso crítico). Mientras la guardiana no esté
  bien, no se escribe nada más: así el cargador nunca se queda sin tareas activas.
- Las tareas ajenas (las que no están registradas como propias) nunca se tocan.
- Las tareas propias terminadas se borran siempre (las máscaras no llevan año: se repetirían al año siguiente);
  aviso crítico si alguna lleva un rato caducada.
- La franja en curso y las que empiezan antes de congelar_franja_min no se cambian.
- Las modificadas desde fuera (p. ej. guardadas en la app, que las convierte en semanales) se corrigen, con aviso.
- Solo se escriben las diferencias (idempotente). Pausa: desactiva las propias futuras y no crea nada.
- Orden de la tanda: guardiana, borrados, modificaciones, altas.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from energia_monitor.plan_carga import franjas as fr
from energia_monitor.tiempo import MADRID
from energia_monitor.viaris import MAX_TAREAS, Tarea

GUARDIANA = (31, 12, 0, 1)  # día, mes, hourMin, duration: 31/12 a las 00:00 durante 1 minuto
CADUCADA_TRAS = timedelta(minutes=30)  # una tarea propia terminada sin borrar tras este margen: aviso crítico


@dataclass(frozen=True)
class Propia:
    """Fila de planificador_tarea."""

    id: int
    papel: str  # guardiana | franja
    origen: str  # automatica | manual | guardiana
    fecha: date
    hora_min: int
    duracion: int
    activa: bool
    franja_inicio: datetime | None = None
    franja_fin: datetime | None = None
    estado: str = "confirmada"  # pendiente | confirmada | error

    @property
    def inicio(self) -> datetime:
        return instante(self.fecha, self.hora_min)

    @property
    def fin(self) -> datetime:
        return instante(self.fecha, self.hora_min + self.duracion)

    def mismo_horario(self, d: Deseada) -> bool:
        return (self.fecha, self.hora_min, self.duracion) == (d.fecha, d.hora_min, d.duracion)


@dataclass(frozen=True)
class Deseada:
    """Una tarea con fecha que el plan quiere en el cargador (una franja, o media si cruza la medianoche)."""

    fecha: date
    hora_min: int
    duracion: int
    origen: str  # automatica | manual
    franja_inicio: datetime
    franja_fin: datetime

    @property
    def inicio(self) -> datetime:
        return instante(self.fecha, self.hora_min)

    @property
    def fin(self) -> datetime:
        return instante(self.fecha, self.hora_min + self.duracion)


@dataclass(frozen=True)
class Operacion:
    metodo: str  # POST | PUT | DELETE
    id: int
    cuerpo: dict[str, Any]
    motivo: str
    registro: Propia | None  # cómo queda la tarea propia tras la escritura; None: se borra


@dataclass(frozen=True)
class Aviso:
    clave: str
    nivel: str  # error | aviso | info
    titulo: str
    detalle: str
    regla: str


@dataclass(frozen=True)
class Reconciliacion:
    operaciones: tuple[Operacion, ...]
    avisos: tuple[Aviso, ...]
    adoptar: tuple[Propia, ...] = ()  # se registran como propias sin escribir (la guardiana que ya existe)
    olvidar: tuple[int, ...] = ()  # registros sin tarea en el cargador (POST que no llegó o borrada fuera)
    libres: tuple[int, ...] = field(default_factory=tuple)


def instante(fecha: date, minutos: int) -> datetime:
    """Minuto local (puede ser 1440: medianoche del día siguiente) a UTC; reloj local, como el cargador."""
    dia = fecha + timedelta(days=minutos // 1440)
    resto = minutos % 1440
    return datetime(dia.year, dia.month, dia.day, resto // 60, resto % 60, tzinfo=MADRID).astimezone(UTC)


def deseadas(franjas: Iterable[tuple[datetime, datetime, str]]) -> list[Deseada]:
    """(inicio, fin, origen) → tareas con fecha, partidas en cada medianoche local (E3.1)."""
    salida = []
    for inicio, fin, origen in franjas:
        for t in fr.tareas(inicio, fin):
            salida.append(Deseada(t.fecha, t.hora_min, t.duracion, origen, inicio, fin))
    return salida


def cuerpo(id_: int, fecha: date, hora_min: int, duracion: int, activa: bool,
           base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tarea con fecha (weekday 0). Sin base: el cuerpo mínimo que envía la interfaz al crear (verificado el
    13/11). Con base (la tarea leída): la tarea completa sin los campos calculados «_…», como hace la interfaz al
    modificar (PUT verificado con la tarea completa)."""
    tiempo = {"day": 1 << (fecha.day - 1), "month": 1 << (fecha.month - 1), "weekday": 0,
              "timeList": [{"hourMin": hora_min, "duration": duracion}]}
    if base is None:
        tarea: dict[str, Any] = {"id": id_, "active": int(activa), "user": "", "group": 0, "priority": 1,
                                 "initTime": tiempo}
    else:
        tarea = {k: v for k, v in base.items() if not k.startswith("_") and k != "initTime"}
        franja = {k: v for k, v in base["initTime"]["timeList"][0].items() if not k.startswith("_")}
        franja.update(hourMin=hora_min, duration=duracion)
        tarea.update(id=id_, active=int(activa), initTime={**tiempo, "timeList": [franja]})
    return {"tasks": [tarea]}


def es_guardiana(t: Tarea) -> bool:
    return t.dia_mes == GUARDIANA[:2] and (t.hour_min, t.duracion) == GUARDIANA[2:] and t.tipo == 0


def fecha_guardiana(ahora: datetime) -> date:
    return date(ahora.astimezone(MADRID).year, 12, 31)


def _coincide(t: Tarea, p: Propia) -> bool:
    """La tarea del cargador es como se escribió (fecha, horario y estado)."""
    return t.dia_mes == (p.fecha.day, p.fecha.month) and (t.hour_min, t.duracion, t.activa) == (
        p.hora_min, p.duracion, p.activa) and t.tipo == 0


def ocurrencias(t: Tarea, desde: datetime, hasta: datetime) -> list[tuple[datetime, datetime]]:
    """Intervalos en [desde, hasta) en que una tarea (semanal, diaria o con fecha) está programada."""
    salida = []
    dia = desde.astimezone(MADRID).date() - timedelta(days=1)
    while instante(dia, 0) < hasta:
        bit_semana = 1 << ((dia.weekday() + 1) % 7)  # bit 0 = domingo
        if ((t.weekday == 0 or t.weekday & bit_semana) and (t.day == 0 or t.day & (1 << (dia.day - 1)))
                and (t.month == 0 or t.month & (1 << (dia.month - 1)))):
            a = instante(dia, t.hour_min)
            b = instante(dia, min(t.hour_min + t.duracion, 1440))  # una tarea no cruza la medianoche (E3.1)
            if a < hasta and b > desde:
                salida.append((a, b))
        dia += timedelta(days=1)
    return salida


def reconciliar(ahora: datetime, modo: str, guardiana_id: int, lectura: Sequence[Tarea],
                propias: Sequence[Propia], deseadas_: Sequence[Deseada], congelar: timedelta) -> Reconciliacion:
    leidas = {t.id: t for t in lectura}
    registradas = {p.id: p for p in propias}
    avisos: list[Aviso] = []
    ops: list[Operacion] = []
    adoptar: list[Propia] = []
    olvidar: list[int] = []

    if not any(t.activa for t in lectura):
        avisos.append(Aviso("ninguna_tarea_activa", "error", "Ninguna tarea activa en el cargador",
                            "Sin ninguna tarea activa, el cargador carga libremente en cuanto se enchufa el coche.",
                            "lectura del programador sin tareas activas"))

    # --- Guardiana -----------------------------------------------------------------------------
    fecha_g = fecha_guardiana(ahora)
    registro_g = Propia(guardiana_id, "guardiana", "guardiana", fecha_g, GUARDIANA[2], GUARDIANA[3], True)
    g = leidas.get(guardiana_id)
    propia_g = registradas.get(guardiana_id)
    guardiana_ok = False
    if g is None:
        if propia_g is not None and propia_g.papel != "guardiana":
            avisos.append(Aviso("guardiana_ocupada", "error", "Id de la tarea guardiana ocupado",
                                f"El id {guardiana_id} está registrado como franja propia.", "id_tarea_guardiana"))
        else:
            ops.append(Operacion("POST", guardiana_id, cuerpo(guardiana_id, fecha_g, 0, 1, True), "crear la guardiana",
                                 registro_g))
            avisos.append(Aviso("guardiana_ausente", "error", "Falta la tarea guardiana",
                                f"No hay tarea {guardiana_id} en el cargador: se crea (31/12 00:00, 1 min).",
                                "guardiana ausente en la lectura del programador"))
    elif not es_guardiana(g):
        if propia_g is not None and propia_g.papel == "guardiana":
            ops.append(Operacion("PUT", guardiana_id, cuerpo(guardiana_id, fecha_g, 0, 1, True, g.datos),
                                 "corregir la guardiana modificada fuera", registro_g))
            avisos.append(Aviso("guardiana_modificada", "aviso", "Tarea guardiana modificada fuera del sistema",
                                "Se vuelve a dejar el 31/12 a las 00:00, 1 minuto, activa.", "guardiana distinta"))
        else:
            avisos.append(Aviso("guardiana_ocupada", "error", "Id de la tarea guardiana ocupado",
                                f"La tarea {guardiana_id} del cargador no es la guardiana y no es propia: no se crea "
                                "encima. Libera el id o cambia id_tarea_guardiana con «planificador guardiana --id N».",
                                "id_tarea_guardiana ocupado por una tarea ajena"))
    elif not g.activa:
        ops.append(Operacion("PUT", guardiana_id, cuerpo(guardiana_id, fecha_g, 0, 1, True, g.datos),
                             "activar la guardiana", registro_g))
        avisos.append(Aviso("guardiana_inactiva", "error", "Tarea guardiana inactiva",
                            "Sin ella, sin franjas activas el cargador carga libremente: se activa.",
                            "guardiana inactiva en la lectura del programador"))
    else:
        guardiana_ok = True
        if propia_g is None or propia_g.papel != "guardiana" or propia_g.fecha != fecha_g:
            adoptar.append(registro_g)

    ocupados = set(leidas) | set(registradas) | {guardiana_id}
    libres = tuple(i for i in range(1, MAX_TAREAS + 1) if i not in ocupados)
    if not guardiana_ok:
        # Mientras la guardiana no esté bien, no se toca nada más.
        return Reconciliacion(tuple(ops), tuple(avisos), tuple(adoptar), (), libres)

    # --- Tareas propias que ya hay --------------------------------------------------------------
    borrar: list[Operacion] = []
    vivas: list[tuple[Propia, Tarea]] = []
    for p in sorted((x for x in propias if x.papel == "franja"), key=lambda x: x.inicio):
        t = leidas.get(p.id)
        if t is None:
            if p.estado != "pendiente":
                avisos.append(Aviso(f"tarea_desaparecida:{p.id}", "aviso", "Tarea propia borrada fuera del sistema",
                                    f"La tarea {p.id} ya no está en el cargador; el plan la vuelve a escribir si "
                                    "hace falta.", "tarea propia ausente en la lectura"))
            olvidar.append(p.id)
            continue
        if p.fin <= ahora:
            borrar.append(Operacion("DELETE", p.id, {"tasks": [{"id": p.id}]}, "borrar la tarea terminada", None))
            if ahora - p.fin > CADUCADA_TRAS:
                avisos.append(Aviso(f"tarea_caducada:{p.id}", "error", "Tarea propia caducada sin borrar",
                                    f"La tarea {p.id} terminó el {p.fin.astimezone(MADRID):%d/%m %H:%M}; sin borrar se "
                                    "repetiría el año que viene.", "tarea propia terminada en el cargador"))
            continue
        if not _coincide(t, p):
            # Al compararla con lo que quiere el plan, más abajo, se corrige (salvo en curso o congelada).
            avisos.append(Aviso(f"tarea_modificada:{p.id}", "aviso", "Tarea propia modificada fuera del sistema",
                                f"La tarea {p.id} no es como se escribió (¿guardada desde la app?): se corrige.",
                                "tarea propia distinta en la lectura"))
        vivas.append((p, t))

    # --- Qué se queda, qué se cambia ---------------------------------------------------------------
    pendientes_d = [d for d in deseadas_ if d.fin > ahora]
    cambiar: list[Operacion] = []
    altas: list[Operacion] = []
    sobrantes: list[tuple[Propia, Tarea]] = []
    for p, t in vivas:
        intocable = p.inicio <= ahora < p.fin or ahora <= p.inicio < ahora + congelar
        igual = next((d for d in pendientes_d if p.mismo_horario(d)), None)
        if igual is not None:
            pendientes_d.remove(igual)
        if intocable:
            # En curso o congelada: se queda como está (también si el plan ya no la quiere).
            pendientes_d = [d for d in pendientes_d if not fr.solapa(d.inicio, d.fin, p.inicio, p.fin)]
            continue
        if modo == "pausa":
            if t.activa:
                cambiar.append(Operacion("PUT", p.id, cuerpo(p.id, p.fecha, p.hora_min, p.duracion, False, t.datos),
                                         "pausa: desactivar la franja futura", replace(p, activa=False)))
            continue
        if igual is not None:
            if not _coincide(t, replace(p, activa=True)):
                cambiar.append(Operacion("PUT", p.id, cuerpo(p.id, p.fecha, p.hora_min, p.duracion, True, t.datos),
                                         "dejarla como la quiere el plan", replace(p, activa=True)))
            continue
        sobrantes.append((p, t))

    if modo != "pausa":
        # Las propias que sobran se reutilizan para las deseadas que faltan (PUT); el resto se borra.
        pendientes_d.sort(key=lambda d: d.inicio)
        for p, t in sobrantes:
            if pendientes_d:
                d = pendientes_d.pop(0)
                cambiar.append(Operacion("PUT", p.id, cuerpo(p.id, d.fecha, d.hora_min, d.duracion, True, t.datos),
                                         "mover la franja", Propia(p.id, "franja", d.origen, d.fecha, d.hora_min,
                                                                   d.duracion, True, d.franja_inicio, d.franja_fin)))
            else:
                borrar.append(Operacion("DELETE", p.id, {"tasks": [{"id": p.id}]}, "el plan ya no la quiere", None))
        huecos = list(libres)
        for d in pendientes_d:
            if not huecos:
                avisos.append(Aviso("sin_huecos", "aviso", "No quedan ids libres en el cargador",
                                    "Alguna tarea del plan queda sin escribir: el cargador admite 10 tareas.",
                                    "ids 1–10 ocupados"))
                break
            i = huecos.pop(0)
            altas.append(Operacion("POST", i, cuerpo(i, d.fecha, d.hora_min, d.duracion, True), "nueva franja",
                                   Propia(i, "franja", d.origen, d.fecha, d.hora_min, d.duracion, True,
                                          d.franja_inicio, d.franja_fin, "pendiente")))

    return Reconciliacion(tuple(ops + borrar + cambiar + altas), tuple(avisos), tuple(adoptar), tuple(olvidar),
                          libres)
