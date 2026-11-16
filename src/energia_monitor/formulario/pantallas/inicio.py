"""Inicio (formulario-maquetas.md, 7.1; maquetas 01a, 01b y 01c)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import Inicio
from energia_monitor.formulario.html import Html, h, unir
from energia_monitor.formulario.plantilla import banner

ETIQUETA = {"leido": "LEÍDO", "leido_carga": "LEÍDO + CARGA", "estimado": "ESTIMADO"}
CIEN = Decimal(100)


@dataclass
class FormLectura:
    """Valores y errores del formulario de lectura (para el 422 con el formulario relleno)."""

    pct: str = ""
    fecha: str = ""
    hora: str = ""
    errores: dict[str, str] = field(default_factory=dict)


def _pct_de(kwh: Decimal | None, cap: Decimal) -> Decimal | None:
    return None if kwh is None else kwh / cap * CIEN


def _acotar(x: Decimal | None) -> int | None:
    return None if x is None else max(0, min(100, f.entero(x)))


def _texto_bateria(d: Inicio) -> tuple[str, str]:
    """(texto corto de la cabecera del panel, frase bajo la barra)."""
    e, cap = d.estado, d.estado["capacidad_kwh"]
    if e["nivel_kwh"] is None:
        return "sin nivel", "Aún no hay nivel: anota el % que marca el coche."
    ts, lec = e["ultima_lectura_ts"], e["ultima_lectura_pct"]
    cargados = e["kwh_cargados_desde_lectura"] or Decimal(0)
    n = e["trayectos_desde_lectura"] or 0
    if e["confianza"] == "leido" and ts is not None:
        corto = f"leído {f.cuando(ts, d.ahora)}"
        cuando = (f"hoy a las {f.hora(ts)}" if f.cuando(ts, d.ahora).startswith("hoy")
                  else f"el {f.dia(ts)} a las {f.hora(ts)}")
        return corto, f"Nivel leído por ti {cuando}. {_frase_objetivo(d)}"
    if lec is None or ts is None:
        # Sin lectura activa pero con nivel: tras una carga completa o con datos derivados sin recalcular.
        tipo = "carga completa" if e["ultimo_absoluto_tipo"] == "carga_completa" else "valor absoluto"
        return (f"{tipo} {f.corta(e['ultimo_absoluto'])}",
                f"Último valor absoluto: {tipo} el {f.dia(e['ultimo_absoluto'])} a las "
                f"{f.hora(e['ultimo_absoluto'])}. {_frase_objetivo(d)}")
    despues = [f"carga {f.kwh(cargados, signo=True)} a batería"] if cargados > 0 else []
    if n:
        trayectos = f"{n} trayecto{'s' if n != 1 else ''} estimado{'s' if n != 1 else ''}"
        consumo = f.kwh(-(e["kwh_trayectos_desde_lectura"] or 0))
        mediana = ", mediana" if d.origen_trayectos == {"estimado"} else ""
        despues.append(f"{trayectos} ({consumo}{mediana})")
    frase = f"Última lectura: {f.entero(lec)} % el {f.dia(ts)} a las {f.hora(ts)}."
    if despues:
        frase += " Después: " + " y ".join(despues) + "."
    if e["confianza"] == "leido_carga":
        corto = f"{f.entero(lec)} % leído {f.corta(ts)} {f.kwh(cargados, signo=True)} a batería"
    elif cargados > 0:
        base = lec + cargados / cap * CIEN
        corto = f"{f.entero(min(base, CIEN))} % tras carga {f.MENOS} {n} trayecto{'s' if n != 1 else ''}"
    else:
        corto = f"{f.entero(lec)} % leído {f.corta(ts)} {f.MENOS} {n} trayecto{'s' if n != 1 else ''}"
    return corto, f"{frase} {_frase_objetivo(d)}".strip()


def _frase_objetivo(d: Inicio) -> str:
    e, cap = d.estado, d.estado["capacidad_kwh"]
    if e["objetivo_kwh"] is None or e["consumo_plan_kwh"] is None:
        return ""
    origen = {"historia": "P75", "inicial": "consumo inicial", "declarado": "indicado"}.get(
        e["origen_consumo_plan"], e["origen_consumo_plan"])
    consumo_pct = e["consumo_plan_kwh"] / cap * CIEN
    sin_tope = d.reserva_pct + consumo_pct
    if d.pct_objetivo_salida is not None and d.pct_objetivo_salida > sin_tope:
        frase = f"Objetivo = mínimo {f.entero(d.pct_objetivo_salida)} % (excepción)"
        sin_tope = d.pct_objetivo_salida
    else:
        frase = (f"Objetivo = reserva {f.entero(d.reserva_pct)} % + consumo previsto {f.entero(consumo_pct)} % "
                 f"({f.numero(e['consumo_plan_kwh'], 0)} kWh, {origen})")
    if sin_tope > d.tope_pct:
        frase += f", recortado al tope {f.entero(d.tope_pct)} %"
    return frase + "."


def _barra(d: Inicio, movil: bool = False) -> Html:
    e, cap = d.estado, d.estado["capacidad_kwh"]
    nivel = _acotar(_pct_de(e["nivel_kwh"], cap))
    objetivo = _acotar(e["objetivo_pct"])
    reserva = _acotar(d.reserva_pct)
    partes: list[Html] = []
    if nivel is not None:
        leido = nivel
        if e["confianza"] == "leido_carga" and e["ultima_lectura_pct"] is not None:
            leido = min(_acotar(e["ultima_lectura_pct"]) or 0, nivel)
            if nivel > leido:
                partes.append(h('<div class="seg cargado x-{} w-{}"></div>', leido, nivel - leido))
        partes.insert(0, h('<div class="seg leido w-{}"></div>', leido))
        if objetivo is not None and objetivo > nivel:
            partes.append(h('<div class="seg falta x-{} w-{}"></div>', nivel, objetivo - nivel))
    partes.append(h('<div class="marca reserva x-{}"></div>', reserva))
    if objetivo is not None:
        partes.append(h('<div class="marca objetivo x-{}"></div>', objetivo))
    escala: list[Html] = []
    if not movil:
        escala += [Html('<span class="cero">0</span>'), Html('<span class="cien">100</span>')]
    escala.append(h('<span class="reserva x-{}">reserva {}</span>', reserva, reserva))
    if nivel is not None and not movil:
        escala.append(h('<span class="nivel x-{}">{}</span>', nivel, nivel))
    if objetivo is not None:
        escala.append(h('<span class="objetivo x-{}">objetivo {}</span>', objetivo, objetivo))
    return h('<div class="barra" aria-hidden="true">{}</div><div class="escala" aria-hidden="true">{}</div>',
             unir(partes), unir(escala))


def _coche(d: Inicio) -> tuple[str, str, str]:
    """(texto, clase del punto, detalle)."""
    e = d.estado
    c = d.conexion

    def cuando(t: datetime | None, prefijo: str) -> str:
        return f"{prefijo} {f.cuando(t, d.ahora).removeprefix('hoy ')}" if t else ""

    evsm = f"evsm {e['estado_conector']}" if e["estado_conector"] is not None else ""
    if e["coche"] == "fuera":
        return "Fuera", "aviso", f"{cuando(e['fuera_desde'], 'desde')} · nivel a la salida"
    if e["coche"] == "cargando":
        # Mientras carga, solo desde cuándo carga (sin «conectado desde»: la línea cabe en el móvil).
        potencia = f"{f.numero((d.potencia_w or 0) / 1000)} kW" if d.potencia_w else ""
        return "Cargando", "ok", " · ".join(x for x in (cuando(c.cargando_desde, "desde"), evsm, potencia) if x)
    if e["coche"] == "en_casa":
        if c.sin_cargar_desde:
            partes = (cuando(c.conectado_desde, "desde"), cuando(c.sin_cargar_desde, "sin cargar desde"), evsm)
        else:
            partes = (cuando(c.conectado_desde, "desde"), evsm, "sin carga")
        return "Conectado", "azul", " · ".join(x for x in partes if x)
    return "Desconocido", "gris", "sin estado del conector"


def _necesaria(d: Inicio) -> tuple[str, bool, str]:
    """(cifra, cubierto, detalle)."""
    e, cap = d.estado, d.estado["capacidad_kwh"]
    if e["proxima_salida"] is None:
        return "—", False, "ninguna salida en 7 días"
    if e["energia_necesaria_kwh"] is None:
        return "—", False, "sin nivel"
    if e["energia_necesaria_kwh"] > 0:
        faltan = e["energia_necesaria_kwh"] / cap * CIEN
        return f.kwh(e["energia_necesaria_kwh"]), False, f"objetivo {f.pct(e['objetivo_pct'])} · faltan {f.pts(faltan, False)}"
    margen = (e["nivel_plan_kwh"] or 0) - (e["objetivo_kwh"] or 0)
    return "cubierto", True, f"+{f.pts(margen / cap * CIEN, False)} de margen (≈{f.numero(margen, 0)} kWh)"


def _banners(d: Inicio) -> list[Html]:
    e = d.estado
    salida: list[Html] = []
    g = d.lectura_guardada
    if g is not None:
        texto = f"Lectura guardada: {f.entero(g.pct)} % a las {f.hora(g.instante)}."
        if g.estimado_pct is not None:
            dif = g.pct - g.estimado_pct
            texto = (f"Lectura guardada: {f.entero(g.pct)} % a las {f.hora(g.instante)}. El modelo estimaba "
                     f"{f.entero(g.estimado_pct)} % (diferencia {f.pts(dif)}); queda registrada en el historial.")
        else:
            texto += " Queda registrada en el historial."
        salida.append(banner("ok", texto))
    if e["nivel_kwh"] is None:
        salida.append(banner("aviso", "Aún no hay nivel: anota el % que marca el coche."))
    elif d.aviso_sin_lecturas and e["ultima_lectura_ts"] is not None:
        ts = e["ultima_lectura_ts"]
        dias = (d.ahora - ts).days
        n = e["trayectos_desde_lectura"] or 0
        salida.append(banner("aviso", f"Sin lecturas desde hace {dias} días ({f.dia_hora(ts)}) y {n} trayecto"
                                      f"{'s' if n != 1 else ''} estimado{'s' if n != 1 else ''} desde entonces. "
                                      "Anota el % al enchufar para afinar."))
    if d.intervalo_incoherente:
        salida.append(banner("aviso", "Hay un intervalo incoherente entre dos lecturas: revisa las lecturas.",
                             ("/historial", "Historial →")))
    return salida


def _form(d: Inicio, form: FormLectura) -> Html:
    def campo(nombre: str) -> tuple[Html, Html]:
        if nombre in form.errores:
            return (Html(f' aria-invalid="true" aria-describedby="error-{nombre}"'),
                    h('<p class="error-campo" id="error-{}">{}</p>', nombre, form.errores[nombre]))
        return Html(""), Html("")

    local = f.local(d.ahora)
    pct_attr, pct_err = campo("pct")
    fe_attr, fe_err = campo("fecha")
    ho_attr, ho_err = campo("hora")
    general = h('<p class="error-campo">{}</p>', form.errores["general"]) if "general" in form.errores else Html("")
    return h(
        '<form class="pn orden-4" id="lectura" action="/lectura" method="post"><div class="ph"><h2 class="pt">Anotar lectura</h2>'
        '</div><div class="relleno">{}<div class="campo"><label for="lectura-pct" class="k">Porcentaje que marca el '
        'coche</label><div class="con-unidad"><input id="lectura-pct" name="pct" class="inp grande" type="number" '
        'inputmode="numeric" min="0" max="100" step="1" required placeholder="0–100" value="{}"{}><span>%</span>'
        '</div>{}</div><div class="dos"><div class="campo"><label for="lectura-fecha" class="k">Fecha</label>'
        '<input id="lectura-fecha" name="fecha" class="inp" type="date" required value="{}"{}>{}</div>'
        '<div class="campo"><label for="lectura-hora" class="k">Hora</label><input id="lectura-hora" name="hora" '
        'class="inp" type="time" required value="{}"{}>{}</div></div><button type="submit" class="btn">Guardar '
        'lectura</button><a href="/carga-externa" class="enlace-centrado">¿Cargaste fuera de casa? Registrar carga '
        'externa →</a></div></form>',
        general, form.pct, pct_attr, pct_err, form.fecha or f"{local:%Y-%m-%d}", fe_attr, fe_err,
        form.hora or f"{local:%H:%M}", ho_attr, ho_err)


def _evento(ev) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    if ev.tipo == "lectura":
        return f"lectura {f.pct(ev.valor)}", "azul"
    # Cargas: primero la energía medida por el cargador y, entre paréntesis, la estimada a batería (0019).
    if ev.tipo == "carga":
        if ev.medido is None:
            return f"carga {f.kwh(ev.valor, signo=True)} a batería", "verde"
        return f"carga {f.kwh(ev.medido)} ({f.numero(ev.valor)} a batería)", "verde"
    if ev.tipo == "carga_externa":
        if ev.medido is None:
            return "carga externa sin kWh", "verde"
        return f"carga externa {f.kwh(ev.medido)} ({f.numero(ev.valor)} a batería)", "verde"
    if ev.tipo == "trayecto":
        # Partido por una lectura con parte reconciliada: el total y lo real (0018).
        real = (f" ({f.numero(-ev.real)} reales)" if ev.estimado and ev.real is not None and ev.valor is not None
                and ev.real != ev.valor else "")
        return f"trayecto{' est.' if ev.estimado else ''} {f.kwh(ev.valor)}{real}", ""
    if ev.tipo == "conexion":
        return "conexión", ""
    if ev.tipo == "inicio_carga":
        return "inicio carga (en curso)", "verde"
    return ev.tipo, ""


def pantalla(d: Inicio, form: FormLectura | None = None) -> Html:
    form = form or FormLectura()
    e, cap = d.estado, d.estado["capacidad_kwh"]
    sin_nivel = e["nivel_kwh"] is None
    etiqueta = h('<span class="tag m {}">{}</span>', e["confianza"], ETIQUETA[e["confianza"]])
    cifra = "—" if sin_nivel else f.pct(e["nivel_pct"])
    kwh_texto = "anota una lectura" if sin_nivel else f"{f.numero(e['nivel_kwh'])} / {f.numero(cap)} kWh"
    if e["coche"] == "fuera" and not sin_nivel:
        kwh_texto += " · nivel a la salida"
    coche, punto, coche_det = _coche(d)
    necesita, cubierto, necesita_det = _necesaria(d)
    if e["proxima_salida"] is not None:
        salida_txt = f.dia_hora(e["proxima_salida"])
        salida_det = "excepción" if e["tipo_salida"] == "con_salida" else "horario habitual"
    else:
        salida_txt, salida_det = "—", "ninguna en 7 días"
    corto, frase = _texto_bateria(d)

    resumen = h(
        '<section class="resumen solo-escritorio" aria-label="Resumen">'
        '<div class="pn indicador"><div class="fila"><p class="k">Nivel</p>{}</div><p class="big m nivel">{}</p>'
        '<p class="sub m">{}</p></div>'
        '<div class="pn indicador"><p class="k">Estado del coche</p><p class="big coche"><span class="dot grande {}">'
        '</span>{}</p><p class="sub m">{}</p></div>'
        '<div class="pn indicador"><p class="k">Próxima salida</p><p class="big m salida">{}</p><p class="sub">{}</p></div>'
        '<div class="pn indicador {}"><p class="k">Energía necesaria</p><p class="big m necesaria{}">{}</p>'
        '<p class="sub m">{}</p></div></section>',
        etiqueta, cifra, kwh_texto, punto, coche, coche_det, salida_txt, salida_det,
        "cubierto" if cubierto else "destacado", " cubierto" if cubierto else "", necesita, necesita_det)

    bateria = h('<section class="pn"><div class="ph"><h2 class="pt">Batería</h2><span class="k m">{}</span></div>'
                '<div class="bateria-cuerpo">{}<p class="sub">{}</p></div></section>', corto, _barra(d), frase)

    filas = []
    for dia in d.dias:
        if dia.salida is None:
            filas.append(h('<tr><td>{}</td><td class="tenue">—</td><td><span class="tag {}">{}</span></td>'
                           '<td class="r tenue">0,0 kWh</td><td class="r tenue">—</td></tr>',
                           f"{f.DIAS[dia.fecha.weekday()]} {dia.fecha:%d/%m}", dia.clase, dia.origen))
        else:
            filas.append(h('<tr><td>{}</td><td>{}</td><td><span class="tag {}">{}</span></td><td class="r">{}</td>'
                           '<td class="r">{}</td></tr>', f.dia(dia.salida), f.hora(dia.salida), dia.clase, dia.origen,
                           f.kwh(dia.consumo_kwh), f.pct(dia.objetivo_pct)))
    dias7 = h('<section class="pn"><div class="ph"><h2 class="pt">Próximos 7 días</h2><a href="/excepciones" '
              'class="k">Excepciones →</a></div><table><thead><tr><th>Fecha</th><th>Salida</th><th>Origen</th>'
              '<th class="r">Consumo prev.</th><th class="r">Objetivo</th></tr></thead><tbody class="m">{}</tbody>'
              '</table></section>', unir(filas))

    eventos_filas = [h('<tr><td class="tenue">{}</td><td class="{}">{}</td></tr>', f.corta(ev.instante), clase, texto)
                     for ev in d.eventos for texto, clase in [_evento(ev)]]
    eventos = h('<section class="pn eventos solo-escritorio"><div class="ph"><h2 class="pt">Últimos eventos</h2></div>'
                '{}</section>',
                h('<table><tbody>{}</tbody></table>', unir(eventos_filas)) if eventos_filas
                else Html('<p class="vacio">Aún no hay eventos.</p>'))

    bateria_movil = h(
        '<section class="pn bateria-movil solo-movil orden-2"><div class="ph"><h2 class="pt">Batería</h2>{}</div>'
        '<div class="relleno"><div class="cifra"><span class="pct">{}</span><span class="kwh">{}</span></div>'
        '{}<p class="detalle">{}</p></div></section>', etiqueta, cifra, kwh_texto, _barra(d, movil=True), corto)
    coche_movil = h(
        '<section class="coche-necesita solo-movil orden-3"><div class="pn"><p class="k">Coche</p><p class="valor">'
        '<span class="dot {}"></span>{}</p><p class="detalle">{}</p></div><div class="pn{}"><p class="k">Necesita</p>'
        '<p class="valor kwh{}">{}</p><p class="detalle">{}</p></div></section>',
        punto, coche, coche_det.split(" · ")[0], " cubierto" if cubierto else " destacado",
        " cubierto" if cubierto else "", necesita, salida_txt)
    proximos = [h('<div class="it"><span>{}</span><span>{}</span></div>', f.dia_hora(dia.salida),
                  f.pct(dia.objetivo_pct)) for dia in d.dias if dia.salida is not None][:3]
    dias_movil = h('<section class="pn solo-movil orden-5"><div class="ph"><h2 class="pt">Próximos días</h2>'
                   '<a href="/excepciones" class="k">7 días →</a></div>{}</section>',
                   h('<div class="lista">{}</div>', unir(proximos)) if proximos
                   else Html('<p class="vacio">Ninguna salida en 7 días.</p>'))
    banners = _banners(d)
    zona_banners = h('<div class="banners orden-1">{}</div>', unir(banners)) if banners else Html("")
    # Escritorio: dos columnas. Móvil: .col pasa a display: contents y los bloques se ordenan con orden-N
    # (banner, batería, coche y necesita, lectura, próximos días); los de solo-escritorio se ocultan.
    return h('{}<div class="columnas"><div class="col">{}{}{}{}{}</div><div class="col">{}{}{}</div></div>',
             resumen, h('<div class="solo-escritorio">{}</div>', bateria),
             h('<div class="solo-escritorio">{}</div>', dias7), bateria_movil, coche_movil, dias_movil,
             zona_banners, _form(d, form), eventos)


__all__ = ["FormLectura", "pantalla"]
