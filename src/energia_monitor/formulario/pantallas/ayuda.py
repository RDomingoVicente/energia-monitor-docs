"""Ayuda (formulario-maquetas.md, 7.6; maqueta 06).

Los textos «qué es» y «efecto» de cada parámetro salen de bateria_parametro_clave
y las cifras del ejemplo, de los valores vigentes; el resto es fijo. La búsqueda
(?q=) se hace en el servidor: se muestran solo los bloques que contienen el texto.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import Parametros, Plan
from energia_monitor.formulario.html import Html, h, unir
from energia_monitor.formulario.pantallas.parametros import rango, valor

ESTADOS = (
    ("leido", "LEÍDO", "Acabas de anotar el % y no ha pasado nada después. Valor exacto."),
    ("leido_carga", "LEÍDO + CARGA", "Desde la lectura solo ha habido cargas: lectura + kWh medidos × eficiencia de "
                                     "carga. Fiable."),
    ("estimado", "ESTIMADO", "Ha habido trayectos (o el coche está fuera): se restan con la mediana. Pierde precisión "
                             "con cada trayecto sin lectura."),
)
PANTALLAS = (
    ("Inicio", "Estado actual: nivel con su etiqueta, coche, próxima salida y energía necesaria. Próximas cargas del "
               "plan, anotar lectura y próximos 7 días."),
    ("Plan de carga", "Lo que ha calculado el planificador: franjas con su motivo, energía y coste, precio y franjas en "
                      "una línea de tiempo, últimas franjas y ahorro. En sombra solo se muestra; las acciones llegan "
                      "en el paso 3c."),
    ("Excepciones", "Días que no siguen el horario: con salida (consumo normal P75 o indicado en km o %) o sin salida. "
                    "Una por fecha; se pueden modificar o dar de baja."),
    ("Horario", "Primera salida de cada día de la semana. Los cambios se aplican desde que se guardan."),
    ("Historial", "Lecturas frente a lo estimado. Mide lo bien que acierta el modelo. Corregir o anular, nunca borrar."),
    ("Carga externa", "Cargas fuera de casa con kWh y coste. Desde Inicio («Registrar carga externa») o desde el aviso "
                      "«¿Cargaste fuera de casa?»."),
    ("Parámetros", "Valores del modelo por grupos, con su origen y vigencia. Sugerencias de calibración que tú decides "
                   "aplicar."),
    ("Avisos", "Avisos activos y resueltos en los últimos 7 días. Se generan solos y desaparecen al resolverse la causa."),
)


# Plan de carga (etapa 3, E3.4 y E3.6): modos, niveles y guardiana.
PLAN_CARGA = (
    ("horario", "sombra", "Calcula el plan cada 15 minutos y con cada cambio, y lo muestra, pero no escribe en el "
                          "cargador. Es el modo de la puesta en marcha."),
    ("horario", "activo", "Escribe sus franjas en el cargador y las revisa cada 15 minutos (paso 3c)."),
    ("horario", "pausa", "No escribe y desactiva sus franjas futuras; la franja en curso y la guardiana siguen."),
    ("info", "necesaria", "La energía para salir con el objetivo de cada salida, en las horas más baratas antes de "
                          "salir; si no bastan las baratas, también las caras."),
    ("ok", "margen", "Un porcentaje más sobre la necesaria, solo en horas baratas con precio publicado."),
    ("ok", "oportunista", "Carga de más cuando la energía está barata, hasta el nivel oportunista; nunca por encima "
                          "del tope."),
    ("estimado", "guardiana", "Una tarea del cargador siempre activa (31/12 a las 00:00, 1 minuto) para que nunca "
                              "cargue libremente cuando no hay franjas. El sistema no la toca nunca."),
)


@dataclass
class AyudaDatos:
    parametros: Parametros
    plan: Plan
    estado: dict[str, Any]


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).casefold()


def _glosario(d: AyudaDatos) -> list[tuple[str, str]]:
    p = d.parametros
    pt = d.plan.capacidad_kwh / 100
    ventana = f.entero(p.de("ventana_consumo_dias").valor)
    desconexion = valor("desconexion_min_s", "s", p.de("desconexion_min_s").valor)
    percentil = p.de("percentil_planificacion").valor
    return [
        ("Nivel", "Porcentaje de batería que se estima o has leído, con su etiqueta de fiabilidad."),
        ("%", "Nivel absoluto de batería: 52 %, objetivo 59 %."),
        ("pts", f"Puntos de batería: diferencia entre dos niveles. 70 % − 59 % = 11 pts. 1 pt = {f.numero(pt, 2)} kWh."),
        ("Objetivo de salida", "Nivel con el que conviene salir: reserva + consumo previsto del día (o el nivel mínimo "
                               "de una excepción, si es mayor), sin pasar del tope."),
        ("Energía necesaria", "kWh que faltan para llegar al objetivo antes de la próxima salida."),
        ("Lectura", "Porcentaje que anotas mirando el coche. Es la referencia exacta; al guardarla, el consumo real del "
                    "intervalo se reparte entre sus trayectos (reconciliación)."),
        ("Trayecto", f"Periodo desconectado de al menos desconexion_min_s ({desconexion}). Las desconexiones más cortas "
                     "no consumen."),
        ("Mediana", f"Valor central del consumo real (reconciliado) de los trayectos de los últimos {ventana} días, sin "
                    "los de consumo indicado; se usa para estimar cada trayecto."),
        ("P75", f"Percentil {f.numero(percentil, 2)} del consumo diario real de los últimos {ventana} días: cubre 3 de "
                "cada 4 días con el valor por defecto. Es el consumo previsto para planificar."),
        ("Eficiencia de carga", "Pérdidas entre lo que mide el cargador y lo que entra en la batería (factor_bateria)."),
        ("Excepción", "Día que no sigue el horario. «Con salida»: sales a la hora indicada, con consumo normal (P75) o el "
                      "que indiques en km o %; vale también para un día que el horario marca sin salida; opcionalmente, "
                      "un nivel mínimo a la salida. «Sin salida»: un día que el horario marca con salida y no sales. "
                      "Una por fecha."),
        ("Carga externa", "Carga hecha fuera de casa. Se anota con kWh y coste (ambos opcionales) y se suma al nivel × "
                          "eficiencia. Sin kWh, el intervalo queda fuera de la estadística; al volver, anota una lectura."),
        ("evsm", "Estado del cargador: 1 libre · 4 conectado sin cargar · 6 cargando."),
        ("Recálculo", "Hora del último cálculo del modelo; se repite al cambiar datos (lecturas, cargas, excepciones, "
                      "horario, parámetros) y en cada cambio de estado del cargador."),
    ]


def _ejemplo(d: AyudaDatos) -> Html:
    pl, e = d.plan, d.estado
    pt = pl.capacidad_kwh / 100
    origen = "P75" if pl.origen == "historia" else "inicial"
    consumo_pts = pl.consumo_kwh / pt
    objetivo = min(pl.reserva_pct + consumo_pts, pl.tope_pct)
    lineas = [
        h('1 pt = capacidad útil {} kWh / 100 = <span class="valor">{} kWh</span>', f.numero(pl.capacidad_kwh),
          f.numero(pt, 3)),
        h("consumo previsto ({}) = {} → {} / {} = {} pts", origen, f.kwh(pl.consumo_kwh), f.numero(pl.consumo_kwh),
          f.numero(pt, 3), f.entero(consumo_pts)),
        h('objetivo de salida = reserva {} % + {} pts = <span class="azul">{}</span>', f.entero(pl.reserva_pct),
          f.entero(consumo_pts), f.pct(objetivo)),
    ]
    if e.get("nivel_pct") is not None:
        nivel = e["nivel_pct"]
        lineas.append(h('nivel actual = <span class="azul">{}</span>', f.pct(nivel)))
        falta = max(objetivo - nivel, Decimal(0))
        lineas.append(h('energía necesaria = ({} − {}) pts × {} = <span class="azul">{}</span>', f.entero(objetivo),
                        f.entero(nivel), f.numero(pt, 3), f.kwh(falta * pt)))
    else:
        lineas.append(Html("nivel actual = — (aún no hay ninguna lectura)"))
    lineas.append(h('<span class="tenue">con el tope en {}, un objetivo superior se recorta y se avisa</span>',
                    f.pct(pl.tope_pct)))
    return h('<div class="m calculo ejemplo">{}</div>', unir(lineas, "<br>"))


def _como(d: AyudaDatos) -> Html:
    cajas_e = (("Cargador", "kWh medidos, conexiones y desconexiones"), ("Tus lecturas", "% que marca el coche"),
               ("Horario + excepciones", "cuándo sales y cuánto gastarás"))
    cajas_s = (("Nivel", "con etiqueta LEÍDO / + CARGA / ESTIMADO"), ("Objetivo de salida", "reserva + consumo previsto"),
               ("Energía necesaria", "kWh hasta el objetivo"))

    def caja(titulo: str, texto: str) -> Html:
        return h('<div class="pn caja"><p class="m">{}</p><p class="k">{}</p></div>', titulo, texto)

    flecha = Html('<svg aria-hidden="true" class="flecha" width="28" height="28" viewBox="0 0 24 24" fill="none" '
                  'stroke="#58A6FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14">'
                  '</path><path d="m12 5 7 7-7 7"></path></svg>')
    modelo = Html('<div class="pn caja modelo"><p class="m">Modelo</p><p class="k">recalcular_bateria: trayectos, '
                  'reconciliación, mediana, P75, eficiencia de carga</p></div>')
    return h('<div class="relleno"><div class="diagrama"><div class="cajas">{}</div>{}{}{}<div class="cajas">{}</div>'
             '</div><p class="sub">Ejemplo con los valores vigentes:</p>{}</div>',
             unir(caja(*c) for c in cajas_e), flecha, modelo, flecha, unir(caja(*c) for c in cajas_s), _ejemplo(d))


def pantalla(d: AyudaDatos, q: str = "") -> Html:
    buscar = normalizar(q.strip())

    def coincide(*textos: str) -> bool:
        return not buscar or any(buscar in normalizar(t) for t in textos)

    bloques: list[tuple[str, str, Html, bool]] = []  # id, título, contenido, abierto en el móvil

    if coincide("Cómo funciona cargador lecturas horario excepciones nivel objetivo energía necesaria ejemplo "
                "capacidad reserva tope pts P75"):
        bloques.append(("como", "Cómo funciona", _como(d), True))
    estados = [h('<div class="it-estado"><span class="tag m {}">{}</span><p>{}</p></div>', c, t, x)
               for c, t, x in ESTADOS if coincide(t, x)]
    if estados:
        bloques.append(("estados", "Estados del nivel", h(
            '{}<p class="k texto relleno-x">Anotar el % al enchufar devuelve el nivel a LEÍDO. El consumo real del '
            'intervalo (lectura anterior + cargas en casa y externas × eficiencia − lectura nueva) se reparte entre sus '
            'trayectos en proporción a lo estimado (reconciliación); de esos consumos reales salen la mediana y el P75.'
            '</p>', unir(estados)), False))
    pantallas = [h('<tr><td class="nombre-pantalla">{}</td><td>{}</td></tr>', n, t) for n, t in PANTALLAS
                 if coincide(n, t)]
    if pantallas:
        bloques.append(("pantallas", "Pantallas", h('<table class="fija"><tbody>{}</tbody></table>', unir(pantallas)),
                        False))
    plan = [h('<div class="it-estado"><span class="tag m {}">{}</span><p>{}</p></div>', c, t, x)
            for c, t, x in PLAN_CARGA if coincide(t, x)]
    if plan:
        bloques.append(("plan", "Plan de carga", h(
            '{}<p class="k texto relleno-x">No edites desde la app de Orbis las franjas del planificador: la app las '
            'convierte en diarias y el planificador tendría que corregirlas.</p>', unir(plan)), False))
    filas = []
    # Mismos grupos que la pantalla Parámetros, con la pregunta de cada grupo como subtítulo (E3.9).
    for g in d.parametros.grupos:
        del_grupo = [h('<tr><td><p class="nombre">{}</p><p class="k m">{}</p></td><td class="r m">{}<span '
                       'class="detalle-celda">{}</span></td><td>{}</td><td>{}</td></tr>', p.nombre, p.clave,
                       valor(p.clave, p.unidad, p.valor), rango(p), p.descripcion, p.efecto)
                     for p in d.parametros.vigentes
                     if p.grupo == g.grupo and coincide(p.nombre, p.clave, p.descripcion, p.efecto, g.nombre)]
        if del_grupo:
            filas.append(h('<tr class="grupo-ayuda"><td colspan="4"><span class="pt">{} · {}</span> <span class="k">{}'
                           '</span></td></tr>', g.grupo, g.nombre, g.pregunta))
            filas.extend(del_grupo)
    if filas:
        bloques.append(("parametros", "Parámetros", h(
            '<div class="tabla-desplazable"><table class="fija parametros-ayuda"><thead><tr><th>Parámetro</th><th '
            'class="r">Vigente · rango</th><th>Qué es</th><th>Efecto al cambiarlo</th></tr></thead><tbody>{}</tbody>'
            '</table></div>', unir(filas)), True))
    glosario = [h('<div><dt class="m">{}</dt><dd>{}</dd></div>', t, x) for t, x in _glosario(d) if coincide(t, x)]
    if glosario:
        bloques.append(("glosario", "Glosario", h('<dl class="glosario">{}</dl>', unir(glosario)), False))

    indice = unir(h('<a href="#{}">{}</a>', i, t) for i, t, _, _ in bloques)
    resultado = Html("")
    if buscar:
        resultado = (h('<p class="k">Resultados para «{}» · <a href="/ayuda">ver toda la ayuda</a></p>', q.strip())
                     if bloques else h('<p class="vacio">Nada en la ayuda contiene «{}». <a href="/ayuda">Ver toda la '
                                       'ayuda</a></p>', q.strip()))
    # El buscador va dos veces (índice de escritorio y cabecera en móvil): un id distinto en cada una.
    busqueda = [h('<form class="buscar" action="/ayuda" method="get"><label for="{}" class="k">Buscar en la ayuda'
                  '</label><input id="{}" name="q" class="inp" type="search" placeholder="p. ej. P75" value="{}">'
                  '</form>', i, i, q.strip()) for i in ("ayuda-q", "ayuda-q-movil")]
    extra = {"parametros": Html('<a href="/parametros" class="k">Ir a Parámetros →</a>')}
    paneles = unir(h('<section id="{}" class="pn"><div class="ph"><h2 class="pt">{}</h2>{}</div>{}</section>', i, t,
                     extra.get(i, Html("")), c) for i, t, c, _ in bloques)
    detalles = unir(h('<details class="pn"{}><summary><span class="pt">{}</span></summary>{}</details>',
                      Html(" open") if abierto or buscar else Html(""), t, c) for _, t, c, abierto in bloques)
    return h('<div class="ayuda"><nav class="indice solo-escritorio" aria-label="En esta página"><p class="pt">En esta '
             'página</p>{}{}</nav><div class="col"><header class="cabecera-ayuda"><h1>Ayuda</h1><p class="sub">Qué '
             'calcula el formulario, qué significa cada pantalla y cómo afecta cada parámetro. Los valores mostrados son '
             'los vigentes.</p></header><div class="solo-movil">{}</div>{}'
             # Visibilidad en un contenedor propio: .paneles/.detalles (display: flex) anulaban .solo-*.
             '<div class="solo-escritorio"><div class="paneles">{}</div></div>'
             '<div class="solo-movil"><div class="detalles">{}</div></div></div></div>',
             indice, busqueda[0], busqueda[1], resultado, paneles, detalles)
