"""Contexto de ubicación: de coordenadas a "dónde estoy".

Fase 1: geocodificación inversa con Nominatim (OpenStreetMap).
Fase 2: puntos de interés cercanos con Overpass (aún sin implementar).

Función de entrada: `reverse_geocode(lat, lon) -> Place`.
"""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, asdict
from typing import Any

import requests

from app.config import Config
from app.modules import storage

# El nombre de un pueblo no cambia. Cacheamos 30 días: suficiente para todo el
# viaje, y hace que volver a un sitio ya visitado sea instantáneo y sin red.
_GEOCODE_CACHE_TTL = 30 * 24 * 3600


class LocationError(Exception):
    """Error recuperable al resolver la ubicación.

    Lo lanzamos en vez de devolver None para que quien llame no pueda ignorar
    el fallo por accidente, y para poder transportar un mensaje legible que
    mostrar al usuario.
    """


class InvalidCoordinates(LocationError):
    """Las coordenadas que nos han pasado no son válidas.

    Subclase separada porque la causa es distinta: aquí el fallo es de quien
    llama (HTTP 400), mientras que un `LocationError` a secas significa que el
    servicio externo falló (HTTP 502). Quien captura puede seguir usando
    `except LocationError` para atrapar ambos si no le importa la diferencia.
    """


