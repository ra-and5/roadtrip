"""La ruta del viaje: notas y fotos ordenadas en el tiempo.

Función de entrada: `construir(notas, puntos) -> dict`, pura sobre datos ya
leídos. No toca la base de datos ni Flask, así que la parte interesante —el
orden, la distancia, los días— se prueba con listas escritas a mano.

Aquí se juntan las dos fuentes que **sí** son fiables:

- **Notas.** Lo que se escribió, con su sitio y su hora.
- **Fotos.** Cuándo y dónde se disparó cada una, sacado del EXIF sin subir la
  foto a ninguna parte.

Y una que no: la telemetría del iPhone sigue aparcada a la espera de demostrar
que llega sin huecos, así que **no entra en la ruta**. Cuando se cierre, será
una tercera lista que se mezcla aquí y ya está.

**Cómo se ordenan dos cosas medidas de forma distinta.** Una nota trae su
instante en UTC con el huso aparte; una foto trae la hora local de la cámara y
puede que sin huso ninguno. Para ponerlas en la misma línea se usa la **hora
local de cada una**, que además es la que se recuerda: "esa foto es de después
de comer". El precio, dicho claro: en un viaje que cruce husos horarios, dos
momentos de la misma tarde pueden quedar ordenados con el desfase entre zonas.
Para un viaje por el norte de España es exacto; para uno entre Canarias y
Cataluña habría que decidir otra cosa, y entonces habrá que mirar aquí.
"""

from __future__ import annotations

from datetime import date, datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any

from app.modules import miniaturas

RADIO_TIERRA_KM = 6371.0

