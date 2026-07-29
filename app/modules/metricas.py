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

__all__ = [
    "Cobertura", "Metricas", "cobertura", "dia_local", "instante_local",
    "obtener", "resumir",
]

# Cuántos días atrás se miran para dar contexto al día de hoy. Una semana es lo
# que hace falta para que "hoy has andado poco" signifique algo, y lo que cabe
# en un par de líneas de prompt. Un mes entero no diría mucho más y costaría
# tokens en cada mensaje.
DIAS_DE_CONTEXTO = 7

# La fuente que llega de un móvil de verdad. Todo lo demás es simulado y hay
# que decirlo (decisión 36).
FUENTE_REAL = "atajos-iphone"

# Cuántas muestras debería traer un día completo: las seis automatizaciones de
# *Hora del día* que hay puestas en el iPhone (08:00, 12:00, 16:00, 18:00,
# 20:00 y 23:55). No es un número redondo elegido a ojo — es la cuenta que
# convierte "han llegado 2 muestras hoy" en "faltan cuatro", que es lo que
# decide si la Fase 2d se puede cerrar.
ENVIOS_ESPERADOS_POR_DIA = 6

# A partir de esta hora LOCAL se considera que el día ya está contado.
#
# El criterio NO es cuántos envíos llegaron, y esa es la parte que no se ve
# venir. La columna es un acumulado (decisión 25), así que perder los envíos de
# enmedio no pierde nada: el de las 23:55 ya trae el total del día. Lo único que
# trunca un día es perder el ÚLTIMO. Medido sobre la serie simulada: el
# 27-07-2026 perdió los envíos de las 10:00 y las 14:00 y su total es exacto.
#
# Un día cuya última muestra es de las 14:00 no da "los pasos de ese día": da un
# SUELO. Y eso no da ningún error —es un número plausible— así que la única
# forma de no mentir es marcarlo.
HORA_CIERRE_LOCAL = 22


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
    # Los días de `pasos_por_dia` cuyo total es un SUELO y no un total: hoy, que
    # aún no ha terminado, y los que perdieron su último envío. Un número que
    # es un suelo presentado como total es plausible y falso, que es la peor
    # combinación (decisión 11).
    dias_parciales: tuple[str, ...] = ()
    bateria: int | None = None
    ultima_medida: str | None = None
    muestras_hoy: int = 0
    es_simulado: bool = False
    hay_datos: bool = False

    @property
    def media_diaria(self) -> int | None:
        """Media de los días COMPLETOS de verdad.

        Hoy va a medias por definición —son las 11:00 y llevas 2.000 pasos—, así
        que meterlo en la media la hunde y luego "hoy vas por debajo de tu
        media" sale mal calculado a favor de sí mismo.

        Antes se descartaba el ÚLTIMO elemento dando por hecho que era hoy, y
        eso fallaba de dos formas mudas: si hoy aún no ha llegado ninguna
        muestra, el último elemento es AYER y se tiraba un día bueno; y un día
        que perdió su envío de las 23:55 entraba como completo arrastrando la
        media hacia abajo. Ahora se descarta por nombre, con la lista que
        `resumir()` ya calcula.
        """
        parciales = set(self.dias_parciales)
        completos = [p for fecha, p in self.pasos_por_dia if fecha not in parciales]
        if not completos:
            return None
        return round(sum(completos) / len(completos))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pasos_hoy": self.pasos_hoy,
            "pasos_por_dia": [{"fecha": f, "pasos": p} for f, p in self.pasos_por_dia],
            "dias_parciales": list(self.dias_parciales),
            "media_diaria": self.media_diaria,
            "bateria": self.bateria,
            "ultima_medida": self.ultima_medida,
            "muestras_hoy": self.muestras_hoy,
            "es_simulado": self.es_simulado,
            "hay_datos": self.hay_datos,
        }


