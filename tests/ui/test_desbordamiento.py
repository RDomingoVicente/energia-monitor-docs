"""Ninguna pantalla se sale de su contenedor en 1280×900 ni en 390×844 (formulario-maquetas.md, 4 y 9).

Solo en local, con Playwright (tests/ui/Dockerfile); fuera de la CI. El servidor
HTTPS real con el certificado de pruebas y los datos de datos_falsos.py, sin
base de datos.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright solo en tests/ui/Dockerfile")

import datos_falsos  # noqa: E402

from energia_monitor.formulario.app import App, ConfigApp  # noqa: E402
from energia_monitor.formulario.servidor import crear_servidor  # noqa: E402

pytestmark = pytest.mark.red_local

TLS = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
PUERTO = 18443
PANTALLAS = ["/", "/excepciones", "/excepciones?modificar=2#formulario", "/excepciones#pasadas", "/historial",
             "/historial?corregir=9#corregir", "/historial?anuladas=1", "/carga-externa", "/carga-externa?anular=3",
             "/horario", "/parametros", "/parametros?clave=desconexion_min_s", "/avisos", "/ayuda", "/ayuda?q=P75"]
TAMANOS = [(1280, 900), (390, 844)]

# Elementos que salen de su padre más de 1 px. Se ignoran los ocultos, lo decorativo (aria-hidden: barra
# de batería) y lo que va dentro de un contenedor con desplazamiento (tablas en .tabla-desplazable).
DESBORDADOS = """() => {
  const malos = [];
  const raiz = document.documentElement;
  if (raiz.scrollWidth > window.innerWidth + 1) malos.push(`página: ancho ${raiz.scrollWidth} > ${window.innerWidth}`);
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('[aria-hidden="true"], .oculto, details:not([open]) > :not(summary)')) continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.position === 'fixed') continue;
    const p = el.parentElement;
    if (!p) continue;
    let desplazable = false;
    for (let a = p; a; a = a.parentElement) {
      if (['auto', 'scroll'].includes(getComputedStyle(a).overflowX)) { desplazable = true; break; }
    }
    if (desplazable) continue;
    const r = el.getBoundingClientRect(), rp = p.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || rp.width === 0) continue;
    if (r.right > rp.right + 1 || r.left < rp.left - 1) {
      const id = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().replace(/\\s+/g, '.') : '');
      malos.push(`${id} «${(el.textContent || '').trim().slice(0, 40)}»: ${Math.round(r.left)}–${Math.round(r.right)} fuera de ${Math.round(rp.left)}–${Math.round(rp.right)}`);
    }
  }
  return malos;
}"""


@pytest.fixture(scope="module")
def servidor() -> Iterator[str]:
    mp = pytest.MonkeyPatch()
    datos_falsos.instalar(mp)
    certs = Path(__file__).resolve().parent / ".certs"
    certs.mkdir(exist_ok=True)
    (certs / "server.crt").write_bytes((TLS / "valido.crt").read_bytes())
    (certs / "server.key").write_bytes((TLS / "servidor.key").read_bytes())
    app = App(ConfigApp(f"127.0.0.1:{PUERTO}", "https://grafana.ejemplo/d/energia-bateria", "viaris 192.168.1.34"),
              datos_falsos.ConexionFalsa, lambda: datos_falsos.AHORA, migracion_esperada="0000")  # type: ignore[arg-type]
    srv = crear_servidor(app, certs, PUERTO, "127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{PUERTO}"
    srv.shutdown()
    srv.server_close()
    mp.undo()


@pytest.fixture(scope="module")
def navegador() -> Iterator[object]:
    with sync_api.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.mark.parametrize(("ancho", "alto"), TAMANOS, ids=["escritorio", "movil"])
@pytest.mark.parametrize("ruta", PANTALLAS)
def test_nada_se_sale_de_su_contenedor(servidor: str, navegador: object, ruta: str, ancho: int, alto: int) -> None:
    ctx = navegador.new_context(viewport={"width": ancho, "height": alto}, ignore_https_errors=True)  # type: ignore[attr-defined]
    pagina = ctx.new_page()
    errores: list[str] = []
    pagina.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)
    pagina.on("requestfailed", lambda r: errores.append(f"falló {r.url}"))
    respuesta = pagina.goto(servidor + ruta)
    assert respuesta is not None and respuesta.status == 200
    assert errores == [], errores  # CSP, recursos que no cargan...
    malos = pagina.evaluate(DESBORDADOS)
    ctx.close()
    assert malos == [], "\n".join(malos)


def test_el_detector_encuentra_un_desbordamiento(servidor: str, navegador: object) -> None:
    """Control: un elemento más ancho que su panel aparece en la lista."""
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, ignore_https_errors=True)  # type: ignore[attr-defined]
    pagina = ctx.new_page()
    pagina.goto(servidor + "/")
    # El primer .pn .ph visible (en móvil el panel «Batería» de escritorio está oculto, ancho 0). .ph es
    # flex: sin flex: none el span se encogería hasta el ancho de su texto y no desbordaría.
    pagina.evaluate("""() => { const p = [...document.querySelectorAll('.pn .ph')].find(e => e.offsetWidth > 0);
        const x = document.createElement('span'); x.className = 'prueba'; x.style.display = 'inline-block';
        x.style.flex = 'none'; x.style.width = '2000px'; x.textContent = 'ancho'; p.appendChild(x); }""")
    malos = pagina.evaluate(DESBORDADOS)
    ctx.close()
    assert any("span.prueba" in m for m in malos), malos
    assert any(m.startswith("página: ancho") for m in malos), malos


# Lo marcado para el otro tamaño no debe verse (una regla posterior con display: flex anulaba .solo-movil en la
# Ayuda y la página salía dos veces), y ningún id se repite.
VISIBILIDAD = """(oculta) => {
  const malos = [];
  for (const el of document.querySelectorAll('.' + oculta)) {
    const r = el.getBoundingClientRect();
    if (getComputedStyle(el).display !== 'none' && (r.width > 0 || r.height > 0))
      malos.push(`${el.tagName.toLowerCase()}.${el.className.trim().replace(/\\s+/g, '.')} visible`);
  }
  const vistos = new Set();
  for (const el of document.querySelectorAll('[id]')) {
    if (vistos.has(el.id)) malos.push(`id repetido: ${el.id}`);
    vistos.add(el.id);
  }
  return malos;
}"""


@pytest.mark.parametrize(("ancho", "alto"), TAMANOS, ids=["escritorio", "movil"])
@pytest.mark.parametrize("ruta", PANTALLAS)
def test_solo_se_ve_lo_de_cada_tamano(servidor: str, navegador: object, ruta: str, ancho: int, alto: int) -> None:
    ctx = navegador.new_context(viewport={"width": ancho, "height": alto}, ignore_https_errors=True)  # type: ignore[attr-defined]
    pagina = ctx.new_page()
    pagina.goto(servidor + ruta)
    malos = pagina.evaluate(VISIBILIDAD, "solo-movil" if ancho >= 900 else "solo-escritorio")
    ctx.close()
    assert malos == [], "\n".join(malos)
