"""Tests de las notas geolocalizadas (Fase 3).

Sin red y sin API keys, con el test client de Flask y nunca con HTTP real.

Lo que se protege aquí, por orden de importancia:

  - **La idempotencia.** La misma nota (mismo `client_id`) dos veces se guarda
    UNA. No es una optimización: es la propiedad de la que depende toda la cola
    offline. Si falla, cada zona sin cobertura del viaje deja notas duplicadas
    en el mapa.
  - **La frontera de autenticación.** Estas rutas son de sesión, y el token de
    ingesta de la Fase 2d NO las abre. Es el test simétrico del que ya existe
    al revés (`test_la_cookie_de_sesion_no_abre_la_ingesta`): dos caminos de
    autenticación hacia el mismo sitio es como se cuelan los *confused deputy*.
  - **La validación,** regla a regla y con su caso límite. Una coordenada
    imposible o una fecha corrupta no dan error: se guardan y ensucian el mapa
    y el resumen del viaje para siempre.
  - **El progreso del mapa,** que es una función pura y por tanto se prueba con
    listas escritas a mano. Que la racha o los días cuenten mal no rompe nada:
    solo miente, que es peor.

Nota sobre la base de datos: cada test corre contra una SQLite en `tmp_path`,
parcheando `Config` en vez de reimportar `app.config`, por lo explicado en
`test_app_despliegue.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

from app.app import app as flask_app
from app.config import Config
from app.modules import notes, storage
from app.modules.notes import NoteError

RUTA = "/api/notes"

TOKEN_INGESTA = "token-de-ingesta-de-mentira_AbCdEf123456"
HASH_TOKEN = generate_password_hash(TOKEN_INGESTA, method="pbkdf2:sha256:1000")


def _iso(desfase: timedelta = timedelta(0), huso: str = "+02:00") -> str:
    """Un instante ISO 8601 con huso, como el que manda el navegador."""
    tz = timezone(timedelta(hours=int(huso[:3]), minutes=int(huso[4:])))
    return (datetime.now(tz) + desfase).replace(microsecond=0).isoformat()


def _nota(**campos: Any) -> dict[str, Any]:
    """Una nota válida, con lo que se le quiera cambiar."""
    base: dict[str, Any] = {
        "client_id": str(uuid4()),
        "text": "Mirador sobre la playa, viento fuerte",
        "lat": 43.36129,
        "lon": -8.41151,
        "created_at": _iso(-timedelta(minutes=10)),
        "place_name": "Cudillero, Asturias",
        "region": "Asturias",
    }
    base.update(campos)
    return base


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "INGEST_TOKEN_HASH", HASH_TOKEN)
    storage.init_db()
    yield


@pytest.fixture
def cliente() -> Iterator[Any]:
    flask_app.config["TESTING"] = True
    yield flask_app.test_client()


@pytest.fixture
def sesion(cliente: Any) -> Any:
    """Un cliente con la sesión ya iniciada, que es como se usan estas rutas."""
    with cliente.session_transaction() as s:
        s["authenticated"] = True
    return cliente


def _guardadas() -> list[dict[str, Any]]:
    return storage.list_notes(1000)


# ---------------------------------------------------------------------------
# Idempotencia: la propiedad de la que cuelga la cola offline
# ---------------------------------------------------------------------------

def test_la_misma_nota_dos_veces_se_guarda_una_sola_vez(sesion: Any) -> None:
    """Y la segunda respuesta lo dice, en vez de fingir que la ha creado.

    Este es el reintento normal de la cola: la nota llegó bien, pero la
    respuesta se perdió al entrar en un túnel, así que el móvil la reenvía.
    Duplicarla sería llenar el mapa de chinchetas repetidas cada vez que se cae
    la cobertura; contestar "creada" otra vez sería mentirle a la cola sobre
    qué acaba de pasar.
    """
    nota = _nota()

    primera = sesion.post(RUTA, json=nota)
    segunda = sesion.post(RUTA, json=nota)

    assert primera.status_code == 201
    assert primera.get_json()["estado"] == "creada"

    assert segunda.status_code == 200
    assert segunda.get_json()["estado"] == "duplicada"

    # El id es el mismo: la cola tiene que poder enlazar lo que borra con lo que
    # hay en el servidor.
    assert primera.get_json()["id"] == segunda.get_json()["id"]
    assert len(_guardadas()) == 1


def test_el_reintento_no_pisa_el_texto_original(sesion: Any) -> None:
    """`INSERT OR IGNORE` ignora, no actualiza. Y eso es lo que queremos.

    Si un reintento sobrescribiera, una nota corrupta en la cola local podría
    machacar la buena que ya está a salvo en el servidor. Ante la duda, gana lo
    que ya está guardado.
    """
    nota = _nota(text="El texto bueno")
    sesion.post(RUTA, json=nota)
    sesion.post(RUTA, json={**nota, "text": "algo distinto"})

    assert [n["text"] for n in _guardadas()] == ["El texto bueno"]


def test_dos_notas_distintas_se_guardan_las_dos(sesion: Any) -> None:
    """La idempotencia va por `client_id`, no por contenido: dos notas iguales
    escritas en el mismo sitio son dos notas."""
    texto = "Misma frase, dos momentos"
    sesion.post(RUTA, json=_nota(text=texto))
    sesion.post(RUTA, json=_nota(text=texto))

    assert len(_guardadas()) == 2


# ---------------------------------------------------------------------------
# Autenticación: aquí manda la sesión, y SOLO la sesión
# ---------------------------------------------------------------------------

def test_sin_sesion_la_api_devuelve_401(cliente: Any) -> None:
    """401 con JSON, no una redirección: al otro lado hay un `fetch`.

    Si la API contestara con la página de login, la cola offline recibiría un
    200 lleno de HTML y daría la nota por enviada. Se perdería.
    """
    respuesta = cliente.post(RUTA, json=_nota())

    assert respuesta.status_code == 401
    assert respuesta.get_json() == {"error": "no_autenticado"}
    assert _guardadas() == []

    listado = cliente.get(RUTA)
    assert listado.status_code == 401


def test_sin_sesion_la_pagina_del_mapa_redirige_al_login(cliente: Any) -> None:
    """Una persona con un navegador tiene que ver el login, no un 401 crudo."""
    respuesta = cliente.get("/mapa")

    assert respuesta.status_code == 302
    assert "/login" in respuesta.headers["Location"]


def test_el_token_de_ingesta_no_abre_las_rutas_de_notas(cliente: Any) -> None:
    """La frontera de esta fase, simétrica a la de la 2d.

    El token de la Fase 2d vive en claro dentro del iPhone, en un atajo, y
    autentica a una MÁQUINA para escribir en una tabla concreta. Que además
    abriera las notas convertiría la pérdida del móvil en acceso a todo lo que
    se ha escrito durante el viaje.

    Al revés ya está fijado por `test_la_cookie_de_sesion_no_abre_la_ingesta`.
    Cada ruta tiene exactamente un camino de autenticación; ese es el invariante
    que hace que la seguridad de esta app se pueda razonar entera.
    """
    cabecera = {"Authorization": f"Bearer {TOKEN_INGESTA}"}

    creacion = cliente.post(RUTA, json=_nota(), headers=cabecera)
    listado = cliente.get(RUTA, headers=cabecera)

    assert creacion.status_code == 401
    assert listado.status_code == 401
    assert _guardadas() == []


# ---------------------------------------------------------------------------
# Validación, regla a regla
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cambios, motivo",
    [
        ({"client_id": "no-es-un-uuid"}, "client_id que no es UUID"),
        ({"client_id": "../../etc/passwd"}, "client_id con path traversal"),
        ({"client_id": "6F4B1E2A-8C3D-4A91-B7E0-1F2C3D4E5A6B"}, "UUID en mayúsculas"),
        ({"client_id": None}, "sin client_id"),
        ({"client_id": 12345}, "client_id numérico"),
        ({"text": ""}, "nota vacía"),
        ({"text": "   "}, "nota con solo espacios"),
        ({"text": None}, "sin texto"),
        ({"text": 42}, "texto que no es texto"),
        ({"text": "x" * (notes.MAX_TEXTO + 1)}, "texto por encima del límite"),
        ({"lat": None}, "sin latitud"),
        ({"lon": None}, "sin longitud"),
        ({"lat": 91}, "latitud fuera de rango"),
        ({"lat": -91}, "latitud fuera de rango por abajo"),
        ({"lon": 181}, "longitud fuera de rango"),
        ({"lat": "43.36"}, "latitud como cadena"),
        ({"lat": True}, "latitud booleana"),
        ({"created_at": None}, "sin fecha"),
        ({"created_at": ""}, "fecha vacía"),
        ({"created_at": "28/07/2026 11:32"}, "fecha que no es ISO 8601"),
        ({"created_at": "2026-07-28T11:32:05"}, "fecha ISO 8601 SIN zona horaria"),
        ({"created_at": 1753606800}, "fecha como epoch"),
        ({"place_name": 42}, "place_name que no es texto"),
    ],
)
def test_una_nota_invalida_se_rechaza_con_400(
    sesion: Any, cambios: dict[str, Any], motivo: str
) -> None:
    """Y no se guarda nada.

    El 400 importa tanto como el rechazo: es lo que le dice a la cola offline
    que esta nota no va a entrar nunca y que deje de reintentarla. Un 500 la
    dejaría reintentándose para siempre y atascaría la cola detrás de ella.
    """
    respuesta = sesion.post(RUTA, json=_nota(**cambios))

    assert respuesta.status_code == 400, motivo
    assert "error" in respuesta.get_json(), motivo
    assert _guardadas() == [], motivo


@pytest.mark.parametrize(
    "cambios, motivo",
    [
        ({"lat": 90, "lon": 180}, "el extremo del rango es válido"),
        ({"lat": -90, "lon": -180}, "el otro extremo también"),
        ({"text": "x" * notes.MAX_TEXTO}, "el texto en el límite exacto entra"),
        ({"place_name": None, "region": None}, "el nombre del sitio es opcional"),
        ({"created_at": _iso(timedelta(hours=23))}, "23 h en el futuro: reloj desfasado"),
        ({"created_at": _iso(-timedelta(days=29))}, "29 días atrás: cola muy larga"),
        ({"created_at": _iso(huso="+00:00")}, "una fecha ya en UTC"),
    ],
)
def test_los_casos_limite_validos_se_aceptan(
    sesion: Any, cambios: dict[str, Any], motivo: str
) -> None:
    """El otro lado del límite. Sin esto, un validador demasiado estricto pasa
    los tests de rechazo y tira notas buenas en el viaje."""
    respuesta = sesion.post(RUTA, json=_nota(**cambios))

    assert respuesta.status_code == 201, motivo
    assert len(_guardadas()) == 1, motivo


@pytest.mark.parametrize(
    "desfase, motivo",
    [
        (timedelta(hours=25), "más de 24 h en el futuro"),
        (-timedelta(days=31), "más de 30 días en el pasado"),
    ],
)
def test_una_fecha_increible_se_rechaza(
    sesion: Any, desfase: timedelta, motivo: str
) -> None:
    """Una fecha corrupta no da error: se guarda y desplaza la nota en el mapa
    y en el resumen del viaje, para siempre y sin avisar."""
    respuesta = sesion.post(RUTA, json=_nota(created_at=_iso(desfase)))

    assert respuesta.status_code == 400, motivo
    assert "created_at" in respuesta.get_json()["error"], motivo


def test_el_cuerpo_que_no_es_json_no_revienta(sesion: Any) -> None:
    respuesta = sesion.post(RUTA, data="esto no es json", content_type="text/plain")

    assert respuesta.status_code == 400
    assert "error" in respuesta.get_json()


def test_el_cliente_no_puede_dictar_cuando_llego_la_nota(sesion: Any) -> None:
    """`received_at` lo pone el servidor y solo el servidor.

    Es su medida de cuándo se enteró, y `received_at - created_at` es la única
    prueba de que la cola offline funcionó. Un cliente que pudiera fijarlo
    podría hacer que el retraso pareciera cero justo cuando interesa saber que
    no lo fue.
    """
    hace_seis_horas = _iso(-timedelta(hours=6))
    sesion.post(RUTA, json=_nota(created_at=hace_seis_horas, received_at=hace_seis_horas))

    fila = _guardadas()[0]
    recibido = datetime.fromisoformat(fila["received_at"])
    assert (datetime.now(timezone.utc) - recibido).total_seconds() < 60


def test_el_texto_se_guarda_sin_espacios_de_sobra(sesion: Any) -> None:
    sesion.post(RUTA, json=_nota(text="  con espacios alrededor  "))

    assert _guardadas()[0]["text"] == "con espacios alrededor"


# ---------------------------------------------------------------------------
# Canonización de la fecha
# ---------------------------------------------------------------------------

def test_la_fecha_se_guarda_en_utc_y_conserva_el_huso(sesion: Any) -> None:
    """Guardar en UTC es lo que hace comparables dos notas de husos distintos;
    conservar el desfase es lo que permite recuperar la hora local desde una
    consola del servidor, que corre en UTC."""
    sesion.post(RUTA, json=_nota(created_at="2026-07-28T11:32:05+02:00"))

    fila = _guardadas()[0]
    assert fila["created_at"] == "2026-07-28T09:32:05+00:00"
    assert fila["offset_original"] == "+02:00"


def test_una_fecha_ya_en_utc_no_guarda_desfase(sesion: Any) -> None:
    """"+00:00" ya está en `created_at`; repetirlo es ruido."""
    sesion.post(RUTA, json=_nota(created_at="2026-07-28T09:32:05+00:00"))

    assert _guardadas()[0]["offset_original"] is None


def test_la_api_devuelve_la_hora_local_de_donde_se_escribio(sesion: Any) -> None:
    """No la del navegador que la mira.

    El móvil acertaría por casualidad (está en el huso del viaje), pero un
    portátil desde casa en invierno enseñaría las notas de agosto una hora
    corridas. La hora de una nota es la del sitio donde se escribió.
    """
    sesion.post(RUTA, json=_nota(created_at="2026-07-28T11:32:05+02:00"))

    nota = sesion.get(RUTA).get_json()["notes"][0]
    assert nota["created_at_local"] == "2026-07-28T11:32:05+02:00"


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------

def test_el_listado_ordena_por_cuando_se_escribio_no_por_cuando_llego(
    sesion: Any,
) -> None:
    """Con cola offline no son lo mismo, y el mapa cuenta el viaje, no el tráfico.

    Aquí se envía primero la nota más ANTIGUA (la que estuvo atascada en la
    cola sin cobertura) y después la de hoy. Ordenar por `id` las pondría al
    revés y el relato del viaje saldría desordenado.
    """
    sesion.post(RUTA, json=_nota(text="martes sin cobertura", created_at=_iso(-timedelta(days=2))))
    sesion.post(RUTA, json=_nota(text="hoy", created_at=_iso(-timedelta(minutes=1))))

    textos = [n["text"] for n in sesion.get(RUTA).get_json()["notes"]]
    assert textos == ["hoy", "martes sin cobertura"]


def test_el_listado_va_vacio_y_sin_romperse_cuando_no_hay_nada(sesion: Any) -> None:
    """El primer día del viaje el mapa está vacío, y tiene que cargar igual."""
    cuerpo = sesion.get(RUTA).get_json()

    assert cuerpo["notes"] == []
    assert cuerpo["total"] == 0
    assert cuerpo["progreso"]["total"] == 0


def test_el_filtro_por_anio_no_cambia_el_progreso(sesion: Any) -> None:
    """El filtro decide qué se pinta, no cuánto llevas hecho.

    Es lo que permite comparar años sin volver a pedir el histórico entero.
    """
    sesion.post(RUTA, json=_nota(created_at=_iso(-timedelta(minutes=5))))

    anio_actual = datetime.now().year
    con_filtro = sesion.get(f"{RUTA}?year={anio_actual}").get_json()
    sin_nada = sesion.get(f"{RUTA}?year=1999").get_json()

    assert len(con_filtro["notes"]) == 1
    assert sin_nada["notes"] == []
    assert sin_nada["progreso"]["total"] == 1


def test_un_anio_que_no_es_un_numero_da_400(sesion: Any) -> None:
    assert sesion.get(f"{RUTA}?year=el-verano-pasado").status_code == 400


def test_la_pagina_del_mapa_carga_y_sirve_las_notas(sesion: Any) -> None:
    sesion.post(RUTA, json=_nota(text="una nota en el mapa"))

    pagina = sesion.get("/mapa")
    assert pagina.status_code == 200

    datos = sesion.get(RUTA).get_json()
    assert [n["text"] for n in datos["notes"]] == ["una nota en el mapa"]


def test_leaflet_se_sirve_desde_nuestro_static_y_no_desde_un_cdn(sesion: Any) -> None:
    """Un CDN es un tercero más que puede caerse.

    Con mala cobertura, el navegador tiene más probabilidades de tener nuestro
    archivo en caché (ya ha entrado a la app) que de alcanzar unpkg.com desde
    un camping. Que la página no referencie un CDN es comprobable; que Leaflet
    pinte bien, no: eso solo se ve en un navegador.
    """
    html = sesion.get("/mapa").data

    assert b"/static/vendor/leaflet/leaflet.js" in html
    assert b"/static/vendor/leaflet/leaflet.css" in html
    for cdn in (b"unpkg.com", b"cdnjs", b"jsdelivr"):
        assert cdn not in html


def test_los_archivos_de_leaflet_estan_de_verdad_en_el_repositorio() -> None:
    """Referenciar `/static/vendor/leaflet/leaflet.js` y que el archivo no esté
    daría un mapa en blanco en producción y ningún error en la suite."""
    from app.config import BASE_DIR

    vendor = BASE_DIR / "app" / "static" / "vendor" / "leaflet"
    assert (vendor / "leaflet.js").is_file()
    assert (vendor / "leaflet.css").is_file()
    # Leaflet busca los iconos en `images/` relativo al CSS: si esa carpeta se
    # mueve, las chinchetas desaparecen sin dar ningún error de consola.
    assert (vendor / "images" / "marker-icon.png").is_file()
    assert (vendor / "images" / "marker-shadow.png").is_file()


# ---------------------------------------------------------------------------
# El tablero de comunidades: el mapa como algo que se completa
# ---------------------------------------------------------------------------

def test_el_tablero_enseña_lo_que_falta_y_no_solo_lo_visitado() -> None:
    """"Llevas 2 de 19" es una frase que se entiende sola; "2 regiones" no.

    Un contador que solo cuenta lo hecho no puede decirte lo que te falta, que
    es justamente lo que hace que un mapa se quiera completar.
    """
    tablero = notes.tablero_regiones(["Galicia", "Asturias"])

    assert tablero["completadas"] == 2
    assert tablero["total"] == 19
    assert len(tablero["casillas"]) == 19
    assert {c["nombre"] for c in tablero["casillas"] if c["visitada"]} == {
        "Galicia",
        "Asturias",
    }


@pytest.mark.parametrize(
    "nombre, esperado",
    [
        ("Principado de Asturias", "Asturias"),
        ("Comunidad de Madrid", "Madrid"),
        ("Región de Murcia", "Murcia"),
        ("Comunidad Foral de Navarra", "Navarra"),
        ("Comunitat Valenciana", "Comunidad Valenciana"),
        ("Illes Balears", "Islas Baleares"),
        ("Catalunya", "Cataluña"),
        ("Euskadi", "País Vasco"),
        ("  galicia  ", "Galicia"),
        ("Castilla La Mancha", "Castilla-La Mancha"),
    ],
)
def test_el_nombre_oficial_de_nominatim_encaja_con_la_casilla(
    nombre: str, esperado: str
) -> None:
    """Comparar las cadenas tal cual dejaría la casilla apagada habiendo estado
    allí: un fallo que no da error, solo un tablero que miente."""
    tablero = notes.tablero_regiones([nombre])

    visitadas = [c["nombre"] for c in tablero["casillas"] if c["visitada"]]
    assert visitadas == [esperado]
    assert tablero["otras"] == []


def test_una_region_de_fuera_no_desaparece_del_recuento() -> None:
    """Una nota de Portugal no puede esfumarse sin que nadie se entere.

    La app enseña lo que no sabe encajar en vez de disimularlo (decisión 9).
    """
    tablero = notes.tablero_regiones(["Galicia", "Norte", "Nouvelle-Aquitaine"])

    assert tablero["completadas"] == 1
    assert tablero["otras"] == ["Norte", "Nouvelle-Aquitaine"]


def test_la_misma_comunidad_por_dos_nombres_no_cuenta_dos_veces() -> None:
    tablero = notes.tablero_regiones(["Asturias", "Principado de Asturias"])

    assert tablero["completadas"] == 1


# ---------------------------------------------------------------------------
# El módulo, sin Flask
# ---------------------------------------------------------------------------

def test_la_validacion_se_puede_probar_sin_levantar_la_app() -> None:
    """Es el objetivo del diseño, no una casualidad: la validación vive en el
    módulo y no en la ruta, así que se prueba llamando a una función."""
    nota = notes.parse_note(_nota(text="  hola  "))

    assert nota.text == "hola"
    assert nota.created_at.endswith("+00:00")

    with pytest.raises(NoteError, match="vacía"):
        notes.parse_note(_nota(text=""))


def test_una_nota_que_no_es_un_objeto_se_rechaza() -> None:
    for basura in [None, [], "texto", 42]:
        with pytest.raises(NoteError):
            notes.parse_note(basura)


# ---------------------------------------------------------------------------
# Progreso del mapa: función pura, se prueba con listas escritas a mano
# ---------------------------------------------------------------------------

def _publica(dia: str, lat: float = 43.36, lon: float = -8.41, region: str = "Galicia",
             lugar: str = "A Coruña") -> dict[str, Any]:
    """Una nota ya en su forma pública, para probar el progreso sin base de datos."""
    return {
        "id": 1,
        "client_id": str(uuid4()),
        "text": "nota",
        "lat": lat,
        "lon": lon,
        "place_name": lugar,
        "region": region,
        "created_at": f"{dia}T10:00:00+00:00",
        "created_at_local": f"{dia}T12:00:00+02:00",
        "received_at": f"{dia}T10:05:00+00:00",
        "photo_url": None,
    }


def test_el_progreso_de_un_mapa_vacio_no_revienta() -> None:
    vacio = notes.progreso([])

    assert vacio["total"] == 0
    assert vacio["racha_maxima"] == 0
    assert vacio["regiones"] == []


def test_varias_notas_en_el_mismo_pueblo_son_un_solo_lugar() -> None:
    """A ~110 m de distancia sigues en el mismo sitio.

    Con la precisión fina de la caché de APIs, pasear por un pueblo escribiendo
    notas contaría como varios lugares visitados y el contador premiaría
    caminar en vez de viajar.
    """
    mismas = [
        _publica("2026-07-28", lat=43.3610, lon=-8.4110),
        _publica("2026-07-28", lat=43.3618, lon=-8.4119),
        _publica("2026-07-28", lat=43.3605, lon=-8.4105),
    ]

    assert notes.progreso(mismas)["lugares"] == 1


def test_dos_pueblos_distintos_son_dos_lugares() -> None:
    dos = [_publica("2026-07-28", lat=43.36, lon=-8.41),
           _publica("2026-07-29", lat=43.55, lon=-7.02)]

    assert notes.progreso(dos)["lugares"] == 2


def test_la_racha_cuenta_dias_seguidos_y_se_corta_con_un_hueco() -> None:
    """Premia lo que cuesta —salir todos los días— y no lo que es gratis."""
    seguidos = [_publica(d) for d in ("2026-07-01", "2026-07-02", "2026-07-03")]
    assert notes.progreso(seguidos)["racha_maxima"] == 3

    con_hueco = [_publica(d) for d in ("2026-07-01", "2026-07-02", "2026-07-05")]
    assert notes.progreso(con_hueco)["racha_maxima"] == 2


def test_diez_notas_en_un_dia_no_son_una_racha_de_diez() -> None:
    """El día es la unidad, no la nota. Si no, escribir mucho sentado en un bar
    daría la misma racha que recorrer el norte entero."""
    mismo_dia = [_publica("2026-07-01") for _ in range(10)]

    progreso = notes.progreso(mismo_dia)
    assert progreso["racha_maxima"] == 1
    assert progreso["dias"] == 1
    assert progreso["total"] == 10


def test_las_regiones_no_se_repiten_y_van_ordenadas() -> None:
    varias = [
        _publica("2026-07-01", region="Galicia"),
        _publica("2026-07-02", region="Asturias", lat=43.55, lon=-6.1),
        _publica("2026-07-03", region="Galicia"),
    ]

    assert notes.progreso(varias)["regiones"] == ["Asturias", "Galicia"]


def test_solo_aparece_en_mas_visitados_el_sitio_al_que_se_ha_vuelto() -> None:
    """Una lista donde todo pone "1 visita" no responde a ninguna pregunta.
    La pregunta es "¿a qué sitios vuelvo siempre?"."""
    notas = [
        _publica("2026-07-01", lat=43.36, lon=-8.41, lugar="A Coruña"),
        _publica("2026-08-15", lat=43.36, lon=-8.41, lugar="A Coruña"),
        _publica("2026-07-05", lat=42.88, lon=-8.54, lugar="Santiago"),
    ]

    visitados = notes.progreso(notas)["mas_visitados"]

    assert [v["etiqueta"] for v in visitados] == ["A Coruña"]
    assert visitados[0]["dias"] == 2


def test_el_mismo_sitio_en_un_solo_dia_no_cuenta_como_volver() -> None:
    """Cinco notas en una tarde no son cinco visitas."""
    tarde = [_publica("2026-07-01", lat=43.36, lon=-8.41) for _ in range(5)]

    assert notes.progreso(tarde)["mas_visitados"] == []


def test_el_progreso_separa_los_anios_para_poder_compararlos() -> None:
    """Es lo que hace posible "el año pasado hice más sitios que este"."""
    dos_anios = [
        _publica("2025-08-01", lat=43.36, lon=-8.41),
        _publica("2026-07-28", lat=43.55, lon=-7.02),
        _publica("2026-07-29", lat=43.55, lon=-7.02),
    ]

    por_anio = notes.progreso(dos_anios)["por_anio"]

    assert por_anio["2025"]["notas"] == 1
    assert por_anio["2026"]["notas"] == 2
    assert por_anio["2026"]["dias"] == 2
    assert por_anio["2026"]["lugares"] == 1


def test_el_dia_se_cuenta_en_hora_local_no_en_utc() -> None:
    """Una nota de las 00:30 en España es del día siguiente en UTC.

    Contarla por UTC desplazaría un día entero del viaje, y en un mapa que
    cuenta días y rachas eso no da error: da un número equivocado.
    """
    medianoche = {
        **_publica("2026-07-28"),
        "created_at": "2026-07-27T22:30:00+00:00",
        "created_at_local": "2026-07-28T00:30:00+02:00",
    }
    siguiente = {
        **_publica("2026-07-29"),
        "created_at": "2026-07-29T08:00:00+00:00",
        "created_at_local": "2026-07-29T10:00:00+02:00",
    }

    # Días locales 28 y 29: seguidos. Por UTC serían 27 y 29, con un hueco.
    assert notes.progreso([medianoche, siguiente])["racha_maxima"] == 2
