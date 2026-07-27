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
    LLMProvider,
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

def test_registro_expone_los_tres_proveedores():
    assert set(PROVIDER_NAMES) == {"anthropic", "gemini", "ollama"}


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
    (404, "GEMINI_MODEL"),
])
def test_codigos_de_gemini_dan_mensajes_accionables(monkeypatch, code, esperado):
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "")
    assert esperado in str(GeminiProvider._translate(_gemini_error(code, "x")))


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
