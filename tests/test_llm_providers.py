"""Tests de la abstracción de proveedores de LLM.

Ninguno toca la red ni necesita API key: usamos un proveedor simulado y
fabricamos las excepciones de cada SDK con la misma forma que las reales.

Lo que se protege aquí:
  - que se puede cambiar de proveedor con una variable,
  - que ningún tipo ni excepción de un proveedor concreto escapa del módulo,
  - que un 429 degrada como una condición esperada y no rompe la respuesta,
  - que la API key NUNCA aparece en un mensaje de error, en ningún modo.
"""

from typing import Any

import pytest

from app.config import Config
from app.modules.llm_providers import (
    AIError,
    AnthropicProvider,
    GeminiProvider,
    KimiProvider,
    LLMProvider,
    MAX_OUTPUT_TOKENS,
    PROVIDER_NAMES,
    build_provider,
    redact,
)


class FakeProvider(LLMProvider):
    """Proveedor de mentira: devuelve lo que le digas, o lanza lo que le digas."""

    name = "fake"

    def __init__(self, *, respuesta: str = "{}", error: Exception | None = None) -> None:
        super().__init__("modelo-falso")
        self._respuesta = respuesta
        self._error = error
        self.llamadas: list[dict[str, Any]] = []

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        self.llamadas.append({"system": system, "context": context, "schema": schema})
        if self._error:
            raise self._error
        return self._respuesta


# --- Selección de proveedor -------------------------------------------------

def test_registro_expone_todos_los_proveedores():
    assert set(PROVIDER_NAMES) == {"anthropic", "gemini", "kimi", "ollama"}


def test_build_provider_usa_la_variable_de_entorno(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "AIza-de-mentira-para-el-test")
    provider = build_provider()
    assert provider.name == "gemini"


def test_build_provider_acepta_un_nombre_explicito(monkeypatch):
    """El diagnóstico necesita poder probar un proveedor concreto."""
    monkeypatch.setattr(Config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "AIza-de-mentira-para-el-test")
    assert build_provider("gemini").name == "gemini"


def test_build_provider_normaliza_espacios_y_mayusculas(monkeypatch):
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "AIza-de-mentira-para-el-test")
    assert build_provider("  GEMINI ").name == "gemini"


def test_proveedor_desconocido_lista_las_opciones(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "chatgpt")
    with pytest.raises(AIError) as exc_info:
        build_provider()
    mensaje = str(exc_info.value)
    assert "chatgpt" in mensaje
    assert "anthropic" in mensaje and "gemini" in mensaje


def test_ollama_da_un_error_util_no_uno_generico():
    """Está registrado a propósito: el error debe explicar qué falta."""
    with pytest.raises(AIError) as exc_info:
        build_provider("ollama")
    assert "no está implementado" in str(exc_info.value)


def test_falta_de_key_sugiere_la_alternativa_gratuita(monkeypatch):
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "")
    with pytest.raises(AIError) as exc_info:
        build_provider("anthropic")
    assert "gemini" in str(exc_info.value).lower()


# --- Redacción de secretos --------------------------------------------------

def test_redact_borra_la_key_configurada(monkeypatch):
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "sk-ant-secreto-larguisimo-123")
    texto = "fallo con la clave sk-ant-secreto-larguisimo-123 al final"
    assert "secreto" not in redact(texto)
    assert "[API_KEY_OCULTA]" in redact(texto)


def test_redact_borra_tambien_fragmentos_parciales(monkeypatch):
    """El requisito dice 'ni completa ni parcial'.

    Un mensaje que trae solo un trozo de la clave (porque el proveedor la
    truncó al reportar el error) seguiría siendo una filtración.
    """
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "AIzaSyABCDEFGHIJKLMNOP12345")
    texto = "clave inválida: AIzaSyABCDEFGH..."
    resultado = redact(texto)
    assert "AIzaSyABCDEFGH" not in resultado


def test_redact_borra_claves_no_configuradas_por_patron(monkeypatch):
    """Aunque la clave no sea la nuestra, si parece una clave se tapa."""
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    assert "sk-ant-" not in redact("error con sk-ant-api03-otracosa-larga")
    assert "AIzaSy" not in redact("url ?key=AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123")