@dataclass
class Place:
    """Una ubicación resuelta a nombres humanos.

    Un `dataclass` es como un struct de C++ con constructor, `==` y `repr`
    generados automáticamente. Todos los campos de texto pueden venir vacíos:
    en mitad del campo, Nominatim a veces solo devuelve la provincia.
    """

    lat: float
    lon: float
    name: str = ""          # lo más específico que hay: aldea, pueblo, barrio
    municipality: str = ""  # municipio
    province: str = ""      # provincia
    region: str = ""        # comunidad autónoma
    country: str = ""
    display_name: str = ""  # cadena completa que devuelve Nominatim
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def short_label(self) -> str:
        """Etiqueta corta para la interfaz: "Cudillero, Asturias"."""
        parts = [p for p in (self.name or self.municipality, self.region) if p]
        # dict.fromkeys elimina duplicados conservando el orden (p.ej. cuando
        # el pueblo y la región se llaman igual: "Madrid, Madrid").
        return ", ".join(dict.fromkeys(parts)) or "Ubicación desconocida"

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict, omitiendo `raw` (es ruido para el frontend)."""
        d = asdict(self)
        d.pop("raw", None)
        d["short_label"] = self.short_label()
        return d


def validate_coords(lat: float, lon: float) -> tuple[float, float]:
    """Valida y normaliza coordenadas. Lanza LocationError si no son válidas.

    Validamos en el borde del módulo (no dentro) para que el resto de
    funciones pueda asumir que los datos son correctos.

    Es pública (antes era `_validate_coords`) porque `contexto.construir()`
    lanza varias consultas en paralelo y necesita descartar unas coordenadas
    imposibles ANTES de abrir hilos: si no, una petición con basura acabaría
    molestando a dos servicios ajenos para devolver un 400 igualmente. La
    alternativa —duplicar la validación allí— es peor: dos definiciones de
    "coordenada válida" que se separan sin que nadie se entere.
    """
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError) as exc:
        raise InvalidCoordinates("Las coordenadas no son numéricas.") from exc

    if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
        raise InvalidCoordinates("Coordenadas fuera de rango.")
    return lat_f, lon_f


def _parse_nominatim(payload: dict[str, Any], lat: float, lon: float) -> Place:
    """Convierte la respuesta de Nominatim en un `Place`.

    Nominatim no garantiza qué claves devuelve: para una playa aislada puede
    dar solo `county`, y para una ciudad da `city`. Por eso probamos varias
    claves en orden de preferencia en vez de asumir una.
    """
    addr: dict[str, Any] = payload.get("address", {}) or {}

    def pick(*keys: str) -> str:
        for key in keys:
            value = addr.get(key)
            if value:
                return str(value)
        return ""

    return Place(
        lat=lat,
        lon=lon,
        name=pick("village", "hamlet", "town", "city", "suburb", "locality", "neighbourhood"),
        municipality=pick("municipality", "city", "town", "village"),
        province=pick("province", "county", "state_district"),
        region=pick("state", "region"),
        country=pick("country"),
        display_name=str(payload.get("display_name", "")),
        raw=payload,
    )


def reverse_geocode(lat: float, lon: float) -> Place:
    """Traduce coordenadas GPS a un lugar con nombre.

    Args:
        lat: latitud en grados decimales (-90 a 90).
        lon: longitud en grados decimales (-180 a 180).

    Returns:
        Un `Place`. Algunos campos pueden venir vacíos.

    Raises:
        LocationError: coordenadas inválidas, o la API no responde / responde
            algo que no entendemos.
    """
    lat, lon = validate_coords(lat, lon)

    cache_key = storage.cache_key_for_coords("geocode", lat, lon)
    cached = storage.cache_get(cache_key, _GEOCODE_CACHE_TTL)
    if cached is not None:
        return _parse_nominatim(cached, lat, lon)

    params = {
        "lat": f"{lat}",
        "lon": f"{lon}",
        "format": "jsonv2",
        # zoom=14 ~ nivel de pueblo/barrio. Más alto devuelve la calle exacta
        # (innecesario y peor para la caché); más bajo devuelve solo la región.
        "zoom": "14",
        "addressdetails": "1",
        "accept-language": "es",
    }
    headers = {"User-Agent": Config.NOMINATIM_USER_AGENT}

    try:
        response = requests.get(
            Config.NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=Config.HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout as exc:
        raise LocationError("El servicio de mapas tardó demasiado en responder.") from exc
    except requests.ConnectionError as exc:
        raise LocationError("Sin conexión con el servicio de mapas.") from exc
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        raise LocationError(f"El servicio de mapas devolvió un error ({code}).") from exc
    except ValueError as exc:  # json() falla si el cuerpo no es JSON válido
        raise LocationError("Respuesta ilegible del servicio de mapas.") from exc

    # Nominatim señala "no hay nada aquí" con un 200 y un campo `error`, no con
    # un código HTTP de error. Hay que comprobarlo a mano.
    if not isinstance(payload, dict) or "error" in payload:
        raise LocationError("No se encontró ningún lugar en esas coordenadas.")

    # Solo cacheamos respuestas válidas: cachear un error lo congela 30 días.
    storage.cache_set(cache_key, payload)
    return _parse_nominatim(payload, lat, lon)


# ---------------------------------------------------------------------------
# Puntos de interés cercanos (Overpass API sobre datos de OpenStreetMap)
# ---------------------------------------------------------------------------

# Overpass es lento e inestable: es un servicio comunitario gratuito que
# ejecuta consultas pesadas. Medido en la práctica, la misma consulta puede
# tardar 2 s o 25 s según la carga del servidor y la densidad de la zona.
#
# Estrategia: PETICIONES ESCALONADAS (hedged requests).
#   Lanzamos el primer espejo. Si no ha respondido en _HEDGE_DELAY segundos,
#   lanzamos el segundo SIN cancelar el primero, y así. Nos quedamos con el
#   que responda antes.
#
# Por qué no en serie: probar espejos uno a uno suma sus timeouts. Medido en
# Llanes: 30 s de espera al primer espejo + 23 s del segundo = 53 s. Inservible.
# Por qué no los tres a la vez: triplicaría la carga sobre un servicio
# comunitario gratuito en el caso normal, en el que el primero responde bien.
# El escalonado acota la latencia sin castigar el caso bueno.
_OVERPASS_MIRRORS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
_HEDGE_DELAY = 6.0              # espera antes de lanzar el siguiente espejo
_MIRROR_TIMEOUT = 25.0          # timeout HTTP de cada petición individual
_OVERPASS_TOTAL_BUDGET = 35.0   # techo absoluto: pasado esto, nos rendimos
_OVERPASS_QUERY_TIMEOUT = 20    # el timeout que le pedimos a Overpass
_POI_CACHE_TTL = 7 * 24 * 3600

# Qué buscamos, agrupado en categorías con sentido para un viaje en camper.
# Formato: categoría legible -> (clave OSM, valores aceptados).
_POI_CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "naturaleza": ("natural", ("beach", "peak", "waterfall", "cave_entrance", "spring", "cliff")),
    "miradores": ("tourism", ("viewpoint",)),
    "cultura": ("tourism", ("museum", "artwork", "gallery", "attraction")),
    "patrimonio": (
        "historic",
        ("castle", "ruins", "monument", "archaeological_site", "church", "fort", "tower"),
    ),
    "espacios naturales": ("leisure", ("nature_reserve", "park")),
    "pernocta": ("tourism", ("camp_site", "caravan_site", "picnic_site")),
}

# Cuántos POIs devolvemos por categoría y en total. Limitar por categoría (y
# no globalmente) es lo que evita que en una zona montañosa el resultado sean
# 40 picos y ninguna playa: un límite global sesga hacia lo que Overpass
# devuelva primero, que no es lo más útil.
_MAX_PER_CATEGORY = 5
_MAX_TOTAL = 24


@dataclass
class Poi:
    """Un punto de interés cercano."""

    name: str
    category: str        # nuestra categoría legible ("naturaleza", "cultura"...)
    subcategory: str     # el valor OSM crudo ("beach", "viewpoint"...)
    lat: float
    lon: float
    distance_m: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta sobre la superficie terrestre, en metros.

    Es distancia en línea recta, no por carretera: en el norte de España, con
    valles y curvas, la distancia real en coche puede ser el doble. Lo tenemos
    en cuenta al redactar el prompt de Claude.
    """
    from math import asin, cos, radians, sin, sqrt

    radius_m = 6_371_000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * radius_m * asin(sqrt(a))


