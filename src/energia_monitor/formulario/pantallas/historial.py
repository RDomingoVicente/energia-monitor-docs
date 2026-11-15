"""Historial (formulario-maquetas.md, 7.4; maqueta 04)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import FilaHistorial, Historial
from energia_monitor.formulario.html import Html, h, unir

UMBRAL_DIFERENCIA_PTS = 3  # ámbar si |leído − estimado| ≥ 3 pts (7.4)
PERIODOS = (("30", "30 d"), ("90", "90 d"), ("todo", "todo"))


@dataclass
class FormCorregir:
    """Corregir (pct, fecha, hora, nota) o anular (motivo) una lectura."""

    accion: str  # corregir o anular
    lectura: dict[str, Any]
    pct: str = ""
    fecha: str = ""
    hora: str = ""
    nota: str = ""
    motivo: str = ""
    errores: dict[str, str] = field(default_factory=dict)


def _hace(instante: datetime | None, ahora: datetime) -> str:
    if instante is None:
        return "—"
    s = (ahora - instante).total_seconds()
    if s < 3600:
        return f"hace {int(s // 60)} min"
    if s < 2 * 86400:
        return f"hace {int(s // 3600)} h"
    return f"hace {int(s // 86400)} d"


def _periodo(d: Historial) -> str:
    return "todo" if d.dias is None else str(d.dias)


def _enlace(d: Historial, periodo: str | None = None, anuladas: bool | None = None) -> str:
    p = periodo or _periodo(d)
    a = d.con_anuladas if anuladas is None else anuladas
    return f"/historial?periodo={p}" + ("&anuladas=1" if a else "")


def _indicadores(d: Historial) -> Html:
    comparables = [x for x in d.filas if x.tipo == "lectura" and x.comparable and not x.anulada]
    completas = sum(1 for x in d.filas if x.tipo == "carga_completa")
    etiqueta = "todo" if d.dias is None else f"{d.dias} d"
    if comparables:
        difs = [x.diferencia_pts for x in comparables if x.diferencia_pts is not None]
        medio = sum(difs, Decimal(0)) / len(difs)
        absoluto = sum((abs(x) for x in difs), Decimal(0)) / len(difs)
        medio_txt = f"{'+' if medio > 0 else ''}{f.numero(medio)} pts"
        sentido = ("el coche suele tener menos de lo estimado" if medio < 0
                   else "el coche suele tener más de lo estimado" if medio > 0 else "sin sesgo")
        medio_sub = f"media con signo: {sentido}"
        abs_txt = f"{f.numero(absoluto)} pts"
        abs_sub = f"≈ {f.numero(absoluto * d.capacidad_kwh / 100)} kWh · tamaño típico del fallo"
    else:
        medio_txt = abs_txt = "—"
        medio_sub = abs_sub = "sin lecturas comparables en el periodo"
    validas = len(comparables) + completas
    anuladas = f" · {d.anuladas_periodo} anulada{'s' if d.anuladas_periodo != 1 else ''} (no cuenta" \
               f"{'n' if d.anuladas_periodo != 1 else ''})" if d.anuladas_periodo else ""
    validas_sub = (f"{len(comparables)} comparable{'s' if len(comparables) != 1 else ''} · {completas} de carga "
                   f"completa{anuladas}")
    dias_aviso = f.entero(d.aviso_dias)
    return h(
        '<section class="resumen" aria-label="Precisión del modelo">'
        '<div class="pn indicador"><p class="k">Error medio · {}</p><p class="big m">{}</p><p class="sub">{}</p></div>'
        '<div class="pn indicador"><p class="k">Error absoluto medio</p><p class="big m">{}</p><p class="sub m">{}</p>'
        '</div><div class="pn indicador solo-escritorio"><p class="k">Lecturas válidas</p><p class="big m">{}</p>'
        '<p class="sub m">{}</p></div><div class="pn indicador solo-escritorio"><p class="k">Última lectura</p>'
        '<p class="big m">{}</p><p class="sub m">umbral de aviso: {} día{}</p></div></section>',
        etiqueta, medio_txt.replace("-", f.MENOS), medio_sub, abs_txt, abs_sub, validas, validas_sub,
        _hace(d.ultima_lectura, d.ahora), dias_aviso, "" if dias_aviso == 1 else "s")


def _filtros(d: Historial, movil: bool = False) -> Html:
    botones = [h('<a class="fl{}" href="{}"{}>{}</a>', " on" if _periodo(d) == clave else "",
                 _enlace(d, periodo=clave), Html(' aria-current="true"') if _periodo(d) == clave else Html(""), texto)
               for clave, texto in PERIODOS]
    if not movil:
        botones.append(h('<a class="fl{}" href="{}"{}>incluir anuladas</a>', " on" if d.con_anuladas else "",
                         _enlace(d, anuladas=not d.con_anuladas),
                         Html(' aria-current="true"') if d.con_anuladas else Html("")))
    return h('<div class="filtros" role="group" aria-label="Periodo">{}</div>', unir(botones))


def _acciones(x: FilaHistorial) -> Html:
    if x.anulada or x.tipo == "carga_completa" or x.id is None:
        return Html("")
    base = "/historial" if x.tipo == "lectura" else "/carga-externa"
    return h('<div class="acciones-fila"><a href="{}?corregir={}#corregir" class="lk">Corregir</a>'
             '<a href="{}?anular={}#corregir" class="lk peligro">Anular</a></div>', base, x.id, base, x.id)


def _dif(x: FilaHistorial) -> tuple[str, str]:
    if x.diferencia_pts is None or x.anulada:
        return "—", ""
    clase = "aviso" if x.tipo == "lectura" and abs(x.diferencia_pts) >= UMBRAL_DIFERENCIA_PTS else ""
    return f.pts(x.diferencia_pts), clase


def _nota(x: FilaHistorial) -> str:
    if x.tipo == "carga_externa":
        partes = [p for p in (x.nota, f"{f.numero(x.coste_eur, 2)} €" if x.coste_eur is not None else None) if p]
        return " · ".join(partes)
    if x.tipo == "carga_completa":
        return "referencia"
    if x.anulada:
        return x.motivo or ""
    partes = [p for p in (x.nota, f"corrige a {f.hora(x.corrige_instante)}" if x.corrige_instante else None) if p]
    return " · ".join(partes)


def _tabla(d: Historial) -> Html:
    filas, items = [], []
    for x in d.filas:
        dif, clase = _dif(x)
        origen = {"lectura": "usuario", "carga_externa": "carga externa", "carga_completa": "carga completa"}[x.tipo]
        if x.tipo == "carga_externa":
            leido = estimado = "—"
            estado = h('<span class="tag {}">{}</span>', "anulada" if x.anulada else "externa",
                       "anulada" if x.anulada else (f.kwh(x.kwh) if x.kwh is not None else "sin kWh"))
        else:
            leido, estimado = f.pct(x.pct), f.pct(x.estimado_pct)
            estado = (Html('<span class="tag anulada">anulada</span>') if x.anulada
                      else Html('<span class="tag valida">válida</span>'))
        filas.append(h('<tr{}><td class="m fecha">{}</td><td class="r m">{}</td><td class="r m">{}</td><td class="r m {}">{}'
                       '</td><td>{}</td><td>{}</td><td class="nota-celda">{}</td><td class="r">{}</td></tr>',
                       Html(' class="tenue"') if x.anulada else Html(""), f.dia_hora(x.instante), leido, estimado,
                       clase, dif, origen, estado, _nota(x), _acciones(x)))
        if x.tipo == "carga_externa":
            cifra = f.kwh(x.kwh, signo=True) if x.kwh is not None else "sin kWh"
            detalle = h('<p class="k m">{}</p>', " · ".join(p for p in ("carga externa", _nota(x)) if p))
        elif x.tipo == "carga_completa":
            cifra, detalle = f.pct(x.pct), Html('<p class="k m">carga completa</p>')
        elif x.anulada:
            cifra, detalle = f.pct(x.pct), h('<p class="k"><span class="tag anulada">anulada</span> {}</p>', _nota(x))
        else:
            est = f"est. {f.pct(x.estimado_pct)}" if x.estimado_pct is not None else "sin estimación previa"
            detalle = h('<p class="k m">{}{}</p>', est, h(' · <span class="{}">{}</span>', clase, dif)
                        if dif != "—" else Html(""))
            cifra = f.pct(x.pct)
        items.append(h('<div class="it-historial"><div class="fila"><span class="m">{}</span><span class="m">{}</span>'
                       '</div>{}{}</div>', f.dia_hora(x.instante), cifra, detalle, _acciones(x)))
    if not filas:
        vacio = Html('<p class="vacio">No hay lecturas en el periodo.</p>')
        return vacio, vacio  # type: ignore[return-value]
    tabla = h('<div class="tabla-desplazable"><table class="historial"><thead><tr><th>Fecha ▾</th><th class="r">Leído'
              '</th><th class="r">Estimado</th><th class="r">Diferencia</th><th>Origen</th><th>Estado</th><th>Nota</th>'
              '<th class="r"><span class="oculto">Acciones</span></th></tr></thead><tbody>{}</tbody></table></div>',
              unir(filas))
    return tabla, unir(items)  # type: ignore[return-value]


def _form_corregir(c: FormCorregir) -> Html:
    lec = c.lectura

    def error(campo: str) -> tuple[Html, Html]:
        if campo in c.errores:
            return (h(' aria-invalid="true" aria-describedby="error-c-{}"', campo),
                    h('<p class="error-campo" id="error-c-{}">{}</p>', campo, c.errores[campo]))
        return Html(""), Html("")

    general = h('<p class="error-campo">{}</p>', c.errores["general"]) if "general" in c.errores else Html("")
    titulo = f"{f.dia_hora(lec['instante'])} · {f.pct(lec['pct'])}"
    if c.accion == "anular":
        mo_a, mo_e = error("motivo")
        return h('<form class="pn orden-1" id="corregir" action="/lecturas/{}/anular" method="post"><div class="ph">'
                 '<h2 class="pt">Anular lectura</h2><span class="k m">{}</span></div><div class="relleno">{}'
                 '<div class="campo"><label for="c-motivo" class="k">Motivo</label><input id="c-motivo" name="motivo" '
                 'class="inp" type="text" maxlength="80" required value="{}"{}>{}</div><p class="k texto">La lectura '
                 'no se borra: queda anulada con este motivo y deja de contar.</p><button type="submit" class="btn '
                 'peligro">Anular lectura</button><a href="/historial" class="enlace-centrado">Cancelar</a></div></form>',
                 lec["id"], titulo, general, c.motivo, mo_a, mo_e)
    pc_a, pc_e = error("pct")
    fe_a, fe_e = error("fecha")
    ho_a, ho_e = error("hora")
    return h('<form class="pn orden-1" id="corregir" action="/lecturas/{}/corregir" method="post"><div class="ph">'
             '<h2 class="pt">Corregir lectura</h2><span class="k m">{}</span></div><div class="relleno">{}'
             '<div class="campo"><label for="c-pct" class="k">Porcentaje</label><div class="con-unidad"><input '
             'id="c-pct" name="pct" class="inp" type="number" inputmode="numeric" min="0" max="100" step="1" required '
             'placeholder="0–100" value="{}"{}><span>%</span></div>{}</div><div class="dos"><div class="campo"><label '
             'for="c-fecha" class="k">Fecha</label><input id="c-fecha" name="fecha" class="inp" type="date" required '
             'value="{}"{}>{}</div><div class="campo"><label for="c-hora" class="k">Hora</label><input id="c-hora" '
             'name="hora" class="inp" type="time" required value="{}"{}>{}</div></div><div class="campo"><label '
             'for="c-nota" class="k">Nota (opcional)</label><input id="c-nota" name="nota" class="inp" type="text" '
             'maxlength="80" value="{}"></div><p class="k texto">Se crea una lectura nueva y la anterior queda anulada '
             'con motivo «corregida».</p><button type="submit" class="btn">Guardar corrección</button><a href="/historial" '
             'class="enlace-centrado">Cancelar</a></div></form>',
             lec["id"], titulo, general, c.pct, pc_a, pc_e, c.fecha, fe_a, fe_e, c.hora, ho_a, ho_e, c.nota)


def pantalla(d: Historial, avisos: list[Html], corregir: FormCorregir | None = None) -> Html:
    tabla, lista = _tabla(d)
    zona = h('<div class="banners">{}</div>', unir(avisos)) if avisos else Html("")
    form = _form_corregir(corregir) if corregir else Html("")
    cap = f.numero(d.capacidad_kwh / 100, 2)
    return h('{}{}{}<section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Lecturas y cargas externas</h2>{}'
             '</div>{}<p class="k pie-tabla">Las lecturas no se borran: «Corregir» crea una nueva y anula la anterior; '
             '«Anular» pide motivo. Diferencia = leído − estimado, en puntos de batería (1 pt = {} kWh).</p></section>'
             '<div class="solo-movil">{}</div><section class="pn solo-movil"><div class="ph"><h2 class="pt">Lecturas y '
             'cargas</h2><span class="k m">leído / estimado</span></div><div class="lista-historial">{}</div></section>',
             zona, form, _indicadores(d), _filtros(d), tabla, cap, _filtros(d, movil=True), lista)
