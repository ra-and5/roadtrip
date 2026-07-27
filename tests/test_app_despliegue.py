"""Tests de las dos cosas que se comprueban al desplegar: /healthz y la cookie.

Sin red y sin API keys, como el resto de la suite: `build_provider()` valida
nombre y key sin llamar a nadie, que es justo por lo que /healthz lo usa.

Lo que se protege aquí:
  - que /healthz habla del proveedor ACTIVO y no de una API key concreta,
  - que /healthz no revela cuál es el proveedor (es un endpoint público),
  - que la cookie de sesión sale marcada Secure salvo que se desactive a mano.

Nota sobre cómo se cambia la configuración aquí, porque tiene trampa. `Config`
lee el entorno **al importar el módulo**, y la tentación es reimportarlo para
probar otro valor. No se hace, y no por estilo: `importlib.reload` sustituye la
clase `Config` por una nueva, mientras que los módulos que ya la habían
importado (`storage`, `llm_providers`) se quedan con la vieja. A partir de ahí
el test parchea un objeto y el código de producción lee de otro. Se descubrió
porque `storage` acabó escribiendo en la base de datos REAL en mitad de la
suite, y solo fallaba según el orden de los archivos.

Así que:

  - lo que se consulta en tiempo de ejecución (keys, proveedor) se parchea
    sobre el objeto `Config` que tiene importado el módulo bajo prueba;
  - lo que se lee al arrancar se prueba llamando a `_env_bool` directamente,
    que es justo para lo que se extrajo.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from app.app import app as flask_app
from app.config import _env_bool
from app.modules import llm_providers

_VAR_COOKIE = "SESSION_COOKIE_SECURE"


@pytest.fixture
def cliente() -> Iterator[Any]:
    flask_app.config["TESTING"] = True
    yield flask_app.test_client()


@pytest.fixture
def proveedor(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Configura el proveedor activo tal y como lo ve `build_provider()`."""

    def _configurar(*, activo: str, gemini: str = "", anthropic: str = "") -> None:
        # Se parchea `llm_providers.Config`, no `app.config.Config`: es el
        # mismo objeto hoy, pero nombrarlo así deja escrito de quién depende
        # esta prueba y no se rompe en silencio si mañana cambia la importación.
        monkeypatch.setattr(llm_providers.Config, "LLM_PROVIDER", activo)
        monkeypatch.setattr(llm_providers.Config, "GEMINI_API_KEY", gemini)
        monkeypatch.setattr(llm_providers.Config, "ANTHROPIC_API_KEY", anthropic)

    return _configurar


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

def test_healthz_mira_el_proveedor_activo_no_la_key_de_anthropic(
    cliente: Any, proveedor: Any
) -> None:
    """El caso real del despliegue: servidor con Gemini y sin key de Anthropic.

    Es el bug que tenía este endpoint: informaba `ia_configurada: false` en un
    despliegue perfectamente sano, porque miraba `ANTHROPIC_API_KEY`.
    """
    proveedor(activo="gemini", gemini="key-de-mentira", anthropic="")

    assert cliente.get("/healthz").get_json()["ia_configurada"] is True


def test_healthz_detecta_que_falta_la_key_del_proveedor_activo(
    cliente: Any, proveedor: Any
) -> None:
    proveedor(activo="gemini", gemini="", anthropic="key-de-mentira")

    # Tener la key del OTRO proveedor no cuenta: no es el que va a responder.
    assert cliente.get("/healthz").get_json()["ia_configurada"] is False


def test_healthz_detecta_un_proveedor_mal_escrito(cliente: Any, proveedor: Any) -> None:
    proveedor(activo="gemnini", gemini="key-de-mentira")

    respuesta = cliente.get("/healthz")

    # Sigue respondiendo 200: la app arranca, lo que falla es la IA. Es la
    # degradación en cascada de la decisión 9, vista desde el health check.
    assert respuesta.status_code == 200
    assert respuesta.get_json()["ia_configurada"] is False


def test_healthz_no_necesita_login(cliente: Any, proveedor: Any) -> None:
    """Si pidiera sesión no serviría para comprobar un despliegue desde curl."""
    proveedor(activo="gemini", gemini="key-de-mentira")

    assert cliente.get("/healthz").status_code == 200


def test_healthz_no_filtra_detalles_de_infraestructura(
    cliente: Any, proveedor: Any
) -> None:
    """/healthz no lleva autenticación: no puede contar cómo está montado esto."""
    proveedor(activo="gemini", gemini="key-de-mentira")

    cuerpo = cliente.get("/healthz").get_json()

    assert set(cuerpo) == {"status", "ia_configurada"}
    texto = str(cuerpo).lower()
    for filtracion in ("gemini", "anthropic", "claude", "key-de-mentira"):
        assert filtracion not in texto


# ---------------------------------------------------------------------------
# Cookie de sesión
# ---------------------------------------------------------------------------

def _leer(monkeypatch: pytest.MonkeyPatch, valor: str | None, *, default: bool) -> bool:
    """Parsea `valor` como si viniera del entorno, sin tocar la app.

    Se prueba `_env_bool` directamente en vez de reimportar `app.config`.
    Recargar el módulo sustituye la clase `Config` por una nueva, y los módulos
    que ya la tenían importada (`storage`, `llm_providers`) se quedan con la
    vieja: entonces el `monkeypatch` de un test aterriza en un objeto y el
    código de producción lee de otro. Se descubrió porque `storage` acabó
    escribiendo en la base de datos REAL en mitad de la suite.
    """
    if valor is None:
        monkeypatch.delenv(_VAR_COOKIE, raising=False)
    else:
        monkeypatch.setenv(_VAR_COOKIE, valor)
    return _env_bool(_VAR_COOKIE, default=default)


def test_la_cookie_de_sesion_es_secure_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _leer(monkeypatch, None, default=True) is True


def test_una_variable_vacia_no_apaga_el_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SESSION_COOKIE_SECURE=` en el .env significa "por defecto", no "false"."""
    assert _leer(monkeypatch, "", default=True) is True


def test_un_valor_ilegible_deja_la_cookie_en_el_lado_seguro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una errata al escribir el flag no puede desproteger la cookie."""
    assert _leer(monkeypatch, "flase", default=True) is True


def test_la_cookie_se_puede_desactivar_a_proposito(monkeypatch: pytest.MonkeyPatch) -> None:
    """El escape hatch para probar por http desde otro aparato de la red local."""
    for apagado in ("0", "false", "no", "off", "OFF"):
        assert _leer(monkeypatch, apagado, default=True) is False, apagado


def test_un_flag_apagado_por_defecto_se_equivoca_al_reves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso de SHOW_AI_ERROR_DETAIL, que comparte este parseo.

    Aquí lo seguro es lo contrario: una errata NO debe encender un flag que
    expone detalles del proveedor en la interfaz.
    """
    assert _leer(monkeypatch, "sí", default=False) is True
    assert _leer(monkeypatch, "puede", default=False) is False
    assert _leer(monkeypatch, None, default=False) is False


def test_la_app_aplica_los_tres_flags_de_la_cookie() -> None:
    """El parseo no sirve de nada si luego no se le pasa a Flask."""
    from app.config import Config

    assert flask_app.config["SESSION_COOKIE_SECURE"] is Config.SESSION_COOKIE_SECURE
    assert flask_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert flask_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
