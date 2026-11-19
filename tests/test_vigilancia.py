"""Vigilancia de avisos por correo (paso 3d): comparación con el estado, histéresis, recordatorio diario, texto del
correo y estado en disco. Sin base de datos (la de verdad, en tests/integration/test_avisos_3d_db.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from energia_monitor import vigilancia as v

T0 = datetime(2026, 11, 19, 9, tzinfo=UTC)  # 10:00 en Madrid
PARADO = v.Aviso("planificador_parado", "error", "Planificador parado", "Último ciclo correcto: 19/11 08:55.",
                 T0 - timedelta(hours=1), "plan")
SALIDA = v.Aviso("salida_no_cubierta:1795000000", "aviso", "El plan no cubre una salida", "Salida del 20/11 09:00.",
                 T0, "plan")


def lectura(*avisos: v.Aviso, ciclo: datetime | None = None) -> v.Lectura:
    return v.Lectura({a.clave: a for a in avisos}, ciclo)


def pasar(estado: dict[str, v.Seguido], lec: v.Lectura | None, minutos: int) -> tuple[dict[str, v.Seguido], v.Cambios]:
    return v.comparar(estado, lec, T0 + timedelta(minutes=minutos))


def test_nuevo_se_notifica_en_la_primera_pasada_y_luego_nada() -> None:
    e, c = pasar({}, lectura(PARADO, SALIDA), 0)
    assert [a.clave for a in c.nuevos] == ["planificador_parado", SALIDA.clave] and not c.resueltos
    e, c = pasar(e, lectura(PARADO, SALIDA), 15)
    assert not c


def test_resuelto_solo_tras_dos_pasadas_sin_el() -> None:
    e, _ = pasar({}, lectura(PARADO, SALIDA), 0)
    e, c = pasar(e, lectura(SALIDA), 15)
    assert not c and e["planificador_parado"].ausencias == 1
    e, c = pasar(e, lectura(SALIDA), 30)
    assert [a.clave for a in c.resueltos] == ["planificador_parado"] and "planificador_parado" not in e


def test_un_parpadeo_no_manda_nada() -> None:
    e, _ = pasar({}, lectura(PARADO), 0)
    e, c = pasar(e, lectura(), 15)
    assert not c
    e, c = pasar(e, lectura(PARADO), 30)
    assert not c and e["planificador_parado"].ausencias == 0
    e, c = pasar(e, lectura(), 45)
    assert not c  # vuelve a contar desde cero


def test_base_inaccesible_conserva_los_anteriores() -> None:
    e, _ = pasar({}, lectura(PARADO, SALIDA), 0)
    e, c = pasar(e, lectura(SALIDA), 15)  # el planificador falta una vez
    e, c = pasar(e, None, 30)
    assert [a.clave for a in c.nuevos] == ["bd_no_accesible"] and not c.resueltos
    assert e["planificador_parado"].ausencias == 1 and SALIDA.clave in e
    e, c = pasar(e, None, 45)
    assert not c
    e, c = pasar(e, lectura(SALIDA), 60)  # vuelve: bd falta una vez; el planificador, dos
    assert [a.clave for a in c.resueltos] == ["planificador_parado"]
    e, c = pasar(e, lectura(SALIDA), 75)
    assert [a.clave for a in c.resueltos] == ["bd_no_accesible"] and set(e) == {SALIDA.clave}


def test_un_error_activo_se_recuerda_una_vez_al_dia() -> None:
    e, _ = pasar({}, lectura(PARADO, SALIDA), 0)
    for m in range(15, 24 * 60, 15):
        e, c = pasar(e, lectura(PARADO, SALIDA), m)
        assert not c, m
    e, c = pasar(e, lectura(PARADO, SALIDA), 24 * 60)
    assert [a.clave for a in c.recordados] == ["planificador_parado"]  # el AVISO no se recuerda
    e, c = pasar(e, lectura(PARADO, SALIDA), 24 * 60 + 15)
    assert not c
    e, c = pasar(e, lectura(PARADO, SALIDA), 48 * 60)
    assert [a.clave for a in c.recordados] == ["planificador_parado"]


def test_informe() -> None:
    e, c = pasar({}, lectura(PARADO), 0)
    texto = v.informe(c, T0, T0 - timedelta(minutes=65), "https://nas.ejemplo:8421/")
    assert texto.splitlines()[0] == "energia-monitor · vigilancia · 19/11/2026 10:00"
    assert "Nuevos (1):\n- ERROR · Planificador parado · desde 19/11 09:00\n  Último ciclo correcto: 19/11 08:55.\n" \
           "  https://nas.ejemplo:8421/plan" in texto
    assert texto.endswith("Último ciclo correcto del planificador: 19/11 08:55.")
    recordado = v.Cambios(recordados=[PARADO])
    assert "- ERROR · Planificador parado · sigue activo desde 19/11 09:00" in v.informe(recordado, T0, None, None)
    resuelto = v.informe(v.Cambios(resueltos=[PARADO]), T0, None, None)
    assert "Resueltos (1):\n- ERROR · Planificador parado · desde 19/11 09:00, resuelto hacia 19/11 10:00" in resuelto
    assert "https://" not in resuelto
    sin_accion = v.informe(v.Cambios(nuevos=[v.aviso_bd(T0)]), T0, None, "https://nas.ejemplo:8421")
    assert "https://nas.ejemplo:8421/avisos" in sin_accion


def test_estado_en_disco(tmp_path: Path) -> None:
    ruta = tmp_path / "vigilancia.json"
    assert v.cargar_estado(ruta) == {}
    e, _ = pasar({}, lectura(PARADO, SALIDA), 0)
    e, _ = pasar(e, lectura(SALIDA), 15)
    v.guardar_estado(ruta, e)
    assert v.cargar_estado(ruta) == e
    ruta.write_text("{roto")
    with pytest.raises(ValueError, match="estado ilegible"):
        v.cargar_estado(ruta)


def _sin_bd() -> psycopg.Connection:
    raise psycopg.OperationalError("connection to server at \"db\" failed: password authentication failed for user "
                                   "\"grafana_ro\" (password=secreto)")


def test_pasada_sin_base_y_sin_secretos(tmp_path: Path) -> None:
    ruta = tmp_path / "vigilancia.json"
    codigo, texto = v.pasada(ruta, "https://nas.ejemplo:8421", T0, _sin_bd)
    assert codigo == 1 and "Base de datos no accesible" in texto
    assert "secreto" not in texto and "grafana_ro" not in texto and '"db"' not in texto
    assert v.pasada(ruta, "https://nas.ejemplo:8421", T0 + timedelta(minutes=15), _sin_bd) == (0, "")


def test_estado_ilegible_se_avisa_y_se_rehace(tmp_path: Path) -> None:
    ruta = tmp_path / "vigilancia.json"
    ruta.write_text("{roto")
    codigo, texto = v.pasada(ruta, None, T0, _sin_bd)
    assert codigo == 1 and "estado ilegible" in texto
    assert set(v.cargar_estado(ruta)) == {"bd_no_accesible"}


def test_prueba_no_toca_el_estado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("VIGILANCIA_URL_BASE", "https://nas.ejemplo:8421")
    ruta = tmp_path / "vigilancia.json"
    assert v.main(ruta, prueba=True) == 1
    salida = capsys.readouterr().out
    assert salida.startswith("energia-monitor · vigilancia") and "Prueba" in salida
    assert "https://nas.ejemplo:8421/avisos" in salida
    assert not ruta.exists()