# Salto entre dos puntos seguidos a partir del cual se deja de sumar como
# "trayecto". 300 km entre dos fotos consecutivas no es un tramo recorrido: es
# un vuelo, o dos viajes distintos importados juntos. Sumarlo daría un total
# espectacular y falso, que es peor que uno modesto y cierto.
MAX_SALTO_KM = 300.0


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta sobre la superficie de la Tierra (haversine).

    Se usa haversine y no la diferencia de grados porque un grado de longitud
    mide 111 km en el ecuador y 78 km en el norte de España: restar grados daría
    un 40 % de error justo en la zona del viaje.
    """
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return 2 * RADIO_TIERRA_KM * asin(min(1.0, sqrt(a)))


def _clave_local(iso: str | None) -> str | None:
    """La hora local en forma comparable: "2026-07-28T14:32:05", sin huso.

    Recortar el desfase no es perder información —se conserva en su columna—,
    es lo que permite comparar una nota con una foto en la misma escala.
    """
    if not isinstance(iso, str) or len(iso) < 19:
        return None
    return iso[:19]


def _momento_de_nota(nota: dict[str, Any]) -> dict[str, Any] | None:
    cuando = _clave_local(nota.get("created_at_local") or nota.get("created_at"))
    if cuando is None:
        return None
    return {
        "tipo": "nota",
        "cuando": cuando,
        "lat": nota.get("lat"),
        "lon": nota.get("lon"),
        "texto": nota.get("text"),
        "lugar": nota.get("place_name"),
        "region": nota.get("region"),
        "archivo": None,
        "altitud": None,
        "id": nota.get("id"),
    }


def _momento_de_punto(punto: dict[str, Any]) -> dict[str, Any] | None:
    cuando = _clave_local(punto.get("capturado_en"))
    if cuando is None:
        # Una foto sin fecha no se puede colocar en la línea del viaje. No se
        # tira: se cuenta aparte, porque "tengo 40 fotos que no sé cuándo se
        # hicieron" es información, y esconderlas haría creer que el viaje
        # está entero.
        return None
    return {
        "tipo": "foto",
        "cuando": cuando,
        "lat": punto.get("lat"),
        "lon": punto.get("lon"),
        "texto": None,
        "lugar": None,
        "region": None,
        "archivo": punto.get("archivo"),
        "altitud": punto.get("altitud"),
        "id": punto.get("id"),
        # El nombre de su miniatura, o `None` si esa foto no la tiene.
        #
        # Se resuelve preguntándole AL DISCO y no a una columna, porque no hay
        # columna: el nombre se deriva de `(fuente, archivo)` y el archivo existe
        # o no existe (ver `miniaturas.py`). Una columna podría afirmar que la
        # miniatura está cuando se perdió al desplegar, y entonces el diario
        # pintaría imágenes rotas sin que nada avisara.
        #
        # Y se decide aquí, en el servidor, en vez de dejar que el navegador pida
        # la imagen y trate el 404: así una foto sin miniatura no cuesta una
        # petición fallida por foto en una pantalla que se abre con mala
        # cobertura.
        "miniatura": _miniatura_de(punto),
    }


def _miniatura_de(punto: dict[str, Any]) -> str | None:
    """El nombre de la miniatura de este punto, si la tiene en disco."""
    archivo = punto.get("archivo")
    if not archivo:
        return None
    fuente = punto.get("fuente") or "fotos"
    if not miniaturas.existe(fuente, archivo):
        return None
    return miniaturas.nombre_de(fuente, archivo)


def _tramos(ubicados: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """Los saltos entre puntos consecutivos, sin los inverosímiles.

    Devuelve pares (punto de LLEGADA, km). Que la clave sea la llegada y no la
    salida es lo que hace que los kilómetros por día sumen el total: el tramo
    de las once de la noche de Cudillero a Laredo se le apunta al día en que se
    llegó, en vez de quedarse sin dueño entre dos jornadas.

    Existe como función aparte por una razón concreta: el total y el desglose
    por días se calculaban por separado y **no cuadraban** —el total incluía
    los tramos nocturnos y ningún día los contaba—. Dos números que no suman y
    no dan error son justo la clase de fallo silencioso que este proyecto trata
    de que sea imposible: ahora hay un solo sitio donde se decide qué es un
    tramo, y los dos se derivan de él.
    """
    tramos: list[tuple[dict[str, Any], float]] = []
    for anterior, siguiente in zip(ubicados, ubicados[1:]):
        km = distancia_km(
            anterior["lat"], anterior["lon"], siguiente["lat"], siguiente["lon"]
        )
        if km <= MAX_SALTO_KM:
            tramos.append((siguiente, km))
    return tramos


def _dia(momento: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(momento["cuando"]).date()
    except (ValueError, KeyError, TypeError):
        return None


def construir(
    notas: list[dict[str, Any]],
    puntos: list[dict[str, Any]],
    year: int | None = None,
) -> dict[str, Any]:
    """Mezcla notas y fotos en una sola línea de tiempo, y la mide.

    `year` filtra por el año LOCAL de cada momento. Las notas ya vienen
    filtradas por quien llama (tienen su propia función), pero los puntos no,
    y dejarlos sin filtrar mezclaría las fotos de todos los viajes en el mapa
    de uno solo.
    """
    if year is not None:
        puntos = [
            p for p in puntos
            if (c := _clave_local(p.get("capturado_en"))) and c[:4] == str(year)
        ]

    momentos = [m for m in (_momento_de_nota(n) for n in notas) if m]
    momentos += [m for m in (_momento_de_punto(p) for p in puntos) if m]
    # Empate a segundo: primero la nota. Si escribiste algo y disparaste una
    # foto en el mismo instante, lo que cuenta la historia es el texto.
    momentos.sort(key=lambda m: (m["cuando"], 0 if m["tipo"] == "nota" else 1))

    sin_fecha = sum(1 for p in puntos if not _clave_local(p.get("capturado_en")))
    sin_lugar = sum(1 for p in puntos if p.get("lat") is None)

    ubicados = [m for m in momentos if m["lat"] is not None and m["lon"] is not None]
    tramos = _tramos(ubicados)
    total_km = sum(km for _, km in tramos)
    saltos_ignorados = len(ubicados) - 1 - len(tramos) if len(ubicados) > 1 else 0

    dias = sorted({d for m in momentos if (d := _dia(m))})

    return {
        "momentos": momentos,
        "resumen": {
            "total": len(momentos),
            "notas": sum(1 for m in momentos if m["tipo"] == "nota"),
            "fotos": sum(1 for m in momentos if m["tipo"] == "foto"),
            "ubicados": len(ubicados),
            "primera": momentos[0]["cuando"] if momentos else None,
            "ultima": momentos[-1]["cuando"] if momentos else None,
            "dias": len(dias),
            # Distancia en línea recta entre puntos consecutivos: es un
            # **mínimo**, no los kilómetros del cuentakilómetros. Entre dos
            # fotos separadas por dos horas de carretera de montaña hay muchas
            # más curvas que la recta que las une. Se llama "en línea recta"
            # en la interfaz para no prometer lo que no es.
            "km_linea_recta": round(total_km, 1),
            "saltos_ignorados": saltos_ignorados,
            "fotos_sin_fecha": sin_fecha,
            "fotos_sin_lugar": sin_lugar,
        },
    }


def por_dias(momentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa la línea de tiempo en jornadas, que es como se recuerda un viaje.

    Nadie dice "el momento 47 del viaje"; se dice "el día que llegamos a
    Cudillero". Esta es la forma que hace que la ruta se pueda recorrer.

    Los kilómetros salen de los MISMOS tramos que el total (`_tramos`), así que
    la suma de los días es exactamente el total del viaje. Calcularlos aparte,
    día a día, era lo que hacía que no cuadrasen.
    """
    jornadas: dict[str, dict[str, Any]] = {}
    for momento in momentos:
        dia = _dia(momento)
        if dia is None:
            continue
        clave = dia.isoformat()
        jornada = jornadas.setdefault(
            clave, {"dia": clave, "momentos": [], "km_linea_recta": 0.0}
        )
        jornada["momentos"].append(momento)

    ubicados = [m for m in momentos if m["lat"] is not None and m["lon"] is not None]
    for llegada, km in _tramos(ubicados):
        dia = _dia(llegada)
        if dia is None:
            continue
        jornadas[dia.isoformat()]["km_linea_recta"] += km

    for jornada in jornadas.values():
        jornada["km_linea_recta"] = round(jornada["km_linea_recta"], 1)

    return [jornadas[k] for k in sorted(jornadas)]
