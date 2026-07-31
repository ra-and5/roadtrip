"""Proveedores de LLM intercambiables detrás de una única interfaz.

Este es el ÚNICO módulo que sabe que existen Anthropic, Google o Moonshot.
Hacia fuera solo se exponen tres cosas: `LLMProvider`, `build_provider()` y
`AIError`.
Ni tipos, ni excepciones, ni formatos de respuesta de ningún proveedor cruzan
esta frontera.

El contrato es deliberadamente estrecho:

    generate(system=..., context=..., schema=...) -> str

Recibe el contexto ya construido (`ai_orchestrator.formatear_para_prompt()`, que sigue
siendo una función pura) y devuelve texto. Cada proveedor se encarga por dentro
de sus propias rarezas: cómo se llaman los mensajes, cómo se pide salida en
JSON, y cómo se traduce su jerarquía de errores a `AIError`.

Por qué `AIError` vive aquí y no en `ai_orchestrator`: los proveedores tienen
que lanzarla, y `ai_orchestrator` tiene que importar los proveedores. Si la
excepción viviera arriba tendríamos un import circular. `ai_orchestrator` la
reexporta, así que el resto de la app no se entera de este detalle.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from app.config import Config

# Un LLM tarda segundos, no milisegundos. El timeout de 10 s que usamos para el
# resto de APIs mataría la petición a mitad. Obligatorio en TODOS los
# proveedores: sin timeout, un modelo colgado bloquea un worker de Flask para
# siempre y la app entera deja de responder.
AI_TIMEOUT_SECONDS = 120.0

# Techo de tokens de salida. En modelos con razonamiento (Claude Opus 5) este
# límite cubre el razonamiento MÁS la respuesta, así que hay que dar margen
# para que el JSON no se corte a mitad.
MAX_OUTPUT_TOKENS = 8000


class AIError(Exception):
    """Único error que sale de este módulo. Nunca contiene la API key."""


class FallbackAIError(AIError):
    """Todos los proveedores configurados fallaron."""


# ---------------------------------------------------------------------------
# Redacción de secretos
# ---------------------------------------------------------------------------

# Formas de clave conocidas. Se aplican SIEMPRE, incluso en modo detalle: una
# key en un mensaje de error acaba en un log, en una captura de pantalla o en
# un issue de GitHub, y a partir de ahí está comprometida.
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),        # Anthropic (sk-ant-...), Moonshot/Kimi (sk-...)
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),       # Google
    re.compile(r"(?i)(api[-_]?key|key|token)=[^\s&\"']+"),  # ...&key=XXX en URLs
    # Cabecera Authorization: cubre el token de ingesta del iPhone (Fase 2d).
    # Ese token NO se puede descubrir por convención como las API keys, porque
    # en el servidor solo vive su HASH (INGEST_TOKEN_HASH): el secreto en claro
    # no está en `Config` y `_configured_keys()` no puede verlo. El patrón es la
    # única capa que queda, y por eso está aquí y no en el módulo de ingesta:
    # esta función es por donde pasa todo lo que se va a enseñar o registrar.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{8,}=*"),
)

# Longitud mínima de fragmento de clave que buscamos al redactar. Cubre el
# requisito de que no aparezca "ni completa ni parcial": si un mensaje trae
# solo un trozo de la key configurada, también se tapa.
_MIN_PARTIAL_KEY_LEN = 12


def _configured_keys() -> tuple[str, ...]:
    """Todas las API keys definidas en `Config`, descubiertas por convención.

    Se buscan por nombre (`*_API_KEY`) en vez de enumerarlas a mano. El motivo
    es concreto: una lista escrita a mano se queda corta en cuanto alguien
    añade un proveedor, y ese olvido NO da error — simplemente hace que la key
    nueva salga sin tapar en el primer mensaje de error del proveedor. Es
    exactamente la clase de fallo silencioso de la decisión 11, pero pagando
    con un secreto en vez de con una respuesta equivocada.
    """
    return tuple(
        value
        for name, value in vars(Config).items()
        if name.endswith("_API_KEY") and isinstance(value, str) and value
    )


def redact(text: str) -> str:
    """Elimina cualquier rastro de API key de un texto.

    Tres pasadas, de la más específica a la más genérica:
      1. Las claves configuradas, completas.
      2. Fragmentos de esas claves (>= 12 caracteres).
      3. Patrones de clave conocidos, por si aparece una que no tenemos.
    """
    if not text:
        return text

    for key in _configured_keys():
        if len(key) < _MIN_PARTIAL_KEY_LEN:
            continue
        text = text.replace(key, "[API_KEY_OCULTA]")
        # Fragmentos: recorremos ventanas de la clave de mayor a menor. Es
        # barato porque las claves son cortas y esto solo corre en la ruta de
        # error, nunca en la ruta normal.
        for size in range(len(key), _MIN_PARTIAL_KEY_LEN - 1, -1):
            for start in range(0, len(key) - size + 1):
                fragment = key[start : start + size]
                if fragment in text:
                    text = text.replace(fragment, "[API_KEY_OCULTA]")

    for pattern in _KEY_PATTERNS:
        text = pattern.sub("[API_KEY_OCULTA]", text)

    return text


def _safe_detail(message: str) -> str:
    """Devuelve el detalle del proveedor solo si el interruptor está activado.

    En ambos modos el texto pasa por `redact()`: el interruptor decide cuánto
    detalle se enseña, NUNCA si se protege la clave.
    """
    message = redact(message)
    if Config.SHOW_AI_ERROR_DETAIL:
        return message
    # El detalle completo sigue yendo al log y al diagnóstico; aquí solo
    # decidimos qué ve el usuario en la interfaz.
    return "detalle oculto (activa SHOW_AI_ERROR_DETAIL para verlo)"


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Contrato que cumple todo proveedor.

    `name` y `model` son públicos porque forman parte de la clave de caché de
    las recomendaciones: sin ellos, una respuesta generada con Gemini se
    serviría más tarde como si fuera de Claude.
    """

    name: str = "abstracto"

    def __init__(self, model: str) -> None:
        self.model = model
        # Cliente cacheado. Es obligatorio guardar una referencia fuerte, no
        # crearlo como temporal dentro de la llamada: los SDKs cierran su
        # conexión HTTP cuando el objeto cliente se recolecta, y un temporal
        # puede morir a mitad de la petición ("Cannot send a request, as the
        # client has been closed"). Además evita reconstruirlo en cada llamada.
        self._cached_client: Any = None
        # Tokens de la última llamada, si el proveedor los reporta. NO forma
        # parte del contrato: es información para `tools/diagnostico.py`, que
        # con un proveedor de pago es la diferencia entre saber lo que cuesta
        # cada recomendación y descubrirlo cuando se acaba el saldo. Quien lo
        # lea debe tolerar `None`.
        self.last_usage: dict[str, Any] | None = None

    @abstractmethod
    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        """Genera una respuesta en JSON conforme a `schema`.

        Args:
            system: instrucciones de sistema. Compartidas entre proveedores:
                las define `ai_orchestrator`, no cada proveedor.
            context: el bloque de contexto de `formatear_para_prompt()`.
            schema: JSON Schema que debe cumplir la respuesta.

        Returns:
            El texto de la respuesta, que debe ser JSON válido.

        Raises:
            AIError: cualquier fallo, ya traducido y sin secretos.
        """

    def describe(self) -> str:
        return f"{self.name}/{self.model}"


