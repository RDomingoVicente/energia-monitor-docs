"""Vigilancia de los avisos de correo (paso 3d; deploy/vigilancia.sh la ejecuta desde DSM cada 15 minutos).

Lee los avisos con correo de v_aviso (rol de solo lectura) y los compara con el estado guardado de la pasada
anterior. Solo «avisa» cuando cambia algo:

- un aviso nuevo se notifica en la primera pasada en que aparece;
- un aviso se da por resuelto cuando falta en dos pasadas seguidas (histéresis: un parpadeo no manda dos correos);
- un ERROR que sigue activo más de 24 h se recuerda una vez al día;
- si la base de datos no responde, eso es el aviso «Base de datos no accesible», y los avisos anteriores se
  conservan tal cual (no se sabe si siguen: no se dan por resueltos).

Si hay algo que contar, lo imprime y termina con 1; si no, termina con 0 sin salida. DSM («enviar detalles de
ejecución por correo → solo si termina de forma anómala») manda la salida con el SMTP ya configurado.

La salida no lleva credenciales ni datos personales: títulos, detalles y fechas de los avisos y el enlace a la
pantalla del formulario. Los errores de conexión se resumen sin el mensaje de libpq (lleva host y usuario).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from energia_monitor.tiempo import MADRID

ESTADO_POR_DEFECTO = Path("/estado/vigilancia.json")
RECORDAR_CADA = timedelta(hours=24)
AUSENCIAS_PARA_RESOLVER = 2
CLAVE_BD = "bd_no_accesible"
PANTALLAS = {"plan": "/plan", "lectura": "/#lectura", "historial": "/historial", "carga_externa": "/carga-externa",
             "excepciones": "/excepciones", "parametros": "/parametros"}

CONSULTA = """
SELECT a.clave, a.nivel, a.titulo, coalesce(a.detalle, ''), coalesce(a.desde, h.inicio), a.accion
FROM v_aviso a
LEFT JOIN aviso_historial h ON h.clave = a.clave AND h.fin IS NULL
WHERE a.correo
ORDER BY a.clave
"""
CONSULTA_CICLO = "SELECT ultimo_ok_en FROM planificador_ciclo"


class BdNoAccesible(Exception):
    """No se pudo consultar la base de datos (sin el detalle de libpq)."""


@dataclass(frozen=True)
class Aviso:
    clave: str
    nivel: str
    titulo: str
    detalle: str
    desde: datetime | None
    accion: str | None


@dataclass(frozen=True)
class Lectura:
    avisos: dict[str, Aviso]
    ultimo_ciclo: datetime | None


@dataclass(frozen=True)
class Seguido:
    """Un aviso en el estado: cuándo se notificó, cuándo se recordó y en cuántas pasadas seguidas ha faltado."""

    aviso: Aviso
    notificado: datetime
    recordado: datetime | None = None
    ausencias: int = 0


@dataclass
class Cambios:
    nuevos: list[Aviso] = field(default_factory=list)
    recordados: list[Aviso] = field(default_factory=list)
    resueltos: list[Aviso] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.nuevos or self.recordados or self.resueltos)


# --- Lógica (pura) -----------------------------------------------------------------------------


def comparar(estado: Mapping[str, Seguido], lectura: Lectura | None,
             ahora: datetime) -> tuple[dict[str, Seguido], Cambios]:
    """Nuevo estado y cambios a notificar. lectura None: la base de datos no responde."""
    cambios = Cambios()
    nuevo: dict[str, Seguido] = {}
    if lectura is None:
        # Se conservan los anteriores sin contar ausencias; el único cambio posible es el aviso de la base.
        nuevo.update({c: s for c, s in estado.items() if c != CLAVE_BD})
        bd = estado.get(CLAVE_BD)
        actuales = {CLAVE_BD: bd.aviso if bd else aviso_bd(ahora)}
    else:
        actuales = dict(lectura.avisos)
    for clave, a in actuales.items():
        previo = estado.get(clave)
        if previo is None:
            a = replace(a, desde=a.desde or ahora)
            cambios.nuevos.append(a)
            nuevo[clave] = Seguido(a, ahora)
            continue
        seguido = replace(previo, aviso=replace(a, desde=a.desde or previo.aviso.desde), ausencias=0)
        if a.nivel == "error" and _toca_recordar(seguido, ahora):
            cambios.recordados.append(seguido.aviso)
            seguido = replace(seguido, recordado=ahora)
        nuevo[clave] = seguido
    if lectura is not None:
        for clave, previo in estado.items():
            if clave in actuales:
                continue
            ausencias = previo.ausencias + 1
            if ausencias >= AUSENCIAS_PARA_RESOLVER:
                cambios.resueltos.append(previo.aviso)
            else:
                nuevo[clave] = replace(previo, ausencias=ausencias)
    return nuevo, cambios


def _toca_recordar(s: Seguido, ahora: datetime) -> bool:
    desde = s.aviso.desde or s.notificado
    ultimo = s.recordado or s.notificado
    return ahora - desde >= RECORDAR_CADA and ahora - ultimo >= RECORDAR_CADA


def aviso_bd(ahora: datetime) -> Aviso:
    return Aviso(CLAVE_BD, "error", "Base de datos no accesible",
                 "La vigilancia no puede consultar la base de datos: ¿contenedor db parado o NAS sin recursos?",
                 ahora, None)


# --- Texto del correo --------------------------------------------------------------------------


def _hora(t: datetime | None) -> str:
    return t.astimezone(MADRID).strftime("%d/%m %H:%M") if t else "?"


def _enlace(a: Aviso, url_base: str | None) -> str | None:
    if not url_base:
        return None
    return url_base.rstrip("/") + PANTALLAS.get(a.accion or "", "/avisos")


def informe(cambios: Cambios, ahora: datetime, ultimo_ciclo: datetime | None, url_base: str | None) -> str:
    lineas = [f"energia-monitor · vigilancia · {ahora.astimezone(MADRID):%d/%m/%Y %H:%M}"]

    def bloque(titulo: str, avisos: list[Aviso], marca: Callable[[Aviso], str], con_detalle: bool = True) -> None:
        if not avisos:
            return
        lineas.extend(["", f"{titulo} ({len(avisos)}):"])
        for a in avisos:
            lineas.append(f"- {a.nivel.upper()} · {a.titulo} · {marca(a)}")
            if con_detalle and a.detalle:
                lineas.append(f"  {a.detalle}")
            enlace = _enlace(a, url_base)
            if con_detalle and enlace:
                lineas.append(f"  {enlace}")

    bloque("Nuevos", cambios.nuevos, lambda a: f"desde {_hora(a.desde)}")
    bloque("Siguen activos", cambios.recordados, lambda a: f"sigue activo desde {_hora(a.desde)}")
    bloque("Resueltos", cambios.resueltos, lambda a: f"desde {_hora(a.desde)}, resuelto hacia {_hora(ahora)}",
           con_detalle=False)
    if ultimo_ciclo is not None:
        lineas.extend(["", f"Último ciclo correcto del planificador: {_hora(ultimo_ciclo)}."])
    return "\n".join(lineas)


def informe_prueba(ahora: datetime, url_base: str | None) -> str:
    lineas = [f"energia-monitor · vigilancia · {ahora.astimezone(MADRID):%d/%m/%Y %H:%M}", "",
              "Prueba: si te llega este correo, los avisos por correo funcionan. No hay que hacer nada."]
    if url_base:
        lineas.append(url_base.rstrip("/") + "/avisos")
    return "\n".join(lineas)


# --- Estado en disco -----------------------------------------------------------------------------


def _fecha(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def cargar_estado(ruta: Path) -> dict[str, Seguido]:
    """Estado de la pasada anterior. Sin fichero, vacío. Ilegible: ValueError."""
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        salida = {}
        for clave, s in datos["avisos"].items():
            a = s["aviso"]
            aviso = Aviso(a["clave"], a["nivel"], a["titulo"], a["detalle"], _fecha(a["desde"]), a["accion"])
            salida[clave] = Seguido(aviso, _fecha(s["notificado"]) or datetime.now(UTC), _fecha(s["recordado"]),
                                    int(s["ausencias"]))
        return salida
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"estado ilegible ({type(e).__name__})") from e


def guardar_estado(ruta: Path, estado: Mapping[str, Seguido]) -> None:
    def fecha(v: Any) -> Any:
        return v.isoformat() if isinstance(v, datetime) else v

    datos = {"version": 1, "avisos": {
        c: {"aviso": {k: fecha(v) for k, v in asdict(s.aviso).items()}, "notificado": fecha(s.notificado),
            "recordado": fecha(s.recordado), "ausencias": s.ausencias} for c, s in estado.items()}}
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ruta)


# --- Base de datos -------------------------------------------------------------------------------


def leer(conectar: Callable[[], psycopg.Connection] | None = None) -> Lectura:
    """Avisos con correo de v_aviso y último ciclo del planificador. BdNoAccesible si no se puede."""
    try:
        with (conectar() if conectar else psycopg.connect(connect_timeout=10)) as conn:
            avisos = {f[0]: Aviso(*f) for f in conn.execute(CONSULTA)}
            fila = conn.execute(CONSULTA_CICLO).fetchone()
    except psycopg.Error as e:
        raise BdNoAccesible(type(e).__name__) from None
    return Lectura(avisos, fila[0] if fila else None)


# --- Pasada --------------------------------------------------------------------------------------


def pasada(ruta: Path, url_base: str | None, ahora: datetime | None = None,
           conectar: Callable[[], psycopg.Connection] | None = None) -> tuple[int, str]:
    """(código de salida, texto). 1 y el informe si hay cambios; 0 y nada si no."""
    ahora = ahora or datetime.now(UTC)
    aviso_estado = None
    try:
        estado = cargar_estado(ruta)
    except ValueError as e:
        estado, aviso_estado = {}, f"Estado anterior {e}: se empieza de cero."
    try:
        lectura: Lectura | None = leer(conectar)
    except BdNoAccesible:
        lectura = None
    nuevo, cambios = comparar(estado, lectura, ahora)
    guardar_estado(ruta, nuevo)
    if not cambios and aviso_estado is None:
        return 0, ""
    texto = informe(cambios, ahora, lectura.ultimo_ciclo if lectura else None, url_base)
    if aviso_estado:
        texto += "\n\n" + aviso_estado
    return 1, texto


def main(estado: Path | None, prueba: bool) -> int:
    url_base = os.environ.get("VIGILANCIA_URL_BASE") or None
    if prueba:
        print(informe_prueba(datetime.now(UTC), url_base))
        return 1
    ruta = estado or Path(os.environ.get("VIGILANCIA_ESTADO") or ESTADO_POR_DEFECTO)
    codigo, texto = pasada(ruta, url_base)
    if texto:
        print(texto)
    return codigo
