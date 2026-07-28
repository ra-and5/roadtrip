"""El chatbot: el mismo contexto del viaje, pero conversable.

Función de entrada: `responder(pregunta, contexto, historial, provider=None)`.

Este módulo es deliberadamente pequeño, y que lo sea es la prueba de que las
decisiones anteriores eran las buenas: el contexto ya existe
(`contexto.construir()`), su renderizado ya existe
(`ai_orchestrator.formatear_para_prompt()`) y hablar con un modelo ya existe
(`llm_providers`). Aquí solo se decide **qué se le manda y qué no**.

La regla que ordena el módulo, y que viene de una petición explícita del usuario
—*"no me interesa que acabe siendo carísimo hacer peticiones"*—:

    GUARDAR y ENVIAR son cosas distintas.

Se guarda la conversación entera, porque es texto en SQLite y no cuesta nada, y
porque poder releer dentro de un mes qué preguntaste en cada sitio es la mitad
del "cuaderno de a bordo". Se **envía** solo la ventana de los últimos turnos,
porque eso sí se paga en tokens en cada mensaje y crecería sin parar. Confundir
las dos cosas es lo que convierte un chatbot barato en uno que cuesta más cada
día que pasa, sin que nadie lo note hasta la factura.

Lo mismo aplica al contexto: entra **todo** lo que se puede recoger en directo
—ubicación, tiempo, luna, pasos, viaje— pero **resumido**. Los agregados los
calculan `metricas.py` y `viaje.py`; aquí no se vuelca ninguna tabla.

Dos cosas que este módulo NO hace, y las dos son decisiones:

**No cachea.** Al revés que las recomendaciones (decisión 6), donde dos
pulsaciones seguidas deben dar lo mismo. Aquí la clave tendría que incluir la
pregunta *y* el historial entero, así que casi nunca acertaría; y dos preguntas
iguales en momentos distintos merecen respuestas distintas, que es justo lo que
una conversación es.

**No reintenta ante un 429.** Sigue habiendo alguien esperando (decisión 12).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.modules import storage
from app.modules.ai_orchestrator import formatear_para_prompt
from app.modules.contexto import Contexto
from app.modules.llm_providers import AIError, LLMProvider, build_provider
from app.modules.location_context import Place

__all__ = [
    "AIError",
    "Respuesta",
    "VENTANA_HISTORIAL",
    "borrar_historial",
    "construir_prompt",
    "guardar",
    "historial",
    "responder",
]


# Cuántos mensajes del historial viajan con cada pregunta. Seis son tres turnos
# (pregunta y respuesta), y es LA constante que cuesta dinero en este módulo:
# todo lo demás se calcula una vez y ocupa lo que ocupa, pero esto se envía
# entero en cada mensaje y crecería sin parar si no estuviera acotado.
#
# Tres turnos son los que sostienen un "¿y mañana?" o un "¿y si llueve?", que es
# para lo que sirve el historial. Con cero, el chatbot es un buscador con otra
# cara; con cincuenta, cada mensaje arrastra la conversación entera del viaje.
VENTANA_HISTORIAL = 6

# Cuánto puede medir una pregunta. No es una restricción de producto sino de
# coste: el cuerpo entero de la petición ya lo limita `MAX_CONTENT_LENGTH`, pero
# eso permite megabytes, y un pegote de 200 KB en el prompt se paga en tokens.
MAX_PREGUNTA = 2000

_ROL_USUARIO = "usuario"
_ROL_ASISTENTE = "asistente"

# Salida estructurada con una sola clave. Podría haberse pedido texto plano,
# pero entonces cada proveedor devolvería sus propios adornos (markdown,
# preámbulos, bloques de código) y habría que limpiarlos aquí. El esquema es la
# misma garantía que en las recomendaciones (decisión 8), solo que más simple.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "respuesta": {
            "type": "string",
            "description": "La respuesta a la pregunta, en castellano.",
        }
    },
    "required": ["respuesta"],
}

_SYSTEM_PROMPT = """\
Eres el compañero de viaje de alguien que recorre el norte de España en un \
coche camperizado. Te habla desde el móvil, en marcha o parado en un camping.

