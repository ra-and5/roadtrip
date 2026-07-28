"""Tests de la ingesta de telemetría del iPhone (Fase 2d).

Sin red y sin API keys, como el resto de la suite, y con el test client de
Flask en vez de HTTP real: lo que se prueba es el código, no que exista un
socket.

Lo que se protege aquí, por orden de importancia:

  - **La frontera de seguridad.** El endpoint es público en internet. Los tres
    modos de fallar (sin cabecera, mal formada, token malo) tienen que dar el
    MISMO 401, y la cookie de sesión NO puede abrir esta puerta. Ese último es
    el equivalente en esta fase a
    `test_cualquier_fallo_del_proveedor_sale_como_aierror`: fija una frontera
    que, si se cruza, no da error, solo un agujero.
  - **La idempotencia.** El mismo lote dos veces guarda una sola vez. No es una
    optimización: cada envío del móvil repite a propósito las últimas horas
    (decisión 23), así que si esto falla la tabla se llena de duplicados desde
    el primer día.
  - **La validación,** regla a regla y con su caso límite. Una fecha corrupta o
    una coordenada imposible no dan error: se guardan y envenenan en silencio
    cualquier análisis posterior.

Nota sobre la base de datos: cada test corre contra una SQLite en `tmp_path`.
Se parchea `Config` en vez de reimportar `app.config`, por lo explicado en
`test_app_despliegue.py`: recargar el módulo sustituye la clase y los módulos
que ya la tenían importada (`storage`) siguen con la vieja, y entonces el test
escribe en un sitio y el código de producción en la base de datos REAL.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
from werkzeug.security import generate_password_hash

from app.app import app as flask_app
from app.config import Config
from app.modules import ingest, llm_providers, storage

RUTA = "/api/telemetria"

TOKEN = "un-token-de-mentira-pero-largo_AbCdEf123456"

# Hash con muchas menos iteraciones que las de producción. El método se lee del
# propio hash, así que `check_password_hash` recorre exactamente el mismo camino
# que en el servidor; lo único que cambia es que 30 verificaciones no tardan 6
# segundos. Que el hash REAL (método por defecto, el que genera
# tools/token_ingesta.py) también funciona lo comprueba su propio test.
HASH_TOKEN = generate_password_hash(TOKEN, method="pbkdf2:sha256:1000")


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(desfase: timedelta = timedelta(0)) -> str:
    return (_ahora() + desfase).replace(microsecond=0).isoformat()


def _muestra(**campos: Any) -> dict[str, Any]:
    """Una muestra válida, con lo que se le quiera cambiar."""
    base: dict[str, Any] = {"medido_en": _iso(-timedelta(hours=1)), "pasos": 1234}
    base.update(campos)
    return base


def _cuerpo(*muestras: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"muestras": list(muestras)}
    payload.update(extra)
    return payload


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Base de datos aislada y token configurado, para todos los tests."""
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


def _guardadas() -> list[dict[str, Any]]:
    return storage.recent_telemetry(1000)


# ---------------------------------------------------------------------------
# Autenticación: token propio, y solo token
# ---------------------------------------------------------------------------

def test_las_tres_formas_de_fallar_dan_el_mismo_401(cliente: Any) -> None:
    """Sin cabecera, con token malo y con cabecera mal formada: 401 idéntico.

    Es la propiedad que importa, no el código de estado. Si el cuerpo
    distinguiera "falta la cabecera" de "el token no es ese", quien prueba a
    ciegas sabría cuándo ha acertado el formato y le quedaría solo adivinar el
    token. El 401 no puede enseñar nada.
    """
    cuerpo = _cuerpo(_muestra())

    respuestas = [
        cliente.post(RUTA, json=cuerpo),                                    # sin cabecera
        cliente.post(RUTA, json=cuerpo, headers=_auth("token-equivocado")),  # token malo
        cliente.post(RUTA, json=cuerpo, headers={"Authorization": TOKEN}),   # sin "Bearer "
        cliente.post(RUTA, json=cuerpo, headers={"Authorization": "Bearer"}),
        cliente.post(RUTA, json=cuerpo, headers={"Authorization": "Bearer "}),
        cliente.post(RUTA, json=cuerpo, headers={"Authorization": f"Basic {TOKEN}"}),
        cliente.post(RUTA, json=cuerpo, headers={"Authorization": ""}),
    ]

    for respuesta in respuestas:
        assert respuesta.status_code == 401
        assert respuesta.get_json() == {"error": "no_autorizado"}

    assert _guardadas() == []


def test_el_esquema_bearer_es_insensible_a_mayusculas(cliente: Any) -> None:
    """Lo dice el RFC 7235. El token, en cambio, es sensible."""
    respuesta = cliente.post(
        RUTA, json=_cuerpo(_muestra()), headers={"Authorization": f"bEaReR {TOKEN}"}
    )

    assert respuesta.status_code == 200


