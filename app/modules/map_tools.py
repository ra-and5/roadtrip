"""Herramientas para el chat: sitios, rutas, memoria y lecturas del contexto.

La IA no debe inventar si un bar está cerca ni cuánto se tarda entre dos
ciudades. Este módulo separa tres cosas:

  - detectar cuándo una pregunta necesita una herramienta;
  - consultar una fuente concreta, con caché y errores legibles;
  - renderizar un bloque compacto para el prompt.

No hay llamadas desde aquí al LLM. Es una capa determinista y testeable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import requests

from app.config import Config
from app.modules import aemet, storage
from app.modules.location_context import Place, validate_coords

_TOOLS_CACHE_TTL = 30 * 60
_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_MAX_CONSULTAS_SITIOS = 3

# El tráfico se cachea MUCHO menos que un sitio. Un bar sigue donde estaba
# dentro de media hora; una retención, no. Cinco minutos es el compromiso entre
# no mentir y no pagar una llamada por cada pregunta del hilo.
_TRAFICO_CACHE_TTL = 5 * 60

# Cuántas horas de salida se evalúan como mucho. Cada una es UNA llamada a la
# API que se paga, así que el límite es parte del contrato de coste de este
# módulo, igual que `VENTANA_HISTORIAL` en `chat.py` (decisión 37). Va con
# nombre y con test propio porque si se rompe no falla nada: solo sube la
# factura, que es lo último que alguien mira.
MAX_HORAS_SALIDA = 4

# --- Umbrales del veredicto de tráfico ---
#
# Se calculan aquí y NO se le preguntan al modelo, por lo mismo que el oleaje y
# la luna (decisión 5): es una regla explícita, determinista y testeable, y el
# modelo recibe el veredicto ya hecho.
#
# Son proporciones sobre el tiempo en marcha libre y no minutos absolutos: diez
# minutos de más en un trayecto de dos horas es ruido, y en uno de quince
# minutos es que está parado.
_UMBRAL_DENSO = 0.12
_UMBRAL_ATASCO = 0.30

# Y un suelo absoluto, porque una proporción sola miente en los trayectos
# cortos: un 30 % de un trayecto de 6 minutos son 2 minutos, que es un semáforo,
# no un atasco.
_RETRASO_MINIMO_MIN = 4


class ToolError(Exception):
    """La herramienta no pudo mirar lo que se le pidió."""


@dataclass(frozen=True)
class ToolPlace:
    nombre: str
    tipo: str = ""
    direccion: str = ""
    lat: float | None = None
    lon: float | None = None
    rating: float | None = None
    abierto: str = ""
    maps_url: str = ""
    fuente: str = "google_places"


@dataclass(frozen=True)
class ToolRoute:
    origen: str
    destino: str
    distancia_km: float | None = None
    duracion_min: int | None = None
    duracion_trafico_min: int | None = None
    maps_url: str = ""
    fuente: str = "google_routes"
    # --- Tráfico ---
    # `estado` tiene cuatro valores y `sin_datos` NO es `fluido`: si Google no
    # devuelve el tiempo en marcha libre no se puede saber si hay retención, y
    # decir que la carretera está bien sin haberlo mirado es el fallo del que
    # avisa la decisión 22. Es el mismo vocabulario de `contexto.Fuente`.
    retraso_min: int | None = None
    estado: str = "sin_datos"        # fluido | denso | atasco | sin_datos
    motivo: str = ""
    # Cuántos tramos de la ruta van lentos o parados. Se cuentan TRAMOS y no
    # kilómetros a propósito: Google los da por índice de punto de la polilínea,
    # y los puntos no están repartidos a distancias iguales, así que convertirlo
    # a km sería inventarse una cifra convincente (decisión 11).
    tramos_lentos: int = 0
    tramos_parados: int = 0


@dataclass(frozen=True)
class Salida:
    """Una hora de salida evaluada, para responder «¿a qué hora me voy?»."""

    hora: str            # HH:MM en hora local del viajero
    duracion_min: int
    retraso_min: int


@dataclass(frozen=True)
class ToolHorarios:
    origen: str
    destino: str
    opciones: tuple[Salida, ...] = ()

    def mejor(self) -> Salida | None:
        """La salida más rápida. Empate: la más temprana, que ya viene ordenada."""
        return min(self.opciones, key=lambda s: s.duracion_min, default=None)


@dataclass(frozen=True)
class ToolBundle:
    """Lo que se añade al prompt."""

    sitios: list[ToolPlace] = field(default_factory=list)
    ruta: ToolRoute | None = None
    horarios: ToolHorarios | None = None
    memoria: list[str] = field(default_factory=list)
    lecturas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def hay_algo(self) -> bool:
        return bool(
            self.sitios
            or self.ruta
            or self.horarios
            or self.memoria
            or self.lecturas
            or self.avisos
        )


class MapsProvider(Protocol):
    def buscar_sitios(self, consulta: str, lat: float, lon: float) -> list[ToolPlace]:
        ...

    def calcular_ruta(
        self, origen: str, destino: str, salida: datetime | None = None
    ) -> ToolRoute:
        ...

    def horas_de_salida(
        self, origen: str, destino: str, ahora: datetime, horas: int = ...
    ) -> ToolHorarios:
        ...


_TIPOS_GOOGLE: dict[str, tuple[str, ...]] = {
    "bar": ("bar", "cafe"),
    "cafe": ("cafe", "bar"),
    "restaurante": ("restaurant",),
    "supermercado": ("supermarket",),
    "farmacia": ("pharmacy",),
    "gasolinera": ("gas_station",),
    "lavanderia": ("laundry",),
    "camping": ("campground", "rv_park"),
    "area camper": ("rv_park", "campground"),
    "parking": ("parking",),
    "mirador": ("tourist_attraction",),
}

_PATRONES_SITIOS = (
    ("area camper", re.compile(r"\b(área|area).{0,18}\b(camper|autocaravana|pernocta)\b", re.I)),
    ("bar", re.compile(r"\b(bar|bares|tomar algo|cerveza)\b", re.I)),
    ("cafe", re.compile(r"\b(caf[eé]|cafeter[ií]a)\b", re.I)),
    ("restaurante", re.compile(r"\b(restaurante|cenar|comer)\b", re.I)),
    ("supermercado", re.compile(r"\b(supermercado|s[uú]per|comprar comida)\b", re.I)),
    ("farmacia", re.compile(r"\b(farmacia)\b", re.I)),
    ("gasolinera", re.compile(r"\b(gasolinera|repostar|gas[oó]leo|gasolina)\b", re.I)),
    ("lavanderia", re.compile(r"\b(lavander[ií]a|lavar ropa)\b", re.I)),
    ("camping", re.compile(r"\b(camping|campamento)\b", re.I)),
    ("parking", re.compile(r"\b(parking|aparcamiento|aparcar)\b", re.I)),
    ("mirador", re.compile(r"\b(mirador|vistas|atardecer|amanecer)\b", re.I)),
)

_PATRON_RUTA = re.compile(
    r"\b(?:de|desde)\s+(.+?)\s+(?:a|hasta)\s+(.+?)(?:\?|$|\s+en\s+coche|\s+por\s+carretera)",
    re.I,
)
_PATRON_RUTA_DESDE_AQUI = re.compile(
    r"\b(?:a|hasta)\s+(.+?)(?:\?|$|\s+en\s+coche|\s+por\s+carretera)",
    re.I,
)
_PALABRAS_RUTA = re.compile(
    r"\b(cu[aá]nto tardo|cuanto tardo|se tarda|tardar[ié]a|ruta|llegar|distancia|"
    r"tr[aá]fico|atasco|atascos|retenci[oó]n|retenciones|carretera|conducir|"
    r"salgo|salir|me voy|nos vamos)\b",
    re.I,
)
# «¿A qué hora salgo?» es otra pregunta que «¿cuánto tardo?», y cuesta cuatro
# llamadas en vez de una. Por eso se detecta aparte y no se dispara sola.
_PALABRAS_MEJOR_HORA = re.compile(
    r"\b(a qu[eé] hora|mejor hora|cu[aá]ndo salgo|cuando salgo|cu[aá]ndo me voy|"
    r"evitar (?:el )?(?:atasco|tr[aá]fico)|mejor momento)\b",
    re.I,
)
_PALABRAS_TRAFICO = re.compile(
    r"\b(tr[aá]fico|atasco|atascos|retenci[oó]n|retenciones|caravana|"
    r"cortad[ao]|corte|accidente|obras|incidencia)\b",
    re.I,
)
_PALABRAS_MEMORIA = re.compile(
    r"\b(ayer|dorm[ií]|dormimos|nota|notas|foto|fotos|estuve|pas[eé]|viaje|recuerdo)\b",
    re.I,
)
_PATRONES_PLANES: tuple[tuple[tuple[str, ...], re.Pattern[str]], ...] = (
    (
        ("area camper", "camping", "supermercado"),
        re.compile(r"\b(dormir|ducharme|pernoctar|autocaravana|camper|camping)\b", re.I),
    ),
    (
        ("restaurante", "bar", "cafe"),
        re.compile(r"\b(plan|planes|qu[eé] hago|hacer cerca|algo cerca|comer|cenar)\b", re.I),
    ),
    (
        ("parking", "mirador", "bar"),
        re.compile(r"\b(aparc|atardecer|amanecer|paseo tranquilo|vistas)\b", re.I),
    ),
    (
        ("gasolinera", "supermercado"),
        re.compile(r"\b(repostar|comprar|provisiones|agua|hielo|gasolina|gas[oó]leo)\b", re.I),
    ),
)
_PALABRAS_AGUA = re.compile(r"\b(paddle|surf|tabla|kayak|mar|playa|ba[ñn]o|bañar)\b", re.I)
_PALABRAS_TERRITORIO = re.compile(
    r"\b("
    r"españa|pais|pa[ií]s|territorio|nacional|pen[ií]nsula|avisos?|alertas?|"
    r"aemet|radar|tormentas?|lluvia|nieve|calor|viento|temporal|zonas? mal|"
    r"d[oó]nde est[aá] peor|d[oó]nde llueve"
    r")\b",
    re.I,
)
_PALABRAS_RADAR = re.compile(r"\b(radar|lluvia|tormenta|precipitaci[oó]n)\b", re.I)


def _segundos_a_minutos(valor: str | None) -> int | None:
    if not valor or not valor.endswith("s"):
        return None
    try:
        return max(1, round(float(valor[:-1]) / 60))
    except ValueError:
        return None


def _cache_key(prefix: str, *parts: object) -> str:
    normalizados = [str(p).strip().lower() for p in parts]
    return prefix + ":" + "|".join(normalizados)


def _rfc3339(momento: datetime) -> str:
    """La hora como la quiere Google: RFC 3339 y en UTC.

    Un `datetime` sin zona se trata como local del viajero y no como UTC: aquí
    se recibe la hora que da `contexto.Momento`, que ya viene con la zona del
    sitio donde estás. Mandar una hora ingenua como si fuera UTC desplazaría la
    predicción dos horas en verano — y no daría ningún error, solo el tráfico de
    otro momento (es la trampa del huso de las decisiones 25 y 30).
    """
    if momento.tzinfo is None:
        momento = momento.astimezone()
    return momento.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def veredicto_trafico(
    duracion_min: int | None, libre_min: int | None
) -> tuple[str, str, int | None]:
    """¿Hay retención? Devuelve (estado, motivo, retraso en minutos).

    Función pura y con test propio, no tres `if` dentro del parseo: es lo único
    de esta herramienta que DECIDE algo, y un veredicto equivocado no da ningún
    error — solo hace que el modelo diga que la carretera está despejada cuando
    está parada. Es la misma razón por la que `water_sports()` vive en Python
    (decisión 5).

    `sin_datos` cuando falta cualquiera de los dos tiempos. Es el caso que hay
    que respetar: sin el tiempo en marcha libre no hay con qué comparar, y
    devolver `fluido` ahí sería tranquilizar sin haber mirado (decisión 22).
    """
    if duracion_min is None or libre_min is None or libre_min <= 0:
        return "sin_datos", "No se pudo comparar con el tiempo sin tráfico.", None

    retraso = duracion_min - libre_min

    # Ir MÁS rápido que el tiempo teórico en marcha libre es normal de
    # madrugada; no es un dato raro que haya que reportar como algo.
    if retraso < _RETRASO_MINIMO_MIN:
        return "fluido", "Sin retenciones apreciables.", max(retraso, 0)

    proporcion = retraso / libre_min
    if proporcion >= _UMBRAL_ATASCO:
        return "atasco", f"{retraso} min de más sobre {libre_min} min sin tráfico.", retraso
    if proporcion >= _UMBRAL_DENSO:
        return "denso", f"{retraso} min de más sobre {libre_min} min sin tráfico.", retraso
    return "fluido", "Sin retenciones apreciables.", retraso


def _contar_tramos(ruta: dict[str, Any]) -> tuple[int, int]:
    """Cuántos tramos van lentos y cuántos parados, según la polilínea.

    Se miran los dos sitios donde Google los puede poner —la ruta entera y cada
    tramo— porque devuelve uno u otro según lo que se le pida, y leer solo uno
    daría cero tramos en la mitad de las respuestas sin dar ningún error.
    """
    intervalos: list[dict[str, Any]] = []
    advisory = ruta.get("travelAdvisory")
    if isinstance(advisory, dict):
        intervalos.extend(advisory.get("speedReadingIntervals") or [])
    for leg in ruta.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        leg_advisory = leg.get("travelAdvisory")
        if isinstance(leg_advisory, dict):
            intervalos.extend(leg_advisory.get("speedReadingIntervals") or [])

    lentos = sum(1 for i in intervalos if isinstance(i, dict) and i.get("speed") == "SLOW")
    parados = sum(
        1 for i in intervalos if isinstance(i, dict) and i.get("speed") == "TRAFFIC_JAM"
    )
    return lentos, parados


class GoogleMapsProvider:
    """Cliente mínimo de Google Places/Routes, detrás de una interfaz propia."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else Config.GOOGLE_MAPS_API_KEY

    def _require_key(self) -> str:
        if not self.api_key:
            raise ToolError("GOOGLE_MAPS_API_KEY no está configurada.")
        return self.api_key

    def buscar_sitios(self, consulta: str, lat: float, lon: float) -> list[ToolPlace]:
        lat, lon = validate_coords(lat, lon)
        tipos = _TIPOS_GOOGLE.get(consulta, (consulta,))
        key = _cache_key("google_places", consulta, round(lat, 3), round(lon, 3))
        cached = storage.cache_get(key, _TOOLS_CACHE_TTL)
        if cached is None:
            body = {
                "includedTypes": list(tipos[:3]),
                "maxResultCount": 6,
                "rankPreference": "DISTANCE",
                "languageCode": "es",
                "regionCode": "ES",
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": 3500.0,
                    }
                },
            }
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._require_key(),
                "X-Goog-FieldMask": (
                    "places.displayName,places.formattedAddress,places.location,"
                    "places.primaryType,places.rating,places.currentOpeningHours,"
                    "places.googleMapsLinks"
                ),
            }
            try:
                response = requests.post(_PLACES_URL, json=body, headers=headers, timeout=Config.HTTP_TIMEOUT)
                response.raise_for_status()
                cached = response.json()
            except requests.Timeout as exc:
                raise ToolError("Google Places tardó demasiado.") from exc
            except requests.RequestException as exc:
                raise ToolError("No se pudo consultar Google Places.") from exc
            except ValueError as exc:
                raise ToolError("Google Places devolvió una respuesta ilegible.") from exc
            storage.cache_set(key, cached)
        return _parse_places(cached)

    def calcular_ruta(
        self, origen: str, destino: str, salida: datetime | None = None
    ) -> ToolRoute:
        origen = origen.strip()
        destino = destino.strip()
        if not origen or not destino:
            raise ToolError("Falta origen o destino para calcular la ruta.")

        # La hora de salida ENTRA en la clave de caché, y no es un detalle: sin
        # ella, preguntar "¿y si salgo a las 8?" devolvería la respuesta que se
        # cacheó para "¿y si salgo a las 20?". No daría ningún error, solo las
        # mismas cifras para todas las horas — y el usuario decidiría a qué hora
        # salir con un dato que no es de esa hora (decisión 11, la misma razón
        # por la que la caché de recomendaciones lleva proveedor y modelo).
        marca = salida.isoformat(timespec="hours") if salida else "ahora"
        key = _cache_key("google_routes", origen, destino, marca)
        cached = storage.cache_get(key, _TRAFICO_CACHE_TTL)
        if cached is None:
            body: dict[str, Any] = {
                "origin": {"address": origen},
                "destination": {"address": destino},
                "travelMode": "DRIVE",
                # OPTIMAL en vez de TRAFFIC_AWARE: es el que de verdad mira el
                # tráfico tramo a tramo. Cuesta algo más de latencia y es lo que
                # hace que `speedReadingIntervals` traiga algo.
                "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
                "languageCode": "es-ES",
                "regionCode": "ES",
                "units": "METRIC",
                "extraComputations": ["TRAFFIC_ON_POLYLINE"],
            }
            if salida is not None:
                body["departureTime"] = _rfc3339(salida)
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._require_key(),
                "X-Goog-FieldMask": (
                    "routes.duration,routes.staticDuration,routes.distanceMeters,"
                    "routes.travelAdvisory.speedReadingIntervals,"
                    "routes.legs.travelAdvisory.speedReadingIntervals"
                ),
            }
            try:
                response = requests.post(_ROUTES_URL, json=body, headers=headers, timeout=Config.HTTP_TIMEOUT)
                response.raise_for_status()
                cached = response.json()
            except requests.Timeout as exc:
                raise ToolError("Google Routes tardó demasiado.") from exc
            except requests.RequestException as exc:
                raise ToolError("No se pudo consultar Google Routes.") from exc
            except ValueError as exc:
                raise ToolError("Google Routes devolvió una respuesta ilegible.") from exc
            storage.cache_set(key, cached)
        return _parse_route(cached, origen, destino)

    def horas_de_salida(
        self, origen: str, destino: str, ahora: datetime, horas: int = MAX_HORAS_SALIDA
    ) -> ToolHorarios:
        """Cuánto se tarda saliendo ahora, dentro de 1 h, de 2 h…

        Es la única forma honesta de contestar «¿a qué hora salgo?»: Google
        predice el tráfico para una hora de salida dada, así que se le pregunta
        varias veces y se comparan. Una sola consulta no puede responderlo.

        Se acota a `MAX_HORAS_SALIDA` porque cada hora es una llamada de pago.
        """
        opciones: list[Salida] = []
        for adelanto in range(min(horas, MAX_HORAS_SALIDA)):
            # Dos minutos de margen sobre "ahora": Google RECHAZA una hora de
            # salida en el pasado, y la primera opción llegaba justo tarde
            # —entre construir la petición y que la reciba— así que se perdía
            # una de las cuatro sin decir por qué.
            cuando = ahora + timedelta(hours=adelanto, minutes=2)
            try:
                ruta = self.calcular_ruta(origen, destino, salida=cuando)
            except ToolError:
                # Una hora que falla no tumba las demás: es el mismo criterio
                # que una muestra inválida en la ingesta (decisión 23). Con tres
                # de cuatro horas ya se puede recomendar cuándo salir.
                continue
            if ruta.duracion_trafico_min is None:
                continue
            opciones.append(
                Salida(
                    hora=cuando.strftime("%H:%M"),
                    duracion_min=ruta.duracion_trafico_min,
                    retraso_min=ruta.retraso_min or 0,
                )
            )
        return ToolHorarios(origen=origen, destino=destino, opciones=tuple(opciones))


