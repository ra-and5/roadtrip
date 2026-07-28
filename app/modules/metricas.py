"""Pasos y batería del móvil, resumidos para que quepan en un contexto.

Función de entrada: `obtener(zona) -> Metricas`.

Esto es el hueco que `Contexto.metricas` llevaba declarado y vacío desde la
Fase 5. Se llena ahora porque el usuario da por bueno el formato de la
telemetría y decide no esperar al volumen (decisión 36).

**Se RESUME, no se vuelca, y esa es la decisión que define el módulo.** Hoy hay
81 muestras en la tabla y en un mes de viaje habrá más de mil; meterlas fila a
fila en el prompt costaría miles de tokens en cada mensaje del chatbot para
decir algo que cabe en dos líneas. Lo que un modelo necesita para razonar sobre
"¿hoy toca ruta o playa?" es *cuánto llevas andado hoy* y *cómo va comparado con
tus días*, no una lista de lecturas horarias.

Dos cosas que se hacen aquí y no se ven venir:

**El día es el LOCAL, reconstruido desde el desfase de cada muestra.** Es la
decisión 29 otra vez: una muestra de las 00:30 en España es del día siguiente en
UTC, y contarla ahí desplaza un día entero. El desfase viene guardado en
`offset_original` justamente para poder deshacer esto.

**El total del día es el MÁXIMO de sus muestras, no la suma.** La columna es un
acumulado que se reinicia a medianoche desde que el atajo usa el filtro `es hoy`
(decisión 25). Sumarla daría cerca del cuádruple, y —lo de siempre— sin dar
ningún error: solo un viajero que anda 40.000 pasos al día.

Y lo que no se calla: si las muestras son simuladas, `es_simulado` lo dice, y
quien renderice esto para un modelo tiene que decírselo. Un dato inventado que
se presenta como medido es el fallo de la decisión 11 llevado al sitio donde más
daño hace, que es el razonamiento de un modelo que no puede comprobarlo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules import storage

__all__ = ["Metricas", "obtener", "resumir"]

# Cuántos días atrás se miran para dar contexto al día de hoy. Una semana es lo
# que hace falta para que "hoy has andado poco" signifique algo, y lo que cabe
# en un par de líneas de prompt. Un mes entero no diría mucho más y costaría
# tokens en cada mensaje.
DIAS_DE_CONTEXTO = 7

# La fuente que llega de un móvil de verdad. Todo lo demás es simulado y hay
# que decirlo (decisión 36).
FUENTE_REAL = "atajos-iphone"


@dataclass(frozen=True)
class Metricas:
    """Lo que el cuerpo del viajero ha hecho hoy, y con qué compararlo.

    `es_simulado` no es un detalle de depuración: viaja hasta el prompt. Un
    modelo que no sabe que los pasos son inventados los usará como si fueran
    ciertos, y dirá cosas concretas y falsas sobre lo que has hecho hoy.
    """

    pasos_hoy: int | None = None
    # Máximo de cada día, del más antiguo al más reciente: [(fecha, pasos)].
    pasos_por_dia: list[tuple[str, int]] = field(default_factory=list)
    bateria: int | None = None
    ultima_medida: str | None = None
    muestras_hoy: int = 0
    es_simulado: bool = False
    hay_datos: bool = False

    @property
    def media_diaria(self) -> int | None:
        """Media de los días COMPLETOS, sin contar hoy.

        Hoy va a medias por definición —son las 11:00 y llevas 2.000 pasos—, así
        que meterlo en la media la hunde y luego "hoy vas por debajo de tu
        media" sale mal calculado a favor de sí mismo.
        """
        completos = [pasos for _, pasos in self.pasos_por_dia[:-1]]
        if not completos:
            return None
        return round(sum(completos) / len(completos))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pasos_hoy": self.pasos_hoy,
            "pasos_por_dia": [{"fecha": f, "pasos": p} for f, p in self.pasos_por_dia],
            "media_diaria": self.media_diaria,
            "bateria": self.bateria,
            "ultima_medida": self.ultima_medida,
            "muestras_hoy": self.muestras_hoy,
            "es_simulado": self.es_simulado,
            "hay_datos": self.hay_datos,
        }


def _dia_local(muestra: dict[str, Any]) -> date | None:
    """El día LOCAL de una muestra, deshaciendo la canonización a UTC.

    `medido_en` está en UTC y `offset_original` guarda el desfase con el que
    llegó, precisamente para poder volver atrás. Sin esto, todo lo que ocurra
    entre medianoche y las 02:00 en España se contaría en el día siguiente.
    """
    try:
        instante = datetime.fromisoformat(muestra["medido_en"])
    except (ValueError, TypeError, KeyError):
        return None

    desfase = muestra.get("offset_original")
    if desfase:
        try:
            # Se reconstruye el instante local aplicando el desfase guardado.
            horas, _, minutos = desfase.lstrip("+-").partition(":")
            delta = timedelta(hours=int(horas), minutes=int(minutos or 0))
            if desfase.startswith("-"):
                delta = -delta
            return (instante.astimezone(timezone.utc) + delta).date()
        except ValueError:
            # Un desfase corrupto no puede tirar la métrica entera: se cae al
            # día UTC, que es lo que había antes de intentar afinar.
            pass
    return instante.date()


def resumir(muestras: list[dict[str, Any]], hoy: date) -> Metricas:
    """Convierte las muestras crudas en el resumen. Función PURA.

    `hoy` se recibe en vez de leerse del reloj para que se pueda probar con
    fechas escritas a mano, igual que `ensamblar()` del contexto recibe `ahora`.
    """
    if not muestras:
        return Metricas()

    # Máximo por día: la columna es un acumulado, no incrementos.
    por_dia: dict[date, int] = {}
    for muestra in muestras:
        pasos = muestra.get("pasos")
        dia = _dia_local(muestra)
        if pasos is None or dia is None:
            continue
        por_dia[dia] = max(por_dia.get(dia, 0), int(pasos))

    # La batería es una lectura PUNTUAL, así que vale la última y no el máximo.
    # Confundirlo daría siempre ~100 %: el pico de la noche que cargó.
    ultima = max(muestras, key=lambda m: m["medido_en"])

    return Metricas(
        pasos_hoy=por_dia.get(hoy),
        pasos_por_dia=[(d.isoformat(), p) for d, p in sorted(por_dia.items())],
        bateria=ultima.get("bateria"),
        ultima_medida=ultima.get("medido_en"),
        muestras_hoy=sum(1 for m in muestras if _dia_local(m) == hoy),
        # Basta con que UNA muestra sea simulada para marcarlo todo. Es
        # equivocarse hacia el lado seguro: decir "esto es real" teniendo dentro
        # datos inventados es el error que no se puede permitir; lo contrario
        # solo es una advertencia de más.
        es_simulado=any(m.get("fuente") != FUENTE_REAL for m in muestras),
        hay_datos=bool(por_dia),
    )


def obtener(zona: str = "Europe/Madrid", *, ahora: datetime | None = None) -> Metricas:
    """El resumen de los últimos días. Lee la base de datos local, sin red.

    Nunca lanza: es una fuente opcional del contexto, y que la tabla esté vacía
    o que una fila venga torcida no puede tumbar la pantalla ni el chatbot. Sin
    datos devuelve unas `Metricas` vacías con `hay_datos=False`, que es un
    estado y no un `None` ambiguo (decisión 32).
    """
    try:
        tz = ZoneInfo(zona)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Europe/Madrid")

    referencia = (ahora or datetime.now(tz)).astimezone(tz)
    hoy = referencia.date()
    desde = (referencia - timedelta(days=DIAS_DE_CONTEXTO)).astimezone(
        timezone.utc
    ).isoformat(timespec="seconds")

    try:
        muestras = storage.telemetry_since(desde)
    except Exception:  # noqa: BLE001 - una fuente opcional nunca tumba el contexto
        return Metricas()

    return resumir(muestras, hoy)
