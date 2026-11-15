"""Carga externa (formulario-maquetas.md, 7.8; maqueta 08)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import CargasExternas
from energia_monitor.formulario.html import Html, h, unir
from energia_monitor.formulario.plantilla import icono


@dataclass
class FormCarga:
    fecha: str = ""
    hora: str = ""
    kwh: str = ""
    coste_eur: str = ""
    nota: str = ""
    corregir: int | None = None
    anular: int | None = None  # id: formulario de anulación (solo motivo)
    motivo: str = ""
    titulo_anular: str = ""
    errores: dict[str, str] = field(default_factory=dict)


def _error(form: FormCarga, campo: str) -> tuple[Html, Html]:
    if campo in form.errores:
        return (h(' aria-invalid="true" aria-describedby="error-e-{}"', campo),
                h('<p class="error-campo" id="error-e-{}">{}</p>', campo, form.errores[campo]))
    return Html(""), Html("")


def _calculo(d: CargasExternas, form: FormCarga) -> Html:
    factor = f.numero(d.factor, 2)
    try:
        kwh = Decimal(form.kwh) if form.kwh else None
        coste = Decimal(form.coste_eur) if form.coste_eur else None
    except InvalidOperation:
        kwh = coste = None
    if kwh is None or kwh <= 0:
        return h('<div class="m calculo">kWh × eficiencia {} = kWh en la batería → pts<br>1 pt = {} kWh</div>',
                 factor, f.numero(d.capacidad_kwh / 100, 2))
    bateria = kwh * d.factor
    eur = f"<br>coste: {f.numero(coste / kwh, 3)} €/kWh" if coste is not None else ""
    return h('<div class="m calculo">{} kWh × eficiencia {} = <span class="valor">{}</span> → <span class="azul">{}'
             '</span>{}</div>', f.numero(kwh), factor, f.kwh(bateria), f.pts(bateria / d.capacidad_kwh * 100),
             Html(eur))


def _formulario(d: CargasExternas, form: FormCarga) -> Html:
    general = h('<p class="error-campo">{}</p>', form.errores["general"]) if "general" in form.errores else Html("")
    if form.anular is not None:
        mo_a, mo_e = _error(form, "motivo")
        return h('<form class="pn orden-1" id="corregir" action="/carga-externa/{}/anular" method="post">'
                 '<div class="ph"><h2 class="pt">Anular carga externa</h2><span class="k m">{}</span></div>'
                 '<div class="relleno">{}<div class="campo"><label for="e-motivo" class="k">Motivo</label><input '
                 'id="e-motivo" name="motivo" class="inp" type="text" maxlength="80" required value="{}"{}>{}</div>'
                 '<button type="submit" class="btn peligro">Anular carga externa</button><a href="/carga-externa" '
                 'class="enlace-centrado">Cancelar</a></div></form>',
                 form.anular, form.titulo_anular, general, form.motivo, mo_a, mo_e)
    fe_a, fe_e = _error(form, "fecha")
    ho_a, ho_e = _error(form, "hora")
    kw_a, kw_e = _error(form, "kwh")
    co_a, co_e = _error(form, "coste_eur")
    if form.corregir is not None:
        titulo, accion, boton = "Corregir carga externa", f"/carga-externa/{form.corregir}/corregir", "Guardar corrección"
        cancelar = Html('<a href="/carga-externa" class="enlace-centrado">Cancelar</a>')
    else:
        titulo, accion, boton, cancelar = ("Registrar carga externa", "/carga-externa", "Guardar carga externa",
                                           Html(""))
    return h(
        '<form class="pn orden-1" id="corregir" action="{}" method="post"><div class="ph"><h2 class="pt">{}</h2></div>'
        '<div class="relleno">{}<p class="k texto">Cargas hechas fuera de casa (cargador público, destino). Anótalas para '
        'que el nivel y la reconciliación cuadren. Al volver, anota también el % que marca el coche.</p>'
        '<div class="dos"><div class="campo"><label for="e-fecha" class="k">Fecha</label><input id="e-fecha" name="fecha" '
        'class="inp" type="date" required value="{}"{}>{}</div><div class="campo"><label for="e-hora" class="k">Hora '
        'aproximada</label><input id="e-hora" name="hora" class="inp" type="time" required value="{}"{}>{}</div></div>'
        '<div class="dos"><div class="campo"><label for="e-kwh" class="k">Energía cargada (opcional)</label>'
        '<div class="con-unidad-k"><input id="e-kwh" name="kwh" class="inp" type="number" inputmode="decimal" min="0" '
        'max="150" step="0.1" value="{}"{}><span class="k m">kWh</span></div>{}</div><div class="campo"><label '
        'for="e-coste" class="k">Coste (opcional)</label><div class="con-unidad-k"><input id="e-coste" name="coste_eur" '
        'class="inp" type="number" inputmode="decimal" min="0" step="0.01" value="{}"{}><span class="k m">€</span></div>'
        '{}</div></div>{}<p class="k texto">Sin kWh se registra igual: el nivel se fija con tu próxima lectura y ese '
        'intervalo queda fuera de la mediana y del P75.</p><div class="campo"><label for="e-nota" class="k">Lugar o nota '
        '(opcional)</label><input id="e-nota" name="nota" class="inp" type="text" maxlength="80" value="{}"></div>'
        '<button type="submit" class="btn">{}</button>{}</div></form>',
        accion, titulo, general, form.fecha, fe_a, fe_e, form.hora, ho_a, ho_e, form.kwh, kw_a, kw_e, form.coste_eur,
        co_a, co_e, _calculo(d, form), form.nota, boton, cancelar)


def _lista(d: CargasExternas) -> Html:
    filas, items = [], []
    for x in d.filas:
        acciones = h('<div class="acciones-fila"><a href="/carga-externa?corregir={}#corregir" class="lk">Corregir</a>'
                     '<a href="/carga-externa?anular={}#corregir" class="lk peligro">Anular</a></div>', x.id, x.id)
        if x.kwh is not None:
            energia, sub_e = f.kwh(x.kwh), f"nivel {f.pts(x.pts or 0)}"
        else:
            energia, sub_e = "—", "sin kWh"
        coste = f"{f.numero(x.coste_eur, 2)} €" if x.coste_eur is not None else "—"
        sub_c = f"{f.numero(x.eur_kwh, 3)} €/kWh" if x.eur_kwh is not None else "—"
        nota = x.nota or ("Sin kWh: intervalo fuera de la estadística" if x.kwh is None else "")
        filas.append(h('<tr><td class="m">{}</td><td class="r m">{}<span class="detalle-celda">{}</span></td>'
                       '<td class="r m">{}<span class="detalle-celda">{}</span></td><td class="nota-celda">{}</td>'
                       '<td class="r">{}</td></tr>', f.dia_hora(x.instante), energia, sub_e, coste, sub_c, nota, acciones))
        detalle = " · ".join(p for p in (coste if x.coste_eur is not None else "", sub_c if x.eur_kwh is not None else "",
                                         sub_e) if p)
        items.append(h('<div class="it-historial"><div class="fila"><span class="m">{}</span><span class="m">{}</span>'
                       '</div><p class="k m">{}</p>{}{}</div>', f.dia_hora(x.instante), energia, detalle,
                       h('<p class="k">{}</p>', nota) if nota else Html(""), acciones))
    total = sum((x.coste_eur for x in d.filas if x.coste_eur is not None), Decimal(0))
    n = len(d.filas)
    resumen = f"90 días · {n} carga{'s' if n != 1 else ''} · {f.numero(total, 2)} €"
    if not filas:
        vacio = Html('<p class="vacio">Ninguna carga externa en los últimos 90 días.</p>')
        tabla, lista = vacio, vacio
    else:
        tabla = h('<div class="tabla-desplazable"><table><thead><tr><th>Fecha</th><th class="r">Energía</th><th class="r">'
                  'Coste</th><th>Lugar o nota</th><th class="r"><span class="oculto">Acciones</span></th></tr></thead>'
                  '<tbody>{}</tbody></table></div>', unir(filas))
        lista = unir(items)
    return h('<section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Cargas externas</h2><span class="k m">{}'
             '</span></div>{}<p class="k pie-tabla">Se suman al nivel como una carga de casa (× eficiencia de carga) y su '
             'coste entra en el gasto del coche de Grafana. Corregir o anular, nunca borrar.</p></section>'
             '<section class="pn solo-movil orden-2"><div class="ph"><h2 class="pt">Cargas externas · 90 días</h2>'
             '<span class="k m">{} €</span></div><div class="lista-historial">{}</div></section>',
             resumen, tabla, f.numero(total, 2), lista)


def pantalla(d: CargasExternas, form: FormCarga, avisos: list[Html]) -> Html:
    zona = h('<div class="banners orden-0">{}</div>', unir(avisos)) if avisos else Html("")
    nota = h('<section role="status" class="banner info solo-escritorio">{}<p>Si al anotar una lectura el coche tiene '
                'claramente más batería de la esperada (más de umbral_carga_externa_kwh), se muestra el aviso «¿Cargaste '
                'fuera de casa?» con enlace a esta pantalla.</p></section>', icono("info", 18, "#58A6FF"))
    return h('<div class="dos-columnas"><div class="col">{}{}{}</div><div class="col lateral">{}</div></div>',
             zona, _lista(d), nota, _formulario(d, form))