def _parse_places(payload: dict[str, Any]) -> list[ToolPlace]:
    lugares = payload.get("places") if isinstance(payload, dict) else None
    if not isinstance(lugares, list):
        return []
    salida: list[ToolPlace] = []
    for item in lugares:
        if not isinstance(item, dict):
            continue
        nombre = ((item.get("displayName") or {}).get("text") or "").strip()
        if not nombre:
            continue
        loc = item.get("location") or {}
        opening = item.get("currentOpeningHours") or {}
        links = item.get("googleMapsLinks") or {}
        abierto = ""
        if opening.get("openNow") is True:
            abierto = "abierto ahora"
        elif opening.get("openNow") is False:
            abierto = "cerrado ahora"
        salida.append(
            ToolPlace(
                nombre=nombre,
                tipo=str(item.get("primaryType") or ""),
                direccion=str(item.get("formattedAddress") or ""),
                lat=loc.get("latitude"),
                lon=loc.get("longitude"),
                rating=item.get("rating"),
                abierto=abierto,
                maps_url=str(links.get("placeUri") or ""),
            )
        )
    return salida


def _parse_route(payload: dict[str, Any], origen: str, destino: str) -> ToolRoute:
    rutas = payload.get("routes") if isinstance(payload, dict) else None
    if not rutas:
        raise ToolError("Google Routes no encontró ruta.")
    ruta = rutas[0]
    metros = ruta.get("distanceMeters")
    distancia = round(float(metros) / 1000, 1) if isinstance(metros, (int, float)) else None
    maps_url = (
        "https://www.google.com/maps/dir/?api=1&origin="
        + requests.utils.quote(origen)
        + "&destination="
        + requests.utils.quote(destino)
    )
    libre = _segundos_a_minutos(ruta.get("staticDuration"))
    con_trafico = _segundos_a_minutos(ruta.get("duration"))
    estado, motivo, retraso = veredicto_trafico(con_trafico, libre)
    lentos, parados = _contar_tramos(ruta)

    return ToolRoute(
        origen=origen,
        destino=destino,
        distancia_km=distancia,
        duracion_min=libre,
        duracion_trafico_min=con_trafico,
        maps_url=maps_url,
        retraso_min=retraso,
        estado=estado,
        motivo=motivo,
        tramos_lentos=lentos,
        tramos_parados=parados,
    )