Te doy el estado real del viaje ahora mismo: dónde está, qué hora es allí, qué \
tiempo hace, qué mar hay, qué luna habrá esta noche, cuánto ha andado hoy y por \
dónde ha pasado ya. Responde a lo que te pregunte usando ESO.

Cómo trabajas:

- **Responde a la pregunta que te hacen.** Si te piden una hora, das una hora. \
  No conviertas cada respuesta en una lista de cinco planes: para eso está el \
  botón de recomendaciones.
- **Si el contexto no trae el dato, dilo.** No supongas la temperatura, ni la \
  hora a la que abre un sitio, ni dónde estuvo ayer si no te lo he contado. \
  Decir "no lo sé" vale más que acertar por casualidad, porque va a tomar \
  decisiones con tu respuesta estando lejos de casa.
- **Los veredictos vienen calculados** (deportes de agua, caminar de noche). \
  Respétalos, no los recalcules ni los contradigas.
- **Si un dato viene marcado como SIMULADO, no lo presentes como cierto.**
- Las distancias que te doy son en línea recta. En el norte, con valles y \
  puertos, la carretera puede ser el doble.
- Si te pregunta por sus notas, cítalas por lo que dicen, no las inventes.

Tono: español de España, tuteo, directo. Frases cortas: esto se lee en la \
pantalla de un móvil, a veces con una mano. Nada de relleno de folleto \
turístico ni de "¡qué buena pregunta!". Si algo es del montón, dilo.

Extensión: lo que pida la pregunta. Dos frases si con dos basta.\
"""


@dataclass(frozen=True)
class Respuesta:
    """Lo que contesta el modelo, con la trazabilidad de quién lo dijo."""

    texto: str
    proveedor: str = ""
    modelo: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"texto": self.texto, "proveedor": self.proveedor, "modelo": self.modelo}


def ventana(mensajes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los últimos mensajes, que son los únicos que se envían.

    Función aparte y no un `[-N:]` escondido dentro del prompt: es la decisión
    que controla el coste de toda la fase, y merece un nombre y un test propio.
    """
    return mensajes[-VENTANA_HISTORIAL:]


# ---------------------------------------------------------------------------
# La conversación guardada
# ---------------------------------------------------------------------------
#
# Vive aquí y no en la vista porque `app.py` no debe saber que existe una tabla.
# Y se guarda ENTERA, aunque solo se envíe la ventana: son dos decisiones
# distintas y la primera no cuesta nada.

def guardar(rol: str, texto: str, lugar: Place | None = None) -> int:
    """Guarda un mensaje de la conversación y devuelve su id.

    La ubicación se guarda **con el mensaje** en vez de deducirse al leerlo: la
    gracia de releer esto dentro de un mes es saber dónde estabas cuando
    preguntaste, y la ubicación de mañana no sitúa la pregunta de hoy.
    """
    return storage.insert_chat_mensaje(
        {
            "rol": rol,
            "texto": texto,
            "creado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lat": lugar.lat if lugar else None,
            "lon": lugar.lon if lugar else None,
            "lugar": lugar.short_label() if lugar else None,
        }
    )


def historial(limite: int = 50) -> list[dict[str, Any]]:
    """La conversación guardada, en orden cronológico.

    El límite por defecto es mayor que `VENTANA_HISTORIAL` a propósito: esto es
    lo que se repinta en la pantalla al abrirla, y ahí leer treinta mensajes no
    cuesta nada. Lo que se le manda al modelo lo decide `ventana()`, y solo
    `ventana()`.
    """
    return storage.list_chat_mensajes(limite)


def borrar_historial() -> int:
    """Borra la conversación entera. Devuelve cuántos mensajes se borraron."""
    return storage.delete_chat_mensajes()


