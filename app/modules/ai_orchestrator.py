"""Orquestador de IA: construye el prompt y delega en un proveedor de LLM.

Función de entrada: `get_recommendations(place, weather, pois) -> Recommendation`.

Tres reglas de diseño que hacen este módulo testeable y barato de iterar:

1. **No llama a ninguna API de contexto.** Recibe el `Place`, el `Weather` y
   los `Poi` ya resueltos por los otros módulos. Así puedes probar el prompt
   con datos inventados, sin red y sin gastar llamadas.
2. **`build_context()` es una función pura.** Puedes ver exactamente qué texto
   recibe el modelo sin hacer una sola petición. Iterar sobre un prompt a
   ciegas es la forma más cara de perder una tarde.
3. **No sabe qué proveedor hay detrás.** El prompt de sistema y el esquema de
   salida se definen aquí UNA vez y se pasan al proveedor activo. Cambiar de
   Claude a Gemini es cambiar `LLM_PROVIDER` en el entorno; este archivo no se
   toca.

La API key nunca sale del backend.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules import storage
from app.modules.llm_providers import AIError, LLMProvider, build_provider
from app.modules.location_context import Place, Poi
from app.modules.weather_context import Weather

# Reexportamos AIError para que el resto de la app la siga importando de aquí.
# Vive en llm_providers porque los proveedores tienen que lanzarla y este
# módulo tiene que importarlos: al revés sería un import circular.
__all__ = ["AIError", "Activity", "Recommendation", "build_context", "get_recommendations"]

# Las recomendaciones caducan porque dependen de la hora y del tiempo, no solo
# del sitio. 3 horas: lo bastante para no repetir llamada si pulsas el botón
# dos veces, lo bastante corto para que "por la mañana" no se convierta en
# "por la noche".
_RECOMMENDATION_CACHE_TTL = 3 * 3600


# ---------------------------------------------------------------------------
# Esquema de salida (compartido por todos los proveedores)
# ---------------------------------------------------------------------------

# Salida estructurada: el proveedor garantiza que la respuesta cumple este
# esquema. Es la diferencia entre un frontend que se rompe cuando el modelo
# decide escribir markdown, y uno que no se rompe nunca.
#
# Se define UNA vez aquí, no por proveedor: si cada uno tuviera su copia,
# divergirían y no sabrías cuál estás afinando.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resumen": {
            "type": "string",
            "description": (
                "2-4 frases que sitúen al viajero: dónde está, qué momento del día "
                "y qué tiempo hace, y qué tipo de plan encaja mejor ahora mismo."
            ),
        },
        "actividades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "descripcion": {
                        "type": "string",
                        "description": "Qué es y qué se hace allí. 1-3 frases concretas.",
                    },
                    "categoria": {
                        "type": "string",
                        "enum": [
                            "naturaleza", "cultura", "patrimonio", "deporte",
                            "gastronomia", "ruta", "descanso",
                        ],
                    },
                    "por_que_ahora": {
                        "type": "string",
                        "description": (
                            "Por qué encaja con la hora y el tiempo de HOY. "
                            "Esto es lo que convierte la lista en una recomendación."
                        ),
                    },
                    "duracion": {"type": "string", "description": "Ej: '1-2 horas'"},
                    "distancia": {"type": "string", "description": "Ej: 'a 3 km'"},
                    "origen": {
                        "type": "string",
                        "enum": ["lista_cercana", "conocimiento_general"],
                        "description": (
                            "'lista_cercana' si sale de los puntos de interés "
                            "aportados; 'conocimiento_general' si lo añades tú."
                        ),
                    },
                },
                "required": [
                    "titulo", "descripcion", "categoria",
                    "por_que_ahora", "duracion", "distancia", "origen",
                ],
                "additionalProperties": False,
            },
        },
        "aviso": {
            "type": "string",
            "description": (
                "Advertencia relevante (meteorología, mareas, seguridad). "
                "Cadena vacía si no hay ninguna."
            ),
        },
    },
    "required": ["resumen", "actividades", "aviso"],
    "additionalProperties": False,
}


@dataclass
class Activity:
    titulo: str
    descripcion: str
    categoria: str
    por_que_ahora: str
    duracion: str
    distancia: str
    origen: str


@dataclass
class Recommendation:
    resumen: str
    actividades: list[Activity] = field(default_factory=list)
    aviso: str = ""
    proveedor: str = ""
    modelo: str = ""
    desde_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Prompt de sistema (compartido por todos los proveedores)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Eres un compañero de viaje que acompaña a alguien que recorre el norte de \
España en un coche camperizado. Conoces bien la zona: Galicia, Asturias, \
Cantabria, País Vasco, Navarra, La Rioja y el norte de Castilla y León.

Tu trabajo es recomendarle qué hacer AHORA, desde donde está. No eres un \
listado de sitios: eres alguien que razona. Combina siempre tres cosas —dónde \
está, qué hora es y qué tiempo hace— y deja ese razonamiento visible.

Cómo trabajas:

- Prioriza los puntos de interés que te aporto: son datos reales del mapa, \
  verificados y con su distancia medida. Márcalos como "lista_cercana".
- Puedes añadir lugares o planes que conozcas de la zona aunque no estén en \
  esa lista (la cobertura del mapa es irregular y a veces se deja fuera lo \
  más obvio). Márcalos como "conocimiento_general" y no inventes detalles \
  concretos —horarios, precios, teléfonos— que no puedas saber.
- Las distancias que te doy son en LÍNEA RECTA. En el norte, con valles y \
  puertos de montaña, la carretera puede ser el doble o el triple. Tenlo en \
  cuenta al estimar tiempos y dilo si es relevante.
- Ajústate al momento del día. No propongas una ruta de 4 horas al atardecer \
  ni un museo a las 22:00. Si queda poca luz, dilo y propón algo corto.
- Si el tiempo desaconseja un plan, no lo escondas: descártalo explicando por \
  qué y ofrece la alternativa de interior o de resguardo.
- Sobre deportes de agua: te doy una evaluación ya calculada a partir de \
  oleaje y viento. Respétala. Si dice "desaconsejado", no lo propongas; \
  explica el motivo concreto (la ola, la racha).
- Propón entre 3 y 5 actividades, variadas entre ellas. Cinco miradores no \
  son cinco planes.

Tono: español de España, tuteo, directo y concreto. Nada de "sumérgete en la \
magia" ni relleno de folleto turístico. Si un sitio es del montón, dilo.

Responde ÚNICAMENTE con el JSON pedido, sin texto adicional ni marcadores de \
bloque de código.\
"""


