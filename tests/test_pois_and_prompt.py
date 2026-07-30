"""Tests del parseo de Overpass y de la construcción del prompt.

Ninguno toca la red ni necesita API key. El objetivo del segundo grupo es
poder iterar sobre el prompt viendo exactamente qué texto recibe Claude, sin
gastar una llamada.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from app.modules.ai_orchestrator import formatear_para_prompt, _format_pois, _format_weather
from app.modules.contexto import ensamblar
from app.modules.location_context import (
    Place,
    Poi,
    _build_overpass_query,
    _classify,
    _haversine_m,
    _parse_overpass,
)
from app.modules.weather_context import Marine, Weather


# --- Distancias -----------------------------------------------------------

def test_haversine_distancia_conocida():
    """Cudillero -> Luarca: 31,6 km en línea recta.

    Valor comprobado a mano: a latitud 43,55° un grado de longitud son
    111,32 x cos(43,55) = 80,7 km, y hay 0,3914° de diferencia -> 31,6 km.
    Toleramos 1 km de margen.
    """
    d = _haversine_m(43.5622, -6.1456, 43.5450, -6.5370)
    assert 30_600 < d < 32_600


def test_haversine_un_grado_de_latitud_son_111_km():
    """Contraste independiente: un grado de latitud son ~111,2 km en cualquier sitio."""
    d = _haversine_m(43.0, -6.0, 44.0, -6.0)
    assert 111_000 < d < 111_400


def test_haversine_mismo_punto_es_cero():
    assert _haversine_m(43.0, -6.0, 43.0, -6.0) == 0.0


# --- Clasificación --------------------------------------------------------

def test_classify_reconoce_playa_y_mirador():
    assert _classify({"natural": "beach"}) == ("naturaleza", "beach")
    assert _classify({"tourism": "viewpoint"}) == ("miradores", "viewpoint")


def test_classify_descarta_lo_irrelevante():
    assert _classify({"amenity": "bench"}) is None
    assert _classify({}) is None


# --- Parseo de Overpass ---------------------------------------------------

def _element(name, key, value, lat, lon, etype="node"):
    e = {"type": etype, "tags": {key: value}, "lat": lat, "lon": lon}
    if name:
        e["tags"]["name"] = name
    return e


def test_parse_descarta_elementos_sin_nombre():
    """Un mirador sin nombre no es una recomendación accionable."""
    payload = {"elements": [_element(None, "tourism", "viewpoint", 43.5, -6.1)]}
    assert _parse_overpass(payload, 43.5, -6.1) == []


def test_parse_calcula_distancia_y_ordena_por_cercania():
    payload = {"elements": [
        _element("Lejos", "tourism", "viewpoint", 43.60, -6.1456),
        _element("Cerca", "tourism", "viewpoint", 43.5630, -6.1456),
    ]}
    pois = _parse_overpass(payload, 43.5622, -6.1456)
    assert [p.name for p in pois] == ["Cerca", "Lejos"]
    assert pois[0].distance_m < pois[1].distance_m


def test_parse_usa_center_en_los_ways():
    """Una playa suele estar mapeada como polígono; 'out center' da su centroide."""
    payload = {"elements": [{
        "type": "way", "tags": {"natural": "beach", "name": "Playa de Aguilar"},
        "center": {"lat": 43.5500, "lon": -6.1000},
    }]}
    pois = _parse_overpass(payload, 43.5622, -6.1456)
    assert len(pois) == 1
    assert pois[0].lat == 43.5500


def test_parse_deduplica_misma_entidad_node_y_way():
    """OSM mapea a veces la iglesia como nodo y su edificio como way."""
    payload = {"elements": [
        _element("Iglesia de San Pedro", "historic", "church", 43.5622, -6.1456),
        _element("Iglesia de San Pedro", "historic", "church", 43.5623, -6.1457, etype="way"),
    ]}
    assert len(_parse_overpass(payload, 43.5622, -6.1456)) == 1


def test_parse_balancea_por_categoria():
    """El fallo real que esto previene: 40 picos y cero playas.

    Con un límite global, una zona montañosa devuelve solo picos porque son
    los que Overpass lista primero. El balanceo por categoría garantiza
    variedad, que es lo que hace útil la recomendación.
    """
    elements = [
        _element(f"Pico {i}", "natural", "peak", 43.5 + i * 0.001, -6.1)
        for i in range(30)
    ]
    elements.append(_element("Playa lejana", "tourism", "viewpoint", 43.7, -6.1))
    pois = _parse_overpass({"elements": elements}, 43.5, -6.1)

    categorias = {p.category for p in pois}
    assert "miradores" in categorias, "el mirador lejano debe sobrevivir al recorte"
    assert sum(1 for p in pois if p.category == "naturaleza") <= 5


def test_se_encuentran_los_sitios_para_entrenar_y_para_dormir():
    """Un gimnasio y una barra de calistenia NO son la misma etiqueta en OSM.

    `leisure=fitness_centre` es el gimnasio cerrado y `leisure=fitness_station`
    la barra al aire libre. Buscar solo una de las dos deja fuera justo la que
    hay en la mitad de los pueblos, y no daría ningún error: solo una lista
    corta que parece que ahí no hay nada.

    `sanitary_dump_station` va con ellas porque es lo que convierte un sitio
    bonito en un sitio donde dormir: sin vaciado, no se puede parar.
    """
    payload = {"elements": [
        _element("Gimnasio Cudillero", "leisure", "fitness_centre", 43.5623, -6.1457),
        _element("Barras de la playa", "leisure", "fitness_station", 43.5624, -6.1458),
        _element("Pista de atletismo", "leisure", "track", 43.5625, -6.1459),
        _element("Área de autocaravanas", "amenity", "sanitary_dump_station", 43.5626, -6.1460),
        _element("Camping L'Amuravela", "tourism", "camp_site", 43.5627, -6.1461),
    ]}
    pois = _parse_overpass(payload, 43.5622, -6.1456)

    por_categoria = {p.category for p in pois}
    assert "deporte" in por_categoria
    assert "servicios de camper" in por_categoria
    assert "pernocta" in por_categoria
    assert len(pois) == 5, "no se puede caer ninguno por el camino"


def test_ninguna_categoria_se_queda_fuera_por_el_recorte():
    """El fallo medido en Alicante: 30 POIs y `servicios de camper` en ninguno.

    Las categorías con muchos resultados cerca llenaban el tope global antes de
    que le llegara el turno a las de pocos, así que una categoría recién añadida
    no aparecía **nunca** — y eso no da ningún error, solo una lista que parece
    completa. El recorte va por rondas: primero el más cercano de cada
    categoría, luego el segundo de cada una.
    """
    elements = []
    # Seis categorías con cinco resultados pegados, que es lo que llena el tope.
    for i in range(5):
        elements.append(_element(f"Pico {i}", "natural", "peak", 43.50 + i * 0.001, -6.1))
        elements.append(_element(f"Museo {i}", "tourism", "museum", 43.50 + i * 0.001, -6.1))
        elements.append(_element(f"Castillo {i}", "historic", "castle", 43.50 + i * 0.001, -6.1))
        elements.append(_element(f"Parque {i}", "leisure", "park", 43.50 + i * 0.001, -6.1))
        elements.append(_element(f"Mirador {i}", "tourism", "viewpoint", 43.50 + i * 0.001, -6.1))
        elements.append(_element(f"Camping {i}", "tourism", "camp_site", 43.50 + i * 0.001, -6.1))
    # Y una sola cosa de cada categoría nueva, más lejos: es el caso real.
    elements.append(_element("Barras del parque", "leisure", "fitness_station", 43.60, -6.1))
    elements.append(_element("Área de servicio", "amenity", "sanitary_dump_station", 43.61, -6.1))

    pois = _parse_overpass({"elements": elements}, 43.5, -6.1)
    categorias = {p.category for p in pois}

    assert "deporte" in categorias, "la categoría lejana se la comió el recorte"
    assert "servicios de camper" in categorias, "la categoría lejana se la comió el recorte"


def test_la_consulta_pide_las_categorias_nuevas():
    """Que la categoría exista no basta: tiene que entrar en la consulta.

    Son dos cosas distintas —clasificar lo que llega y pedirlo— y la segunda no
    da ningún error si falta: Overpass devuelve 200 con lo demás y la categoría
    nueva sale vacía para siempre.
    """
    consulta = _build_overpass_query(43.5622, -6.1456, 12_000)

    for valor in ("fitness_centre", "fitness_station", "sanitary_dump_station"):
        assert valor in consulta, valor
    # `leisure` sale una sola vez por elemento aunque lo usen dos categorías:
    # agrupar por clave es lo que evita duplicar consultas geográficas.
    assert consulta.count('node["leisure"') == 1


# --- Construcción del prompt ----------------------------------------------

def _place() -> Place:
    return Place(lat=43.5622, lon=-6.1456, name="Cudillero", region="Asturias",
                 display_name="Cudillero, Asturias, España")


def test_format_pois_agrupa_por_categoria():
    pois = [
        Poi("Playa de Aguilar", "naturaleza", "beach", 43.55, -6.10, 400),
        Poi("Mirador del Pico", "miradores", "viewpoint", 43.56, -6.14, 1500),
    ]
    texto = _format_pois(pois)
    assert "NATURALEZA:" in texto and "MIRADORES:" in texto
    assert "400 m" in texto      # menos de 1 km se expresa en metros
    assert "1.5 km" in texto


def test_format_pois_sin_resultados_lo_dice_explicitamente():
    assert "No hay puntos de interés" in _format_pois([])


def test_format_weather_sin_datos_instruye_a_no_inventar():
    """Si falta el tiempo, el prompt debe impedir que el modelo se lo invente."""
    texto = _format_weather(None)
    assert "No hagas suposiciones" in texto


def test_format_weather_incluye_veredicto_de_agua():
    weather = Weather(temperature_c=20.0, wind_speed_kmh=6.0, wind_gusts_kmh=9.0,
                      weather_code=0, marine=Marine(wave_height_m=0.3))
    texto = _format_weather(weather)
    assert "EXCELENTE" in texto
    assert "oleaje 0.3 m" in texto


def _contexto(tiempo: Weather | None = None, ahora: datetime | None = None):
    """Un contexto de mentira, sin tocar la red. Para eso `ensamblar()` es pura."""
    ahora = ahora or datetime(2026, 7, 27, 18, 30, tzinfo=ZoneInfo("Europe/Madrid"))
    return ensamblar(_place(), tiempo, ahora=ahora)


def test_formatear_para_prompt_contiene_todas_las_secciones():
    texto = formatear_para_prompt(_contexto(), [])

    for seccion in ("### UBICACIÓN", "### MOMENTO", "### METEOROLOGÍA",
                    "### PUNTOS DE INTERÉS", "### TAREA"):
        assert seccion in texto


def test_formatear_para_prompt_usa_hora_local_y_dia_en_espanol():
    """Recomendar 'plan de tarde' a las 22:00 locales sería un fallo real."""
    texto = formatear_para_prompt(_contexto(), [])
    assert "lunes" in texto      # 27/7/2026 es lunes
    assert "18:30" in texto


def test_formatear_para_prompt_es_puro():
    """Mismos argumentos -> mismo texto. Sin esto no se puede cachear ni testear."""
    assert formatear_para_prompt(_contexto(), []) == formatear_para_prompt(_contexto(), [])


def test_el_prompt_avisa_al_modelo_cuando_la_hora_local_es_supuesta():
    """Sin esto, el modelo razonaría sobre la luz que queda con una hora falsa.

    Sin tiempo no hay zona horaria (la aporta Open-Meteo), así que la hora local
    es una suposición. Dársela al modelo como si fuera cierta es exactamente el
    fallo silencioso de la decisión 11: no da error, solo recomienda mal.
    """
    texto = formatear_para_prompt(_contexto(tiempo=None), [])
    assert "no se ha podido confirmar la zona horaria" in texto.lower()


def test_el_prompt_no_avisa_de_la_zona_cuando_open_meteo_la_ha_dado():
    """El aviso solo cuando toca: si salta siempre, se deja de leer."""
    tiempo = Weather(temperature_c=20.0, timezone="Europe/Madrid")
    texto = formatear_para_prompt(_contexto(tiempo=tiempo), [])
    assert "no se ha podido confirmar la zona horaria" not in texto.lower()


# --- Buscar una categoría concreta ----------------------------------------

def test_pedir_una_categoria_solo_consulta_esa():
    """Menos cláusulas para Overpass, y una lista que se puede leer entera.

    Cada cláusula es una búsqueda geográfica en un servidor comunitario que ya
    va justo (decisión 22): pedir «deporte» son dos en vez de diez.
    """
    from app.modules.location_context import _build_overpass_query

    solo_deporte = _build_overpass_query(43.5, -6.1, 12_000, "deporte")

    assert "fitness_station" in solo_deporte
    assert "camp_site" not in solo_deporte, "se está pidiendo más de lo que se buscó"
    assert solo_deporte.count("(around:") == 2, "una clave OSM x node y way"


def test_la_categoria_entra_en_la_clave_de_cache():
    """Si no, buscar «deporte» envenena la búsqueda general de ese sitio.

    La caché guardaría un payload que solo trae gimnasios y la siguiente
    búsqueda lo serviría tal cual: la pantalla diría que aquí no hay playas ni
    campings sin haberlos buscado nunca. Es la decisión 11 en su versión peor,
    porque el hueco parece un resultado.
    """
    from app.modules.location_context import _poi_cache_key

    general = _poi_cache_key(43.5, -6.1, 12_000)
    deporte = _poi_cache_key(43.5, -6.1, 12_000, "deporte")

    assert general != deporte


def test_una_categoria_inventada_se_rechaza():
    from app.modules.location_context import LocationError, find_nearby_pois

    with pytest.raises(LocationError) as fallo:
        find_nearby_pois(43.5, -6.1, categoria="discotecas")

    # El mensaje dice cuáles valen: el error lo va a leer quien monte la
    # pantalla, no el usuario final.
    assert "deporte" in str(fallo.value)
