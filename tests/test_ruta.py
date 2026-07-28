"""Tests de los puntos del viaje y de la ruta (Fase 3b).

Lo que se protege aquí:

  - **La idempotencia de la importación.** Volcar las fotos del móvil y
    reimportar la carpeta entera es lo normal, no un accidente. Si esto falla,
    cada importación duplica el viaje.
  - **La frontera de autenticación.** `/api/waypoints` va con token, como la
    ingesta, y la cookie de sesión NO lo abre. Es la decisión 24 aplicada a una
    ruta nueva.
  - **Que los números no mientan.** La distancia, los días y el orden son
    cifras que cuando están mal no dan ningún error: solo cuentan otro viaje.
    En particular, que la suma de los kilómetros de cada día sea EXACTAMENTE
    el total, que es donde ya se coló una incoherencia.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
from werkzeug.security import generate_password_hash

from app.app import app as flask_app
from app.config import Config
from app.modules import ruta, storage, waypoints
from app.modules.waypoints import WaypointError

RUTA = "/api/waypoints"

TOKEN = "token-de-ingesta-de-mentira_AbCdEf123456"
HASH_TOKEN = generate_password_hash(TOKEN, method="pbkdf2:sha256:1000")


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _punto(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "archivo": "IMG_4213.JPG",
        "capturado_en": "2026-07-28T14:32:05",
        "offset_original": "+02:00",
        "lat": 43.5619,
        "lon": -6.1467,
        "altitud": 123.4,
        "camara": "Apple iPhone 15",
    }
    base.update(campos)
    return base


def _cuerpo(*puntos: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"puntos": list(puntos)}
    payload.update(extra)
    return payload


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


def _guardados() -> list[dict[str, Any]]:
    return storage.list_waypoints()


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------

def test_reimportar_la_misma_carpeta_no_duplica_el_viaje(cliente: Any) -> None:
    """Volcar las fotos del móvil y reimportar todo es el uso normal.

    Sin esto, cada vez que se importa la carpeta el mapa gana una copia entera
    del viaje, y a la tercera el trayecto es ilegible.
    """
    lote = _cuerpo(_punto(archivo="A.JPG"), _punto(archivo="B.JPG"))

    primera = cliente.post(RUTA, json=lote, headers=_auth())
    segunda = cliente.post(RUTA, json=lote, headers=_auth())

    assert primera.get_json() == {
        "guardados": 2, "duplicados": 0, "descartados": 0, "errores": [],
    }
    assert segunda.get_json()["guardados"] == 0
    assert segunda.get_json()["duplicados"] == 2
    assert len(_guardados()) == 2


def test_la_clave_es_el_nombre_del_archivo_no_la_fecha(cliente: Any) -> None:
    """Dos fotos del mismo segundo (una ráfaga) son dos puntos distintos."""
    cliente.post(
        RUTA,
        json=_cuerpo(
            _punto(archivo="rafaga1.JPG", capturado_en="2026-07-28T14:32:05"),
            _punto(archivo="rafaga2.JPG", capturado_en="2026-07-28T14:32:05"),
        ),
        headers=_auth(),
    )

    assert len(_guardados()) == 2


def test_un_punto_malo_no_tumba_el_lote(cliente: Any) -> None:
    """Una foto con el EXIF corrupto entre mil buenas se descarta y se cuenta.

    Lo contrario haría que una sola foto rara tirase la importación de un viaje
    entero, justo en el lote más largo.
    """
    respuesta = cliente.post(
        RUTA,
        json=_cuerpo(
            _punto(archivo="buena1.JPG"),
            _punto(archivo="mala.JPG", lat=999),
            _punto(archivo="buena2.JPG"),
        ),
        headers=_auth(),
    )

    cuerpo = respuesta.get_json()
    assert cuerpo["guardados"] == 2
    assert cuerpo["descartados"] == 1
    assert "lat" in cuerpo["errores"][0]
    assert {p["archivo"] for p in _guardados()} == {"buena1.JPG", "buena2.JPG"}


# ---------------------------------------------------------------------------
# Autenticación: token, y solo token
# ---------------------------------------------------------------------------

def test_sin_token_no_se_importa_nada(cliente: Any) -> None:
    for cabecera in ({}, _auth("token-malo"), {"Authorization": TOKEN}):
        respuesta = cliente.post(RUTA, json=_cuerpo(_punto()), headers=cabecera)
        assert respuesta.status_code == 401
        assert respuesta.get_json() == {"error": "no_autorizado"}

    assert _guardados() == []


def test_la_cookie_de_sesion_no_abre_la_importacion(cliente: Any) -> None:
    """Un solo camino de autenticación por ruta (decisión 24).

    Aceptar también la sesión permitiría que cualquier cosa capaz de hacer que
    un navegador ya autenticado emita la petición escribiera en el viaje.
    """
    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True

    respuesta = cliente.post(RUTA, json=_cuerpo(_punto()))

    assert respuesta.status_code == 401
    assert _guardados() == []


def test_la_ruta_del_mapa_si_necesita_sesion(cliente: Any) -> None:
    """Y al revés: `/api/ruta` la mira una persona, así que va con sesión y el
    token de importación no la abre."""
    assert cliente.get("/api/ruta").status_code == 401
    assert cliente.get("/api/ruta", headers=_auth()).status_code == 401

    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True
    assert cliente.get("/api/ruta").status_code == 200


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cambios, motivo",
    [
        ({"archivo": None}, "sin nombre de archivo"),
        ({"archivo": ""}, "nombre vacío"),
        ({"archivo": "../../etc/passwd"}, "ruta en vez de nombre"),
        ({"archivo": "carpeta/foto.jpg"}, "nombre con barra"),
        ({"lat": 91}, "latitud imposible"),
        ({"lon": -181}, "longitud imposible"),
        ({"lat": "43.5"}, "latitud como texto"),
        ({"altitud": 99999}, "altitud imposible"),
        ({"capturado_en": "28/07/2026"}, "fecha que no es ISO"),
        ({"capturado_en": "2099-01-01T00:00:00"}, "fecha en el futuro"),
        ({"capturado_en": None, "lat": None, "lon": None}, "ni fecha ni sitio"),
    ],
)
def test_un_punto_invalido_se_descarta(
    cliente: Any, cambios: dict[str, Any], motivo: str
) -> None:
    respuesta = cliente.post(RUTA, json=_cuerpo(_punto(**cambios)), headers=_auth())

    assert respuesta.get_json()["descartados"] == 1, motivo
    assert _guardados() == [], motivo


def test_una_foto_sin_gps_se_guarda_igual(cliente: Any) -> None:
    """Ordena el relato del viaje aunque no ponga una chincheta. Pasa siempre
    que se dispara con la ubicación desactivada."""
    respuesta = cliente.post(
        RUTA, json=_cuerpo(_punto(lat=None, lon=None, altitud=None)), headers=_auth()
    )

    assert respuesta.get_json()["guardados"] == 1
    assert _guardados()[0]["lat"] is None


def test_una_fecha_con_huso_se_separa_en_vez_de_rechazarse(cliente: Any) -> None:
    """Es lo que devuelve Atajos: `...T14:23:37+02:00`.

    Rechazarlo obligaría a montar un *Reemplazar texto* en el móvil para tirar
    información que aquí sí se sabe guardar. Y lo que NO puede pasar es que se
    recorte la cadena y el huso se pierda en silencio: la hora quedaría
    guardada como local estando desplazada dos horas.
    """
    cliente.post(
        RUTA,
        json=_cuerpo(
            _punto(capturado_en="2026-07-26T14:23:37+02:00", offset_original=None)
        ),
        headers=_auth(),
    )

    punto = _guardados()[0]
    # La hora que se lee es la de la cámara, no la de UTC.
    assert punto["capturado_en"] == "2026-07-26T14:23:37"
    assert punto["offset_original"] == "+02:00"


def test_el_desfase_explicito_manda_sobre_el_de_la_fecha(cliente: Any) -> None:
    """Quien se molesta en mandarlo aparte lo ha sacado del EXIF, que es la
    fuente buena; el de la fecha puede venir del reloj del móvil que exporta."""
    cliente.post(
        RUTA,
        json=_cuerpo(
            _punto(capturado_en="2026-07-26T14:23:37+05:00", offset_original="+02:00")
        ),
        headers=_auth(),
    )

    assert _guardados()[0]["offset_original"] == "+02:00"


def test_una_fecha_en_utc_no_guarda_desfase(cliente: Any) -> None:
    """"+00:00" no aporta nada como desfase original: o la cámara iba en UTC o
    no lo escribió."""
    cliente.post(
        RUTA,
        json=_cuerpo(
            _punto(capturado_en="2026-07-26T14:23:37+00:00", offset_original=None)
        ),
        headers=_auth(),
    )

    assert _guardados()[0]["offset_original"] is None


def test_una_foto_vieja_se_acepta(cliente: Any) -> None:
    """Al revés que en la telemetría y las notas, aquí NO se acota el pasado:
    importar las fotos de un viaje de hace tres años es justo lo que permite
    comparar años."""
    respuesta = cliente.post(
        RUTA,
        json=_cuerpo(_punto(capturado_en="2019-08-14T18:00:00")),
        headers=_auth(),
    )

    assert respuesta.get_json()["guardados"] == 1


def test_una_fuente_desconocida_tumba_el_lote(cliente: Any) -> None:
    """Lista blanca, como en la ingesta: una errata crearía una serie paralela
    y la deduplicación dejaría de funcionar en silencio."""
    respuesta = cliente.post(
        RUTA, json=_cuerpo(_punto(), fuente="foto"), headers=_auth()
    )

    assert respuesta.status_code == 400
    assert _guardados() == []


def test_un_lote_vacio_se_rechaza(cliente: Any) -> None:
    respuesta = cliente.post(RUTA, json=_cuerpo(), headers=_auth())

    assert respuesta.status_code == 400


def test_la_validacion_se_puede_probar_sin_flask() -> None:
    with pytest.raises(WaypointError, match="puntos"):
        waypoints.import_waypoints({})
    with pytest.raises(WaypointError):
        waypoints.import_waypoints("esto no es un objeto")


# ---------------------------------------------------------------------------
# La ruta: mezclar notas y fotos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url, permitida, motivo",
    [
        ("https://d10sdrebrasov.pythonanywhere.com", True, "el caso normal"),
        ("HTTPS://MAYUSCULAS.COM", True, "el esquema no distingue mayúsculas"),
        ("http://127.0.0.1:5000", True, "la máquina local no tiene red que espiar"),
        ("http://localhost:5001/", True, "igual por nombre"),
        ("http://d10sdrebrasov.pythonanywhere.com", False, "http por internet"),
        ("http://192.168.1.40:5000", False, "otra máquina de la red local"),
        ("http://localhost.atacante.com", False, "subdominio que empieza por localhost"),
        ("ftp://loquesea", False, "otro esquema"),
        ("d10sdrebrasov.pythonanywhere.com", False, "sin esquema"),
    ],
)
def test_el_token_no_se_manda_por_una_conexion_sin_cifrar(
    url: str, permitida: bool, motivo: str
) -> None:
    """Por `http://` la cabecera `Authorization` viaja en claro y cualquiera en
    el wifi de un camping se lleva el token.

    Se comprueba aquí y no se confía en escribir bien la URL porque el fallo es
    **silencioso**: la petición funciona igual, y cuando te enteras el secreto
    ya está comprometido.
    """
    import importlib.util

    ruta_tool = Path(__file__).resolve().parent.parent / "tools" / "importar_fotos.py"
    spec = importlib.util.spec_from_file_location("importar_fotos", ruta_tool)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    assert modulo.url_segura(url) is permitida, motivo


def _nota(cuando: str, lat: float, lon: float, texto: str = "nota") -> dict[str, Any]:
    return {
        "id": 1, "client_id": "x", "text": texto, "lat": lat, "lon": lon,
        "place_name": None, "region": None,
        "created_at": cuando + "+00:00", "created_at_local": cuando + "+02:00",
        "received_at": cuando + "+00:00", "photo_url": None,
    }


def _foto(cuando: str | None, lat: float | None, lon: float | None,
          archivo: str = "IMG.JPG") -> dict[str, Any]:
    return {
        "id": 1, "fuente": "fotos", "archivo": archivo, "capturado_en": cuando,
        "offset_original": "+02:00", "lat": lat, "lon": lon,
        "altitud": None, "camara": None, "importado_en": "2026-07-28T00:00:00+00:00",
    }


def test_notas_y_fotos_se_ordenan_juntas_en_el_tiempo() -> None:
    """Es el punto entero de la ruta: el viaje se cuenta en orden, mezclando lo
    que escribiste con lo que fotografiaste."""
    linea = ruta.construir(
        [_nota("2026-07-24T12:00:00", 43.36, -8.41, "llegada")],
        [
            _foto("2026-07-24T18:00:00", 43.38, -8.40, "tarde.JPG"),
            _foto("2026-07-24T09:00:00", 43.30, -8.50, "manana.JPG"),
        ],
    )

    assert [m["archivo"] or m["texto"] for m in linea["momentos"]] == [
        "manana.JPG", "llegada", "tarde.JPG",
    ]
    assert linea["resumen"]["notas"] == 1
    assert linea["resumen"]["fotos"] == 2


def test_en_el_mismo_instante_la_nota_va_antes_que_la_foto() -> None:
    """Lo que cuenta la historia es el texto."""
    linea = ruta.construir(
        [_nota("2026-07-24T12:00:00", 43.36, -8.41, "el texto")],
        [_foto("2026-07-24T12:00:00", 43.36, -8.41)],
    )

    assert [m["tipo"] for m in linea["momentos"]] == ["nota", "foto"]


def test_una_foto_sin_fecha_no_entra_en_la_linea_pero_se_cuenta() -> None:
    """Esconderla haría creer que el viaje está entero. "Tengo 40 fotos que no
    sé cuándo se hicieron" es información."""
    linea = ruta.construir([], [_foto(None, 43.36, -8.41), _foto("2026-07-24T12:00:00", 43.36, -8.41)])

    assert linea["resumen"]["total"] == 1
    assert linea["resumen"]["fotos_sin_fecha"] == 1


