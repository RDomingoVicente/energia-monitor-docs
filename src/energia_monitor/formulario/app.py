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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
from urllib.parse import parse_qs, urlsplit

import psycopg

from energia_monitor import __version__, bateria_servicio, db
from energia_monitor.formulario import datos
from energia_monitor.formulario.html import Html
from energia_monitor.formulario.pantallas import inicio
from energia_monitor.formulario.plantilla import Marco, en_preparacion, error_pagina, pagina

log = logging.getLogger(__name__)

MAX_CUERPO = 16 * 1024
CSP = "default-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
SEGURIDAD = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
# Pantallas de la v0.5.5 (formulario-maquetas.md, 7.2 a 7.8).
EN_PREPARACION = {"/excepciones": ("excepciones", "Excepciones"), "/horario": ("horario", "Horario"),
                  "/historial": ("historial", "Historial"), "/parametros": ("parametros", "Parámetros"),
                  "/ayuda": ("ayuda", "Ayuda"), "/avisos": ("avisos", "Avisos"),
                  "/carga-externa": ("", "Carga externa")}


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
                 migracion_esperada: str | None = None) -> None:
        self.config = config
        self.conectar = conectar
        self.reloj = reloj
        self.migracion_esperada = migracion_esperada or _ultima_migracion()

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
        if p.camino == "/estatico/estilo.css" and p.metodo == "GET":
            css = resources.files("energia_monitor.formulario").joinpath("estatico/estilo.css").read_bytes()
            return Respuesta(200, css, "text/css; charset=utf-8", {"Cache-Control": "public, max-age=86400"})
        if p.metodo == "POST":
            if not self._mismo_origen(p):
                return _texto(403, "Origen no admitido")
            if p.camino == "/lectura":
                return self._con_bd(lambda conn, marco: self.post_lectura(conn, marco, p))
            return _texto(404, "No encontrado")
        if p.metodo != "GET":
            return _texto(405, "Método no admitido")
        if p.camino == "/":
            return self._con_bd(lambda conn, marco: self.get_inicio(conn, marco, p))
        if p.camino in EN_PREPARACION:
            activa, titulo = EN_PREPARACION[p.camino]
            return self._con_bd(lambda conn, marco: _html(200, pagina(
                marco, activa, p.camino.strip("/"), en_preparacion(titulo), titulo)))
        return _texto(404, "No encontrado")

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

    # --- Inicio ---------------------------------------------------------------------------

    def get_inicio(self, conn: psycopg.Connection, marco: Marco, p: Peticion,
                   form: inicio.FormLectura | None = None, estado: int = 200) -> Respuesta:
        d = datos.inicio(conn, self.reloj())
        consulta = p.consulta
        ident = consulta.get("id", [""])[0]
        if form is None and consulta.get("ok") == ["lectura"] and ident.isdigit():
            d.lectura_guardada = datos.lectura_guardada(conn, int(ident))
        return _html(estado, pagina(marco, "inicio", "inicio", inicio.pantalla(d, form), "Inicio"))

    def post_lectura(self, conn: psycopg.Connection, marco: Marco, p: Peticion) -> Respuesta:
        v = p.formulario()
        form = inicio.FormLectura(v.get("pct", ""), v.get("fecha", ""), v.get("hora", ""))
        pct: Decimal | None = None
        try:
            pct = Decimal(form.pct)
            if pct != pct.to_integral_value() or not 0 <= pct <= 100:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            form.errores["pct"] = "Indica un porcentaje entero de 0 a 100."
        instante = None
        if not form.fecha or not form.hora:
            form.errores["fecha" if not form.fecha else "hora"] = "Indica la fecha y la hora de la lectura."
        else:
            try:
                instante = bateria_servicio.instante_local(f"{form.fecha} {form.hora}").instante
            except bateria_servicio.ErrorLectura as e:
                form.errores["hora"] = _frase(str(e))
        if form.errores or pct is None or instante is None:
            return self.get_inicio(conn, marco, p, form, 422)
        try:
            ident = bateria_servicio.registrar_lectura(conn, instante=instante, pct=pct, origen="formulario",
                                                       ahora=self.reloj())
        except bateria_servicio.ErrorLectura as e:
            form.errores["general"] = _frase(str(e))
            return self.get_inicio(conn, marco, p, form, 422)
        return Respuesta(303, cabeceras={"Location": f"/?ok=lectura&id={ident}", "Cache-Control": "no-store"})


def _frase(texto: str) -> str:
    texto = texto[:1].upper() + texto[1:]
    return texto if texto.endswith(".") else texto + "."


def _ultima_migracion() -> str:
    return max(f.name[:4] for f in db.ficheros_migracion(db.directorio_migraciones()))