def detectar_sitio(pregunta: str) -> str | None:
    for nombre, patron in _PATRONES_SITIOS:
        if patron.search(pregunta):
            return nombre
    return None


def detectar_consultas_sitios(pregunta: str) -> list[str]:
    """Devuelve las consultas de Places que merece la pena hacer.

    Una pregunta abierta como "qué hago cerca" no es una categoría de Google:
    la convertimos en dos o tres búsquedas pequeñas y cacheables. El límite es
    parte del contrato de coste de este módulo.
    """
    consultas: list[str] = []

    def add(nombre: str) -> None:
        if nombre not in consultas:
            consultas.append(nombre)

    explicita = detectar_sitio(pregunta)
    if explicita:
        add(explicita)
    for nombres, patron in _PATRONES_PLANES:
        if patron.search(pregunta):
            for nombre in nombres:
                add(nombre)
    return consultas[:_MAX_CONSULTAS_SITIOS]


# Un destino no es una frase. Estas son las palabras con las que sigue la
# oración después del nombre del sitio, y donde hay que cortar.
_COLA_DESTINO = re.compile(
    r"\s+(?:para|por|si|y|cuando|cu[aá]ndo|antes|despu[eé]s|en\s+coche|hoy|ma[ñn]ana)\b.*$",
    re.I,
)
# Y estas son las que delatan que NO hemos cogido un sitio, sino el principio de
# la pregunta: «¿a qué hora salgo a Vitoria?» hacía que el patrón de "a …"
# enganchara la «a» de «a qué hora» y se llevara la frase entera.
#
# Lleva también VERBOS, y no por gusto: al aceptar «me voy a …» hay que impedir
# que «voy a dormir» se convierta en una ruta al municipio de «dormir». Google
# geocodifica eso sin protestar.
_NO_ES_SITIO = re.compile(
    r"^(?:qu[eé]|cu[aá]l|cu[aá]ndo|c[oó]mo|d[oó]nde|cu[aá]nto|hora|"
    r"ver|salir|ir|dormir|comer|cenar|desayunar|hacer|tomar|buscar|comprar|"
    r"repostar|ducharme|pasear|casa|ning[uú]n|alg[uú]n)\b",
    re.I,
)
# Un topónimo no ocupa media línea. Es el último cinturón contra un destino que
# en realidad es una oración: Google geocodifica CUALQUIER cosa y devuelve una
# ruta con su distancia y su tiempo, así que un destino basura no da error —
# da un viaje convincente a un sitio que nadie ha pedido (decisión 11).
_MAX_LARGO_DESTINO = 40