def test_redact_tolera_texto_vacio():
    assert redact("") == ""


def test_la_key_no_aparece_ni_con_el_detalle_activado(monkeypatch):
    """La garantía NO depende del interruptor: es incondicional."""
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", True)
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "AIzaSyCLAVESECRETA1234567890")

    exc = _gemini_error(401, "API key AIzaSyCLAVESECRETA1234567890 rechazada")
    traducido = GeminiProvider._translate(exc)
    assert "CLAVESECRETA" not in str(traducido)


# --- Interruptor de detalle -------------------------------------------------

def _gemini_error(code: int, message: str) -> Any:
    """Fabrica algo con la forma de google.genai.errors.APIError."""
    class FakeGeminiError(Exception):
        def __init__(self) -> None:
            super().__init__(message)
            self.code = code
            self.message = message

    return FakeGeminiError()


def test_detalle_oculto_por_defecto(monkeypatch):
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", False)
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    mensaje = str(GeminiProvider._translate(_gemini_error(500, "algo interno raro")))
    assert "algo interno raro" not in mensaje
    assert "SHOW_AI_ERROR_DETAIL" in mensaje  # dice cómo verlo


def test_detalle_visible_cuando_se_activa(monkeypatch):
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", True)
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    mensaje = str(GeminiProvider._translate(_gemini_error(500, "algo interno raro")))
    assert "algo interno raro" in mensaje


# --- Mapeo de errores a AIError ---------------------------------------------

def test_429_se_traduce_a_condicion_esperada(monkeypatch):
    """Un 429 de capa gratuita no es un fallo del sistema: es cuota agotada.

    Debe seguir siendo AIError (para que la ruta degrade como con cualquier
    otra fuente caída) pero con un mensaje que diga qué hacer.
    """
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", False)
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    error = GeminiProvider._translate(_gemini_error(429, "quota exceeded"))

    assert isinstance(error, AIError)
    mensaje = str(error)
    assert "429" in mensaje
    assert "capa gratuita" in mensaje.lower()
    assert "espera" in mensaje.lower()  # dice qué hacer, no solo que falló


@pytest.mark.parametrize("code,esperado", [
    (401, "no es válida"),
    (403, "no es válida"),
    # El 404 debe apuntar a la herramienta que resuelve el problema, no solo
    # nombrar la variable: un modelo puede aparecer listado por la API y aun
    # así devolver 404 ("no longer available to new users"), así que "revisa
    # GEMINI_MODEL" no basta para saber cuál poner.
    (404, "listar_modelos.py"),
])
def test_codigos_de_gemini_dan_mensajes_accionables(monkeypatch, code, esperado):
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    assert esperado in str(GeminiProvider._translate(_gemini_error(code, "x")))


def test_el_404_incluye_el_motivo_de_la_api(monkeypatch):
    """Un 404 puede ser 'no existe' o 'ya no se sirve a cuentas nuevas'.

    Se arreglan distinto, así que el motivo tiene que verse.
    """
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", True)
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    mensaje = str(GeminiProvider._translate(
        _gemini_error(404, "This model is no longer available to new users.")
    ))
    assert "no longer available to new users" in mensaje


def test_effort_invalido_falla_antes_de_llamar(monkeypatch):
    """Un typo en ANTHROPIC_EFFORT daría un 400 críptico de la API.

    Validarlo al construir el proveedor da un error que nombra la variable.
    """
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "sk-ant-de-mentira")
    monkeypatch.setattr(Config, "ANTHROPIC_EFFORT", "low  # comentario pegado")
    with pytest.raises(AIError) as exc_info:
        AnthropicProvider()

    mensaje = str(exc_info.value)
    assert "ANTHROPIC_EFFORT" in mensaje
    assert "low, medium, high, xhigh, max" in mensaje


def test_extract_text_detecta_rechazo():
    """stop_reason='refusal' antes de leer content: content puede venir vacío."""
    class FakeResponse:
        stop_reason = "refusal"
        content: list = []

    with pytest.raises(AIError) as exc_info:
        AnthropicProvider._extract_text(FakeResponse())
    assert "declinó" in str(exc_info.value)


