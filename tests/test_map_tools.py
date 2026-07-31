"""Herramientas de mapa para el chat.

Sin red y sin API keys. Aquí se prueba el contrato: qué se detecta, cómo se
normaliza y cómo se degrada cuando Google no está configurado.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from app.config import Config
from app.modules import map_tools, storage
from app.modules.location_context import Place


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    storage.init_db()
    yield


def test_detecta_sitios_practicos() -> None:
    assert map_tools.detectar_sitio("bar más cerca") == "bar"
    assert map_tools.detectar_sitio("necesito una farmacia abierta") == "farmacia"
    assert map_tools.detectar_sitio("área camper para dormir") == "area camper"


def test_detecta_plan_cerca_como_varias_consultas_acotadas() -> None:
    assert map_tools.detectar_consultas_sitios("qué hago cerca para cenar?") == [
        "restaurante",
        "bar",
        "cafe",
    ]
    assert map_tools.detectar_consultas_sitios("dónde puedo dormir y comprar agua?") == [
        "area camper",
        "camping",
        "supermercado",
    ]


def test_detecta_ruta_de_origen_a_destino() -> None:
    assert map_tools.detectar_ruta("cuánto tardo de Burgos a Vitoria?") == (
        "Burgos",
        "Vitoria",
    )


def test_detecta_ruta_desde_ubicacion_actual() -> None:
    lugar = Place(lat=38.39, lon=-0.51, name="San Vicente")

    assert map_tools.detectar_ruta("cuánto tardo a Vitoria?", lugar) == (
        "San Vicente",
        "Vitoria",
    )


def test_sin_key_no_revienta_y_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "GOOGLE_MAPS_API_KEY", "")
    lugar = Place(lat=38.39, lon=-0.51, name="San Vicente")

    bundle = map_tools.ejecutar("bar más cerca", lugar)

    assert not bundle.sitios
    assert any("GOOGLE_MAPS_API_KEY" in aviso for aviso in bundle.avisos)


def test_parsea_places_con_field_mask_minimo() -> None:
    payload = {
        "places": [
            {
                "displayName": {"text": "Bar Pepe"},
                "primaryType": "bar",
                "formattedAddress": "Calle Mayor 1",
                "location": {"latitude": 38.4, "longitude": -0.5},
                "rating": 4.2,
                "currentOpeningHours": {"openNow": True},
                "googleMapsLinks": {"placeUri": "https://maps.google.com/?cid=1"},
            }
        ]
    }

    sitio = map_tools._parse_places(payload)[0]  # noqa: SLF001

    assert sitio.nombre == "Bar Pepe"
    assert sitio.abierto == "abierto ahora"
    assert sitio.rating == 4.2


def test_parsea_route_y_url_de_maps() -> None:
    payload = {
        "routes": [
            {
                "distanceMeters": 121000,
                "duration": "5400s",
                "staticDuration": "5100s",
            }
        ]
    }

    ruta = map_tools._parse_route(payload, "Burgos", "Vitoria")  # noqa: SLF001

    assert ruta.distancia_km == 121.0
    assert ruta.duracion_trafico_min == 90
    assert "google.com/maps/dir" in ruta.maps_url


def test_formatea_resultados_para_prompt() -> None:
    bundle = map_tools.ToolBundle(
        sitios=[
            map_tools.ToolPlace(
                nombre="Bar Pepe",
                abierto="abierto ahora",
                rating=4.2,
                direccion="Calle Mayor 1",
                maps_url="https://maps.example/bar",
            )
        ],
        ruta=map_tools.ToolRoute(
            origen="Burgos",
            destino="Vitoria",
            distancia_km=121,
            duracion_trafico_min=90,
            maps_url="https://maps.example/ruta",
        ),
    )

    texto = map_tools.formatear(bundle)

    assert "SITIOS CONSULTADOS" in texto
    assert "Bar Pepe" in texto
    assert "RUTA CONSULTADA" in texto
    assert "90 min con tráfico" in texto


class FakeMaps:
    def __init__(self) -> None:
        self.consultas: list[str] = []

    def buscar_sitios(self, consulta: str, lat: float, lon: float) -> list[map_tools.ToolPlace]:
        self.consultas.append(consulta)
        return [
            map_tools.ToolPlace(nombre=f"{consulta} 1", direccion="cerca")
        ]

    def calcular_ruta(self, origen: str, destino: str) -> map_tools.ToolRoute:
        return map_tools.ToolRoute(origen=origen, destino=destino)


def test_ejecuta_varias_herramientas_de_plan_sin_pasarse() -> None:
    provider = FakeMaps()

    bundle = map_tools.ejecutar(
        "plan para cenar, aparcar y comprar cerca",
        Place(lat=38.39, lon=-0.51, name="San Vicente"),
        provider=provider,
    )

    assert provider.consultas == ["restaurante", "bar", "cafe"]
    assert len(bundle.sitios) == 3


def test_memoria_basica_lee_sqlite() -> None:
    storage.insert_note(
        {
            "client_id": "n1",
            "text": "Dormí junto al puerto",
            "photo_path": None,
            "lat": 43.0,
            "lon": -5.0,
            "place_name": "Puerto bonito",
            "region": "Asturias",
            "created_at": "2026-07-30T22:00:00+00:00",
            "offset_original": "+02:00",
            "received_at": "2026-07-30T22:01:00+00:00",
        }
    )

    bundle = map_tools.ejecutar(
        "dónde dormí ayer?",
        Place(lat=43.0, lon=-5.0, name="Puerto bonito"),
        provider=map_tools.GoogleMapsProvider(api_key=""),
    )

    assert any("Dormí junto al puerto" in linea for linea in bundle.memoria)