def test_la_cookie_de_sesion_no_abre_este_endpoint(cliente: Any) -> None:
    """LA frontera de seguridad de esta fase.

    Estar logueado en la web no puede dar acceso a la ingesta. Aceptar los dos
    caminos parece cómodo y es como se cuelan los fallos de "confused deputy":
    a partir de ahí, cualquier cosa que consiga que el navegador ya autenticado
    haga una petición (una pestaña abierta, un enlace) estaría escribiendo en la
    tabla de telemetría.
    """
    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True

    respuesta = cliente.post(RUTA, json=_cuerpo(_muestra()))

    assert respuesta.status_code == 401
    assert _guardadas() == []


def test_una_sesion_valida_mas_un_token_malo_sigue_siendo_401(cliente: Any) -> None:
    """La sesión no puede ni siquiera *ayudar*: el token es el único camino."""
    with cliente.session_transaction() as sesion:
        sesion["authenticated"] = True

    respuesta = cliente.post(
        RUTA, json=_cuerpo(_muestra()), headers=_auth("token-equivocado")
    )

    assert respuesta.status_code == 401


def test_sin_hash_configurado_el_endpoint_esta_cerrado(
    cliente: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equivocarse hacia el lado seguro: sin configurar no es "abierto"."""
    monkeypatch.setattr(Config, "INGEST_TOKEN_HASH", "")

    assert cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth()).status_code == 401


def test_un_hash_corrupto_en_el_env_no_abre_la_puerta(
    cliente: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un hash mal copiado hace que Werkzeug lance: eso es 401, no un 500."""
    monkeypatch.setattr(Config, "INGEST_TOKEN_HASH", "esto-no-es-un-hash")

    assert cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth()).status_code == 401


def test_el_hash_de_produccion_tambien_vale() -> None:
    """El resto de la suite usa un hash rebajado por velocidad: este es el real.

    Comprueba que lo que genera `tools/token_ingesta.py` (método por defecto de
    Werkzeug, PBKDF2 con las iteraciones de verdad) lo acepta `token_valido`.
    """
    token = "otro-token-de-mentira-igual-de-largo_XyZ987"
    hash_real = generate_password_hash(token)

    class _ConfigFalsa:
        INGEST_TOKEN_HASH = hash_real

    original = ingest.Config
    ingest.Config = _ConfigFalsa  # type: ignore[assignment]
    try:
        assert ingest.token_valido(f"Bearer {token}") is True
        assert ingest.token_valido("Bearer otro-cualquiera") is False
    finally:
        ingest.Config = original  # type: ignore[assignment]


def test_get_al_endpoint_da_405(cliente: Any) -> None:
    """Y en JSON, no en HTML: al otro lado hay un atajo que espera JSON."""
    respuesta = cliente.get(RUTA)

    assert respuesta.status_code == 405
    assert respuesta.get_json() == {"error": "Método no permitido."}


# ---------------------------------------------------------------------------
# El token no se filtra
# ---------------------------------------------------------------------------

def test_el_token_no_aparece_en_la_respuesta_de_error(cliente: Any) -> None:
    """Ni el bueno ni el que se haya intentado: la respuesta no es un espejo."""
    intentos = [
        cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth("token-equivocado")),
        cliente.post(RUTA, json={"muestras": "esto no es una lista"}, headers=_auth()),
        cliente.post(RUTA, json=_cuerpo(_muestra(pasos=-5)), headers=_auth()),
    ]

    for respuesta in intentos:
        texto = respuesta.get_data(as_text=True)
        assert TOKEN not in texto
        assert "token-equivocado" not in texto
        assert HASH_TOKEN not in texto


