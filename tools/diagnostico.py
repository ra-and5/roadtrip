"""Comprueba una por una todas las dependencias externas de la app.

Uso:
    python tools/diagnostico.py                  # usa Cudillero por defecto
    python tools/diagnostico.py 43.36 -8.41      # unas coordenadas concretas
    python tools/diagnostico.py --todos          # prueba TODOS los proveedores
    python tools/diagnostico.py -v               # con traza completa

Para cuando algo no funciona y estás a 800 km de casa: te dice QUÉ falla, no
solo que "hay un error". Cada línea es independiente, así que sabes
exactamente qué pieza está rota y cuál sigue en pie.

El detalle de los errores de IA se muestra aquí SIEMPRE, tenga el valor que
tenga SHOW_AI_ERROR_DETAIL: esa variable controla lo que ve el usuario en la
interfaz, no lo que ves tú depurando.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

# Este script vive en tools/, así que Python pone tools/ en el path, no la
# raíz del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(nombre: str, fn) -> bool:
    """Ejecuta una comprobación y la reporta sin dejar que reviente el script."""
    print(f"  {nombre:.<34}", end=" ", flush=True)
    t0 = time.time()
    try:
        detalle = fn()
    except Exception as exc:  # noqa: BLE001 - el objetivo es reportar cualquier fallo
        print(f"FALLO  ({time.time() - t0:.1f}s)")
        print(f"     {type(exc).__name__}: {exc}")
        if "-v" in sys.argv:
            traceback.print_exc()
        return False
    print(f"OK     ({time.time() - t0:.1f}s)  {detalle}")
    return True


def main() -> None:
    # OJO: no se puede filtrar "lo que empieza por '-'" como si fueran flags.
    # Todo el norte de España tiene longitud NEGATIVA (-4.29, -6.14...), así
    # que ese filtro se comía justo el argumento que nos interesa. Por eso los
    # flags se listan explícitamente en vez de detectarse por el guion.
    _FLAGS = {"-v", "--todos"}
    todos = "--todos" in sys.argv
    args = [a for a in sys.argv[1:] if a not in _FLAGS]
    lat, lon = (float(args[0]), float(args[1])) if len(args) >= 2 else (43.5622, -6.1456)

    print(f"\nDiagnóstico para {lat}, {lon}\n" + "=" * 56)

    # 1. Configuración: si esto falla, nada más va a funcionar.
    print("\nCONFIGURACIÓN")
    try:
        from app.config import Config
        print(f"  {'proveedor activo':.<34} OK     LLM_PROVIDER={Config.LLM_PROVIDER}")
        print(f"  {'ANTHROPIC_API_KEY':.<34} "
              f"{'definida' if Config.ANTHROPIC_API_KEY else 'ausente'}"
              f"   (modelo={Config.ANTHROPIC_MODEL}, effort={Config.ANTHROPIC_EFFORT})")
        print(f"  {'GEMINI_API_KEY':.<34} "
              f"{'definida' if Config.GEMINI_API_KEY else 'ausente'}"
              f"   (modelo={Config.GEMINI_MODEL})")
        print(f"  {'KIMI_API_KEY':.<34} "
              f"{'definida' if Config.KIMI_API_KEY else 'ausente'}"
              f"   (modelo={Config.KIMI_MODEL}, effort={Config.KIMI_REASONING_EFFORT})")
        print(f"  {'SHOW_AI_ERROR_DETAIL':.<34} "
              f"{'activado' if Config.SHOW_AI_ERROR_DETAIL else 'desactivado (por defecto)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  configuración: FALLO -> {exc}")
        sys.exit(1)

    from app.modules import storage
    from app.modules.ai_orchestrator import get_recommendations
    from app.modules.llm_providers import PROVIDER_NAMES, build_provider
    from app.modules.location_context import find_nearby_pois, reverse_geocode
    from app.modules.weather_context import get_weather

    # El diagnóstico SIEMPRE enseña el detalle completo del error: para eso
    # existe. SHOW_AI_ERROR_DETAIL controla lo que ve el usuario en la
    # interfaz, no lo que ves tú depurando. La redacción de la API key sigue
    # aplicándose igualmente: eso no lo desactiva nada.
    Config.SHOW_AI_ERROR_DETAIL = True

    print("\nSERVICIOS")
    ok = True
    ok &= check("base de datos (SQLite)", lambda: (storage.init_db(), "esquema listo")[1])

    place = None
    def _geo():
        nonlocal place
        place = reverse_geocode(lat, lon)
        return place.short_label()
    ok &= check("Nominatim (geocodificación)", _geo)

    weather = None
    def _weather():
        nonlocal weather
        weather = get_weather(lat, lon)
        return f"{weather.summary()[:40]} | agua: {weather.water_sports().rating}"
    weather_ok = check("Open-Meteo (tiempo + oleaje)", _weather)

    pois: list = []
    def _pois():
        nonlocal pois
        pois = find_nearby_pois(lat, lon)
        return f"{len(pois)} puntos de interés"
    pois_ok = check("Overpass (puntos de interés)", _pois)

    # Con --todos probamos cada proveedor registrado; sin él, solo el activo.
    # Probar todos de una vez responde a "¿tengo alternativa si este falla?",
    # que es la pregunta útil cuando te quedas sin saldo a mitad de viaje.
    a_probar = list(PROVIDER_NAMES) if todos else [Config.LLM_PROVIDER]

    ai_ok = False
    if place is not None:
        def _make_check(nombre: str):
            def _ai():
                # use_cache=False: una recomendación cacheada no prueba nada
                # sobre si el proveedor responde ahora mismo.
                provider = build_provider(nombre)
                reco = get_recommendations(
                    place, weather, pois, use_cache=False, provider=provider
                )
                detalle = f"{len(reco.actividades)} actividades vía {provider.describe()}"
                # Con un proveedor de prepago, "cuántos tokens ha costado esto"
                # deja de ser curiosidad. `last_usage` es opcional: los
                # proveedores que no lo reportan lo dejan a None.
                uso = provider.last_usage
                if uso:
                    detalle += (
                        f" [{uso.get('prompt_tokens', '?')} tokens dentro, "
                        f"{uso.get('completion_tokens', '?')} fuera]"
                    )
                return detalle
            return _ai

        resultados = [check(f"IA: {nombre}", _make_check(nombre)) for nombre in a_probar]
        ai_ok = any(resultados)

    # El saldo solo se consulta si Kimi entra en juego: es el único proveedor
    # de prepago, y con la recarga mínima de 1 $ la pregunta "¿cuánto me queda?"
    # se hace de verdad. Va después de la llamada para que el saldo que salga
    # ya refleje lo que acaba de gastarse.
    if "kimi" in a_probar and Config.KIMI_API_KEY:
        from app.modules.llm_providers import KimiProvider
        check("saldo de Kimi", lambda: KimiProvider().balance())

    print("\n" + "=" * 56)
    if ok and weather_ok and pois_ok and ai_ok:
        print("Todo correcto.")
    elif ok:
        # Este es el mensaje importante: recuerda que la app está diseñada
        # para seguir sirviendo aunque falten fuentes opcionales.
        print("La app FUNCIONA en modo degradado: la ubicación se resuelve y")
        print("las fuentes que fallan se sustituyen por un aviso en la interfaz.")
    else:
        print("La ubicación no se puede resolver: la app no será utilizable.")
    print()


if __name__ == "__main__":
    main()
