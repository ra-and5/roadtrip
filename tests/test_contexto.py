"""Tests del contexto del viaje (Fase 5, §2).

Sin red y sin API keys, como el resto de la suite.

Lo que se protege aquí, por orden de importancia:

  - **Que `/api/contexto` no llama a ningún LLM.** Es la razón de ser de la
    separación: si mañana alguien mete una llamada al modelo "porque queda
    mejor", esta ruta pasa de costar cero a costar tokens y doce segundos, y no
    daría ningún error — solo una pantalla lenta y una factura. Lo fija un
    proveedor que revienta si se le invoca.
  - **Que el contexto degrada en vez de caerse.** Con el tiempo caído sigue
    habiendo ubicación y momento, y se dice. Una app que oculta que le falta
    la mitad del contexto no es fiable, es opaca (decisión 9).
  - **Que un hueco no se puede confundir con un dato.** `fuentes` distingue
    "aquí no hay mar" de "la API del mar se cayó" de "no se ha preguntado".
    Es el corolario de la decisión 22, que hasta ahora estaba escrito y no
    implementado.
  - **Que las dos consultas van de verdad en paralelo.** En serie, la pantalla
    tarda la suma; el encargo pide bajar de dos segundos.
  - **La frontera de autenticación**, igual que en las notas: esta ruta es de
    sesión y el token de ingesta NO la abre.

Casi todo se prueba contra `ensamblar()`, que es una función pura. Esa es la
ventaja de haber separado la red del razonamiento: la degradación se comprueba
con datos escritos a mano, no doblando HTTP.
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import pytest
from werkzeug.security import generate_password_hash

from app.app import app as flask_app
from app.config import Config
from app.modules import ai_orchestrator, contexto, storage
from app.modules.contexto import (
    FALLO,
    NO_CONSULTADA,
    OK,
    SIN_DATOS,
    ensamblar,
)
from app.modules.location_context import InvalidCoordinates, LocationError, Place
from app.modules.luna import Efemerides
from app.modules.weather_context import Marine, Weather, WeatherError

RUTA = "/api/contexto"

TOKEN_INGESTA = "token-de-ingesta-de-mentira_AbCdEf123456"
HASH_TOKEN = generate_password_hash(TOKEN_INGESTA, method="pbkdf2:sha256:1000")

AHORA = datetime(2026, 7, 27, 18, 30, tzinfo=ZoneInfo("Europe/Madrid"))


def _place() -> Place:
    return Place(
        lat=43.5622,
        lon=-6.1456,
        name="Cudillero",
        region="Asturias",
        display_name="Cudillero, Asturias, España",
    )


def _weather(**campos: Any) -> Weather:
    base: dict[str, Any] = {
        "temperature_c": 20.0,
        "wind_speed_kmh": 6.0,
        "wind_gusts_kmh": 9.0,
        "weather_code": 3,
        "timezone": "Europe/Madrid",
    }
    base.update(campos)
    return Weather(**base)


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
    with cliente.session_transaction() as s:
        s["authenticated"] = True
    return cliente


@pytest.fixture
def sin_red(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Sustituye las TRES fuentes del contexto. Devuelve el configurador.

    Se parchean los nombres **tal y como los tiene importados `contexto`**, no
    en su módulo de origen: `from x import y` copia la referencia, así que
    parchear el origen no cambiaría lo que ejecuta el módulo bajo prueba. Es la
    misma trampa que documenta `test_app_despliegue.py` con `Config`.
    """

    def _configurar(*, lugar: Any = None, tiempo: Any = None, luna: Any = None) -> None:
        def _lugar(lat: float, lon: float) -> Place:
            if isinstance(lugar, Exception):
                raise lugar
            return lugar if lugar is not None else _place()

        def _tiempo(lat: float, lon: float) -> Weather:
            if isinstance(tiempo, Exception):
                raise tiempo
            return tiempo if tiempo is not None else _weather()

        def _luna(lat: float, lon: float, instante: Any) -> Any:
            if isinstance(luna, Exception):
                raise luna
            return luna if luna is not None else Efemerides(
                salida="2026-07-27T21:30+02:00", puesta="2026-07-28T06:12+02:00"
            )

        monkeypatch.setattr(contexto, "reverse_geocode", _lugar)
        monkeypatch.setattr(contexto, "get_weather", _tiempo)
        monkeypatch.setattr(contexto, "efemerides", _luna)

    _configurar()
    return _configurar


