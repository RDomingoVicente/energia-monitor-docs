"""Estructura común de las páginas: barra lateral y cabecera (escritorio), cabecera y barra inferior (móvil)."""

from __future__ import annotations

from dataclasses import dataclass

from energia_monitor.formulario import formato
from energia_monitor.formulario.datos import Cabecera
from energia_monitor.formulario.html import Html, h, unir


def _svg(trazos: str, tam: int = 18, color: str = "currentColor") -> Html:
    return Html(f'<svg aria-hidden="true" width="{tam}" height="{tam}" viewBox="0 0 24 24" fill="none" '
                f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{trazos}</svg>')


ICONOS = {
    "inicio": '<rect x="2" y="7" width="18" height="10" rx="2"></rect><path d="M22 11v2"></path>'
              '<path d="M6 10v4"></path><path d="M10 10v4"></path>',
    "excepciones": '<rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M16 2v4"></path>'
                   '<path d="M8 2v4"></path><path d="M3 10h18"></path>',
    "horario": '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>',
    "historial": '<path d="M8 6h13"></path><path d="M8 12h13"></path><path d="M8 18h13"></path>'
                 '<path d="M3 6h.01"></path><path d="M3 12h.01"></path><path d="M3 18h.01"></path>',
    "parametros": '<path d="M4 21v-7"></path><path d="M4 10V3"></path><path d="M12 21v-9"></path>'
                  '<path d="M12 8V3"></path><path d="M20 21v-5"></path><path d="M20 12V3"></path>'
                  '<path d="M2 14h4"></path><path d="M10 8h4"></path><path d="M18 16h4"></path>',
    "avisos": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"></path><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"></path>',
    "ayuda": '<circle cx="12" cy="12" r="9"></circle><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"></path>'
             '<path d="M12 17h.01"></path>',
    "grafana": '<path d="M3 3v18h18"></path><path d="m7 15 4-4 3 3 5-6"></path>',
    "salud": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>',
    "rayo": '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"></path>',
    "ok": '<path d="M20 6 9 17l-5-5"></path>',
    "alerta": '<path d="M12 9v4"></path><path d="M12 17h.01"></path><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17'
              'a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"></path>',
}

PANTALLAS = [("inicio", "/", "Inicio"), ("excepciones", "/excepciones", "Excepciones"),
             ("horario", "/horario", "Horario"), ("historial", "/historial", "Historial"),
             ("parametros", "/parametros", "Parámetros"), ("avisos", "/avisos", "Avisos")]
NAV_MOVIL = [("inicio", "/", "Inicio"), ("excepciones", "/excepciones", "Excepciones"),
             ("horario", "/horario", "Horario"), ("historial", "/historial", "Historial"),
             ("parametros", "/parametros", "Ajustes")]


def icono(nombre: str, tam: int = 18, color: str = "currentColor") -> Html:
    return _svg(ICONOS[nombre], tam, color)


@dataclass(frozen=True)
class Marco:
    version: str
    grafana_url: str | None
    cabecera: Cabecera | None  # None: base de datos no accesible
    ahora_texto: str = ""


def _contador(cab: Cabecera | None, movil: bool = False) -> Html:
    if cab is None or not cab.avisos:
        return Html("")
    n = len(cab.avisos)
    return h('<span class="contador {}" aria-label="{} activos">{}</span>', cab.nivel_avisos, n, n)


def pagina(marco: Marco, activa: str, ruta: str, contenido: Html, titulo: str) -> Html:
    cab = marco.cabecera
    enlaces = []
    for clave, href, texto in PANTALLAS:
        actual = ' class="on" aria-current="page"' if clave == activa else ""
        extra = _contador(cab) if clave == "avisos" else Html("")
        enlaces.append(h('<a href="{}"' + actual + '>{}{}{}</a>', href, icono(clave), texto, extra))
    pie = [h('<a href="/ayuda"{}>{}Ayuda</a>',
             Html(' class="on" aria-current="page"') if activa == "ayuda" else Html(""), icono("ayuda"))]
    if marco.grafana_url:
        pie.append(h('<a href="{}">{}Grafana</a>', marco.grafana_url, icono("grafana")))
    pie.append(h('<a href="/salud">{}/salud</a>', icono("salud")))
    lateral = h(
        '<nav class="side" aria-label="Secciones"><div class="marca">{}<div><p class="m nombre">energia-monitor</p>'
        '<p class="m version">formulario · v{}</p></div></div>{}<div class="hueco"></div>{}</nav>',
        icono("rayo", 22, "#2F81F7"), marco.version, unir(enlaces), unir(pie))

    chips = []
    if cab is not None:
        if cab.avisos:
            n = len(cab.avisos)
            chips.append(h('<a href="/avisos" class="chip {}">{}{} aviso{}</a>', cab.nivel_avisos,
                           icono("avisos", 14), n, "" if n == 1 else "s"))
        chips.append(h('<span class="chip"><span class="dot {}"></span>{}</span>',
                       "ok" if cab.cargador_ok else "error", cab.cargador))
        chips.append(Html('<span class="chip"><span class="dot ok"></span>postgres</span>'))
        if cab.recalculo is not None:
            chips.append(h('<span class="chip">recálculo {}</span>', formato.hora(cab.recalculo)))
    else:
        chips.append(Html('<span class="chip error"><span class="dot error"></span>postgres</span>'))
    cabecera = h('<header class="cabecera"><p class="m ruta">formulario / <span>{}</span></p>'
                 '<div class="chips">{}</div></header>', ruta, unir(chips))

    recalculo = Html("")
    if cab is not None and cab.recalculo is not None:
        recalculo = h('<span class="m recalculo"><span class="dot {}"></span>{}</span>',
                      "ok" if cab.cargador_ok else "error", formato.hora(cab.recalculo))
    n_avisos = len(cab.avisos) if cab else 0
    contador_movil = _contador(cab, movil=True)
    cabecera_movil = h(
        '<header class="cabecera-movil"><p class="m ruta">formulario / <span>{}</span></p>'
        '<div class="acciones">{}<a href="/avisos" class="icono" aria-label="Avisos: {} activos">{}{}</a>'
        '<a href="/ayuda" class="icono" aria-label="Ayuda">{}</a></div></header>',
        ruta, recalculo, n_avisos, icono("avisos", 20), contador_movil, icono("ayuda", 20))
    nav = unir(h('<a href="{}"' + (' class="on" aria-current="page"' if clave == activa else "") + '>{}{}</a>',
                 href, icono(clave, 20), texto) for clave, href, texto in NAV_MOVIL)
    return h(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>{} · batería</title><link rel="stylesheet" href="/estatico/estilo.css?v={}"></head>'
        '<body><div class="app">{}<div class="cuerpo">{}{}<main>{}</main>'
        '<nav class="nav" aria-label="Secciones">{}</nav></div></div></body></html>',
        titulo, marco.version, lateral, cabecera, cabecera_movil, contenido, nav)


def banner(clase: str, texto: Html | str, enlace: tuple[str, str] | None = None) -> Html:
    simbolo = icono("ok", 18, "#3FB950") if clase == "ok" else icono("alerta", 18, "#D29922" if clase == "aviso"
                                                                      else "#F85149")
    extra = h(' <a href="{}">{}</a>', *enlace) if enlace else Html("")
    return h('<section role="status" class="banner {}">{}<p>{}{}</p></section>', clase, simbolo, texto, extra)


def en_preparacion(titulo: str) -> Html:
    return h('<section class="pn"><div class="ph"><h2 class="pt">{}</h2></div>'
             '<p class="vacio">Esta pantalla llega en la v0.5.5. Mientras tanto, usa el CLI'
             ' (docs/parametros.md, «Batería»).</p></section>', titulo)


def error_pagina(marco: Marco, titulo: str, texto: str) -> Html:
    return pagina(marco, "", titulo.lower(), banner("error", texto), titulo)
