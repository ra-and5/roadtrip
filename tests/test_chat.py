"""Tests del chatbot (Fase 6). Sin red y sin API keys, con el proveedor doblado.

Lo que se protege aquí, por orden de importancia:

  - **Que lo que se ENVÍA está acotado.** Es la petición explícita del usuario
    ("no me interesa que acabe siendo carísimo"), y es un fallo que no da error:
    si la ventana se rompe, todo sigue funcionando y solo crece la factura, que
    es lo último que se mira.
  - **Que un dato simulado no se presenta como cierto.** El aviso viaja en el
    texto del prompt, así que si alguien lo quita, ningún test de tipos ni de
    esquema se entera: solo un modelo afirmando con seguridad cuántos pasos has
    dado hoy, inventados.
  - **La frontera de autenticación.** `/api/chat` va con sesión, no con el token
    de ingesta. Son dos caminos que no se cruzan (decisión 24).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import pytest
from werkzeug.security import generate_password_hash

from app.app import app as flask_app
from app.config import Config
from app.modules import chat, map_tools, storage
from app.modules.contexto import ensamblar
from app.modules.llm_providers import AIError, LLMProvider
from app.modules.location_context import Place
from app.modules.metricas import Metricas
from app.modules.viaje import Viaje
from app.modules.weather_context import Marine, Weather

AHORA = datetime(2026, 7, 28, 17, 20, tzinfo=ZoneInfo("Europe/Madrid"))
CONTRASENA = "una-contrasena-de-prueba"


class FakeProvider(LLMProvider):
    """Proveedor de mentira que registra lo que recibe."""

    def __init__(self, respuesta: str = "", error: Exception | None = None) -> None:
        super().__init__("modelo-1")
        self.name = "fake"
        self._respuesta = respuesta or json.dumps({"respuesta": "Pues mira, sí."})
        self._error = error
        self.llamadas: list[dict[str, Any]] = []

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        self.llamadas.append({"system": system, "context": context, "schema": schema})
        if self._error:
            raise self._error
        return self._respuesta


class FakeTools:
    """Herramienta de mapa doblada: no toca red, solo devuelve datos."""

    def buscar_sitios(self, consulta: str, lat: float, lon: float) -> list[map_tools.ToolPlace]:
        return [
            map_tools.ToolPlace(
                nombre="Bar Puerto",
                abierto="abierto ahora",
                rating=4.4,
                direccion="Muelle 1",
                maps_url="https://maps.example/bar-puerto",
            )
        ]

    def calcular_ruta(self, origen: str, destino: str) -> map_tools.ToolRoute:
        return map_tools.ToolRoute(
            origen=origen,
            destino=destino,
            distancia_km=121.0,
            duracion_trafico_min=90,
            maps_url="https://maps.example/ruta",
        )


def _place() -> Place:
    return Place(
        lat=43.5622, lon=-6.1456, name="Cudillero", region="Asturias",
        display_name="Cudillero, Asturias, España",
    )


def _weather() -> Weather:
    return Weather(
        temperature_c=21.0,
        wind_speed_kmh=6.0,
        wind_gusts_kmh=9.0,
        weather_code=3,
        timezone="Europe/Madrid",
    )


def _contexto(metricas: Metricas | None = None, viaje: Viaje | None = None) -> Any:
    return ensamblar(_place(), _weather(), metricas=metricas, viaje=viaje, ahora=AHORA)


def _mensajes(cuantos: int) -> list[dict[str, Any]]:
    return [
        {"rol": "usuario" if i % 2 == 0 else "asistente", "texto": f"mensaje {i}"}
        for i in range(cuantos)
    ]


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(
        Config, "APP_PASSWORD_HASH",
        generate_password_hash(CONTRASENA, method="pbkdf2:sha256:1000"),
    )
    storage.init_db()
    yield


@pytest.fixture
def cliente() -> Iterator[Any]:
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        c.post("/login", data={"password": CONTRASENA})
        yield c


@pytest.fixture
def anonimo() -> Iterator[Any]:
    flask_app.config["TESTING"] = True
    yield flask_app.test_client()


# ---------------------------------------------------------------------------
# Lo que cuesta dinero: la ventana del historial
# ---------------------------------------------------------------------------

def test_solo_viajan_los_ultimos_turnos() -> None:
    """LA propiedad de esta fase, y la que no da error si se rompe.

    Con la conversación entera en cada mensaje, el coste crece con el viaje y
    nadie se entera hasta la factura. Guardar cincuenta mensajes está bien;
    enviarlos, no.
    """
    recortado = chat.ventana(_mensajes(50))

    assert len(recortado) == chat.VENTANA_HISTORIAL
    assert recortado[-1]["texto"] == "mensaje 49"


def test_una_conversacion_corta_viaja_entera() -> None:
    assert len(chat.ventana(_mensajes(2))) == 2
    assert chat.ventana([]) == []


def test_el_prompt_solo_lleva_la_ventana() -> None:
    """No basta con que `ventana()` recorte: hay que comprobar que el prompt la
    usa. Son dos cosas distintas y la segunda es la que se paga."""
    proveedor = FakeProvider()

    chat.responder("¿y ahora?", _contexto(), _mensajes(50), provider=proveedor)

    enviado = proveedor.llamadas[0]["context"]
    assert "mensaje 49" in enviado
    assert "mensaje 10" not in enviado


def test_el_chat_mete_herramientas_en_el_prompt() -> None:
    """Preguntar por un bar no puede depender de la memoria general del modelo."""
    proveedor = FakeProvider()

    chat.responder(
        "bar más cerca",
        _contexto(),
        [],
        provider=proveedor,
        tools_provider=FakeTools(),
    )

    enviado = proveedor.llamadas[0]["context"]
    assert "### HERRAMIENTAS CONSULTADAS" in enviado
    assert "Bar Puerto" in enviado
    assert "abierto ahora" in enviado


def test_el_chat_mete_veredicto_de_paddle_en_herramientas() -> None:
    proveedor = FakeProvider()

    chat.responder(
        "puedo sacar la tabla de paddle?",
        _contexto(),
        [],
        provider=proveedor,
        tools_provider=FakeTools(),
    )

    enviado = proveedor.llamadas[0]["context"]
    assert "LECTURAS DEL CONTEXTO" in enviado
    assert "PADDLE_SURF" in enviado
    assert "sin datos" in enviado.lower()


# ---------------------------------------------------------------------------
# Lo que no se puede callar
# ---------------------------------------------------------------------------

def test_el_prompt_avisa_de_que_las_metricas_son_simuladas() -> None:
    """Si esto se cae, el modelo afirma como cierto un dato que nos hemos
    inventado, y no hay forma de detectarlo mirando la pantalla: la respuesta
    parece perfectamente razonable."""
    proveedor = FakeProvider()
    metricas = Metricas(pasos_hoy=12757, hay_datos=True, es_simulado=True)

    chat.responder("¿cuántos pasos llevo?", _contexto(metricas), [], provider=proveedor)

    enviado = proveedor.llamadas[0]["context"]
    assert "12.757" in enviado
    assert "SIMULADAS" in enviado


def test_unas_metricas_reales_no_llevan_el_aviso() -> None:
    """El aviso tiene que significar algo. Si saliera siempre, se ignoraría."""
    proveedor = FakeProvider()
    metricas = Metricas(pasos_hoy=5688, hay_datos=True, es_simulado=False)

    chat.responder("¿cuántos pasos llevo?", _contexto(metricas), [], provider=proveedor)

    assert "SIMULADAS" not in proveedor.llamadas[0]["context"]


def test_el_prompt_distingue_no_pedido_de_no_haber_datos() -> None:
    """Es la decisión 32 llegando hasta el texto: el modelo tiene que poder
    decir "no lo he mirado" en vez de "no tienes datos"."""
    sin_pedir = chat.construir_prompt("hola", _contexto(), [])
    vacias = chat.construir_prompt("hola", _contexto(Metricas()), [])

    assert "No se han consultado" in sin_pedir
    assert "no ha enviado ninguna muestra" in vacias


def test_el_prompt_lleva_el_viaje_con_las_notas() -> None:
    """Sin el texto de las notas, el modelo sabe por dónde pasaste pero no qué
    te pareció, que es la mitad de para lo que sirve preguntarle."""
    viaje = Viaje(
        dias=4, lugares=3, km=41.0, notas_totales=2, fotos=4,
        regiones=["Asturias"], hay_datos=True,
        recientes=[{"cuando": "2026-07-27T10:00:00+00:00",
                    "lugar": "Cudillero", "texto": "El puerto al atardecer, brutal."}],
    )

    prompt = chat.construir_prompt("¿qué escribí?", _contexto(viaje=viaje), [])

    assert "Cudillero" in prompt
    assert "El puerto al atardecer" in prompt
    assert "Asturias" in prompt


def test_el_prompt_lleva_ubicacion_tiempo_y_luna() -> None:
    """El chatbot razona sobre el MISMO contexto que la pantalla: si esto se
    rompiera, cada cara del proyecto contaría un viaje distinto."""
    prompt = chat.construir_prompt("¿qué hago?", _contexto(), [])

    assert "Cudillero" in prompt
    assert "21" in prompt              # la temperatura
    assert "LUNA" in prompt
    assert "¿qué hago?" in prompt


# ---------------------------------------------------------------------------
# El proveedor
# ---------------------------------------------------------------------------

def test_la_respuesta_sale_del_json() -> None:
    proveedor = FakeProvider(json.dumps({"respuesta": "Vete a la playa."}))

    respuesta = chat.responder(
        "¿qué hago?", _contexto(), [], provider=proveedor, tools_provider=FakeTools()
    )

    assert respuesta.texto == "Vete a la playa."
    assert respuesta.proveedor == "fake"
    assert respuesta.modelo == "modelo-1"


def test_una_respuesta_sin_json_no_se_tira() -> None:
    """Un modelo local o mal configurado puede contestar la frase a secas.
    Tirarla sería perder algo útil por una formalidad."""
    proveedor = FakeProvider("Vete a la playa.")

    assert (
        chat.responder("?", _contexto(), [], provider=proveedor, tools_provider=FakeTools()).texto
        == "Vete a la playa."
    )


def test_cualquier_fallo_del_proveedor_sale_como_aierror() -> None:
    """La misma frontera que fija `test_ai_orchestrator`: fuera del módulo de
    proveedores no puede aparecer una excepción de Anthropic ni de Google."""
    proveedor = FakeProvider(error=AIError("cuota agotada"))

    with pytest.raises(AIError):
        chat.responder("?", _contexto(), [], provider=proveedor, tools_provider=FakeTools())


# ---------------------------------------------------------------------------
# La ruta
# ---------------------------------------------------------------------------

def test_el_chat_exige_sesion(anonimo: Any) -> None:
    """Con sesión, y NO con el token de ingesta: son dos caminos que no se
    cruzan (decisión 24)."""
    respuesta = anonimo.post("/api/chat", json={"lat": 43.5, "lon": -6.1, "mensaje": "hola"})

    assert respuesta.status_code in (302, 401)


def test_un_mensaje_vacio_da_400(cliente: Any) -> None:
    for cuerpo in ({"lat": 43.5, "lon": -6.1}, {"lat": 43.5, "lon": -6.1, "mensaje": "  "}):
        assert cliente.post("/api/chat", json=cuerpo).status_code == 400


def test_sin_coordenadas_da_400(cliente: Any) -> None:
    assert cliente.post("/api/chat", json={"mensaje": "hola"}).status_code == 400


def test_un_mensaje_kilometrico_da_400(cliente: Any) -> None:
    """El techo del cuerpo permite megabytes; un pegote enorme se pagaría en
    tokens sin que nadie lo hubiera decidido."""
    respuesta = cliente.post(
        "/api/chat",
        json={"lat": 43.5, "lon": -6.1, "mensaje": "a" * (chat.MAX_PREGUNTA + 1)},
    )

    assert respuesta.status_code == 400


def test_el_historial_se_guarda_y_se_puede_borrar(cliente: Any) -> None:
    chat.guardar("usuario", "¿qué hago?", _place())
    chat.guardar("asistente", "Vete a la playa.", _place())

    cuerpo = cliente.get("/api/chat").get_json()
    assert [m["rol"] for m in cuerpo["mensajes"]] == ["usuario", "asistente"]
    assert cuerpo["mensajes"][0]["lugar"] == "Cudillero, Asturias"

    assert cliente.delete("/api/chat").get_json()["borrados"] == 2
    assert cliente.get("/api/chat").get_json()["mensajes"] == []


def test_el_historial_sale_en_orden_cronologico() -> None:
    """Se consulta al revés (los N últimos por id) y se devuelve en orden. Si
    quien llama tuviera que acordarse de invertirlo, algún día no lo haría y el
    modelo leería la conversación del revés."""
    for i in range(5):
        chat.guardar("usuario", f"mensaje {i}", None)

    assert [m["texto"] for m in chat.historial()] == [f"mensaje {i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Lo que el prompt no puede afirmar
# ---------------------------------------------------------------------------

def test_no_se_afirma_que_no_hay_pois_sin_haberlos_buscado() -> None:
    """El chatbot nunca llama a Overpass, así que su lista siempre está vacía.

    Si el prompt tradujera ese vacío a "no hay nada mapeado aquí", el modelo
    descartaría la zona por un dato que nos hemos inventado, y lo diría con
    seguridad porque se lo hemos afirmado nosotros. Es el corolario de la
    decisión 22 llegando hasta el texto.
    """
    prompt = chat.construir_prompt("¿qué hay cerca?", _contexto(), [])

    assert "No se han buscado" in prompt
    assert "No hay puntos de interés mapeados" not in prompt


def test_sin_muestra_de_hoy_no_se_dice_que_no_ha_andado() -> None:
    """Un bloque titulado "su actividad de hoy" sin pasos se lee como cero
    pasos. Que no haya llegado la muestra y que no haya andado son cosas
    distintas, y a las 00:30 la primera es la normal."""
    metricas = Metricas(
        pasos_por_dia=[("2026-07-27", 9000), ("2026-07-28", 11000)], hay_datos=True
    )

    prompt = chat.construir_prompt("¿cuánto he andado?", _contexto(metricas), [])

    assert "no se sabe cuánto ha andado" in prompt.lower()


def test_la_media_diaria_no_cuenta_los_dias_parciales() -> None:
    """Hoy va a medias por definición: a las 11:00 llevas 2.000 pasos. Meterlo
    en la media la hunde, y luego "vas por debajo de tu media" sale calculado a
    favor de sí mismo.

    El día en curso se declara por NOMBRE en `dias_parciales` y no se adivina
    descartando el último elemento. La versión que adivinaba fallaba de dos
    formas mudas: si hoy aún no ha llegado ninguna muestra tiraba AYER, que es
    un día bueno; y un día que perdió su envío de las 23:55 entraba como
    completo con un total truncado, arrastrando la media hacia abajo.
    """
    metricas = Metricas(
        pasos_por_dia=[("2026-07-26", 10000), ("2026-07-27", 12000), ("2026-07-28", 500)],
        dias_parciales=("2026-07-28",),
        hay_datos=True,
    )

    assert metricas.media_diaria == 11000


def test_el_viaje_no_escribe_un_dia_en_plural() -> None:
    """Un texto descuidado es una señal de que los datos también lo son, y un
    modelo entrenado con lenguaje natural la lee como tal."""
    viaje = Viaje(dias=1, fotos=1, hay_datos=True)

    prompt = chat.construir_prompt("?", _contexto(viaje=viaje), [])

    assert "1 día," in prompt or "1 día." in prompt
    assert "1 días" not in prompt
    # Y lo que vale cero no se escribe: "0 lugares distintos" es ruido.
    assert "0 lugares" not in prompt