def _build_overpass_query(lat: float, lon: float, radius_m: int) -> str:
    """Construye la consulta en Overpass QL.

    Pedimos nodes y ways: una playa o un castillo suelen estar mapeados como
    `way` (un polígono), no como un punto. `out center` hace que Overpass
    calcule el centroide de cada polígono, para poder tratarlo como un punto.

    Agrupamos las categorías por clave OSM antes de generar las cláusulas.
    `tourism` aparece en tres de nuestras categorías (miradores, cultura,
    pernocta): emitirlas por separado serían tres búsquedas espaciales en vez
    de una. Agrupando pasamos de 12 cláusulas a 8, y cada cláusula es una
    consulta geográfica que Overpass ejecuta por separado.
    """
    by_key: dict[str, set[str]] = {}
    for osm_key, values in _POI_CATEGORIES.values():
        by_key.setdefault(osm_key, set()).update(values)

    clauses: list[str] = []
    # sorted() para que la consulta sea determinista: dos llamadas iguales
    # generan la misma cadena, que es lo que hace fiable la caché.
    for osm_key in sorted(by_key):
        pattern = "|".join(sorted(by_key[osm_key]))
        for element in ("node", "way"):
            clauses.append(f'{element}["{osm_key}"~"^({pattern})$"](around:{radius_m},{lat},{lon});')

    return (
        f"[out:json][timeout:{_OVERPASS_QUERY_TIMEOUT}];"
        f"({''.join(clauses)});"
        # 300 es un techo de seguridad, no el número que devolvemos: el
        # balanceo real por categoría lo hacemos en Python.
        f"out center tags 300;"
    )