def test_la_distancia_usa_haversine_y_no_restas_de_grados() -> None:
    """A Coruña - Santander son ~440 km en línea recta. Restar grados daría un
    40 % de error justo en la latitud del viaje."""
    km = ruta.distancia_km(43.3623, -8.4115, 43.4620, -3.8050)

    assert 360 < km < 380  # medido: ~371 km


def test_un_salto_inverosimil_no_suma_kilometros() -> None:
    """300 km entre dos fotos seguidas no es un tramo recorrido: es un vuelo o
    dos viajes importados juntos. Sumarlo daría un total espectacular y falso.
    """
    linea = ruta.construir(
        [],
        [
            _foto("2026-07-24T10:00:00", 43.36, -8.41, "a.JPG"),
            _foto("2026-07-24T11:00:00", 43.38, -8.40, "b.JPG"),
            _foto("2026-07-24T12:00:00", 28.29, -16.62, "tenerife.JPG"),
        ],
    )

    assert linea["resumen"]["saltos_ignorados"] == 1
    assert linea["resumen"]["km_linea_recta"] < 10


def test_los_kilometros_de_cada_dia_suman_exactamente_el_total() -> None:
    """Ya se coló una vez: el total contaba los tramos entre días y ningún día
    los contaba, así que la suma no cuadraba. Dos números que no suman y no dan
    error son el fallo silencioso de manual.
    """
    fotos = [
        _foto("2026-07-24T10:00:00", 43.36, -8.41, "a.JPG"),
        _foto("2026-07-24T20:00:00", 43.41, -8.03, "b.JPG"),
        _foto("2026-07-25T09:00:00", 43.56, -6.14, "c.JPG"),   # tramo nocturno
        _foto("2026-07-25T19:00:00", 43.45, -5.85, "d.JPG"),
    ]
    linea = ruta.construir([], fotos)
    dias = ruta.por_dias(linea["momentos"])

    suma = round(sum(d["km_linea_recta"] for d in dias), 1)
    assert suma == pytest.approx(linea["resumen"]["km_linea_recta"], abs=0.15)
    # Y el tramo de noche se le apunta al día en que se LLEGÓ.
    assert dias[1]["km_linea_recta"] > dias[0]["km_linea_recta"]


