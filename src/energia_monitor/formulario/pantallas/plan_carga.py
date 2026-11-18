"""Plan de carga (formulario-maquetas.md, 7.9; maqueta 01d). Paso 3b: solo lectura.

Las acciones que escriben (modo, Cargar ahora, Detener, Modificar, Eliminar, Añadir franja) se muestran
desactivadas con «disponible en el paso 3c». En sombra las franjas no se escriben: estados de sombra de 7.9.
La línea de tiempo se pinta con HTML y clases (la CSP no admite estilos en línea): q-N y n-N son la posición y
el ancho en cuartos de hora dentro de la vista (lt-240: 60 h; lt-96: 24 h), h-N y b-N alturas en % de la escala
0–0,60 €/kWh.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.html import Html, h, unir
from energia_monitor.formulario.plan_datos import ESCALA_EUR_KWH, Ahorro, FranjaPlan, Linea, PlanCarga
from energia_monitor.tiempo import MADRID

PASO_3C = "disponible en el paso 3c"
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
    return unir(lineas)


# --- Panel de control ---------------------------------------------------------------------------


def _control(d: PlanCarga) -> Html:
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
    opciones = unir(h('<label title="{}"><input type="radio" name="modo" value="{}" disabled{}>{}</label>', titulo, modo,
                      Html(" checked") if modo == d.modo else Html(""), modo) for modo, titulo in MODOS)
    nombre, frase = FRASE_MODO.get(d.modo, ("", ""))
    return h(
        '<section class="pn control-plan orden-1"><p class="pt solo-movil cabecera-control">Planificador <span class="tag '
        'estimado">{}</span></p><div class="estado-plan">{}</div>'
        '<div class="modo-plan"><span class="k">Modo</span><div class="seg desactivado" role="radiogroup" '
        'aria-label="Modo del planificador">{}</div><p class="k texto"><span class="destacado">{}</span> {}</p>'
        '<p class="k paso">{}</p></div>'
        '<div class="ahora-plan"><span class="k">Ignora el precio</span><button type="button" class="btn2" disabled '
        'title="{}">Cargar ahora</button><p class="k texto">Hasta el objetivo de la próxima salida.</p>'
        '<p class="k paso">{}</p></div></section>',
        d.modo.upper(), estado, opciones, nombre, frase, PASO_3C, PASO_3C, PASO_3C)


# --- Línea de tiempo -----------------------------------------------------------------------------


def _altura(precio: Decimal) -> int:
    return max(1, min(100, int(precio / ESCALA_EUR_KWH * 100)))


def _linea(d: PlanCarga, ln: Linea, movil: bool) -> Html:
    n = ln.cuartos
    bloques: list[Html] = []
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
        bloques.append(h('<div class="lt-bloque {} q-{} n-{}">{}<span class="m">{}</span></div>'
                         '<span class="lt-hor m q-{}">{}</span>', clase, i, j - i, relleno, texto, (i + j) // 2,
                         _horario(fr.inicio, fr.fin)))
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


def _estado_cargador(d: PlanCarga) -> str:
    return "no escrita (sombra)" if d.sombra else "pendiente"


def _nota_ejecucion(fr: FranjaPlan) -> str:
    if fr.progreso_kwh is not None:
        return f"{f.numero(fr.progreso_kwh)} de {f.numero(fr.kwh)} kWh en su horario"
    return ""


def _acciones(fr: FranjaPlan) -> Html:
    if fr.ejecucion != "programada":  # en curso: solo Detener
        return h('<button type="button" class="del" disabled title="{}">Detener</button>', PASO_3C)
    return h('<button type="button" class="mod" disabled title="{}">Modificar</button><button type="button" '
             'class="del" disabled title="{}">Eliminar</button>', PASO_3C, PASO_3C)


def _franjas(d: PlanCarga) -> Html:
    filas, tarjetas = [], []
    for fr in d.franjas:
        energia = h('<span class="m fuerte">{} kWh</span><span class="m azul">{}</span><span class="m">{}</span>'
                    '<span class="k m">{}</span>', f.numero(fr.kwh), _pts(d, fr.kwh), _eur(fr.coste, fr.provisional),
                    _precio(fr.precio_medio))
        ejecucion = h('<span class="tag horario">{}</span><span class="tag {}">{}</span>{}', _estado_cargador(d),
                      "leido" if "cargando" in fr.ejecucion else "horario", fr.ejecucion,
                      h('<span class="k m nota-ejecucion">{}</span>', _nota_ejecucion(fr))
                      if _nota_ejecucion(fr) else Html(""))
        tareas = h(' <span class="k m">· {} tareas</span>', fr.tareas) if fr.tareas > 1 else Html("")
        filas.append(h('<tr><td><span class="m">{}</span><br><span class="m">{}</span><br><span class="k m">{}{}</span>'
                       '</td><td class="motivos">{}</td><td class="r energia">{}</td><td class="ejecucion">{}</td>'
                       '<td class="r acciones-col">{}</td></tr>', f.dia(fr.inicio), _horario(fr.inicio, fr.fin),
                       _duracion(fr.inicio, fr.fin), tareas, _desglose(fr), energia, ejecucion, _acciones(fr)))
        tarjetas.append(h('<div class="it-franja"><div class="fila"><span class="m">{} · {}</span><span class="m '
                          'fuerte">{} kWh</span></div><div class="motivos">{}</div><div class="fila"><span class="k m">'
                          '{} · {} · {}</span><span class="tag {}">{}</span></div><div class="acciones-fila">{}</div>'
                          '</div>', f.dia(fr.inicio), _horario(fr.inicio, fr.fin), f.numero(fr.kwh), _desglose(fr),
                          _pts(d, fr.kwh), _eur(fr.coste, fr.provisional), _precio(fr.precio_medio),
                          "leido" if "cargando" in fr.ejecucion else "horario", fr.ejecucion, _acciones(fr)))
    cuerpo = (h('<table class="fija franjas-plan"><colgroup><col class="c-franja"><col><col class="c-energia">'
                '<col class="c-ejecucion"><col class="c-acciones"></colgroup><thead><tr><th>Franja</th><th>Motivo · kWh'
                '</th><th class="r">Energía · coste</th><th>Cargador · ejecución</th><th class="r"><span class="oculto">Acciones'
                '</span></th></tr></thead><tbody>{}</tbody></table>', unir(filas))
              if filas else Html('<p class="vacio">Sin cargas programadas.</p>'))
    pie = Html('<p class="k texto pie-tabla">La franja en curso solo se puede detener; luego, eliminar. Una franja que '
               'cruza la medianoche se muestra como una sola y se escribe como 2 tareas. Al detener o eliminar una '
               'franja automática, el planificador no la vuelve a crear en ese tramo. Las acciones estarán disponibles '
               'en el paso 3c.</p>')
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


def _manual(d: PlanCarga) -> Html:
    hoy = f"{f.local(d.ahora):%Y-%m-%d}"
    # Sin action ni POST en 3b: el formulario de verdad llega en 3c.
    return h('<section class="pn orden-4 desactivado"><div class="ph"><h2 class="pt">Añadir '
             'franja manual</h2></div><fieldset disabled class="relleno"><div class="campo"><label for="fm-dia" '
             'class="k">Día</label><input id="fm-dia" class="inp" type="date" value="{}"></div><div class="dos"><div '
             'class="campo"><label for="fm-ini" class="k">Inicio</label><input id="fm-ini" class="inp" type="time" '
             'step="900"></div><div class="campo"><label for="fm-fin" class="k">Fin</label><input id="fm-fin" '
             'class="inp" type="time" step="900"></div></div><p class="k texto">Se envía al cargador al guardar y se '
             'confirma leyéndolo de vuelta. No puede solaparse con otra tarea activa: si choca, se indica con cuál. El '
             'planificador la respeta y no la mueve.</p><button type="button" class="btn">Añadir franja</button>'
             '<p class="k paso">{}</p></fieldset></section>', hoy, PASO_3C)


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


def _tareas() -> Html:
    return h('<section class="pn orden-6"><div class="ph"><h2 class="pt">Tareas en el cargador</h2><span class="k m paso">'
             'paso 3c</span></div><p class="k texto relleno-x">{}: la lectura del programador llega en el paso 3c. En '
             'sombra el plan no tiene en cuenta las tareas de la app de Orbis (E3.4). La guardiana evita que el cargador '
             'cargue libremente si no queda ninguna tarea activa. No edites desde la app las tareas del planificador.'
             '</p></section>', PASO_3C.capitalize())


def pantalla(d: PlanCarga) -> Html:
    return h('<div class="plan-carga">{}{}<div class="columnas-plan"><div class="col">{}{}</div><div class="col lateral">'
             '{}{}{}</div></div></div>', _control(d), _seccion_linea(d), _franjas(d), _pasadas(d), _manual(d),
             _ahorro(d), _tareas())

