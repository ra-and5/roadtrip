"""Tests del orquestador con el proveedor mockeado.

Ninguno toca la red ni necesita API key. Lo que se protege:
  - que el orquestador no sabe qué proveedor hay detrás,
  - que el prompt de sistema es el mismo para todos (no una copia por proveedor),
  - que la clave de caché distingue proveedor y modelo,
  - que cualquier fallo del proveedor sale como AIError y nada más.
"""

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.modules import ai_orchestrator as ai
from app.modules.ai_orchestrator import AIError, Recommendation, get_recommendations
from app.modules.llm_providers import LLMProvider
from app.modules.location_context import Place
from app.modules.weather_context import Marine, Weather

RESPUESTA_VALIDA = json.dumps({
    "resumen": "Estás en Cudillero a media tarde y el mar está movido.",
    "actividades": [{
        "titulo": "Mirador de la Garita",
        "descripcion": "Balcón sobre el puerto.",
        "categoria": "naturaleza",
        "por_que_ahora": "Queda hora y media de luz, suficiente para la subida.",
        "duracion": "45 min",
        "distancia": "a 300 m",
        "origen": "lista_cercana",
    }],
    "aviso": "El oleaje desaconseja el paddle surf hoy.",
}, ensure_ascii=False)


class FakeProvider(LLMProvider):
    """Proveedor de mentira, configurable, que registra lo que recibe."""

    def __init__(self, name: str = "fake", model: str = "modelo-1",
                 respuesta: str = RESPUESTA_VALIDA, error: Exception | None = None) -> None:
        super().__init__(model)
        self.name = name
        self._respuesta = respuesta
        self._error = error
        self.llamadas: list[dict[str, Any]] = []

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        self.llamadas.append({"system": system, "context": context, "schema": schema})
        if self._error:
            raise self._error
        return self._respuesta


@pytest.fixture
def place() -> Place:
    return Place(lat=43.5622, lon=-6.1456, name="Cudillero", region="Asturias",
                 display_name="Cudillero, Asturias, España")


