"""Comprueba una por una todas las piezas de la app: las de fuera y las de casa.

Uso:
    python tools/diagnostico.py                  # usa Cudillero por defecto
    python tools/diagnostico.py 43.36 -8.41      # unas coordenadas concretas
    python tools/diagnostico.py --todos          # prueba TODOS los proveedores
    python tools/diagnostico.py -v               # con traza completa

Para cuando algo no funciona y estás a 800 km de casa: te dice QUÉ falla, no
solo que "hay un error". Cada línea es independiente, así que sabes
exactamente qué pieza está rota y cuál sigue en pie.

Cuatro bloques, y el orden no es decorativo — va de lo que no puede fallar a lo
que degrada:

    CONFIGURACIÓN     lo que ni siquiera llega a intentarse si está mal
    DATOS DEL VIAJE   lo nuestro: SQLite, el disco y si las fuentes propias
                      están llegando sin huecos
    FUENTES EXTERNAS  lo de fuera, que puede caerse y la app lo sustituye por
                      un aviso (decisión 9)
    EL CONTEXTO       la pieza central, de punta a punta y cronometrada

**Por qué el contexto se prueba aparte, al final.** `contexto.construir()` es lo
que alimentan a la vez la pantalla, el recomendador y el chatbot, así que es lo
único cuyo fallo se nota en las tres caras. Y va cronometrado porque su tiempo
es un contrato: por debajo de un segundo. Si sube de dos, alguien ha vuelto a
meter una fuente lenta en el camino normal —Overpass costaba 31 s (decisión
33)—, y eso no da ningún error: solo una app que se abandona por lenta.

El detalle de los errores de IA se muestra aquí SIEMPRE, tenga el valor que
tenga SHOW_AI_ERROR_DETAIL: esa variable controla lo que ve el usuario en la
interfaz, no lo que ves tú depurando.

Código de salida: 0 si la app es utilizable (aunque sea degradada), 1 si no.
Así se puede encadenar en un script de despliegue sin leer la salida a ojo.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Este script vive en tools/, así que Python pone tools/ en el path, no la
# raíz del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Se importa aquí arriba y no dentro de `main()` como el resto de módulos de la
# app, y la diferencia importa: los demás se importan tarde a propósito para que
# una configuración rota salga como una línea FALLO en vez de reventar el script
# antes de imprimir nada. `timeparse` no lee `Config` ni abre la base de datos
# —solo hace aritmética de fechas—, así que no puede provocar eso.
from app.modules.timeparse import hace_cuanto  # noqa: E402

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


def veredicto(*, ok: bool, contexto_ok: bool, todo_fino: bool, lento: str) -> list[str]:
    """Las líneas finales, que son lo único que mucha gente lee.

    Es una función y no cuatro `print` dentro de `main()` por la razón de
    siempre: aquí se **decide** qué se le dice a alguien que está mirando un
    servidor, y eso merece pruebas. Y hacía falta, porque estaba mal: cuando el
    contexto se construía entero pero tardaba de más, el veredicto anunciaba
    "la ubicación no se puede resolver" **con la ubicación resuelta y 6/6
    fuentes impresas dos líneas más arriba**. Mandaba a depurar el GPS, que era
    lo único que no tenía nada que ver.

    Args:
        ok: las comprobaciones obligatorias (base de datos, ubicación) pasaron.
        contexto_ok: `contexto.construir()` pasó, tiempo incluido.
        todo_fino: además, las fuentes opcionales respondieron.
        lento: cuánto tardó el contexto, si incumplió el contrato. Vacío si no.
    """
    if ok and contexto_ok and todo_fino:
        return ["Todo correcto."]
    if ok and contexto_ok:
        # Este es el mensaje importante: recuerda que la app está diseñada
        # para seguir sirviendo aunque falten fuentes opcionales.
        return [
            "La app FUNCIONA en modo degradado: la ubicación se resuelve y",
            "las fuentes que fallan se sustituyen por un aviso en la interfaz.",
        ]
    if lento:
        # Se construyó, con su ubicación y sus fuentes. Lo que falla es el
        # tiempo, y decirlo así evita mandar a mirar el GPS, que está bien.
        return [
            f"El contexto se construye ENTERO, pero tardó {lento} y el contrato",
            "es de un segundo. La app se abre, pero se abandona por lenta.",
            "  python tools/medir_contexto.py   dice cuál de las tres fuentes es",
            "Si las tres salen rápidas ahí, no era la red: el servidor estaba",
            "ahogado en ese momento (la cuota de CPU del plan gratuito). Repite.",
        ]
    return ["La ubicación no se puede resolver: la app no será utilizable."]


def dato(nombre: str, valor: str) -> None:
    """Una línea informativa: ni pasa ni falla, solo se lee.

    Existe para separar dos cosas que `check()` mezclaría: "esto lo he probado
    y funciona" de "esto es como está configurado". Un valor de configuración no
    puede salir con un OK y un cronómetro al lado, porque no se ha probado nada.
    """
    print(f"  {nombre:.<34} {valor}")


# Cudillero. Solo se usa si no hay ni un dato propio, y entonces se dice.
_COORDS_DE_EJEMPLO = (43.5622, -6.1456)


def ultimo_sitio_conocido() -> tuple[float, float, str]:
    """Dónde se estuvo por última vez, según lo que hay guardado.

    Existe porque medir siempre el mismo punto inventado da aprobados falsos, y
    eso costó una mañana: el diagnóstico decía que `contexto.construir()`
    tardaba 0,05 s mientras la app tardaba 34 s desde el móvil. Las dos cifras
    eran ciertas — la del diagnóstico salía de unas coordenadas fijas que
    llevaban meses cacheadas, y el móvil pedía el sitio donde estabas, que no
    se había consultado nunca. Un punto de prueba que nunca cambia deja de
    probar la parte que falla.

    Se prueba en orden de "cuánto se parece a lo que hace la app":

    1. `lugar_del_dia` — el sitio donde de verdad se abrió la pantalla.
    2. la telemetría **real** — dónde estaba el móvil. Nunca la simulada: sus
       coordenadas están inventadas y volveríamos al problema (decisión 36).
    3. los puntos de las fotos — dónde se estuvo, aunque sea de otro día.

    Devuelve también de dónde salió, porque una cifra sin su procedencia es lo
    que hacía que "Diagnóstico para 43.5622, -6.1456" pareciera un dato del GPS.
    """
    from app.modules import metricas, storage

    try:
        for dia in storage.list_lugares_del_dia(limit=1):
            if dia.get("lat") is not None and dia.get("lon") is not None:
                sitio = dia.get("place_name") or "sin nombre"
                return float(dia["lat"]), float(dia["lon"]), f"último día: {sitio}"

        for muestra in storage.list_telemetria(limit=200):
            if muestra.get("fuente") != metricas.FUENTE_REAL:
                continue
            if muestra.get("lat") is not None and muestra.get("lon") is not None:
                return float(muestra["lat"]), float(muestra["lon"]), "última telemetría real"

        puntos = [p for p in storage.list_waypoints() if p.get("lat") is not None]
        if puntos:
            ultimo = puntos[-1]
            return float(ultimo["lat"]), float(ultimo["lon"]), f"última foto: {ultimo['archivo']}"
    except Exception:  # noqa: BLE001 - un diagnóstico no puede morir eligiendo dónde medir
        pass

    lat, lon = _COORDS_DE_EJEMPLO
    return lat, lon, "SIN DATOS PROPIOS todavía: punto de ejemplo, no es donde estás"


def main() -> None:
    # OJO: no se puede filtrar "lo que empieza por '-'" como si fueran flags.
    # Todo el norte de España tiene longitud NEGATIVA (-4.29, -6.14...), así
    # que ese filtro se comía justo el argumento que nos interesa. Por eso los
    # flags se listan explícitamente en vez de detectarse por el guion.
    _FLAGS = {"-v", "--todos"}
    todos = "--todos" in sys.argv
    args = [a for a in sys.argv[1:] if a not in _FLAGS]
    if len(args) >= 2:
        lat, lon, origen = float(args[0]), float(args[1]), "coordenadas dadas a mano"
    else:
        lat, lon, origen = ultimo_sitio_conocido()

    ahora_utc = datetime.now(timezone.utc)
    print(f"\nDiagnóstico para {lat}, {lon}   ({origen})"
          f"\n   ({ahora_utc.astimezone().strftime('%d-%m-%Y %H:%M %Z')})")
    print("=" * 66)

    # 1. Configuración: si esto falla, nada más va a funcionar.
    print("\nCONFIGURACIÓN")
    try:
        from app.config import Config
        # La versión importa en el servidor y no es curiosidad: PythonAnywhere
        # tiene varios Python instalados y el virtualenv se puede haber creado
        # con el que no era (ver el aviso del README sobre `virtualenv` contra
        # `python3.11 -m venv`). Un fallo de ese tipo aparece luego disfrazado
        # de "un paquete no se instala".
        # `importlib.metadata` y no `flask.__version__`, que está deprecado y
        # desaparece en Flask 3.2: un diagnóstico que escupe un DeprecationWarning
        # antes de la primera línea inspira poca confianza en lo que dice después.
        from importlib.metadata import version as _version
        dato("Python / Flask",
             f"{sys.version.split()[0]} / {_version('flask')}"
             f"   ({'virtualenv' if sys.prefix != sys.base_prefix else 'SIN virtualenv'})")
        dato("proveedor activo", f"LLM_PROVIDER={Config.LLM_PROVIDER}")
        dato("ANTHROPIC_API_KEY",
             f"{'definida' if Config.ANTHROPIC_API_KEY else 'ausente'}"
             f"   (modelo={Config.ANTHROPIC_MODEL}, effort={Config.ANTHROPIC_EFFORT})")
        dato("GEMINI_API_KEY",
             f"{'definida' if Config.GEMINI_API_KEY else 'ausente'}"
             f"   (modelo={Config.GEMINI_MODEL})")
        dato("KIMI_API_KEY",
             f"{'definida' if Config.KIMI_API_KEY else 'ausente'}"
             f"   (modelo={Config.KIMI_MODEL}, effort={Config.KIMI_REASONING_EFFORT})")
        dato("SHOW_AI_ERROR_DETAIL",
             "activado" if Config.SHOW_AI_ERROR_DETAIL else "desactivado (por defecto)")
        # Se informa de si el HASH está puesto, nunca de su contenido, y el
        # token en claro no existe aquí: el servidor solo guarda el hash.
        dato("INGEST_TOKEN_HASH",
             f"{'configurado' if Config.INGEST_TOKEN_HASH else 'AUSENTE (ingesta cerrada)'}"
             f"   (máx. {Config.INGEST_MAX_SAMPLES} muestras/envío)")
        # Sin esta clave la tarjeta de fuego no consulta nada y NO se queja: la
        # petición la hace el navegador, así que aquí no hay ningún error que
        # registrar (decisión 53). El síntoma es una tarjeta que no aparece, que
        # es indistinguible de "hoy no hay nada cerca". Y se dice la longitud
        # porque el fallo real fue pegarla cortada: FIRMS contesta "Invalid
        # MAP_KEY" con HTTP 200 y el nombre de la variable también se escribe
        # mal con facilidad (FIRMS_API_KEY no existe).
        clave_firms = Config.FIRMS_MAP_KEY
        dato("FIRMS_MAP_KEY",
             f"configurada ({len(clave_firms)} caracteres"
             f"{'' if len(clave_firms) == 32 else ', OJO: se esperan 32'})"
             if clave_firms else "AUSENTE (la tarjeta de fuego no consulta nada)")
        dato("GOOGLE_MAPS_API_KEY",
             "configurada (chat puede consultar Places/Routes)"
             if Config.GOOGLE_MAPS_API_KEY
             else "ausente (chat avisa y no inventa bares/rutas)")
        dato("AEMET_API_KEY",
             "configurada (chat puede consultar predicción/avisos/radar de España)"
             if Config.AEMET_API_KEY
             else "ausente (chat avisa y no inventa meteo nacional)")
        # La trampa de la decisión 15, que cuesta una tarde y no da NINGÚN
        # mensaje de error: con la cookie `Secure` (que es lo que hay por
        # defecto, y bien) y *Force HTTPS* desactivado en PythonAnywhere, el
        # navegador descarta la cookie y la app entra en bucle de login sin
        # decir nada. Aquí no se puede comprobar desde fuera, así que se
        # recuerda: es lo único que gatea la app entera y no se estaba mirando.
        dato("cookie de sesión",
             "Secure ACTIVADA — exige *Force HTTPS* en el servidor, o bucle de login"
             if Config.SESSION_COOKIE_SECURE
             else "Secure DESACTIVADA — la sesión viaja en claro si entras por http")
        dato("cuota de disco declarada",
             f"{Config.DISCO_CUOTA_MB:.0f} MB (DISCO_CUOTA_MB)")
    except Exception as exc:  # noqa: BLE001
        print(f"  configuración: FALLO -> {exc}")
        sys.exit(1)

    from zoneinfo import ZoneInfo

    from app.modules import contexto, luna, metricas, storage
    from app.modules.ai_orchestrator import get_recommendations
    from app.modules.contexto import ensamblar
    from app.modules.llm_providers import PROVIDER_NAMES, build_provider
    from app.modules.location_context import find_nearby_pois, reverse_geocode
    from app.modules.weather_context import get_weather

    # El contacto del User-Agent, que es una fuente de fallos mudos con nombre
    # propio: met.no devuelve un 403 de nginx SIN mensaje ante un dominio de
    # ejemplo, y Nominatim puede bloquear la IP (decisión 34). Se valida con la
    # misma función que usa el módulo de la luna para negarse a llamar, para
    # que aquí no pueda decir una cosa distinta de la que hace la app.
    contacto = Config.NOMINATIM_USER_AGENT
    dato("contacto (User-Agent)",
         (contacto[:44] if luna.contacto_valido(contacto)
          else f"DOMINIO DE EJEMPLO -> la luna no llamará: {contacto[:30]}"))

    # La hora local, una sola vez y compartida. Se usa para la luna y para saber
    # qué día es "hoy" al contar la telemetría, y son la MISMA pregunta: si cada
    # una leyera su reloj podrían caer a distinto lado de la medianoche y el
    # diagnóstico se contradiría a sí mismo. Europe/Madrid explícito, porque el
    # servidor corre en UTC y ahí "hoy" no es el día del viaje (decisión 29).
    ahora_local = datetime.now(ZoneInfo("Europe/Madrid"))

    # El diagnóstico SIEMPRE enseña el detalle completo del error: para eso
    # existe. SHOW_AI_ERROR_DETAIL controla lo que ve el usuario en la
    # interfaz, no lo que ves tú depurando. La redacción de la API key sigue
    # aplicándose igualmente: eso no lo desactiva nada.
    Config.SHOW_AI_ERROR_DETAIL = True

    print("\nDATOS DEL VIAJE   (lo nuestro; solo SQLite cuenta para el veredicto)")
    ok = True
    ok &= check("base de datos (SQLite)", lambda: (storage.init_db(), "esquema listo")[1])

    # Telemetría del móvil (Fase 2d). No es una dependencia externa, así que no
    # cuenta para el veredicto final: la app funciona igual sin ella. Se mira
    # aquí porque es la pregunta que cierra esa fase —¿siguen llegando datos?—
    # y esta es la herramienta que se abre en el servidor cuando no llegan.
    #
    # Esto decía "5 muestras, última medida <ISO>" y las dos mitades engañaban:
    #
    #   - **el total mezclaba lo real con lo SIMULADO.** Con el simulador
    #     sembrado, un "86 muestras" verde se lee como "la telemetría llega",
    #     que es exactamente lo contrario de lo que pasa. Y es el peor sitio
    #     para ese error, porque esta línea es la que decide si la 2d se cierra
    #     (decisión 36). Ahora las dos series salen separadas y la simulada va
    #     marcada, como en `ver_telemetria.py`.
    #   - **un total no dice si hay huecos.** Seis envíos diarios que llegan
    #     tres días y fallan dos suman igual que cinco días completos. Lo que
    #     cierra la fase no es el volumen, es la continuidad, así que se enseña
    #     lo mismo que en `lugar del día`: días cubiertos, huecos, y los días
    #     que llegaron a medias — que no son un hueco y tampoco son un día
    #     bueno.
    def _telemetria() -> str:
        s = storage.telemetry_stats()
        if not s["total"]:
            return "0 muestras (aún no ha llegado ninguna)"

        reales = s["por_fuente"].get(metricas.FUENTE_REAL, 0)
        simuladas = s["total"] - reales
        cola = f"  [+{simuladas} simuladas, que NO cierran nada]" if simuladas else ""

        if not reales:
            return f"0 muestras REALES de {metricas.FUENTE_REAL}{cola}"

        # Desde el principio de los tiempos: aquí interesa el histórico entero,
        # no una ventana. `medido_en` se compara como texto ISO, así que una
        # fecha anterior a cualquier muestra posible las trae todas.
        muestras = storage.telemetry_since(
            "1970-01-01T00:00:00+00:00", fuentes=[metricas.FUENTE_REAL]
        )
        cob = metricas.cobertura(muestras, ahora_local.date())

        estado = "sin huecos" if cob.sin_huecos else f"{cob.huecos} días SIN datos"
        if cob.dias_incompletos:
            estado += (f", {cob.dias_incompletos} a medias "
                       f"(<{metricas.ENVIOS_ESPERADOS_POR_DIA}/día)")
        return (f"{reales} reales en {cob.dias_con_datos}/{cob.dias_abarcados} días — "
                f"{estado}; última {hace_cuanto(cob.ultima, ahora_utc)}{cola}")
    check("telemetría del móvil", _telemetria)

    # Notas del viaje (Fase 3). Tampoco cuenta para el veredicto: la app
    # funciona sin ninguna nota. Se mira por lo mismo que la telemetría, y
    # porque es lo único que responde "¿está llegando lo que escribo desde el
    # móvil?" sin abrir el mapa.
    def _notas() -> str:
        s = storage.notes_stats()
        if not s["total"]:
            return "0 notas (aún no hay ninguna)"
        return f"{s['total']} notas, la última {hace_cuanto(s['ultima'], ahora_utc)}"
    check("notas del viaje", _notas)

    # Puntos sacados del EXIF de las fotos (Fase 3b). Tampoco cuenta para el
    # veredicto. Se mira `ubicados` y no solo el total porque es la cifra que
    # dice si el trayecto se puede dibujar: mil fotos sin GPS no pintan nada.
    def _puntos() -> str:
        s = storage.waypoints_stats()
        if not s["total"]:
            return "0 puntos (aún no se ha importado ninguna foto)"
        plural = "punto" if s["total"] == 1 else "puntos"
        return (
            f"{s['total']} {plural}, {s['ubicados']} con GPS, "
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

    # El chatbot (Fase 6). `storage.chat_stats()` se escribió literalmente "para
    # el diagnóstico" y el diagnóstico no la llamaba: la tabla que la app está
    # escribiendo era la única que no se veía desde aquí. Tampoco cuenta para el
    # veredicto —no haber preguntado nada no es un fallo—, pero sí responde
    # "¿se está guardando lo que pregunto?", que es la mitad del cuaderno de a
    # bordo y no se puede comprobar de otra forma sin abrir la web.
    def _chat() -> str:
        s = storage.chat_stats()
        if not s["total"]:
            return "0 mensajes (aún no se ha preguntado nada)"
        return (f"{s['total']} mensajes, el último "
                f"{hace_cuanto(s['ultimo'], ahora_utc)}")
    check("conversaciones del chat", _chat)

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

    print("\nFUENTES EXTERNAS   (se caen, y la app lo sustituye por un aviso)")

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
        # La altitud sale gratis en la misma respuesta (decisión 35) y es lo
        # único de esta línea que se puede contrastar contra un mapa, así que
        # vale como comprobación de que las coordenadas son las que crees.
        altitud = (f", {weather.elevation_m:.0f} m"
                   if weather.elevation_m is not None else "")
        return (f"{weather.summary()[:52]}{altitud}"
                f" | agua: {weather.water_sports().rating}")
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

    # 4. El contexto, de punta a punta. Es lo ÚNICO que se prueba por el mismo
    #    camino que usa la app: todo lo de arriba llama a cada fuente por
    #    separado —que es lo que permite decir cuál falla— y esto llama a la
    #    función que de verdad se ejecuta cuando alguien abre la pantalla.
    #
    #    Vale por tres cosas que ninguna línea anterior puede dar:
    #
    #    - **el tiempo.** Es un contrato medido: por debajo de un segundo. Si
    #      sube de dos, alguien ha metido una fuente lenta en el camino normal,
    #      y eso no da error — solo una app que se abandona por lenta. Como las
    #      fuentes acaban de consultarse arriba, aquí están cacheadas, así que
    #      un tiempo alto significa además que la caché no está funcionando.
    #    - **que los POIs siguen FUERA.** Overpass costaba 31,3 s en el camino
    #      normal (decisión 33). Si vuelve, se ve aquí y en ningún otro sitio.
    #    - **el veredicto de cada fuente**, en el vocabulario de la decisión 32:
    #      una fuente en `sin_datos` no es una fuente caída, y confundirlas es
    #      el error que este proyecto ya evitó a propósito.
    print("\nEL CONTEXTO   (la pieza que alimenta pantalla, recomendador y chat)")

    # Se anota si el contexto se CONSTRUYÓ pero incumplió el contrato de tiempo.
    # Sin esto, el veredicto final trataba las dos formas de suspender como una
    # sola y anunciaba "la ubicación no se puede resolver" con la ubicación
    # resuelta y 6/6 fuentes en la línea de arriba. Una herramienta de
    # diagnóstico que nombra mal el fallo manda a depurar lo que no era: es el
    # fallo silencioso de la decisión 11 dentro de la propia herramienta que
    # existe para cazarlo.
    lento: list[str] = []

    def _contexto() -> str:
        t0 = time.time()
        estado = contexto.construir(lat, lon)
        tardanza = time.time() - t0

        caidas = [n for n, f in estado.fuentes.items() if f.estado == contexto.FALLO]
        resumen = f"{estado.ubicacion.short_label()}"
        if estado.momento.zona_es_supuesta:
            # La zona supuesta desplaza una hora todo lo que cuelga de la hora
            # local, y en Canarias eso ya no es un detalle (decisión 32).
            resumen += " · ZONA HORARIA SUPUESTA"
        resumen += f" · {len(estado.fuentes) - len(caidas)}/{len(estado.fuentes)} fuentes"
        if caidas:
            resumen += f" (caídas: {', '.join(caidas)})"
        if tardanza > 2:
            lento.append(f"{tardanza:.1f}s")
            raise RuntimeError(
                f"ha tardado {tardanza:.1f}s, y el contrato es <1s. O alguien ha "
                f"metido una fuente lenta en el camino normal, o el servidor está "
                f"ahogado. Para saber cuál: python tools/medir_contexto.py. "
                f"El contexto SÍ se construyó: {resumen}"
            )
        return resumen
    contexto_ok = check("contexto.construir()", _contexto)

    print("\n" + "=" * 66)
    for linea in veredicto(
        ok=ok,
        contexto_ok=contexto_ok,
        todo_fino=weather_ok and pois_ok and ai_ok,
        lento=lento[0] if lento else "",
    ):
        print(linea)
    print()

    # Degradado sale 0: es un estado de funcionamiento diseñado a propósito
    # (decisión 9), no un fallo, y hacerlo fallar convertiría un Overpass caído
    # —que lo está casi siempre— en un despliegue "roto". Solo el caso en que
    # la app no se puede usar devuelve 1.
    sys.exit(0 if (ok and contexto_ok) else 1)


if __name__ == "__main__":
    main()