def test_extract_text_salta_bloques_que_no_son_texto():
    """content trae bloques de varios tipos; content[0].text se rompería."""
    class Block:
        def __init__(self, type_: str, text: str = "") -> None:
            self.type = type_
            self.text = text

    class FakeResponse:
        stop_reason = "end_turn"
        content = [Block("thinking"), Block("text", '{"ok": true}')]

    assert AnthropicProvider._extract_text(FakeResponse()) == '{"ok": true}'


# --- Kimi (Moonshot) --------------------------------------------------------
#
# Kimi se habla por HTTP con requests, no con un SDK, así que aquí se simula la
# capa de transporte: una sesión falsa que devuelve la respuesta que le digas.
# Ninguno de estos tests toca la red.

class FakeHttpResponse:
    """Algo con la forma de requests.Response, lo justo para estos tests."""

    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload


class FakeSession:
    """Sesión falsa: registra lo que se envía y devuelve lo que se le diga."""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.enviado: dict[str, Any] = {}

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> Any:
        self.enviado = {"url": url, "json": json, "timeout": timeout}
        if self._error:
            raise self._error
        return self._response


_SCHEMA_DE_PRUEBA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def _kimi(monkeypatch, session: Any = None, **config: Any) -> KimiProvider:
    """Construye un KimiProvider con la config pedida y el transporte simulado."""
    monkeypatch.setattr(Config, "KIMI_API_KEY", config.pop("key", "sk-de-mentira-para-el-test"))
    monkeypatch.setattr(Config, "KIMI_MODEL", config.pop("modelo", "kimi-k3"))
    monkeypatch.setattr(Config, "KIMI_REASONING_EFFORT", config.pop("effort", "low"))
    provider = KimiProvider()
    if session is not None:
        # Saltamos _client(): no queremos abrir una sesión HTTP de verdad.
        provider._cached_client = session
    return provider


