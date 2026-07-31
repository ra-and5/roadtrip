"""Contexto meteorológico: Open-Meteo (previsión) + Open-Meteo Marine (oleaje).

Función de entrada: `get_weather(lat, lon) -> Weather`.

Este módulo no devuelve números crudos: devuelve datos **interpretados**
(¿se puede estar al aire libre?, ¿se puede hacer paddle surf?, ¿por qué no?).
El razonamiento meteorológico vive aquí, no en el prompt de Claude. Motivo:
es lógica determinista y testeable, y un LLM no debería hacer de meteorólogo
cuando unas reglas explícitas dan una respuesta mejor y auditable.

Open-Meteo no requiere API key.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import requests

from app.config import Config
from app.modules import storage

# El tiempo cambia. 30 minutos es el equilibrio entre estar al día y no
# machacar la API cada vez que se pulsa el botón.
_WEATHER_CACHE_TTL = 30 * 60

_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

# Códigos WMO -> descripción. Open-Meteo devuelve el código numérico; la
# traducción es cosa nuestra.
_WMO: dict[int, str] = {
    0: "despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "niebla",
    48: "niebla con escarcha",
    51: "llovizna débil",
    53: "llovizna moderada",
    55: "llovizna intensa",
    56: "llovizna helada",
    57: "llovizna helada intensa",
    61: "lluvia débil",
    63: "lluvia moderada",
    65: "lluvia fuerte",
    66: "lluvia helada",
    67: "lluvia helada fuerte",
    71: "nevada débil",
    73: "nevada moderada",
    75: "nevada fuerte",
    77: "granos de nieve",
    80: "chubascos débiles",
    81: "chubascos moderados",
    82: "chubascos fuertes",
    85: "chubascos de nieve",
    86: "chubascos de nieve fuertes",
    95: "tormenta",
    96: "tormenta con granizo",
    99: "tormenta con granizo fuerte",
}

# Códigos que implican precipitación activa (a partir de 51 = llovizna).
_WET_CODES = frozenset(range(51, 100))


class WeatherError(Exception):
    """Error recuperable al consultar el tiempo."""


@dataclass
class Marine:
    """Condiciones marítimas. Solo tiene sentido cerca de la costa."""

    wave_height_m: float | None = None
    wave_period_s: float | None = None
    wind_wave_height_m: float | None = None
    swell_wave_height_m: float | None = None
    sea_temperature_c: float | None = None
    # Por qué no hay datos, cuando el motivo es que la consulta falló. Vacío
    # cuando la API respondió bien (haya mar o no).
    #
    # Sin esto, "estoy en León" y "la API marina está caída" se veían
    # exactamente igual desde fuera —un `Marine` vacío— y son cosas
    # completamente distintas: la primera es normal y la segunda hay que
    # decirla. Es el corolario de la decisión 22 (distinguir "sin resultados"
    # de "no consultado") aplicado al oleaje, y hace falta ahora porque
    # `contexto.Fuente` informa de los dos casos por separado.
    fallo: str = ""

    def has_data(self) -> bool:
        """¿Hay datos reales?

        La API marina responde 200 con todos los campos a `null` tierra
        adentro, en vez de devolver un error. Comprobado contra la API real:
        asumir un 4xx habría sido un bug silencioso.
        """
        return self.wave_height_m is not None


@dataclass
class WaterSports:
    """Veredicto sobre deportes de agua (paddle surf, kayak)."""

    rating: str          # "excelente" | "aceptable" | "desaconsejado" | "sin datos"
    suitable: bool
    reason: str


@dataclass
class Weather:
    """Condiciones actuales más contexto del día."""

    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = None
    wind_speed_kmh: float | None = None
    wind_gusts_kmh: float | None = None
    weather_code: int | None = None
    description: str = ""
    is_day: bool = True
    today_max_c: float | None = None
    today_min_c: float | None = None
    precip_probability_pct: int | None = None
    sunrise: str = ""
    sunset: str = ""
    timezone: str = ""
    # Altitud del punto, en metros. La da Open-Meteo gratis en la misma
    # respuesta, así que no cuesta una llamada más. OJO con lo que es: la
    # altitud de la CELDA del modelo, no la del punto exacto — en un valle
    # estrecho puede diferir bastante del suelo que pisas. Sirve para "estoy a
    # 24 m" o "estoy a 1.200 m", no para calcular un desnivel.
    elevation_m: float | None = None
    marine: Marine = field(default_factory=Marine)

    # -- Interpretación ----------------------------------------------------

    def is_wet(self) -> bool:
        """¿Está lloviendo/nevando ahora mismo?"""
        if self.precipitation_mm is not None and self.precipitation_mm > 0.1:
            return True
        return self.weather_code in _WET_CODES if self.weather_code is not None else False

    def outdoor_rating(self) -> str:
        """Veredicto general para actividades al aire libre."""
        if self.weather_code in (95, 96, 99):
            return "peligroso"          # tormenta eléctrica
        if self.is_wet() and (self.precipitation_mm or 0) > 2.0:
            return "malo"
        if self.wind_gusts_kmh is not None and self.wind_gusts_kmh > 60:
            return "malo"
        if self.is_wet() or (self.wind_gusts_kmh or 0) > 40:
            return "regular"
        return "bueno"

    def water_sports(self) -> WaterSports:
        """Evalúa si es buen momento para paddle surf o kayak.

        Umbrales pensados para paddle surf recreativo en el Cantábrico:
        es un deporte de superficie plana, y el viento lo estropea antes que
        el oleaje. Un paddle con 25 km/h de viento es una lucha, no un paseo.
        """
        if not self.marine.has_data():
            return WaterSports(
                rating="sin datos",
                suitable=False,
                reason=(
                    self.marine.fallo
                    or "No hay datos de oleaje: esta ubicación no está junto al mar."
                ),
            )

        wave = self.marine.wave_height_m or 0.0
        period = self.marine.wave_period_s
        wind_wave = self.marine.wind_wave_height_m
        swell = self.marine.swell_wave_height_m
        wind = self.wind_speed_kmh or 0.0
        gusts = self.wind_gusts_kmh or wind

        def detalle(base: str) -> str:
            partes = [base, f"Ola {wave:.1f} m"]
            if period is not None:
                partes.append(f"periodo {period:.0f} s")
            if wind_wave is not None:
                partes.append(f"mar de viento {wind_wave:.1f} m")
            if swell is not None:
                partes.append(f"mar de fondo {swell:.1f} m")
            partes.append(f"viento {wind:.0f} km/h")
            partes.append(f"rachas {gusts:.0f} km/h")
            if self.precip_probability_pct is not None:
                partes.append(f"lluvia {self.precip_probability_pct}%")
            if self.marine.sea_temperature_c is not None:
                partes.append(f"agua {self.marine.sea_temperature_c:.0f} °C")
            return ". ".join([partes[0], ", ".join(partes[1:]) + "."])

        # El orden importa: se evalúa de lo más restrictivo a lo más permisivo,
        # y se devuelve el primer criterio que descarta.
        if self.weather_code in (95, 96, 99):
            return WaterSports(
                "desaconsejado", False,
                detalle("Hay tormenta eléctrica: no entres al agua."),
            )
        if wave > 1.2:
            return WaterSports(
                "desaconsejado", False,
                detalle("Oleaje demasiado alto para paddle surf recreativo."),
            )
        if gusts > 30:
            return WaterSports(
                "desaconsejado", False,
                detalle("Rachas fuertes: el viento puede arrastrarte mar adentro."),
            )
        if wave <= 0.5 and wind <= 12:
            return WaterSports(
                "excelente", True,
                detalle("Mar casi plano y viento flojo."),
            )
        return WaterSports(
            "aceptable", True,
            detalle("Viable, pero no es paseo plano."),
        )

    def summary(self) -> str:
        """Una línea legible, para la interfaz y para el prompt de Claude."""
        parts: list[str] = []
        if self.temperature_c is not None:
            parts.append(f"{self.temperature_c:.0f} °C")
            # La sensación térmica solo se menciona si difiere de forma
            # perceptible: repetir "19 °C (sensación 19 °C)" es ruido.
            if (
                self.apparent_temperature_c is not None
                and abs(self.apparent_temperature_c - self.temperature_c) >= 2
            ):
                parts.append(f"sensación {self.apparent_temperature_c:.0f} °C")
        if self.description:
            parts.append(self.description)
        if self.wind_speed_kmh is not None:
            parts.append(f"viento {self.wind_speed_kmh:.0f} km/h")
        if self.precip_probability_pct is not None:
            parts.append(f"prob. lluvia {self.precip_probability_pct}%")
        return ", ".join(parts) or "sin datos"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["summary"] = self.summary()
        data["outdoor_rating"] = self.outdoor_rating()
        data["is_wet"] = self.is_wet()
        data["water_sports"] = asdict(self.water_sports())
        data["marine"]["has_data"] = self.marine.has_data()
        return data


# ---------------------------------------------------------------------------
# Llamadas a la API
# ---------------------------------------------------------------------------

def _get_json(url: str, params: dict[str, Any], what: str) -> dict[str, Any]:
    """GET + JSON con los errores traducidos a WeatherError."""
    try:
        response = requests.get(url, params=params, timeout=Config.HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout as exc:
        raise WeatherError(f"El servicio de {what} tardó demasiado.") from exc
    except requests.ConnectionError as exc:
        raise WeatherError(f"Sin conexión con el servicio de {what}.") from exc
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        raise WeatherError(f"El servicio de {what} devolvió un error ({code}).") from exc
    except ValueError as exc:
        raise WeatherError(f"Respuesta ilegible del servicio de {what}.") from exc

    if not isinstance(payload, dict):
        raise WeatherError(f"Respuesta inesperada del servicio de {what}.")
    return payload


def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    return _get_json(
        Config.OPEN_METEO_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,apparent_temperature,precipitation,weather_code,"
                "wind_speed_10m,wind_gusts_10m,is_day"
            ),
            "daily": (
                "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                "sunrise,sunset"
            ),
            # timezone=auto hace que sunrise/sunset vengan en hora LOCAL del
            # punto consultado, que es lo que el usuario espera leer.
            "timezone": "auto",
            "forecast_days": 1,
        },
        "meteorología",
    )


def _fetch_marine(lat: float, lon: float) -> Marine:
    """Datos de oleaje. Nunca lanza: si falla, devuelve un Marine vacío.

    Es información opcional (solo aplica en la costa), así que un fallo aquí
    no debe tumbar toda la consulta del tiempo. Pero el fallo se **anota** en
    `Marine.fallo` en vez de tragárselo: un vacío por avería y un vacío por
    estar tierra adentro se ven igual desde fuera y no son lo mismo.
    """
    try:
        payload = _get_json(
            _MARINE_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "wave_height,wave_period,wind_wave_height,"
                    "swell_wave_height,sea_surface_temperature"
                ),
                "timezone": "auto",
            },
            "oleaje",
        )
    except WeatherError as exc:
        return Marine(fallo=str(exc))

    current = payload.get("current") or {}
    return Marine(
        wave_height_m=current.get("wave_height"),
        wave_period_s=current.get("wave_period"),
        wind_wave_height_m=current.get("wind_wave_height"),
        swell_wave_height_m=current.get("swell_wave_height"),
        sea_temperature_c=current.get("sea_surface_temperature"),
    )


def _parse_forecast(payload: dict[str, Any], marine: Marine) -> Weather:
    """Convierte la respuesta de Open-Meteo en un `Weather`."""
    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    def first(key: str) -> Any:
        """Los campos `daily` son listas (una entrada por día). Cogemos hoy."""
        values = daily.get(key)
        return values[0] if isinstance(values, list) and values else None

    code = current.get("weather_code")
    code_int = int(code) if isinstance(code, (int, float)) else None

    return Weather(
        temperature_c=current.get("temperature_2m"),
        apparent_temperature_c=current.get("apparent_temperature"),
        precipitation_mm=current.get("precipitation"),
        wind_speed_kmh=current.get("wind_speed_10m"),
        wind_gusts_kmh=current.get("wind_gusts_10m"),
        weather_code=code_int,
        description=_WMO.get(code_int, "condiciones desconocidas") if code_int is not None else "",
        is_day=bool(current.get("is_day", 1)),
        today_max_c=first("temperature_2m_max"),
        today_min_c=first("temperature_2m_min"),
        precip_probability_pct=first("precipitation_probability_max"),
        sunrise=str(first("sunrise") or ""),
        sunset=str(first("sunset") or ""),
        timezone=str(payload.get("timezone", "")),
        elevation_m=payload.get("elevation"),
        marine=marine,
    )


def get_weather(lat: float, lon: float) -> Weather:
    """Devuelve las condiciones meteorológicas actuales e interpretadas.

    Args:
        lat, lon: coordenadas en grados decimales.

    Returns:
        Un `Weather`. Si el punto no está en la costa, `weather.marine` estará
        vacío y `water_sports()` devolverá "sin datos" — no es un error.

    Raises:
        WeatherError: la previsión (no el oleaje) no se pudo obtener.
    """
    cache_key = storage.cache_key_for_coords("weather", lat, lon)
    cached = storage.cache_get(cache_key, _WEATHER_CACHE_TTL)
    if cached is not None:
        return _parse_forecast(cached["forecast"], Marine(**cached["marine"]))

    # En SERIE, y a propósito. Son dos servicios independientes, así que
    # paralelizarlos parece la respuesta obvia y fue lo primero que se probó.
    # No es que los hilos sean caros —eso se midió y es falso—: es que estas
    # funciones escriben en la caché de SQLite, y en PythonAnywhere la base de
    # datos vive en un disco de red donde el bloqueo de escritura se paga muy
    # caro. Está razonado entero en `contexto.construir()`, que es donde se vio:
    # 34 s para envolver 0,15 s de trabajo.
    #
    # La previsión va primera porque es la obligatoria: si falla, no se gasta la
    # llamada del oleaje para acabar lanzando igual.
    forecast = _fetch_forecast(lat, lon)
    marine = _fetch_marine(lat, lon)

    # El `Marine` se guarda tal cual, incluso cuando trae `fallo`. Es
    # deliberado y va en la misma línea que la decisión 12 (no reintentar
    # contra un muro): si la API marina está caída, no cachear el fallo haría
    # que CADA petición de los siguientes 30 minutos pagase su timeout entero,
    # y el contexto tiene que responder por debajo de dos segundos. Así falla
    # rápido, y `Marine.fallo` deja escrito que es una avería y no que aquí no
    # haya mar. Se reevalúa media hora después, sola.
    storage.cache_set(cache_key, {"forecast": forecast, "marine": asdict(marine)})
    return _parse_forecast(forecast, marine)
