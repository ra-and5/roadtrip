"""Comprueba la API de FIRMS (incendios activos, NASA) contra la API REAL.

Uso:
    python tools/verificar_firms.py                 # Cudillero, Asturias
    python tools/verificar_firms.py 42.60 -6.55     # unas coordenadas concretas
    python tools/verificar_firms.py --radio 100     # caja de 100 km de lado
    python tools/verificar_firms.py -v              # con la traza de cada fallo

Existe por la regla del proyecto: las APIs externas se comprueban contra la API
real ANTES de escribir el módulo que las consume. Las decisiones 5, 7, 20 y 22
salieron todas de hacer esto, y cada una era un fallo que no habría dado error.

Lo que hay que saber ANTES de escribir `fire_context.py`, y que este script
contesta:

    1. ¿Qué sensores responden hoy? La lista documentada y la lista que
       funciona no son la misma (decisión 14, con los modelos de Gemini).
    2. ¿Cuánto tarda? `contexto.construir()` tiene un contrato de menos de un
       segundo, y en serie ese número se suma (decisión 43). Una fuente de
       30 s vuelve a ser Overpass (decisión 33).
    3. **¿Un CSV vacío significa "no hay fuego" o "el satélite no ha pasado"?**
       Es LA pregunta. Las dos cosas llegan igual, y confundirlas hace que la
       app diga "todo tranquilo" cuando lo que pasa es que no lo sabe. Es el
       espejo suizo de la decisión 22, y aquí se paga más caro porque al otro
       lado hay alguien durmiendo en un camper. Por eso se consulta
       `data_availability` y no solo `area`.
    4. ¿Qué columnas trae el CSV y cuáles son fiables?

Lo que este script NO puede contestar, y hay que decirlo: si funcionará en
PythonAnywhere. `firms.modaps.eosdis.nasa.gov` **no está en la lista blanca**
del proxy del plan gratuito (comprobado sobre el HTML de la página, como en la
decisión 21), así que en producción devolverá un 403 del proxy hasta que se
pida el alta. Aquí corre desde tu máquina, sin proxy. Correr esto en verde NO
significa que el despliegue funcione.

La MAP_KEY no se imprime nunca, ni entera ni en trozos: va en la RUTA de la
URL, así que cualquier volcado de URL la filtraría. Todo lo que sale pasa por
`redact()`.

Código de salida: 0 si FIRMS es utilizable, 1 si no.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
import traceback
from datetime import date, timedelta

# Este script vive en tools/, así que Python pone tools/ en el path, no la raíz
# del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "https://firms.modaps.eosdis.nasa.gov/api"

# Cudillero, que es el punto que ya usan los demás tools del proyecto.
LAT_POR_DEFECTO = 43.5622
LON_POR_DEFECTO = -6.1456

# Lado de la caja de búsqueda, en km. 50 km es lo que importa desde un camper:
# más lejos no cambia ninguna decisión de esta noche.
RADIO_KM_POR_DEFECTO = 50

# Sensores CANDIDATOS, no la lista buena. Se prueban todos y se informa de
# cuáles responden, porque la lista documentada y la que funciona no coinciden
# —pasó con los modelos de Gemini (decisión 14)—. Si mañana NASA retira uno,
# este script lo dice en vez de dejar el módulo devolviendo vacío en silencio.
SENSORES_CANDIDATOS = (
    "VIIRS_NOAA21_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_SNPP_NRT",
    "MODIS_NRT",
    "LANDSAT_NRT",
)

# Por encima de esto, la fuente no puede ir en el camino normal de
# `contexto.construir()`: su contrato es menos de un segundo y en serie los
# tiempos se suman (decisión 43).
UMBRAL_LENTO_S = 2.0

TIMEOUT_S = 20


def _grados_por_km(lat: float) -> tuple[float, float]:
    """Cuántos grados son un km aquí, en latitud y en longitud.

    Un grado de longitud mide 111 km en el ecuador y 78 km en el norte de
    España, así que usar el mismo número para las dos daría una caja un 40 %
    torcida justo en la zona del viaje. Es el mismo motivo por el que la ruta
    usa Haversine y no resta grados (decisión 31).
    """
    import math

    lat_km = 110.574
    lon_km = 111.320 * math.cos(math.radians(lat))
    return 1 / lat_km, 1 / max(lon_km, 1e-6)


def caja(lat: float, lon: float, radio_km: float) -> str:
    """La caja que pide FIRMS: oeste,sur,este,norte.

    Se devuelve como cadena ya formateada para que salga impresa tal cual y se
    pueda pegar en un navegador. Una caja mal construida no da error: devuelve
    focos de otro sitio.
    """
    d_lat, d_lon = _grados_por_km(lat)
    media = radio_km / 2
    oeste = lon - d_lon * media
    este = lon + d_lon * media
    sur = lat - d_lat * media
    norte = lat + d_lat * media
    return f"{oeste:.4f},{sur:.4f},{este:.4f},{norte:.4f}"


def _redact(texto: str) -> str:
    """Tapa la MAP_KEY. Se importa tarde para que una config rota no reviente."""
    try:
        from app.modules.llm_providers import redact

        return redact(texto)
    except Exception:
        # Si ni siquiera se puede importar, se prefiere no imprimir el texto a
        # imprimirlo con un secreto dentro.
        return "[texto oculto: no se pudo cargar redact()]"


def _clave() -> str | None:
    """La MAP_KEY, de `Config` si existe y del entorno si aún no está cableada.

    Se acepta `FIRMS_MAP_KEY` del entorno para poder verificar ANTES de tocar
    `config.py` —que ahora mismo lo está editando otra sesión—, pero el nombre
    bueno en `Config` es `FIRMS_API_KEY`: `redact()` descubre las claves
    buscando el sufijo `*_API_KEY` (decisión 19), así que con el nombre de NASA
    la clave saldría SIN TAPAR en el primer mensaje de error.
    """
    try:
        from app.config import Config

        valor = getattr(Config, "FIRMS_API_KEY", None)
        if valor:
            return valor
    except Exception:
        pass
    return os.environ.get("FIRMS_API_KEY") or os.environ.get("FIRMS_MAP_KEY")


def _pedir(url: str, verbose: bool) -> tuple[int | None, str, float]:
    """Devuelve (código, cuerpo, segundos). Nunca lanza."""
    import requests

    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
        return r.status_code, r.text, time.monotonic() - t0
    except Exception as e:  # noqa: BLE001
        if verbose:
            traceback.print_exc()
        # El mensaje de `requests` lleva la URL entera, y la URL lleva la
        # MAP_KEY en la ruta. Redactar aquí no es opcional.
        return None, _redact(f"{type(e).__name__}: {e}"), time.monotonic() - t0


def _es_error_de_clave(codigo: int | None, cuerpo: str) -> bool:
    """FIRMS usa DOS códigos para la misma causa.

    Comprobado contra la API real el 29-07-2026: `area` con una clave inválida
    devuelve **400** y `data_availability` devuelve **401**. Tratar solo uno
    haría que el otro se leyera como "fuente caída" y la app avisara de lo que
    no es.
    """
    return codigo in (400, 401) and "MAP_KEY" in cuerpo


def bloque_disponibilidad(clave: str, verbose: bool) -> dict[str, str]:
    """Qué sensores tienen datos y hasta qué fecha.

    Es la mitad que impide el fallo mudo: sin esto, un CSV vacío se lee como
    "no hay fuego" cuando puede ser "el satélite no ha pasado".
    """
    print("\nDISPONIBILIDAD DE DATOS")
    url = f"{BASE}/data_availability/csv/{clave}/ALL"
    codigo, cuerpo, seg = _pedir(url, verbose)

    if codigo is None:
        print(f"  FALLO  no se pudo consultar ({seg:.2f}s): {cuerpo}")
        return {}
    if _es_error_de_clave(codigo, cuerpo):
        print(f"  FALLO  MAP_KEY rechazada (HTTP {codigo}). Revisa FIRMS_API_KEY.")
        return {}
    if codigo != 200:
        print(f"  FALLO  HTTP {codigo} ({seg:.2f}s): {_redact(cuerpo[:200])}")
        return {}

    filas = list(csv.DictReader(io.StringIO(cuerpo)))
    if not filas:
        print("  AVISO  respondió 200 pero sin filas. Formato cambiado?")
        return {}

    print(f"  OK     {len(filas)} sensores listados en {seg:.2f}s")
    print(f"  columnas: {', '.join(filas[0].keys())}")
    ultimo: dict[str, str] = {}
    for f in filas:
        nombre = f.get("data_id") or f.get("dataset") or "?"
        fin = f.get("max_date") or f.get("end_date") or "?"
        ini = f.get("min_date") or f.get("start_date") or "?"
        ultimo[nombre] = fin
        print(f"    {nombre:<24} {ini} .. {fin}")
    return ultimo


def bloque_area(
    clave: str, bbox: str, sensores: tuple[str, ...], dias: int, verbose: bool
) -> tuple[bool, list[str]]:
    """Prueba cada sensor sobre la misma caja. Devuelve (hubo_alguno_ok, vivos)."""
    print(f"\nFOCOS EN LA CAJA  {bbox}  (últimos {dias} día(s))")
    vivos: list[str] = []
    alguno_ok = False

    for sensor in sensores:
        url = f"{BASE}/area/csv/{clave}/{sensor}/{bbox}/{dias}"
        codigo, cuerpo, seg = _pedir(url, verbose)
        etiqueta = f"  {sensor:<20}"

        if codigo is None:
            print(f"{etiqueta} FALLO  {cuerpo}")
            continue
        if _es_error_de_clave(codigo, cuerpo):
            print(f"{etiqueta} FALLO  MAP_KEY rechazada (HTTP {codigo})")
            continue
        if codigo != 200:
            primera = cuerpo.strip().splitlines()[:1]
            print(f"{etiqueta} no sirve (HTTP {codigo}) {_redact(primera[0] if primera else '')}")
            continue

        filas = list(csv.DictReader(io.StringIO(cuerpo)))
        vivos.append(sensor)
        alguno_ok = True
        lento = "  <-- LENTO" if seg > UMBRAL_LENTO_S else ""
        print(f"{etiqueta} OK     {len(filas)} focos en {seg:.2f}s{lento}")

        if filas:
            print(f"      columnas: {', '.join(filas[0].keys())}")
            f = filas[0]
            print(
                f"      ejemplo: {f.get('latitude')},{f.get('longitude')} "
                f"{f.get('acq_date')} {f.get('acq_time')} "
                f"conf={f.get('confidence')} frp={f.get('frp')} "
                f"daynight={f.get('daynight')}"
            )

    return alguno_ok, vivos


def main() -> int:
    p = argparse.ArgumentParser(description="Verifica la API de FIRMS contra la API real.")
    p.add_argument("lat", nargs="?", type=float, default=LAT_POR_DEFECTO)
    p.add_argument("lon", nargs="?", type=float, default=LON_POR_DEFECTO)
    p.add_argument("--radio", type=float, default=RADIO_KM_POR_DEFECTO, help="lado de la caja en km")
    p.add_argument("--dias", type=int, default=1, help="días hacia atrás (FIRMS admite 1..10)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    print("=" * 70)
    print("FIRMS (NASA) — incendios activos")
    print("=" * 70)
    print(f"punto: {args.lat}, {args.lon}   caja de {args.radio:.0f} km de lado")
    print(f"hoy:   {date.today().isoformat()}   (ayer: {(date.today() - timedelta(days=1)).isoformat()})")

    clave = _clave()
    if not clave:
        print("\nFALLO  no hay MAP_KEY.")
        print("  Añade a .env:  FIRMS_API_KEY=tu_clave")
        print("  (el nombre lleva API_KEY a propósito: así redact() la tapa sola)")
        print("  Se pide en https://firms.modaps.eosdis.nasa.gov/api/map_key/")
        return 1
    print(f"MAP_KEY: configurada ({len(clave)} caracteres)")

    disponibles = bloque_disponibilidad(clave, args.verbose)

    sensores = SENSORES_CANDIDATOS
    alguno, vivos = bloque_area(clave, caja(args.lat, args.lon, args.radio), sensores, args.dias, args.verbose)

    print("\n" + "=" * 70)
    if not alguno:
        print("VEREDICTO: FIRMS NO es utilizable ahora mismo.")
        print("  No escribas el módulo todavía: no hay nada demostrado sobre lo que construir.")
        return 1

    print(f"VEREDICTO: utilizable. Sensores que responden: {', '.join(vivos)}")
    if disponibles:
        print("  Un CSV vacío de un sensor CON datos hasta hoy = no hay fuego.")
        print("  Un CSV vacío de un sensor SIN datos de hoy    = no se sabe. No afirmar nada.")
    else:
        print("  AVISO: data_availability no contestó, así que HOY no se puede")
        print("  distinguir 'no hay fuego' de 'el satélite no ha pasado'. Sin esa")
        print("  distinción el módulo no debe afirmar que no hay incendios.")
    print("\n  Recuerda: esto ha corrido SIN el proxy de PythonAnywhere.")
    print("  firms.modaps.eosdis.nasa.gov NO está en su lista blanca.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
