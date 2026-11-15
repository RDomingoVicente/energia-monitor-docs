"""Avisos (formulario-maquetas.md, 7.7; maqueta 07). Se calculan solos: sin «marcar como leído»."""

from __future__ import annotations

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import Avisos
from energia_monitor.formulario.html import Html, h, unir

ACCIONES = {"lectura": ("/#lectura", "Anotar lectura →"), "historial": ("/historial", "Revisar en Historial →"),
            "carga_externa": ("/carga-externa", "Registrar carga externa →"),
            "excepciones": ("/excepciones", "Revisar excepciones →"),
            "parametros": ("/parametros?clave=factor_bateria#cambiar", "Revisar en Parámetros →")}
CATALOGO = (
    ("error", "Cargador no accesible · Base de datos no accesible",
     "Inmediato. Se resuelve solo al recuperarse (la página no consulta el cargador: mira la última muestra)."),
    ("aviso", "Sin lecturas · Hueco de muestreo · Intervalo incoherente · ¿Cargaste fuera de casa? · Objetivo por "
              "encima del tope · Carga completa dudosa",
     "Umbrales en Parámetros: aviso_sin_lectura_dias, hueco_muestreo_aviso_s, umbral_carga_externa_kwh, tope_pct."),
    ("info", "Calibración sugerida (eficiencia de carga)", "Desaparece al registrar el valor sugerido."),
)


def _tag(nivel: str) -> Html:
    return h('<span class="tag m nivel-{}">{}</span>', nivel, nivel.upper())


def pantalla(d: Avisos) -> Html:
    activos = []
    for a in d.activos:
        desde = f"desde {f.dia_hora(a.desde)} · " if a.desde else ""
        enlace = h('<a href="{}" class="lk">{}</a>', *ACCIONES[a.accion]) if a.accion in ACCIONES else Html("")
        activos.append(h('<div class="it-aviso"><div><p class="titulo-aviso">{}<span>{}</span></p><p class="detalle">{}'
                         '</p><p class="k m">{}{}</p></div>{}</div>', _tag(a.nivel), a.titulo, a.detalle, desde,
                         a.regla, enlace))
    n = len(d.activos)
    lista = unir(activos) if activos else Html('<p class="vacio">Ningún aviso activo.</p>')
    filas = []
    for r in d.resueltos:
        fin = f.hora(r.fin) if f.local(r.fin).date() == f.local(r.inicio).date() else f.corta(r.fin)
        filas.append(h('<tr><td class="m">{}</td><td>{}</td><td class="nota-celda">{}</td><td class="r m">resuelto {}</td>'
                       '</tr>', f.dia_hora(r.inicio), r.titulo.lower(), r.detalle or "", fin))
    resueltos = (h('<div class="tabla-desplazable"><table><thead><tr><th>Inicio</th><th>Tipo</th><th>Detalle</th>'
                   '<th class="r">Estado</th></tr></thead><tbody>{}</tbody></table></div>', unir(filas)) if filas
                 else Html('<p class="vacio">Ninguno en los últimos 7 días.</p>'))
    catalogo = unir(h('<div class="it-aviso"><div><p class="titulo-aviso">{}<span>{}</span></p><p class="k texto">{}</p>'
                      '</div></div>', _tag(nivel), titulo, texto) for nivel, titulo, texto in CATALOGO)
    return h('<div class="dos-columnas"><div class="col"><section class="pn"><div class="ph"><h2 class="pt">Activos · {}'
             '</h2><span class="k solo-escritorio">se generan solos y desaparecen al resolverse la causa</span></div>{}'
             '</section><p class="k solo-movil">Se generan solos a partir de los datos y desaparecen cuando se resuelve '
             'la causa.</p><section class="pn"><div class="ph"><h2 class="pt">Resueltos · últimos 7 días</h2></div>{}'
             '</section></div><section class="pn solo-escritorio"><div class="ph"><h2 class="pt">Tipos de aviso</h2>'
             '</div>{}</section></div>', n, lista, resueltos, catalogo)
