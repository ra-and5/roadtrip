#!/usr/bin/env python3
"""Cuánto tarda cambiar de pantalla, y cuánto de eso es red y cuánto es pintar.

Es el número que pide el §2 de la Fase 7 **antes** de elegir cómo arreglarlo.
Sin él, la discusión entre precargar, cachear en memoria o montar una sola
página se decide por intuición, y la decisión 43 dejó claro cuánto vale la
intuición aquí: el contexto tardaba 34 s en el servidor y 0,00 s en el
portátil.

Por eso este guion mide **contra el servidor que se quiera**:

    python tools/medir_pantallas.py                     # el servidor de prueba local
    python tools/medir_pantallas.py --url https://…     # el desplegado, que es el que importa
    python tools/medir_pantallas.py --url https://… --pasadas 7

Con `--url` pide la contraseña de la app (o la lee de `ROADTRIP_PASSWORD`).
Medir el desplegado desde tu propio navegador es lo más cerca que se puede
estar del iPhone sin tener el iPhone delante: la latencia, el único worker y
el disco de red del servidor entran en el número; lo que no entra es la red
móvil.

Qué significa cada columna, que es lo que hace útil la tabla:

    html       lo que tarda el servidor en devolver el documento
    estáticos  el JavaScript y el CSS (en caliente sale ~0: los cachea el
               navegador, decisión 41)
    api        el `fetch` que hace la pantalla para traerse sus datos
    pintar     lo que va desde que llegan los datos hasta que se ven
    TOTAL      desde que se pulsa hasta que la pantalla dice algo

Y cada pantalla se mide dos veces:

    en frío     caché del navegador vacía: la primera visita del día
    en caliente ya has estado antes; es el caso normal al ir y volver

La diferencia entre las dos columnas **es** el ahorro máximo que puede dar
precargar o cachear. Si en caliente ya es rápido, el trabajo está en otro
sitio.

**Una trampa medida, no supuesta:** en local la columna `estáticos` sale casi
igual en frío que en caliente, y no es que la caché no funcione. El servidor de
desarrollo de Flask manda `Cache-Control: no-cache` en los estáticos
(comprobado con `curl -I`), así que el navegador revalida cada archivo en cada
navegación. En PythonAnywhere los sirve nginx **sin** `Cache-Control`, solo con
`Last-Modified` (decisión 41), y entonces no revalida nada. Es decir: el número
local de esa columna es un techo, y el bueno es el del desplegado.

Los dominios de fuera (los tiles del mapa) se bloquean por defecto: no son
nuestros, su latencia no la arregla ninguna de las tres opciones sobre la mesa
y mezclarla haría el número incomparable entre ejecuciones. Con `--con-tiles`
se incluyen.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from tools.servidor_de_prueba import CONTRASENA  # noqa: E402
from tools.verificar import BASE, Fallo, arrancar_servidor, esperar  # noqa: E402

# Qué se espera en cada pantalla para dar por "vista". No es el `load` del
# navegador: es el momento en que la pantalla contesta su pregunta, que es lo
# que percibe quien la abre.
PANTALLAS = [
    ("Inicio", "/", "#contexto-btn", None),
    ("Perfil", "/perfil", "#cuerpo-card:not([hidden])", "/api/perfil"),
    ("Mapa", "/mapa", "#progreso-cifras .marcador, #progreso-cifras div", "/api/ruta"),
    ("Chat", "/chat", "#chat-texto", "/api/chat"),
]


def _medir(page: Any, base: str, ruta: str, marcador: str, api: str | None) -> dict[str, float]:
    """Una visita, cronometrada desde dentro del navegador.

    Los tiempos salen de la Performance API y no de un cronómetro en Python: el
    de fuera incluiría el ida y vuelta del protocolo de Playwright, que no lo
    paga ningún usuario.
    """
    page.goto(f"{base}{ruta}", wait_until="commit")
    esperar(lambda: page.locator(marcador).count() > 0, f"{ruta} no llegó a pintar")

    # Además del marcador visible, se espera a que la llamada de la pantalla
    # haya terminado. El Chat enseña su caja de texto antes de traerse el
    # historial, así que sin esto se estaría midiendo media pantalla.
    if api:
        esperar(
            lambda: page.evaluate(
                "api => performance.getEntriesByType('resource')"
                ".some(r => r.name.includes(api))", api
            ),
            f"{ruta} no llegó a pedir {api}",
        )

    visto = page.evaluate("performance.now()")
    nav = page.evaluate(
        "() => { const n = performance.getEntriesByType('navigation')[0];"
        " return {inicio: n.requestStart, html: n.responseEnd}; }"
    )
    recursos = page.evaluate(
        """(api) => performance.getEntriesByType('resource').map(r => ({
             url: r.name, ini: r.startTime, fin: r.responseEnd,
           }))"""
        , api
    )

    estaticos = [r for r in recursos if "/static/" in r["url"]]
    llamadas = [r for r in recursos if api and api in r["url"]]

    html = nav["html"] - nav["inicio"]
    # SUMA de duraciones y no el intervalo del primero al último: en el Mapa,
    # Leaflet pide los iconos de las chinchetas mucho después de cargarse, así
    # que el intervalo incluía el arranque entero del mapa y hacía creer que la
    # culpa era de la red. Sumar exagera un poco (van en paralelo), pero no
    # mete dentro tiempo en el que no se estaba descargando nada.
    t_estaticos = sum(r["fin"] - r["ini"] for r in estaticos)
    t_api = max((r["fin"] - r["ini"] for r in llamadas), default=0.0)
    fin_datos = max((r["fin"] for r in llamadas), default=nav["html"])

    return {
        "html": html,
        "estaticos": t_estaticos,
        "api": t_api,
        # Lo que queda tras la última respuesta: parsear, construir el DOM y
        # dibujar. Es la parte que NO arregla ninguna caché de red.
        "pintar": max(visto - fin_datos, 0.0),
        "total": visto,
    }


def _mediana(muestras: list[dict[str, float]], clave: str) -> float:
    return statistics.median(m[clave] for m in muestras)


def _fila(nombre: str, muestras: list[dict[str, float]]) -> str:
    return (
        f"  {nombre:<12}"
        f"{_mediana(muestras, 'html'):7.0f}"
        f"{_mediana(muestras, 'estaticos'):11.0f}"
        f"{_mediana(muestras, 'api'):8.0f}"
        f"{_mediana(muestras, 'pintar'):9.0f}"
        f"{_mediana(muestras, 'total'):9.0f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cuánto tarda cambiar de pantalla.")
    parser.add_argument("--url", default="", help="servidor a medir (por defecto, uno local de prueba)")
    parser.add_argument("--pasadas", type=int, default=5, help="visitas por pantalla y estado")
    parser.add_argument("--con-tiles", action="store_true", help="no bloquear los dominios de fuera")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Falta Playwright:  pip install -r requirements-dev.txt"
              " && python -m playwright install chromium")
        return 1

    remoto = bool(args.url)
    base = args.url.rstrip("/") if remoto else BASE
    if remoto:
        clave = os.environ.get("ROADTRIP_PASSWORD") or getpass.getpass(
            f"Contraseña de {base}: "
        )
    else:
        clave = CONTRASENA

    servidor = None
    datos = None
    if not remoto:
        datos = Path(tempfile.mkdtemp(prefix="roadtrip-medida-"))
        servidor = arrancar_servidor(datos)

    try:
        with sync_playwright() as pw:
            navegador = pw.chromium.launch()
            contexto = navegador.new_context(
                locale="es-ES", timezone_id="Europe/Madrid",
                geolocation={"latitude": 43.5622, "longitude": -6.1456, "accuracy": 18},
                permissions=["geolocation"],
                viewport={"width": 414, "height": 896},
            )
            if not args.con_tiles:
                propio = base.split("//", 1)[-1].split("/", 1)[0]
                contexto.route(
                    "**/*",
                    lambda route: route.continue_()
                    if propio in route.request.url else route.abort(),
                )

            page = contexto.new_page()
            cdp = contexto.new_cdp_session(page)

            # El login va ANTES de imprimir la cabecera: una tabla con título y
            # sin filas parece que el servidor no contesta, cuando lo que pasa
            # es que la contraseña no era.
            try:
                page.goto(f"{base}/login", timeout=20000)
            except Exception as exc:  # noqa: BLE001
                print(f"\nNo se pudo abrir {base}/login: {type(exc).__name__}")
                print("¿Está el servidor levantado y bien escrita la URL?\n")
                return 1

            page.fill("#password", clave)
            page.click("button[type=submit]")
            try:
                esperar(lambda: page.locator("#contexto-btn").count() == 1,
                        "el servidor no llevó a la pantalla de Inicio")
            except Fallo:
                mala = "incorrecta" in page.content()
                print("\nNo se pudo entrar en " + base + ": "
                      + ("contraseña incorrecta." if mala
                         else "el login no llevó a Inicio."))
                if remoto:
                    print("La contraseña es la de la app; se puede dar sin que se vea con:")
                    print("  ROADTRIP_PASSWORD='…' python tools/medir_pantallas.py --url " + base)
                print()
                return 1

            print(f"\nCambiar de pantalla — {base}")
            print(f"mediana de {args.pasadas} pasadas, en milisegundos"
                  + ("" if args.con_tiles else ", sin los tiles de fuera"))
            print("las columnas NO suman el TOTAL: se solapan entre ellas")
            print("=" * 62)

            for estado in ("en frío", "en caliente"):
                print(f"\n{estado}"
                      + ("   (caché del navegador vacía)" if estado == "en frío" else ""))
                print(f"  {'pantalla':<12}{'html':>7}{'estáticos':>11}"
                      f"{'api':>8}{'pintar':>9}{'TOTAL':>9}")

                for nombre, ruta, marcador, api in PANTALLAS:
                    muestras = []
                    for _ in range(args.pasadas):
                        if estado == "en frío":
                            # Sin esto la segunda pasada ya trae el JavaScript
                            # cacheado y "en frío" mediría otra cosa.
                            cdp.send("Network.clearBrowserCache")
                        muestras.append(_medir(page, base, ruta, marcador, api))
                    print(_fila(nombre, muestras))

            navegador.close()
    finally:
        if servidor is not None:
            servidor.terminate()
            try:
                servidor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                servidor.kill()
        if datos is not None:
            shutil.rmtree(datos, ignore_errors=True)

    print("\n" + "=" * 62)
    print("Cómo se lee: si 'en caliente' ya es rápido, precargar no arregla nada")
    print("y el trabajo está en 'api' o en 'pintar'. Si la diferencia entre frío")
    print("y caliente es grande, ahí está el margen de una caché.")
    print("Y estos números son los del sitio donde se ejecuta: mídelo contra el")
    print("servidor desplegado antes de decidir (decisión 43).")
    if not remoto:
        print("\nOJO: en local Flask manda 'Cache-Control: no-cache' en los estáticos, así")
        print("que 'en caliente' sigue pagando una revalidación por archivo. En el")
        print("servidor los sirve nginx sin esa cabecera y no revalida: esa columna solo")
        print("significa algo medida contra el desplegado.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
