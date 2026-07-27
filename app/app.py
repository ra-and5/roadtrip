"""Rutas de Flask. Conecta los módulos, nada más.

Regla que se mantiene en todo el proyecto: en este archivo NO hay lógica de
negocio. Cada vista hace tres cosas:
  1. Leer y validar la entrada de la petición.
  2. Llamar a un módulo.
  3. Formatear la respuesta.

Si una vista empieza a crecer, la lógica se va a un módulo.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from app.config import Config
from app.modules import auth, storage
from app.modules.ai_orchestrator import AIError, get_recommendations
from app.modules.location_context import (
    InvalidCoordinates,
    LocationError,
    Place,
    Poi,
    find_nearby_pois,
    reverse_geocode,
)
from app.modules.weather_context import Weather, WeatherError, get_weather

app = Flask(__name__)
app.config["SECRET_KEY"] = Config.SECRET_KEY
# Cuánto dura la sesión "permanente" (ver auth.login_user).
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=90)
# La cookie de sesión no debe viajar en peticiones cross-site.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Crear el esquema al arrancar. Es idempotente, así que da igual cuántos
# workers levante PythonAnywhere.
storage.init_db()


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if auth.is_logged_in():
        return redirect(url_for("index"))

    error: str | None = None
    if request.method == "POST":
        if auth.check_password(request.form.get("password", "")):
            auth.login_user()
            # `next` viene del decorador login_required. Validamos que sea una
            # ruta interna: si aceptáramos cualquier valor, un enlace malicioso
            # podría redirigir a un sitio externo tras el login (open redirect).
            next_path = request.args.get("next", "")
            if next_path.startswith("/") and not next_path.startswith("//"):
                return redirect(next_path)
            return redirect(url_for("index"))
        error = "Contraseña incorrecta."

    return render_template("login.html", error=error), (401 if error else 200)


@app.route("/logout")
def logout() -> Any:
    auth.logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

@app.route("/")
@auth.login_required
def index() -> Any:
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/location", methods=["POST"])
@auth.login_required
def api_location() -> Any:
    """Recibe {lat, lon} del GPS del navegador y devuelve el lugar resuelto."""
    payload = request.get_json(silent=True) or {}

    if "lat" not in payload or "lon" not in payload:
        return jsonify({"error": "Faltan 'lat' y/o 'lon'."}), 400

    try:
        place = reverse_geocode(payload["lat"], payload["lon"])
    except InvalidCoordinates as exc:
        # Culpa del cliente: nos ha mandado basura.
        # (Va antes que LocationError porque es una subclase suya: Python
        # evalúa los `except` en orden y se queda con el primero que encaja.)
        return jsonify({"error": str(exc)}), 400
    except LocationError as exc:
        # 502 Bad Gateway: nuestro servidor está bien, el servicio del que
        # dependemos no. Comunica mejor el problema que un 500 genérico.
        return jsonify({"error": str(exc)}), 502

    return jsonify({"place": place.to_dict()})


@app.route("/api/recommendations", methods=["POST"])
@auth.login_required
def api_recommendations() -> Any:
    """El endpoint principal: coordenadas -> qué hacer aquí y ahora.

    La propiedad importante de esta vista es la **degradación en cascada**.
    Solo una fuente es imprescindible (la ubicación); todas las demás pueden
    fallar de forma independiente y la respuesta sigue siendo útil:

        ubicación falla -> 502, no hay nada que hacer
        tiempo falla    -> se recomienda sin tiempo, se avisa
        POIs falla      -> Claude tira de conocimiento general, se avisa
        Claude falla    -> se devuelven igualmente ubicación, tiempo y POIs

    Los avisos van en `warnings` para que el frontend los muestre: una app que
    silencia que le falta la mitad del contexto no es fiable, es opaca.
    """
    payload = request.get_json(silent=True) or {}
    if "lat" not in payload or "lon" not in payload:
        return jsonify({"error": "Faltan 'lat' y/o 'lon'."}), 400

    # 1. Ubicación: única fuente imprescindible.
    try:
        place: Place = reverse_geocode(payload["lat"], payload["lon"])
    except InvalidCoordinates as exc:
        return jsonify({"error": str(exc)}), 400
    except LocationError as exc:
        return jsonify({"error": str(exc)}), 502

    lat, lon = place.lat, place.lon
    warnings: list[str] = []

    # 2. Tiempo y POIs en paralelo. Overpass tarda entre 2 y 20 segundos y
    #    Open-Meteo menos de 1: en serie pagas la suma, en paralelo el máximo.
    #    Son dos llamadas de red independientes, así que dos hilos bastan
    #    (esperan a la red, no consumen CPU).
    with ThreadPoolExecutor(max_workers=2) as pool:
        weather_future = pool.submit(get_weather, lat, lon)
        pois_future = pool.submit(find_nearby_pois, lat, lon)

        weather: Weather | None = None
        try:
            weather = weather_future.result()
        except WeatherError as exc:
            warnings.append(f"Sin datos meteorológicos: {exc}")
        except Exception:  # noqa: BLE001 - una fuente opcional nunca tumba la petición
            warnings.append("Sin datos meteorológicos por un error inesperado.")

        pois: list[Poi] = []
        try:
            pois = pois_future.result()
        except LocationError as exc:
            warnings.append(f"Sin puntos de interés cercanos: {exc}")
        except Exception:  # noqa: BLE001
            warnings.append("Sin puntos de interés por un error inesperado.")

    if not pois and not warnings:
        warnings.append("No hay puntos de interés mapeados en esta zona de OpenStreetMap.")

    body: dict[str, Any] = {
        "place": place.to_dict(),
        "weather": weather.to_dict() if weather else None,
        "pois": [p.to_dict() for p in pois],
        "recommendation": None,
        "warnings": warnings,
    }

    # 3. La IA. Si falla, devolvemos 200 con el resto del contexto: tener el
    #    tiempo y los sitios cercanos sigue siendo útil sin la recomendación.
    try:
        recommendation = get_recommendations(
            place, weather, pois, use_cache=not payload.get("refresh", False)
        )
        body["recommendation"] = recommendation.to_dict()
    except AIError as exc:
        warnings.append(f"Sin recomendación de IA: {exc}")
    except Exception:  # noqa: BLE001
        app.logger.exception("Fallo inesperado generando recomendaciones")
        warnings.append("Sin recomendación de IA por un error inesperado.")

    body["warnings"] = warnings
    return jsonify(body)


@app.route("/healthz")
def healthz() -> Any:
    """Comprobación de vida. Sin autenticación, a propósito."""
    return jsonify({"status": "ok", "ia_configurada": bool(Config.ANTHROPIC_API_KEY)})


# ---------------------------------------------------------------------------
# Manejadores de error
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_: Any) -> Any:
    if request.path.startswith("/api/"):
        return jsonify({"error": "Ruta no encontrada."}), 404
    return render_template("error.html", code=404, message="Página no encontrada."), 404


@app.errorhandler(500)
def server_error(_: Any) -> Any:
    if request.path.startswith("/api/"):
        return jsonify({"error": "Error interno del servidor."}), 500
    return render_template("error.html", code=500, message="Error interno."), 500
