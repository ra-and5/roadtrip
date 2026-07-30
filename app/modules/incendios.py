"""Detecciones de calor por satélite (NASA FIRMS), interpretadas.

Función de entrada: `evaluar(csv, lat, lon) -> Situacion`.

**Por qué esto no se llama "incendios" en la pantalla.** VIIRS y MODIS no ven
fuego: ven **anomalías térmicas**. Un horno cerámico, una antorcha de refinería,
una quema agrícola y un incendio forestal salen los cuatro como un punto en el
CSV. Medido con datos reales el 30-07-2026 a 2 km de San Vicente del Raspeig:
dos detecciones nocturnas de 0,62 y 1,85 MW, que son casi con seguridad
industria. Escribir "incendio a 2 km" con eso es la alarma que se aprende a
ignorar — y entonces tampoco se lee el día que arde el monte de al lado.

Así que el veredicto se calcula aquí, en Python y con umbrales razonados
(decisión 5, la misma que el oleaje y la luna), y elige las palabras según la
**potencia radiativa** (FRP, en megavatios) y la distancia. Nunca afirma más de
lo que el dato aguanta.

**Quién hace la petición, que es lo raro de este módulo.** No la hace el
servidor: `firms.modaps.eosdis.nasa.gov` **no está en la lista blanca** del
proxy de PythonAnywhere (comprobado sobre la página de la lista, decisión 21),
así que desde producción la llamada devolvería un 403 del proxy y la app lo
leería como "fuente caída". Lo que sí se puede es pedirlo desde el **navegador**:
FIRMS responde con `access-control-allow-origin: *` (comprobado con `curl -D -`).

De ahí el reparto: el navegador trae el CSV crudo y este módulo lo interpreta.
El navegador es una tubería, no un cerebro — si el veredicto viviera en el
JavaScript no habría forma de probarlo sin abrir un navegador, y los umbrales
acabarían dispersos. El día que el dominio entre en la lista blanca, se añade
aquí un `consultar()` que haga la petición y **no cambia nada más**: las
funciones puras siguen siendo las mismas.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

__all__ = [
    "CAJA_ESPANA", "Deteccion", "GRADOS_ALREDEDOR", "GRADOS_MAPA", "MAX_DIAS",
    "MAX_EN_EL_MAPA", "SENSOR", "Situacion", "URL_BASE", "evaluar", "para_el_mapa",
    "parsear", "url_de_area", "url_de_consulta",
]


class IncendioError(Exception):
    """El CSV de FIRMS no se pudo leer."""


# El endpoint `area`: rectángulo, sensor y días hacia atrás. VIIRS de 375 m y no
# MODIS de 1 km: la resolución importa cuando la pregunta es "¿está cerca de
# donde voy a dormir?".
URL_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SENSOR = "VIIRS_SNPP_NRT"

# Radio de la caja de búsqueda, en grados. 0,5° son unos 55 km de norte a sur;
# más ancho traería detecciones de las que no hay nada que hacer y menos dejaría
# fuera un fuego al que le da el viento.
GRADOS_ALREDEDOR = 0.5

# El del MAPA es mucho mayor, y responde a otra pregunta. La tarjeta de Inicio
# contesta "¿me tengo que preocupar aquí?"; el mapa contesta "¿hacia dónde me
# muevo y hacia dónde no?", y eso no se decide con 55 km. 3° son unos 330 km de
# norte a sur: una jornada de camper.
GRADOS_MAPA = 3.0

# El rango de días que acepta FIRMS. Comprobado contra la API: con 7 devuelve
# "Invalid day range. Expects [1..5]." y lo manda con **HTTP 200**, como todos
# sus errores. Sin este tope, el desplegable ofrecería una opción que siempre
# falla (decisión 5).
MAX_DIAS = 5

# El país entero, para la vista de "¿por dónde NO paso?". Península y Baleares;
# Canarias queda fuera a propósito — meterlas en la misma caja obligaría a
# pedir un rectángulo del Atlántico entero para traer cuatro islas, y el mapa
# saldría con España del tamaño de un sello. Medido: 3 días de España son 986
# filas y 80 KB de CSV.
CAJA_ESPANA = (-10.0, 35.5, 5.0, 44.5)   # oeste, sur, este, norte

# Techo de detecciones que se devuelven al mapa. En un agosto malo, España
# entera trae miles; el navegador de un móvil no pinta miles de círculos sin
# atragantarse. Cuando sobra, se recortan **las más flojas**: un punto de 1 MW
# a 5 km ya lo cubre la tarjeta de Inicio, y en un mapa de país lo que hay que
# ver es dónde están los grandes.
MAX_EN_EL_MAPA = 600

# A partir de aquí una detección deja de parecer industria. La potencia
# radiativa de un horno o una antorcha se mide en unidades o pocas decenas de
# MW y es constante; un incendio forestal declarado pasa de cien con facilidad.
# El umbral es deliberadamente bajo: equivocarse hacia el lado seguro importa
# más aquí que en ningún otro veredicto, porque al otro lado hay alguien
# durmiendo en un camper.
FRP_LLAMATIVA_MW = 20.0

# Hasta dónde se considera "aquí al lado". 15 km es lo que un frente puede
# recorrer en unas horas con viento, y lo que separa "vigílalo" de "cámbiate de
# sitio".
KM_CERCA = 15.0


@dataclass(frozen=True)
class Deteccion:
    """Un punto caliente visto por el satélite. NO es "un incendio"."""

    lat: float
    lon: float
    fecha: str            # "2026-07-30", en UTC, tal y como lo da FIRMS
    hora: str             # "0158" UTC; FIRMS lo manda sin los dos puntos
    frp_mw: float         # potencia radiativa: lo único que habla de intensidad
    confianza: str        # VIIRS: "l" baja, "n" nominal, "h" alta
    de_noche: bool
    distancia_km: float = 0.0
    # Horas desde que el satélite lo vio. Se calcula en el servidor, que es
    # quien sabe qué hora es en UTC: en el navegador habría que reconstruir el
    # instante desde `acq_date` + `acq_time` ("0158", sin dos puntos) y esa
    # aritmética repetida en JavaScript es justo donde se cuela un desfase que
    # no da ningún error, solo colores equivocados.
    horas: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lat": self.lat, "lon": self.lon, "fecha": self.fecha,
            "hora": self.hora, "frp_mw": self.frp_mw, "confianza": self.confianza,
            "de_noche": self.de_noche, "distancia_km": round(self.distancia_km, 1),
            "horas": round(self.horas, 1) if self.horas is not None else None,
        }


@dataclass(frozen=True)
class Situacion:
    """Lo que se puede afirmar, y con qué palabras."""

    hay_algo: bool = False
    cuantas: int = 0
    mas_cercana_km: float | None = None
    frp_maxima_mw: float | None = None
    # El texto que se enseña. Se compone aquí y no en la plantilla para que la
    # frase y los umbrales que la justifican vivan en el mismo sitio.
    veredicto: str = ""
    detalle: str = ""
    detecciones: list[Deteccion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hay_algo": self.hay_algo,
            "cuantas": self.cuantas,
            "mas_cercana_km": (
                round(self.mas_cercana_km, 1) if self.mas_cercana_km is not None else None
            ),
            "frp_maxima_mw": self.frp_maxima_mw,
            "veredicto": self.veredicto,
            "detalle": self.detalle,
            "detecciones": [d.to_dict() for d in self.detecciones],
        }


def _instante(fecha: str, hora: str) -> datetime | None:
    """El instante UTC de una detección, desde `acq_date` y `acq_time`.

    FIRMS manda la hora como un número sin dos puntos y **sin ceros a la
    izquierda**: las 01:58 llegan como "158". Tratarlo como texto de cuatro
    dígitos sin rellenar daría las 15:8, que no existe — y la detección
    aparecería con doce horas de antigüedad equivocada, o sea del color que no
    es. Lo mismo que hace el propio tutorial de la NASA con `zfill(4)`.
    """
    if not fecha:
        return None
    try:
        return datetime.strptime(
            f"{fecha} {str(hora or '0').zfill(4)}", "%Y-%m-%d %H%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def url_de_area(clave: str, caja: tuple[float, float, float, float], dias: int = 1) -> str:
    """La URL para un rectángulo dado: (oeste, sur, este, norte)."""
    oeste, sur, este, norte = caja
    dias = max(1, min(int(dias), MAX_DIAS))
    return f"{URL_BASE}/{clave}/{SENSOR}/{oeste:.4f},{sur:.4f},{este:.4f},{norte:.4f}/{dias}"


def url_de_consulta(
    clave: str, lat: float, lon: float, dias: int = 1, grados: float = GRADOS_ALREDEDOR
) -> str:
    """La URL que tiene que pedir el navegador.

    Se compone en el servidor y no en el JavaScript por un motivo concreto: así
    el sensor, el radio y los días son **una sola definición**. Repartidos entre
    Python y JavaScript, cambiar el radio en un sitio y no en el otro daría una
    caja de búsqueda distinta de la que dicen los tests, sin ningún error.
    """
    return url_de_area(
        clave, (lon - grados, lat - grados, lon + grados, lat + grados), dias
    )


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine. Restar grados daría un 40 % de error en estas latitudes."""
    from math import asin, cos, radians, sin, sqrt

    d_lat, d_lon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def parsear(
    texto: str, lat: float, lon: float, *, ahora: datetime | None = None
) -> list[Deteccion]:
    """Convierte el CSV de FIRMS en detecciones, ordenadas por cercanía.

    Tolera que falten columnas: FIRMS ha cambiado el juego de campos entre
    versiones y una columna nueva no puede tumbar la pantalla. Lo que NO se
    tolera es inventarse un valor: sin `frp` la detección se queda a 0 y el
    veredicto la tratará como poca cosa, que es lo prudente.

    Raises:
        IncendioError: el cuerpo no es el CSV que dice ser. FIRMS contesta con
            texto plano también cuando la clave es inválida ("Invalid MAP_KEY"),
            y eso llega con **HTTP 200**: como la API marina de Open-Meteo
            (decisión 5), un 200 no significa que la respuesta sirva.
    """
    texto = (texto or "").strip()
    if not texto:
        raise IncendioError("FIRMS no devolvió nada.")

    primera = texto.splitlines()[0].lower()
    if "latitude" not in primera:
        # El cuerpo se recorta: puede traer una página entera de error.
        raise IncendioError(f"FIRMS no devolvió un CSV de detecciones: {texto[:120]!r}")

    ahora = ahora or datetime.now(timezone.utc)

    detecciones: list[Deteccion] = []
    for fila in csv.DictReader(io.StringIO(texto)):
        try:
            punto_lat = float(fila["latitude"])
            punto_lon = float(fila["longitude"])
        except (KeyError, TypeError, ValueError):
            continue

        try:
            frp = float(fila.get("frp") or 0.0)
        except ValueError:
            frp = 0.0

        fecha = str(fila.get("acq_date") or "")
        hora = str(fila.get("acq_time") or "")
        visto = _instante(fecha, hora)

        detecciones.append(Deteccion(
            lat=punto_lat,
            lon=punto_lon,
            fecha=fecha,
            hora=hora,
            frp_mw=frp,
            confianza=str(fila.get("confidence") or "").strip().lower(),
            de_noche=str(fila.get("daynight") or "").strip().upper() == "N",
            distancia_km=_km(lat, lon, punto_lat, punto_lon),
            horas=(ahora - visto).total_seconds() / 3600 if visto else None,
        ))

    detecciones.sort(key=lambda d: d.distancia_km)
    return detecciones


