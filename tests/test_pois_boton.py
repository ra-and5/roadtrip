"""Tests de sacar Overpass del camino normal (Fase 5, §3).

Sin red y sin API keys.

El problema que esto resuelve, medido y no supuesto: los tres espejos de
Overpass fallan desde el servidor y cuestan **31,3 s por petición** (decisión
22). Eso era el 70 % de lo que tardaba la pantalla, gastado en no obtener nada.

Lo que NO se hizo, y es la mitad importante de la decisión: **silenciar el
aviso**. Un aviso que salta siempre es ruido inútil, pero callarlo convierte un
fallo ruidoso en uno silencioso, que es exactamente lo que se evitó a propósito
al descartar el espejo suizo que respondía `200` con cero elementos. Lo que se
quita es la **fuente** del camino normal, no el aviso.

Lo que se protege aquí:

  - que generar una recomendación **nunca** espera a Overpass;
  - que aun así aprovecha los POIs que ya estén en caché, así que buscar una
    vez en un sitio los deja disponibles 7 días;
  - y que "aquí no hay nada mapeado" no se puede confundir con "no lo he
    mirado" ni con "no he podido mirarlo".
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from app.app import app as flask_app
from app.config import Config
from app.modules import contexto, location_context, storage
from app.modules.location_context import LocationError, Place, Poi, pois_cacheados
from app.modules.weather_context import Weather

LAT, LON = 43.5622, -6.1456


def _place() -> Place:
    return Place(lat=LAT, lon=LON, name="Cudillero", region="Asturias",
                 display_name="Cudillero, Asturias, España")


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    storage.init_db()
    # El contexto se resuelve sin red en todos estos tests: lo que se prueba
    # aquí son los POIs, no la ubicación ni el tiempo.
    monkeypatch.setattr(contexto, "reverse_geocode", lambda lat, lon: _place())
    monkeypatch.setattr(
        contexto, "get_weather",
        lambda lat, lon: Weather(temperature_c=20.0, timezone="Europe/Madrid"),
    )
    yield


@pytest.fixture
def sesion() -> Iterator[Any]:
    flask_app.config["TESTING"] = True
    cliente = flask_app.test_client()
    with cliente.session_transaction() as s:
        s["authenticated"] = True
    yield cliente


def _cachear_pois(elementos: list[dict[str, Any]]) -> None:
    """Mete una respuesta de Overpass en la caché, como si se hubiera buscado."""
    clave = location_context._poi_cache_key(LAT, LON, 12_000)
    storage.cache_set(clave, {"elements": elementos})


def _elemento(nombre: str, lat: float, lon: float) -> dict[str, Any]:
    return {"type": "node", "lat": lat, "lon": lon,
            "tags": {"name": nombre, "natural": "beach"}}


# ---------------------------------------------------------------------------
# Lo que arregla la fase: la recomendación no paga Overpass
# ---------------------------------------------------------------------------

def test_recomendar_nunca_espera_a_overpass(sesion: Any, monkeypatch: pytest.MonkeyPatch,
                                            ) -> None:
    """31,3 s medidos en el servidor para no obtener nada. No se pagan aquí.

    Un `find_nearby_pois` que revienta si se le llama convierte en un fallo
    ruidoso lo que si no sería solo una pantalla lenta — y una pantalla lenta no
    da error, solo se abandona.
    """

    def _explota(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("/api/recommendations ha llamado a Overpass")

    import app.app as modulo_app
    monkeypatch.setattr(modulo_app, "find_nearby_pois", _explota)
    monkeypatch.setattr(
        modulo_app, "get_recommendations",
        lambda estado, pois, **kw: _reco_falsa(),
    )

    respuesta = sesion.post("/api/recommendations", json={"lat": LAT, "lon": LON})

    assert respuesta.status_code == 200


def _reco_falsa() -> Any:
    from app.modules.ai_orchestrator import Recommendation
    return Recommendation(resumen="da igual")


def test_sin_busqueda_previa_lo_dice_en_vez_de_callarlo(
    sesion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No es lo mismo "no hay sitios" que "no los he buscado".

    Si esto dijera lo primero, la app estaría afirmando que la zona está vacía
    sin haber mirado — el error que ya se evitó al descartar el espejo suizo.
    """
    import app.app as modulo_app
    monkeypatch.setattr(modulo_app, "get_recommendations",
                        lambda estado, pois, **kw: _reco_falsa())

    cuerpo = sesion.post("/api/recommendations",
                         json={"lat": LAT, "lon": LON}).get_json()

    assert cuerpo["contexto"]["fuentes"]["pois"]["estado"] == "no_consultada"
    assert cuerpo["pois"] == []