def _limpiar_lugar(bruto: str) -> str | None:
    limpio = _COLA_DESTINO.sub("", re.sub(r"\s+", " ", bruto)).strip(" .,¿?¡!")
    if len(limpio) < 2 or len(limpio) > _MAX_LARGO_DESTINO:
        return None
    if _NO_ES_SITIO.search(limpio):
        return None
    return limpio


def detectar_ruta(pregunta: str, ubicacion: Place | None = None) -> tuple[str, str] | None:
    if not _PALABRAS_RUTA.search(pregunta):
        return None
    match = _PATRON_RUTA.search(pregunta)
    if match:
        origen = _limpiar_lugar(match.group(1))
        destino = _limpiar_lugar(match.group(2))
    elif ubicacion is not None:
        # Se prueban TODAS las apariciones de "a …" / "hasta …", no solo la
        # primera: en «¿a qué hora salgo a Vitoria?» la primera es la de «a qué
        # hora» y hay que descartarla para llegar a la buena.
        origen = ubicacion.display_name or ubicacion.name
        destino = None
        # Se prueba CADA "a …" / "hasta …" de la frase, de izquierda a derecha,
        # y se queda el primero que parezca un sitio. En «¿a qué hora salgo a
        # Vitoria?» el primero es «a qué hora» —que `_limpiar_lugar` descarta— y
        # el bueno es el segundo.
        #
        # Se buscan los CONECTORES y se corta el resto de la frase a mano, en vez
        # de un patrón que capture hasta el final: `finditer` no solapa, así que
        # con el patrón largo la primera coincidencia se comía la oración entera
        # y no quedaba nada donde encontrar el segundo candidato.
        for conector in re.finditer(r"\b(?:a|hasta)\s+", pregunta, re.I):
            destino = _limpiar_lugar(pregunta[conector.end():])
            if destino:
                break
    else:
        return None
    if not origen or not destino:
        return None
    return origen, destino


