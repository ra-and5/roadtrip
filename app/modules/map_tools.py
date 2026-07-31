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
from typing import Any, Protocol

import requests

from app.config import Config
from app.modules import aemet, storage
from app.modules.location_context import Place, validate_coords

_TOOLS_CACHE_TTL = 30 * 60
_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_MAX_CONSULTAS_SITIOS = 3


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


@dataclass(frozen=True)
class ToolBundle:
    """Lo que se añade al prompt."""

    sitios: list[ToolPlace] = field(default_factory=list)
    ruta: ToolRoute | None = None
    memoria: list[str] = field(default_factory=list)
    lecturas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def hay_algo(self) -> bool:
        return bool(self.sitios or self.ruta or self.memoria or self.lecturas or self.avisos)


class MapsProvider(Protocol):
    def buscar_sitios(self, consulta: str, lat: float, lon: float) -> list[ToolPlace]:
        ...

    def calcular_ruta(self, origen: str, destino: str) -> ToolRoute:
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
_PALABRAS_RUTA = re.compile(r"\b(cu[aá]nto tardo|cuanto tardo|ruta|llegar|distancia)\b", re.I)
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

    def calcular_ruta(self, origen: str, destino: str) -> ToolRoute:
        origen = origen.strip()
        destino = destino.strip()
        if not origen or not destino:
            raise ToolError("Falta origen o destino para calcular la ruta.")

        key = _cache_key("google_routes", origen, destino)
        cached = storage.cache_get(key, _TOOLS_CACHE_TTL)
        if cached is None:
            body = {
                "origin": {"address": origen},
                "destination": {"address": destino},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE",
                "languageCode": "es-ES",
                "regionCode": "ES",
                "units": "METRIC",
            }
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._require_key(),
                "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters",
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
    return ToolRoute(
        origen=origen,
        destino=destino,
        distancia_km=distancia,
        duracion_min=_segundos_a_minutos(ruta.get("staticDuration")),
        duracion_trafico_min=_segundos_a_minutos(ruta.get("duration")),
        maps_url=maps_url,
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


def detectar_ruta(pregunta: str, ubicacion: Place | None = None) -> tuple[str, str] | None:
    if not _PALABRAS_RUTA.search(pregunta):
        return None
    match = _PATRON_RUTA.search(pregunta)
    if match:
        origen = re.sub(r"\s+", " ", match.group(1)).strip(" .,")
        destino = re.sub(r"\s+", " ", match.group(2)).strip(" .,")
    elif ubicacion is not None:
        match = _PATRON_RUTA_DESDE_AQUI.search(pregunta)
        if not match:
            return None
        origen = ubicacion.display_name or ubicacion.name
        destino = re.sub(r"\s+", " ", match.group(1)).strip(" .,")
    else:
        return None
    if len(origen) < 2 or len(destino) < 2:
        return None
    return origen, destino


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
) -> ToolBundle:
    """Ejecuta solo las herramientas que la pregunta parece necesitar."""
    provider = provider or GoogleMapsProvider()
    avisos: list[str] = []
    sitios: list[ToolPlace] = []
    ruta: ToolRoute | None = None

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

    return ToolBundle(
        sitios=sitios,
        ruta=ruta,
        memoria=_memoria_basica(pregunta),
        lecturas=(
            _lecturas_contexto(pregunta, tiempo)
            + _lecturas_territorio(pregunta, aemet_client)
        ),
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
        if r.maps_url:
            partes.append(r.maps_url)
        lineas.append("RUTA CONSULTADA:")
        lineas.append("- " + " · ".join(partes))

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
