"""Estimación de SSAA para los tramos sin valor real de ESIOS.

Orden de preferencia (lo aplica v_precio_final):
  1. Real de ESIOS (v_ssaa_real).
  2. prevision_ree: previsión de SSAA del PVPC (indicador de uso
     'ssaa_prevision', 1871) × (real / previsión de los últimos 28 días con
     ambos valores). Solo si la previsión está verificada: REE no corrige lo
     publicado la víspera (ver esios.estado_prevision).
  3. perfil_14d: media por hora local de los 14 últimos días con valor real,
     solo los del mismo tipo (laborable, o fin de semana y festivo).
  4. El parámetro estimado de contrato_parametro.

Contraste con los datos de 2026 (269 días, del 15/02 al 10/11, con la
información disponible a las 20:30 del día anterior): MAE horario de 3,38
€/MWh con prevision_ree y de 5,35 con el perfil de 14 días.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg

from energia_monitor import esios
from energia_monitor.tiempo import MADRID, limites_dia_utc

log = logging.getLogger(__name__)

VENTANA_PERFIL_DIAS = 14
VENTANA_FACTOR_DIAS = 28
MIN_DIAS_FACTOR = 7
DIAS_ATRAS = 10
CUATRO_DECIMALES = Decimal("0.0001")
UNA_HORA = timedelta(hours=1)


@dataclass(frozen=True)
class Estimacion:
    inicio: datetime
    fin: datetime
    valor: Decimal
    metodo: str


def es_laborable(dia: date, festivos: Collection[date]) -> bool:
    return dia.weekday() < 5 and dia not in festivos


def dias_completos(real_por_hora: Mapping[datetime, Decimal]) -> dict[date, dict[int, list[Decimal]]]:
    """Valores reales por día y hora local, solo de días con todas sus horas.

    En el día de 25 h la hora local 2 aparece dos veces: se guardan ambas.
    """
    por_dia: dict[date, dict[int, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    horas: Counter[date] = Counter()
    for hora_utc, valor in real_por_hora.items():
        local = hora_utc.astimezone(MADRID)
        por_dia[local.date()][local.hour].append(valor)
        horas[local.date()] += 1
    completos = {}
    for dia, n in horas.items():
        a, b = limites_dia_utc(dia)
        if n * UNA_HORA == b - a:
            completos[dia] = dict(por_dia[dia])
    return completos


def perfil(dias: Mapping[date, Mapping[int, Sequence[Decimal]]], objetivo: date,
           festivos: Collection[date], ventana: int = VENTANA_PERFIL_DIAS) -> dict[int, Decimal]:
    """Media por hora local de los últimos `ventana` días anteriores a objetivo."""
    anteriores = [d for d in dias if d < objetivo]
    if not anteriores:
        return {}
    ultimo = max(anteriores)
    en_ventana = [d for d in anteriores if d > ultimo - timedelta(days=ventana)]
    mismo_tipo = [d for d in en_ventana if es_laborable(d, festivos) == es_laborable(objetivo, festivos)]
    elegidos = mismo_tipo or en_ventana
    resultado = {}
    for hora in range(24):
        valores = [v for d in elegidos for v in dias[d].get(hora, ())]
        if valores:
            resultado[hora] = sum(valores, Decimal(0)) / len(valores)
    return resultado


def factor_prevision(real_por_hora: Mapping[datetime, Decimal], prevision_por_hora: Mapping[datetime, Decimal],
                     ventana: int = VENTANA_FACTOR_DIAS, min_dias: int = MIN_DIAS_FACTOR) -> Decimal | None:
    """Relación real / previsión en los últimos `ventana` días con ambos valores."""
    comunes = [h for h in real_por_hora if h in prevision_por_hora]
    if not comunes:
        return None
    ultimo = max(h.astimezone(MADRID).date() for h in comunes)
    desde = ultimo - timedelta(days=ventana - 1)
    horas = [h for h in comunes if h.astimezone(MADRID).date() >= desde]
    if len({h.astimezone(MADRID).date() for h in horas}) < min_dias:
        return None
    real = sum((real_por_hora[h] for h in horas), Decimal(0))
    prevision = sum((prevision_por_hora[h] for h in horas), Decimal(0))
    if prevision <= 0:
        return None
    return real / prevision


def calcular(tramos: Sequence[tuple[datetime, datetime]], real_por_hora: Mapping[datetime, Decimal],
             prevision_por_hora: Mapping[datetime, Decimal], festivos: Collection[date],
             usar_prevision: bool) -> list[Estimacion]:
    """Estimación de cada tramo, o nada si no hay datos para estimarlo."""
    dias = dias_completos(real_por_hora)
    factor = factor_prevision(real_por_hora, prevision_por_hora) if usar_prevision else None
    perfiles: dict[date, dict[int, Decimal]] = {}
    salida = []
    for inicio, fin in tramos:
        # Madrid tiene desfases de horas enteras: la hora UTC es la hora local.
        hora = inicio.replace(minute=0, second=0, microsecond=0)
        if factor is not None and hora in prevision_por_hora:
            valor, metodo = factor * prevision_por_hora[hora], "prevision_ree"
        else:
            local = inicio.astimezone(MADRID)
            if local.date() not in perfiles:
                perfiles[local.date()] = perfil(dias, local.date(), festivos)
            media = perfiles[local.date()].get(local.hour)
            if media is None:
                continue
            valor, metodo = media, "perfil_14d"
        salida.append(Estimacion(inicio, fin, valor.quantize(CUATRO_DECIMALES), metodo))
    return salida


# --- Base de datos -----------------------------------------------------------

SQL_REAL_POR_HORA = """
SELECT date_trunc('hour', inicio, 'UTC') AS hora, avg(ssaa_eur_mwh)
FROM v_ssaa_real
WHERE contrato = %s AND inicio >= %s AND inicio < %s
GROUP BY 1
HAVING sum(fin - inicio) = interval '1 hour'
"""

SQL_PREVISION = """
SELECT s.inicio, s.valor
FROM esios_indicador i
JOIN serie_esios s ON s.indicador = i.id AND s.geo_id = i.geo_id
WHERE i.uso = 'ssaa_prevision' AND s.inicio >= %s AND s.inicio < %s
  AND s.fin - s.inicio = interval '1 hour'