def instante_local(muestra: dict[str, Any]) -> datetime | None:
    """El instante LOCAL de una muestra, deshaciendo la canonización a UTC.

    Devuelve la hora de pared del móvil: el `tzinfo` sigue siendo UTC porque lo
    que interesa son los dígitos del reloj, no el instante absoluto.

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
            return instante.astimezone(timezone.utc) + delta
        except ValueError:
            # Un desfase corrupto no puede tirar la métrica entera: se cae al
            # día UTC, que es lo que había antes de intentar afinar.
            pass
    return instante


def dia_local(muestra: dict[str, Any]) -> date | None:
    """El día LOCAL de una muestra.

    Es pública porque la usan dos cosas —el resumen de pasos y la cobertura— y
    reescribirla en la segunda sería reintroducir el bug de la decisión 29 en
    la mitad del código que no lo tiene. Por el mismo motivo delega en
    `instante_local()` en vez de repetir la aritmética del desfase.
    """
    instante = instante_local(muestra)
    return instante.date() if instante else None


def resumir(muestras: list[dict[str, Any]], hoy: date) -> Metricas:
    """Convierte las muestras crudas en el resumen. Función PURA.

    `hoy` se recibe en vez de leerse del reloj para que se pueda probar con
    fechas escritas a mano, igual que `ensamblar()` del contexto recibe `ahora`.
    """
    if not muestras:
        return Metricas()

    # Máximo por día: la columna es un acumulado, no incrementos.
    por_dia: dict[date, int] = {}
    # La hora local de la última muestra de cada día, que es lo que decide si su
    # total está cerrado o es solo un suelo.
    ultima_hora: dict[date, int] = {}
    for muestra in muestras:
        pasos = muestra.get("pasos")
        instante = instante_local(muestra)
        if pasos is None or instante is None:
            continue
        dia = instante.date()
        por_dia[dia] = max(por_dia.get(dia, 0), int(pasos))
        ultima_hora[dia] = max(ultima_hora.get(dia, -1), instante.hour)

    # Hoy siempre es parcial: son las 11:00 y el día no ha terminado. Los demás
    # lo son si su última muestra llegó antes de la hora de cierre.
    parciales = tuple(
        dia.isoformat()
        for dia in sorted(por_dia)
        if dia >= hoy or ultima_hora.get(dia, -1) < HORA_CIERRE_LOCAL
    )

    # La batería es una lectura PUNTUAL, así que vale la última y no el máximo.
    # Confundirlo daría siempre ~100 %: el pico de la noche que cargó.
    ultima = max(muestras, key=lambda m: m["medido_en"])

    return Metricas(
        pasos_hoy=por_dia.get(hoy),
        pasos_por_dia=[(d.isoformat(), p) for d, p in sorted(por_dia.items())],
        dias_parciales=parciales,
        bateria=ultima.get("bateria"),
        ultima_medida=ultima.get("medido_en"),
        muestras_hoy=sum(1 for m in muestras if dia_local(m) == hoy),
        # Basta con que UNA muestra sea simulada para marcarlo todo. Es
        # equivocarse hacia el lado seguro: decir "esto es real" teniendo dentro
        # datos inventados es el error que no se puede permitir; lo contrario
        # solo es una advertencia de más.
        es_simulado=any(m.get("fuente") != FUENTE_REAL for m in muestras),
        hay_datos=bool(por_dia),
    )


@dataclass(frozen=True)
class Cobertura:
    """Si la telemetría llega SOLA y SIN HUECOS, que es lo que cierra la 2d.

    Es una pregunta distinta de la que responde `Metricas`, y por eso es otro
    tipo. `Metricas` dice *cuánto has andado*; esto dice *si te puedes creer ese
    número*. Mezclarlas haría que una serie llena de agujeros se leyera igual
    que una completa, que es justo lo que la Fase 2d lleva semanas sin dar por
    bueno.
    """

    dias_abarcados: int = 0
    dias_con_datos: int = 0
    huecos: int = 0
    dias_incompletos: int = 0
    por_dia: list[tuple[str, int]] = field(default_factory=list)
    primera: str | None = None
    ultima: str | None = None

    @property
    def sin_huecos(self) -> bool:
        return bool(self.dias_con_datos) and self.huecos == 0


def cobertura(
    muestras: list[dict[str, Any]], hoy: date,
    *, esperadas_por_dia: int = ENVIOS_ESPERADOS_POR_DIA,
) -> Cobertura:
    """Cuántos días cubre esa serie y cuántos le faltan. Función PURA.

    **La ventana va del primer día con datos hasta hoy, no de hoy hacia atrás.**
    La diferencia importa: contar sobre los últimos siete días diría "cinco
    huecos" el día siguiente a montar las automatizaciones, y un aviso que salta
    cuando todo va bien se aprende a ignorar. Contando desde la primera muestra,
    un hueco es siempre un envío que se perdió — y que hoy no haya llegado nada
    también sale, porque el tramo llega hasta hoy.

    `dias_incompletos` es la señal fina, y hace falta: seis automatizaciones al
    día que entregan dos muestras dan cero huecos y aun así no son una serie
    fiable. Sin esta cifra, "sin huecos" se leería como "cerrado".

    Quien llama decide qué muestras entran. Para responder "¿es fiable la
    telemetría?" hay que pasarle SOLO las de `FUENTE_REAL`: una simulación no
    cierra nada (decisión 36).
    """
    por_dia: dict[date, int] = {}
    for muestra in muestras:
        dia = dia_local(muestra)
        if dia is not None:
            por_dia[dia] = por_dia.get(dia, 0) + 1

    if not por_dia:
        return Cobertura()

    primer_dia = min(por_dia)
    # `max(..., 1)` por si la única muestra es de mañana: un reloj adelantado en
    # el móvil no puede producir un tramo de cero días y una división rara.
    abarcados = max((hoy - primer_dia).days + 1, 1)

    instantes = [m["medido_en"] for m in muestras if m.get("medido_en")]

    return Cobertura(
        dias_abarcados=abarcados,
        dias_con_datos=len(por_dia),
        huecos=max(abarcados - len(por_dia), 0),
        dias_incompletos=sum(1 for n in por_dia.values() if n < esperadas_por_dia),
        por_dia=[(d.isoformat(), n) for d, n in sorted(por_dia.items())],
        primera=min(instantes) if instantes else None,
        ultima=max(instantes) if instantes else None,
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