def para_el_mapa(
    texto: str, lat: float, lon: float, *, ahora: datetime | None = None
) -> list[Deteccion]:
    """Todas las detecciones, para pintarlas. Función PURA.

    Distinta de `evaluar()` a propósito: aquella responde "¿me tengo que
    preocupar aquí?" y se queda con las diez más cercanas; esta responde "¿hacia
    dónde me muevo?" y las quiere todas, porque un frente a 200 km es
    exactamente lo que hay que ver para decidir la ruta del día.

    Si hay más de `MAX_EN_EL_MAPA` se recortan **las más flojas**, no las más
    lejanas: en un mapa de país la pregunta es dónde están los focos grandes, y
    un punto de 1 MW al lado de casa ya sale en la tarjeta de Inicio. Recortar
    por orden de llegada del CSV dejaría fuera un incendio por casualidad, que
    es la única detección que no puede faltar.

    Se devuelven ordenadas por cercanía igual que `parsear()`, para que quien
    pinte no tenga que volver a ordenarlas.
    """
    detecciones = parsear(texto, lat, lon, ahora=ahora)
    if len(detecciones) <= MAX_EN_EL_MAPA:
        return detecciones

    fuertes = sorted(detecciones, key=lambda d: d.frp_mw, reverse=True)[:MAX_EN_EL_MAPA]
    return sorted(fuertes, key=lambda d: d.distancia_km)


