"""Texto de `planificador plan` y `planificador simular` (hora de Madrid)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from energia_monitor.plan_carga.modelo import Entrada, Franja, Plan
from energia_monitor.plan_carga.servicio import Contexto, Simulacion
from energia_monitor.tiempo import MADRID

DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _t(instante: datetime) -> str:
    local = instante.astimezone(MADRID)
    return f"{DIAS[local.weekday()]} {local:%d/%m %H:%M}"


def _n(x: Decimal | None, decimales: int = 1) -> str:
    return "—" if x is None else f"{x:.{decimales}f}".replace(".", ",")


def coste_franja(plan: Plan, f: Franja, factor: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    """(precio medio €/kWh, coste €) de la franja: kWh × factor_contador × precio, como v_plan_franja."""
    qs = [c for c in plan.cuartos if f.inicio <= c.inicio < f.fin]
    kwh = sum((c.kwh for c in qs), Decimal(0))
    if factor is None or any(c.precio is None for c in qs) or not qs:
        return None, None
    euros = sum((c.kwh * c.precio for c in qs), Decimal(0))  # type: ignore[operator]
    return (euros / kwh if kwh else None), euros * factor


def lineas_plan(e: Entrada, ctx: Contexto, plan: Plan, milisegundos: Decimal) -> list[str]:
    pct = e.nivel_kwh / ctx.capacidad_kwh * 100 if ctx.capacidad_kwh else Decimal(0)
    lineas = [
        f"Plan a las {_t(e.ahora)} · modo {ctx.modo} · {milisegundos} ms",
        f"  nivel {'sin lectura' if ctx.sin_nivel else f'{_n(e.nivel_kwh)} kWh ({_n(pct, 0)} %)'}"
        f" · {'enchufado' if e.conectado else 'no enchufado'}"
        f" · umbral {_n(e.umbral, 4)} €/kWh"
        f" · {'modo prudente' if e.prudente else 'disponibilidad aprendida'} ({ctx.dias_historial} días de historial)",
        "Salidas:",
    ]
    for k, s in enumerate(plan.salidas):
        # En modo prudente solo se planifica la próxima salida; las demás, al volver a enchufar.
        pendiente = e.prudente and k > 0
        marcas = [m for m, si in (("salto de umbral", s.salto_umbral),
                                  ("se planifica al volver a enchufar", pendiente and not s.cubierta),
                                  ("NO CUBIERTA", not pendiente and not s.cubierta)) if si]
        lineas.append(f"  {_t(s.instante)}  objetivo {_n(s.objetivo_kwh)}  consumo {_n(s.consumo_kwh)}"
                      f"  necesaria {_n(s.necesaria_kwh)}  al salir {_n(s.nivel_salida_kwh)} kWh"
                      + (f"  [{', '.join(marcas)}]" if marcas else ""))
    if not plan.salidas:
        lineas.append("  ninguna en el horizonte")
    lineas.append(f"Franjas ({plan.tareas} tarea{'s' if plan.tareas != 1 else ''} en el cargador):")
    total_kwh, total_eur = Decimal(0), Decimal(0)
    for f in plan.franjas:
        precio, euros = coste_franja(plan, f, ctx.factor_contador)
        total_kwh += f.kwh
        total_eur += euros or 0
        motivo = f.motivo + (f" ({_t(f.salida)})" if f.salida else "")
        marcas = [m for m, si in (("en curso", f.en_curso), ("provisional", f.provisional),
                                  (f"{f.tareas} tareas", f.tareas > 1),
                                  (f"choca con {', '.join(map(str, f.choca_con))}", bool(f.choca_con))) if si]
        lineas.append(f"  {_t(f.inicio)} – {f.fin.astimezone(MADRID):%H:%M}  {motivo:<26} {_n(f.kwh):>5} kWh"
                      f"  {_n(precio, 4)} €/kWh  {_n(euros, 2)} €" + (f"  [{', '.join(marcas)}]" if marcas else ""))
    if not plan.franjas:
        lineas.append("  ninguna")
    else:
        lineas.append(f"  total {_n(total_kwh)} kWh, {_n(total_eur, 2)} €")
    for aviso in plan.avisos:
        lineas.append(f"AVISO: {aviso}")
    return lineas


def lineas_simulacion(s: Simulacion) -> list[str]:
    lineas = lineas_plan(s.entrada, s.contexto, s.plan, s.milisegundos)
    if s.conexion is None or s.ahorro is None:
        return [*lineas, "Sin conexión cerrada que contenga ese instante: no hay comparación."]
    a = s.ahorro
    inicio, fin = s.conexion
    lineas.append(f"Conexión {_t(inicio)} – {_t(fin)}: {_n(a.energia_cargador_kwh, 2)} kWh medidos por el cargador")
    if a.sin_precio:
        return [*lineas, "  Falta el precio o factor_contador de algún cuarto: sin costes."]
    for nombre, euros in (("real", a.coste_real_eur), ("plan", a.coste_plan_eur),
                          ("referencia", a.coste_referencia_eur)):
        medio = euros / a.energia_cargador_kwh if euros is not None and a.energia_cargador_kwh else None
        lineas.append(f"  {nombre:<11} {_n(euros, 2):>7} €  ({_n(medio, 4)} €/kWh del contador)")
    if not a.plan_cubre:
        lineas.append(f"  el plan tenía {_n(a.plan_kwh, 2)} kWh: el resto, en los primeros cuartos libres")
    if a.coste_referencia_eur is not None and a.coste_plan_eur is not None and a.coste_real_eur is not None:
        lineas.append(f"  ahorro frente a la referencia: plan {_n(a.coste_referencia_eur - a.coste_plan_eur, 2)} €,"
                      f" real {_n(a.coste_referencia_eur - a.coste_real_eur, 2)} €")
    return lineas