"""

SQL_TRAMOS = """
SELECT DISTINCT ON (inicio) inicio, fin
FROM precio_mercado
WHERE inicio >= %s AND inicio < %s
ORDER BY inicio, fin
"""

UPSERT_ESTIMACION = """
INSERT INTO ssaa_estimacion (contrato, inicio, fin, valor, metodo, calculado_en)
VALUES (%s, %s, %s, %s, %s, now())
ON CONFLICT (contrato, inicio) DO UPDATE SET
    fin = EXCLUDED.fin, valor = EXCLUDED.valor, metodo = EXCLUDED.metodo,
    calculado_en = EXCLUDED.calculado_en
"""


def actualizar(conn: psycopg.Connection, hoy: date, dias_atras: int = DIAS_ATRAS) -> dict[str, Counter[str]]:
    """Recalcula la estimación de los tramos de mercado de hoy - dias_atras a mañana sin SSAA real."""
    estado = esios.estado_prevision(conn)
    usar_prevision = estado.estado == "verificada"
    if estado.estado == "corregida":
        log.warning("SSAA: REE ha corregido %d valor(es) de la previsión 1871 publicada la víspera;"
                    " se estima con el perfil de 14 días", estado.corregidos)
    elif estado.estado == "pendiente":
        log.info("SSAA: previsión 1871 pendiente de verificar (%d/%d días comprobados);"
                 " se estima con el perfil de 14 días", estado.dias_verificados, esios.DIAS_VERIFICACION_PREVISION)

    inicio, _ = limites_dia_utc(hoy - timedelta(days=dias_atras))
    _, fin = limites_dia_utc(hoy + timedelta(days=1))
    historia = inicio - timedelta(days=VENTANA_FACTOR_DIAS + dias_atras + 7)
    festivos = {f for (f,) in conn.execute("SELECT fecha FROM festivo")}
    prevision = dict(conn.execute(SQL_PREVISION, (historia, fin)).fetchall())
    tramos = conn.execute(SQL_TRAMOS, (inicio, fin)).fetchall()
    resultado: dict[str, Counter[str]] = {}
    for (contrato,) in conn.execute("SELECT id FROM contrato ORDER BY id").fetchall():
        real = dict(conn.execute(SQL_REAL_POR_HORA, (contrato, historia, fin)).fetchall())
        con_real = {r for (r,) in conn.execute(
            "SELECT inicio FROM v_ssaa_real WHERE contrato = %s AND inicio >= %s AND inicio < %s",
            (contrato, inicio, fin),
        )}
        pendientes = [(a, b) for a, b in tramos if not _cubierto(a, b, con_real)]
        estimaciones = calcular(pendientes, real, prevision, festivos, usar_prevision)
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(UPSERT_ESTIMACION, [
                (contrato, e.inicio, e.fin, e.valor, e.metodo) for e in estimaciones
            ])
        metodos = Counter(e.metodo for e in estimaciones)
        resultado[contrato] = metodos
        log.info("SSAA %s: %d tramo(s) sin valor real; estimados %s",
                 contrato, len(pendientes), dict(metodos) or "ninguno")
    return resultado


def _cubierto(inicio: datetime, fin: datetime, con_real: Collection[datetime]) -> bool:
    paso = timedelta(minutes=15)
    t = inicio
    while t < fin:
        if t not in con_real:
            return False
        t += paso
    return True