# ---------------------------------------------------------------------------
# Construcción del prompt (funciones puras: testeables sin red ni API key)
# ---------------------------------------------------------------------------

def _local_now(weather: Weather | None) -> datetime:
    """Hora local del punto consultado.

    Usamos la zona horaria que devuelve Open-Meteo (`timezone=auto`) en vez de
    la del servidor: PythonAnywhere corre en UTC, y recomendar "un plan de
    tarde" a las 20:00 UTC cuando en Asturias son las 22:00 es un fallo real.
    """
    tz_name = (weather.timezone if weather else "") or "Europe/Madrid"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Europe/Madrid")
    return datetime.now(tz)


def _format_pois(pois: list[Poi]) -> str:
    """Agrupa los POIs por categoría en texto compacto.

    Agrupar en vez de volcar JSON crudo tiene dos ventajas: gasta menos tokens
    y le da al modelo la estructura ya hecha, en vez de obligarle a deducirla.
    """
    if not pois:
        return "(No hay puntos de interés mapeados en el radio consultado.)"

    grouped: dict[str, list[Poi]] = {}
    for poi in pois:
        grouped.setdefault(poi.category, []).append(poi)

    lines: list[str] = []
    for category, items in grouped.items():
        lines.append(f"{category.upper()}:")
        for poi in items:
            km = poi.distance_m / 1000
            distancia = f"{poi.distance_m} m" if km < 1 else f"{km:.1f} km"
            lines.append(f"  - {poi.name} ({poi.subcategory}) — a {distancia} en línea recta")
    return "\n".join(lines)