# ---------------------------------------------------------------------------
# Lo que justifica la fase: el contexto no cuesta tokens
# ---------------------------------------------------------------------------

def test_contexto_no_invoca_a_ningun_llm(sesion: Any, sin_red: Any,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """El motivo entero de partir el endpoint en dos.

    Si esta ruta acabara llamando al modelo, no daría ningún error: solo
    tardaría doce segundos y gastaría cuota en cada pulsación, que es
    exactamente lo que se ha venido a quitar. Un proveedor que revienta al
    construirse lo convierte en un fallo ruidoso.
    """

    def _explota() -> None:
        raise AssertionError("/api/contexto ha intentado construir un proveedor de LLM")

    monkeypatch.setattr(ai_orchestrator, "build_provider", _explota)

    respuesta = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456})

    assert respuesta.status_code == 200
    assert "recommendation" not in respuesta.get_json()


def test_el_modulo_de_contexto_no_conoce_a_los_proveedores(sin_red: Any) -> None:
    """Frontera de módulo, comprobable en vez de prometida.

    Es el mismo tipo de test que `test_cualquier_fallo_del_proveedor_sale_como_aierror`:
    fija una dependencia que NO debe existir. Si algún día `contexto` importa un
    proveedor, el chatbot y la pantalla dejarán de poder pedir contexto gratis.
    """
    fuente = inspect.getsource(contexto)
    imports = [l for l in fuente.splitlines() if l.startswith(("import ", "from "))]

    assert not [l for l in imports if "llm_providers" in l or "ai_orchestrator" in l]


def test_las_tres_fuentes_se_consultan_en_paralelo(monkeypatch: pytest.MonkeyPatch) -> None:
    """En serie se paga la suma de las latencias; en paralelo, la mayor.

    Con márgenes anchos a propósito: lo que se protege es que nadie convierta
    esto en código secuencial, no medir milisegundos. Tres fuentes de 0,3 s
    tardan 0,9 s en serie y ~0,3 s en paralelo.
    """

    def _lento(devuelve: Any) -> Any:
        def _f(*args: Any) -> Any:
            time.sleep(0.3)
            return devuelve
        return _f

    monkeypatch.setattr(contexto, "reverse_geocode", _lento(_place()))
    monkeypatch.setattr(contexto, "get_weather", _lento(_weather()))
    monkeypatch.setattr(contexto, "efemerides", _lento(Efemerides()))

    inicio = time.monotonic()
    contexto.construir(43.5622, -6.1456)
    transcurrido = time.monotonic() - inicio

    assert transcurrido < 0.55, f"parece que van en serie: {transcurrido:.2f} s"


# ---------------------------------------------------------------------------
# Degradación: qué pasa cuando falta cada fuente
# ---------------------------------------------------------------------------

def test_sin_tiempo_sigue_habiendo_ubicacion_y_momento(sesion: Any, sin_red: Any) -> None:
    """La degradación en cascada de la decisión 9, aplicada al contexto."""
    sin_red(tiempo=WeatherError("El servicio de meteorología tardó demasiado."))

    respuesta = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456})
    cuerpo = respuesta.get_json()

    assert respuesta.status_code == 200
    assert cuerpo["ubicacion"]["short_label"] == "Cudillero, Asturias"
    assert cuerpo["momento"]["hora"]
    assert cuerpo["tiempo"] is None
    assert cuerpo["fuentes"]["tiempo"]["estado"] == FALLO
    assert any("meteorológicos" in w for w in cuerpo["warnings"])


def test_sin_ubicacion_no_hay_contexto(sesion: Any, sin_red: Any) -> None:
    """La única fuente imprescindible. Un 200 con la ubicación vacía sería
    devolver un contexto que no contextualiza nada."""
    sin_red(lugar=LocationError("Sin conexión con el servicio de mapas."))

    respuesta = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456})

    assert respuesta.status_code == 502
    assert "mapas" in respuesta.get_json()["error"]


