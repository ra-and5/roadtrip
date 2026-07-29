"""Tests de la interpretación meteorológica.

Nada aquí toca la red: probamos las REGLAS, que es donde está el riesgo real.
Un fallo en `water_sports()` no da un error, da un consejo malo — y un consejo
malo sobre entrar al mar con rachas de 35 km/h es peor que un crash.
"""

import pytest

from app.modules.weather_context import Marine, Weather, _parse_forecast


def w(**kwargs) -> Weather:
    """Constructor breve con valores por defecto benignos."""
    base = dict(temperature_c=20.0, wind_speed_kmh=8.0, wind_gusts_kmh=12.0,
                weather_code=0, precipitation_mm=0.0)
    base.update(kwargs)
    return Weather(**base)


# --- Marine.has_data ------------------------------------------------------

def test_marine_sin_datos_cuando_los_campos_son_none():
    """Tierra adentro la API responde 200 con nulls, no un 4xx.

    Comprobado contra la API real: este test protege el comportamiento del que
    depende toda la degradación de deportes de agua.
    """
    assert Marine().has_data() is False
    assert Marine(wave_height_m=None, wave_period_s=None).has_data() is False


def test_marine_con_datos():
    assert Marine(wave_height_m=0.4).has_data() is True


# --- Deportes de agua -----------------------------------------------------

def test_agua_sin_datos_tierra_adentro():
    result = w().water_sports()
    assert result.rating == "sin datos"
    assert result.suitable is False


def test_agua_excelente_con_mar_plano_y_sin_viento():
    weather = w(wind_speed_kmh=6.0, wind_gusts_kmh=9.0,
                marine=Marine(wave_height_m=0.3, sea_temperature_c=21.0))
    result = weather.water_sports()
    assert result.rating == "excelente"
    assert result.suitable is True
    assert "21" in result.reason  # menciona la temperatura del agua


def test_agua_desaconsejada_por_oleaje():
    weather = w(marine=Marine(wave_height_m=1.5))
    result = weather.water_sports()
    assert result.rating == "desaconsejado"
    assert result.suitable is False
    assert "1.5" in result.reason  # el motivo es concreto, no genérico


def test_agua_desaconsejada_por_rachas_aunque_el_mar_este_plano():
    """El viento descarta el paddle antes que el oleaje. Caso real y peligroso."""
    weather = w(wind_speed_kmh=25.0, wind_gusts_kmh=40.0, marine=Marine(wave_height_m=0.2))
    result = weather.water_sports()
    assert result.suitable is False
    assert "40" in result.reason


def test_agua_desaconsejada_con_tormenta():
    weather = w(weather_code=95, wind_speed_kmh=5.0, wind_gusts_kmh=8.0,
                marine=Marine(wave_height_m=0.2))
    result = weather.water_sports()
    assert result.suitable is False
    assert "tormenta" in result.reason.lower()


def test_agua_aceptable_en_condiciones_intermedias():
    weather = w(wind_speed_kmh=18.0, wind_gusts_kmh=25.0, marine=Marine(wave_height_m=0.8))
    result = weather.water_sports()
    assert result.rating == "aceptable"
    assert result.suitable is True


# --- Aire libre -----------------------------------------------------------

@pytest.mark.parametrize("code", [95, 96, 99])
def test_aire_libre_peligroso_con_tormenta(code):
    assert w(weather_code=code).outdoor_rating() == "peligroso"


def test_aire_libre_malo_con_lluvia_fuerte():
    assert w(weather_code=65, precipitation_mm=5.0).outdoor_rating() == "malo"


def test_aire_libre_malo_con_vendaval():
    assert w(wind_gusts_kmh=75.0).outdoor_rating() == "malo"


def test_aire_libre_regular_con_llovizna():
    assert w(weather_code=51, precipitation_mm=0.4).outdoor_rating() == "regular"


def test_aire_libre_bueno_despejado():
    assert w().outdoor_rating() == "bueno"


def test_is_wet_detecta_precipitacion_por_codigo_sin_mm():
    """A veces llega el código de lluvia con 0.0 mm acumulados en ese instante."""
    assert w(weather_code=61, precipitation_mm=0.0).is_wet() is True


# --- Resumen legible ------------------------------------------------------

def test_summary_omite_sensacion_termica_si_es_igual():
    """Repetir '20 °C (sensación 20 °C)' es ruido."""
    assert "sensación" not in w(apparent_temperature_c=20.5).summary()


def test_summary_incluye_sensacion_termica_si_difiere():
    assert "sensación" in w(apparent_temperature_c=26.0).summary()


# --- Parseo de la respuesta real ------------------------------------------