def test_el_token_no_aparece_en_los_logs(
    cliente: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Un token en el log de PythonAnywhere es un token comprometido.

    Y quien provoca la línea de log elige lo que se escribe en ella, así que la
    cabecera no se registra ni siquiera parcialmente.
    """
    with caplog.at_level("DEBUG"):
        cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth("token-equivocado"))
        cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth())

    registrado = caplog.text
    assert TOKEN not in registrado
    assert "token-equivocado" not in registrado
    assert HASH_TOKEN not in registrado


def test_redact_tapa_una_cabecera_authorization() -> None:
    """Última línea de defensa, por si un token llega a un mensaje de error.

    `redact()` descubre las API keys recorriendo `Config` (decisión 19), y ese
    truco NO puede alcanzar a este token: en el servidor solo vive su hash, el
    secreto en claro no está en `Config`. Por eso hace falta el patrón.
    """
    sucio = f"fallo al llamar con Authorization: Bearer {TOKEN} (401)"

    limpio = llm_providers.redact(sucio)

    assert TOKEN not in limpio
    assert "[API_KEY_OCULTA]" in limpio


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------

def test_el_mismo_lote_dos_veces_se_guarda_una_sola_vez(cliente: Any) -> None:
    """El caso normal de la ventana solapada, no una rareza.

    Cada envío del móvil repite a propósito las últimas horas: si esto no
    funcionara, la tabla tendría seis copias de cada muestra desde el primer
    día de viaje.
    """
    lote = _cuerpo(
        _muestra(medido_en=_iso(-timedelta(hours=1)), pasos=100),
        _muestra(medido_en=_iso(-timedelta(hours=2)), pasos=200),
        _muestra(medido_en=_iso(-timedelta(hours=3)), pasos=300),
    )

    primera = cliente.post(RUTA, json=lote, headers=_auth())
    segunda = cliente.post(RUTA, json=lote, headers=_auth())

    def _recuentos(respuesta: Any) -> dict[str, Any]:
        cuerpo = respuesta.get_json()
        return {k: cuerpo[k] for k in ("guardadas", "duplicadas", "descartadas", "errores")}

    assert _recuentos(primera) == {
        "guardadas": 3, "duplicadas": 0, "descartadas": 0, "errores": []
    }
    assert _recuentos(segunda) == {
        "guardadas": 0, "duplicadas": 3, "descartadas": 0, "errores": []
    }
    assert len(_guardadas()) == 3


def test_una_ventana_solapada_solo_guarda_lo_nuevo(cliente: Any) -> None:
    """El régimen normal: el segundo envío trae 3 muestras viejas y 1 nueva."""
    horas = [_iso(-timedelta(hours=h)) for h in (4, 3, 2, 1)]
    cliente.post(RUTA, json=_cuerpo(*(_muestra(medido_en=h) for h in horas[:3])),
                 headers=_auth())

    respuesta = cliente.post(
        RUTA, json=_cuerpo(*(_muestra(medido_en=h) for h in horas)), headers=_auth()
    )

    assert respuesta.get_json()["guardadas"] == 1
    assert respuesta.get_json()["duplicadas"] == 3
    assert len(_guardadas()) == 4


def test_el_mismo_instante_en_otro_huso_es_la_misma_muestra() -> None:
    """La canonización a UTC no es cosmética: sostiene el UNIQUE.

    Si el atajo cambiara de formato de fecha (a UTC, al volver del extranjero,
    o por un cambio de iOS), sin canonizar reenviaría como nuevas muestras que
    ya estaban guardadas. Nadie vería un error: solo saldrían pasos duplicados.
    """
    en_madrid = "2026-07-27T12:00:00+02:00"
    en_utc = "2026-07-27T10:00:00+00:00"

    # Se llama al módulo directamente para no depender de que la fecha fija de
    # arriba caiga dentro de la ventana de 30 días cuando se corra la suite.
    canon_madrid, offset = ingest._parse_medido_en(en_madrid)
    canon_utc, sin_offset = ingest._parse_medido_en(en_utc)

    assert canon_madrid == canon_utc == "2026-07-27T10:00:00+00:00"
    assert offset == "+02:00"      # el huso original no se pierde
    assert sin_offset is None      # "+00:00" ya está en medido_en; repetirlo es ruido


def test_dos_muestras_iguales_dentro_del_mismo_lote(cliente: Any) -> None:
    """La deduplicación también funciona dentro de una sola petición."""
    momento = _iso(-timedelta(hours=1))

    respuesta = cliente.post(
        RUTA,
        json=_cuerpo(_muestra(medido_en=momento), _muestra(medido_en=momento)),
        headers=_auth(),
    )

    assert respuesta.get_json()["guardadas"] == 1
    assert respuesta.get_json()["duplicadas"] == 1


# ---------------------------------------------------------------------------
# Validación: forma del lote
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cuerpo",
    [
        [{"medido_en": "2026-07-27T10:00:00+00:00", "pasos": 1}],  # array pelado
        {"no_hay_muestras": []},
        {"muestras": "esto no es una lista"},
        {"muestras": {}},
        "una cadena",
        None,
    ],
)
def test_un_cuerpo_con_otra_forma_da_400(cliente: Any, cuerpo: Any) -> None:
    respuesta = cliente.post(RUTA, json=cuerpo, headers=_auth())

    assert respuesta.status_code == 400
    assert "error" in respuesta.get_json()
    assert _guardadas() == []


def test_un_json_ilegible_da_400_y_no_500(cliente: Any) -> None:
    respuesta = cliente.post(
        RUTA, data="{esto no es json", content_type="application/json", headers=_auth()
    )

    assert respuesta.status_code == 400


def test_la_respuesta_dice_que_se_ha_guardado_no_solo_cuanto(cliente: Any) -> None:
    """`guardadas: 1` no distingue una muestra completa de una a medias.

    Si una clave viene mal escrita (`"lat:"` en vez de `"lat"`, que es JSON
    perfectamente válido), la ubicación se guarda como NULL y la respuesta sale
    idéntica. El resumen hace visible esa pérdida desde el móvil.
    """
    momento = _iso(-timedelta(hours=1))

    cuerpo = cliente.post(
        RUTA,
        json=_cuerpo(
            {"medido_en": momento, "pasos": 4213, "bateria": 77,
             "lat": 38.39064, "lon": -0.51648}
        ),
        headers=_auth(),
    ).get_json()

    assert cuerpo["detalle"] == [
        f"{momento} pasos=4213 bat=77% lat=38.39064 lon=-0.51648"
    ]


def test_el_resumen_omite_lo_que_no_llego(cliente: Any) -> None:
    """La omisión ES la información: si falta `lat=`, la ubicación se perdió."""
    momento = _iso(-timedelta(hours=1))

    cuerpo = cliente.post(
        RUTA,
        # `lat:` y `lon:` con dos puntos dentro: JSON válido, claves
        # equivocadas. Es la errata exacta que se comió una ubicación montando
        # el atajo del iPhone. (Si solo se estropeara UNA, la validación lo
        # cazaría: lat y lon van juntas. Con las dos mal, la muestra es
        # legítima -- solo que sin ubicación.)
        json=_cuerpo(
            {"medido_en": momento, "bateria": 77, "lat:": 38.4, "lon:": -0.5}
        ),
        headers=_auth(),
    ).get_json()

    assert cuerpo["guardadas"] == 1          # se guarda: la muestra es válida
    assert cuerpo["detalle"] == [f"{momento} bat=77%"]   # pero sin ubicación, y se ve


def test_el_resumen_tambien_cubre_las_duplicadas(cliente: Any) -> None:
    """En régimen normal casi todo el lote son duplicadas: hay que poder verlas."""
    lote = _cuerpo(_muestra(medido_en=_iso(-timedelta(hours=1))))
    cliente.post(RUTA, json=lote, headers=_auth())

    cuerpo = cliente.post(RUTA, json=lote, headers=_auth()).get_json()

    assert cuerpo["duplicadas"] == 1
    assert len(cuerpo["detalle"]) == 1


def test_el_resumen_esta_acotado(cliente: Any) -> None:
    """Quien lo lee es una persona mirando una alerta en un móvil."""
    muestras = [_muestra(medido_en=_iso(-timedelta(minutes=i))) for i in range(20)]

    cuerpo = cliente.post(RUTA, json=_cuerpo(*muestras), headers=_auth()).get_json()

    assert len(cuerpo["detalle"]) == ingest.MAX_DETALLE + 1
    assert cuerpo["detalle"][-1] == "...y 15 muestras más"


def test_un_cuerpo_que_no_es_json_se_devuelve_en_el_error(cliente: Any) -> None:
    """El 400 enseña lo que llegó. Sin esto se depura a ciegas desde un móvil.

    "esperaba un objeto JSON" no le sirve a nadie: lo que hace falta saber es
    QUÉ se envió. Montando el atajo del iPhone es la diferencia entre ver la
    coma decimal de `43,5622` al instante y pasar media noche adivinando.
    """
    roto = '{"muestras":[{"lat":43,5622}]}'

    cuerpo = cliente.post(
        RUTA, data=roto, content_type="application/json", headers=_auth()
    ).get_json()

    assert cuerpo["recibido"] == roto


def test_el_eco_del_cuerpo_esta_acotado(cliente: Any) -> None:
    """Un cuerpo entero en un mensaje de error es ruido, no ayuda."""
    from app.app import _MAX_ECO_CUERPO

    cuerpo = cliente.post(
        RUTA, data="x" * 5000, content_type="application/json", headers=_auth()
    ).get_json()

    assert len(cuerpo["recibido"]) == _MAX_ECO_CUERPO + len("...")
    assert cuerpo["recibido"].endswith("...")


def test_el_eco_no_devuelve_un_secreto_que_venga_en_el_cuerpo(cliente: Any) -> None:
    """Reflejar la entrada es justo donde un secreto mal pegado saldría fuera.

    Pasa por `redact()`, la misma defensa que los mensajes de los proveedores.
    """
    cuerpo = cliente.post(
        RUTA,
        data=f'{{roto, Authorization: Bearer {TOKEN}}}',
        content_type="application/json",
        headers=_auth(),
    ).get_json()

    assert TOKEN not in cuerpo["recibido"]


def test_un_lote_con_json_valido_no_lleva_eco(cliente: Any) -> None:
    """Si el JSON se parseó, el mensaje ya nombra el campo: el eco sobra."""
    cuerpo = cliente.post(RUTA, json={"muestras": []}, headers=_auth()).get_json()

    assert "recibido" not in cuerpo


def test_un_lote_vacio_se_rechaza(cliente: Any) -> None:
    """Un atajo que manda cero muestras no está funcionando: no leyó nada.

    Responderle 200 lo dejaría fallando en silencio durante días, que es
    justamente el fallo que esta fase existe para detectar.
    """
    respuesta = cliente.post(RUTA, json=_cuerpo(), headers=_auth())

    assert respuesta.status_code == 400
    assert "vacío" in respuesta.get_json()["error"]


def test_un_array_por_encima_del_limite_no_inserta_nada(cliente: Any) -> None:
    """Se rechaza el lote entero antes de tocar la base de datos."""
    exceso = Config.INGEST_MAX_SAMPLES + 1
    muestras = [
        _muestra(medido_en=_iso(-timedelta(minutes=i)), pasos=i) for i in range(exceso)
    ]

    respuesta = cliente.post(RUTA, json=_cuerpo(*muestras), headers=_auth())

    assert respuesta.status_code == 400
    assert str(Config.INGEST_MAX_SAMPLES) in respuesta.get_json()["error"]
    assert _guardadas() == []


def test_el_limite_exacto_se_acepta(cliente: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """El caso límite del lado bueno. Se baja el máximo para no tardar."""
    monkeypatch.setattr(Config, "INGEST_MAX_SAMPLES", 5)
    muestras = [_muestra(medido_en=_iso(-timedelta(minutes=i))) for i in range(5)]

    respuesta = cliente.post(RUTA, json=_cuerpo(*muestras), headers=_auth())

    assert respuesta.status_code == 200
    assert respuesta.get_json()["guardadas"] == 5


def test_un_cuerpo_enorme_se_corta_antes_de_parsear_el_json(cliente: Any) -> None:
    """MAX_CONTENT_LENGTH: 413 sin haber deserializado nada.

    El límite de muestras se comprueba sobre una lista YA deserializada, y
    deserializar es donde se va la CPU. En PythonAnywhere gratuito la CPU es
    cuota diaria: agotarla ralentiza la app entera el resto del día.
    """
    relleno = "x" * (Config.MAX_CONTENT_LENGTH + 1024)

    respuesta = cliente.post(
        RUTA,
        data=json.dumps({"muestras": [], "relleno": relleno}),
        content_type="application/json",
        headers=_auth(),
    )

    assert respuesta.status_code == 413
    assert _guardadas() == []


def test_la_app_aplica_el_limite_de_tamano_configurado() -> None:
    """El valor no sirve de nada si luego no se le pasa a Flask."""
    assert flask_app.config["MAX_CONTENT_LENGTH"] == Config.MAX_CONTENT_LENGTH


@pytest.mark.parametrize("fuente", ["desconocida", "atajos-iphone ", "", "ATAJOS-IPHONE"])
def test_una_fuente_fuera_de_la_lista_blanca_da_400(cliente: Any, fuente: str) -> None:
    """Una errata en el atajo crearía una serie paralela sin dar ningún error.

    Y como el UNIQUE es (fuente, medido_en), la deduplicación dejaría de
    funcionar en silencio: fallo mudo, decisión 11. Aquí es un 400 que se ve al
    primer envío. (`"atajos-iphone "` con espacio final sí pasa el `strip()` y
    se acepta; el resto no.)
    """
    respuesta = cliente.post(RUTA, json=_cuerpo(_muestra(), fuente=fuente), headers=_auth())

    if fuente.strip() in ingest.FUENTES_VALIDAS:
        assert respuesta.status_code == 200
    else:
        assert respuesta.status_code == 400
        assert "fuente" in respuesta.get_json()["error"]


def test_sin_fuente_se_usa_la_por_defecto(cliente: Any) -> None:
    cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth())

    assert _guardadas()[0]["fuente"] == ingest.FUENTE_POR_DEFECTO


# ---------------------------------------------------------------------------
# Validación: campos de una muestra
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "campos, motivo",
    [
        ({"medido_en": None}, "sin fecha"),
        ({"medido_en": ""}, "fecha vacía"),
        ({"medido_en": "27/07/2026 10:00"}, "no es ISO 8601"),
        ({"medido_en": "2026-07-27T10:00:00"}, "ISO 8601 pero SIN zona horaria"),
        ({"medido_en": 1753606800}, "epoch, no una cadena"),
        ({"pasos": -1}, "pasos negativos"),
        ({"pasos": 12.5}, "pasos no entero"),
        ({"pasos": "1234"}, "pasos como texto"),
        ({"pasos": True}, "pasos booleano"),
        ({"pasos": None, "bateria": 101}, "batería por encima de 100"),
        ({"pasos": None, "bateria": -1}, "batería negativa"),
        ({"pasos": None, "bateria": 55.5}, "batería no entera"),
        ({"lat": 43.5}, "latitud sin longitud"),
        ({"lon": -6.1}, "longitud sin latitud"),
        ({"lat": 90.1, "lon": 0}, "latitud fuera de rango"),
        ({"lat": -90.1, "lon": 0}, "latitud fuera de rango por abajo"),
        ({"lat": 0, "lon": 180.1}, "longitud fuera de rango"),
        ({"lat": 0, "lon": -180.1}, "longitud fuera de rango por abajo"),
        ({"lat": "43.5", "lon": "-6.1"}, "coordenadas como texto"),
        ({"pasos": None}, "sin ningún dato: solo la fecha"),
    ],
)
def test_una_muestra_invalida_se_descarta_con_su_motivo(
    cliente: Any, campos: dict[str, Any], motivo: str
) -> None:
    respuesta = cliente.post(RUTA, json=_cuerpo(_muestra(**campos)), headers=_auth())
    cuerpo = respuesta.get_json()

    assert respuesta.status_code == 200, motivo
    assert cuerpo["descartadas"] == 1, motivo
    assert cuerpo["guardadas"] == 0, motivo
    # El mensaje tiene que decir QUÉ campo está mal: al otro lado hay un atajo
    # que alguien está escribiendo a mano, y "muestra 0: inválida" no se depura.
    assert len(cuerpo["errores"]) == 1, motivo
    assert _guardadas() == [], motivo


@pytest.mark.parametrize(
    "campos, motivo",
    [
        ({"lat": 90, "lon": 0}, "latitud 90 exacta"),
        ({"lat": -90, "lon": 0}, "latitud -90 exacta"),
        ({"lat": 0, "lon": 180}, "longitud 180 exacta"),
        ({"lat": 0, "lon": -180}, "longitud -180 exacta"),
        ({"pasos": 0}, "cero pasos es un dato, no un hueco"),
        ({"pasos": 4213.0}, "entero que JSON serializó como real"),
        ({"pasos": None, "bateria": 0}, "batería 0"),
        ({"pasos": None, "bateria": 100}, "batería 100"),
        ({"pasos": None, "lat": 43.5622, "lon": -6.1456}, "solo ubicación"),
        ({"medido_en": _iso(timedelta(hours=23))}, "23 h en el futuro"),
        ({"medido_en": _iso(-timedelta(days=29))}, "29 días en el pasado"),
    ],
)
def test_los_casos_limite_validos_se_guardan(
    cliente: Any, campos: dict[str, Any], motivo: str
) -> None:
    """El otro lado de cada frontera: 90 vale aunque 90,1 no."""
    respuesta = cliente.post(RUTA, json=_cuerpo(_muestra(**campos)), headers=_auth())

    assert respuesta.get_json()["guardadas"] == 1, motivo
    assert respuesta.get_json()["errores"] == [], motivo


def test_se_acepta_la_z_de_utc(cliente: Any) -> None:
    """`fromisoformat` acepta la "Z" desde Python 3.11, la versión mínima aquí.

    Se prueba con una fecha relativa: una fecha fija en el código dejaría de
    caer dentro de la ventana de 30 días en cuanto pasara un mes, y el test se
    "rompería" solo sin que nadie hubiera tocado nada.
    """
    en_z = _iso(-timedelta(hours=1)).replace("+00:00", "Z")

    respuesta = cliente.post(RUTA, json=_cuerpo(_muestra(medido_en=en_z)), headers=_auth())

    assert respuesta.get_json()["guardadas"] == 1
    assert _guardadas()[0]["medido_en"].endswith("+00:00")
    assert _guardadas()[0]["offset_original"] is None


@pytest.mark.parametrize(
    "desfase, motivo",
    [
        (timedelta(hours=25), "más de 24 h en el futuro"),
        (timedelta(days=400), "muy en el futuro"),
        (-timedelta(days=31), "más de 30 días en el pasado"),
        (-timedelta(days=20000), "el epoch de Unix"),
    ],
)
def test_una_fecha_absurda_se_rechaza(
    cliente: Any, desfase: timedelta, motivo: str
) -> None:
    """Una fecha corrupta no da error: se guarda y envenena el análisis futuro.

    Es más barato rechazarla aquí que descubrir dentro de un mes que hay pasos
    fechados en 1970 metidos entre los buenos.
    """
    respuesta = cliente.post(
        RUTA, json=_cuerpo(_muestra(medido_en=_iso(desfase))), headers=_auth()
    )

    assert respuesta.get_json()["descartadas"] == 1, motivo
    assert "medido_en" in respuesta.get_json()["errores"][0], motivo


def test_una_muestra_que_no_es_un_objeto_se_descarta(cliente: Any) -> None:
    respuesta = cliente.post(
        RUTA, json=_cuerpo(_muestra(), "una cadena", 42, None), headers=_auth()  # type: ignore[arg-type]
    )

    cuerpo = respuesta.get_json()

    assert (cuerpo["guardadas"], cuerpo["duplicadas"], cuerpo["descartadas"]) == (1, 0, 3)
    assert cuerpo["errores"] == [
        "muestra 1: no es un objeto JSON",
        "muestra 2: no es un objeto JSON",
        "muestra 3: no es un objeto JSON",
    ]


# ---------------------------------------------------------------------------
# Lotes mezclados: una mala no tumba a las buenas
# ---------------------------------------------------------------------------

def test_las_buenas_se_guardan_aunque_haya_malas(cliente: Any) -> None:
    """Lo contrario tiraría seis horas de datos buenos por un dato raro.

    Y justo en el escenario para el que se diseñó la ventana solapada: el
    envío que llega después de horas sin cobertura es el más largo y el que
    más probabilidades tiene de traer algo torcido.
    """
    respuesta = cliente.post(
        RUTA,
        json=_cuerpo(
            _muestra(medido_en=_iso(-timedelta(hours=1)), pasos=100),
            _muestra(medido_en=_iso(-timedelta(hours=2)), pasos=-5),       # mala
            _muestra(medido_en=_iso(-timedelta(hours=3)), pasos=300),
            _muestra(medido_en="ayer por la tarde", pasos=400),            # mala
            _muestra(medido_en=_iso(-timedelta(hours=5)), bateria=88, pasos=None),
        ),
        headers=_auth(),
    )
    cuerpo = respuesta.get_json()

    assert cuerpo["guardadas"] == 3
    assert cuerpo["descartadas"] == 2
    assert [e.split(":")[0] for e in cuerpo["errores"]] == ["muestra 1", "muestra 3"]
    assert {f["pasos"] for f in _guardadas()} == {100, 300, None}


def test_los_mensajes_de_descarte_estan_acotados(cliente: Any) -> None:
    """Un lote de 100 muestras malas no puede generar 100 líneas de respuesta.

    El recuento sí es exacto: se acotan los mensajes, no la contabilidad.
    """
    malas = [_muestra(medido_en=_iso(-timedelta(minutes=i)), pasos=-1) for i in range(100)]

    cuerpo = cliente.post(RUTA, json=_cuerpo(*malas), headers=_auth()).get_json()

    assert cuerpo["descartadas"] == 100
    assert len(cuerpo["errores"]) == ingest.MAX_ERRORES_REPORTADOS + 1
    assert "descartes más" in cuerpo["errores"][-1]


# ---------------------------------------------------------------------------
# Lo que llega a la base de datos
# ---------------------------------------------------------------------------

def test_se_guarda_en_utc_conservando_el_huso_original(cliente: Any) -> None:
    """UTC para poder comparar, huso original para no perder información.

    PythonAnywhere corre en UTC y el viaje es en Europe/Madrid: mezclar zonas
    da errores que no se ven hasta que analizas.
    """
    hora_local = (_ahora() - timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=2))
    ).replace(microsecond=0)

    cliente.post(
        RUTA,
        json=_cuerpo(_muestra(medido_en=hora_local.isoformat(), bateria=77)),
        headers=_auth(),
    )
    fila = _guardadas()[0]

    assert fila["medido_en"].endswith("+00:00")
    assert fila["offset_original"] == "+02:00"
    assert datetime.fromisoformat(fila["medido_en"]) == hora_local
    assert fila["bateria"] == 77


def test_se_guarda_cuando_llego_al_servidor(cliente: Any) -> None:
    """`recibido_en - medido_en` es la medida del retraso por cobertura.

    Sin esta columna, un envío retrasado 5 h y un reloj mal puesto en el móvil
    son indistinguibles, y esta fase existe precisamente para medir eso.
    """
    antes = _ahora().replace(microsecond=0)

    cliente.post(
        RUTA,
        json=_cuerpo(_muestra(medido_en=_iso(-timedelta(hours=5)))),
        headers=_auth(),
    )
    fila = _guardadas()[0]

    recibido = datetime.fromisoformat(fila["recibido_en"])
    assert antes <= recibido <= _ahora()
    assert recibido - datetime.fromisoformat(fila["medido_en"]) >= timedelta(hours=4)


def test_todas_las_muestras_de_un_envio_comparten_recepcion(cliente: Any) -> None:
    """Además de ser el dato correcto, agrupa las muestras por lote."""
    cliente.post(
        RUTA,
        json=_cuerpo(*(_muestra(medido_en=_iso(-timedelta(hours=h))) for h in (1, 2, 3))),
        headers=_auth(),
    )

    assert len({f["recibido_en"] for f in _guardadas()}) == 1


def test_las_metricas_ausentes_se_guardan_como_null(cliente: Any) -> None:
    """Un hueco es un hueco. Un 0 en `pasos` significaría "no anduvo nada"."""
    cliente.post(RUTA, json=_cuerpo(_muestra(pasos=None, bateria=42)), headers=_auth())
    fila = _guardadas()[0]

    assert fila["pasos"] is None
    assert fila["lat"] is None and fila["lon"] is None
    assert fila["bateria"] == 42


# ---------------------------------------------------------------------------
# El módulo, sin Flask
# ---------------------------------------------------------------------------

def test_la_validacion_se_prueba_sin_flask() -> None:
    """La lógica vive en el módulo, no en la ruta: aquí no hay test client."""
    resultado = ingest.ingest(
        {
            "fuente": "atajos-iphone",
            "muestras": [
                {"medido_en": _iso(-timedelta(hours=1)), "pasos": 10},
                {"medido_en": _iso(-timedelta(hours=2)), "pasos": -10},
            ],
        }
    )

    assert isinstance(resultado, ingest.ResultadoIngesta)
    assert (resultado.guardadas, resultado.descartadas) == (1, 1)


def test_el_lote_entero_malo_lanza_ingesterror() -> None:
    """Como `LocationError` o `AIError`: cada módulo lanza la suya."""
    with pytest.raises(ingest.IngestError):
        ingest.ingest({"muestras": None})


def test_se_pueden_borrar_muestras_por_id(cliente: Any) -> None:
    """Los datos reales se ensucian: pruebas, fechas a mano, métricas mal.

    Borrarlas es más honesto que dejarlas y acordarse de filtrarlas al
    analizar; ese "acordarse" no sobrevive a un mes de viaje.
    """
    cliente.post(
        RUTA,
        json=_cuerpo(*(_muestra(medido_en=_iso(-timedelta(hours=h))) for h in (1, 2, 3))),
        headers=_auth(),
    )
    ids = [f["id"] for f in _guardadas()]

    assert storage.delete_telemetry(ids[:2]) == 2
    assert [f["id"] for f in _guardadas()] == ids[2:]


def test_borrar_un_id_que_no_existe_lo_dice(cliente: Any) -> None:
    """Devolver el recuento REAL distingue "borrado" de "creía haberlo borrado"."""
    assert storage.delete_telemetry([9999]) == 0
    assert storage.delete_telemetry([]) == 0


def test_los_recuentos_siempre_suman_lo_enviado(cliente: Any) -> None:
    """Invariante de la respuesta: nada se pierde por el camino sin contarse."""
    muestras = [
        _muestra(medido_en=_iso(-timedelta(hours=1))),
        _muestra(medido_en=_iso(-timedelta(hours=1))),    # duplicada en el lote
        _muestra(medido_en=_iso(-timedelta(hours=2))),
        _muestra(pasos=-1),                               # descartada
    ]

    cuerpo = cliente.post(RUTA, json=_cuerpo(*muestras), headers=_auth()).get_json()

    assert cuerpo["guardadas"] + cuerpo["duplicadas"] + cuerpo["descartadas"] == 4


# ---------------------------------------------------------------------------
# La serie simulada, y la frontera que la separa de la real
# ---------------------------------------------------------------------------

def test_lo_simulado_y_lo_real_conviven_sin_taparse(cliente: Any) -> None:
    """La MISMA hora en las dos fuentes son dos filas, no una duplicada.

    Es lo que hace seguro sembrar datos inventados: el UNIQUE es
    (fuente, medido_en), así que una muestra simulada nunca puede ocupar el
    hueco de una real ni desplazarla. Si la clave fuera solo `medido_en`,
    sembrar borraría en silencio muestras del móvil -- y sin dar ningún error,
    que es lo que las haría imposibles de echar de menos.
    """
    instante = _iso(-timedelta(hours=1))

    real = cliente.post(
        RUTA, json=_cuerpo(_muestra(medido_en=instante)), headers=_auth()
    ).get_json()
    simulada = cliente.post(
        RUTA,
        json=_cuerpo(_muestra(medido_en=instante), fuente="simulado"),
        headers=_auth(),
    ).get_json()

    assert real["guardadas"] == 1
    assert simulada["guardadas"] == 1
    assert simulada["duplicadas"] == 0
    assert {f["fuente"] for f in _guardadas()} == {"atajos-iphone", "simulado"}


def test_el_instante_de_recepcion_se_puede_inyectar() -> None:
    """El simulador siembra días pasados, y con el reloj real todos saldrían
    con un `retraso` de días. `retraso` es justo la columna que esta fase mira,
    así que falsearla dejaría la tabla inservible para lo único que sirve.
    """
    recibido = _iso(-timedelta(hours=1))

    ingest.ingest(
        {"fuente": "simulado", "muestras": [_muestra(medido_en=_iso(-timedelta(hours=2)))]},
        recibido_en=recibido,
    )

    assert _guardadas()[0]["recibido_en"] == recibido


def test_sin_inyectar_el_instante_sale_del_reloj(cliente: Any) -> None:
    """Que exista el parámetro no puede cambiar lo que hace producción."""
    antes = _ahora().replace(microsecond=0)

    cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth())

    recibido = datetime.fromisoformat(_guardadas()[0]["recibido_en"])
    assert antes <= recibido <= _ahora()


def test_limpiar_lo_simulado_no_toca_lo_real(cliente: Any) -> None:
    """Poder deshacer la siembra de un comando es la otra mitad de que sembrar
    sea seguro: sin ella, quitar los datos falsos sería una lista de ids
    copiada a mano, y eso no se hace bien a la tercera vez.
    """
    cliente.post(RUTA, json=_cuerpo(_muestra()), headers=_auth())
    cliente.post(
        RUTA,
        json=_cuerpo(
            *(_muestra(medido_en=_iso(-timedelta(hours=h))) for h in (2, 3, 4)),
            fuente="simulado",
        ),
        headers=_auth(),
    )

    assert storage.delete_telemetry_by_source("simulado") == 3
    assert [f["fuente"] for f in _guardadas()] == ["atajos-iphone"]
