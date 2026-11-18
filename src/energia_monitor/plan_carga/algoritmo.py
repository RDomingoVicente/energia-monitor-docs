"""Algoritmo del planificador (E3.3 y E3.4): función pura `planificar(entrada) -> Plan`.

1. Cuartos candidatos: futuros (inicio >= ahora), con el coche en casa (ventana
   segura: enchufado ahora y antes de la próxima salida; probable: probabilidad
   >= umbral, salvo en modo prudente), fuera de la franja en curso, de las
   tareas respetadas (manuales; ajenas solo en activo) y del margen antes de
   cada salida (`margen_salida_min`).
2. Trayectoria del nivel: nivel de ahora, más la carga de cada cuarto ×
   factor_bateria, menos el consumo previsto de cada salida en su instante. La
   carga fija (resto de la franja en curso y tareas respetadas) cuenta igual.
   El nivel nunca pasa de `tope_pct`: sus máximos están justo antes de cada
   salida y al final del horizonte.
3. Nivel 1, necesaria: por salidas en orden, los cuartos más baratos que
   terminan antes de la salida − margen hasta cubrir su objetivo; para la
   próxima, primero la ventana segura y, si no basta, también la probable. Un
   cuarto elegido para una salida posterior puede quedar antes de una anterior:
   así se acumula la energía de las salidas sin otra ventana. En modo prudente
   solo la próxima salida.
4. Nivel 2, margen: `margen_carga_pct` de lo cargado para cada salida, solo en
   cuartos baratos (precio publicado < umbral).
5. Nivel 3, oportunista: cuartos baratos hasta `nivel_oportunista_pct`.
6. Franjas: cuartos contiguos; las cortas se alargan o se descartan (lo que
   cueste menos); si no caben en `max_tareas_sistema`, se cierran los huecos
   más baratos (E3.4).

Empates de precio: madrugada (00:00–08:00 local), ventana segura y antes.
Los cuartos se asignan enteros, recortados al tope.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from energia_monitor.plan_carga import franjas as fr
from energia_monitor.plan_carga.modelo import (
    CUARTO,
    PRIORIDAD,
    Cuarto,
    CuartoPlan,
    Entrada,
    Franja,
    Plan,
    Salida,
    SalidaPlan,
)
from energia_monitor.tiempo import MADRID

CERO = Decimal(0)
OPCIONALES = ("margen", "oportunista")
CUARTO_H = Decimal("0.25")
TOLERANCIA = Decimal("1e-9")


@dataclass
class _Eleccion:
    nivel: str
    salida: datetime | None
    kwh: Decimal  # contador del cargador


class _Planificador:
    def __init__(self, e: Entrada) -> None:
        self.e = e
        self.p = e.parametros
        self.cuartos: dict[datetime, Cuarto] = {c.inicio: c for c in e.cuartos}
        self.proxima = e.salidas[0].instante if e.salidas else None
        self.elegidos: dict[datetime, _Eleccion] = {}
        self.avisos: list[str] = []
        self.fijos: list[tuple[datetime, Decimal]] = self._cargas_fijas()
        self.candidatos: set[datetime] = {q for q in self.cuartos if self._candidato(q)}

    # --- Disponibilidad y bloqueos ---------------------------------------------------------

    def ventana(self, q: datetime) -> str | None:
        c = self.cuartos[q]
        if self.e.conectado and (self.proxima is None or c.fin <= self.proxima):
            return "segura"
        if (not self.e.prudente and c.probabilidad is not None
                and c.probabilidad * 100 >= self.p.umbral_disponibilidad_pct):
            return "probable"
        return None

    def _respetados(self, con_energia: bool = False) -> list[tuple[datetime, datetime]]:
        """Tramos que el plan no puede usar; con_energia: solo los que cargan (un bloqueo no carga)."""
        return [(o.inicio, o.fin) for o in self.e.ocupados
                if (o.origen != "ajena" or self.e.respetar_ajenas) and not (con_energia and o.origen == "bloqueo")]

    def _candidato(self, q: datetime) -> bool:
        c = self.cuartos[q]
        if q < self.e.ahora or c.fin > self.e.horizonte_fin or self.ventana(q) is None:
            return False
        if self.e.en_curso and fr.solapa(q, c.fin, self.e.en_curso.inicio, self.e.en_curso.fin):
            return False
        if any(fr.solapa(q, c.fin, i, f) for i, f in self._respetados()):
            return False
        margen = timedelta(minutes=self.p.margen_salida_min)
        return not any(fr.solapa(q, c.fin, s.instante - margen, s.instante) for s in self.e.salidas)

    def _cargas_fijas(self) -> list[tuple[datetime, Decimal]]:
        """(instante, kWh a batería) del resto de la franja en curso y de las tareas respetadas."""
        fijos: list[tuple[datetime, Decimal]] = []
        if self.e.en_curso and self.e.conectado:
            for c in self.e.en_curso.cuartos:
                fin = c.inicio + CUARTO
                if fin > self.e.ahora:
                    resto = Decimal((fin - max(c.inicio, self.e.ahora)).total_seconds()) / 900
                    fijos.append((max(c.inicio, self.e.ahora), c.kwh * resto * self.p.factor_bateria))
        for inicio, fin in self._respetados(con_energia=True):
            for q, c in self.cuartos.items():
                if q >= self.e.ahora and self.ventana(q) and fr.solapa(q, c.fin, inicio, fin):
                    horas = Decimal((min(c.fin, fin) - max(q, inicio)).total_seconds()) / 3600
                    fijos.append((q, c.potencia_kw * horas * self.p.factor_bateria))
        return fijos

    # --- Trayectoria ---------------------------------------------------------------------

    def _cargas(self) -> list[tuple[datetime, Decimal]]:
        return self.fijos + [(q, x.kwh * self.p.factor_bateria) for q, x in self.elegidos.items()]

    def nivel_en(self, t: datetime, solo_fijos: bool = False) -> Decimal:
        """Nivel justo antes de t: cargas que empiezan antes de t, consumos de las salidas anteriores."""
        cargas = self.fijos if solo_fijos else self._cargas()
        nivel = self.e.nivel_kwh + sum((k for q, k in cargas if q < t), CERO)
        return nivel - sum((s.consumo_kwh for s in self.e.salidas if s.instante < t), CERO)

    def _puntos(self, q: datetime) -> list[datetime]:
        """Máximos de la trayectoria a los que afecta una carga en q: salidas posteriores y el final."""
        return [s.instante for s in self.e.salidas if s.instante > q] + [self.e.horizonte_fin + CUARTO]

    def holgura(self, q: datetime, techo: Decimal) -> Decimal:
        return max(CERO, min(techo - self.nivel_en(t) for t in self._puntos(q)))

    # --- Selección ---------------------------------------------------------------------------

    def barato(self, q: datetime) -> bool:
        c = self.cuartos[q]
        return self.e.umbral is not None and c.precio is not None and not c.provisional and c.precio < self.e.umbral

    def sobre_umbral(self, q: datetime) -> bool:
        c = self.cuartos[q]
        return self.e.umbral is not None and (c.precio is None or c.precio >= self.e.umbral)

    def _orden(self, q: datetime) -> tuple:  # type: ignore[type-arg]
        c = self.cuartos[q]
        return (c.precio is None, c.precio or CERO, q.astimezone(MADRID).hour >= 8, self.ventana(q) != "segura", q)

    def lleno(self, q: datetime) -> Decimal:
        """kWh del contador en un cuarto entero."""
        return self.cuartos[q].potencia_kw * CUARTO_H

    def _anadir(self, q: datetime, nivel: str, salida: datetime | None, techo: Decimal) -> Decimal:
        """Elige q recortado al techo (a batería); devuelve los kWh a batería añadidos (0: no cabe)."""
        bateria = min(self.lleno(q) * self.p.factor_bateria, self.holgura(q, techo))
        if bateria <= 0:
            return CERO
        self.elegidos[q] = _Eleccion(nivel, salida, bateria / self.p.factor_bateria)
        return bateria

    def libres(self, ventanas: tuple[str, ...], limite: datetime | None = None,
               solo_baratos: bool = False) -> list[datetime]:
        qs = [q for q in self.candidatos if q not in self.elegidos and self.ventana(q) in ventanas
              and (limite is None or self.cuartos[q].fin <= limite) and (not solo_baratos or self.barato(q))]
        return sorted(qs, key=self._orden)

    def necesaria(self, salidas: list[Salida]) -> dict[datetime, Decimal]:
        cargado: dict[datetime, Decimal] = {}
        margen = timedelta(minutes=self.p.margen_salida_min)
        for k, s in enumerate(salidas):
            cargado[s.instante] = CERO
            fases = [("segura",), ("segura", "probable")] if k == 0 else [("segura", "probable")]
            for ventanas in fases:
                for q in self.libres(ventanas, s.instante - margen):
                    if self.nivel_en(s.instante) >= s.objetivo_kwh - TOLERANCIA:
                        break
                    cargado[s.instante] += self._anadir(q, "necesaria", s.instante, self.p.tope_kwh)
                if self.nivel_en(s.instante) >= s.objetivo_kwh - TOLERANCIA:
                    break
        return cargado

    def margen(self, salidas: list[Salida], cargado: dict[datetime, Decimal]) -> None:
        ventanas = ("segura",) if self.e.prudente else ("segura", "probable")
        margen = timedelta(minutes=self.p.margen_salida_min)
        for s in salidas:
            extra = cargado.get(s.instante, CERO) * self.p.margen_carga_pct / 100
            for q in self.libres(ventanas, s.instante - margen, solo_baratos=True):
                if extra <= 0:
                    break
                extra -= self._anadir(q, "margen", s.instante, self.p.tope_kwh)

    def oportunista(self) -> None:
        if not self.p.carga_oportunista:
            return
        techo = self.techo("oportunista")
        ventanas = ("segura",) if self.e.prudente else ("segura", "probable")
        for q in self.libres(ventanas, solo_baratos=True):
            self._anadir(q, "oportunista", None, techo)

    # --- Franjas -------------------------------------------------------------------------------

    def _coste(self, qs: Iterable[datetime], kwh: dict[datetime, Decimal] | None = None) -> Decimal | None:
        total = CERO
        for q in qs:
            precio = self.cuartos[q].precio
            if precio is None:
                return None
            total += precio * (kwh[q] if kwh else self.elegidos[q].kwh if q in self.elegidos else self.lleno(q))
        return total

    def _en_curso(self) -> set[datetime]:
        return {c.inicio for c in self.e.en_curso.cuartos} if self.e.en_curso else set()

    def _grupos(self) -> list[tuple[datetime, datetime]]:
        """Franjas propias (sin la en curso, que es aparte aunque sea contigua)."""
        return fr.agrupar(self.elegidos)

    def _cortas(self) -> list[tuple[datetime, datetime]]:
        minimo = timedelta(minutes=self.p.franja_min_min)
        return [g for g in self._grupos() if g[1] - g[0] < minimo]

    def _cuartos_de(self, inicio: datetime, fin: datetime) -> list[datetime]:
        n = int((fin - inicio) / CUARTO)
        return [inicio + i * CUARTO for i in range(n)]

    def _limite(self, x: _Eleccion) -> datetime | None:
        """Fin máximo de un cuarto que sirve a la salida de x (salida − margen)."""
        return x.salida - timedelta(minutes=self.p.margen_salida_min) if x.salida else None

    def _admite(self, q: datetime, x: _Eleccion) -> bool:
        """q puede llevar el nivel de x: antes de su salida − margen y, si es opcional, barato y publicado."""
        limite = self._limite(x)
        if limite is not None and q + CUARTO > limite:
            return False
        return x.nivel not in OPCIONALES or self.barato(q)

    def _alargar(self, g: tuple[datetime, datetime], x: _Eleccion) -> tuple[Decimal, list[datetime]] | None:
        """Ventana más barata de franja_min que contiene g, con cuartos candidatos libres o ya elegidos."""
        n = math.ceil(self.p.franja_min_min / 15)
        tam = int((g[1] - g[0]) / CUARTO)
        mejor: tuple[Decimal, list[datetime]] | None = None
        for desplazamiento in range(n - tam + 1):
            inicio = g[0] - (n - tam - desplazamiento) * CUARTO
            nuevos = [q for q in self._cuartos_de(inicio, inicio + n * CUARTO) if q not in self.elegidos]
            if any(q not in self.candidatos or not self._admite(q, x) for q in nuevos):
                continue
            coste = self._coste(nuevos)
            if coste is not None and (mejor is None or coste < mejor[0]):
                mejor = (coste, nuevos)
        return mejor

    def _ventana_nueva(self, prohibidos: set[datetime], ocupados: set[datetime], limite: datetime | None,
                       n: int) -> tuple[Decimal, list[datetime]] | None:
        """Ventana más barata de n cuartos candidatos libres que terminan antes del límite."""
        mejor: tuple[Decimal, list[datetime]] | None = None
        for q in sorted(self.candidatos):
            qs = [q + i * CUARTO for i in range(n)]
            if any(x not in self.candidatos or x in prohibidos or x in ocupados
                   or (limite is not None and x + CUARTO > limite) for x in qs):
                continue
            coste = self._coste(qs)
            if coste is not None and (mejor is None or coste < mejor[0]):
                mejor = (coste, qs)
        return mejor

    def _descartar(self, g: tuple[datetime, datetime]) -> tuple[Decimal, list[tuple[datetime, _Eleccion]]] | None:
        """Quita g y repone su energía necesaria sin crear franjas cortas: alargando otras franjas por los
        bordes o con una franja nueva de la duración mínima, lo que cueste menos."""
        quitados = {q: self.elegidos[q] for q in self._cuartos_de(*g)}
        coste_quitado = self._coste(quitados) or CERO
        necesarios = [x for x in quitados.values() if x.nivel == "necesaria"]
        if not necesarios:
            return -coste_quitado, []
        # Una sola reposición con la salida más temprana de los quitados (la más exigente).
        x0 = min(necesarios, key=lambda x: x.salida or datetime.max.replace(tzinfo=g[0].tzinfo))
        pendiente = sum((x.kwh for x in necesarios), CERO)
        limite = self._limite(x0)
        restantes = set(self.elegidos) - set(quitados)
        opciones: list[tuple[Decimal, list[datetime]]] = []
        # a) Bordes de las franjas que quedan.
        nuevos: list[datetime] = []
        resto, coste = pendiente, CERO
        while resto > TOLERANCIA:
            elegidos = restantes | set(nuevos)
            bordes = [q for q in self.candidatos if q not in elegidos and q not in quitados
                      and (q - CUARTO in elegidos or q + CUARTO in elegidos)
                      and (limite is None or q + CUARTO <= limite)]
            if not bordes:
                break
            q = min(bordes, key=self._orden)
            c = self._coste([q])
            if c is None:
                break
            nuevos.append(q)
            coste += c
            resto -= self.lleno(q)
        if resto <= TOLERANCIA:
            opciones.append((coste, nuevos))
        # b) Una franja nueva de la duración mínima (o más, si hace falta más energía).
        n = max(math.ceil(self.p.franja_min_min / 15),
                math.ceil(pendiente / max(self.lleno(q) for q in quitados)))
        ventana = self._ventana_nueva(set(quitados), restantes, limite, n)
        if ventana is not None:
            opciones.append(ventana)
        if not opciones:
            return None
        coste, qs = min(opciones, key=lambda o: (o[0], o[1][0]))
        return coste - coste_quitado, [(q, _Eleccion("necesaria", x0.salida, self.lleno(q))) for q in qs]

    def consolidar(self) -> None:
        intocables: set[tuple[datetime, datetime]] = set()
        for _ in range(4 * len(self.cuartos) + 1):
            cortas = [g for g in self._cortas() if g not in intocables]
            if not cortas:
                break
            g = cortas[0]
            # Los cuartos nuevos llevan el nivel de mayor prioridad de la franja y su salida.
            nivel = self._principal(*g)
            alargar = self._alargar(g, nivel)
            if nivel.nivel in OPCIONALES:
                # Margen u oportunista: se alarga si hay cuartos que cumplen su nivel; si no, se descarta.
                if alargar is None or not self._aplicar(alargar[1], nivel):
                    for q in self._cuartos_de(*g):
                        del self.elegidos[q]
                continue
            descartar = self._descartar(g)
            if alargar is None and descartar is None:
                intocables.add(g)  # necesaria sin alternativa: se queda corta
                continue
            if descartar is None or (alargar is not None and alargar[0] <= descartar[0]):
                assert alargar is not None
                self._aplicar(alargar[1], nivel)
            else:
                for q in self._cuartos_de(*g):
                    del self.elegidos[q]
                for q, x in descartar[1]:
                    self._aplicar([q], x)
        self._caber()
        self._recortar_oportunista()

    def recortable(self, q: datetime) -> bool:
        """q está en un borde de su franja y quitarlo no la parte ni la deja más corta que franja_min."""
        antes, despues = q - CUARTO in self.elegidos, q + CUARTO in self.elegidos
        if antes and despues:
            return False
        grupo = next(g for g in self._grupos() if g[0] <= q < g[1])
        largo = grupo[1] - grupo[0] - CUARTO
        return largo == timedelta(0) or largo >= timedelta(minutes=self.p.franja_min_min)

    def _recortar_oportunista(self) -> None:
        """La consolidación puede añadir energía necesaria después del oportunista: si con ella algún punto
        pasa de nivel_oportunista_pct, se quitan los cuartos oportunistas más caros de los bordes."""
        techo = self.techo("oportunista")
        while True:
            puntos = [t for t in [*(s.instante for s in self.e.salidas), self.e.horizonte_fin + CUARTO]
                      if self.nivel_en(t) > techo + TOLERANCIA]
            quitables = [q for q, x in self.elegidos.items() if x.nivel == "oportunista"
                         and any(q < t for t in puntos) and self.recortable(q)]
            if not quitables:
                return
            del self.elegidos[max(quitables, key=lambda q: (self.cuartos[q].precio or CERO, q))]

    def _principal(self, inicio: datetime, fin: datetime) -> _Eleccion:
        """Nivel de mayor prioridad de una franja (con la salida más temprana entre los de ese nivel)."""
        return min((self.elegidos[q] for q in self._cuartos_de(inicio, fin)),
                   key=lambda x: (PRIORIDAD[x.nivel], x.salida or datetime.max.replace(tzinfo=inicio.tzinfo)))

    def techo(self, nivel: str) -> Decimal:
        return min(self.p.nivel_oportunista_kwh, self.p.tope_kwh) if nivel == "oportunista" else self.p.tope_kwh

    def _aplicar(self, qs: list[datetime], x: _Eleccion) -> bool:
        """Añade qs con el nivel de x. Necesaria: un cuarto sin holgura entra con 0 kWh (completa la franja;
        el coche ya está al tope). Margen u oportunista: todos con energía bajo su techo, o ninguno."""
        antes = dict(self.elegidos)
        for q in qs:
            if self._anadir(q, x.nivel, x.salida, self.techo(x.nivel)) > 0:
                continue
            if x.nivel in OPCIONALES:
                self.elegidos = antes
                return False
            self.elegidos[q] = _Eleccion(x.nivel, x.salida, CERO)
        return True

    def tareas(self) -> int:
        grupos = self._grupos()
        if self.e.en_curso:
            grupos = [*grupos, (self.e.en_curso.inicio, self.e.en_curso.fin)]
        return sum(fr.num_tareas(i, f) for i, f in grupos)

    def _caber(self) -> None:
        while self.tareas() > self.p.max_tareas:
            grupos = self._grupos()
            huecos = []
            for (inicio_a, fin_a), (inicio_b, fin_b) in zip(grupos, grupos[1:], strict=False):
                qs = self._cuartos_de(fin_a, inicio_b)
                ahorra = fr.num_tareas(inicio_a, fin_b) < fr.num_tareas(inicio_a, fin_a) + fr.num_tareas(inicio_b, fin_b)
                if not ahorra or not all(q in self.candidatos for q in qs):
                    continue
                # El hueco toma el nivel principal de una de sus vecinas (primero el de más prioridad) y
                # cumple sus reglas.
                for x in sorted((self._principal(inicio_a, fin_a), self._principal(inicio_b, fin_b)),
                                key=lambda x: PRIORIDAD[x.nivel]):
                    coste = self._coste(qs)
                    if coste is not None and all(self._admite(q, x) for q in qs):
                        huecos.append((coste, fin_a, qs, x))
                        break
            cerrado = False
            for _, _, qs, x in sorted(huecos, key=lambda h: (h[0], h[1])):
                if self._aplicar(qs, x):
                    cerrado = True
                    break
            if cerrado:
                continue
            opcionales = [g for g in grupos if all(self.elegidos[q].nivel in OPCIONALES for q in self._cuartos_de(*g))]
            if not opcionales:
                self.avisos.append(f"El plan necesita {self.tareas()} tareas y solo caben {self.p.max_tareas}.")
                return
            peor = max(opcionales, key=lambda g: (self._coste(self._cuartos_de(*g)) or CERO, g[0]))
            for q in self._cuartos_de(*peor):
                del self.elegidos[q]

    # --- Resultado -----------------------------------------------------------------------------

    def _choques(self, inicio: datetime, fin: datetime) -> tuple[int, ...]:
        return tuple(sorted({o.tarea for o in self.e.ocupados if o.origen == "ajena" and o.tarea is not None
                             and fr.solapa(inicio, fin, o.inicio, o.fin)}))

    def resultado(self, salidas: list[Salida], necesarias: dict[datetime, Decimal]) -> Plan:
        cuartos: dict[datetime, CuartoPlan] = {}
        if self.e.en_curso:
            cuartos.update({c.inicio: c for c in self.e.en_curso.cuartos})
        for q, x in self.elegidos.items():
            c = self.cuartos[q]
            cuartos[q] = CuartoPlan(q, x.nivel, x.salida, x.kwh, c.precio, c.provisional, self.ventana(q) or "segura",
                                    self.sobre_umbral(q))
        grupos = [(g, False) for g in self._grupos()]
        if self.e.en_curso:
            grupos.append(((self.e.en_curso.inicio, self.e.en_curso.fin), True))
        lista = []
        for (inicio, fin), en_curso in sorted(grupos, key=lambda x: x[0][0]):
            qs = [cuartos[q] for q in sorted(cuartos) if inicio <= q < fin]
            principal = min(qs, key=lambda c: (PRIORIDAD[c.nivel], c.salida or datetime.max.replace(tzinfo=inicio.tzinfo)))
            choques = self._choques(inicio, fin)
            if en_curso and choques and self.e.respetar_ajenas:
                self.avisos.append(f"La tarea ajena {', '.join(map(str, choques))} pisa la franja en curso.")
            por_nivel: dict[tuple[str, datetime | None], Decimal] = {}
            for c in qs:
                por_nivel[(c.nivel, c.salida)] = por_nivel.get((c.nivel, c.salida), CERO) + c.kwh
            maximo = datetime.max.replace(tzinfo=inicio.tzinfo)
            desglose = tuple((n, s, k) for (n, s), k in sorted(por_nivel.items(),
                                                                key=lambda x: (PRIORIDAD[x[0][0]], x[0][1] or maximo)))
            lista.append(Franja(inicio, fin, principal.nivel, principal.salida, sum((c.kwh for c in qs), CERO),
                                any(c.provisional for c in qs), fr.num_tareas(inicio, fin), en_curso, choques,
                                desglose))
        salidas_plan = []
        for s in self.e.salidas:
            nivel = self.nivel_en(s.instante)
            cubierta = nivel >= s.objetivo_kwh - TOLERANCIA
            salto = any(c.salida == s.instante and c.nivel == "necesaria" and c.sobre_umbral for c in cuartos.values())
            if not cubierta and s in salidas:
                self.avisos.append(f"El plan no cubre la salida de las {s.instante.astimezone(MADRID):%d/%m %H:%M}.")
            salidas_plan.append(SalidaPlan(s.instante, s.tipo, s.objetivo_kwh, s.consumo_kwh,
                                           necesarias[s.instante], nivel, cubierta, salto))
        return Plan(tuple(cuartos[q] for q in sorted(cuartos)), tuple(lista), tuple(salidas_plan), tuple(self.avisos))


def planificar(e: Entrada) -> Plan:
    pl = _Planificador(e)
    necesarias = {s.instante: max(CERO, s.objetivo_kwh - pl.nivel_en(s.instante, solo_fijos=True))
                  for s in e.salidas}
    if e.prudente and not e.conectado:
        pl.avisos.append("Modo prudente y el coche no está enchufado: sin ventana segura, no se planifica.")
    salidas = list(e.salidas[:1]) if e.prudente else list(e.salidas)
    cargado = pl.necesaria(salidas)
    pl.margen(salidas, cargado)
    pl.oportunista()
    pl.consolidar()
    return pl.resultado(salidas, necesarias)