def _format_weather(weather: Weather | None) -> str:
    if weather is None:
        return (
            "(No se pudo obtener la previsión meteorológica. No hagas suposiciones "
            "sobre el tiempo: propón planes que funcionen haga lo que haga, o di "
            "explícitamente que conviene comprobar el tiempo antes.)"
        )

    lines = [
        f"Ahora mismo: {weather.summary()}",
        f"Valoración para actividades al aire libre: {weather.outdoor_rating()}",
    ]
    if weather.today_max_c is not None and weather.today_min_c is not None:
        lines.append(f"Hoy: mínima {weather.today_min_c:.0f} °C, máxima {weather.today_max_c:.0f} °C")
    if weather.sunrise and weather.sunset:
        # Solo la hora: la fecha completa ISO es ruido para el modelo.
        lines.append(f"Amanecer {weather.sunrise[-5:]}, anochecer {weather.sunset[-5:]}")

    water = weather.water_sports()
    lines.append(f"Deportes de agua (paddle surf, kayak): {water.rating.upper()} — {water.reason}")

    if weather.marine.has_data():
        marine_bits = [f"oleaje {weather.marine.wave_height_m:.1f} m"]
        if weather.marine.wave_period_s is not None:
            marine_bits.append(f"periodo {weather.marine.wave_period_s:.0f} s")
        if weather.marine.sea_temperature_c is not None:
            marine_bits.append(f"agua a {weather.marine.sea_temperature_c:.0f} °C")
        lines.append("Mar: " + ", ".join(marine_bits))

    return "\n".join(lines)


def build_context(
    place: Place,
    weather: Weather | None,
    pois: list[Poi],
    now: datetime | None = None,
) -> str:
    """Construye el bloque de contexto que se envía al modelo.

    Función pura: mismos argumentos, mismo texto. Imprímela para depurar el
    prompt sin gastar una sola llamada a la API. No depende del proveedor.
    """
    now = now or _local_now(weather)
    dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

    return f"""\
### UBICACIÓN
{place.short_label()}
Dirección completa: {place.display_name or "no disponible"}
Coordenadas: {place.lat:.4f}, {place.lon:.4f}

### MOMENTO
{dias[now.weekday()]} {now.day}/{now.month}/{now.year}, {now.strftime("%H:%M")} (hora local)

### METEOROLOGÍA
{_format_weather(weather)}

### PUNTOS DE INTERÉS CERCANOS (datos de OpenStreetMap, distancias en línea recta)
{_format_pois(pois)}

### TAREA
Recomiéndame qué hacer desde aquí, ahora."""


# ---------------------------------------------------------------------------
# Interpretación de la respuesta
# ---------------------------------------------------------------------------

