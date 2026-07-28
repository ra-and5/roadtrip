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
from pathlib import Path
from typing import Iterable

# Este script vive en tools/, así que Python pone tools/ en el path, no la
# raíz del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Por debajo de esto el disco se considera en peligro. 50 MB no es donde la app
# se rompe, es donde todavía da tiempo a hacer algo: el margen existe para que
# el aviso llegue ANTES del problema, no a la vez.
MIN_DISCO_MB = 50

BASE_DIR = Path(__file__).resolve().parent.parent

# El virtualenv, que en el servidor son ~101 MB de los 512 y es el mayor
# inquilino de la cuota. Se cuenta aparte SOLO cuando cae fuera del repositorio
# (en PythonAnywhere vive en ~/.virtualenvs/), porque si está dentro —un `.venv`
# en la raíz, que es lo normal en local— el recorrido del repo ya lo ha sumado y
# volver a contarlo daría el doble.
#
# Y `sys.prefix == sys.base_prefix` significa que NO hay virtualenv: se está
# corriendo con el Python del sistema. Medirlo entonces sería recorrer un
# miniconda de varios GB que no tiene nada que ver con este proyecto.
def _raiz_venv() -> Path | None:
    if sys.prefix == sys.base_prefix:
        return None
    prefix = Path(sys.prefix).resolve()
    return None if prefix.is_relative_to(BASE_DIR) else prefix


def uso_mb(rutas: Iterable[Path]) -> float:
    """Cuánto disco ocupan de verdad esas rutas, en MB.

    Se suma `st_blocks * 512` y no `st_size`, que es la diferencia entre lo que
    mide una cuota y lo que mide un `ls`: el sistema de archivos reserva bloques
    enteros, así que diez mil archivos de 100 bytes —un virtualenv -— ocupan
    mucho más de un mega. Es el mismo número que da `du`, que es con lo que se
    va a contrastar esto desde una consola del servidor.

    Los inodos ya vistos no se vuelven a sumar: un enlace duro apunta a bloques
    que ya están contados, y contarlos dos veces inventaría ocupación.

    Un archivo que desaparece a mitad del recorrido (un `.db-wal`, un temporal
    de pip) se ignora en vez de reventar el diagnóstico entero: esta función
    existe precisamente para las veces en que algo va mal.
    """
    vistos: set[tuple[int, int]] = set()
    bloques = 0
    for raiz in rutas:
        if not raiz.exists():
            continue
        for directorio, _, archivos in os.walk(raiz):
            for nombre in archivos:
                try:
                    st = os.lstat(os.path.join(directorio, nombre))
                except OSError:
                    continue
                clave = (st.st_dev, st.st_ino)
                if clave in vistos:
                    continue
                vistos.add(clave)
                bloques += st.st_blocks
    return bloques * 512 / (1024 * 1024)