def _respuesta_ok(content: str = '{"ok": true}', **extra: Any) -> dict[str, Any]:
    choice = {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
    choice.update(extra)
    return {"choices": [choice], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _error_kimi(status: int, tipo: str, mensaje: str = "x") -> FakeHttpResponse:
    return FakeHttpResponse(status, {"error": {"type": tipo, "message": mensaje}})


# --- Kimi: configuración ---

def test_kimi_falta_de_key_sugiere_la_alternativa_gratuita(monkeypatch):
    monkeypatch.setattr(Config, "KIMI_API_KEY", "")
    with pytest.raises(AIError) as exc_info:
        build_provider("kimi")
    mensaje = str(exc_info.value)
    assert "KIMI_API_KEY" in mensaje
    assert "gemini" in mensaje.lower()


def test_kimi_rechaza_los_valores_de_effort_de_anthropic(monkeypatch):
    """Las dos escalas se parecen lo bastante para confundirlas.

    'medium' y 'xhigh' son válidos en Anthropic y NO existen en Kimi. Copiar la
    línea del .env de un proveedor a otro daría un 400 a mitad de petición; se
    detecta al construir, con un error que nombra la variable.
    """
    monkeypatch.setattr(Config, "KIMI_API_KEY", "sk-de-mentira-para-el-test")
    monkeypatch.setattr(Config, "KIMI_REASONING_EFFORT", "medium")
    with pytest.raises(AIError) as exc_info:
        KimiProvider()

    mensaje = str(exc_info.value)
    assert "KIMI_REASONING_EFFORT" in mensaje
    assert "low, high, max" in mensaje
    assert "ANTHROPIC_EFFORT" in mensaje  # dice con qué se está confundiendo


@pytest.mark.parametrize("modelo,esperado", [
    # k3 razona siempre y se regula con reasoning_effort.
    ("kimi-k3", {"reasoning_effort": "low"}),
    # k2.6 permite apagar el razonamiento, y lo apagamos: se factura como
    # tokens de salida, que son los caros.
    ("kimi-k2.6", {"thinking": {"type": "disabled"}}),
    # k2.7-code lo tiene fijo en "enabled": mandarle el campo da error, así
    # que no se manda nada.
    ("kimi-k2.7-code", {}),
])
def test_kimi_usa_el_mando_de_razonamiento_de_cada_familia(monkeypatch, modelo, esperado):
    assert _kimi(monkeypatch, modelo=modelo)._thinking_params() == esperado


# --- Kimi: forma de la petición ---

def test_kimi_pide_salida_estructurada_estricta(monkeypatch):
    """Sin strict=true la API solo promete 'un JSON válido'.

    Esa es justo la promesa que no sirve: el frontend necesita ESTOS campos,
    no cualquier JSON (decisión 8).
    """
    session = FakeSession(FakeHttpResponse(200, _respuesta_ok()))
    provider = _kimi(monkeypatch, session)
    provider.generate(system="sistema", context="contexto", schema=_SCHEMA_DE_PRUEBA)

    enviado = session.enviado["json"]
    formato = enviado["response_format"]
    assert formato["type"] == "json_schema"
    assert formato["json_schema"]["strict"] is True
    assert formato["json_schema"]["schema"] == _SCHEMA_DE_PRUEBA
    # El esquema se pasa TAL CUAL: si este proveedor lo adaptase, tendría una
    # copia propia y divergiría del que se afina en ai_orchestrator.

    assert enviado["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "contexto"},
    ]
    # `max_tokens` está deprecado en esta API; mandarlo sería silenciosamente
    # ignorado y el techo de salida no se aplicaría.
    assert enviado["max_completion_tokens"] == MAX_OUTPUT_TOKENS
    assert "max_tokens" not in enviado
    assert session.enviado["timeout"] > 60  # un LLM tarda segundos, no milisegundos


def test_kimi_devuelve_el_content_y_guarda_los_tokens(monkeypatch):
    session = FakeSession(FakeHttpResponse(200, _respuesta_ok('{"resumen": "hola"}')))
    provider = _kimi(monkeypatch, session)

    assert provider.generate(system="s", context="c", schema=_SCHEMA_DE_PRUEBA) == '{"resumen": "hola"}'
    assert provider.last_usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_kimi_ignora_reasoning_content(monkeypatch):
    """El razonamiento viene en otro campo y NO es la respuesta.

    Concatenarlo o devolverlo por error rompería el json.loads de arriba.
    """
    payload = _respuesta_ok('{"ok": true}')
    payload["choices"][0]["message"]["reasoning_content"] = "primero pienso esto..."
    provider = _kimi(monkeypatch, FakeSession(FakeHttpResponse(200, payload)))

    assert provider.generate(system="s", context="c", schema=_SCHEMA_DE_PRUEBA) == '{"ok": true}'


# --- Kimi: respuestas 200 que no sirven ---

def test_kimi_detecta_el_json_truncado(monkeypatch):
    """finish_reason='length' llega con HTTP 200 y el JSON a medias.

    Sin comprobarlo, el fallo aparecería tres capas más arriba como 'no era
    JSON válido', que no dice nada sobre cómo arreglarlo.
    """
    payload = _respuesta_ok('{"resumen": "empeza', finish_reason="length")
    provider = _kimi(monkeypatch, FakeSession(FakeHttpResponse(200, payload)))

    with pytest.raises(AIError) as exc_info:
        provider.generate(system="s", context="c", schema=_SCHEMA_DE_PRUEBA)
    mensaje = str(exc_info.value)
    assert "incompleto" in mensaje
    assert "KIMI_REASONING_EFFORT" in mensaje  # dice qué mover


@pytest.mark.parametrize("payload", [
    {"choices": []},
    {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]},
])
def test_kimi_respuesta_vacia_es_un_error_explicito(monkeypatch, payload):
    provider = _kimi(monkeypatch, FakeSession(FakeHttpResponse(200, payload)))
    with pytest.raises(AIError):
        provider.generate(system="s", context="c", schema=_SCHEMA_DE_PRUEBA)


# --- Kimi: traducción de errores ---

def test_kimi_distingue_quedarse_sin_saldo_de_ir_demasiado_rapido(monkeypatch):
    """Los dos son 429 y se arreglan de forma OPUESTA.

    Sin saldo, esperar no sirve de nada: hay que recargar. Presentarlo como
    'prueba en un minuto' manda al usuario a reintentar contra un muro.
    """
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", False)

    sin_saldo = str(KimiProvider._translate(_error_kimi(429, "exceeded_current_quota_error")))
    assert "saldo" in sin_saldo.lower()
    assert "recarga" in sin_saldo.lower()
    # Lo que NO debe decir: que esperar arregla algo. Aquí no arregla nada.
    assert "espera un minuto" not in sin_saldo.lower()

    demasiado_rapido = str(KimiProvider._translate(_error_kimi(429, "rate_limit_reached_error")))
    assert "espera un minuto" in demasiado_rapido.lower()
    assert "recarga" not in demasiado_rapido.lower()  # ni manda a gastar dinero

    saturado = str(KimiProvider._translate(_error_kimi(429, "engine_overloaded_error")))
    assert "saturad" in saturado.lower()
    assert "saldo" in saturado.lower()  # aclara que NO es un problema de dinero


def test_kimi_401_menciona_la_plataforma_no_solo_la_key(monkeypatch):
    """Una key buena contra el endpoint equivocado da el MISMO 401.

    Verificado en la documentación: las keys de las plataformas regionales de
    Moonshot no son intercambiables. Sin este aviso, el usuario tira una key
    que funciona perfectamente.
    """
    mensaje = str(KimiProvider._translate(_error_kimi(401, "incorrect_api_key_error")))
    assert "KIMI_BASE_URL" in mensaje


def test_kimi_404_lista_modelos_validos(monkeypatch):
    monkeypatch.setattr(Config, "KIMI_MODEL", "kimi-k9-inventado")
    mensaje = str(KimiProvider._translate(_error_kimi(404, "resource_not_found_error")))
    assert "kimi-k9-inventado" in mensaje
    assert "kimi-k3" in mensaje  # dice cuál poner, no solo que el tuyo no vale


def test_kimi_error_sin_cuerpo_json_no_revienta():
    """Un 502 de un proxy devuelve HTML, no el JSON de error de la API."""
    respuesta = FakeHttpResponse(502, None, text="<html>Bad Gateway</html>")
    assert isinstance(KimiProvider._translate(respuesta), AIError)


def test_kimi_ningun_fallo_de_requests_escapa_del_modulo(monkeypatch):
    """La frontera del módulo: arriba solo puede llegar AIError.

    Si app.py tuviera que capturar requests.ConnectionError, la abstracción
    habría fracasado.
    """
    import requests

    for excepcion in (requests.Timeout(), requests.ConnectionError(), requests.TooManyRedirects()):
        provider = _kimi(monkeypatch, FakeSession(error=excepcion))
        with pytest.raises(AIError):
            provider.generate(system="s", context="c", schema=_SCHEMA_DE_PRUEBA)


# --- Kimi: secretos ---

def test_la_key_de_kimi_tambien_se_redacta(monkeypatch):
    """Las keys se descubren por convención (`*_API_KEY`), no por una lista.

    Este test existe porque el fallo contrario es invisible: añadir un
    proveedor y olvidarse de meter su key en la función de redacción no rompe
    nada, solo hace que la clave salga en claro en el primer error.
    """
    monkeypatch.setattr(Config, "SHOW_AI_ERROR_DETAIL", True)
    monkeypatch.setattr(Config, "KIMI_API_KEY", "sk-CLAVEDEKIMISUPERSECRETA123456")

    mensaje = str(KimiProvider._translate(_error_kimi(
        401, "invalid_authentication_error",
        "key sk-CLAVEDEKIMISUPERSECRETA123456 rechazada",
    )))
    assert "CLAVEDEKIMI" not in mensaje


def test_redact_tapa_una_key_estilo_moonshot_no_configurada(monkeypatch):
    """Aunque no sea la nuestra: si parece una key, se tapa."""
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(Config, "KIMI_API_KEY", "")
    assert "sk-" not in redact("error con sk-abcdefghijklmnopqrstuvwxyz123456")