@pytest.fixture
def weather() -> Weather:
    return Weather(temperature_c=20.0, wind_speed_kmh=6.0, wind_gusts_kmh=9.0,
                   weather_code=3, timezone="Europe/Madrid",
                   marine=Marine(wave_height_m=1.5))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Base de datos temporal: cada test empieza con la caché vacía."""
    from app.config import Config
    from app.modules import storage
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    storage.init_db()
    return storage


# --- El orquestador no conoce al proveedor ---------------------------------

def test_usa_el_proveedor_inyectado(db, place, weather):
    provider = FakeProvider()
    reco = get_recommendations(place, weather, [], provider=provider)

    assert isinstance(reco, Recommendation)
    assert reco.proveedor == "fake"
    assert reco.modelo == "modelo-1"
    assert len(reco.actividades) == 1
    assert reco.actividades[0].origen == "lista_cercana"


def test_pasa_el_prompt_de_sistema_compartido(db, place, weather):
    """El prompt lo define el orquestador, no el proveedor.

    Si cada proveedor tuviera su copia, divergirían y no sabrías cuál estás
    afinando. Este test fija que llega desde arriba.
    """
    provider = FakeProvider()
    get_recommendations(place, weather, [], provider=provider)

    system = provider.llamadas[0]["system"]
    assert system is ai._SYSTEM_PROMPT
    assert "coche camperizado" in system


def test_pasa_el_contexto_construido_y_el_esquema(db, place, weather):
    provider = FakeProvider()
    get_recommendations(place, weather, [], provider=provider)

    llamada = provider.llamadas[0]
    assert "### UBICACIÓN" in llamada["context"]
    assert "Cudillero" in llamada["context"]
    assert llamada["schema"] is ai._OUTPUT_SCHEMA


def test_cualquier_fallo_del_proveedor_sale_como_aierror(db, place, weather):
    """Hacia fuera solo existe AIError, venga el fallo de donde venga."""
    provider = FakeProvider(error=RuntimeError("algo muy específico de un SDK"))
    with pytest.raises(RuntimeError):
        # Un error que el proveedor NO tradujo se propaga tal cual: es un bug
        # del proveedor, no una condición esperada. Los proveedores reales
        # traducen todo a AIError (ver test_llm_providers.py).
        get_recommendations(place, weather, [], provider=provider)


def test_error_traducido_por_el_proveedor_llega_intacto(db, place, weather):
    provider = FakeProvider(error=AIError("Límite de la capa gratuita (429)."))
    with pytest.raises(AIError) as exc_info:
        get_recommendations(place, weather, [], provider=provider)
    assert "429" in str(exc_info.value)


# --- Clave de caché ---------------------------------------------------------

def test_la_cache_funciona_con_el_mismo_proveedor(db, place, weather):
    provider = FakeProvider()
    get_recommendations(place, weather, [], provider=provider)
    segunda = get_recommendations(place, weather, [], provider=provider)

    assert len(provider.llamadas) == 1, "la segunda llamada debía salir de caché"
    assert segunda.desde_cache is True


def test_cambiar_de_proveedor_no_sirve_la_cache_del_otro(db, place, weather):
    """EL fallo que esto evita, y por qué es peligroso:

    sin proveedor en la clave, tras afinar el prompt con Gemini y cambiar a
    Claude seguirías viendo las respuestas cacheadas de Gemini. No da error:
    da conclusiones equivocadas sobre qué modelo lo hace mejor.
    """
    gemini = FakeProvider(name="gemini", model="gemini-2.5-flash",
                          respuesta=RESPUESTA_VALIDA)
    claude = FakeProvider(name="anthropic", model="claude-opus-5",
                          respuesta=RESPUESTA_VALIDA)

    primera = get_recommendations(place, weather, [], provider=gemini)
    segunda = get_recommendations(place, weather, [], provider=claude)

    assert len(claude.llamadas) == 1, "Claude debía ser consultado de verdad"
    assert primera.proveedor == "gemini"
    assert segunda.proveedor == "anthropic"
    assert segunda.desde_cache is False


def test_cambiar_de_modelo_dentro_del_mismo_proveedor_tampoco(db, place, weather):
    """Comparar dos modelos del mismo proveedor es un caso igual de real."""
    flash = FakeProvider(name="gemini", model="gemini-2.5-flash")
    pro = FakeProvider(name="gemini", model="gemini-2.5-pro")

    get_recommendations(place, weather, [], provider=flash)
    get_recommendations(place, weather, [], provider=pro)

    assert len(pro.llamadas) == 1


def test_la_clave_incluye_proveedor_y_modelo(place, weather):
    now = datetime(2026, 7, 27, 18, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    clave = ai._cache_key(place, weather, now, FakeProvider(name="gemini", model="g-2.5"))
    assert ":gemini:" in clave
    assert "g-2.5" in clave


def test_use_cache_false_fuerza_llamada_nueva(db, place, weather):
    provider = FakeProvider()
    get_recommendations(place, weather, [], provider=provider)
    get_recommendations(place, weather, [], provider=provider, use_cache=False)
    assert len(provider.llamadas) == 2


# --- Interpretación de la respuesta ----------------------------------------

def test_tolera_json_envuelto_en_bloque_de_codigo(db, place, weather):
    """Un modelo local (Ollama) casi seguro envolverá el JSON en ```json.

    Tolerarlo una vez aquí evita duplicar la limpieza en cada proveedor.
    """
    provider = FakeProvider(respuesta=f"```json\n{RESPUESTA_VALIDA}\n```")
    reco = get_recommendations(place, weather, [], provider=provider)
    assert reco.resumen.startswith("Estás en Cudillero")


def test_json_invalido_da_aierror(db, place, weather):
    provider = FakeProvider(respuesta="lo siento, no puedo ayudarte con eso")
    with pytest.raises(AIError) as exc_info:
        get_recommendations(place, weather, [], provider=provider)
    assert "JSON" in str(exc_info.value)


def test_respuesta_sin_actividades_no_revienta(db, place, weather):
    provider = FakeProvider(respuesta='{"resumen": "vacío", "actividades": [], "aviso": ""}')
    reco = get_recommendations(place, weather, [], provider=provider)
    assert reco.actividades == []