def test_parse_forecast_con_respuesta_real_de_open_meteo():
    """Payload capturado de la API real (Cudillero)."""
    payload = {
        "timezone": "Europe/Madrid",
        "current": {
            "temperature_2m": 19.9, "apparent_temperature": 22.0, "precipitation": 0.0,
            "weather_code": 3, "wind_speed_10m": 2.9, "wind_gusts_10m": 5.4, "is_day": 0,
        },
        "daily": {
            "temperature_2m_max": [23.6], "temperature_2m_min": [19.2],
            "precipitation_probability_max": [10],
            "sunrise": ["2026-07-27T07:08"], "sunset": ["2026-07-27T21:53"],
        },
    }
    weather = _parse_forecast(payload, Marine(wave_height_m=1.48))

    assert weather.temperature_c == 19.9
    assert weather.description == "nublado"
    assert weather.is_day is False
    assert weather.today_max_c == 23.6
    assert weather.sunset.endswith("21:53")
    assert weather.timezone == "Europe/Madrid"


def test_parse_forecast_tolera_payload_vacio():
    """La API puede devolver 200 con menos campos de los esperados."""
    weather = _parse_forecast({}, Marine())
    assert weather.temperature_c is None
    assert weather.summary() == "sin datos"


# ---------------------------------------------------------------------------
# La previsión y el oleaje van EN PARALELO
# ---------------------------------------------------------------------------


def test_el_tiempo_no_monta_hilos(monkeypatch: pytest.MonkeyPatch) -> None:
    """La previsión y el oleaje van en serie, igual que el resto del contexto.

    Se intentó al revés —son dos servicios independientes, paralelizarlos es lo
    que dice el manual—. El problema no son los hilos: es que estas funciones
    escriben en la caché de SQLite, y en PythonAnywhere eso es un disco de red
    donde el bloqueo de escritura se paga carísimo. Medido allí: 34 s para
    envolver 0,15 s de llamadas, y 0,18 s en serie. El razonamiento entero está
    en `contexto.construir()`, que es donde se vio.

    Y aquí el argumento del paralelismo era todavía más flojo: estas dos
    respuestas llegan en 0,2 s, así que lo que había que ganar eran 0,2 s.
    """
    import inspect

    from app.modules import weather_context

    imports = [
        l for l in inspect.getsource(weather_context).splitlines()
        if l.startswith(("import ", "from "))
    ]

    assert not [l for l in imports if "concurrent.futures" in l], (
        "han vuelto los hilos: mídelo en el SERVIDOR antes (tools/medir_contexto.py)"
    )


def test_un_fallo_de_la_prevision_no_gasta_la_llamada_del_oleaje(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La obligatoria va primera, ahora que el orden se paga.

    Si la previsión falla no hay `Weather` que devolver, así que consultar el
    oleaje después de saberlo sería gastar una llamada —y en un solo worker, un
    timeout entero— para acabar lanzando igual.
    """
    from app.modules import storage, weather_context
    from app.modules.weather_context import WeatherError

    llamadas: list[str] = []

    def _revienta(lat: float, lon: float) -> dict:
        llamadas.append("forecast")
        raise WeatherError("El servicio de previsión tardó demasiado.")

    def _marine(lat: float, lon: float) -> Marine:
        llamadas.append("marine")
        return Marine()

    monkeypatch.setattr(weather_context, "_fetch_forecast", _revienta)
    monkeypatch.setattr(weather_context, "_fetch_marine", _marine)
    monkeypatch.setattr(storage, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(storage, "cache_set", lambda *a, **k: None)

    with pytest.raises(WeatherError):
        weather_context.get_weather(43.5622, -6.1456)

    assert llamadas == ["forecast"], f"se gastó la llamada del oleaje: {llamadas}"


def test_un_fallo_de_la_prevision_sale_aunque_el_oleaje_vaya_bien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paralelizar no puede tragarse la excepción que sí importa.

    El oleaje es opcional y nunca lanza; la previsión es obligatoria. Al
    recoger primero el futuro que no lanza, la excepción de la previsión tiene
    que seguir saliendo igual.
    """
    from app.modules import storage, weather_context
    from app.modules.weather_context import WeatherError

    def _revienta(lat: float, lon: float) -> dict:
        raise WeatherError("El servicio de previsión tardó demasiado.")

    monkeypatch.setattr(weather_context, "_fetch_forecast", _revienta)
    monkeypatch.setattr(weather_context, "_fetch_marine", lambda lat, lon: Marine())
    monkeypatch.setattr(storage, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(storage, "cache_set", lambda *a, **k: None)

    with pytest.raises(WeatherError):
        weather_context.get_weather(43.5622, -6.1456)