def detectar_mejor_hora(pregunta: str) -> bool:
    return bool(_PALABRAS_MEJOR_HORA.search(pregunta))


# Lo que la herramienta NO puede ver, dicho en el propio texto que lee el modelo.
#
# Google Routes da congestión —lo lento que se circula— y NO da incidencias:
# ni accidentes, ni cortes, ni obras. Sin esta línea, un modelo que recibe
# "tráfico fluido" contesta tranquilamente "la carretera está despejada", que es
# una afirmación sobre algo que no hemos mirado. Es exactamente el problema de
# los POIs en la decisión 37 —no se afirma que no hay nada sin haberlo buscado—
# y de la NASA en la 53, donde el aviso de lo que el satélite no ve es parte del
# veredicto.
#
# La fuente que sí lo tiene es la DGT (DATEX II), y hoy no se puede consultar
# desde el servidor: `infocar.dgt.es` no está en la lista blanca del proxy de
# PythonAnywhere y su CORS no permite pedirla desde el navegador. Mientras eso
# siga así, este aviso es la única respuesta honesta.
_AVISO_SIN_INCIDENCIAS = (
    "COBERTURA DEL TRÁFICO: la herramienta ve la CONGESTIÓN (lo lento que se "
    "circula), pero NO ve accidentes, cortes de carretera ni obras. No afirmes "
    "que no hay incidencias ni que la carretera está despejada: di que no lo "
    "puedes comprobar y remite a la DGT (infocar.dgt.es o el 011)."
)


