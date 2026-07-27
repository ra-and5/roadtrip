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
from app.modules import auth, ingest, storage
from app.modules.ai_orchestrator import AIError, get_recommendations
from app.modules.ingest import IngestError
from app.modules.llm_providers import build_provider
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
# Y no viaja por http. Ver Config.SESSION_COOKIE_SECURE para el porqué del
# valor por defecto.
app.config["SESSION_COOKIE_SECURE"] = Config.SESSION_COOKIE_SECURE
# Techo del cuerpo de cualquier petición. Werkzeug corta aquí ANTES de que
# nadie parsee el JSON, que es donde se gastaría la CPU: en PythonAnywhere
# gratuito la CPU es cuota diaria y agotarla ralentiza la app entera. Ver
# Config.MAX_CONTENT_LENGTH para el porqué del valor.
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

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
        # El detalle completo va SIEMPRE al log, esté o no activado
        # SHOW_AI_ERROR_DETAIL: el interruptor decide qué ve el usuario en la
        # interfaz, no qué se registra para depurar. El mensaje ya viene sin
        # secretos desde llm_providers.
        app.logger.warning("Fallo de IA (%s): %s", Config.LLM_PROVIDER, exc)
        warnings.append(f"Sin recomendación de IA: {exc}")
    except Exception:  # noqa: BLE001
        app.logger.exception("Fallo inesperado generando recomendaciones")
        warnings.append("Sin recomendación de IA por un error inesperado.")

    body["warnings"] = warnings
    return jsonify(body)


@app.route("/api/telemetria", methods=["POST"])
def api_telemetria() -> Any:
    """Recibe las muestras que el iPhone envía desde un atajo de Atajos.

    Sin `@auth.login_required`, y eso es la decisión, no un olvido: este
    endpoint es público en internet y el cliente es una automatización que no
    inicia sesión ni guarda cookies. La autenticación es un token propio en la
    cabecera `Authorization`, y **solo** eso: la cookie de sesión no da acceso
    aquí. Dos caminos hacia el mismo sitio es como se cuelan los fallos de
    "confused deputy", y lo fija un test.

    El 401 es idéntico para las tres formas de fallar (sin cabecera, mal
    formada, token incorrecto). Un mensaje que distinga "token caducado" de
    "token inexistente" le está confirmando a quien prueba a ciegas que va por
    buen camino. Para depurar está `tools/diagnostico.py`, que sí dice si el
    hash está configurado.
    """
    if not ingest.token_valido(request.headers.get("Authorization")):
        # No se registra la cabecera ni ningún fragmento de ella: un token en
        # un log de PythonAnywhere es un token comprometido, y el atacante que
        # lo provoca elegiría qué queda escrito.
        app.logger.warning("Ingesta rechazada: credencial ausente o inválida")
        return jsonify({"error": "no_autorizado"}), 401

    try:
        resultado = ingest.ingest(request.get_json(silent=True))
    except IngestError as exc:
        # Culpa del cliente y con el campo culpable en el mensaje: al otro lado
        # hay un atajo que alguien está escribiendo a mano, y "400" a secas no
        # se puede depurar desde un iPhone.
        return jsonify({"error": str(exc)}), 400

    return jsonify(resultado.to_dict())


@app.route("/healthz")
def healthz() -> Any:
    """Comprobación de vida. Sin autenticación, a propósito.

    `ia_configurada` pregunta por el proveedor ACTIVO, no por una key concreta.
    Antes miraba solo `ANTHROPIC_API_KEY`, así que un despliegue sano con
    Gemini informaba `false`: justo el tipo de fallo silencioso de la
    decisión 11, pero en la herramienta con la que compruebas el despliegue.

    `build_provider()` valida el nombre del proveedor y su key sin llamar a la
    API: un health check no debe gastar cuota ni tardar 10 s. No se revela cuál
    es el proveedor ni por qué falla, porque este endpoint es público; para eso
    está `tools/diagnostico.py`.
    """
    try:
        build_provider()
        ia_configurada = True
    except AIError:
        ia_configurada = False

    return jsonify({"status": "ok", "ia_configurada": ia_configurada})


# ---------------------------------------------------------------------------
# Manejadores de error
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_: Any) -> Any:
    if request.path.startswith("/api/"):
        return jsonify({"error": "Ruta no encontrada."}), 404
    return render_template("error.html", code=404, message="Página no encontrada."), 404


@app.errorhandler(405)
def method_not_allowed(_: Any) -> Any:
    """Un GET a /api/telemetria, por ejemplo. Devuelve JSON, no HTML.

    Sin este manejador Flask contesta una página de error, y lo que hay al
    otro lado es un atajo del iPhone que espera JSON: recibir HTML se traduce
    en un error de parseo que no explica nada.
    """
    if request.path.startswith("/api/"):
        return jsonify({"error": "Método no permitido."}), 405
    return render_template("error.html", code=405, message="Método no permitido."), 405


@app.errorhandler(413)
def payload_too_large(_: Any) -> Any:
    """Cuerpo por encima de MAX_CONTENT_LENGTH. Lo corta Werkzeug antes de leerlo."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Cuerpo demasiado grande."}), 413
    return render_template("error.html", code=413, message="Envío demasiado grande."), 413


@app.errorhandler(500)
def server_error(_: Any) -> Any:
    if request.path.startswith("/api/"):
        return jsonify({"error": "Error interno del servidor."}), 500
    return render_template("error.html", code=500, message="Error interno."), 500
