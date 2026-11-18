"""Parámetros (formulario-maquetas.md, 7.5; maqueta 05). Mismas reglas que el CLI; en la página, desde hoy."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from energia_monitor.formulario import formato as f
from energia_monitor.parametros import UNIDAD_HORA, hora_de_minutos, valor_de_texto
from energia_monitor.formulario.datos import Parametro, Parametros
from energia_monitor.formulario.html import Html, h, unir

ORIGENES = {"ficha_tecnica": "ficha técnica", "usuario": "usuario", "estimado": "estimado", "contrato": "contrato",
            "regulado": "regulado"}


def _num(x: Decimal, decimales: int | None = None) -> str:
    if decimales is not None:
        return f.numero(x, decimales)
    n = x.normalize()
    return f"{n:f}".replace(".", ",") if n != n.to_integral_value() else f"{int(n)}"


def _segundos(s: Decimal) -> str:
    if s == 0:
        return "0 min"
    if s % 3600 == 0:
        return f"{_num(s / 3600)} h"
    return f"{_num(s / 60)} min"


def valor(clave: str, unidad: str, v: Decimal) -> str:
    """Valor como se muestra (7.5): segundos en minutos u horas, sí/no, P75, hora del día..."""
    if unidad == UNIDAD_HORA:
        return hora_de_minutos(v)
    if unidad == "s":
        return _segundos(v)
    if unidad == "sí/no":
        return "sí" if v == 1 else "no"
    if unidad == "fracción":
        return f"{_num(v, 2)} (P{_num(v * 100)})"
    if unidad == "factor":
        return _num(v, 2)
    if unidad == "días":
        return f"{_num(v)} día{'s' if v != 1 else ''}"
    if unidad == "n":
        return _num(v)
    return f"{_num(v)} {unidad}"


def rango(p: Parametro) -> str:
    if p.unidad == "s":
        return f"{'0' if p.minimo == 0 else _segundos(p.minimo)}–{_segundos(p.maximo)}"
    if p.unidad == "sí/no":
        return "sí / no"
    if p.unidad == UNIDAD_HORA:
        return f"{hora_de_minutos(p.minimo)}–{hora_de_minutos(p.maximo)}"
    if p.unidad in ("factor", "fracción"):
        return f"{_num(p.minimo, 2)}–{_num(p.maximo, 2)}"
    return f"{_num(p.minimo)}–{_num(p.maximo)}"


def unidad_entrada(p: Parametro) -> str:
    """Unidad en la que se escribe el valor nuevo en la página."""
    return {"s": "min", "sí/no": "1 = sí, 0 = no", "fracción": "fracción", "factor": "factor", "n": "número",
            UNIDAD_HORA: "HH:MM"}.get(
        p.unidad, p.unidad)


def leer_valor(p: Parametro, texto: str) -> Decimal:
    """Valor escrito en la página, en la unidad guardada. ErrorParametro si no se entiende."""
    return a_unidad_guardada(p, valor_de_texto(texto, p.unidad))


def a_unidad_guardada(p: Parametro, v: Decimal) -> Decimal:
    return v * 60 if p.unidad == "s" else v


def de_unidad_guardada(p: Parametro, v: Decimal) -> Decimal:
    return v / 60 if p.unidad == "s" else v


@dataclass
class FormParametro:
    clave: str = "reserva_pct"
    valor: str = ""
    desde: str = ""
    nota: str = ""
    errores: dict[str, str] = field(default_factory=dict)


def _tabla(d: Parametros) -> Html:
    filas, items = [], []
    for p in d.vigentes:
        v = valor(p.clave, p.unidad, p.valor)
        origen = ORIGENES.get(p.origen, p.origen)
        desde = f"{p.desde:%d/%m/%Y}"
        # Las claves no editables (id_tarea_guardiana) se muestran sin enlace: solo se cambian por CLI.
        cambiar = (h('<a href="/parametros?clave={}#cambiar" class="lk">Cambiar</a>', p.clave) if p.editable
                   else h('<span class="k m">solo CLI</span>'))
        filas.append(h('<tr><td><p class="nombre">{}</p><p class="k m">{}</p></td><td class="r m">{}</td>'
                       '<td class="r m">{}</td><td><span class="tag horario">{}</span></td><td class="m">{}</td>'
                       '<td class="r">{}</td></tr>',
                       p.nombre, p.clave, v, rango(p), origen, desde, cambiar))
        contenido = h('<div><p class="nombre">{}</p><p class="k m">{}</p></div><div class="r"><p class="m">{}</p>'
                      '<p class="k m">{} · {}</p></div>', p.nombre, p.clave, v, origen, desde)
        items.append(h('<a class="it-parametro" href="/parametros?clave={}#cambiar">{}</a>', p.clave, contenido)
                     if p.editable else h('<div class="it-parametro">{}</div>', contenido))
    return h('<section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Parámetros vigentes</h2><span class="k m">'
             'cada cambio es un registro nuevo con fecha de inicio</span></div><div class="tabla-desplazable"><table>'
             '<thead><tr><th>Parámetro</th><th class="r">Valor</th><th class="r">Rango</th><th>Origen</th><th>Desde</th>'
             '<th class="r"><span class="oculto">Acciones</span></th></tr></thead><tbody>{}</tbody></table></div></section>'
             '<section class="pn solo-movil orden-2"><div class="ph"><h2 class="pt">Vigentes</h2></div>{}</section>',
             unir(filas), unir(items))


def _cambios(d: Parametros) -> Html:
    unidades = {p.clave: p.unidad for p in d.vigentes}
    filas = []
    for c in d.cambios:
        u = unidades.get(c.clave, "")
        anterior = valor(c.clave, u, c.anterior) if c.anterior is not None else "—"
        filas.append(h('<tr><td>{}</td><td>{}</td><td>{} → {}</td><td class="nota-celda">{}</td></tr>',
                       f"{c.desde:%d/%m/%Y}", c.clave, anterior, valor(c.clave, u, c.valor), c.nota))
    return h('<section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Últimos cambios</h2><span class="k m">'
             'fecha de inicio de cada valor</span></div><div class="tabla-desplazable"><table><tbody class="m">{}</tbody>'
             '</table></div></section>', unir(filas))


def _calibracion(d: Parametros) -> Html:
    c = d.calibracion
    if c.sugerido is None:
        cuerpo = Html('<p class="k texto">Aún no hay observaciones: hacen falta dos lecturas con solo una carga entre '
                      'ellas (sin trayectos) y al menos la energía mínima para calibrar.</p>')
    else:
        sugerido = c.sugerido.quantize(Decimal("0.01"))
        rango_txt = f" · rango {f.numero(c.minimo, 2)}–{f.numero(c.maximo, 2)}" if c.minimo is not None else ""
        cuerpo = h('<p class="m calibracion">observado <span class="azul">{}</span> · vigente {}<br><span class="k">'
                   'n = {} carga{}{}</span></p><p class="k texto">No se aplica solo. Al registrarlo entra en vigor desde '
                   'hoy y se recalcula la estimación.</p><form action="/parametros" method="post"><input type="hidden" '
                   'name="clave" value="factor_bateria"><input type="hidden" name="valor" value="{}"><input type="hidden" '
                   'name="desde" value="{}"><input type="hidden" name="nota" value="calibración sugerida ({} observación{})">'
                   '<button type="submit" class="btn2">Registrar {} como valor nuevo</button></form>',
                   f.numero(sugerido, 2), f.numero(c.vigente, 2), c.observaciones, "" if c.observaciones == 1 else "s",
                   rango_txt, f"{sugerido}", f"{f.local(d.ahora):%Y-%m-%d}", c.observaciones,
                   "" if c.observaciones == 1 else "es", f.numero(sugerido, 2))
    return h('<section class="pn orden-3"><div class="ph"><h2 class="pt">Calibración sugerida</h2><span class="k m">'
             'factor_bateria</span></div><div class="relleno">{}</div></section>', cuerpo)


def _formulario(d: Parametros, form: FormParametro) -> Html:
    editables = [p for p in d.vigentes if p.editable]
    elegido = next((p for p in editables if p.clave == form.clave), editables[0])
    opciones = unir(h('<option value="{}"{}>{} · {}</option>', p.clave,
                      Html(" selected") if p.clave == elegido.clave else Html(""), p.clave, p.nombre) for p in editables)

    def error(campo: str) -> tuple[Html, Html]:
        if campo in form.errores:
            return (h(' aria-invalid="true" aria-describedby="error-p-{}"', campo),
                    h('<p class="error-campo" id="error-p-{}">{}</p>', campo, form.errores[campo]))
        return Html(""), Html("")

    va_a, va_e = error("valor")
    de_a, de_e = error("desde")
    cl_a, cl_e = error("clave")
    hora = elegido.unidad == UNIDAD_HORA
    valor_actual = form.valor or (hora_de_minutos(elegido.valor) if hora
                                  else _num(de_unidad_guardada(elegido, elegido.valor)))
    tipo = (h('type="time" step="60" min="{}" max="{}"', hora_de_minutos(elegido.minimo),
              hora_de_minutos(elegido.maximo)) if hora else Html('type="text" inputmode="decimal"'))
    hoy = f"{f.local(d.ahora):%Y-%m-%d}"
    return h(
        '<form class="pn orden-1" id="cambiar" action="/parametros" method="post"><div class="ph"><h2 class="pt">Cambiar '
        'un valor</h2></div><div class="relleno"><div class="campo"><label for="p-clave" class="k">Parámetro</label>'
        '<select id="p-clave" name="clave" class="inp"{}>{}</select>{}</div><div class="dos"><div class="campo">'
        '<label for="p-valor" class="k">Valor nuevo ({})</label><input id="p-valor" name="valor" class="inp" {} '
        'required value="{}"{}>{}</div><div class="campo"><label for="p-desde" class="k">Desde</label>'
        '<input id="p-desde" name="desde" class="inp" type="date" required min="{}" value="{}"{}>{}</div></div>'
        '<p class="k m">rango admitido {} · fuera de rango → no se guarda</p><p class="k texto">{} Si eliges otro '
        'parámetro en la lista, el valor va en su unidad (minutos para los que se guardan en segundos; HH:MM para las '
        'horas).</p>'
        '<div class="campo"><label for="p-nota" class="k">Nota</label><input id="p-nota" name="nota" class="inp" '
        'type="text" maxlength="80" placeholder="Motivo del cambio" value="{}"></div><button type="submit" class="btn">'
        'Registrar valor</button></div></form>',
        cl_a, opciones, cl_e, unidad_entrada(elegido), tipo, valor_actual, va_a, va_e, hoy, form.desde or hoy, de_a, de_e,
        rango(elegido), elegido.descripcion, form.nota)


def pantalla(d: Parametros, form: FormParametro, avisos: list[Html]) -> Html:
    zona = h('<div class="banners orden-0">{}</div>', unir(avisos)) if avisos else Html("")
    return h('{}<div class="dos-columnas"><div class="col">{}{}</div><div class="col lateral">{}{}</div></div>',
             zona, _tabla(d), _cambios(d), _calibracion(d), _formulario(d, form))