def _classify(tags: dict[str, str]) -> tuple[str, str] | None:
    """Traduce las etiquetas OSM a (categoría, subcategoría). None si no encaja."""
    for category, (osm_key, values) in _POI_CATEGORIES.items():
        value = tags.get(osm_key)
        if value in values:
            return category, value
    return None


def _parse_overpass(payload: dict[str, Any], lat: float, lon: float) -> list[Poi]:
    """Convierte la respuesta de Overpass en POIs ordenados y balanceados."""
    pois: list[Poi] = []
    seen: set[tuple[str, str]] = set()

    for element in payload.get("elements", []):
        tags = element.get("tags") or {}

        # Sin nombre no sirve: "un mirador sin nombre a 3 km" no es una
        # recomendación accionable, y ensucia el prompt.
        name = tags.get("name")
        if not name:
            continue

        classified = _classify(tags)
        if classified is None:
            continue
        category, subcategory = classified

        # Los `way` traen las coordenadas en `center` (por `out center`);
        # los `node` las traen directamente.
        center = element.get("center") or element
        poi_lat, poi_lon = center.get("lat"), center.get("lon")
        if poi_lat is None or poi_lon is None:
            continue

        # OSM mapea a veces la misma entidad como node y como way (p.ej. una
        # iglesia y su edificio). Deduplicamos por nombre + categoría.
        key = (name.strip().lower(), category)
        if key in seen:
            continue
        seen.add(key)

        pois.append(
            Poi(
                name=name.strip(),
                category=category,
                subcategory=subcategory,
                lat=float(poi_lat),
                lon=float(poi_lon),
                distance_m=int(_haversine_m(lat, lon, float(poi_lat), float(poi_lon))),
            )
        )

    pois.sort(key=lambda p: p.distance_m)

    # Balanceo: los N más cercanos de CADA categoría, no los N más cercanos
    # en total. Así el resultado siempre es variado.
    per_category: dict[str, int] = {}
    balanced: list[Poi] = []
    for poi in pois:
        count = per_category.get(poi.category, 0)
        if count >= _MAX_PER_CATEGORY:
            continue
        per_category[poi.category] = count + 1
        balanced.append(poi)
        if len(balanced) >= _MAX_TOTAL:
            break

    balanced.sort(key=lambda p: p.distance_m)
    return balanced


def _poi_cache_key(lat: float, lon: float, radius_m: int) -> str:
    return f"{storage.cache_key_for_coords('pois', lat, lon)}:r{radius_m}"


def pois_cacheados(lat: float, lon: float, radius_m: int = 12_000) -> list[Poi] | None:
    """Los POIs que YA estén en caché. Nunca toca la red.

    Devuelve `None` cuando no hay nada cacheado para ese sitio, y eso es
    distinto de devolver `[]`:

        [] -> se consultó y aquí no hay nada mapeado en OpenStreetMap
        None -> no se ha consultado nunca

    Confundir las dos cosas es el error que ya se evitó a propósito al
    descartar el espejo suizo de Overpass (decisión 22): convertir "no lo he
    mirado" en "aquí no hay nada que ver". Por eso son valores distintos y no
    una lista vacía para ambos.

    Existe porque Overpass está medido en 31,3 s desde el servidor cuando falla
    (decisión 22), y hacer esperar eso para generar una recomendación es
    inaceptable. La recomendación usa lo que ya haya; buscar de verdad es una
    decisión explícita del usuario, con su botón. La caché dura 7 días, así que
    en un sitio donde ya se buscó una vez esto sale gratis y completo.
    """
    lat, lon = validate_coords(lat, lon)

    cached = storage.cache_get(_poi_cache_key(lat, lon, radius_m), _POI_CACHE_TTL)
    if cached is None:
        return None
    return _parse_overpass(cached, lat, lon)


