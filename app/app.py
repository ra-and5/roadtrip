"""Rutas de Flask. Conecta los módulos, nada más.

Regla que se mantiene en todo el proyecto: en este archivo NO hay lógica de
negocio. Cada vista hace tres cosas:
  1. Leer y validar la entrada de la petición.
  2. Llamar a un módulo.
  3. Formatear la respuesta.

Si una vista empieza a crecer, la lógica se va a un módulo.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from app.config import Config
from app.modules import (
    auth,
    chat,
    contexto,
    diario,
    ingest,
    notes,
    perfil,
    ruta,
    storage,
    waypoints,
)
from app.modules.ai_orchestrator import AIError, get_recommendations
from app.modules.ingest import IngestError
from app.modules.notes import NoteError
from app.modules.waypoints import WaypointError
from app.modules.llm_providers import build_provider, redact
from app.modules.location_context import (
    InvalidCoordinates,
    LocationError,
    find_nearby_pois,
    pois_cacheados,
    reverse_geocode,
)

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

# Cuánto del cuerpo se devuelve cuando no era JSON válido. Suficiente para ver
# el error en una muestra típica (~100 bytes) sin reflejar un cuerpo entero.
_MAX_ECO_CUERPO = 400


@app.after_request
def sin_cache_en_la_api(respuesta: Any) -> Any:
    """Ninguna respuesta de `/api/` se cachea. Las páginas y el estático, sí.

    Sin `Cache-Control`, un navegador puede reutilizar un GET por su cuenta
    (Safari en iOS lo hace), y entonces importas una foto nueva y el mapa sigue
    enseñando lo de antes **sin dar ningún error**: parece que la importación no
    llegó cuando lo que pasa es que no se ha vuelto a preguntar.

    Solo la API. El HTML y el JavaScript se siguen cacheando a propósito: es lo
    que hace que las páginas abran sin cobertura (decisión 28).
    """
    if request.path.startswith("/api/"):
        respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


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


@app.route("/mapa")
@auth.login_required
def mapa() -> Any:
    """El mapa acumulado del viaje.

    La página no lleva ninguna nota incrustada: las pide por `fetch` a
    `/api/notes`. No es purismo, es lo que hace que el mapa siga sirviendo de
    algo con mala cobertura: el HTML y el JavaScript los cachea el navegador y
    las chinchetas salen de nuestro servidor. Lo único que no cargará son los
    tiles, que vienen de OpenStreetMap.
    """
    return render_template("mapa.html")


@app.route("/perfil")
@auth.login_required
def perfil_page() -> Any:
    """Cómo estás tú. Los datos los pide por `fetch` a /api/perfil."""
    return render_template("perfil.html")


@app.route("/chat")
@auth.login_required
def chat_page() -> Any:
    """Preguntarle al contexto en vez de leerlo.

    La página no incrusta ningún mensaje: pide el historial por `fetch`, igual
    que el mapa pide las notas. Así el HTML y el JavaScript se cachean y lo
    único que hace falta al abrirla es una petición pequeña.
    """
    return render_template("chat.html")


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


def _coordenadas_de(payload: dict[str, Any]) -> tuple[float, float] | tuple[None, None]:
    """Saca lat/lon del cuerpo. `(None, None)` si no vienen las dos."""
    if "lat" not in payload or "lon" not in payload:
        return None, None
    return payload["lat"], payload["lon"]


@app.route("/api/contexto", methods=["POST"])
@auth.login_required
def api_contexto() -> Any:
    """Dónde estás y qué tiempo hace. Rápido, gratis y SIN llamar a ningún LLM.

    Es lo que pinta la pantalla nada más abrirla, y por eso está separado de
    `/api/recommendations`: antes, mirar la temperatura obligaba a pagar una
    llamada al modelo y a esperar a la fuente más lenta.

    **Qué devuelve cuando algo falla, que es la decisión de esta vista:**

        ubicación inválida -> 400. Culpa de quien llama.
        ubicación falla    -> 502. Sin saber dónde estás no hay contexto.
        cualquier otra     -> 200 con esa parte vacía.

    Que un `200` pueda traer partes vacías es seguro **aquí y solo aquí**
    porque el cuerpo trae su propio veredicto: `fuentes` dice de cada una si
    respondió, si no había nada que dar o si se cayó. Las decisiones 5 y 20
    avisan de lo contrario —un `200` cuyo cuerpo *parece* bueno—, y el remedio
    es justamente ese: nada hay que deducir de un `null`.
    """
    payload = request.get_json(silent=True) or {}
    lat, lon = _coordenadas_de(payload)
    if lat is None:
        return jsonify({"error": "Faltan 'lat' y/o 'lon'."}), 400

    try:
        estado = contexto.construir(lat, lon)
    except InvalidCoordinates as exc:
        # Va antes que LocationError porque es subclase suya: Python evalúa los
        # `except` en orden y se queda con el primero que encaja.
        return jsonify({"error": str(exc)}), 400
    except LocationError as exc:
        # 502: nuestro servidor está bien, el servicio del que dependemos no.
        return jsonify({"error": str(exc)}), 502

    # Queda constancia de dónde estabas la primera vez que hoy preguntaste. Va
    # aquí y no dentro de `contexto.construir()` a propósito: esa función la van
    # a llamar también el recomendador y el chatbot, y darle un efecto lateral
    # haría que preguntarle algo al chatbot escribiera en la base de datos.
    diario.registrar_lugar_del_dia(estado)

    return jsonify(estado.to_dict())


@app.route("/api/perfil", methods=["GET"])
@auth.login_required
def api_perfil() -> Any:
    """El cuaderno de a bordo: viaje, pasos y fiabilidad de cada fuente.

    GET y sin cuerpo porque no necesita ni GPS ni red: todo sale de SQLite, así
    que responde igual con el móvil sin cobertura. `zona` la manda el navegador
    (`Intl.DateTimeFormat`) porque el día es el local y el servidor va en UTC.
    """
    zona = (request.args.get("zona") or "")[:64]
    return jsonify(perfil.construir(zona or "Europe/Madrid").to_dict())


@app.route("/api/pois", methods=["POST"])
@auth.login_required
def api_pois() -> Any:
    """Busca sitios cerca en OpenStreetMap. Lento y a propósito bajo botón.

    Overpass está medido en 31,3 s desde el servidor cuando fallan los tres
    espejos (decisión 22), y eso era el 70 % de lo que tardaba la pantalla
    gastado en no obtener nada. La respuesta **no** fue silenciar el aviso —eso
    convierte un fallo ruidoso en uno silencioso, que es justo lo que se evitó
    al descartar el espejo suizo que respondía 200 con cero elementos— sino
    sacar la fuente del camino normal. Aquí esperar treinta segundos es una
    decisión de quien pulsa, no un peaje que paga todo el mundo.

    Lo que además deja hecho: la caché de POIs dura 7 días, así que a partir de
    una búsqueda, `/api/recommendations` los usa gratis en ese sitio.

    Devuelve 200 con `fuente` en el mismo vocabulario que `/api/contexto`, para
    que "no hay nada mapeado aquí" y "no se pudo consultar" no se confundan.
    """
    payload = request.get_json(silent=True) or {}
    lat, lon = _coordenadas_de(payload)
    if lat is None:
        return jsonify({"error": "Faltan 'lat' y/o 'lon'."}), 400

    try:
        pois = find_nearby_pois(lat, lon)
    except InvalidCoordinates as exc:
        return jsonify({"error": str(exc)}), 400
    except LocationError as exc:
        # 200 y no 502: la petición ha hecho su trabajo y la respuesta dice
        # exactamente qué ha pasado. Un 502 aquí obligaría al frontend a
        # distinguir "no hay sitios" de "error HTTP" por dos caminos distintos.
        fuente = contexto.Fuente(contexto.FALLO, str(exc))
        return jsonify({"pois": [], "fuente": fuente.to_dict(),
                        "warnings": [f"Sin puntos de interés cercanos: {exc}"]})

    fuente = contexto.Fuente(contexto.OK) if pois else contexto.Fuente(
        contexto.SIN_DATOS, "No hay nada mapeado en OpenStreetMap en esta zona."
    )
    return jsonify({
        "pois": [p.to_dict() for p in pois],
        "fuente": fuente.to_dict(),
        "warnings": [],
    })


@app.route("/api/recommendations", methods=["POST"])
@auth.login_required
def api_recommendations() -> Any:
    """Qué hacer aquí y ahora, según el modelo. Bajo botón, porque cuesta.

    Reconstruye el contexto con la MISMA función que `/api/contexto` en vez de
    resolverlo por su cuenta. Sale casi gratis: la pantalla acaba de pedirlo,
    así que Nominatim y Open-Meteo están cacheados.

    **La alternativa que se descarta: que el navegador mande el contexto en el
    cuerpo.** Es lo que sugería la letra del encargo ("recibe el contexto ya
    construido") y es la mala: el servidor estaría alimentando al modelo con lo
    que diga el cliente, tendría que revalidarlo entero, y un cuerpo manipulado
    pondría al modelo a razonar sobre un sitio y un tiempo inventados sin dar
    ningún error. "Ya construido" se cumple igual dentro del servidor, y el
    contrato con el frontend no cambia.

    La degradación en cascada se mantiene:

        ubicación falla -> 502, no hay nada que hacer
        tiempo falla    -> se recomienda sin tiempo, se avisa
        POIs falla      -> el modelo tira de conocimiento general, se avisa
        modelo falla    -> se devuelven igualmente contexto y POIs
    """
    payload = request.get_json(silent=True) or {}
    lat, lon = _coordenadas_de(payload)
    if lat is None:
        return jsonify({"error": "Faltan 'lat' y/o 'lon'."}), 400

    try:
        estado = contexto.construir(lat, lon)
    except InvalidCoordinates as exc:
        return jsonify({"error": str(exc)}), 400
    except LocationError as exc:
        return jsonify({"error": str(exc)}), 502

    # Los POIs, SOLO si ya están en caché. Esta vista no espera nunca a
    # Overpass: medido desde el servidor, un fallo de los tres espejos cuesta
    # 31,3 s, que era el 70 % de lo que tardaba la pantalla gastado en no
    # obtener nada. Buscarlos de verdad es una decisión del usuario y tiene su
    # propia ruta (`/api/pois`); aquí se aprovecha lo que esa búsqueda dejó
    # cacheado, que dura 7 días.
    pois = pois_cacheados(lat, lon)
    if pois is None:
        pois = []
        estado.fuentes["pois"] = contexto.Fuente(
            contexto.NO_CONSULTADA,
            "Nadie los ha buscado todavía en este sitio. La recomendación sale "
            "del conocimiento general del modelo, sin datos del mapa.",
        )
    elif not pois:
        estado.fuentes["pois"] = contexto.Fuente(
            contexto.SIN_DATOS,
            "No hay nada mapeado en OpenStreetMap en esta zona.",
        )
    else:
        estado.fuentes["pois"] = contexto.Fuente(contexto.OK)

    warnings: list[str] = estado.avisos()

    # El sitio y el tiempo van SOLO dentro de `contexto`. Estuvieron también en
    # la raíz mientras la pantalla no sabía leer el contexto, y se quitan ahora
    # que lo pinta todo con `renderContexto`. Dos copias del mismo dato en la
    # misma respuesta es una invitación a que diverjan, y entonces no habría
    # forma de saber cuál es la buena.
    body: dict[str, Any] = {
        "contexto": estado.to_dict(),
        "pois": [p.to_dict() for p in pois],
        "recommendation": None,
        "warnings": warnings,
    }

    # 2. La IA. Si falla, devolvemos 200 con el resto del contexto: tener el
    #    tiempo y los sitios cercanos sigue siendo útil sin la recomendación.
    try:
        recommendation = get_recommendations(
            estado, pois, use_cache=not payload.get("refresh", False)
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


@app.route("/api/chat", methods=["POST"])
@auth.login_required
def api_chat() -> Any:
    """Una pregunta sobre el viaje, respondida con el contexto de ahora mismo.

    **El contexto se rearma AQUÍ, no llega del navegador.** Es la misma decisión
    que en `/api/recommendations`: aceptar el contexto del cliente obligaría a
    revalidarlo entero y un cuerpo manipulado pondría al modelo a razonar sobre
    un sitio, un tiempo y unos pasos inventados sin dar ningún error.

    Se pide con `incluir_historia=True`, que es lo que distingue esta llamada de
    la de la pantalla: aquí sí hacen falta los pasos y el viaje, porque son la
    mitad de lo que se le puede preguntar.

    El orden de escritura importa: la pregunta se guarda **antes** de llamar al
    modelo. Si el proveedor falla o se agota la cuota, la pregunta no se pierde;
    al revés, un 429 se llevaría por delante lo que el usuario acababa de
    escribir. Es el mismo criterio que "archivo primero, fila después" de la
    decisión 27: entre dos fallos, el recuperable.
    """
    payload = request.get_json(silent=True) or {}

    pregunta = payload.get("mensaje")
    if not isinstance(pregunta, str) or not pregunta.strip():
        return jsonify({"error": "Falta 'mensaje'."}), 400
    pregunta = pregunta.strip()
    if len(pregunta) > chat.MAX_PREGUNTA:
        return jsonify(
            {"error": f"El mensaje es demasiado largo (máximo {chat.MAX_PREGUNTA})."}
        ), 400

    lat, lon = _coordenadas_de(payload)
    if lat is None:
        return jsonify({"error": "Faltan 'lat' y/o 'lon'."}), 400

    try:
        estado = contexto.construir(lat, lon, incluir_historia=True)
    except InvalidCoordinates as exc:
        return jsonify({"error": str(exc)}), 400
    except LocationError as exc:
        return jsonify({"error": str(exc)}), 502

    conversacion = chat.historial()
    chat.guardar("usuario", pregunta, estado.ubicacion)

    try:
        respuesta = chat.responder(pregunta, estado, conversacion)
    except AIError as exc:
        # Igual que en las recomendaciones: el detalle completo al log siempre,
        # y al usuario lo que decida SHOW_AI_ERROR_DETAIL. El mensaje ya viene
        # sin secretos desde llm_providers.
        app.logger.warning("Fallo de IA en el chat (%s): %s", Config.LLM_PROVIDER, exc)
        # 502 y no 500: nuestro servidor está bien, el proveedor no. La pregunta
        # ya está guardada, así que reintentar no la duplica en la pantalla.
        return jsonify({"error": str(exc), "warnings": estado.avisos()}), 502
    except Exception:  # noqa: BLE001
        app.logger.exception("Fallo inesperado en el chat")
        return jsonify({"error": "Error inesperado al responder."}), 500

    chat.guardar("asistente", respuesta.texto, estado.ubicacion)

    return jsonify(
        {
            "respuesta": respuesta.to_dict(),
            # Los avisos viajan con la respuesta en vez de esconderse: si el
            # tiempo se ha caído, el modelo ha contestado sin él y quien lee
            # tiene derecho a saberlo (decisión 9).
            "warnings": estado.avisos(),
            "lugar": estado.ubicacion.short_label(),
        }
    )


@app.route("/api/chat", methods=["GET"])
@auth.login_required
def api_chat_historial() -> Any:
    """La conversación guardada, para repintarla al abrir la página.

    Devuelve más mensajes de los que se le mandan al modelo, y eso no es una
    incoherencia: guardar es gratis y enviar se paga. Aquí se lee, no se razona.
    """
    return jsonify({"mensajes": chat.historial()})


@app.route("/api/chat", methods=["DELETE"])
@auth.login_required
def api_chat_borrar() -> Any:
    """Borra la conversación entera.

    Todo o nada: borrar una pregunta suelta dejaría su respuesta contestando a
    algo que ya no está.
    """
    return jsonify({"borrados": chat.borrar_historial()})


@app.route("/api/notes", methods=["POST"])
@auth.login_required
def api_crear_nota() -> Any:
    """Crea una nota geolocalizada. Idempotente por `client_id`.

    Con `@auth.login_required`, y aquí eso SÍ es lo correcto, al revés que en
    `/api/telemetria`: al otro lado hay una persona con un navegador que ya ha
    iniciado sesión. Que convivan los dos modelos de autenticación está bien
    **mientras cada ruta tenga exactamente uno**; que el token de ingesta no
    abra esta ruta lo fija un test, simétrico al que ya existe al revés.

    Los códigos de estado no son decorativos: son lo que la cola offline del
    navegador usa para decidir si borra la nota de la cola local o la
    reintenta. `201` y `200` significan "está a salvo en el servidor, bórrala";
    un `400` significa "esta nota no va a entrar nunca, deja de reintentarla";
    cualquier `5xx` o un fallo de red significa "no se sabe, guárdala".
    """
    payload = request.get_json(silent=True)

    try:
        resultado = notes.create_note(payload)
    except NoteError as exc:
        return jsonify({"error": str(exc)}), 400

    # 201 cuando se ha creado y 200 cuando ya existía. El cuerpo lo dice además
    # en `estado`, igual que la ingesta devuelve `guardadas`/`duplicadas`: una
    # nota duplicada es funcionamiento normal (un reintento que en realidad
    # había llegado bien), y quiero poder verlo sin mirar códigos de estado.
    return jsonify(resultado.to_dict()), (201 if resultado.creada else 200)


@app.route("/api/notes", methods=["GET"])
@auth.login_required
def api_listar_notas() -> Any:
    """Las notas del mapa y el progreso del viaje, en una sola petición.

    Una sola y no dos porque el cliente es un móvil con mala cobertura: dos
    peticiones son dos oportunidades de que una falle y la pantalla quede a
    medias.

    `progreso` se calcula SIEMPRE sobre todas las notas, aunque `year` filtre
    la lista. Es lo que permite comparar años sin pedir el histórico entero
    otra vez: el filtro cambia qué se pinta, no cuánto llevas hecho.
    """
    year: int | None = None
    bruto = request.args.get("year", "").strip()
    if bruto:
        try:
            year = int(bruto)
        except ValueError:
            return jsonify({"error": f"'year' no es un número: {bruto!r}"}), 400

    todas = notes.get_notes()

    return jsonify(
        {
            "total": len(todas),
            "notes": notes.solo_del_anio(todas, year),
            "progreso": notes.progreso(todas),
        }
    )


@app.route("/api/ruta", methods=["GET"])
@auth.login_required
def api_ruta() -> Any:
    """El viaje entero: la línea de tiempo, los días y el progreso, de una vez.

    Todo en una petición y no en tres, por lo mismo que en `/api/notes`: el
    cliente es un móvil con mala cobertura y cada petición extra es otra
    oportunidad de que la pantalla se quede a medias.

    `progreso` se calcula siempre sobre TODAS las notas aunque `year` filtre la
    línea de tiempo: el filtro cambia qué viaje se está mirando, no cuánto
    llevas hecho en total. Es lo que permite comparar años sin volver a pedir
    el histórico entero.
    """
    year: int | None = None
    bruto = request.args.get("year", "").strip()
    if bruto:
        try:
            year = int(bruto)
        except ValueError:
            return jsonify({"error": f"'year' no es un número: {bruto!r}"}), 400

    todas = notes.get_notes()
    linea = ruta.construir(
        notes.solo_del_anio(todas, year),
        storage.list_waypoints(),
        year=year,
    )

    return jsonify(
        {
            "resumen": linea["resumen"],
            "momentos": linea["momentos"],
            "dias": ruta.por_dias(linea["momentos"]),
            "progreso": notes.progreso(todas),
        }
    )


@app.route("/api/waypoints", methods=["POST"])
def api_waypoints() -> Any:
    """Recibe los metadatos de las fotos que manda `tools/importar_fotos.py`.

    Sin `@auth.login_required` y con el token de ingesta, igual que
    `/api/telemetria`: al otro lado hay un script, no un navegador con sesión.
    Que use el MISMO token y no uno propio es una decisión, no pereza. Un
    secreto más significa otro sitio donde guardarlo y otra cosa que rotar, y
    aquí no compraría nada: los dos clientes son máquinas que controla el dueño
    de la app, con el mismo nivel de confianza, y si el token se compromete hay
    que rotarlo igual para los dos.

    Lo que **no** se hace es aceptar además la cookie de sesión. Esa es la
    decisión 24 y sigue en pie: cada ruta, exactamente un camino de
    autenticación. Lo fija un test.
    """
    if not ingest.token_valido(request.headers.get("Authorization")):
        app.logger.warning("Importación de puntos rechazada: credencial inválida")
        return jsonify({"error": "no_autorizado"}), 401

    payload = request.get_json(silent=True)

    try:
        resultado = waypoints.import_waypoints(payload)
    except WaypointError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(resultado.to_dict())


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

    payload = request.get_json(silent=True)

    try:
        resultado = ingest.ingest(payload)
    except IngestError as exc:
        # Culpa del cliente y con el campo culpable en el mensaje: al otro lado
        # hay un atajo que alguien está escribiendo a mano, y "400" a secas no
        # se puede depurar desde un iPhone.
        cuerpo: dict[str, Any] = {"error": str(exc)}

        if payload is None:
            # El cuerpo ni siquiera era JSON. Aquí el mensaje solo puede decir
            # "esperaba un objeto JSON", que no ayuda a nadie: lo que hace falta
            # saber es QUÉ se envió. Devolverlo cierra el bucle de depuración
            # sin salir del móvil -- y montando el atajo del iPhone eso importa,
            # porque el teclado mete tildes, los decimales salen con coma y una
            # variable rota se envía vacía sin que Atajos avise (ver
            # docs/atajo-iphone.md). Sin esto, se depura a ciegas.
            #
            # Se devuelve solo el principio: un cuerpo entero en un mensaje de
            # error es ruido, y no hay razón para reflejar 128 KB de vuelta.
            # Y pasa por redact() por si acaso: es exactamente el sitio donde un
            # secreto mal pegado saldría reflejado hacia fuera.
            crudo = request.get_data(as_text=True)
            cuerpo["recibido"] = redact(crudo[:_MAX_ECO_CUERPO])
            if len(crudo) > _MAX_ECO_CUERPO:
                cuerpo["recibido"] += "..."

        return jsonify(cuerpo), 400

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