def _es_fallo_recuperable(exc: AIError) -> bool:
    """¿Tiene sentido probar otro proveedor?

    No se reintenta el MISMO 429: eso seguiría chocando contra la misma cuota.
    Pero si hay otro proveedor configurado, sí tiene sentido cambiar de motor
    dentro de la misma petición. No se consideran recuperables los 400, keys
    malas o modelos inexistentes, porque otro proveedor escondería un bug de
    configuración que hay que corregir.
    """
    texto = str(exc).lower()
    return any(
        pista in texto
        for pista in (
            "429",
            "límite",
            "limite",
            "cuota",
            "saturad",
            "timeout",
            "tardó",
            "tardo",
            "sin conexión",
            "sin conexion",
            "api connection",
        )
    )


class FallbackProvider(LLMProvider):
    """Proveedor compuesto: activo primero, alternativas configuradas después."""

    name = "fallback"

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise AIError("No hay ningún proveedor de IA configurado.")
        self.providers = providers
        self._last_provider: LLMProvider | None = None
        super().__init__(">".join(p.describe() for p in providers))

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        fallos: list[str] = []
        for provider in self.providers:
            try:
                texto = provider.generate(system=system, context=context, schema=schema)
                self._last_provider = provider
                # Desde fuera tiene que verse quién contestó de verdad: el
                # cacheo y la trazabilidad usan `name/model`.
                self.name = provider.name
                self.model = provider.model
                self.last_usage = provider.last_usage
                return texto
            except AIError as exc:
                fallos.append(f"{provider.describe()}: {exc}")
                if not _es_fallo_recuperable(exc):
                    raise

        raise FallbackAIError(
            "Todos los proveedores de IA configurados fallaron: "
            + " | ".join(fallos)
        )