def find_nearby_pois(lat: float, lon: float, radius_m: int = 12_000) -> list[Poi]:
    """Busca puntos de interés alrededor de unas coordenadas. PUEDE TARDAR.

    Medido desde PythonAnywhere: hasta 31,3 s cuando los tres espejos fallan.
    Por eso esta llamada solo cuelga de una acción explícita del usuario, y no
    del camino normal de la pantalla. Para lo que ya se sepa sin esperar está
    `pois_cacheados()`.

    Args:
        lat, lon: centro de la búsqueda.
        radius_m: radio en metros. 12 km por defecto: lo razonable para
            "cojo el coche un rato", teniendo en cuenta que en el norte la
            distancia por carretera casi duplica la línea recta.

    Returns:
        Lista de POIs con nombre, ordenada por cercanía y balanceada por
        categoría. Puede venir vacía: hay zonas de España sin apenas mapear.

    Raises:
        InvalidCoordinates: coordenadas inválidas.
        LocationError: ningún espejo de Overpass respondió.
    """
    lat, lon = validate_coords(lat, lon)

    cache_key = _poi_cache_key(lat, lon, radius_m)
    cached = storage.cache_get(cache_key, _POI_CACHE_TTL)
    if cached is not None:
        return _parse_overpass(cached, lat, lon)

    payload = _fetch_overpass(_build_overpass_query(lat, lon, radius_m))
    storage.cache_set(cache_key, payload)
    return _parse_overpass(payload, lat, lon)


def _ask_mirror(mirror: str, query: str) -> dict[str, Any]:
    """Lanza la consulta contra un espejo concreto. Lanza excepción si falla."""
    response = requests.post(
        mirror,
        data={"data": query},
        headers={"User-Agent": Config.NOMINATIM_USER_AGENT},
        timeout=_MIRROR_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "elements" not in payload:
        raise ValueError("respuesta sin 'elements'")
    return payload


def _fetch_overpass(query: str) -> dict[str, Any]:
    """Consulta Overpass con peticiones escalonadas entre espejos.

    Lanza el primer espejo; si a los `_HEDGE_DELAY` segundos no ha contestado,
    lanza el siguiente sin cancelar el anterior. Devuelve la primera respuesta
    válida que llegue, venga del espejo que venga.

    Raises:
        LocationError: ningún espejo respondió dentro del presupuesto total.
    """
    errors: list[str] = []
    pending = list(_OVERPASS_MIRRORS)
    running: dict[Future[dict[str, Any]], str] = {}
    deadline = time.monotonic() + _OVERPASS_TOTAL_BUDGET

    # No usamos `with`: al salir del bloque, el executor esperaría a que
    # terminen los espejos que aún estén en vuelo (hasta 25 s), justo lo que
    # queremos evitar. Hacemos shutdown(wait=False) y los abandonamos.
    pool = ThreadPoolExecutor(max_workers=len(_OVERPASS_MIRRORS))
    try:
        while True:
            if pending:
                mirror = pending.pop(0)
                running[pool.submit(_ask_mirror, mirror, query)] = mirror

            remaining_budget = deadline - time.monotonic()
            if remaining_budget <= 0:
                break

            # Mientras queden espejos por lanzar esperamos solo _HEDGE_DELAY,
            # para poder escalar. Cuando ya están todos lanzados, esperamos
            # todo el presupuesto que quede.
            timeout = min(_HEDGE_DELAY, remaining_budget) if pending else remaining_budget
            done, _ = wait(running, timeout=timeout, return_when=FIRST_COMPLETED)

            for future in done:
                mirror = running.pop(future)
                try:
                    return future.result()
                except Exception as exc:  # noqa: BLE001 - un espejo caído es lo normal
                    errors.append(f"{mirror.split('/')[2]}: {type(exc).__name__}")

            if not running and not pending:
                break
    finally:
        pool.shutdown(wait=False)

    detail = "; ".join(errors) if errors else "todos los servidores agotaron el tiempo"
    raise LocationError(f"No se pudo consultar el mapa de puntos de interés ({detail}).")
