"""Excepciones (formulario-maquetas.md, 7.2; maqueta 02)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import ExcepcionProxima, Excepciones
from energia_monitor.formulario.html import Html, h, unir
from energia_monitor.formulario.plantilla import banner

ESTADOS = {"cumplida": "cumplida", "no_cumplida": "no cumplida", "dada_de_baja": "dada de baja"}


@dataclass
class FormExcepcion:
    """Valores del formulario (texto tal cual) y errores por campo, para el 422."""

    tipo: str = "con_salida"
    fecha: str = ""
    hora: str = ""
    consumo: str = "normal"
    km: str = ""
    pct: str = ""
    pct_objetivo: str = ""
    nota: str = ""
    modificar: int | None = None  # id de la excepción que se modifica
    errores: dict[str, str] = field(default_factory=dict)


def _dia(d: date) -> str:
    return f"{f.DIAS[d.weekday()]} {d:%d/%m}"


def _tag(tipo: str) -> Html:
    return (Html('<span class="tag excepcion">con salida</span>') if tipo == "con_salida"
            else Html('<span class="tag sin-salida">sin salida</span>'))


def _num(x: Decimal | None) -> str:
    return "" if x is None else f"{x.normalize():f}"


def _consumo_detalle(x: ExcepcionProxima) -> str:
    if x.consumo_modo == "indicado":
        return f"indicado · {_num(x.km)} km" if x.km is not None else f"indicado · {_num(x.pct)} %"
    return "normal · P75"


def _acciones(x: ExcepcionProxima, baja: str) -> Html:
    return h('<div class="acciones-fila"><a class="mod" href="/excepciones?modificar={}#formulario">Modificar</a>'
             '<form action="/excepciones/{}/baja" method="post"><button type="submit" class="del">{}</button></form>'
             '</div>', x.id, x.id, baja)


def _proximas(d: Excepciones) -> Html:
    filas, items = [], []
    for x in d.proximas:
        # Escritorio: la nota va siempre en una fila propia a todo el ancho, sin borde con la de su excepción.
        tr = Html('<tr class="con-nota">') if x.nota else Html("<tr>")
        if x.tipo == "sin_salida":
            filas.append(h('{}<td class="m fecha">{}</td><td>{}</td><td class="m tenue">—</td>'
                           '<td class="r m tenue">0,0 kWh</td><td class="r m tenue">—</td><td class="r">{}</td></tr>',
                           tr, _dia(x.fecha), _tag(x.tipo), _acciones(x, "Dar de baja")))
            detalle = Html("")
        else:
            minimo = Html('<span class="detalle-celda">mínimo</span>') if x.minimo else Html("")
            filas.append(h('{}<td class="m fecha">{}</td><td>{}</td><td class="m">{}</td><td class="r m">{}'
                           '<span class="detalle-celda">{}</span></td><td class="r m">{}{}</td><td class="r">{}</td>'
                           '</tr>', tr, _dia(x.fecha), _tag(x.tipo), f"{x.hora_salida:%H:%M}", f.kwh(x.previsto_kwh),
                           _consumo_detalle(x), f.pct(x.objetivo_pct), minimo, _acciones(x, "Dar de baja")))
            obj = f"obj. {f.pct(x.objetivo_pct)}{' (mínimo)' if x.minimo else ''}"
            consumo = (f"{_num(x.km)} km" if x.km is not None else f"{_num(x.pct)} %") \
                if x.consumo_modo == "indicado" else "normal"
            detalle = h('<p class="k m">{} · {}<br>{} · {}</p>', f"{x.hora_salida:%H:%M}", obj, consumo,
                        f.kwh(x.previsto_kwh))
        if x.nota:
            filas.append(h('<tr class="fila-nota"><td colspan="6">{}</td></tr>', x.nota))
        nota_movil = h('<p class="k">{}</p>', x.nota) if x.nota and x.tipo == "sin_salida" else Html("")
        items.append(h('<div class="it"><div><p class="m dia">{} {}</p>{}{}</div>{}</div>',
                       _dia(x.fecha), _tag(x.tipo), detalle, nota_movil, _acciones(x, "Baja")))
    n = len(d.proximas)
    resumen = f"{n} activa{'s' if n != 1 else ''} · 1 por fecha"
    if not d.proximas:
        vacio = Html('<p class="vacio">No hay excepciones próximas: se sigue el horario.</p>')
        tabla, lista = vacio, vacio
    else:
        tabla = h('<table><thead><tr><th>Fecha</th><th>Tipo</th><th>Salida</th><th class="r">Consumo prev.</th>'
                  '<th class="r">Objetivo</th><th class="r"><span class="oculto">Acciones</span></th></tr></thead>'
                  '<tbody>{}</tbody></table>', unir(filas))
        lista = unir(items)
    return h('<section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Próximas excepciones</h2>'
             '<span class="k m">{}</span></div>{}</section>'
             '<section class="pn solo-movil orden-2"><div class="ph"><h2 class="pt">Próximas</h2>'
             '<span class="k m">{}</span></div><div class="lista-acciones">{}</div></section>',
             resumen, tabla, f"{n} activa{'s' if n != 1 else ''}", lista)


def _pasadas(d: Excepciones) -> Html:
    filas = []
    for x in d.pasadas:
        if x.estado == "dada_de_baja":
            filas.append(h('<tr><td class="tenue">{}</td><td>{}</td><td class="r tenue">—</td><td class="r tenue">—</td>'
                           '<td class="r tenue">—</td><td class="tenue">dada de baja</td></tr>', _dia(x.fecha),
                           _tag(x.tipo)))
            continue
        previsto, real = x.previsto_kwh or Decimal(0), x.real_kwh or Decimal(0)
        if previsto > 0:
            desvio = (real - previsto) / previsto * 100
            texto, clase = f"{'+' if f.entero(desvio) > 0 else ''}{f.entero(desvio)} %".replace("-", f.MENOS), \
                ("aviso" if abs(desvio) >= 5 else "")
        else:
            texto, clase = ("0 %", "") if real == 0 else ("—", "")
        filas.append(h('<tr><td>{}</td><td>{}</td><td class="r">{}</td><td class="r">{}</td><td class="r {}">{}</td>'
                       '<td class="estado">{}</td></tr>', _dia(x.fecha), _tag(x.tipo), f.kwh(previsto), f.kwh(real),
                       clase, texto, ESTADOS[x.estado]))
    cuerpo = (h('<table><thead><tr><th>Fecha</th><th>Tipo</th><th class="r">Previsto</th><th class="r">Estimado real'
                '</th><th class="r">Desvío</th><th>Estado</th></tr></thead><tbody class="m">{}</tbody></table>',
                unir(filas)) if filas else Html('<p class="vacio">Ninguna excepción en los últimos 30 días.</p>'))
    return h('<section class="pn pasadas orden-4" id="pasadas"><div class="ph"><h2 class="pt">Pasadas · 30 días</h2>'
             '<span class="k m">consumo previsto frente a medido</span></div><div class="tabla-desplazable">{}</div>'
             '</section>', cuerpo)


def _error(form: FormExcepcion, campo: str) -> tuple[Html, Html]:
    if campo in form.errores:
        return (h(' aria-invalid="true" aria-describedby="error-x-{}"', campo),
                h('<p class="error-campo" id="error-x-{}">{}</p>', campo, form.errores[campo]))
    return Html(""), Html("")


def _formulario(d: Excepciones, form: FormExcepcion) -> Html:
    p = d.plan
    origen = "P75" if p.origen == "historia" else "inicial"
    consumo_pct = p.consumo_kwh / p.capacidad_kwh * 100
    objetivo = min(p.reserva_pct + consumo_pct, p.tope_pct)
    fe_a, fe_e = _error(form, "fecha")
    ho_a, ho_e = _error(form, "hora")
    co_a, co_e = _error(form, "consumo")
    km_a, _ = _error(form, "km")
    pc_a, _ = _error(form, "pct")
    ob_a, ob_e = _error(form, "pct_objetivo")
    no_a, no_e = _error(form, "nota")
    general = h('<p class="error-campo">{}</p>', form.errores["general"]) if "general" in form.errores else Html("")

    def marcado(condicion: bool) -> Html:
        return Html(" checked") if condicion else Html("")

    if form.modificar is not None:
        titulo, accion, boton = "Modificar excepción", f"/excepciones/{form.modificar}/modificar", "Guardar cambios"
        cancelar = Html('<a href="/excepciones" class="enlace-centrado">Cancelar</a>')
    else:
        titulo, accion, boton, cancelar = "Nueva excepción", "/excepciones", "Añadir excepción", Html("")
    return h(
        '<form class="pn formulario-excepcion orden-3" id="formulario" action="{}" method="post">'
        '<div class="ph"><h2 class="pt">{}</h2></div><div class="relleno">{}'
        '<fieldset class="grupo"><legend class="k">Tipo</legend><div class="seg seg-2">'
        '<label><input type="radio" name="tipo" value="con_salida"{}>Con salida</label>'
        '<label><input type="radio" name="tipo" value="sin_salida"{}>Sin salida</label></div></fieldset>'
        '<div class="dos"><div class="campo"><label for="x-fecha" class="k">Fecha</label><input id="x-fecha" '
        'name="fecha" class="inp" type="date" required value="{}"{}>{}</div><div class="campo solo-con-salida">'
        '<label for="x-hora" class="k">Hora de salida</label><input id="x-hora" name="hora" class="inp" type="time" '
        'value="{}"{}>{}</div></div>'
        '<fieldset class="grupo consumo solo-con-salida"><legend class="k">Consumo previsto</legend>'
        '<label class="opcion"><input type="radio" name="consumo" value="normal"{}><span>Normal ({})</span>'
        '<span class="m azul">{}</span></label>'
        '<label class="opcion"><input type="radio" name="consumo" value="indicado"{}{}><span>Indicar consumo</span>'
        '</label><div class="dos indicado"><div class="con-unidad-k"><input name="km" class="inp" type="number" '
        'inputmode="numeric" min="1" step="1" placeholder="km" aria-label="Kilómetros" value="{}"{}>'
        '<span class="k m">km</span></div><div class="con-unidad-k"><input name="pct" class="inp" type="number" '
        'inputmode="numeric" min="1" max="100" step="1" placeholder="%" aria-label="Porcentaje de batería" value="{}"{}>'
        '<span class="k m">%</span></div></div>{}</fieldset>'
        '<div class="campo solo-con-salida"><label for="x-min" class="k">Nivel mínimo a la salida (opcional)</label>'
        '<div class="con-unidad-k"><input id="x-min" name="pct_objetivo" class="inp corto" type="number" '
        'inputmode="numeric" min="1" max="100" step="1" placeholder="—" value="{}"{}><span class="k m">%</span></div>{}'
        '<p class="k texto">Si lo indicas, el objetivo es el mayor entre este nivel y reserva + consumo, sin pasar del '
        'tope.</p></div>'
        '<div class="m calculo solo-con-salida">consumo normal {} = <span class="valor">{}</span><br>objetivo = {} % + '
        '{} pts = <span class="azul">{}</span></div>'
        '<p class="k texto">Por defecto se usa el consumo normal (P75). Elige «Indicar consumo» para un viaje largo (km o '
        '%). «Sin salida» solo se admite en días que el horario marca con salida. «Modificar» carga aquí la excepción; '
        'al guardar, la anterior queda dada de baja.</p>'
        '<div class="campo"><label for="x-nota" class="k">Nota (opcional)</label><input id="x-nota" name="nota" '
        'class="inp" type="text" maxlength="80" value="{}"{}>{}</div>'
        '<button type="submit" class="btn">{}</button>{}</div></form>',
        accion, titulo, general, marcado(form.tipo != "sin_salida"), marcado(form.tipo == "sin_salida"),
        form.fecha, fe_a, fe_e, form.hora, ho_a, ho_e,
        marcado(form.consumo != "indicado"), origen, f.kwh(p.consumo_kwh), marcado(form.consumo == "indicado"), co_a,
        form.km, km_a, form.pct, pc_a, co_e, form.pct_objetivo, ob_a, ob_e,
        origen, f.kwh(p.consumo_kwh), f.entero(p.reserva_pct), f.entero(consumo_pct), f.pct(objetivo),
        form.nota, no_a, no_e, boton, cancelar)


def pantalla(d: Excepciones, form: FormExcepcion | None = None, avisos: list[Html] | None = None) -> Html:
    form = form or FormExcepcion()
    zona = h('<div class="banners orden-1">{}</div>', unir(avisos)) if avisos else Html("")
    return h('<div class="dos-columnas"><div class="col">{}{}{}<a href="#pasadas" class="k solo-movil orden-5 '
             'enlace-pasadas">Ver pasadas (30 días) →</a></div><div class="col lateral">{}</div></div>',
             zona, _proximas(d), _pasadas(d), _formulario(d, form))


def banner_ok(texto: str) -> Html:
    return banner("ok", texto)