# ---------------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------------

_VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class AnthropicProvider(LLMProvider):
    """Claude, vía la API de Anthropic."""

    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or Config.ANTHROPIC_MODEL)
        if not Config.ANTHROPIC_API_KEY:
            raise AIError(
                "Falta ANTHROPIC_API_KEY. Ponla en el .env (local) o en las "
                "variables de entorno de PythonAnywhere (producción). "
                "Alternativa gratuita: LLM_PROVIDER=gemini."
            )
        self._effort = self._validated_effort()

    @staticmethod
    def _validated_effort() -> str:
        """Valida ANTHROPIC_EFFORT antes de llamar.

        Un valor inválido (un typo, un comentario pegado en el .env) produciría
        un 400 críptico de la API. Detectarlo aquí da un error que dice qué
        variable corregir.
        """
        effort = (Config.ANTHROPIC_EFFORT or "").strip().lower()
        if effort not in _VALID_EFFORTS:
            raise AIError(
                f"ANTHROPIC_EFFORT='{Config.ANTHROPIC_EFFORT}' no es válido. "
                f"Acepta: {', '.join(_VALID_EFFORTS)}. Revisa el .env (ojo con "
                f"espacios o comentarios en la misma línea)."
            )
        return effort

    @staticmethod
    def _error_message(exc: Exception) -> str:
        """Saca el mensaje real de la API.

        El SDK pone el detalle útil en `exc.body["error"]["message"]`; el
        atributo `exc.message` solo trae "Error code: 400".
        """
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        return getattr(exc, "message", None) or str(exc)

    def _client(self) -> Any:
        if self._cached_client is not None:
            return self._cached_client
        try:
            import anthropic
        except ImportError as exc:
            raise AIError("Falta el paquete 'anthropic' (pip install anthropic).") from exc
        self._cached_client = anthropic.Anthropic(
            api_key=Config.ANTHROPIC_API_KEY,
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=2,  # el SDK reintenta solo 5xx y errores de red
        )
        return self._cached_client

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Saca el texto comprobando antes si hubo rechazo.

        `content` puede traer bloques de varios tipos (thinking, text,
        fallback...). Indexar `content[0].text` se rompe en cuanto aparece uno
        que no es texto.
        """
        if getattr(response, "stop_reason", None) == "refusal":
            raise AIError("El modelo declinó responder a esta petición.")
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                return block.text
        raise AIError("El modelo no devolvió ningún texto.")

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        import anthropic

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": context}],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }

        try:
            try:
                # Endpoint beta con fallbacks: si los clasificadores de
                # seguridad declinasen, la API reintenta en otro modelo dentro
                # de la misma llamada en vez de devolvernos un fallo.
                client = self._client()
                response = client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **params,
                )
            except anthropic.BadRequestError as beta_exc:
                # La beta rechazó la petición: reintentamos por el endpoint
                # estable. Guardamos su motivo: si el estable también falla,
                # saber si ambos se quejan de LO MISMO (parámetro nuestro
                # inválido) o de cosas distintas (beta retirada) es media
                # depuración hecha.
                beta_reason = self._error_message(beta_exc)
                try:
                    response = client.messages.create(**params)
                except anthropic.BadRequestError as exc:
                    reason = self._error_message(exc)
                    detail = (
                        reason if reason == beta_reason
                        else f"{reason} (endpoint beta: {beta_reason})"
                    )
                    raise AIError(f"La API rechazó la petición (400): {_safe_detail(detail)}") from exc

        except anthropic.AuthenticationError as exc:
            raise AIError("La API key de Anthropic no es válida.") from exc
        except anthropic.RateLimitError as exc:
            raise AIError(
                "Límite de peticiones de Anthropic alcanzado. "
                f"Prueba en un minuto. {_safe_detail(self._error_message(exc))}"
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise AIError(f"El modelo tardó más de {AI_TIMEOUT_SECONDS:.0f} s.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Sin conexión con la API de Anthropic.") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(
                f"La API de Anthropic devolvió un error ({exc.status_code}): "
                f"{_safe_detail(self._error_message(exc))}"
            ) from exc

        return self._extract_text(response)


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    """Gemini, vía Google AI Studio (capa gratuita, sin tarjeta)."""

    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or Config.GEMINI_MODEL)
        if not Config.GEMINI_API_KEY:
            raise AIError(
                "Falta GEMINI_API_KEY. Sácala en aistudio.google.com "
                "(Get API key > Create API key) y ponla en el .env."
            )

    def _client(self) -> Any:
        if self._cached_client is not None:
            return self._cached_client
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise AIError("Falta el paquete 'google-genai' (pip install google-genai).") from exc
        self._cached_client = genai.Client(
            api_key=Config.GEMINI_API_KEY,
            # OJO: HttpOptions.timeout va en MILISEGUNDOS, no en segundos.
            # Pasar 120 aquí serían 120 ms y todas las llamadas fallarían.
            http_options=types.HttpOptions(timeout=int(AI_TIMEOUT_SECONDS * 1000)),
        )
        return self._cached_client

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        from google.genai import errors, types

        try:
            response = self._client().models.generate_content(
                model=self.model,
                contents=context,
                config=types.GenerateContentConfig(
                    # El prompt de sistema llega desde ai_orchestrator: es
                    # compartido, no una copia de este proveedor.
                    system_instruction=system,
                    # `response_json_schema` acepta JSON Schema estándar.
                    # Verificado contra el SDK: soporta type, properties,
                    # required, enum, items y additionalProperties, así que
                    # nuestro esquema vale tal cual, sin adaptarlo.
                    # Exige response_mime_type y excluye response_schema.
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except errors.APIError as exc:
            raise self._translate(exc) from exc
        except Exception as exc:  # noqa: BLE001 - nada del SDK debe escapar
            raise AIError(f"Fallo llamando a Gemini: {_safe_detail(str(exc))}") from exc

        text = getattr(response, "text", None)
        if not text:
            # Gemini devuelve 200 con texto vacío cuando corta por filtros de
            # seguridad o por límite de tokens. No es una excepción del SDK:
            # hay que comprobarlo a mano, igual que con la API marina.
            raise AIError(
                "Gemini devolvió una respuesta vacía (posible filtro de "
                "contenido o límite de tokens alcanzado)."
            )
        return text

    @staticmethod
    def _translate(exc: Any) -> AIError:
        """Traduce un error de Gemini a AIError, sin filtrar secretos."""
        code = getattr(exc, "code", None)
        detail = _safe_detail(str(getattr(exc, "message", "") or exc))

        if code == 429:
            # Condición esperada en capa gratuita, no un fallo del sistema.
            # La app degrada igual que con cualquier otra fuente caída.
            return AIError(
                "Límite de la capa gratuita de Gemini alcanzado (429). "
                f"Espera un minuto y vuelve a pulsar. {detail}"
            )
        if code in (401, 403):
            return AIError("La API key de Gemini no es válida o no tiene permisos.")
        if code == 404:
            # Incluimos el detalle: un 404 aquí puede ser "el modelo no existe"
            # o "ya no se sirve a cuentas nuevas", que se arreglan distinto.
            # Culpar al modelo sin enseñar el motivo obliga a adivinar.
            return AIError(
                f"El modelo '{Config.GEMINI_MODEL}' no está disponible para tu key. "
                f"Lista los válidos con: python tools/listar_modelos.py. {detail}"
            )
        return AIError(f"La API de Gemini devolvió un error ({code}): {detail}")


# ---------------------------------------------------------------------------
# Kimi (Moonshot AI)
# ---------------------------------------------------------------------------

# Valores que acepta `reasoning_effort` en kimi-k3. NO coinciden con los de
# Anthropic (que además tiene "medium" y "xhigh"): son dos escalas distintas de
# dos proveedores distintos, y mezclarlas da un 400.
_KIMI_EFFORTS = ("low", "high", "max")

# Familias de modelo y cómo se les pide que piensen. Es el detalle más
# resbaladizo de esta API: el mando NO es el mismo en todos los modelos, y
# mandarle a uno el del otro devuelve un 400.
#   - kimi-k3          -> razona SIEMPRE; se regula con `reasoning_effort`.
#   - kimi-k2.6        -> `thinking` opcional; lo apagamos, porque el
#                         razonamiento se factura como salida (lo más caro) y
#                         aquí no aporta.
#   - kimi-k2.7-code   -> `thinking` está fijo en "enabled" y no se puede
#                         desactivar: mandar el campo devuelve error. Por eso
#                         para esta familia no se manda nada.
_KIMI_THINKING_OFF = {"type": "disabled"}


class KimiProvider(LLMProvider):
    """Kimi (Moonshot AI), vía su API compatible con OpenAI.

    Se habla con la API por HTTP con `requests`, que ya es una dependencia del
    proyecto, en vez de instalar el SDK de OpenAI. Con una sola llamada
    (`POST /chat/completions`) el SDK no ahorraría código —solo lo escondería—
    y sí añadiría un paquete que mantener y que ocupa cuota de disco en
    PythonAnywhere. La contrapartida es que aquí se ven los códigos HTTP a
    pelo; a cambio, cuando algo falla se ve exactamente qué se envió.

    No hay capa gratuita: la key se activa recargando 1 $. Ese es el motivo de
    que este proveedor informe de tokens (`last_usage`) y de que distinga un
    429 por saldo agotado de un 429 por ritmo de peticiones, que se arreglan de
    formas opuestas.
    """

    name = "kimi"

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or Config.KIMI_MODEL)
        if not Config.KIMI_API_KEY:
            raise AIError(
                "Falta KIMI_API_KEY. Sácala en platform.kimi.ai > Console > "
                "API Keys (requiere una recarga mínima de 1 $) y ponla en el "
                ".env. Alternativa gratuita: LLM_PROVIDER=gemini."
            )
        self._effort = self._validated_effort()

    @staticmethod
    def _validated_effort() -> str:
        """Valida KIMI_REASONING_EFFORT antes de llamar.

        Mismo criterio que en Anthropic: un typo debe dar un error que nombre
        la variable, no un 400 críptico de la API a mitad de una petición.
        """
        effort = (Config.KIMI_REASONING_EFFORT or "").strip().lower()
        if effort not in _KIMI_EFFORTS:
            raise AIError(
                f"KIMI_REASONING_EFFORT='{Config.KIMI_REASONING_EFFORT}' no es válido. "
                f"Acepta: {', '.join(_KIMI_EFFORTS)}. Ojo: NO son los mismos "
                f"valores que ANTHROPIC_EFFORT."
            )
        return effort

    def _thinking_params(self) -> dict[str, Any]:
        """El mando de razonamiento que entiende ESTE modelo (ver _KIMI_*)."""
        if self.model.startswith("kimi-k3"):
            return {"reasoning_effort": self._effort}
        if self.model.startswith("kimi-k2.7-code"):
            return {}
        return {"thinking": _KIMI_THINKING_OFF}

    def _client(self) -> Any:
        if self._cached_client is not None:
            return self._cached_client
        import requests

        # Una Session reutiliza la conexión TCP y el handshake TLS entre
        # llamadas. Con un solo usuario el ahorro es modesto, pero es gratis.
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {Config.KIMI_API_KEY}",
            "Content-Type": "application/json",
        })
        self._cached_client = session
        return self._cached_client

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        import requests

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
            # `strict: true` activa decodificación restringida: el modelo no
            # puede emitir un token que rompa el esquema. Sin él la API solo
            # promete "un JSON válido", que es justo la promesa que no sirve
            # (decisión 8). `name` es únicamente una etiqueta para sus logs.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "recomendacion_viaje",
                    "strict": True,
                    "schema": schema,
                },
            },
            # `max_tokens` está deprecado en esta API en favor de este campo.
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            **self._thinking_params(),
        }

        try:
            response = self._client().post(
                f"{Config.KIMI_BASE_URL}/chat/completions",
                json=payload,
                timeout=AI_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            raise AIError(f"Kimi tardó más de {AI_TIMEOUT_SECONDS:.0f} s.") from exc
        except requests.RequestException as exc:
            raise AIError(f"Sin conexión con la API de Kimi: {_safe_detail(str(exc))}") from exc

        if response.status_code != 200:
            raise self._translate(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise AIError("Kimi devolvió algo que no era JSON.") from exc

        return self._extract_text(data)

    def _extract_text(self, data: dict[str, Any]) -> str:
        """Saca el texto de la respuesta y guarda el consumo de tokens."""
        self.last_usage = data.get("usage") if isinstance(data.get("usage"), dict) else None

        choices = data.get("choices") or []
        if not choices:
            raise AIError("Kimi devolvió una respuesta sin contenido.")
        choice = choices[0]

        # Truncado por límite de tokens: la API responde 200 y el JSON llega a
        # medias, así que el fallo aparecería tres capas más arriba como "no
        # era JSON válido". Igual que la API marina de Open-Meteo (decisión 5),
        # un 200 no significa que la respuesta sirva.
        if choice.get("finish_reason") == "length":
            raise AIError(
                f"Kimi cortó la respuesta al llegar al límite de "
                f"{MAX_OUTPUT_TOKENS} tokens y el JSON quedó incompleto. "
                f"Con un modelo que razona, el razonamiento también consume ese "
                f"límite: baja KIMI_REASONING_EFFORT o sube MAX_OUTPUT_TOKENS."
            )

        message = choice.get("message") or {}
        # `reasoning_content` viene aparte y NO es la respuesta: el JSON útil
        # está siempre en `content`. Mezclarlos rompería el parseo.
        content = message.get("content")
        if not content or not content.strip():
            raise AIError(
                "Kimi devolvió una respuesta vacía "
                f"(finish_reason={choice.get('finish_reason', 'desconocido')})."
            )
        return content

    @staticmethod
    def _translate(response: Any) -> AIError:
        """Traduce una respuesta HTTP de error a AIError, sin filtrar secretos.

        La API devuelve `{"error": {"type": ..., "message": ...}}`. El `type`
        importa tanto como el código: un 429 puede ser "vas demasiado rápido",
        "el servidor está saturado" o "te has quedado sin saldo", y las tres se
        arreglan de forma distinta. Verificado contra la API real: el cuerpo de
        error NO siempre trae el campo `code`, así que nos guiamos por el
        estado HTTP y por `type`.
        """
        status = getattr(response, "status_code", 0)
        try:
            error = (response.json() or {}).get("error") or {}
        except ValueError:
            error = {}
        tipo = str(error.get("type", ""))
        mensaje = str(error.get("message", "") or getattr(response, "text", "") or "")
        detalle = _safe_detail(mensaje)

        if status == 401:
            return AIError(
                "La API key de Kimi no es válida. Comprueba también que "
                "KIMI_BASE_URL corresponde a la plataforma donde creaste la "
                "key: las keys no son intercambiables entre las plataformas "
                "regionales de Moonshot, y usar la que no toca da este mismo 401."
            )
        if status == 403:
            return AIError(f"Tu cuenta de Kimi no tiene permiso para esto. {detalle}")
        if status == 404:
            return AIError(
                f"El modelo '{Config.KIMI_MODEL}' no existe o tu cuenta no tiene "
                f"acceso. Modelos habituales: kimi-k3, kimi-k2.6, kimi-k2.7-code. "
                f"{detalle}"
            )
        if status == 429:
            if tipo == "exceeded_current_quota_error":
                # Esto NO es un límite de ritmo: es dinero. Esperar no lo
                # arregla, y presentarlo como "prueba en un minuto" mandaría al
                # usuario a reintentar en bucle contra un muro.
                return AIError(
                    "Te has quedado sin saldo en Kimi (o la cuenta está "
                    "desactivada). Esperar no lo arregla: recarga en "
                    f"platform.kimi.ai, o cambia a LLM_PROVIDER=gemini. {detalle}"
                )
            if tipo == "engine_overloaded_error":
                return AIError(
                    "Los servidores de Kimi están saturados ahora mismo. "
                    f"No es culpa de tu cuenta ni del saldo. {detalle}"
                )
            # Ritmo de peticiones. Con el nivel de recarga mínimo (1 $) el
            # límite es de 3 peticiones por minuto, que se alcanza pulsando el
            # botón varias veces seguidas.
            return AIError(
                "Límite de peticiones por minuto de Kimi alcanzado (429). "
                f"Espera un minuto y vuelve a pulsar. {detalle}"
            )
        if status == 400 and tipo == "content_filter":
            return AIError(f"Kimi rechazó la petición por su filtro de contenido. {detalle}")
        if status == 400:
            return AIError(f"Kimi rechazó la petición (400): {detalle}")
        return AIError(f"La API de Kimi devolvió un error ({status}): {detalle}")

    def balance(self) -> str:
        """Saldo disponible, en texto listo para imprimir.

        Fuera del contrato de `LLMProvider` a propósito: solo tiene sentido en
        un proveedor de prepago, y lo usa `tools/diagnostico.py`. Con 1 $ de
        saldo, "cuánto me queda" es una pregunta que se hace de verdad.
        """
        import requests

        try:
            response = self._client().get(
                f"{Config.KIMI_BASE_URL}/users/me/balance",
                timeout=Config.HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AIError(f"No se pudo consultar el saldo: {_safe_detail(str(exc))}") from exc

        if response.status_code != 200:
            raise self._translate(response)
        datos = (response.json() or {}).get("data") or {}
        return (
            f"{datos.get('available_balance', '?')} disponible "
            f"({datos.get('cash_balance', '?')} recargado + "
            f"{datos.get('voucher_balance', '?')} en vales)"
        )


# ---------------------------------------------------------------------------
# Ollama (local) -- estructura preparada, sin implementar
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Modelo local vía Ollama. AÚN NO IMPLEMENTADO.

    Está registrado a propósito para que `LLM_PROVIDER=ollama` dé un error
    que explique qué falta, en vez de un genérico "proveedor desconocido".

    Para implementarlo (unas 30 líneas): POST a
    `http://localhost:11434/api/chat` con `{"model": ..., "messages":
    [{"role": "system", ...}, {"role": "user", ...}], "format": <schema>,
    "stream": false}`. Ollama acepta un JSON Schema en `format` desde la
    versión 0.5, así que la salida estructurada funciona igual que en los
    otros dos. La respuesta viene en `message.content`.
    """

    name = "ollama"

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or "llama3.1")
        raise AIError(
            "El proveedor 'ollama' todavía no está implementado. "
            "Usa LLM_PROVIDER=gemini (gratis) o LLM_PROVIDER=anthropic. "
            "Ver OllamaProvider en app/modules/llm_providers.py para el diseño."
        )

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        raise AIError("El proveedor 'ollama' todavía no está implementado.")