def _lecturas_contexto(pregunta: str, tiempo: Any | None) -> list[str]:
    lineas: list[str] = []
    if _PALABRAS_AGUA.search(pregunta):
        if tiempo is None:
            lineas.append(
                "PADDLE_SURF: no hay previsión meteorológica/marina en el contexto; "
                "no des una recomendación segura para entrar al agua."
            )
        else:
            water = tiempo.water_sports()
            lineas.append(f"PADDLE_SURF: {water.rating.upper()} — {water.reason}")
    return lineas


def _lecturas_territorio(pregunta: str, client: aemet.AemetClient | None = None) -> list[str]:
    if not _PALABRAS_TERRITORIO.search(pregunta):
        return []
    informe = aemet.informe_territorio(
        incluir_radar=bool(_PALABRAS_RADAR.search(pregunta)),
        client=client,
    )
    return aemet.formatear(informe)


def _memoria_basica(pregunta: str) -> list[str]:
    if not _PALABRAS_MEMORIA.search(pregunta):
        return []
    notas = storage.list_notes(limit=5)
    waypoints = storage.list_waypoints(limit=5)
    lineas: list[str] = []
    if notas:
        lineas.append("Últimas notas:")
        for nota in notas[:5]:
            texto = str(nota.get("text") or "").strip()
            lugar = nota.get("place_name") or "sin lugar"
            cuando = str(nota.get("created_at") or "")[:16]
            lineas.append(f"- {cuando} · {lugar}: {texto[:160]}")
    if waypoints:
        lineas.append("Últimas fotos con metadatos:")
        for foto in waypoints[:5]:
            archivo = foto.get("archivo") or "foto"
            cuando = str(foto.get("capturado_en") or "")[:16]
            lineas.append(f"- {cuando} · {archivo}")
    if not lineas:
        lineas.append("No hay notas ni fotos recientes guardadas en SQLite.")
    return lineas