def libres_mb(usado_mb: float, cuota_mb: float) -> float:
    """Cuánto queda de la cuota. Lanza `RuntimeError` si queda poco.

    Es una función y no tres líneas dentro del diagnóstico porque es la única
    parte de esta comprobación que decide algo, y lo que decide es si sale un
    aviso o no. Justo lo que estuvo roto: cuando el umbral no puede alcanzarse,
    nada falla y nadie se entera.
    """
    libres = cuota_mb - usado_mb
    if libres < MIN_DISCO_MB:
        raise RuntimeError(
            f"quedan {libres:.0f} MB de la cuota de {cuota_mb:.0f} (menos de "
            f"{MIN_DISCO_MB}): libera sitio antes de que la app deje de poder "
            f"escribir. Mira dónde se fue con `du -sh ~/* ~/.virtualenvs/*`"
        )
    return libres


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
        # Se informa de si el HASH está puesto, nunca de su contenido, y el
        # token en claro no existe aquí: el servidor solo guarda el hash.
        print(f"  {'INGEST_TOKEN_HASH':.<34} "
              f"{'configurado' if Config.INGEST_TOKEN_HASH else 'AUSENTE (ingesta cerrada)'}"
              f"   (máx. {Config.INGEST_MAX_SAMPLES} muestras/envío)")
    except Exception as exc:  # noqa: BLE001
        print(f"  configuración: FALLO -> {exc}")
        sys.exit(1)

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.modules import luna, storage
    from app.modules.ai_orchestrator import get_recommendations
    from app.modules.contexto import ensamblar
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

    # Telemetría del móvil (Fase 2d). No es una dependencia externa, así que no
    # cuenta para el veredicto final: la app funciona igual sin ella. Se mira
    # aquí porque es la pregunta que cierra esa fase —¿siguen llegando datos?—
    # y esta es la herramienta que se abre en el servidor cuando no llegan.
    def _telemetria() -> str:
        s = storage.telemetry_stats()
        if not s["total"]:
            return "0 muestras (aún no ha llegado ninguna)"
        return f"{s['total']} muestras, última medida {s['ultima_medida']}"
    check("telemetría del móvil", _telemetria)

    # Notas del viaje (Fase 3). Tampoco cuenta para el veredicto: la app
    # funciona sin ninguna nota. Se mira por lo mismo que la telemetría, y
    # porque es lo único que responde "¿está llegando lo que escribo desde el
    # móvil?" sin abrir el mapa.
    def _notas() -> str:
        s = storage.notes_stats()
        if not s["total"]:
            return "0 notas (aún no hay ninguna)"
        return f"{s['total']} notas, la última del {s['ultima']}"
    check("notas del viaje", _notas)

    # Puntos sacados del EXIF de las fotos (Fase 3b). Tampoco cuenta para el
    # veredicto. Se mira `ubicados` y no solo el total porque es la cifra que
    # dice si el trayecto se puede dibujar: mil fotos sin GPS no pintan nada.
    def _puntos() -> str:
        s = storage.waypoints_stats()
        if not s["total"]:
            return "0 puntos (aún no se ha importado ninguna foto)"
        return (
            f"{s['total']} puntos, {s['ubicados']} con GPS, "
            f"de {(s['primera'] or '?')[:10]} a {(s['ultima'] or '?')[:10]}"
        )
    check("puntos de las fotos", _puntos)

    # El primer sitio de cada día (Fase 5). Lo que se enseña es el número de
    # HUECOS, no el total, porque es la única cifra que decide si este dato
    # sirve para construir algo encima — la misma vara de medir que mantiene
    # aparcada la Fase 2d. Un total alto con huecos no es una serie: son
    # anécdotas sueltas.
    def _lugares() -> str:
        from app.modules import diario

        s = diario.resumen()
        if not s["total"]:
            return "0 días (aún no se ha abierto la app desde ningún sitio)"
        estado = "sin huecos" if s["huecos"] == 0 else f"{s['huecos']} días SIN registrar"
        return f"{s['total']} días, de {s['primero']} a {s['ultimo']} — {estado}"
    check("lugar del día", _lugares)

    # El disco, que es el recurso que se agota sin avisar en un plan gratuito
    # de 512 MB. Hoy las notas son solo texto y no gastan casi nada, pero el
    # aviso tiene que existir ANTES de que haya fotos: quedarse sin disco a
    # mitad de viaje no puede ser una sorpresa, y en PythonAnywhere un disco
    # lleno no degrada, rompe la app entera (SQLite necesita sitio hasta para
    # leer, porque escribe el WAL).
    #
    # Esto MEDÍA el volumen y no la cuota, así que no servía para nada: en
    # PythonAnywhere `shutil.disk_usage()` contestaba 1,6 TB libres y el aviso
    # de "por debajo de 50 MB" no podía saltar jamás. Un número tranquilizador
    # y falso, que es peor que no tener número. Ahora se mide lo que ocupamos
    # nosotros y se compara contra la cuota declarada en la configuración.
    #
    # El hueco, dicho en voz alta en vez de disimulado: esto suma el repositorio
    # y el virtualenv, y contra la misma cuota cuentan también los logs de
    # PythonAnywhere y cualquier otra cosa que haya en el $HOME. Así que la
    # cifra es un suelo, no el total, y `du -sh ~` sigue siendo la verdad de
    # referencia. Un hueco declarado se entiende; el que estamos arreglando es
    # justo el contrario.
    def _disco() -> str:
        venv_dir = _raiz_venv()
        repo = uso_mb([BASE_DIR])
        venv = uso_mb([venv_dir]) if venv_dir else 0.0
        subidas = uso_mb([Config.UPLOAD_DIR])
        usado = repo + venv
        libres = libres_mb(usado, Config.DISCO_CUOTA_MB)
        # Un "venv 0" se leería como "no hay virtualenv", que es otra cosa
        # distinta de "está dentro del repo y ya va contado ahí arriba".
        desglose = f"repo {repo:.0f}"
        if venv_dir:
            desglose += f", venv {venv:.0f}"
        return (
            f"{usado:.0f} MB de {Config.DISCO_CUOTA_MB:.0f} usados, "
            f"quedan {libres:.0f}  ({desglose}, uploads {subidas:.1f})"
        )
    check("espacio en disco", _disco)

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

    # La luna, en DOS comprobaciones y no en una, porque son dos cosas con
    # riesgos distintos y mezclarlas escondería cuál ha fallado.
    #
    # La primera no toca la red: si esta fallara sería un bug del cálculo, no
    # una fuente caída. La segunda sale a `api.met.no`, que es un **dominio
    # más** que tiene que pasar la lista blanca del proxy de PythonAnywhere —
    # y comprobar eso ANTES de tocar el móvil es literalmente para lo que
    # existe este script (decisión 21). Un host no permitido devuelve un 403
    # del proxy que la app degrada en silencio.
    #
    # Que la segunda falle NO cuenta para el veredicto final, y es lo correcto:
    # sin met.no sigue habiendo fase, iluminación y veredicto nocturno. Lo
    # único que se pierde es la hora de salida y puesta.
    ahora_local = datetime.now(ZoneInfo("Europe/Madrid"))

    def _fase_luna() -> str:
        f = luna.fase(ahora_local)
        return f"{f.nombre} al {f.iluminacion_pct:.0f} % (sin red, siempre)"
    check("luna: fase (calculada)", _fase_luna)

    def _efemerides_luna() -> str:
        ef = luna.efemerides(lat, lon, ahora_local)
        sale = ef.salida[11:16] if ef.salida else "no sale hoy"
        pone = ef.puesta[11:16] if ef.puesta else "no se pone hoy"
        return f"sale {sale}, se pone {pone}"
    check("api.met.no (salida y puesta)", _efemerides_luna)

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
                # El contexto se ensambla a partir de lo que cada
                # comprobación de arriba ya ha resuelto, en vez de llamar a
                # `contexto.construir()`: aquí las fuentes se prueban UNA A UNA
                # para poder decir cuál falla, que es todo el sentido de un
                # diagnóstico. `ensamblar()` es la parte pura, así que no
                # repite ninguna llamada de red.
                reco = get_recommendations(
                    ensamblar(place, weather), pois, use_cache=False, provider=provider
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
