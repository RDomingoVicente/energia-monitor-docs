"""Parámetros (formulario-maquetas.md, 7.5; maqueta 05, rehecha por grupos en la etapa 3). Mismas reglas que el
CLI; en la página, desde hoy. Grupos, orden, nombres, rangos y notas salen de la base de datos (E3.9), sin listas
fijas en el código."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from energia_monitor.formulario import formato as f
from energia_monitor.parametros import UNIDAD_HORA, hora_de_minutos, valor_de_texto
from energia_monitor.formulario.datos import Grupo, Parametro, Parametros
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
    if p.unidad == "min":
        return f"{'0' if p.minimo == 0 else _segundos(p.minimo * 60)}–{_segundos(p.maximo * 60)}"
    if p.unidad == "h":
        return f"{_num(p.minimo)}–{_num(p.maximo)} h"
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


ANCLAS = {1: "g-coche", 2: "g-nivel", 3: "g-consumo", 4: "g-estrategia", 5: "g-disponibilidad", 6: "g-cargador",
          7: "g-avisos"}


def _ancla(g: Grupo) -> str:
    return ANCLAS.get(g.grupo, f"g-{g.grupo}")


def _nota(d: Parametros, p: Parametro) -> str | None:
    return d.notas.get(p.clave, p.nota)


def _cambiar(p: Parametro) -> Html:
    # Las claves no editables (id_tarea_guardiana) se muestran sin enlace: solo se cambian por CLI.
    return (h('<a href="/parametros?clave={}#cambiar" class="lk">Cambiar</a>', p.clave) if p.editable
            else Html('<span class="k m">solo CLI</span>'))


def _calibracion(d: Parametros, movil: bool) -> Html:
    """«Calibración sugerida», dentro del grupo 1 bajo la eficiencia de carga. No se aplica sola."""
    c = d.calibracion
    if c.sugerido is None:
        texto = Html('<p class="k texto">Aún no hay observaciones: hacen falta dos lecturas con solo una carga entre '
                     'ellas (sin trayectos) y al menos la energía mínima para calibrar.</p>')
        boton = Html("")
    else:
        sugerido = c.sugerido.quantize(Decimal("0.01"))
        rango_txt = f" · rango {f.numero(c.minimo, 2)}–{f.numero(c.maximo, 2)}" if c.minimo is not None else ""
        texto = h('<p class="m calibracion">observado <span class="cifra">{}</span> · vigente {}{}<span class="k">n = {} '
                  'carga{}{}</span></p><p class="k texto">No se aplica solo: al registrarlo entra en vigor desde hoy y se '
                  'recalcula la estimación.</p>',
                  f.numero(sugerido, 2), f.numero(c.vigente, 2), Html("<br>") if movil else Html(" · "),
                  c.observaciones, "" if c.observaciones == 1 else "s", rango_txt)
        boton = h('<form action="/parametros" method="post"><input type="hidden" name="clave" value="factor_bateria">'
                  '<input type="hidden" name="valor" value="{}"><input type="hidden" name="desde" value="{}">'
                  '<input type="hidden" name="nota" value="calibración sugerida ({} observación{})"><button type="submit" '
                  'class="{}">Registrar {}{}</button></form>',
                  f"{sugerido}", f"{f.local(d.ahora):%Y-%m-%d}", c.observaciones,
                  "" if c.observaciones == 1 else "es", "btn2 azul" if movil else "lk", f.numero(sugerido, 2),
                  " como valor nuevo" if movil else "")
    return h('<div class="calibracion-caja"><div class="calibracion-texto"><p class="pt azul">Calibración sugerida</p>{}'
             '</div>{}</div>', texto, boton)


def _fila(d: Parametros, p: Parametro) -> Html:
    nota = _nota(d, p)
    return h('<tr><td><p class="nombre">{}</p><p class="k m clave">{}</p>{}</td><td class="r m valor">{}</td>'
             '<td class="r m tenue">{}</td><td><span class="tag horario">{}</span></td><td class="m tenue" title="{}">{}'
             '</td><td class="r">{}</td></tr>',
             p.nombre, p.clave, h('<p class="nota-param">{}</p>', nota) if nota else Html(""),
             valor(p.clave, p.unidad, p.valor), rango(p), ORIGENES.get(p.origen, p.origen), f"{p.desde:%d/%m/%Y}",
             f"{p.desde:%d/%m}", _cambiar(p))


def _grupo_escritorio(d: Parametros, g: Grupo) -> Html:
    filas: list[Html] = []
    for p in (x for x in d.vigentes if x.grupo == g.grupo):
        filas.append(_fila(d, p))
        if p.clave == "factor_bateria":
            filas.append(h('<tr class="fila-calibracion"><td colspan="6">{}</td></tr>', _calibracion(d, movil=False)))
    tabla = h('<table class="fija parametros-grupo"><colgroup><col><col class="c-valor"><col class="c-rango">'
              '<col class="c-origen"><col class="c-desde"><col class="c-accion"></colgroup><thead><tr><th>Parámetro</th>'
              '<th class="r">Valor</th><th class="r">Rango</th><th>Origen</th><th>Desde</th><th class="r"><span '
              'class="oculto">Acciones</span></th></tr></thead><tbody>{}</tbody></table>', unir(filas))
    titulo = f"{g.grupo} · {g.nombre}"
    n = sum(1 for x in d.vigentes if x.grupo == g.grupo)
    if g.avanzado:
        # Grupo avanzado: plegado también en escritorio.
        return h('<details id="{}" class="pn grupo-param"><summary class="ph"><span class="titulo-grupo"><span class="pt">'
                 '{}</span><span class="tag estimado">avanzado</span></span><span class="k">{} · {} parámetros · '
                 'desplegar ▾</span></summary><p class="k texto relleno-x">Valores técnicos que casi nunca hay que tocar.'
                 '</p>{}</details>', _ancla(g), titulo, g.pregunta, n, tabla)
    return h('<section id="{}" class="pn grupo-param"><div class="ph"><h2 class="pt">{}</h2><span class="k">{}</span>'
             '</div>{}</section>', _ancla(g), titulo, g.pregunta, tabla)


def _grupo_movil(d: Parametros, g: Grupo) -> Html:
    items: list[Html] = []
    for p in (x for x in d.vigentes if x.grupo == g.grupo):
        nota = _nota(d, p)
        contenido = h('<div class="izq"><p class="nombre">{}</p><p class="k m clave">{}</p>{}</div><div class="r">'
                      '<p class="m valor">{}</p><p class="k m">{} · {}</p></div>', p.nombre, p.clave,
                      h('<p class="nota-param">{}</p>', nota) if nota else Html(""), valor(p.clave, p.unidad, p.valor),
                      ORIGENES.get(p.origen, p.origen), f"{p.desde:%d/%m}")
        items.append(h('<a class="it-parametro" href="/parametros?clave={}#cambiar">{}</a>', p.clave, contenido)
                     if p.editable else h('<div class="it-parametro">{}<span class="k m solo-cli">solo CLI</span></div>',
                                          contenido))
        if p.clave == "factor_bateria":
            items.append(_calibracion(d, movil=True))
    n = len([x for x in d.vigentes if x.grupo == g.grupo])
    avanzado = Html('<span class="tag estimado">avanzado</span>') if g.avanzado else Html("")
    return h('<details class="pn grupo-param"{}><summary class="ph"><span class="titulo-grupo-movil"><span '
             'class="titulo-grupo"><span class="pt">{} · {}</span>{}</span><span class="k pregunta">{}</span></span>'
             '<span class="k m">{}</span></summary><div class="lista-param">{}</div></details>',
             Html(" open") if g.abierto_movil else Html(""), g.grupo, g.nombre, avanzado, g.pregunta, n, unir(items))


def _indice(d: Parametros) -> Html:
    enlaces = []
    for g in d.grupos:
        n = sum(1 for x in d.vigentes if x.grupo == g.grupo)
        enlaces.append(h('<a href="#{}"><span>{} · {}</span><span class="k m">{}{}</span></a>', _ancla(g), g.grupo,
                         g.nombre, n, " · avanzado" if g.avanzado else ""))
    return h('<nav class="pn indice-grupos solo-escritorio" aria-label="Grupos de parámetros"><div class="ph">'
             '<h2 class="pt">Grupos</h2><span class="k m">{} parámetros</span></div>{}</nav>',
             len(d.vigentes), unir(enlaces))


def _cambios(d: Parametros) -> Html:
    unidades = {p.clave: p.unidad for p in d.vigentes}
    filas = []
    for c in d.cambios:
        if c.n > 1:
            filas.append(h('<div class="it"><span>{}</span><span>{} parámetros nuevos</span><span class="nota-celda">{}'
                           '</span></div>', f"{c.desde:%d/%m/%Y}", c.n, c.nota))
            continue
        u = unidades.get(c.clave, "")
        anterior = valor(c.clave, u, c.anterior) if c.anterior is not None else "—"
        filas.append(h('<div class="it"><span>{}</span><span>{} {} → {}</span><span class="nota-celda">{}</span></div>',
                       f"{c.desde:%d/%m/%Y}", c.clave, anterior, valor(c.clave, u, c.valor), c.nota))
    return h('<section class="pn cambios-param orden-3"><div class="ph"><h2 class="pt">Últimos cambios</h2></div>'
             '<div class="lista">{}</div></section>', unir(filas))


def _formulario(d: Parametros, form: FormParametro) -> Html:
    editables = [p for p in d.vigentes if p.editable]
    elegido = next((p for p in editables if p.clave == form.clave), editables[0])
    nombres = {g.grupo: f"{g.grupo} · {g.nombre}" for g in d.grupos}
    grupos_sel: list[Html] = []
    for g in sorted({p.grupo for p in editables}):
        opciones_g = unir(h('<option value="{}"{}>{} · {}</option>', p.clave,
                            Html(" selected") if p.clave == elegido.clave else Html(""), p.clave, p.nombre)
                          for p in editables if p.grupo == g)
        grupos_sel.append(h('<optgroup label="{}">{}</optgroup>', nombres.get(g, str(g)), opciones_g))
    opciones = unir(grupos_sel)

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
        '<form class="pn orden-2" id="cambiar" action="/parametros" method="post"><div class="ph"><h2 class="pt">Cambiar '
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
    escritorio = h('<div class="solo-escritorio grupos-escritorio"><p class="k texto">Cada cambio es un registro nuevo '
                   'con fecha de inicio. El modo del planificador (sombra, activo, pausa) se cambia desde Plan de '
                   'carga.</p>{}</div>', unir(_grupo_escritorio(d, g) for g in d.grupos))
    movil = h('<div class="solo-movil grupos-movil orden-1">{}</div>', unir(_grupo_movil(d, g) for g in d.grupos))
    return h('{}<div class="columnas-parametros"><div class="col">{}{}</div><div class="col lateral">{}{}{}</div></div>',
             zona, escritorio, movil, _indice(d), _formulario(d, form), _cambios(d))