def ejecutar(
    pregunta: str,
    ubicacion: Place,
    *,
    provider: MapsProvider | None = None,
    tiempo: Any | None = None,
    aemet_client: aemet.AemetClient | None = None,
    ahora: datetime | None = None,
) -> ToolBundle:
    """Ejecuta solo las herramientas que la pregunta parece necesitar."""
    provider = provider or GoogleMapsProvider()
    avisos: list[str] = []
    sitios: list[ToolPlace] = []
    ruta: ToolRoute | None = None
    horarios: ToolHorarios | None = None

    consultas_sitios = detectar_consultas_sitios(pregunta)
    for consulta_sitio in consultas_sitios:
        try:
            encontrados = provider.buscar_sitios(consulta_sitio, ubicacion.lat, ubicacion.lon)
            sitios.extend(encontrados)
            if not encontrados:
                avisos.append(f"No encontré {consulta_sitio} cerca con la herramienta de sitios.")
        except ToolError as exc:
            avisos.append(f"{consulta_sitio}: {exc}")

    ruta_detectada = detectar_ruta(pregunta, ubicacion)
    if ruta_detectada:
        try:
            ruta = provider.calcular_ruta(*ruta_detectada)
        except ToolError as exc:
            avisos.append(str(exc))

        # Las horas de salida solo si LAS PIDEN: son cuatro llamadas de pago
        # contra una, así que «¿cuánto tardo a Vitoria?» no puede dispararlas
        # de rebote. Y solo si la ruta de ahora ha salido bien: si Google no
        # contesta para el momento actual, insistir cuatro veces más es quemar
        # el timeout de una petición que alguien está esperando (decisión 12).
        if ruta is not None and detectar_mejor_hora(pregunta):
            try:
                horarios = provider.horas_de_salida(
                    *ruta_detectada, ahora=ahora or datetime.now(timezone.utc)
                )
            except ToolError as exc:
                avisos.append(str(exc))

    lecturas = _lecturas_contexto(pregunta, tiempo) + _lecturas_territorio(
        pregunta, aemet_client
    )
    # El aviso de cobertura entra si se ha mirado el tráfico O si la pregunta
    # habla de incidencias, aunque no hayamos podido mirar nada. El segundo caso
    # es el que importa: preguntar «¿hay algún corte en la A-8?» y no recibir
    # datos NO significa que no lo haya.
    if ruta is not None or _PALABRAS_TRAFICO.search(pregunta):
        lecturas.append(_AVISO_SIN_INCIDENCIAS)

    return ToolBundle(
        sitios=sitios,
        ruta=ruta,
        horarios=horarios,
        memoria=_memoria_basica(pregunta),
        lecturas=lecturas,
        avisos=avisos,
    )


