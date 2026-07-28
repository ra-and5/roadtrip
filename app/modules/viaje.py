"""El viaje hasta ahora, resumido: dónde has estado y qué escribiste.

Función de entrada: `resumen() -> Viaje`.

Reutiliza entero lo que ya existe —`notes.progreso()` y `ruta.construir()`— y no
recalcula nada por su cuenta. Eso importa: si este módulo contara los días o los
kilómetros a su manera, el chatbot y el mapa acabarían diciendo cifras distintas
del mismo viaje, y no habría forma de saber cuál es la buena. Es el motivo por el
que el contexto es un módulo y no un trozo de vista (decisión 32), aplicado un
nivel más abajo.

**Lo que aporta es el recorte, no el cálculo.** El mapa puede permitirse cargar
las mil notas del viaje; un prompt no. Aquí se queda lo que de verdad ayuda a
responder *"¿qué hago hoy?"* y *"¿qué escribí en Llanes?"*: los agregados (días,
sitios, kilómetros, comunidades) y las **últimas** notas con su texto. El resto
del viaje sigue existiendo en la base de datos y se consulta desde el mapa.

Es una fuente demostrada, al revés que la telemetría: las notas las escribe una
persona y se guardan con cola offline, así que lo que hay es lo que hubo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules import notes, ruta, storage

__all__ = ["Viaje", "resumen"]

# Cuántas notas recientes viajan con su texto completo. Diez son las de los
# últimos días de viaje: bastantes para que el modelo sepa por dónde vas y qué
# te ha gustado, pocas para no convertir cada mensaje del chat en un volcado del
# diario. Las anteriores siguen contando en los agregados.
NOTAS_RECIENTES = 10

# Cuánto texto de cada nota. Una nota de viaje son dos frases; el que escriba un
# testamento verá el principio, que es donde está el qué y el dónde.
MAX_TEXTO = 280


@dataclass(frozen=True)
class Viaje:
    """La forma del viaje: cuánto llevas y qué has ido dejando escrito."""

    notas_totales: int = 0
    dias: int = 0
    lugares: int = 0
    km: float = 0.0
    fotos: int = 0
    regiones: list[str] = field(default_factory=list)
    primera: str | None = None
    ultima: str | None = None
    # Las últimas notas, de la más antigua a la más reciente: se leen como un
    # diario y no como un listado al revés.
    recientes: list[dict[str, Any]] = field(default_factory=list)
    hay_datos: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "notas_totales": self.notas_totales,
            "dias": self.dias,
            "lugares": self.lugares,
            "km": self.km,
            "fotos": self.fotos,
            "regiones": self.regiones,
            "primera": self.primera,
            "ultima": self.ultima,
            "recientes": self.recientes,
            "hay_datos": self.hay_datos,
        }


def _recorta(texto: str | None) -> str:
    limpio = (texto or "").strip()
    if len(limpio) <= MAX_TEXTO:
        return limpio
    return limpio[: MAX_TEXTO - 1].rstrip() + "…"


def armar(notas: list[dict[str, Any]], puntos: list[dict[str, Any]]) -> Viaje:
    """Arma el resumen a partir de las listas ya leídas. Función PURA.

    Separada de `resumen()` por lo mismo que `ensamblar()` lo está de
    `construir()` en el contexto: aquí vive lo que merece pruebas y no toca la
    base de datos.
    """
    if not notas and not puntos:
        return Viaje()

    progreso = notes.progreso(notas)
    linea = ruta.construir(notas, puntos)
    resumen_ruta = linea["resumen"]

    # Las notas llegan de `list_notes` con la más reciente primero; se le da la
    # vuelta al recorte para que se lean en el orden en que se escribieron.
    recientes = [
        {
            "cuando": nota.get("created_at"),
            "lugar": nota.get("place_name"),
            "texto": _recorta(nota.get("text")),
        }
        for nota in reversed(notas[:NOTAS_RECIENTES])
    ]

    return Viaje(
        notas_totales=progreso["total"],
        # Los días y los kilómetros salen de la línea de tiempo (notas + fotos)
        # y no solo de las notas: un día en el que solo hiciste fotos es un día
        # de viaje igual.
        dias=resumen_ruta["dias"],
        lugares=progreso["lugares"],
        km=resumen_ruta["km_linea_recta"],
        fotos=resumen_ruta["fotos"],
        regiones=progreso["regiones"],
        primera=resumen_ruta["primera"],
        ultima=resumen_ruta["ultima"],
        recientes=recientes,
        hay_datos=True,
    )


def resumen() -> Viaje:
    """Lee las notas y los puntos y los resume. Sin red.

    Nunca lanza, por lo mismo que `metricas.obtener()`: es una fuente opcional
    del contexto y una base de datos vacía o una fila torcida no puede tumbar la
    conversación.
    """
    try:
        notas = [notes.to_public(f) for f in storage.list_notes()]
        puntos = storage.list_waypoints()
    except Exception:  # noqa: BLE001 - fuente opcional
        return Viaje()

    return armar(notas, puntos)
