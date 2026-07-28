"""Orquestador de IA: construye el prompt y delega en un proveedor de LLM.

Función de entrada: `get_recommendations(contexto, pois) -> Recommendation`.

Tres reglas de diseño que hacen este módulo testeable y barato de iterar:

1. **No llama a ninguna API de contexto.** Recibe el `Contexto` ya construido
   por `contexto.construir()` y los `Poi` ya resueltos. Así puedes probar el
   prompt con datos inventados, sin red y sin gastar llamadas.
2. **`formatear_para_prompt()` es una función pura.** Puedes ver exactamente
   qué texto recibe el modelo sin hacer una sola petición. Iterar sobre un
   prompt a ciegas es la forma más cara de perder una tarde.
3. **No sabe qué proveedor hay detrás.** El prompt de sistema y el esquema de
   salida se definen aquí UNA vez y se pasan al proveedor activo. Cambiar de
   Claude a Gemini es cambiar `LLM_PROVIDER` en el entorno; este archivo no se
   toca.

Sobre el nombre de `formatear_para_prompt()`, que antes era `build_context()`:
lo que hace es **renderizar** el contexto como texto, no construirlo. Desde que
existe `contexto.construir()` había dos funciones llamadas "contexto" que no
devuelven lo mismo —una datos, otra una cadena—, y ese es exactamente el tipo de
ambigüedad que este proyecto ya evitó a propósito llamando de forma distinta a
`waypoints.capturado_en` y `notes.created_at`.

La API key nunca sale del backend.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.modules import storage
from app.modules.contexto import NO_CONSULTADA, Contexto
from app.modules.llm_providers import AIError, LLMProvider, build_provider
from app.modules.location_context import Poi
from app.modules.weather_context import Weather

# Reexportamos AIError para que el resto de la app la siga importando de aquí.
# Vive en llm_providers porque los proveedores tienen que lanzarla y este
# módulo tiene que importarlos: al revés sería un import circular.
__all__ = [
    "AIError",
    "Activity",
    "Recommendation",
    "formatear_para_prompt",
    "get_recommendations",
]

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
- Con la luna, igual: el veredicto sobre caminar de noche viene calculado. \
  Respétalo. Si dice que no hay luz, no propongas una ruta nocturna sin \
  frontal; si dice que sí, una luna llena con el cielo despejado es una buena \
  razón para proponer algo de noche, y merece que lo menciones.
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

def _format_pois(pois: list[Poi], consultados: bool = True) -> str:
    """Agrupa los POIs por categoría en texto compacto.

    Agrupar en vez de volcar JSON crudo tiene dos ventajas: gasta menos tokens
    y le da al modelo la estructura ya hecha, en vez de obligarle a deducirla.

    `consultados` distingue las dos cosas que una lista vacía NO distingue: que
    se haya mirado y no haya nada, o que no se haya mirado. Es el corolario de
    la decisión 22 —el que se escribió al descartar el espejo suizo— llegando
    hasta el prompt: decirle al modelo "no hay nada mapeado aquí" sin haber
    preguntado le hace descartar la zona por un dato que nos hemos inventado, y
    lo hará con toda la seguridad del mundo porque se lo hemos afirmado nosotros.
    """
    if not pois:
        if not consultados:
            return (
                "(No se han buscado. NO son cero: es que nadie ha consultado el "
                "mapa en este sitio. No digas que no hay nada cerca.)"
            )
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


def _format_momento(contexto: Contexto) -> str:
    """El "cuándo" que lee el modelo.

    Si la zona horaria se ha supuesto, se dice. Callarlo sería darle al modelo
    una hora local que puede estar equivocada presentándola como cierta, y a
    partir de ahí razonaría sobre la luz que queda y sobre si un museo está
    abierto con un dato falso. Es la misma honestidad que el resto del prompt:
    lo que no se sabe se declara, no se disimula.
    """
    m = contexto.momento
    linea = (
        f"{m.dia_semana} {m.dt.day}/{m.dt.month}/{m.dt.year}, "
        f"{m.dt.strftime('%H:%M')} (hora local)"
    )
    if m.zona_es_supuesta:
        linea += (
            f"\n(ATENCIÓN: no se ha podido confirmar la zona horaria; se ha supuesto "
            f"{m.zona}. La hora puede estar desplazada, tenlo en cuenta antes de "
            f"afirmar cuánta luz queda.)"
        )
    return linea


def _format_luna(contexto: Contexto) -> str:
    """La luna que lee el modelo, con el veredicto YA calculado.

    Igual que con los deportes de agua (decisión 5): al modelo no se le pregunta
    si se puede caminar de noche, se le da la respuesta hecha por una regla
    explícita y se le pide que la respete. Un LLM haciendo de astrónomo daría
    una respuesta distinta cada vez y no habría forma de auditarla.
    """
    luna = contexto.luna
    if luna is None:
        return "(No hay datos de la luna.)"

    lineas = [
        f"{luna.fase.nombre.capitalize()}, {luna.fase.iluminacion_pct:.0f} % iluminada "
        f"({'creciendo' if luna.fase.creciendo else 'menguando'})",
    ]

    efem = luna.efemerides
    if efem is not None and (efem.salida or efem.puesta):
        # Solo la hora: la fecha ISO completa es ruido para el modelo, igual que
        # en el amanecer y el anochecer.
        salida = efem.salida[11:16] if efem.salida else "no sale hoy"
        puesta = efem.puesta[11:16] if efem.puesta else "no se pone hoy"
        lineas.append(f"Sale a las {salida}, se pone a las {puesta}")
    else:
        lineas.append("(No hay hora de salida ni de puesta: no se pudieron consultar.)")

    lineas.append(f"Caminar de noche: {luna.veredicto.motivo}")
    return "\n".join(lineas)


def _pois_consultados(contexto: Contexto) -> bool:
    """¿Se ha llegado a mirar el mapa en este sitio?

    La respuesta ya está en `contexto.fuentes["pois"]`, que rellena la vista con
    los cuatro estados de la decisión 32. Aquí solo se traduce a la única
    pregunta que le importa al texto del prompt. Si nadie ha puesto la fuente
    —el chatbot, que no los pide— se asume que NO se han consultado, que es
    equivocarse hacia el lado que no inventa datos.
    """
    fuente = contexto.fuentes.get("pois")
    return fuente is not None and fuente.estado != NO_CONSULTADA


def _format_metricas(contexto: Contexto) -> str:
    """Los pasos y la batería que lee el modelo.

    El aviso de que el dato es SIMULADO no es cosmético y por eso va aquí, en el
    texto que lee el modelo, y no solo en un campo del JSON. Un modelo que no
    sabe que los pasos son inventados dirá "hoy llevas 12.757 pasos, ya has
    hecho bastante" con toda la seguridad del mundo, y eso es afirmar como
    cierto algo que nos hemos inventado nosotros. Es la decisión 11 llevada al
    único sitio donde el fallo no se puede detectar mirando la pantalla.
    """
    metricas = contexto.metricas
    if metricas is None:
        return "(No se han consultado.)"
    if not metricas.hay_datos:
        return "(El móvil no ha enviado ninguna muestra estos días.)"

    lineas = []
    media = metricas.media_diaria
    if metricas.pasos_hoy is not None:
        linea = f"Pasos de hoy hasta ahora: {metricas.pasos_hoy:,}".replace(",", ".")
        if media is not None:
            linea += f" (su media de los días anteriores es {media:,})".replace(",", ".")
        lineas.append(linea)
    else:
        # Decirlo explícitamente y no callarlo: un bloque titulado "su actividad
        # de hoy" del que faltan los pasos se lee como cero pasos, y entonces el
        # modelo concluye que lleva el día entero sentado. Que no haya llegado
        # todavía la muestra de hoy (a las 00:30 no ha llegado ninguna) y que no
        # haya andado nada son cosas distintas.
        linea = "Todavía no ha llegado ninguna muestra de hoy, así que NO se sabe cuánto ha andado."
        if media is not None:
            linea += f" Su media de los días anteriores es {media:,}".replace(",", ".")
            linea += " pasos."
        lineas.append(linea)
    if metricas.bateria is not None:
        lineas.append(f"Batería del móvil: {metricas.bateria} %")

    if metricas.es_simulado:
        lineas.append(
            "ATENCIÓN: estas cifras son SIMULADAS, no medidas. No las presentes "
            "como un hecho ni saques conclusiones sobre lo que ha hecho hoy de "
            "verdad. Si te pregunta por ellas, dile que son datos de prueba."
        )
    return "\n".join(lineas) if lineas else "(Sin datos utilizables.)"


def _format_viaje(contexto: Contexto) -> str:
    """El viaje hasta ahora: los agregados y las últimas notas.

    Las notas van con su texto porque son la única fuente que cuenta lo que le
    pareció un sitio, y sin eso el modelo solo sabe por dónde pasó. Van las
    últimas y no todas: el recorte lo decide `viaje.py`, aquí solo se pinta.
    """
    v = contexto.viaje
    if v is None:
        return "(No se ha consultado.)"
    if not v.hay_datos:
        return "(Todavía no hay ninguna nota ni ninguna foto de este viaje.)"

    # Solo lo que tiene valor, y con el singular bien puesto. "1 días, 0
    # lugares distintos, 0 km" no es un error de programa pero sí de lectura:
    # un modelo entrenado con lenguaje natural trata un texto descuidado como
    # una señal de que los datos también lo son.
    partes = []
    if v.dias:
        partes.append(f"{v.dias} día" + ("s" if v.dias != 1 else ""))
    if v.lugares:
        partes.append(f"{v.lugares} lugares distintos")
    if v.km:
        partes.append(f"{v.km} km en línea recta")
    if v.notas_totales:
        partes.append(f"{v.notas_totales} notas")
    if v.fotos:
        partes.append(f"{v.fotos} foto" + ("s" if v.fotos != 1 else ""))
    lineas = [(", ".join(partes) + ".") if partes else "Sin recorrido registrado todavía."]
    if v.regiones:
        lineas.append(f"Comunidades pisadas: {', '.join(v.regiones)}.")
    if v.recientes:
        lineas.append("\nÚltimas notas escritas (de la más antigua a la más reciente):")
        for nota in v.recientes:
            lugar = nota.get("lugar") or "sin lugar"
            cuando = (nota.get("cuando") or "")[:10]
            lineas.append(f"- [{cuando}, {lugar}] {nota['texto']}")
    return "\n".join(lineas)


def formatear_para_prompt(
    contexto: Contexto,
    pois: list[Poi],
    *,
    tarea: str = "Recomiéndame qué hacer desde aquí, ahora.",
) -> str:
    """Renderiza el contexto como el bloque de texto que lee el modelo.

    Función pura: mismo contexto, mismo texto. Imprímela para depurar el prompt
    sin gastar una sola llamada a la API. No depende del proveedor.

    Antes se llamaba `build_context()` y recibía las piezas sueltas. Ahora
    recibe el `Contexto` entero, que es lo que garantiza que la pantalla, el
    recomendador y el chatbot razonen sobre **lo mismo**: si cada uno armara
    su propio contexto, divergirían sin dar ningún error.

    Regla que se mantiene aquí: un dato que falta NUNCA se renderiza como
    silencio. Si no hay tiempo, el texto lo dice y le pide al modelo que no
    suponga; si la hora local es dudosa, también. Un hueco callado se lee como
    "no pasa nada", que es la peor forma de equivocarse.
    """
    place = contexto.ubicacion

    return f"""\
