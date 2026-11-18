"""Plan de carga (formulario-maquetas.md, 7.9; maqueta 01d).

Desde el paso 3c las acciones (modo, Cargar ahora, Detener, Modificar, Eliminar, Añadir franja) se encolan en
plan_accion: las ejecuta el muestreo al momento y la página muestra su resultado tras el PRG. En sombra las
franjas no se escriben: estados de sombra de 7.9.
La línea de tiempo se pinta con HTML y clases (la CSP no admite estilos en línea): q-N y n-N son la posición y
el ancho en cuartos de hora dentro de la vista (lt-240: 60 h; lt-96: 24 h), h-N y b-N alturas en % de la escala
0–0,60 €/kWh.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.html import Html, h, unir
from energia_monitor.formulario.plantilla import banner
from energia_monitor.formulario.plan_datos import ESCALA_EUR_KWH, Ahorro, FranjaPlan, Linea, PlanCarga
from energia_monitor.tiempo import MADRID

from dataclasses import dataclass, field

from energia_monitor.formulario.plan_datos import Accion


@dataclass
class FormFranja:
    """Añadir o modificar una franja manual (hora de Madrid). anterior_*: la franja que se modifica."""

    dia: str = ""
    inicio: str = ""
    fin: str = ""
    anterior_inicio: str = ""
    anterior_fin: str = ""
    errores: dict[str, str] = field(default_factory=dict)


@dataclass
class FormModo:
    errores: dict[str, str] = field(default_factory=dict)


EN_SOMBRA = "Cambia a activo para usarlo."
NOTA_SOMBRA = h('<p class="k paso">{}</p>', EN_SOMBRA)

TIPO_ACCION = {"modo": "Cambio de modo", "cargar_ahora": "Cargar ahora", "detener": "Detener",
               "modificar": "Modificar franja", "eliminar": "Eliminar franja", "anadir": "Franja manual"}
MODOS = (
    ("sombra", "Calcula el plan y lo muestra, pero no escribe en el cargador"),
    ("activo", "Escribe sus franjas en el cargador y las mantiene al día"),
    ("pausa", "Desactiva sus franjas futuras y deja de escribir; la franja en curso y la guardiana siguen"),
)
FRASE_MODO = {
    "sombra": ("Sombra:", "calcula el plan cada 15 min sin escribir en el cargador. En activo escribe sus franjas y las "
                          "revisa cada 15 min; en pausa no escribe y desactiva sus franjas futuras."),
    "activo": ("Activo:", "escribe sus franjas en el cargador y las revisa cada 15 min. En sombra solo calcula; en pausa "
                          "no escribe y desactiva sus franjas futuras."),
    "pausa": ("Pausa:", "no escribe y desactiva sus franjas futuras; la franja en curso y la guardiana siguen. En sombra "
                        "solo calcula; en activo escribe y revisa cada 15 min."),
}
TAG_NIVEL = {"necesaria": "info", "margen": "ok", "oportunista": "ok", "en_curso": "info"}


def _eur(x: Decimal | None, provisional: bool = False) -> str:
    if x is None:
        return "—"
    return f"{'≈' if provisional else ''}{f.numero(x, 2)} €"


def _precio(x: Decimal | None) -> str:
    return "—" if x is None else f"{f.numero(x, 3)} €/kWh"


def _horario(inicio: datetime, fin: datetime) -> str:
    """Una franja que cruza la medianoche se muestra como una sola («23:00–02:30»)."""
    return f"{f.hora(inicio)}–{f.hora(fin)}"


def _duracion(inicio: datetime, fin: datetime) -> str:
    minutos = int((fin - inicio).total_seconds() // 60)
    horas, resto = divmod(minutos, 60)
    if horas and resto:
        return f"{horas} h {resto} min"
    return f"{horas} h" if horas else f"{resto} min"


def _pts(d: PlanCarga, kwh: Decimal) -> str:
    if not d.capacidad_kwh:
        return ""
    return f"+{f.entero(kwh * d.factor_bateria / d.capacidad_kwh * 100)} pts"


def _desglose(fr: FranjaPlan, corto: bool = False) -> Html:
    """Una línea por motivo: «necesaria 7,4 · lun 09:30», «oportunista 23,4»; y «provisional»."""
    lineas = []
    for nivel, salida, kwh in fr.desglose:
        cuando = h(' <span class="k m">· {}</span>', f"{f.DIAS[f.local(salida).weekday()]} {f.hora(salida)}") \
            if salida and not corto else Html("")
        lineas.append(h('<span class="motivo"><span class="tag {}">{}</span> <span class="m">{}</span>{}</span>',
                        TAG_NIVEL.get(nivel, "horario"), nivel, f.numero(kwh), cuando))
    if fr.provisional:
        lineas.append(Html('<span class="motivo"><span class="tag horario">provisional</span></span>'))
    if fr.manual:
        lineas.append(Html('<span class="motivo"><span class="tag estimado">fijada</span></span>'))
    return unir(lineas)


# --- Panel de control ---------------------------------------------------------------------------


def _control(d: PlanCarga, fm: FormModo) -> Html:
    if d.calculado is None:
        estado = Html('<p class="titulo-plan">Aún no hay plan</p><p class="sub">El planificador calcula en el servicio de '
                      'muestreo cada 15 minutos y con cada cambio; vuelve en unos minutos.</p>')
    else:
        lineas: list[Html] = []
        if d.salidas:
            s = d.salidas[0]
            lineas.append(h('<p class="titulo-plan">Próxima salida <span class="m">{}</span><span class="tag {}">{}</span>'
                            '</p>', f.dia_hora(s.instante), "leido" if s.cubierta else "estimado",
                            "cubierta" if s.cubierta else "no cubierta"))
            objetivo = f.pct(s.objetivo_kwh / d.capacidad_kwh * 100) if d.capacidad_kwh else "—"
            curso = next((x for x in d.franjas if x.inicio <= d.ahora < x.fin), None)
            if d.nivel_tras_curso_kwh is not None and curso is not None:
                tras = f"al terminar la franja en curso: {f.pct(d.nivel_tras_curso_kwh / d.capacidad_kwh * 100)}"
            elif d.nivel_kwh is not None and d.capacidad_kwh:
                tras = f"nivel ahora: {f.pct(d.nivel_kwh / d.capacidad_kwh * 100)}"
            else:
                tras = "sin nivel"
            lineas.append(h('<p class="sub m">objetivo {} · {}</p>', objetivo, tras))
        else:
            lineas.append(Html('<p class="titulo-plan">Sin salidas en el horizonte</p>'))
        total = d.coste_total
        provisional = d.coste_provisional
        n = len(d.franjas)
        coste = _eur(total) + (f" ({_eur(provisional, True)} provisional)" if provisional else "")
        lineas.append(h('<p class="sub m">plan hasta {} · {} franja{} · {}</p>',
                        f.dia_hora(d.horizonte_fin) if d.horizonte_fin else "—", n, "" if n == 1 else "s", coste))
        if d.umbral is not None and d.umbral_pct:
            media = d.umbral / d.umbral_pct * 100
            lineas.append(h('<p class="sub m">umbral barato {} = {} % × {} (media {} días)</p>', _precio(d.umbral),
                            f.entero(d.umbral_pct), f.numero(media, 3), d.ventana_umbral_dias))
        if d.prudente:
            lineas.append(Html('<p class="sub m aviso">modo prudente: solo con el coche enchufado</p>'))
        lineas.append(h('<p class="k m">último cálculo {}</p>', f.cuando(d.calculado, d.ahora)))
        estado = unir(lineas)
    opciones = unir(h('<label title="{}"><input type="radio" name="modo" value="{}"{}>{}</label>', titulo, modo,
                      Html(" checked") if modo == d.modo else Html(""), modo) for modo, titulo in MODOS)
    nombre, frase = FRASE_MODO.get(d.modo, ("", ""))
    error_modo = h('<p class="error-campo">{}</p>', fm.errores["confirmar"]) if "confirmar" in fm.errores else Html("")
    return h(
        '<section class="pn control-plan orden-1"><p class="pt solo-movil cabecera-control">Planificador <span class="tag '
        'estimado">{}</span></p><div class="estado-plan">{}</div>'
        '<form class="modo-plan" action="/plan/modo" method="post"><span class="k">Modo</span><div class="seg" '
        'role="radiogroup" aria-label="Modo del planificador">{}</div><p class="k texto"><span class="destacado">{}</span> '
        '{}</p><label class="k confirmar"><input type="checkbox" name="confirmar" value="si"> Para pasar a activo: '
        'entiendo que escribirá en el cargador</label>{}<button type="submit" class="btn2">Aplicar</button></form>'
        '<form class="ahora-plan" action="/plan/cargar-ahora" method="post"><span class="k">Ignora el precio</span>'
        '<button type="submit" class="btn2"{}>Cargar ahora</button><p class="k texto">Hasta el objetivo de la próxima '
        'salida.</p>{}</form></section>',
        d.modo.upper(), estado, opciones, nombre, frase, error_modo, _desactivado(d), NOTA_SOMBRA if d.sombra else Html(""))


def _desactivado(d: PlanCarga) -> Html:
    """En sombra las acciones sobre franjas no se pueden usar: no se escribe nada en el cargador."""
    return h(' disabled title="{}"', EN_SOMBRA) if d.sombra else Html("")


# --- Línea de tiempo -----------------------------------------------------------------------------


def _altura(precio: Decimal) -> int:
    return max(1, min(100, int(precio / ESCALA_EUR_KWH * 100)))


def _linea(d: PlanCarga, ln: Linea, movil: bool) -> Html:
    n = ln.cuartos
    bloques: list[Html] = []
    # Etiqueta horaria (~80 px) solo si no pisa la anterior: en escritorio un cuarto mide ~3,7 px; en móvil, ~6.
    separacion, ultima = (14 if movil else 24), -100
    for fr in ln.franjas:
        i, j = ln.indice(fr.inicio), ln.indice(fr.fin)
        if j <= i:
            continue
        if fr.inicio <= ln.ahora < fr.fin:
            hecho = int((ln.ahora - fr.inicio) / (fr.fin - fr.inicio) * 100)
            clase, texto = "curso", "cargando" if "cargando" in fr.ejecucion else "en curso"
            relleno = h('<span class="hecho w-{}"></span>', hecho)
        elif fr.provisional:
            clase, texto, relleno = "provisional", "prov.", Html("")
        else:
            clase, texto, relleno = "programada", "", Html("")
        centro = (i + j) // 2
        etiqueta = Html("")
        if centro - ultima >= separacion:
            etiqueta, ultima = h('<span class="lt-hor m q-{}">{}</span>', centro, _horario(fr.inicio, fr.fin)), centro
        bloques.append(h('<div class="lt-bloque {} q-{} n-{}" title="{}">{}<span class="m">{}</span></div>{}', clase, i,
                         j - i, _horario(fr.inicio, fr.fin), relleno, texto, etiqueta))
    barras: list[Html] = []
    for k, hr in enumerate(ln.horas):
        if hr.precio is None:
            continue
        if hr.provisional:
            clase = "prov"
        elif ln.umbral is not None and hr.precio < ln.umbral:
            clase = "bajo"
        else:
            clase = "sobre"
        barras.append(h('<div class="lt-barra {} q-{} n-4 h-{}" title="{}{}"></div>', clase, 4 * k,
                        _altura(hr.precio), "≈" if hr.provisional else "", _precio(hr.precio)))
    umbral = (h('<div class="lt-umbral b-{}"><span class="m">umbral {}</span></div>', _altura(ln.umbral),
                f.numero(ln.umbral, 3)) if ln.umbral is not None else Html(""))
    casa = unir(h('<div class="lt-casa {} q-{} n-{}"></div>', tipo, ln.indice(a), max(1, ln.indice(b) - ln.indice(a)))
                for a, b, tipo in ln.coche if ln.indice(b) > ln.indice(a))
    marcas = [h('<div class="lt-ahora q-{}"></div>', ln.indice(ln.ahora))]
    marcas += [h('<div class="lt-salida q-{}"></div>', ln.indice(s)) for s in ln.salidas]
    etiquetas_salida = unir(h('<span class="q-{}">{}{}</span>', ln.indice(s), "salida " if k == 0 else "",
                              f"{f.DIAS[f.local(s).weekday()]} {f.hora(s)}") for k, s in enumerate(ln.salidas))
    eje = []
    t = ln.desde
    while t <= ln.hasta:
        local = t.astimezone(MADRID)
        if local.minute == 0 and local.hour % (3 if movil else 6) == 0:
            texto = f"{f.DIAS[local.weekday()]} 00" if local.hour == 0 else f"{local.hour:02d}"
            i = ln.indice(t)
            clases = (" dia" if local.hour == 0 else "") + (" ultimo" if i == n else "")
            eje.append(h('<span class="m q-{}{}">{}</span>', i, clases, texto))
        t += timedelta(hours=1)
    guias = unir(Html(f'<div class="lt-guia g-{k}"></div>') for k in range(1, 6))
    etiquetas_precio = unir(h('<span class="m lt-eje-p g-{}">{}</span>', k, f.numero(Decimal(k) / 10, 2))
                            for k in range(1, 6))
    return h(
        '<div class="lt lt-{}{}"><div class="lt-fila lt-cargas"><span class="lt-etq k">Cargas programadas</span>'
        '<div class="lt-pista">{}</div></div>'
        '<div class="lt-fila lt-precio"><span class="lt-etq k">Precio<br>€/kWh</span>{}<div class="lt-pista">{}{}{}</div>'
        '</div><div class="lt-fila lt-coche"><span class="lt-etq k">Coche en casa</span><div class="lt-pista">{}</div>'
        '</div><div class="lt-fila lt-salidas"><div class="lt-pista">{}</div></div><div class="lt-marcas">{}</div>'
        '<div class="lt-fila lt-eje"><div class="lt-pista m">{}</div></div></div>',
        n, " movil" if movil else "", unir(bloques), etiquetas_precio, guias, unir(barras), umbral, casa,
        etiquetas_salida, unir(marcas), unir(eje))


def _leyenda(d: PlanCarga) -> Html:
    return h('<div class="lt-leyenda k"><span><i class="ley bajo"></i>bajo el umbral</span><span><i class="ley sobre">'
             '</i>sobre el umbral</span><span><i class="ley prov"></i>precio provisional</span><span><i class="ley '
             'enchufado"></i>enchufado</span><span><i class="ley probable"></i>probable (≥ {} % de los días)</span>'
             '<span><i class="ley ahora"></i>ahora {}</span></div>', f.entero(d.umbral_disponibilidad_pct),
             f.hora(d.ahora))


def _seccion_linea(d: PlanCarga) -> Html:
    if d.linea is None or d.linea_movil is None:
        return Html("")
    despues = Html("")
    if d.linea.despues:
        n = len(d.linea.despues)
        despues = h('<a href="#franjas" class="m despues">+{} franja{} después: {} ▸</a>', n, "" if n == 1 else "s",
                    f.dia_hora(d.linea.despues[0].inicio))
    return h('<section class="pn solo-escritorio" id="linea"><div class="ph"><h2 class="pt">Próximas 60 h · precio y '
             'franjas</h2>{}</div><div class="relleno-lt">{}{}</div></section>'
             '<section class="pn solo-movil orden-2"><div class="ph"><h2 class="pt">Próximas 24 h</h2><span class="k m">'
             'desliza →</span></div>'
             '<div class="lt-desplazable">{}</div>{}</section>',
             despues, _linea(d, d.linea, False), _leyenda(d), _linea(d, d.linea_movil, True), _leyenda(d))


# --- Franjas ---------------------------------------------------------------------------------------


def _clase_estado(estado: str) -> str:
    if estado.startswith("error"):
        return "nivel-error"
    return "leido" if estado == "confirmada" else "horario"


def _nota_ejecucion(fr: FranjaPlan) -> str:
    if fr.progreso_kwh is not None:
        return f"{f.numero(fr.progreso_kwh)} de {f.numero(fr.kwh)} kWh en su horario"
    return ""


def _acciones(d: PlanCarga, fr: FranjaPlan) -> Html:
    """En curso: solo Detener. Futuras: Modificar (formulario de franja manual) y Eliminar. En sombra, desactivadas."""
    inicio, fin = fr.inicio.isoformat(), fr.fin.isoformat()
    if d.sombra:
        if fr.ejecucion != "programada":
            return h('<button type="button" class="del"{}>Detener</button>', _desactivado(d))
        return h('<button type="button" class="mod"{}>Modificar</button><button type="button" class="del"{}>Eliminar'
                 '</button>', _desactivado(d), _desactivado(d))
    if fr.ejecucion != "programada":
        return h('<form action="/plan/franjas/detener" method="post"><input type="hidden" name="inicio" value="{}">'
                 '<button type="submit" class="del">Detener</button></form>', inicio)
    return h('<a href="/plan?modificar={}#manual" class="mod">Modificar</a><form action="/plan/franjas/eliminar" '
             'method="post"><input type="hidden" name="inicio" value="{}"><input type="hidden" name="fin" value="{}">'
             '<button type="submit" class="del">Eliminar</button></form>', inicio, inicio, fin)


def _franjas(d: PlanCarga) -> Html:
    filas, tarjetas = [], []
    for fr in d.franjas:
        energia = h('<span class="m fuerte">{} kWh</span><span class="m azul">{}</span><span class="m">{}</span>'
                    '<span class="k m">{}</span>', f.numero(fr.kwh), _pts(d, fr.kwh), _eur(fr.coste, fr.provisional),
                    _precio(fr.precio_medio))
        ejecucion = h('<span class="tag {}">{}</span><span class="tag {}">{}</span>{}', _clase_estado(fr.estado_cargador),
                      fr.estado_cargador,
                      "leido" if "cargando" in fr.ejecucion else "horario", fr.ejecucion,
                      h('<span class="k m nota-ejecucion">{}</span>', _nota_ejecucion(fr))
                      if _nota_ejecucion(fr) else Html(""))
        tareas = h(' <span class="k m">· {} tareas</span>', fr.tareas) if fr.tareas > 1 else Html("")
        filas.append(h('<tr><td><span class="m">{}</span><br><span class="m">{}</span><br><span class="k m">{}{}</span>'
                       '</td><td class="motivos">{}</td><td class="r energia">{}</td><td class="ejecucion">{}</td>'
                       '<td class="r acciones-col">{}</td></tr>', f.dia(fr.inicio), _horario(fr.inicio, fr.fin),
                       _duracion(fr.inicio, fr.fin), tareas, _desglose(fr), energia, ejecucion, _acciones(d, fr)))
        tarjetas.append(h('<div class="it-franja"><div class="fila"><span class="m">{} · {}</span><span class="m '
                          'fuerte">{} kWh</span></div><div class="motivos">{}</div><div class="fila"><span class="k m">'
                          '{} · {} · {}</span><span class="tag {}">{}</span></div><div class="fila"><span class="tag {}">'
                          '{}</span></div><div class="acciones-fila">{}</div></div>', f.dia(fr.inicio),
                          _horario(fr.inicio, fr.fin), f.numero(fr.kwh), _desglose(fr), _pts(d, fr.kwh),
                          _eur(fr.coste, fr.provisional), _precio(fr.precio_medio),
                          "leido" if "cargando" in fr.ejecucion else "horario", fr.ejecucion,
                          _clase_estado(fr.estado_cargador), fr.estado_cargador, _acciones(d, fr)))
    cuerpo = (h('<table class="fija franjas-plan"><colgroup><col class="c-franja"><col><col class="c-energia">'
                '<col class="c-ejecucion"><col class="c-acciones"></colgroup><thead><tr><th>Franja</th><th>Motivo · kWh'
                '</th><th class="r">Energía · coste</th><th>Cargador · ejecución</th><th class="r"><span class="oculto">Acciones'
                '</span></th></tr></thead><tbody>{}</tbody></table>', unir(filas))
              if filas else Html('<p class="vacio">Sin cargas programadas.</p>'))
    pie = h('<p class="k texto pie-tabla">La franja en curso solo se puede detener; luego, eliminar. Una franja que '
               'cruza la medianoche se muestra como una sola y se escribe como 2 tareas. Al detener o eliminar una '
               'franja automática, el planificador no la vuelve a crear en ese tramo.{}</p>', h(' En sombra: {}', EN_SOMBRA)
               if d.sombra else Html(""))
    sombra = Html('<span class="tag estimado">SOMBRA</span>') if d.sombra else Html("")
    tareas = sum(x.tareas for x in d.franjas)
    resumen = h('<span class="cabecera-proximas">{}<span class="k m">{} franja{} · {} tarea{} de {}</span></span>', sombra,
                len(d.franjas), "" if len(d.franjas) == 1 else "s", tareas, "" if tareas == 1 else "s", d.max_tareas)
    return h('<section class="pn solo-escritorio" id="franjas"><div class="ph"><h2 class="pt">Franjas programadas</h2>{}'
             '</div>{}{}</section><section class="pn solo-movil orden-3"><div class="ph"><h2 class="pt">Franjas '
             'programadas</h2>{}</div>{}</section>', resumen, cuerpo, pie, sombra,
             unir(tarjetas) if tarjetas else Html('<p class="vacio">Sin cargas programadas.</p>'))


def _pasadas(d: PlanCarga) -> Html:
    filas, items = [], []
    for p in d.pasadas:
        motivos = unir(h('<span class="tag {}">{}</span>', TAG_NIVEL.get(n, "horario"), n) for n, _ in p.desglose)
        resultado = h('<span class="tag {}">{}</span>', "leido" if p.real_kwh > 0 else "horario", p.resultado)
        filas.append(h('<tr><td class="m">{}<br>{}</td><td class="motivos">{}</td><td class="r m">{}</td>'
                       '<td class="r m">{}</td><td class="r m">{}</td><td class="estado">{}</td></tr>',
                       f.dia(p.inicio), _horario(p.inicio, p.fin), motivos, f.numero(p.plan_kwh),
                       f.numero(p.real_kwh), _eur(p.coste_real), resultado))
        items.append(h('<div class="it-franja"><div class="fila"><span class="m">{} · {}</span><span class="m fuerte">'
                       '{} / {} kWh</span></div><p class="k">{} · {}</p><div>{}</div></div>', f.dia(p.inicio),
                       _horario(p.inicio, p.fin), f.numero(p.real_kwh), f.numero(p.plan_kwh),
                       " · ".join(n for n, _ in p.desglose), _eur(p.coste_real), resultado))
    tabla = (h('<table class="pasadas-plan"><thead><tr><th>Franja</th><th>Motivo</th><th class="r">Plan kWh</th>'
               '<th class="r">Real kWh</th><th class="r">Coste</th><th>Resultado</th></tr></thead><tbody>{}</tbody>'
               '</table>', unir(filas)) if filas else Html('<p class="vacio">Sin franjas en los últimos 7 días.</p>'))
    lista = (unir(items) if items
             else Html('<p class="vacio">Sin franjas en los últimos 7 días.</p>'))
    return h('<section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Últimas franjas · 7 días</h2>'
             '<a href="/historial" class="k">Historial →</a></div><div class="tabla-desplazable">{}</div></section>'
             '<section class="pn solo-movil orden-7"><div class="ph"><h2 class="pt">Últimas franjas · 7 días</h2>'
             '<span class="k m">real / plan</span></div>{}<a href="/historial" class="enlace-centrado">Historial →</a>'
             '</section>', tabla, lista)


# --- Columna derecha -----------------------------------------------------------------------------------


def _manual(d: PlanCarga, form: FormFranja) -> Html:
    def error(campo: str) -> tuple[Html, Html]:
        if campo in form.errores:
            return (h(' aria-invalid="true" aria-describedby="error-f-{}"', campo),
                    h('<p class="error-campo" id="error-f-{}">{}</p>', campo, form.errores[campo]))
        return Html(""), Html("")
    di_a, di_e = error("dia")
    in_a, in_e = error("inicio")
    fi_a, fi_e = error("fin")
    general = h('<p class="error-campo">{}</p>', form.errores["general"]) if "general" in form.errores else Html("")
    modificar = bool(form.anterior_inicio)
    titulo = "Modificar franja" if modificar else "Añadir franja manual"
    return h('<form class="pn orden-4" id="manual" action="/plan/franjas" method="post"><div class="ph"><h2 class="pt">{}'
             '</h2></div><fieldset class="relleno"{}>{}<input type="hidden" name="anterior_inicio" value="{}"><input '
             'type="hidden" name="anterior_fin" value="{}"><div class="campo"><label for="fm-dia" class="k">Día</label>'
             '<input id="fm-dia" name="dia" class="inp" type="date" required value="{}"{}>{}</div><div class="dos"><div '
             'class="campo"><label for="fm-ini" class="k">Inicio</label><input id="fm-ini" name="inicio" class="inp" '
             'type="time" step="900" required value="{}"{}>{}</div><div class="campo"><label for="fm-fin" class="k">Fin'
             '</label><input id="fm-fin" name="fin" class="inp" type="time" step="900" required value="{}"{}>{}</div>'
             '</div><p class="k texto">Se envía al cargador al guardar y se confirma leyéndolo de vuelta. No puede '
             'solaparse con otra tarea activa: si choca, se indica con cuál. El planificador la respeta y no la mueve. '
             'Un fin anterior al inicio es del día siguiente (cruza la medianoche).</p><button type="submit" '
             'class="btn">{}</button>{}</fieldset></form>',
             titulo, Html(" disabled") if d.sombra else Html(""), general, form.anterior_inicio, form.anterior_fin, form.dia or f"{f.local(d.ahora):%Y-%m-%d}", di_a,
             di_e, form.inicio, in_a, in_e, form.fin, fi_a, fi_e, "Guardar cambios" if modificar else "Añadir franja",
             NOTA_SOMBRA if d.sombra else Html(""))


def _ahorro(d: PlanCarga) -> Html:
    def fila(a: Ahorro) -> Html:
        plan = h('<td class="r m">{}</td>', _eur(a.plan)) if d.sombra else Html("")
        return h('<tr><td class="m periodo">{}</td><td class="r m">{}</td>{}<td class="r m">{}</td>'
                 '<td class="r m verde">{}</td></tr>',
                 a.periodo, _eur(a.real), plan, _eur(a.referencia), _eur(a.ahorro))
    plan_th = Html('<th class="r">Plan</th>') if d.sombra else Html("")
    resto = sum((a.resto for a in d.ahorro if a.periodo == "30 días"), Decimal(0))
    sin_precio = max((a.sin_precio for a in d.ahorro), default=0)
    notas = [Html("Hoy: lo cargado hasta ahora en la conexión en curso.")]
    if d.sombra and resto:
        notas.append(h("El plan incluye {} de energía que no tenía (el resto, a los cuartos libres más baratos).",
                       _eur(resto)))
    if sin_precio:
        notas.append(h("{} conexión(es) sin precio o sin factor del contador: fuera de las sumas.", sin_precio))
    return h('<section class="pn orden-5"><div class="ph"><h2 class="pt">Ahorro</h2><span class="k">frente a cargar al '
             'enchufar</span></div><table class="ahorro-plan"><thead><tr><th>Periodo</th><th class="r">Real</th>{}<th class="r">'
             'Al enchufar</th><th class="r">Ahorro</th></tr></thead><tbody>{}</tbody></table><p class="k texto '
             'relleno-x">{}</p></section>', plan_th, unir(fila(a) for a in d.ahorro), unir(notas, " "))


def _tareas(d: PlanCarga) -> Html:
    texto = Html('La guardiana evita que el cargador cargue libremente si no queda ninguna tarea activa. No edites desde '
                 'la app las tareas del planificador: la app las convierte en semanales.')
    if d.tareas_cargador is None:
        return h('<section class="pn orden-6"><div class="ph"><h2 class="pt">Tareas en el cargador</h2></div>'
                 '<p class="k texto relleno-x">Aún sin lectura del programador. {}</p></section>', texto)
    filas = unir(h('<div class="it-tarea"><div><p class="m"><span class="tenue">#{}</span> <span class="tag {}">{}</span>'
                   '</p><p class="k m">{}</p></div><span class="tag {}">{}</span></div>', t.id,
                   {"planificador": "info", "manual": "estimado", "guardiana": "sin-salida"}.get(t.origen, "horario"),
                   t.origen, t.programacion, "leido" if t.estado != "inactiva" else "horario", t.estado)
                 for t in d.tareas_cargador)
    libres = ", ".join(map(str, d.libres)) or "ninguno"
    leida = f" Leído {f.cuando(d.lectura_en, d.ahora)}." if d.lectura_en else ""
    return h('<section class="pn orden-6"><div class="ph"><h2 class="pt">Tareas en el cargador</h2><span class="k m">{} de '
             '10</span></div><div class="lista-tareas">{}</div><p class="k texto relleno-x">Libres: {}. {}{}</p>'
             '</section>', len(d.tareas_cargador), filas, libres, texto, leida)


def _resultado(a: Accion | None) -> Html:
    """Banner tras el PRG (?ok=accion&id=N): pendiente, hecha o error, con «Reintentar» si falló Detener."""
    if a is None:
        return Html("")
    tipo = TIPO_ACCION.get(a.tipo, a.tipo)
    if a.estado == "pendiente":
        return banner("info", f"{tipo}: enviada al cargador. Recarga en unos segundos para ver la confirmación.")
    if a.estado == "hecha":
        return banner("ok", f"{tipo}: {a.resultado}.")
    reintentar = Html("")
    if a.tipo == "detener" and isinstance(a.datos.get("inicio"), str):
        reintentar = h('<form action="/plan/franjas/detener" method="post" class="reintentar"><input type="hidden" '
                       'name="inicio" value="{}"><button type="submit" class="del">Reintentar</button></form>',
                       a.datos["inicio"])
    return h('<div class="banners">{}{}</div>', banner("error", f"{tipo}: {a.resultado}"), reintentar)


def pantalla(d: PlanCarga, form: FormFranja | None = None, fm: FormModo | None = None) -> Html:
    form = form or FormFranja()
    return h('<div class="plan-carga">{}{}{}<div class="columnas-plan"><div class="col">{}{}</div><div class="col lateral">'
             '{}{}{}</div></div></div>', _resultado(d.ultima_accion), _control(d, fm or FormModo()), _seccion_linea(d),
             _franjas(d), _pasadas(d), _manual(d, form), _ahorro(d), _tareas(d))