def _format_historial(historial: list[dict[str, Any]]) -> str:
    """La conversación reciente, en texto.

    Va dentro del bloque de contexto y no como turnos de verdad de la API. El
    motivo es el contrato de `LLMProvider.generate(system, context, schema)`,
    que es estrecho a propósito (decisión 10): añadirle un método `converse()`
    obligaría a implementarlo en los cuatro proveedores —uno de ellos,
    `OllamaProvider`, ni siquiera está escrito— para ganar, a esta escala,
    exactamente nada. Es la decisión 18 otra vez: no se añade maquinaria que no
    se paga a sí misma.

    Lo que sí se pierde y conviene saber: el proveedor no puede cachear el
    prefijo de la conversación como haría con turnos reales. Con tres turnos y
    un usuario, es irrelevante.
    """
    if not historial:
        return "(Es el primer mensaje de la conversación.)"

    lineas = []
    for mensaje in historial:
        quien = "ÉL" if mensaje.get("rol") == _ROL_USUARIO else "TÚ"
        lineas.append(f"{quien}: {mensaje.get('texto', '').strip()}")
    return "\n".join(lineas)


def construir_prompt(
    pregunta: str, contexto: Contexto, historial: list[dict[str, Any]]
) -> str:
    """El bloque de texto que lee el modelo. Función PURA.

    Imprímela para depurar el prompt sin gastar una sola llamada a la API, igual
    que `formatear_para_prompt()`. Reutiliza esa misma función en vez de armar
    su propio contexto: si el chatbot y el recomendador renderizaran cada uno el
    suyo, divergirían sin dar ningún error, que es exactamente lo que la
    decisión 32 existe para impedir.

    Los POIs van vacíos a propósito: son la fuente cara y poco fiable
    (Overpass, decisión 22 y 33), y una conversación no puede pararse treinta
    segundos a buscarlos. Los que estuvieran cacheados los pasa quien llama.
    """
    return f"""\
{formatear_para_prompt(contexto, [], tarea="Responde a lo que te pregunta.")}

### CONVERSACIÓN RECIENTE
{_format_historial(historial)}

### SU PREGUNTA AHORA
{pregunta.strip()}"""


def _extraer(bruto: str) -> str:
    """Saca la respuesta del JSON, tolerando que no lo sea.

    Un modelo con salida estructurada devuelve el objeto pedido; uno local o mal
    configurado puede devolver la frase a secas. En ese caso se usa el texto tal
    cual en vez de fallar: la respuesta es lo que el usuario quería, y tirarla
    por no venir envuelta sería perder algo útil por una formalidad.
    """
    try:
        datos = json.loads(bruto)
    except (json.JSONDecodeError, TypeError):
        return bruto.strip()

    if isinstance(datos, dict):
        texto = datos.get("respuesta")
        if isinstance(texto, str) and texto.strip():
            return texto.strip()
    return bruto.strip()


def responder(
    pregunta: str,
    contexto: Contexto,
    historial: list[dict[str, Any]] | None = None,
    *,
    provider: LLMProvider | None = None,
) -> Respuesta:
    """Contesta una pregunta sobre el viaje, aquí y ahora.

    Args:
        pregunta: lo que ha escrito el usuario. Ya validada por quien llama.
        contexto: el estado del viaje, de `contexto.construir()`. Conviene
            pedirlo con `incluir_historia=True`: sin eso el chatbot no sabe ni
            cuántos pasos lleva ni por dónde ha pasado.
        historial: la conversación completa. Aquí se recorta a la ventana; el
            recorte NO se le pide a quien llama, para que no haya dos sitios que
            decidan cuánto se paga.
        provider: se inyecta en los tests. En producción sale de `LLM_PROVIDER`.

    Raises:
        AIError: falta configuración o el proveedor falló. Es la única excepción
            que sale de aquí, venga del proveedor que venga.
    """
    provider = provider or build_provider()

    bruto = provider.generate(
        system=_SYSTEM_PROMPT,
        context=construir_prompt(pregunta, contexto, ventana(historial or [])),
        schema=_OUTPUT_SCHEMA,
    )

    return Respuesta(
        texto=_extraer(bruto), proveedor=provider.name, modelo=provider.model
    )
