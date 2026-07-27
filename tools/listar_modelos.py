"""Lista qué modelos de Gemini funcionan DE VERDAD con tu API key.

Uso:
    python tools/listar_modelos.py            # solo los que funcionan
    python tools/listar_modelos.py --todos    # también los que fallan y por qué

Por qué existe esta herramienta: la lista que devuelve la API (`models.list()`)
NO es la lista de modelos que puedes usar. Comprobado contra la API real:
`gemini-2.5-flash` aparece listado y sin embargo responde 404 con "no longer
available to new users", y varios modelos listados devuelven 429 porque su
cuota gratuita está agotada.

La única forma fiable de saber qué sirve es intentar generar con cada uno,
usando la MISMA configuración de salida estructurada que usa la app. Eso es lo
que hace este script.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Esquema mínimo, con la misma forma que el de la app (objeto + required +
# additionalProperties): un modelo puede generar texto y aun así no soportar
# salida estructurada, y eso es justo lo que necesitamos saber.
_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

# Modelos que no son de texto (audio, imagen, embeddings, robótica...). No
# tiene sentido probarlos y gastan cuota.
_NO_GENERATIVOS = (
    "embedding", "image", "tts", "live", "native-audio",
    "dialog", "robotics", "computer-use", "veo", "imagen",
)


def main() -> None:
    mostrar_todos = "--todos" in sys.argv

    from app.config import Config

    if not Config.GEMINI_API_KEY:
        print("Falta GEMINI_API_KEY en el .env.")
        print("Sácala en aistudio.google.com > Get API key > Create API key.")
        sys.exit(1)

    try:
        from google import genai
        from google.genai import errors, types
    except ImportError:
        print("Falta el paquete 'google-genai' (pip install google-genai).")
        sys.exit(1)

    client = genai.Client(
        api_key=Config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=60_000),  # OJO: milisegundos
    )

    print("Consultando modelos disponibles...\n")
    try:
        listados = [m.name.replace("models/", "") for m in client.models.list()]
    except errors.APIError as exc:
        print(f"No se pudo listar: {exc.code} — {exc.message}")
        sys.exit(1)

    candidatos = [
        m for m in listados
        if m.startswith("gemini") and not any(x in m for x in _NO_GENERATIVOS)
    ]
    print(f"{len(listados)} modelos listados, {len(candidatos)} candidatos de texto.")
    print("Probando cada uno con salida estructurada (esto tarda un poco)...\n")

    funcionan: list[str] = []
    fallan: list[tuple[str, str]] = []

    for modelo in candidatos:
        try:
            respuesta = client.models.generate_content(
                model=modelo,
                contents='Devuelve {"ok": true}',
                config=types.GenerateContentConfig(
                    system_instruction="Responde solo con JSON.",
                    response_mime_type="application/json",
                    response_json_schema=_SCHEMA,
                    max_output_tokens=200,
                ),
            )
            if respuesta.text:
                funcionan.append(modelo)
                print(f"  OK    {modelo}")
            else:
                fallan.append((modelo, "respuesta vacía"))
        except errors.APIError as exc:
            motivo = {
                429: "cuota gratuita agotada para este modelo",
                404: "no disponible para tu cuenta",
            }.get(exc.code, f"error {exc.code}")
            fallan.append((modelo, motivo))
            if mostrar_todos:
                print(f"  ---   {modelo}  ({motivo})")
        except Exception as exc:  # noqa: BLE001
            fallan.append((modelo, type(exc).__name__))

    print("\n" + "=" * 60)
    if funcionan:
        # Preferimos un modelo FIJADO y no un alias tipo "gemini-flash-latest":
        # al afinar un prompt necesitas reproducibilidad, y un alias cambia de
        # modelo bajo tus pies sin avisar.
        fijados = [m for m in funcionan if not m.endswith("-latest")]
        recomendado = (fijados or funcionan)[-1]
        print(f"Funcionan {len(funcionan)} de {len(candidatos)}.")
        print(f"\nPon esto en tu .env:\n\n    GEMINI_MODEL={recomendado}\n")
        if mostrar_todos:
            print("Todos los que funcionan:")
            for m in funcionan:
                print(f"  - {m}")
    else:
        print("Ningún modelo respondió correctamente.")
        print("Causas típicas: cuota diaria agotada, o key sin permisos.")
        for modelo, motivo in fallan[:5]:
            print(f"  {modelo}: {motivo}")
    print()


if __name__ == "__main__":
    main()
