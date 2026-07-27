"""Comprueba una por una todas las dependencias externas de la app.

Uso:
    python tools/diagnostico.py                  # usa Cudillero por defecto
    python tools/diagnostico.py 43.36 -8.41      # unas coordenadas concretas

Para cuando algo no funciona y estás a 800 km de casa: te dice QUÉ falla, no
solo que "hay un error". Cada línea es independiente, así que sabes
exactamente qué pieza está rota y cuál sigue en pie.
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
    # que ese filtro se comía justo el argumento que nos interesa.
    args = [a for a in sys.argv[1:] if a != "-v"]
    lat, lon = (float(args[0]), float(args[1])) if len(args) >= 2 else (43.5622, -6.1456)

    print(f"\nDiagnóstico para {lat}, {lon}\n" + "=" * 56)

    # 1. Configuración: si esto falla, nada más va a funcionar.
    print("\nCONFIGURACIÓN")
    try:
        from app.config import Config
        print(f"  {'variables de entorno':.<34} OK     "
              f"modelo={Config.ANTHROPIC_MODEL} effort={Config.ANTHROPIC_EFFORT}")
        print(f"  {'ANTHROPIC_API_KEY':.<34} "
              f"{'OK     definida' if Config.ANTHROPIC_API_KEY else 'AUSENTE (no habrá recomendaciones)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  configuración: FALLO -> {exc}")
        sys.exit(1)

    from app.modules import storage
    from app.modules.ai_orchestrator import get_recommendations
    from app.modules.location_context import find_nearby_pois, reverse_geocode
    from app.modules.weather_context import get_weather

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

    if place is not None:
        def _ai():
            reco = get_recommendations(place, weather, pois, use_cache=False)
            return f"{len(reco.actividades)} actividades, modelo={reco.modelo}"
        ai_ok = check("Anthropic (recomendaciones)", _ai)
    else:
        ai_ok = False

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