def test_el_filtro_por_anio_tambien_filtra_las_fotos() -> None:
    """Filtrar solo las notas dejaría las fotos de todos los viajes mezcladas
    en el mapa de uno solo: el trayecto de 2026 saldría cruzando el de 2025."""
    linea = ruta.construir(
        [],
        [
            _foto("2025-08-01T10:00:00", 43.36, -8.41, "vieja.JPG"),
            _foto("2026-07-24T10:00:00", 43.36, -8.41, "nueva.JPG"),
        ],
        year=2026,
    )

    assert [m["archivo"] for m in linea["momentos"]] == ["nueva.JPG"]


def test_el_filtro_por_anio_no_cambia_el_progreso(cliente: Any) -> None:
    """El filtro decide qué viaje se mira, no cuánto llevas hecho en total."""
    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True
    cliente.post(
        RUTA,
        json=_cuerpo(_punto(archivo="vieja.JPG", capturado_en="2019-08-14T18:00:00")),
        headers=_auth(),
    )

    todo = cliente.get("/api/ruta").get_json()
    filtrado = cliente.get("/api/ruta?year=2019").get_json()
    vacio = cliente.get("/api/ruta?year=1999").get_json()

    assert todo["resumen"]["fotos"] == 1
    assert filtrado["resumen"]["fotos"] == 1
    assert vacio["momentos"] == []
    # El progreso viene de las notas y no se toca con el filtro.
    assert vacio["progreso"]["total"] == todo["progreso"]["total"]