def formatear(bundle: ToolBundle) -> str:
    """Renderiza resultados para el prompt sin volcar JSON crudo."""
    if not bundle.hay_algo():
        return "(No se ha usado ninguna herramienta extra en esta pregunta.)"

    lineas: list[str] = []
    if bundle.sitios:
        lineas.append("SITIOS CONSULTADOS:")
        for sitio in bundle.sitios:
            partes = [sitio.nombre]
            if sitio.abierto:
                partes.append(sitio.abierto)
            if sitio.rating is not None:
                partes.append(f"rating {sitio.rating}")
            if sitio.direccion:
                partes.append(sitio.direccion)
            if sitio.maps_url:
                partes.append(sitio.maps_url)
            lineas.append("- " + " · ".join(partes))

    if bundle.ruta:
        r = bundle.ruta
        partes = [f"{r.origen} -> {r.destino}"]
        if r.distancia_km is not None:
            partes.append(f"{r.distancia_km} km")
        if r.duracion_trafico_min is not None:
            partes.append(f"{r.duracion_trafico_min} min con tráfico")
        elif r.duracion_min is not None:
            partes.append(f"{r.duracion_min} min")
        if r.duracion_min is not None and r.duracion_trafico_min is not None:
            partes.append(f"{r.duracion_min} min sin tráfico")
        if r.maps_url:
            partes.append(r.maps_url)
        lineas.append("RUTA CONSULTADA:")
        lineas.append("- " + " · ".join(partes))

        # El veredicto va en su propia línea y en mayúsculas: es lo que el
        # modelo tiene que repetir, y lo calculamos nosotros (decisión 5).
        trafico = [f"TRÁFICO: {r.estado.upper()}"]
        if r.motivo:
            trafico.append(r.motivo)
        if r.tramos_parados:
            trafico.append(f"{r.tramos_parados} tramo(s) parado(s)")
        if r.tramos_lentos:
            trafico.append(f"{r.tramos_lentos} tramo(s) lento(s)")
        lineas.append("- " + " · ".join(trafico))

    if bundle.horarios and bundle.horarios.opciones:
        h = bundle.horarios
        lineas.append("SALIR A QUÉ HORA (predicción de Google por hora de salida):")
        mejor = h.mejor()
        for opcion in h.opciones:
            marca = "  <- la más rápida" if opcion is mejor else ""
            lineas.append(
                f"- salir {opcion.hora}: {opcion.duracion_min} min"
                + (f" ({opcion.retraso_min:+d} min de tráfico)" if opcion.retraso_min else "")
                + marca
            )

    if bundle.memoria:
        lineas.append("MEMORIA DEL VIAJE:")
        lineas.extend(bundle.memoria)

    if bundle.lecturas:
        lineas.append("LECTURAS DEL CONTEXTO:")
        lineas.extend("- " + lectura for lectura in bundle.lecturas)

    if bundle.avisos:
        lineas.append("AVISOS DE HERRAMIENTAS:")
        lineas.extend("- " + aviso for aviso in bundle.avisos)

    return "\n".join(lineas)