def test_lo_ya_buscado_se_aprovecha_gratis(sesion: Any,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Buscar una vez en un sitio deja los POIs disponibles 7 días.

    Es lo que hace que dejarlo bajo botón no empeore la recomendación: se paga
    la espera una vez, y a partir de ahí el modelo sigue recibiendo datos reales
    del mapa con su distancia medida.
    """
    _cachear_pois([_elemento("Playa de Aguilar", 43.5580, -6.1200)])

    recibidos: list[Any] = []

    def _capturar(estado: Any, pois: list[Poi], **kw: Any) -> Any:
        recibidos.extend(pois)
        return _reco_falsa()

    import app.app as modulo_app
    monkeypatch.setattr(modulo_app, "get_recommendations", _capturar)

    cuerpo = sesion.post("/api/recommendations",
                         json={"lat": LAT, "lon": LON}).get_json()

    assert [p.name for p in recibidos] == ["Playa de Aguilar"]
    assert cuerpo["contexto"]["fuentes"]["pois"]["estado"] == "ok"


def test_zona_buscada_y_vacia_no_es_lo_mismo_que_no_buscada(
    sesion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hay zonas de España sin apenas mapear, y eso es un dato, no un fallo."""
    _cachear_pois([])

    import app.app as modulo_app
    monkeypatch.setattr(modulo_app, "get_recommendations",
                        lambda estado, pois, **kw: _reco_falsa())

    cuerpo = sesion.post("/api/recommendations",
                         json={"lat": LAT, "lon": LON}).get_json()

    assert cuerpo["contexto"]["fuentes"]["pois"]["estado"] == "sin_datos"


# ---------------------------------------------------------------------------
# La distinción, en el módulo
# ---------------------------------------------------------------------------

def test_pois_cacheados_distingue_vacio_de_no_consultado() -> None:
    """`None` y `[]` significan cosas distintas y por eso son valores distintos.

    Devolver `[]` en los dos casos habría sido cómodo y habría convertido "no
    lo he mirado" en "aquí no hay nada que ver", que es el fallo silencioso de
    la decisión 22.
    """
    assert pois_cacheados(LAT, LON) is None

    _cachear_pois([])
    assert pois_cacheados(LAT, LON) == []


def test_pois_cacheados_no_toca_la_red(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explota(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pois_cacheados ha salido a la red")

    monkeypatch.setattr(location_context, "_fetch_overpass", _explota)

    assert pois_cacheados(LAT, LON) is None


# ---------------------------------------------------------------------------
# El botón: /api/pois
# ---------------------------------------------------------------------------

def test_buscar_sitios_devuelve_los_puntos(sesion: Any,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        location_context, "_fetch_overpass",
        lambda query: {"elements": [_elemento("Playa de Aguilar", 43.5580, -6.1200)]},
    )

    cuerpo = sesion.post("/api/pois", json={"lat": LAT, "lon": LON}).get_json()

    assert cuerpo["fuente"]["estado"] == "ok"
    assert cuerpo["pois"][0]["name"] == "Playa de Aguilar"


def test_buscar_sitios_con_overpass_caido_lo_dice_en_voz_alta(
    sesion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El aviso NO se quita: quitarlo era el error que el encargo descarta.

    Lo que se ha quitado es la fuente del camino normal. Aquí, donde el usuario
    ha pedido explícitamente buscar, tiene que enterarse de que no se pudo.
    """
    def _caido(query: str) -> None:
        raise LocationError("No se pudo consultar el mapa de puntos de interés.")

    monkeypatch.setattr(location_context, "_fetch_overpass", _caido)

    respuesta = sesion.post("/api/pois", json={"lat": LAT, "lon": LON})
    cuerpo = respuesta.get_json()

    assert respuesta.status_code == 200
    assert cuerpo["fuente"]["estado"] == "fallo"
    assert cuerpo["pois"] == []
    assert any("puntos de interés" in w for w in cuerpo["warnings"])


def test_buscar_sitios_en_zona_vacia_no_es_un_fallo(sesion: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(location_context, "_fetch_overpass",
                        lambda query: {"elements": []})

    cuerpo = sesion.post("/api/pois", json={"lat": LAT, "lon": LON}).get_json()

    assert cuerpo["fuente"]["estado"] == "sin_datos"
    assert cuerpo["warnings"] == []


def test_buscar_sitios_necesita_sesion() -> None:
    flask_app.config["TESTING"] = True
    cliente = flask_app.test_client()

    respuesta = cliente.post("/api/pois", json={"lat": LAT, "lon": LON})

    assert respuesta.status_code == 401


def test_buscar_sitios_valida_las_coordenadas(sesion: Any) -> None:
    assert sesion.post("/api/pois", json={"lat": 999, "lon": 0}).status_code == 400
    assert sesion.post("/api/pois", json={"lat": LAT}).status_code == 400