def test_un_anio_que_no_es_un_numero_da_400(cliente: Any) -> None:
    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True

    assert cliente.get("/api/ruta?year=el-verano-pasado").status_code == 400


def test_la_pagina_del_mapa_pinta_la_ruta_desde_nuestro_static(cliente: Any) -> None:
    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True

    html = cliente.get("/mapa").data

    assert b"/static/vendor/leaflet/leaflet.js" in html
    assert b"/static/js/mapa.js" in html
    assert b"revivir-btn" in html
    for cdn in (b"unpkg.com", b"cdnjs", b"jsdelivr"):
        assert cdn not in html


def test_un_viaje_vacio_no_revienta() -> None:
    linea = ruta.construir([], [])

    assert linea["momentos"] == []
    assert linea["resumen"]["km_linea_recta"] == 0
    assert linea["resumen"]["primera"] is None
    assert ruta.por_dias([]) == []


def test_los_dias_salen_agrupados_y_en_orden() -> None:
    linea = ruta.construir(
        [_nota("2026-07-26T12:00:00", 43.36, -8.41)],
        [
            _foto("2026-07-24T10:00:00", 43.36, -8.41, "a.JPG"),
            _foto("2026-07-24T20:00:00", 43.38, -8.40, "b.JPG"),
        ],
    )
    dias = ruta.por_dias(linea["momentos"])

    assert [d["dia"] for d in dias] == ["2026-07-24", "2026-07-26"]
    assert len(dias[0]["momentos"]) == 2