def test_coordenadas_invalidas_son_culpa_de_quien_llama(sesion: Any) -> None:
    respuesta = sesion.post(RUTA, json={"lat": 999, "lon": 0})

    assert respuesta.status_code == 400
    assert "rango" in respuesta.get_json()["error"]


def test_coordenadas_invalidas_no_molestan_a_los_servicios(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se valida ANTES de abrir hilos. Nominatim limita a 1 petición/segundo:
    gastarla en una coordenada que ya se sabe imposible es tirarla."""

    def _explota(lat: float, lon: float) -> None:
        raise AssertionError("no se debe consultar nada con coordenadas inválidas")

    monkeypatch.setattr(contexto, "reverse_geocode", _explota)
    monkeypatch.setattr(contexto, "get_weather", _explota)

    with pytest.raises(InvalidCoordinates):
        contexto.construir(999, 0)


def test_faltan_las_coordenadas(sesion: Any) -> None:
    assert sesion.post(RUTA, json={"lat": 43.5}).status_code == 400


# ---------------------------------------------------------------------------
# `fuentes`: por qué un `null` no basta
# ---------------------------------------------------------------------------

def test_tierra_adentro_no_es_una_averia(sin_red: Any) -> None:
    """La API marina responde 200 con todo a null tierra adentro (decisión 5).

    Eso NO es un fallo, y avisarlo haría que estar en León pareciera una avería.
    """
    estado = ensamblar(_place(), _weather(marine=Marine()), ahora=AHORA)

    assert estado.fuentes["oleaje"].estado == SIN_DATOS
    assert estado.avisos() == []


def test_la_api_marina_caida_si_es_una_averia(sin_red: Any) -> None:
    """Y aquí está la diferencia que un `null` no puede expresar.

    Sin este campo, estar en Cudillero con la API del mar caída se veía igual
    que estar en Palencia: `Marine` vacío. La app diría "esta ubicación no está
    junto al mar" estando en el puerto. Es el espejo suizo de Overpass otra vez
    (decisión 22): convertir "no he podido consultarlo" en "aquí no hay nada".
    """
    caida = Marine(fallo="Sin conexión con el servicio de oleaje.")
    estado = ensamblar(_place(), _weather(marine=caida), ahora=AHORA)

    assert estado.fuentes["oleaje"].estado == FALLO
    assert any("oleaje" in w.lower() for w in estado.avisos())


def test_sin_tiempo_el_oleaje_no_es_sin_datos_sino_no_consultado(sin_red: Any) -> None:
    """Decir "no hay oleaje" cuando no se ha llegado a preguntar es mentir por
    omisión."""
    estado = ensamblar(_place(), None, fallo_tiempo="timeout", ahora=AHORA)

    assert estado.fuentes["oleaje"].estado == NO_CONSULTADA


def test_el_hueco_reservado_se_declara(sin_red: Any) -> None:
    """Las métricas existen como hueco, con su motivo escrito.

    Que esté declarado y no ausente es lo que permite a la pantalla reservar el
    sitio y al chatbot saber que no es que no haya datos, es que todavía no se
    piden.
    """
    estado = ensamblar(_place(), _weather(), ahora=AHORA)

    assert estado.metricas is None
    assert estado.fuentes["metricas"].estado == NO_CONSULTADA
    assert "sin huecos" in estado.fuentes["metricas"].motivo


def test_sin_efemerides_la_luna_no_desaparece(sin_red: Any) -> None:
    """La fase se calcula, así que la luna sigue estando sin met.no.

    Es la degradación de la decisión 9 aplicada a la luna: un tercero caído la
    deja a medias en vez de borrarla. No confundir con "funciona sin
    cobertura": esto lo resuelve el servidor, y sin cobertura el móvil no llega
    a preguntar.
    """
    estado = ensamblar(_place(), _weather(), ahora=AHORA)

    assert estado.luna is not None
    assert estado.luna.fase.iluminacion_pct > 0
    assert estado.luna.efemerides is None


def test_no_pedir_las_efemerides_no_es_una_averia(sin_red: Any) -> None:
    """Tres casos, no dos: se pudo, falló, o nadie lo pidió.

    Marcar `fallo` cuando nadie ha preguntado sacaría un aviso de una avería
    que no ha ocurrido.
    """
    estado = ensamblar(_place(), _weather(), ahora=AHORA)

    assert estado.fuentes["luna"].estado == NO_CONSULTADA
    assert estado.avisos() == []


def test_las_efemerides_caidas_si_avisan(sin_red: Any) -> None:
    estado = ensamblar(_place(), _weather(), fallo_luna="met.no no responde.",
                       ahora=AHORA)

    assert estado.fuentes["luna"].estado == FALLO
    assert any("luna" in w.lower() for w in estado.avisos())


def test_un_hueco_declarado_no_genera_ruido(sin_red: Any) -> None:
    """`warnings` es lo que ha ido mal HOY, no el catálogo de lo que falta.

    Si los pasos avisaran en cada petición durante las semanas que tarde en
    cerrarse la 2d, se dejarían de leer los avisos — que es justo el diagnóstico
    del §3 del encargo sobre el aviso de POIs.
    """
    estado = ensamblar(_place(), _weather(), ahora=AHORA)

    assert estado.avisos() == []
    assert estado.fuentes["metricas"].estado == NO_CONSULTADA


def test_todo_fallo_tiene_aviso_y_todo_aviso_viene_de_un_fallo(sin_red: Any) -> None:
    """La invariante que hace fiable el par `fuentes` / `warnings`.

    Se comprueba porque los avisos se DERIVAN de las fuentes en vez de irse
    añadiendo a mano. Es la misma idea que poner la idempotencia de la ingesta
    en el UNIQUE de la tabla y no en un SELECT previo: una regla que depende de
    que alguien se acuerde no es una regla.
    """
    estado = ensamblar(
        _place(),
        None,
        fallo_tiempo="Open-Meteo devolvió un error (503).",
        ahora=AHORA,
    )

    fallos = [n for n, f in estado.fuentes.items() if f.estado == FALLO]
    assert len(estado.avisos()) == len(fallos)
    assert any("503" in w for w in estado.avisos())


# ---------------------------------------------------------------------------
# El momento, y la zona horaria supuesta
# ---------------------------------------------------------------------------

def test_la_hora_es_la_local_del_sitio_no_la_del_servidor(sin_red: Any) -> None:
    """PythonAnywhere corre en UTC. Recomendar "un plan de tarde" a las 20:00
    UTC cuando en Asturias son las 22:00 es un fallo real."""
    utc = datetime(2026, 7, 27, 20, 0, tzinfo=ZoneInfo("UTC"))
    estado = ensamblar(_place(), _weather(), ahora=utc)

    assert estado.momento.to_dict()["hora"] == "22:00"
    assert estado.momento.dia_semana == "lunes"


def test_sin_tiempo_la_zona_horaria_queda_marcada_como_supuesta(sin_red: Any) -> None:
    """El fallo silencioso que este campo hace visible.

    La zona la aporta Open-Meteo. Si el tiempo falla no hay zona, se cae a
    Europe/Madrid y hasta ahora eso pasaba sin decírselo a nadie: en Canarias,
    una hora de error en todo lo que cuelga de la hora local.
    """
    estado = ensamblar(_place(), None, fallo_tiempo="timeout", ahora=AHORA)

    assert estado.momento.zona_es_supuesta is True
    assert estado.fuentes["zona_horaria"].estado == FALLO
    assert any("hora local" in w for w in estado.avisos())


def test_con_tiempo_la_zona_no_se_supone(sin_red: Any) -> None:
    estado = ensamblar(_place(), _weather(timezone="Atlantic/Canary"), ahora=AHORA)

    assert estado.momento.zona_es_supuesta is False
    assert estado.momento.zona == "Atlantic/Canary"
    assert "zona_horaria" not in estado.fuentes


def test_una_zona_desconocida_se_trata_como_no_saber_la_hora(sin_red: Any) -> None:
    """Un sistema sin tzdata completo devolvería la zona equivocada en silencio."""
    estado = ensamblar(_place(), _weather(timezone="Marte/Olympus"), ahora=AHORA)

    assert estado.momento.zona_es_supuesta is True
    assert estado.momento.zona == "Europe/Madrid"


# ---------------------------------------------------------------------------
# Frontera de autenticación (decisión 24)
# ---------------------------------------------------------------------------

def test_sin_sesion_no_se_puede_pedir_contexto(cliente: Any) -> None:
    respuesta = cliente.post(RUTA, json={"lat": 43.5622, "lon": -6.1456})

    assert respuesta.status_code == 401
    assert respuesta.get_json()["error"] == "no_autenticado"


def test_el_token_de_ingesta_no_abre_el_contexto(cliente: Any, sin_red: Any) -> None:
    """Simétrico al que ya existe al revés. Cada ruta, exactamente un camino de
    autenticación: dos caminos hacia el mismo sitio es como se cuelan los
    *confused deputy* (decisión 24)."""
    respuesta = cliente.post(
        RUTA,
        json={"lat": 43.5622, "lon": -6.1456},
        headers={"Authorization": f"Bearer {TOKEN_INGESTA}"},
    )

    assert respuesta.status_code == 401


# ---------------------------------------------------------------------------
# Serialización
# ---------------------------------------------------------------------------

def test_el_json_lleva_siempre_todas_las_claves(sesion: Any, sin_red: Any) -> None:
    """Incluidas las vacías. Una clave que aparece y desaparece obliga a cada
    consumidor a defenderse, y tarde o temprano uno se olvida."""
    cuerpo = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456}).get_json()

    for clave in ("ubicacion", "momento", "tiempo", "luna", "metricas",
                  "fuentes", "warnings"):
        assert clave in cuerpo, f"falta {clave}"


def test_las_coordenadas_siguen_estando_en_el_contexto(sesion: Any, sin_red: Any) -> None:
    """El §5 las quitará de la TARJETA, que es cosa de la presentación. Del dato
    no: el mapa y las notas las necesitan."""
    cuerpo = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456}).get_json()

    assert cuerpo["ubicacion"]["lat"] == pytest.approx(43.5622)


# ---------------------------------------------------------------------------
# La pantalla (§5): lo que se enseña y lo que se guarda
# ---------------------------------------------------------------------------

def test_pedir_contexto_deja_constancia_del_sitio_del_dia(sesion: Any, sin_red: Any) -> None:
    """Lo pidió el usuario: que quede escrito dónde estabas la primera vez.

    Se registra desde la RUTA y no desde `construir()`, porque esa función la
    van a llamar también el recomendador y el chatbot.
    """
    sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456})

    filas = storage.list_lugares_del_dia()
    assert len(filas) == 1
    assert filas[0]["place_name"] == "Cudillero, Asturias"


def test_mirar_diez_veces_al_dia_no_son_diez_filas(sesion: Any, sin_red: Any) -> None:
    for _ in range(5):
        sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456})

    assert len(storage.list_lugares_del_dia()) == 1


def test_la_altitud_viaja_en_el_contexto(sesion: Any, sin_red: Any) -> None:
    """Sale gratis de Open-Meteo, en la misma respuesta del tiempo.

    Sustituye a las coordenadas crudas en la tarjeta: "a 24 m de altitud" dice
    algo, "43.56220, -6.14560" no.
    """
    sin_red(tiempo=_weather(elevation_m=24.0))

    cuerpo = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456}).get_json()

    assert cuerpo["tiempo"]["elevation_m"] == 24.0


def test_sin_tiempo_tampoco_hay_altitud_y_no_se_inventa(sesion: Any, sin_red: Any) -> None:
    """Un cero aquí sería "estás al nivel del mar", que es una afirmación."""
    sin_red(tiempo=WeatherError("Open-Meteo no responde."))

    cuerpo = sesion.post(RUTA, json={"lat": 43.5622, "lon": -6.1456}).get_json()

    assert cuerpo["tiempo"] is None