def evaluar(texto: str, lat: float, lon: float, *, hoy: date | None = None) -> Situacion:
    """Qué se puede decir de esas detecciones. Función PURA.

    Tres tramos, y las palabras cambian con la potencia y no solo con la
    distancia, que es lo que separa un aviso útil de una alarma que se ignora:

      - nada en el radio                    -> se dice que está limpio
      - detecciones flojas (FRP baja)       -> se nombran como puntos de calor y
                                               se avisa de que suelen ser
                                               industria o quemas
      - alguna llamativa y cerca            -> se dice claramente que hay que
                                               informarse antes de dormir ahí

    `hoy` no se usa para filtrar: FIRMS ya devuelve solo el rango pedido. Está
    para poder fechar el resumen sin leer el reloj dentro de una función pura.
    """
    detecciones = parsear(texto, lat, lon)

    if not detecciones:
        return Situacion(
            hay_algo=False,
            veredicto="Sin detecciones de calor por satélite en 50 km.",
            detalle="Últimas 24 h, sensor VIIRS (375 m). No detecta fuegos pequeños ni de noche bajo nubes.",
        )

    cercana = detecciones[0]
    frp_maxima = max(d.frp_mw for d in detecciones)
    llamativas = [d for d in detecciones if d.frp_mw >= FRP_LLAMATIVA_MW]

    if llamativas and llamativas[0].distancia_km <= KM_CERCA:
        veredicto = (
            f"Foco activo a {llamativas[0].distancia_km:.0f} km "
            f"({llamativas[0].frp_mw:.0f} MW)."
        )
        detalle = (
            "Potencia alta y cerca: infórmate antes de quedarte a dormir aquí. "
            "Mira los avisos de Protección Civil de la zona."
        )
    elif llamativas:
        veredicto = (
            f"Foco activo a {llamativas[0].distancia_km:.0f} km "
            f"({llamativas[0].frp_mw:.0f} MW), lejos."
        )
        detalle = "No es tu zona inmediata, pero conviene mirar el viento."
    else:
        veredicto = (
            f"{len(detecciones)} punto{'s' if len(detecciones) > 1 else ''} de calor, "
            f"el más cercano a {cercana.distancia_km:.0f} km."
        )
        detalle = (
            f"Potencia baja (máximo {frp_maxima:.1f} MW): el satélite marca también "
            "hornos, industria y quemas agrícolas. Casi nunca es un incendio."
        )

    return Situacion(
        hay_algo=True,
        cuantas=len(detecciones),
        mas_cercana_km=cercana.distancia_km,
        frp_maxima_mw=round(frp_maxima, 2),
        veredicto=veredicto,
        detalle=detalle,
        # Solo las diez más cercanas: la pantalla no puede enseñar doscientas y
        # el resto no cambia ninguna decisión.
        detecciones=detecciones[:10],
    )
