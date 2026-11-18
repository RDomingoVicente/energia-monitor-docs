"""Núcleo del formulario: una petición entra, una respuesta sale. Sin red ni hilos (los pone servidor.py).

- Solo se atienden peticiones con Host igual a FORMULARIO_HOST, salvo /salud (el
  healthcheck llega por 127.0.0.1).
- Un POST tiene que venir del mismo origen (Origin o, si no lo hay, Referer).
- Tras un POST correcto, 303 a la página con ?ok=<código>&id=<n>; el mensaje se
  monta desde la base de datos, nunca desde la URL.
- Errores de validación: 422 con el formulario relleno. Base de datos no
  accesible o migración pendiente: 503.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic as time_monotonic
from time import sleep as dormir
from decimal import Decimal, InvalidOperation
from importlib import resources
from urllib.parse import parse_qs, urlsplit

import psycopg

from energia_monitor import __version__, bateria_servicio, db, parametros
from energia_monitor.formulario import datos, plan_datos
from energia_monitor.formulario.html import Html
from energia_monitor.formulario import formato
from energia_monitor.formulario.pantallas import ayuda
from energia_monitor.formulario.pantallas import avisos as pantalla_avisos
from energia_monitor.formulario.pantallas import carga_externa, excepciones, historial, horario, inicio
from energia_monitor.formulario.pantallas import parametros as pantalla_parametros
from energia_monitor.formulario.pantallas import plan_carga as pantalla_plan
from energia_monitor.formulario.plantilla import Marco, banner, error_pagina, pagina
from energia_monitor.plan_carga import acciones as plan_acciones
from energia_monitor.tiempo import MADRID

log = logging.getLogger(__name__)

MAX_CUERPO = 16 * 1024
CSP = "default-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
SEGURIDAD = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
# Ficheros estáticos: lista cerrada (ruta → fichero del paquete y tipo). Los iconos van en la raíz porque los
# navegadores piden /favicon.ico y /apple-touch-icon.png por su cuenta.
ESTATICOS = {
    "/estatico/estilo.css": ("estatico/estilo.css", "text/css; charset=utf-8"),
    "/favicon.ico": ("estatico/favicon.ico", "image/x-icon"),
    "/favicon.svg": ("estatico/favicon.svg", "image/svg+xml"),
    "/apple-touch-icon.png": ("estatico/apple-touch-icon.png", "image/png"),
}


@dataclass(frozen=True)
class Peticion:
    metodo: str
    ruta: str  # con la consulta: /?ok=lectura&id=3
    cabeceras: Mapping[str, str]  # nombres en minúsculas
    cuerpo: bytes = b""

    @property
    def camino(self) -> str:
        return urlsplit(self.ruta).path

    @property
    def consulta(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.ruta).query)

    def formulario(self) -> dict[str, str]:
        valores = parse_qs(self.cuerpo.decode("utf-8", errors="replace"), keep_blank_values=True)
        return {k: v[0].strip() for k, v in valores.items()}


@dataclass
class Respuesta:
    estado: int
    cuerpo: bytes = b""
    tipo: str = "text/html; charset=utf-8"
    cabeceras: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigApp:
    host: str  # rdv-nas.tail1aac93.ts.net:8421
    grafana_url: str | None = None
    cargador: str = "cargador"  # etiqueta del chip de la cabecera


def _html(estado: int, contenido: Html) -> Respuesta:
    return Respuesta(estado, contenido.encode("utf-8"), cabeceras={"Cache-Control": "no-store"})


def _texto(estado: int, texto: str) -> Respuesta:
    return Respuesta(estado, texto.encode("utf-8"), "text/plain; charset=utf-8", {"Cache-Control": "no-store"})


class App:
    def __init__(self, config: ConfigApp, conectar: Callable[[], psycopg.Connection] = db.conectar,
                 reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
                 migracion_esperada: str | None = None, espera_accion_s: float = 4.0) -> None:
        self.config = config
        self.conectar = conectar
        self.reloj = reloj
        self.migracion_esperada = migracion_esperada or _ultima_migracion()
        # Tras encolar una acción del plan, cuánto se espera a que el muestreo la procese antes del PRG.
        self.espera_accion_s = espera_accion_s

    # --- Entrada -------------------------------------------------------------------------

    def manejar(self, p: Peticion) -> Respuesta:
        try:
            r = self._manejar(p)
        except Exception:
            log.exception("error al atender %s %s", p.metodo, p.camino)
            r = _texto(500, "Error interno; detalles en el log del contenedor.")
        r.cabeceras = {**SEGURIDAD, **r.cabeceras}
        return r

    def _manejar(self, p: Peticion) -> Respuesta:
        if p.camino == "/salud":
            return self.salud() if p.metodo in ("GET", "HEAD") else _texto(405, "Método no admitido")
        if p.cabeceras.get("host", "").lower() != self.config.host.lower():
            return _texto(421, "Host no admitido")
        if len(p.cuerpo) > MAX_CUERPO:
            return _texto(413, "Petición demasiado grande")
        if p.camino in ESTATICOS and p.metodo == "GET":
            fichero, tipo = ESTATICOS[p.camino]
            contenido = resources.files("energia_monitor.formulario").joinpath(fichero).read_bytes()
            return Respuesta(200, contenido, tipo, {"Cache-Control": "public, max-age=86400"})
        if p.metodo == "POST":
            if not self._mismo_origen(p):
                return _texto(403, "Origen no admitido")
            for patron, accion in self.rutas_post():
                m = re.fullmatch(patron, p.camino)
                if m:
                    return self._con_bd(lambda conn, marco: accion(conn, marco, p, *m.groups()))
            return _texto(404, "No encontrado")
        if p.metodo != "GET":
            return _texto(405, "Método no admitido")
        if p.camino == "/":
            return self._con_bd(lambda conn, marco: self.get_inicio(conn, marco, p))
        if p.camino == "/plan":
            return self._con_bd(lambda conn, marco: self.get_plan(conn, marco, p))
        if p.camino == "/excepciones":
            return self._con_bd(lambda conn, marco: self.get_excepciones(conn, marco, p))
        if p.camino == "/historial":
            return self._con_bd(lambda conn, marco: self.get_historial(conn, marco, p))
        if p.camino == "/carga-externa":
            return self._con_bd(lambda conn, marco: self.get_carga_externa(conn, marco, p))
        if p.camino == "/horario":
            return self._con_bd(lambda conn, marco: self.get_horario(conn, marco, p))
        if p.camino == "/parametros":
            return self._con_bd(lambda conn, marco: self.get_parametros(conn, marco, p))
        if p.camino == "/avisos":
            return self._con_bd(lambda conn, marco: _html(200, pagina(
                marco, "avisos", "avisos", pantalla_avisos.pantalla(datos.avisos(conn, self.reloj())), "Avisos")))
        if p.camino == "/ayuda":
            return self._con_bd(lambda conn, marco: self.get_ayuda(conn, marco, p))
        return _texto(404, "No encontrado")

    def rutas_post(self) -> list[tuple[str, Callable[..., Respuesta]]]:
        return [
            (r"/lectura", self.post_lectura),
            (r"/excepciones", self.post_excepcion),
            (r"/excepciones/(\d+)/modificar", self.post_excepcion),
            (r"/excepciones/(\d+)/baja", self.post_baja_excepcion),
            (r"/lecturas/(\d+)/corregir", self.post_corregir_lectura),
            (r"/lecturas/(\d+)/anular", self.post_anular_lectura),
            (r"/carga-externa", self.post_carga_externa),
            (r"/carga-externa/(\d+)/corregir", self.post_carga_externa),
            (r"/carga-externa/(\d+)/anular", self.post_anular_carga_externa),
            (r"/horario", self.post_horario),
            (r"/parametros", self.post_parametros),
            (r"/plan/modo", self.post_plan_modo),
            (r"/plan/cargar-ahora", self.post_plan_cargar_ahora),
            (r"/plan/franjas", self.post_plan_franja),
            (r"/plan/franjas/detener", self.post_plan_detener),
            (r"/plan/franjas/eliminar", self.post_plan_eliminar),
        ]

    def _mismo_origen(self, p: Peticion) -> bool:
        propio = f"https://{self.config.host}".lower()
        origen = p.cabeceras.get("origin")
        if origen:
            return origen.lower() == propio
        referer = p.cabeceras.get("referer", "").lower()
        return referer == propio or referer.startswith(propio + "/")

    def _con_bd(self, accion: Callable[[psycopg.Connection, Marco], Respuesta]) -> Respuesta:
        sin_bd = Marco(__version__, self.config.grafana_url, None)
        try:
            conn = self.conectar()
        except psycopg.OperationalError:
            log.warning("base de datos no accesible", exc_info=True)
            return _html(503, error_pagina(sin_bd, "Error", "Base de datos no accesible. Vuelve a intentarlo en unos "
                                                          "minutos; /salud muestra el estado."))
        with conn:
            version = datos.version_bd(conn)
            if version is None or version < self.migracion_esperada:
                return _html(503, error_pagina(
                    sin_bd, "Error", f"Migración pendiente: la base de datos está en la {version} y esta versión "
                                     f"necesita la {self.migracion_esperada}. Reinicia el colector, que la aplica."))
            ahora = self.reloj()
            marco = Marco(__version__, self.config.grafana_url, datos.cabecera(conn, ahora, self.config.cargador))
            return accion(conn, marco)

    # --- /salud ---------------------------------------------------------------------------

    def salud(self) -> Respuesta:
        cuerpo: dict[str, object] = {"version": __version__, "migracion_esperada": self.migracion_esperada}
        try:
            with self.conectar() as conn:
                cuerpo.update(datos.salud(conn, self.reloj()))
            cuerpo["bd"] = True
        except psycopg.Error as e:
            cuerpo.update(bd=False, error=type(e).__name__)
        ok = bool(cuerpo["bd"]) and (cuerpo.get("migracion") or "") >= self.migracion_esperada
        cuerpo["estado"] = "ok" if ok else "error"
        return Respuesta(200 if ok else 503, json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    # --- Plan de carga (7.9; acciones desde el paso 3c) ------------------------------------

    def get_plan(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                 form: pantalla_plan.FormFranja | None = None, fm: pantalla_plan.FormModo | None = None,
                 estado: int = 200) -> Respuesta:
        ahora = self.reloj()
        d = plan_datos.plan_carga(conn, ahora)
        ok = _ok(p)
        if ok is not None and ok[0] == "accion":
            d.ultima_accion = plan_datos.accion(conn, ok[1])
        if form is None:
            form = pantalla_plan.FormFranja()
            texto = p.consulta.get("modificar", [""])[0]
            fr = next((x for x in d.franjas if x.inicio.isoformat() == texto and x.ejecucion == "programada"), None)
            if fr is not None:
                li, lf = formato.local(fr.inicio), formato.local(fr.fin)
                form = pantalla_plan.FormFranja(f"{li:%Y-%m-%d}", f"{li:%H:%M}", f"{lf:%H:%M}", fr.inicio.isoformat(),
                                                fr.fin.isoformat())
        return _html(estado, pagina(marco, "plan", "plan", pantalla_plan.pantalla(d, form, fm), "Plan de carga"))

    def _encolar_plan(self, conn: psycopg.Connection, tipo: str, datos_: dict[str, object]) -> Respuesta:
        """Encola, confirma (el NOTIFY sale al confirmar) y espera un poco al muestreo antes del PRG."""
        ident = plan_acciones.encolar(conn, tipo, datos_)
        conn.commit()
        limite = time_monotonic() + self.espera_accion_s
        while time_monotonic() < limite:
            fila = conn.execute("SELECT estado FROM plan_accion WHERE id = %s", (ident,)).fetchone()
            conn.commit()
            if fila is None or fila[0] != "pendiente":
                break
            dormir(0.25)
        return _redirigir(f"/plan?ok=accion&id={ident}")

    def post_plan_modo(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        v = p.formulario()
        modo = v.get("modo", "")
        if modo not in plan_acciones.MODOS:
            return self.get_plan(conn, marco, p, fm=pantalla_plan.FormModo({"confirmar": "Elige un modo."}), estado=422)
        actual = conn.execute("SELECT planificador_modo_en(%s)", (self.reloj(),)).fetchone()[0]  # type: ignore[index]
        if modo == actual:
            return _redirigir("/plan")
        if modo == "activo" and v.get("confirmar") != "si":
            return self.get_plan(conn, marco, p, fm=pantalla_plan.FormModo(
                {"confirmar": "Para pasar a activo, marca la casilla: el planificador escribirá en el cargador."}),
                estado=422)
        return self._encolar_plan(conn, "modo", {"modo": modo, "nota": "formulario"})

    def _sombra(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta | None:
        """En sombra las acciones sobre franjas no se encolan (no se escribe nada en el cargador): 409."""
        modo = conn.execute("SELECT planificador_modo_en(%s)", (self.reloj(),)).fetchone()[0]  # type: ignore[index]
        if modo != "sombra":
            return None
        form = pantalla_plan.FormFranja(errores={"general": "En sombra no se escribe en el cargador. "
                                                            + pantalla_plan.EN_SOMBRA})
        return self.get_plan(conn, marco, p, form, estado=409)

    def post_plan_cargar_ahora(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        if (r := self._sombra(conn, marco, p)) is not None:
            return r
        return self._encolar_plan(conn, "cargar_ahora", {})

    def post_plan_franja(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        if (r := self._sombra(conn, marco, p)) is not None:
            return r
        v = p.formulario()
        form = pantalla_plan.FormFranja(v.get("dia", ""), v.get("inicio", ""), v.get("fin", ""),
                                        v.get("anterior_inicio", ""), v.get("anterior_fin", ""))
        franja = _franja_desde_form(form)
        if franja is None:
            return self.get_plan(conn, marco, p, form, estado=422)
        inicio, fin = franja
        anterior = None
        if form.anterior_inicio:
            try:
                anterior = (datetime.fromisoformat(form.anterior_inicio), datetime.fromisoformat(form.anterior_fin))
            except ValueError:
                form.errores["general"] = "La franja que se modifica no es válida; vuelve a abrirla desde la lista."
                return self.get_plan(conn, marco, p, form, estado=422)
        error = plan_acciones.validar_en_bd(conn, inicio, fin, self.reloj(), anterior)
        if error:
            form.errores["general"] = error
            return self.get_plan(conn, marco, p, form, estado=422)
        datos_: dict[str, object] = {"inicio": inicio.isoformat(), "fin": fin.isoformat()}
        if anterior is not None:
            datos_.update(anterior_inicio=anterior[0].isoformat(), anterior_fin=anterior[1].isoformat())
        return self._encolar_plan(conn, "modificar" if anterior else "anadir", datos_)

    def post_plan_detener(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        if (r := self._sombra(conn, marco, p)) is not None:
            return r
        inicio = _instante_form(p.formulario().get("inicio", ""))
        if inicio is None:
            return _texto(400, "Franja no válida")
        return self._encolar_plan(conn, "detener", {"inicio": inicio.isoformat()})

    def post_plan_eliminar(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        if (r := self._sombra(conn, marco, p)) is not None:
            return r
        v = p.formulario()
        inicio, fin = _instante_form(v.get("inicio", "")), _instante_form(v.get("fin", ""))
        if inicio is None or fin is None:
            return _texto(400, "Franja no válida")
        return self._encolar_plan(conn, "eliminar", {"inicio": inicio.isoformat(), "fin": fin.isoformat()})

    # --- Inicio ---------------------------------------------------------------------------

    def get_inicio(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                   form: inicio.FormLectura | None = None, estado: int = 200) -> Respuesta:
        d = datos.inicio(conn, self.reloj())
        d.modo_plan, d.proximas = plan_datos.proximas_cargas(conn, self.reloj())
        ok = _ok(p)
        if form is None and ok is not None and ok[0] == "lectura":
            d.lectura_guardada = datos.lectura_guardada(conn, ok[1])
        return _html(estado, pagina(marco, "inicio", "inicio", inicio.pantalla(d, form), "Inicio"))

    def post_lectura(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        v = p.formulario()
        form = inicio.FormLectura(v.get("pct", ""), v.get("fecha", ""), v.get("hora", ""))
        pct, instante = _pct_e_instante(form.pct, form.fecha, form.hora, form.errores)
        if form.errores or pct is None or instante is None:
            return self.get_inicio(conn, marco, p, form, 422)
        try:
            ident = bateria_servicio.registrar_lectura(conn, instante=instante, pct=pct, origen="formulario",
                                                       ahora=self.reloj())
        except bateria_servicio.ErrorLectura as e:
            form.errores["general"] = _frase(str(e))
            return self.get_inicio(conn, marco, p, form, 422)
        return _redirigir(f"/?ok=lectura&id={ident}")


    # --- Excepciones (7.2) ------------------------------------------------------------------

    def get_excepciones(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                        form: excepciones.FormExcepcion | None = None, estado: int = 200) -> Respuesta:
        d = datos.excepciones(conn, self.reloj())
        if form is None:
            form = excepciones.FormExcepcion()
            ident = p.consulta.get("modificar", [""])[0]
            x = datos.excepcion(conn, int(ident), activa=True) if ident.isdigit() and len(ident) < 12 else None
            if x is not None:
                form = excepciones.FormExcepcion(
                    x["tipo"], x["fecha"].isoformat(), x["hora_salida"].strftime("%H:%M") if x["hora_salida"] else "",
                    x["consumo_modo"] or "normal", _entero_texto(x["km"]), _entero_texto(x["pct"]),
                    _entero_texto(x["pct_objetivo"]), x["nota"] or "", x["id"])
        avisos = []
        ok = _ok(p) if estado == 200 else None
        if ok is not None:
            texto = self._mensaje_excepcion(conn, *ok)
            if texto:
                avisos.append(banner("ok", texto))
        for x in d.proximas:
            if x.recortado:
                avisos.append(banner("aviso", f"Objetivo por encima del tope: el del {formato.DIAS[x.fecha.weekday()]} "
                                              f"{x.fecha:%d/%m} se recorta al tope ({formato.pct(d.plan.tope_pct)})."))
        return _html(estado, pagina(marco, "excepciones", "excepciones",
                                    excepciones.pantalla(d, form, avisos), "Excepciones"))

    def _mensaje_excepcion(self, conn: psycopg.Connection, codigo: str, ident: int) -> str | None:
        if codigo == "excepcion":
            x = datos.excepcion(conn, ident, activa=True)
            if x is None:
                return None
            dia = f"{formato.DIAS[x['fecha'].weekday()]} {x['fecha']:%d/%m}"
            if x["tipo"] == "sin_salida":
                return f"Excepción guardada: {dia}, sin salida."
            return f"Excepción guardada: {dia}, con salida a las {x['hora_salida']:%H:%M}."
        if codigo == "baja":
            x = datos.excepcion(conn, ident, activa=False)
            if x is None:
                return None
            return f"Excepción del {formato.DIAS[x['fecha'].weekday()]} {x['fecha']:%d/%m} dada de baja."
        return None

    def post_excepcion(self, conn: psycopg.Connection, marco: Marco, p: Peticion, ident: str | None = None) -> Respuesta:
        v = p.formulario()
        form = excepciones.FormExcepcion(
            v.get("tipo", ""), v.get("fecha", ""), v.get("hora", ""), v.get("consumo", "normal"), v.get("km", ""),
            v.get("pct", ""), v.get("pct_objetivo", ""), v.get("nota", ""), int(ident) if ident else None)
        nueva = _excepcion_desde_form(form)
        if nueva is None:
            return self.get_excepciones(conn, marco, p, form, 422)
        try:
            if ident is None:
                nuevo = bateria_servicio.registrar_excepcion(conn, nueva, self.reloj())
            else:
                nuevo = bateria_servicio.modificar_excepcion(conn, int(ident), nueva, self.reloj())
        except bateria_servicio.ErrorExcepcion as e:
            form.errores[e.campo or "general"] = _frase(str(e))
            return self.get_excepciones(conn, marco, p, form, 422)
        return _redirigir(f"/excepciones?ok=excepcion&id={nuevo}")

    def post_baja_excepcion(self, conn: psycopg.Connection, marco: Marco, p: Peticion, ident: str) -> Respuesta:
        try:
            bateria_servicio.anular_excepcion(conn, int(ident), self.reloj())
        except bateria_servicio.ErrorExcepcion as e:
            form = excepciones.FormExcepcion(errores={"general": _frase(str(e))})
            return self.get_excepciones(conn, marco, p, form, 422)
        return _redirigir(f"/excepciones?ok=baja&id={ident}")


    # --- Historial (7.4) --------------------------------------------------------------------

    def get_historial(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                      corregir: historial.FormCorregir | None = None, estado: int = 200) -> Respuesta:
        c = p.consulta
        periodo = c.get("periodo", ["30"])[0]
        dias = None if periodo == "todo" else (90 if periodo == "90" else 30)
        d = datos.historial(conn, self.reloj(), dias, c.get("anuladas", [""])[0] == "1")
        avisos = []
        ok = _ok(p) if estado == 200 else None
        if ok is not None:
            lec = datos.lectura(conn, ok[1])
            if ok[0] == "corregida" and lec is not None and lec["anulada_en"] is None and lec["corrige"] is not None:
                avisos.append(banner("ok", f"Lectura corregida: {formato.pct(lec['pct'])} el "
                                           f"{formato.dia_hora(lec['instante'])}; la anterior queda anulada."))
            elif ok[0] == "anulada" and lec is not None and lec["anulada_en"] is not None:
                avisos.append(banner("ok", f"Lectura del {formato.dia_hora(lec['instante'])} anulada."))
        if corregir is None:
            for accion in ("corregir", "anular"):
                ident = c.get(accion, [""])[0]
                lec = datos.lectura(conn, int(ident)) if ident.isdigit() and len(ident) < 12 else None
                if lec is not None and lec["anulada_en"] is None:
                    local = formato.local(lec["instante"])
                    corregir = historial.FormCorregir(accion, lec, _entero_texto(lec["pct"]), f"{local:%Y-%m-%d}",
                                                      f"{local:%H:%M}", lec["nota"] or "")
        return _html(estado, pagina(marco, "historial", "historial", historial.pantalla(d, avisos, corregir),
                                    "Historial"))

    def post_corregir_lectura(self, conn: psycopg.Connection, marco: Marco, p: Peticion, ident: str) -> Respuesta:
        lec = datos.lectura(conn, int(ident))
        if lec is None or lec["anulada_en"] is not None:
            return _texto(404, "No hay ninguna lectura activa con ese id")
        v = p.formulario()
        form = historial.FormCorregir("corregir", lec, v.get("pct", ""), v.get("fecha", ""), v.get("hora", ""),
                                      v.get("nota", "")[:80])
        pct, instante = _pct_e_instante(form.pct, form.fecha, form.hora, form.errores)
        if form.errores or pct is None or instante is None:
            return self.get_historial(conn, marco, p, form, 422)
        try:
            nuevo = bateria_servicio.corregir_lectura(conn, lec["id"], instante=instante, pct=pct, origen="formulario",
                                                      nota=form.nota or None, ahora=self.reloj())
        except bateria_servicio.ErrorLectura as e:
            form.errores["general"] = _frase(str(e))
            return self.get_historial(conn, marco, p, form, 422)
        return _redirigir(f"/historial?ok=corregida&id={nuevo}")

    def post_anular_lectura(self, conn: psycopg.Connection, marco: Marco, p: Peticion, ident: str) -> Respuesta:
        lec = datos.lectura(conn, int(ident))
        if lec is None or lec["anulada_en"] is not None:
            return _texto(404, "No hay ninguna lectura activa con ese id")
        motivo = p.formulario().get("motivo", "")[:80]
        form = historial.FormCorregir("anular", lec, motivo=motivo)
        if not motivo:
            form.errores["motivo"] = "Indica el motivo."
            return self.get_historial(conn, marco, p, form, 422)
        bateria_servicio.anular_lectura(conn, lec["id"], motivo, self.reloj())
        return _redirigir(f"/historial?ok=anulada&id={ident}")


    # --- Carga externa (7.8) ----------------------------------------------------------------

    def get_carga_externa(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                          form: carga_externa.FormCarga | None = None, estado: int = 200) -> Respuesta:
        ahora = self.reloj()
        d = datos.cargas_externas(conn, ahora)
        c = p.consulta
        if form is None:
            local = formato.local(ahora)
            form = carga_externa.FormCarga(f"{local:%Y-%m-%d}", f"{local:%H:%M}")
            for accion in ("corregir", "anular"):
                ident = c.get(accion, [""])[0]
                x = datos.carga_externa(conn, int(ident)) if ident.isdigit() and len(ident) < 12 else None
                if x is not None and x["anulada_en"] is None:
                    lx = formato.local(x["instante"])
                    form = carga_externa.FormCarga(
                        f"{lx:%Y-%m-%d}", f"{lx:%H:%M}", _decimal_texto(x["kwh"]), _decimal_texto(x["coste_eur"]),
                        x["nota"] or "")
                    if accion == "corregir":
                        form.corregir = x["id"]
                    else:
                        form.anular = x["id"]
                        form.titulo_anular = formato.dia_hora(x["instante"])
        avisos = []
        ok = _ok(p) if estado == 200 else None
        if ok is not None:
            x = datos.carga_externa(conn, ok[1])
            if x is not None:
                energia = formato.kwh(x["kwh"]) if x["kwh"] is not None else "sin kWh"
                if ok[0] in ("externa", "externa_corregida") and x["anulada_en"] is None:
                    verbo = "corregida" if ok[0] == "externa_corregida" else "guardada"
                    avisos.append(banner("ok", f"Carga externa {verbo}: {energia} el {formato.dia_hora(x['instante'])}. "
                                               "Al volver, anota el % que marca el coche."))
                elif ok[0] == "externa_anulada" and x["anulada_en"] is not None:
                    avisos.append(banner("ok", f"Carga externa del {formato.dia_hora(x['instante'])} anulada."))
        return _html(estado, pagina(marco, "", "carga-externa", carga_externa.pantalla(d, form, avisos),
                                    "Carga externa"))

    def post_carga_externa(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                           ident: str | None = None) -> Respuesta:
        v = p.formulario()
        form = carga_externa.FormCarga(v.get("fecha", ""), v.get("hora", ""), v.get("kwh", ""), v.get("coste_eur", ""),
                                       v.get("nota", "")[:80], int(ident) if ident else None)
        if ident is not None:
            x = datos.carga_externa(conn, int(ident))
            if x is None or x["anulada_en"] is not None:
                return _texto(404, "No hay ninguna carga externa activa con ese id")
        kwh = coste = instante = None
        try:
            kwh = Decimal(form.kwh.replace(",", ".")) if form.kwh else None
            if kwh is not None and (kwh < 0 or kwh > 150 or kwh != kwh.quantize(Decimal("0.1"))):
                raise InvalidOperation
        except InvalidOperation:
            form.errores["kwh"] = "Indica la energía de 0 a 150 kWh, con un decimal como máximo, o déjala vacía."
        try:
            coste = Decimal(form.coste_eur.replace(",", ".")) if form.coste_eur else None
            if coste is not None and (coste < 0 or coste != coste.quantize(Decimal("0.01"))):
                raise InvalidOperation
        except InvalidOperation:
            form.errores["coste_eur"] = "Indica el coste en euros, con dos decimales como máximo, o déjalo vacío."
        if not form.fecha or not form.hora:
            form.errores["fecha" if not form.fecha else "hora"] = "Indica la fecha y la hora aproximada."
        else:
            try:
                instante = bateria_servicio.instante_local(f"{form.fecha} {form.hora}").instante
            except bateria_servicio.ErrorLectura as e:
                form.errores["hora"] = _frase(str(e))
        if form.errores or instante is None:
            return self.get_carga_externa(conn, marco, p, form, 422)
        nueva = bateria_servicio.NuevaCargaExterna(instante, kwh, coste, form.nota or None)
        try:
            if ident is None:
                nuevo = bateria_servicio.registrar_carga_externa(conn, nueva, "formulario", self.reloj())
            else:
                nuevo = bateria_servicio.corregir_carga_externa(conn, int(ident), nueva, "formulario", self.reloj())
        except bateria_servicio.ErrorCargaExterna as e:
            form.errores[e.campo or "general"] = _frase(str(e))
            return self.get_carga_externa(conn, marco, p, form, 422)
        codigo = "externa" if ident is None else "externa_corregida"
        return _redirigir(f"/carga-externa?ok={codigo}&id={nuevo}")

    def post_anular_carga_externa(self, conn: psycopg.Connection, marco: Marco, p: Peticion, ident: str) -> Respuesta:
        x = datos.carga_externa(conn, int(ident))
        if x is None or x["anulada_en"] is not None:
            return _texto(404, "No hay ninguna carga externa activa con ese id")
        motivo = p.formulario().get("motivo", "")[:80]
        if not motivo:
            form = carga_externa.FormCarga(anular=x["id"], titulo_anular=formato.dia_hora(x["instante"]),
                                           errores={"motivo": "Indica el motivo."})
            return self.get_carga_externa(conn, marco, p, form, 422)
        bateria_servicio.anular_carga_externa(conn, x["id"], motivo, self.reloj())
        return _redirigir(f"/carga-externa?ok=externa_anulada&id={ident}")


    # --- Horario (7.3) ---------------------------------------------------------------------

    def get_horario(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                    form: horario.FormHorario | None = None, estado: int = 200) -> Respuesta:
        d = datos.horario(conn, self.reloj())
        avisos = []
        if estado == 200 and p.consulta.get("ok") == ["horario"]:
            avisos.append(banner("ok", f"Horario guardado: {horario.resumen_semana(d.dias)}."))
        if form is not None and form.errores:
            avisos.append(banner("error", "Revisa las horas marcadas: el horario no se ha guardado."))
        return _html(estado, pagina(marco, "horario", "horario", horario.pantalla(d, form, avisos), "Horario"))

    def post_horario(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        v = p.formulario()
        form = horario.FormHorario()
        dias: dict[int, time | None] = {}
        for dia in range(1, 8):
            sale = v.get(f"sale_{dia}") == "1"
            texto = v.get(f"hora_{dia}", "")
            form.sale[dia], form.hora[dia] = sale, texto
            if not sale:
                dias[dia] = None  # la hora de un día sin salida se ignora
                continue
            try:
                dias[dia] = time.fromisoformat(texto) if texto else None
            except ValueError:
                dias[dia] = None
            if dias[dia] is None:
                form.errores[dia] = "Indica la hora de salida."
        if form.errores:
            return self.get_horario(conn, marco, p, form, 422)
        bateria_servicio.guardar_horario(conn, dias, self.reloj())
        return _redirigir("/horario?ok=horario")


    # --- Parámetros (7.5) ------------------------------------------------------------------

    def get_parametros(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                       form: pantalla_parametros.FormParametro | None = None, estado: int = 200) -> Respuesta:
        d = datos.parametros(conn, self.reloj())
        if form is None:
            clave = p.consulta.get("clave", [""])[0]
            form = pantalla_parametros.FormParametro(clave if any(x.clave == clave for x in d.vigentes)
                                                     else "reserva_pct")
        avisos = []
        ok = _ok(p) if estado == 200 else None
        if ok is not None and ok[0] == "parametro":
            reg = datos.parametro_registrado(conn, ok[1])
            if reg is not None:
                clave, valor, desde, unidad = reg
                avisos.append(banner("ok", f"{clave} = {pantalla_parametros.valor(clave, unidad, valor)} desde el "
                                           f"{desde:%d/%m/%Y}. La estimación se ha recalculado."))
        return _html(estado, pagina(marco, "parametros", "parametros",
                                    pantalla_parametros.pantalla(d, form, avisos), "Parámetros"))

    def post_parametros(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        v = p.formulario()
        form = pantalla_parametros.FormParametro(v.get("clave", ""), v.get("valor", ""), v.get("desde", ""),
                                                 v.get("nota", "")[:80])
        ahora = self.reloj()
        d = datos.parametros(conn, ahora)
        elegido = next((x for x in d.vigentes if x.clave == form.clave and x.editable), None)
        if elegido is None:
            form.clave = "reserva_pct"
            form.errores["clave"] = "Elige un parámetro de la lista."
            return self.get_parametros(conn, marco, p, form, 422)
        valor = None
        try:
            valor = pantalla_parametros.leer_valor(elegido, form.valor)
        except parametros.ErrorParametro:
            form.errores["valor"] = ("Indica la hora (HH:MM)." if elegido.unidad == parametros.UNIDAD_HORA
                                     else f"Indica un número ({pantalla_parametros.unidad_entrada(elegido)}).")
        hoy = formato.local(ahora).date()
        desde = None
        try:
            desde = date.fromisoformat(form.desde)
            if desde < hoy:
                form.errores["desde"] = "Desde la página, el valor nuevo vale desde hoy o después."
        except ValueError:
            form.errores["desde"] = "Indica la fecha de inicio."
        if form.errores or valor is None or desde is None:
            return self.get_parametros(conn, marco, p, form, 422)
        calibracion = form.clave == "factor_bateria" and form.nota.startswith("calibración sugerida")
        try:
            parametros.registrar_parametro_bateria(conn, clave=form.clave, desde=desde, valor=valor,
                                                   origen="estimado" if calibracion else "usuario",
                                                   fuente="formulario", nota=form.nota or None)
        except parametros.ErrorParametro as e:
            campo = "desde" if "empieza el" in str(e) else "valor"
            form.errores[campo] = _frase(str(e))
            return self.get_parametros(conn, marco, p, form, 422)
        bateria_servicio.recalcular(conn, ahora)
        ident = conn.execute("SELECT id FROM bateria_parametro WHERE clave = %s AND lower(vigencia) = %s",
                             (form.clave, desde)).fetchone()
        return _redirigir(f"/parametros?ok=parametro&id={ident[0] if ident else 0}")


    # --- Ayuda (7.6) -----------------------------------------------------------------------

    def get_ayuda(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        ahora = self.reloj()
        d = ayuda.AyudaDatos(datos.parametros(conn, ahora), datos.plan(conn, ahora), datos.estado(conn, ahora))
        q = p.consulta.get("q", [""])[0][:60]
        return _html(200, pagina(marco, "ayuda", "ayuda", ayuda.pantalla(d, q), "Ayuda"))


def _decimal_texto(x: Decimal | None) -> str:
    return "" if x is None else f"{x.normalize():f}"


def _pct_e_instante(pct_txt: str, fecha: str, hora: str, errores: dict[str, str]) -> tuple[Decimal | None,
                                                                                            datetime | None]:
    """Porcentaje entero 0–100 y fecha y hora de Madrid (la ambigua, la primera); errores por campo."""
    try:
        pct = _decimal(pct_txt, 0, 100)
        if pct is None:
            raise ValueError
    except ValueError:
        errores["pct"] = "Indica un porcentaje entero de 0 a 100."
        pct = None
    instante = None
    if not fecha or not hora:
        errores["fecha" if not fecha else "hora"] = "Indica la fecha y la hora de la lectura."
    else:
        try:
            instante = bateria_servicio.instante_local(f"{fecha} {hora}").instante
        except bateria_servicio.ErrorLectura as e:
            errores["hora"] = _frase(str(e))
    return pct, instante


def _instante_form(texto: str) -> datetime | None:
    try:
        x = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return x if x.tzinfo is not None else None


def _franja_desde_form(form: pantalla_plan.FormFranja) -> tuple[datetime, datetime] | None:
    """Día y horas locales (múltiplos de 15 min) a instantes UTC; un fin no posterior al inicio es del día
    siguiente. Rellena form.errores y devuelve None si algo no vale."""
    try:
        dia = date.fromisoformat(form.dia)
    except ValueError:
        form.errores["dia"] = "Fecha no válida."
    horas: dict[str, time] = {}
    for campo in ("inicio", "fin"):
        try:
            x = time.fromisoformat(getattr(form, campo))
        except ValueError:
            form.errores[campo] = "Hora no válida (HH:MM)."
            continue
        if x.minute % 15 or x.second:
            form.errores[campo] = "En cuartos de hora (:00, :15, :30, :45)."
        horas[campo] = x
    if form.errores:
        return None
    inicio = datetime.combine(dia, horas["inicio"], MADRID)
    fin = datetime.combine(dia, horas["fin"], MADRID)
    if fin <= inicio:
        fin = datetime.combine(dia + timedelta(days=1), horas["fin"], MADRID)
    return inicio.astimezone(UTC), fin.astimezone(UTC)


def _redirigir(destino: str) -> Respuesta:
    return Respuesta(303, cabeceras={"Location": destino, "Cache-Control": "no-store"})


def _entero_texto(x: Decimal | None) -> str:
    return "" if x is None else f"{x.normalize():f}"


def _frase(texto: str) -> str:
    texto = texto[:1].upper() + texto[1:]
    return texto if texto.endswith(".") else texto + "."


def _ultima_migracion() -> str:
    return max(f.name[:4] for f in db.ficheros_migracion(db.directorio_migraciones()))


def _ok(p: Peticion) -> tuple[str, int] | None:
    """(?ok=código&id=n) tras un POST: solo un código y un id; el texto se monta desde la base de datos."""
    c = p.consulta
    codigo, ident = c.get("ok", [""])[0], c.get("id", [""])[0]
    if re.fullmatch(r"[a-z_]{1,20}", codigo) and ident.isdigit() and len(ident) < 12:
        return codigo, int(ident)
    return None


def _decimal(texto: str, minimo: int, maximo: int | None) -> Decimal | None:
    """Entero positivo del formulario; ValueError si no lo es."""
    if texto == "":
        return None
    try:
        v = Decimal(texto)
    except InvalidOperation as e:
        raise ValueError from e
    if v != v.to_integral_value() or v < minimo or (maximo is not None and v > maximo):
        raise ValueError
    return v


def _excepcion_desde_form(form: excepciones.FormExcepcion) -> bateria_servicio.NuevaExcepcion | None:
    """Valida el formato de cada campo (los errores van a form.errores) y arma la excepción."""
    try:
        fecha = date.fromisoformat(form.fecha)
    except ValueError:
        form.errores["fecha"] = "Indica la fecha."
        fecha = None
    if form.nota and len(form.nota) > 80:
        form.errores["nota"] = "La nota admite 80 caracteres como máximo."
    if form.tipo == "sin_salida":
        if form.errores or fecha is None:
            return None
        return bateria_servicio.NuevaExcepcion("sin_salida", fecha, nota=form.nota or None)
    if form.tipo != "con_salida":
        form.errores["general"] = "Elige el tipo: con salida o sin salida."
        return None
    hora = None
    try:
        hora = time.fromisoformat(form.hora) if form.hora else None
    except ValueError:
        pass
    if hora is None:
        form.errores["hora"] = "Indica la hora de salida."
    km = pct = objetivo = None
    try:
        objetivo = _decimal(form.pct_objetivo, 1, 100)
    except ValueError:
        form.errores["pct_objetivo"] = "Indica un nivel entero de 1 a 100, o déjalo vacío."
    modo = "indicado" if form.consumo == "indicado" else "normal"
    if modo == "indicado":  # con consumo normal, km y % se ignoran (sin JavaScript se envían igual)
        try:
            km = _decimal(form.km, 1, None)
            pct = _decimal(form.pct, 1, 100)
        except ValueError:
            form.errores["consumo"] = "Indica los km o el % como un número entero positivo (el % hasta 100)."
        else:
            if (km is None) == (pct is None):
                form.errores["consumo"] = "Con «Indicar consumo», indica km o %, uno de los dos."
    if form.errores or fecha is None or hora is None:
        return None
    return bateria_servicio.NuevaExcepcion("con_salida", fecha, hora, modo, km, pct, objetivo, form.nota or None)