### UBICACIÓN
{place.short_label()}
Dirección completa: {place.display_name or "no disponible"}
Coordenadas: {place.lat:.4f}, {place.lon:.4f}

### MOMENTO
{_format_momento(contexto)}

### METEOROLOGÍA
{_format_weather(contexto.tiempo)}

### LA LUNA DE ESTA NOCHE
{_format_luna(contexto)}

### PUNTOS DE INTERÉS CERCANOS (datos de OpenStreetMap, distancias en línea recta)
{_format_pois(pois, _pois_consultados(contexto))}

### SU ACTIVIDAD DE HOY
{_format_metricas(contexto)}

### EL VIAJE HASTA AHORA
{_format_viaje(contexto)}

### TAREA
{tarea}"""


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

def _cache_key(contexto: Contexto, provider: LLMProvider) -> str:
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
    now = contexto.momento.dt
    weather = contexto.tiempo
    return (
        f"{storage.cache_key_for_coords('reco', contexto.ubicacion.lat, contexto.ubicacion.lon)}"
        f":{provider.name}:{provider.model}"
        f":{now.strftime('%Y%m%d')}:h{now.hour // 3}"
        f":{weather.outdoor_rating() if weather else 'sin-tiempo'}"
    )


def get_recommendations(
    contexto: Contexto,
    pois: list[Poi],
    *,
    use_cache: bool = True,
    provider: LLMProvider | None = None,
) -> Recommendation:
    """Genera recomendaciones razonadas para el contexto actual.

    Recibe el contexto **ya construido** en vez de resolverlo por su cuenta.
    Eso es lo que hace que la pantalla y la recomendación no puedan contradecirse
    y lo que deja al chatbot a una llamada de distancia: la misma pieza alimenta
    a los tres.

    Args:
        contexto: el estado del viaje, de `contexto.construir()`. La ubicación
            va dentro y es obligatoria; que falte el tiempo no es un error, el
            prompt se adapta y lo dice explícitamente.
        pois: puntos de interés cercanos. Puede estar vacía. Va aparte del
            contexto a propósito: son caros y poco fiables (Overpass), así que
            quien llama decide si los pide, y la pantalla rápida no los paga.
        use_cache: False para forzar una consulta nueva.
        provider: proveedor concreto. Por defecto, el de `LLM_PROVIDER`.
            Se inyecta en los tests y en el diagnóstico multi-proveedor.

    Raises:
        AIError: falta configuración, o el proveedor falló. Es la única
            excepción que sale de aquí, venga del proveedor que venga.
    """
    provider = provider or build_provider()
    cache_key = _cache_key(contexto, provider)

    if use_cache:
        cached = storage.cache_get(cache_key, _RECOMMENDATION_CACHE_TTL)
        if cached is not None:
            recommendation = _parse_response(
                cached["json"], cached.get("proveedor", ""), cached.get("modelo", "")
            )
            recommendation.desde_cache = True
            return recommendation

    texto_contexto = formatear_para_prompt(contexto, pois)
    raw_text = provider.generate(
        system=_SYSTEM_PROMPT, context=texto_contexto, schema=_OUTPUT_SCHEMA
    )
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