# ---------------------------------------------------------------------------
# Registro y selección
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "kimi": KimiProvider,
    "ollama": OllamaProvider,
}

PROVIDER_NAMES: tuple[str, ...] = tuple(_REGISTRY)


def _tiene_config(nombre: str) -> bool:
    if nombre == "anthropic":
        return bool(Config.ANTHROPIC_API_KEY)
    if nombre == "gemini":
        return bool(Config.GEMINI_API_KEY)
    if nombre == "kimi":
        return bool(Config.KIMI_API_KEY)
    return False


def _orden_fallback(activo: str) -> list[str]:
    orden = [activo, "anthropic", "kimi", "gemini"]
    salida: list[str] = []
    for nombre in orden:
        if nombre in salida:
            continue
        if nombre not in _REGISTRY or nombre == "ollama":
            continue
        if _tiene_config(nombre):
            salida.append(nombre)
    return salida


def build_provider(name: str | None = None) -> LLMProvider:
    """Construye el proveedor pedido, o el configurado en LLM_PROVIDER.

    Raises:
        AIError: el nombre no está registrado, o al proveedor le falta su
            configuración (típicamente la API key).
    """
    requested = (name or Config.LLM_PROVIDER or "").strip().lower()
    provider_cls = _REGISTRY.get(requested)
    if provider_cls is None:
        raise AIError(
            f"LLM_PROVIDER='{requested}' no es un proveedor conocido. "
            f"Opciones: {', '.join(PROVIDER_NAMES)}."
        )
    if name is None:
        nombres = _orden_fallback(requested)
        if len(nombres) > 1:
            return FallbackProvider([_REGISTRY[n]() for n in nombres])
    return provider_cls()
