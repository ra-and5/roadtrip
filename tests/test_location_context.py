"""Tests del módulo location_context.

Todo lo que se testea aquí es puro: no toca la red ni la base de datos. Por eso
los tests son rápidos y se pueden ejecutar sin cobertura, en el coche.

Ejecutar:  pytest -q
"""

import pytest

from app.modules.location_context import (
    InvalidCoordinates,
    LocationError,
    Place,
    _parse_nominatim,
    _validate_coords,
)


# --- Validación de coordenadas -------------------------------------------

def test_validate_coords_acepta_valores_correctos():
    assert _validate_coords(43.36, -8.41) == (43.36, -8.41)


def test_validate_coords_convierte_cadenas():
    """El JSON de una petición puede traer números como strings."""
    assert _validate_coords("43.36", "-8.41") == (43.36, -8.41)


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_validate_coords_rechaza_fuera_de_rango(lat, lon):
    with pytest.raises(InvalidCoordinates):
        _validate_coords(lat, lon)


def test_validate_coords_rechaza_no_numericos():
    with pytest.raises(InvalidCoordinates):
        _validate_coords("norte", "oeste")


def test_invalid_coordinates_es_un_location_error():
    """Quien no quiera distinguir debe poder capturar solo LocationError."""
    assert issubclass(InvalidCoordinates, LocationError)


# --- Parseo de la respuesta de Nominatim ---------------------------------

def test_parse_extrae_campos_de_un_pueblo():
    payload = {
        "display_name": "Cudillero, Asturias, España",
        "address": {
            "village": "Cudillero",
            "municipality": "Cudillero",
            "county": "Asturias",
            "state": "Asturias",
            "country": "España",
        },
    }
    place = _parse_nominatim(payload, 43.56, -6.14)

    assert place.name == "Cudillero"
    assert place.region == "Asturias"
    assert place.country == "España"
    assert place.lat == 43.56


def test_parse_tolera_direccion_incompleta():
    """En una playa aislada Nominatim solo devuelve la región. No debe reventar."""
    place = _parse_nominatim({"address": {"state": "Cantabria"}}, 43.4, -3.8)

    assert place.name == ""
    assert place.region == "Cantabria"
    assert place.short_label() == "Cantabria"


def test_parse_tolera_respuesta_sin_address():
    place = _parse_nominatim({}, 43.0, -5.0)
    assert place.short_label() == "Ubicación desconocida"


# --- Etiqueta corta -------------------------------------------------------

def test_short_label_combina_lugar_y_region():
    place = Place(lat=0, lon=0, name="Llanes", region="Asturias")
    assert place.short_label() == "Llanes, Asturias"


def test_short_label_no_repite_si_coinciden():
    place = Place(lat=0, lon=0, name="Madrid", region="Madrid")
    assert place.short_label() == "Madrid"


def test_to_dict_omite_raw_y_anade_etiqueta():
    place = Place(lat=1.0, lon=2.0, name="Ribadeo", region="Galicia", raw={"mucho": "ruido"})
    data = place.to_dict()

    assert "raw" not in data
    assert data["short_label"] == "Ribadeo, Galicia"