def _strip_code_fence(text: str) -> str:
    """Quita los ```json ... ``` con los que algunos modelos envuelven el JSON.

    Los proveedores con salida estructurada nativa no lo hacen, pero no todos
    la respetan igual de estrictamente y un modelo local (Ollama) casi seguro
    la usará. Tolerarlo aquí, una vez, evita duplicar la limpieza en cada
    proveedor.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Quitamos la primera línea (```json) y la última (```).
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_response(raw_text: str, proveedor: str, modelo: str) -> Recommendation:
    """Convierte el JSON de la respuesta en un `Recommendation`.

    Agnóstico del proveedor a propósito: recibe texto, devuelve el dataclass.
    """
    try:
        data = json.loads(_strip_code_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise AIError("La respuesta del modelo no era JSON válido.") from exc

    if not isinstance(data, dict):
        raise AIError("La respuesta del modelo no era un objeto JSON.")

    actividades = [
        Activity(
            titulo=str(item.get("titulo", "")),
            descripcion=str(item.get("descripcion", "")),
            categoria=str(item.get("categoria", "")),
            por_que_ahora=str(item.get("por_que_ahora", "")),
            duracion=str(item.get("duracion", "")),
            distancia=str(item.get("distancia", "")),
            origen=str(item.get("origen", "conocimiento_general")),
        )
        for item in data.get("actividades", [])
        if isinstance(item, dict)
    ]

    return Recommendation(
        resumen=str(data.get("resumen", "")),
        actividades=actividades,
        aviso=str(data.get("aviso", "")),
        proveedor=proveedor,
        modelo=modelo,
    )


# ---------------------------------------------------------------------------
# Entrada pública
# ---------------------------------------------------------------------------

def _cache_key(place: Place, weather: Weather | None, now: datetime, provider: LLMProvider) -> str:
    """Clave de caché de una recomendación.

    Incluye PROVEEDOR y MODELO además de sitio, día y franja horaria. Sin eso,
    tras desarrollar el prompt con Gemini y cambiar a Claude seguirías viendo
    las respuestas cacheadas de Gemini, y creerías estar evaluando Claude
    cuando no lo estás. Es un fallo silencioso: no da error, solo conclusiones
    equivocadas.

    La franja horaria va en bloques de 3 h porque la recomendación depende de
    la hora (el mismo sitio a las 10:00 y a las 21:00 merece planes distintos),
    pero dos pulsaciones seguidas deben acertar en la caché.
    """
    return (
        f"{storage.cache_key_for_coords('reco', place.lat, place.lon)}"
        f":{provider.name}:{provider.model}"
        f":{now.strftime('%Y%m%d')}:h{now.hour // 3}"
        f":{weather.outdoor_rating() if weather else 'sin-tiempo'}"
    )


def get_recommendations(
    place: Place,
    weather: Weather | None,
    pois: list[Poi],
    *,
    use_cache: bool = True,
    provider: LLMProvider | None = None,
) -> Recommendation:
    """Genera recomendaciones razonadas para la ubicación actual.

    Args:
        place: ubicación resuelta (obligatoria).
        weather: condiciones actuales, o None si no se pudieron obtener.
            No es un error: el prompt se adapta y lo dice explícitamente.
        pois: puntos de interés cercanos. Puede estar vacía.
        use_cache: False para forzar una consulta nueva.
        provider: proveedor concreto. Por defecto, el de `LLM_PROVIDER`.
            Se inyecta en los tests y en el diagnóstico multi-proveedor.

    Raises:
        AIError: falta configuración, o el proveedor falló. Es la única
            excepción que sale de aquí, venga del proveedor que venga.
    """
    provider = provider or build_provider()
    now = _local_now(weather)
    cache_key = _cache_key(place, weather, now, provider)

    if use_cache:
        cached = storage.cache_get(cache_key, _RECOMMENDATION_CACHE_TTL)
        if cached is not None:
            recommendation = _parse_response(
                cached["json"], cached.get("proveedor", ""), cached.get("modelo", "")
            )
            recommendation.desde_cache = True
            return recommendation

    context = build_context(place, weather, pois, now)
    raw_text = provider.generate(system=_SYSTEM_PROMPT, context=context, schema=_OUTPUT_SCHEMA)
    recommendation = _parse_response(raw_text, provider.name, provider.model)

    # Guardamos el JSON ya serializado: si algún día cambian los dataclasses,
    # una entrada vieja de caché no revienta al deserializarse.
    storage.cache_set(
        cache_key,
        {
            "json": json.dumps(
                {
                    "resumen": recommendation.resumen,
                    "actividades": [asdict(a) for a in recommendation.actividades],
                    "aviso": recommendation.aviso,
                },
                ensure_ascii=False,
            ),
            "proveedor": provider.name,
            "modelo": provider.model,
        },
    )
    return recommendation
