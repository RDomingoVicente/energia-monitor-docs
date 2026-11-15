"""Horario (formulario-maquetas.md, 7.3; maqueta 03). Sin vigencia: los cambios se aplican desde que se guardan."""

from __future__ import annotations

from dataclasses import dataclass, field

from energia_monitor.formulario import formato as f
from energia_monitor.formulario.datos import HorarioDatos
from energia_monitor.formulario.html import Html, h, unir

NOMBRES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
LETRAS = ("L", "M", "X", "J", "V", "S", "D")


@dataclass
class FormHorario:
    sale: dict[int, bool] = field(default_factory=dict)
    hora: dict[int, str] = field(default_factory=dict)
    errores: dict[int, str] = field(default_factory=dict)


def resumen_semana(dias: dict[int, object]) -> str:
    """«L–J 09:30 · V 07:00»: días seguidos con la misma hora, agrupados."""
    grupos: list[tuple[int, int, str]] = []
    for d in range(1, 8):
        hora = dias.get(d)
        if hora is None:
            continue
        texto = f"{hora:%H:%M}"  # type: ignore[str-format]
        if grupos and grupos[-1][1] == d - 1 and grupos[-1][2] == texto:
            grupos[-1] = (grupos[-1][0], d, texto)
        else:
            grupos.append((d, d, texto))
    if not grupos:
        return "sin salidas"
    partes = []
    for ini, fin, texto in grupos:
        dias_txt = LETRAS[ini - 1] if ini == fin else (f"{LETRAS[ini - 1]}–{LETRAS[fin - 1]}" if fin - ini > 1
                                                       else f"{LETRAS[ini - 1]} {LETRAS[fin - 1]}")
        partes.append(f"{dias_txt} {texto}")
    return " · ".join(partes)


def pantalla(d: HorarioDatos, form: FormHorario | None, avisos: list[Html]) -> Html:
    p = d.plan
    consumo_pct = p.consumo_kwh / p.capacidad_kwh * 100
    objetivo = f.pct(min(p.reserva_pct + consumo_pct, p.tope_pct))
    filas = []
    for dia in range(1, 8):
        nombre = NOMBRES[dia - 1]
        if form is not None:
            sale, hora = form.sale.get(dia, False), form.hora.get(dia, "")
        else:
            sale = d.dias[dia] is not None
            hora = f"{d.dias[dia]:%H:%M}" if sale else ""
        error = form.errores.get(dia) if form else None
        atributos = h(' aria-invalid="true" aria-describedby="error-h-{}"', dia) if error else Html("")
        error_html = h('<p class="error-campo" id="error-h-{}">{}</p>', dia, error) if error else Html("")
        consumo = f.kwh(p.consumo_kwh) if sale else "0,0 kWh"
        filas.append(h(
            '<tr><td class="dia">{}</td><td><label class="sw"><input type="checkbox" name="sale_{}" value="1"{} '
            'aria-label="Sale el {}"><span></span></label></td><td class="hora"><input class="inp" type="time" '
            'name="hora_{}" value="{}" aria-label="Hora de salida del {}"{}>{}</td><td class="r m solo-escritorio{}">{}'
            '</td><td class="r m solo-escritorio{}">{}</td></tr>',
            nombre, dia, Html(" checked") if sale else Html(""), nombre.lower(), dia, hora, nombre.lower(), atributos,
            error_html, "" if sale else " tenue", consumo, "" if sale else " tenue", objetivo if sale else "—"))
    modificado = f"Última modificación: {f.local(d.modificado):%d/%m/%Y %H:%M}." if d.modificado else \
        "Aún sin guardar."
    salidas = sum(1 for v in d.dias.values() if v is not None)
    total = p.consumo_kwh * salidas
    origen = "P75" if p.origen == "historia" else "consumo inicial"
    zona = h('<div class="banners orden-0">{}</div>', unir(avisos)) if avisos else Html("")
    return h(
        '{}<div class="dos-columnas horario-columnas"><form class="pn orden-1" action="/horario" method="post">'
        '<div class="ph"><h2 class="pt">Horario habitual · primera salida del día</h2><span class="k m solo-escritorio">'
        'las excepciones mandan sobre este horario</span></div><table class="horario"><thead class="solo-escritorio">'
        '<tr><th>Día</th><th>Sale</th><th>Hora</th><th class="r">Consumo prev. ({})</th><th class="r">Objetivo</th></tr>'
        '</thead><tbody>{}</tbody></table><div class="relleno pie-horario"><p class="k">Los cambios se aplican desde que '
        'se guardan. {}</p><button type="submit" class="btn">Guardar horario</button></div></form>'
        '<section class="pn orden-2"><div class="ph"><h2 class="pt">Semana tipo</h2></div><div class="relleno">'
        '<p class="m vigente">{}</p><p class="big m">{} salida{}</p><p class="sub m">{} kWh/semana previstos · {} de '
        'batería</p><p class="sub">Calculado con el consumo {} por día de salida ({} kWh).</p></div></section></div>',
        zona, origen, unir(filas), modificado, resumen_semana(d.dias), salidas, "" if salidas == 1 else "s",
        f.numero(total), f.pct(total / p.capacidad_kwh * 100), origen, f.numero(p.consumo_kwh, 0))
