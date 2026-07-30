"""Recorre las cuatro pantallas en un navegador de verdad y dice qué se ha roto.

Uso:
    python tools/verificar.py            # headless, es lo normal
    python tools/verificar.py --ver      # con ventana, para mirarlo
    python tools/verificar.py --lento    # ralentizado, para depurar
    python tools/verificar.py --solo mapa

Código de salida: 0 si todo pasa, 1 si algo falla. Pensado para correrlo ANTES
de desplegar.

**Por qué existe.** El 29-07-2026 el botón principal de la app estuvo muerto:
`hideAll()` escondía un id que ya no existía, así que pulsar *¿Dónde estoy?*
lanzaba un `TypeError` y dejaba el botón bloqueado sin un solo mensaje. Los 534
tests pasaban, porque eran todos de Python y el HTML lo pinta el navegador
(decisión 42). Esa clase de fallo no se caza con más tests unitarios: se caza
abriendo la página.

De ahí sale la comprobación que va en TODAS las pantallas y que no hace falta
prever: **cero excepciones de JavaScript**. Un id huérfano, un `null` al pintar
o un renombrado a medias revientan ahí sin que nadie haya escrito un test para
ese caso concreto.

**Sin red y sin API keys** (§2 del `CLAUDE.md`). Dos capas, y las dos hacen
falta:

  - el servidor de prueba dobla `requests` y el proveedor de LLM;
  - el navegador **aborta toda petición que no vaya a 127.0.0.1**, así que los
    tiles de OpenStreetMap no cargan. Eso no es una molestia que se tolera: es
    la comprobación de que el mapa avisa y sigue enseñando las chinchetas con
    mala cobertura (decisión 28).

**Lo que este guion NO comprueba, y conviene saberlo antes de fiarse:** corre en
Chromium de escritorio, así que no dice nada del GPS real de iOS, ni de la purga
de IndexedDB a los siete días, ni de lo que tarda la app en el servidor con un
solo worker (decisión 43: eso se mide en el servidor, no aquí). Cierra la
distancia entre "la suite pasa" y "la página funciona", no la de "funciona en
mi portátil" a "funciona en el iPhone".
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from tools.servidor_de_prueba import CONTRASENA, LAT, LON, TOKEN_INGESTA  # noqa: E402

PUERTO = 5099
BASE = f"http://127.0.0.1:{PUERTO}"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

class Fallo(AssertionError):
    """Una comprobación no se cumple. Lleva el motivo escrito."""


def esperar(condicion: Callable[[], Any], que: str, segundos: float = 10.0) -> Any:
    """Espera a que algo sea cierto. Devuelve lo que devuelva la condición.

    Se sondea en vez de usar los `expect` de Playwright para que el mensaje de
    fallo lo escriba quien llama: "el mapa no llegó a pintar ninguna chincheta"
    dice qué mirar; un timeout genérico de un selector, no.
    """
    limite = time.monotonic() + segundos
    ultimo: Any = None
    while time.monotonic() < limite:
        try:
            ultimo = condicion()
        except Exception:  # noqa: BLE001  el DOM puede estar a medio pintar
            ultimo = None
        if ultimo:
            return ultimo
        time.sleep(0.1)
    raise Fallo(f"se agotó la espera de {segundos:.0f} s: {que}")


def texto(page: Any, id_: str) -> str:
    return page.inner_text(f"#{id_}").strip()


class Errores:
    """Recoge lo que el navegador escupe por consola, por pantalla.

    Se distingue una excepción de JavaScript (`pageerror`) de un mensaje de
    error en consola, porque no valen lo mismo: la primera para la ejecución y
    es lo que dejó el botón muerto; la segunda puede ser un recurso externo que
    hemos bloqueado a propósito.
    """

    def __init__(self) -> None:
        self.excepciones: list[str] = []
        self.consola: list[tuple[str, str]] = []   # (mensaje, url de origen)

    def escuchar(self, page: Any) -> None:
        page.on("pageerror", lambda exc: self.excepciones.append(str(exc)))
        page.on("console", self._consola)

    def _consola(self, msg: Any) -> None:
        if msg.type != "error":
            return
        origen = (msg.location or {}).get("url", "")
        self.consola.append((msg.text, origen))

    def limpiar(self) -> None:
        self.excepciones.clear()
        self.consola.clear()

    def descartar(self, fragmento: str) -> None:
        """Olvida los errores de consola que ha provocado el propio guion.

        Dos comprobaciones rompen la red y el servidor a propósito (la cola
        offline y el reintento del Perfil), y el navegador los anota como
        errores. Se descartan por su texto exacto y solo esos: silenciar la
        consola entera para que no molesten dejaría de cazar la clase de fallo
        por la que existe este archivo.
        """
        self.consola = [(m, u) for m, u in self.consola if fragmento not in m]

    def descartar_de(self, origen: str) -> None:
        """Lo mismo, pero por la PÁGINA que los emitió.

        Hace falta para el 401 del login, que se provoca a propósito probando
        una contraseña mala. Por texto no se puede descartar: «Failed to load
        resource … 401» es lo que escribiría también una sesión caducada en
        cualquier otra pantalla, y taparlo ahí sería esconder un fallo de
        verdad.
        """
        self.consola = [(m, u) for m, u in self.consola if origen not in u]

    def propios(self) -> list[str]:
        """Los errores de consola que NO vienen de un recurso externo bloqueado.

        Se filtra por la URL de origen y no por el texto del mensaje: los tiles
        del mapa están abortados a propósito y su error es literalmente el mismo
        texto ("Failed to load resource") que daría un 500 nuestro. Filtrar por
        texto habría escondido el segundo para callar el primero.
        """
        # El mensaje sale con su URL pegada: "Failed to load resource" a secas no
        # dice qué recurso, y era justo lo que había que averiguar a mano cada
        # vez que saltaba.
        return [
            f"{mensaje}  [{origen or 'sin origen'}]"
            for mensaje, origen in self.consola
            if not origen or "127.0.0.1" in origen
        ]


# ---------------------------------------------------------------------------
# El servidor
# ---------------------------------------------------------------------------

def puerto_ocupado() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PUERTO)) == 0


def arrancar_servidor(datos: Path) -> subprocess.Popen[bytes]:
    # El puerto se mira ANTES de arrancar. Si ya hay algo escuchando —una
    # verificación anterior que no acabó de morir—, el hijo muere con «Address
    # already in use» y el bucle de abajo daría por bueno al servidor VIEJO: su
    # base de datos y, lo que de verdad importa, SU CÓDIGO. Entonces esto deja de
    # verificar lo que hay en disco, y no da ningún error — sale verde sobre la
    # versión de antes, o rojo por datos que no son los sembrados.
    if puerto_ocupado():
        raise Fallo(
            f"el puerto {PUERTO} ya está ocupado: hay otra verificación "
            f"corriendo o una anterior dejó el servidor vivo.\n"
            f"     Suéltalo con:  pkill -f servidor_de_prueba.py"
        )

    proceso = subprocess.Popen(
        [sys.executable, str(RAIZ / "tools" / "servidor_de_prueba.py"),
         "--puerto", str(PUERTO), "--datos", str(datos)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    limite = time.monotonic() + 20
    while time.monotonic() < limite:
        if proceso.poll() is not None:
            salida = (proceso.stderr.read() if proceso.stderr else b"").decode()
            raise Fallo(f"el servidor de prueba murió al arrancar:\n{salida}")
        if puerto_ocupado():
            return proceso
        time.sleep(0.2)

    proceso.kill()
    raise Fallo(f"el servidor de prueba no respondió en {BASE}")


def sesion_http() -> Any:
    """Una sesión de `requests` autenticada, para contar filas desde fuera.

    El navegador no sirve para esto: cuando se prueba la cola offline está
    justamente sin red, y lo que hace falta saber entonces es qué tiene el
    servidor. Preguntárselo por otro camino es lo único que distingue "se
    guardó" de "parece que se guardó".
    """
    import requests

    sesion = requests.Session()
    respuesta = sesion.post(f"{BASE}/login", data={"password": CONTRASENA}, timeout=10)
    if respuesta.status_code >= 400:
        raise Fallo(f"no se pudo entrar por HTTP ({respuesta.status_code})")
    return sesion


def notas_en_servidor(sesion: Any) -> int:
    # `total` y no `len(notes)`: la lista viene filtrada por año y el total no.
    return int(sesion.get(f"{BASE}/api/notes", timeout=10).json()["total"])


# ---------------------------------------------------------------------------
# Comprobaciones: Inicio
# ---------------------------------------------------------------------------

def entrar(page: Any, errores: Errores) -> str:
    page.goto(f"{BASE}/login")
    page.fill("#password", "esta-no-es")
    page.click("button[type=submit]")
    esperar(lambda: "incorrecta" in page.content(), "la contraseña mala no dio error")
    # El 401 de la contraseña mala lo provoca este guion. En la pasada completa
    # se lo tragaba por accidente el `limpiar()` de la cola offline; con `--solo`
    # salía como fallo de la pantalla que tocara, que es un aviso falso justo en
    # la herramienta que existe para que los avisos signifiquen algo.
    errores.descartar_de("/login")

    page.fill("#password", CONTRASENA)
    page.click("button[type=submit]")
    esperar(lambda: page.locator("#contexto-btn").count() == 1, "no se llegó a Inicio")
    return "login rechaza la mala y acepta la buena"


def inicio_contexto(page: Any) -> str:
    page.click("#contexto-btn")
    esperar(lambda: texto(page, "place-label"), "el botón «¿Dónde estoy?» no pintó el sitio")

    lugar = texto(page, "place-label")
    if "Cudillero" not in lugar:
        raise Fallo(f"el sitio no es el esperado: {lugar!r}")
    if not page.locator("#weather-card").is_visible():
        raise Fallo("no salió la tarjeta del tiempo")
    if not page.locator("#luna-card").is_visible():
        raise Fallo("no salió la tarjeta de la luna")
    if page.locator("#contexto-btn").is_disabled():
        raise Fallo("el botón se quedó deshabilitado (es el fallo de la decisión 42)")

    # La altitud sale de Open-Meteo y la luna se calcula en local: si alguna de
    # las dos se queda vacía, algo se ha desconectado por el camino.
    if "47" not in texto(page, "place-altitud"):
        raise Fallo("no se pintó la altitud que da Open-Meteo")
    if "%" not in texto(page, "luna-fase"):
        raise Fallo("la fase de la luna salió sin iluminación")

    return f"{lugar} · {texto(page, 'weather-summary')[:38]}…"


def inicio_recomendacion(page: Any) -> str:
    page.click("#locate-btn")
    esperar(lambda: page.locator("#reco-card").is_visible(), "no salió la recomendación")

    actividades = page.locator("#reco-activities article").count()
    if actividades != 2:
        raise Fallo(f"se esperaban 2 actividades del proveedor falso, salieron {actividades}")

    # La distinción entre lo verificado en el mapa y lo que se sabe de memoria
    # es lo que hace fiable una recomendación (decisión 33): tiene que llegar
    # hasta la pantalla.
    marcas = page.inner_text("#reco-activities")
    if "verificado en el mapa" not in marcas or "sugerencia general" not in marcas:
        raise Fallo("no se distingue lo verificado en el mapa de lo general")
    if not page.locator("#refresh-btn").is_visible():
        raise Fallo("no apareció el botón de generar otra")

    return f"{actividades} actividades · {texto(page, 'reco-meta')}"


def inicio_pois(page: Any) -> str:
    page.click("#pois-btn")
    esperar(lambda: page.locator("#pois-detalle").is_visible(), "no salieron los POIs")

    cuantos = int(texto(page, "pois-count"))
    if cuantos != 4:
        raise Fallo(f"se esperaban 4 POIs con nombre, salieron {cuantos}")

    # Cada punto es un enlace a Google Maps con COORDENADAS, no con el nombre
    # (decisión 32). Que apunte al sitio equivocado no daría ningún error.
    enlace = page.locator("#pois-list a").first.get_attribute("href")
    if "google.com/maps/dir/?api=1&destination=43.5" not in (enlace or ""):
        raise Fallo(f"el enlace del POI no lleva coordenadas: {enlace!r}")

    return f"{cuantos} puntos, con enlace a Mapas"


def inicio_nota_offline(page: Any, sesion: Any, errores: Errores) -> str:
    """Los cuatro caminos de la cola: sin red, reintento, duplicada y rechazada."""
    contexto = page.context
    antes = notas_en_servidor(sesion)

    # 1. Sin red: se guarda en IndexedDB y se dice que está guardada.
    contexto.set_offline(True)
    page.fill("#nota-texto", "Nota escrita sin cobertura")
    page.click("#nota-guardar")
    esperar(
        lambda: "1 nota por enviar" in texto(page, "nota-cola-resumen"),
        "la nota sin red no quedó en la cola",
    )
    if notas_en_servidor(sesion) != antes:
        raise Fallo("la nota llegó al servidor estando sin red")

    # 2. Vuelve la conexión: se envía sola.
    contexto.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    esperar(
        lambda: notas_en_servidor(sesion) == antes + 1,
        "la nota no se envió al volver la conexión",
    )
    esperar(lambda: not page.locator("#nota-cola").is_visible(), "la cola no se vació")

    # 3. Un reintento con un client_id que ya existe: el servidor dice
    #    "duplicada", la cola la borra y el total NO sube. Es el caso normal
    #    cuando el POST llegó y la respuesta se perdió en un túnel.
    repetida = page.evaluate(
        """async () => {
          const db = await new Promise((ok, ko) => {
            const p = indexedDB.open("roadtrip", 1);
            p.onsuccess = () => ok(p.result); p.onerror = () => ko(p.error);
          });
          const cid = "00000000-0000-4000-8000-000000000001";
          await new Promise((ok, ko) => {
            const tx = db.transaction("cola", "readwrite");
            tx.objectStore("cola").put({
              client_id: cid, text: "reintento de una que ya está",
              lat: 43.5622, lon: -6.1456, created_at: new Date().toISOString(),
              estado: "pendiente",
            });
            tx.oncomplete = ok; tx.onerror = () => ko(tx.error);
          });
          db.close();
          return cid;
        }"""
    )
    page.evaluate("window.dispatchEvent(new Event('online'))")
    esperar(lambda: not page.locator("#nota-cola").is_visible(), "la duplicada no salió de la cola")
    if notas_en_servidor(sesion) != antes + 1:
        raise Fallo(f"una nota duplicada ({repetida}) subió el total del servidor")

    # 4. Una nota inválida: se marca rechazada y NO se reintenta nunca más, o
    #    atascaría la cola detrás de ella (decisión 26).
    page.evaluate(
        """async () => {
          const db = await new Promise((ok, ko) => {
            const p = indexedDB.open("roadtrip", 1);
            p.onsuccess = () => ok(p.result); p.onerror = () => ko(p.error);
          });
          await new Promise((ok, ko) => {
            const tx = db.transaction("cola", "readwrite");
            tx.objectStore("cola").put({
              client_id: "11111111-1111-4111-8111-111111111111",
              text: "coordenadas imposibles", lat: 999, lon: 999,
              created_at: new Date().toISOString(), estado: "pendiente",
            });
            tx.oncomplete = ok; tx.onerror = () => ko(tx.error);
          });
          db.close();
        }"""
    )
    # Se recarga en vez de disparar `online` otra vez: abrir la app es el
    # disparador que de verdad recupera la cola (decisión 26), y dos eventos
    # `online` seguidos se solapan con la sincronización que aún está en curso.
    page.reload()
    esperar(
        lambda: "rechazada por el servidor" in texto(page, "nota-cola-resumen"),
        "la nota inválida no se marcó como rechazada",
    )

    page.reload()
    esperar(
        lambda: "rechazada por el servidor" in texto(page, "nota-cola-resumen"),
        "la rechazada desapareció al recargar",
    )
    if notas_en_servidor(sesion) != antes + 1:
        raise Fallo("una nota rechazada acabó entrando al recargar")

    # El corte de red y el 400 los ha provocado esta comprobación.
    errores.descartar("ERR_INTERNET_DISCONNECTED")
    errores.descartar("400 (BAD REQUEST)")
    return f"cuatro caminos: {antes} notas -> {antes + 1}, 1 rechazada retenida"


# ---------------------------------------------------------------------------
# Comprobaciones: Perfil, Mapa, Chat
# ---------------------------------------------------------------------------

def perfil(page: Any) -> str:
    page.goto(f"{BASE}/perfil")
    esperar(lambda: page.locator("#cuerpo-card").is_visible(), "el perfil no pintó el cuerpo")
    esperar(lambda: page.locator("#fuentes-lista li").count() > 0, "no salió ninguna fuente")

    # La fiabilidad va JUNTO al dato (decisión 40): sin el veredicto, la
    # pantalla enseña cifras que no se sabe si valen.
    fuentes = page.inner_text("#fuentes-lista")
    if not any(v in fuentes for v in ("demostrada", "huecos", "sin datos", "simulad")):
        raise Fallo(f"las fuentes salen sin veredicto: {fuentes[:120]!r}")

    # Hay telemetría simulada sembrada: el aviso es obligatorio, o la pantalla
    # certifica como medido algo que nos hemos inventado (decisión 36).
    if not page.locator("#cuerpo-simulado").is_visible():
        raise Fallo("hay datos simulados y no sale el aviso de que lo son")

    # Las barras son el dato, no un adorno: el veredicto puede salir perfecto y
    # la serie no pintarse, que es exactamente el síntoma con el que se abre una
    # sesión de depuración ("no se actualizan las barras"). Se sembraron tres
    # días con pasos, así que hoy tiene que traer una cifra y no un hueco.
    barras = page.locator("#cuerpo-barras .barra")
    if barras.count() != 7:
        raise Fallo(f"la serie tiene {barras.count()} barras y son 7 días")
    hoy = page.locator("#cuerpo-barras .barra-hoy .barra-valor").inner_text()
    if "k" not in hoy:
        raise Fallo(f"hoy hay muestras sembradas y la barra dice {hoy!r}")
    if "pasos hoy" not in page.inner_text("#cuerpo-hoy"):
        raise Fallo(f"el titular no da los pasos: {page.inner_text('#cuerpo-hoy')!r}")

    # Perfil no puede repetir lo que ya enseñan Inicio o el Mapa.
    cuerpo = page.inner_text("body")
    for intruso in ("Deportes de agua", "comunidades", "kilómetros"):
        if intruso in cuerpo:
            raise Fallo(f"el Perfil repite algo de otra pantalla: {intruso!r}")

    return f"{page.locator('#fuentes-lista li').count()} fuentes con veredicto"


def perfil_reintento(page: Any, errores: Errores) -> str:
    """Un 5xx suelto se reintenta solo; el usuario no debería ni enterarse.

    Es el camino que existe porque en el plan gratuito hay UN worker y una
    pantalla lenta hace fallar a la siguiente (decisión 43).
    """
    fallos = {"n": 0}

    def responder(route: Any) -> None:
        if fallos["n"] == 0:
            fallos["n"] += 1
            route.fulfill(status=503, content_type="application/json", body='{"error":"ocupado"}')
        else:
            route.continue_()

    page.route("**/api/perfil*", responder)
    try:
        page.goto(f"{BASE}/perfil")
        esperar(
            lambda: page.locator("#cuerpo-card").is_visible(),
            "el perfil no se recuperó de un 503",
            segundos=15,
        )
    finally:
        page.unroute("**/api/perfil*")

    if fallos["n"] != 1:
        raise Fallo("el 503 no llegó a provocarse")

    errores.descartar("503 (Service Unavailable)")
    return "un 503 se reintenta solo y la pantalla sale igual"


def mapa(page: Any) -> str:
    page.goto(f"{BASE}/mapa")
    esperar(lambda: page.locator("#progreso-cifras").inner_text().strip(), "no salió el progreso")
    esperar(lambda: page.locator("#dias-lista").inner_text().strip(), "no salió la línea de días")

    cifras = page.inner_text("#progreso-cifras")
    if "km" not in cifras:
        raise Fallo(f"el progreso no enseña kilómetros: {cifras[:120]!r}")
    if not page.locator("#tablero-casillas li").count():
        raise Fallo("el tablero de comunidades salió vacío")

    chinchetas = page.locator(".leaflet-marker-icon").count()
    if chinchetas == 0:
        raise Fallo("Leaflet no pintó ninguna chincheta")

    # Sin red no cargan los tiles y el mapa sale gris: lo que no puede pasar es
    # que se calle (decisión 28). Las chinchetas de arriba prueban la otra
    # mitad: siguen ahí, porque salen de nuestro servidor.
    esperar(
        lambda: page.locator("#mapa-aviso").is_visible(),
        "los tiles no cargaron y el mapa no avisó",
        segundos=15,
    )

    return f"{chinchetas} chinchetas, tiles caídos y avisados"


def mapa_filtro(page: Any) -> str:
    opciones = page.locator("#filtro-anio option").count()
    if opciones < 2:
        raise Fallo(f"el filtro de años solo tiene {opciones} opción(es); se sembraron dos años")

    antes = page.inner_text("#progreso-cifras")
    valores = page.locator("#filtro-anio option").evaluate_all(
        "nodos => nodos.map(n => n.value)"
    )
    anio = next((v for v in valores if v and v != "todos"), None)
    if anio is None:
        raise Fallo("el filtro no ofrece ningún año concreto")

    page.select_option("#filtro-anio", anio)
    esperar(
        lambda: page.inner_text("#progreso-cifras") != antes,
        f"filtrar por {anio} no cambió las cifras",
    )
    return f"{opciones} opciones; filtrar por {anio} cambia las cifras"


def mapa_revivir(page: Any) -> str:
    page.select_option("#filtro-anio", "")   # "" es «todos los años»
    esperar(lambda: page.locator("#revivir-slider").count() == 1, "no hay control de revivir")

    page.click("#revivir-btn")
    esperar(
        lambda: int(page.input_value("#revivir-slider") or 0) > 0,
        "«revivir el viaje» no movió el recorrido",
        segundos=15,
    )
    esperar(lambda: texto(page, "revivir-pie"), "revivir no dice en qué momento va")
    return f"avanza hasta el momento {page.input_value('#revivir-slider')}"


def mapa_album_de_fotos(page: Any) -> str:
    """El álbum es un ESTADO: lo que entra sale en el mapa, y lo que se quita
    desaparece.

    Es el camino que de verdad se usa —el atajo del iPhone manda el álbum
    entero— y hasta ahora solo lo cubrían tests de Python. Lo que no probaba
    nadie es la mitad que se ve: que el mapa **repinte** después. Un borrado
    correcto en la base de datos y una chincheta que sigue ahí se ven igual de
    bien desde el servidor, y son cosas distintas (decisión 45).
    """
    import requests

    cabeceras = {"Authorization": f"Bearer {TOKEN_INGESTA}"}

    def enviar_album(archivos: list[str]) -> dict[str, Any]:
        puntos = [
            {"archivo": nombre, "capturado_en": f"2026-07-2{i}T12:00:00",
             "lat": 43.5622 + i * 0.001, "lon": -6.1456}
            for i, nombre in enumerate(archivos, start=1)
        ]
        respuesta = requests.post(
            f"{BASE}/api/waypoints",
            # "fotos" es la única fuente que acepta el endpoint, así que este
            # envío manda sobre las fotos sembradas: con `completo` se las
            # lleva por delante, que es exactamente lo que hace el atajo.
            json={"fuente": "fotos", "puntos": puntos, "completo": True},
            headers=cabeceras, timeout=15,
        )
        if respuesta.status_code >= 400:
            raise Fallo(f"el envío del álbum falló: {respuesta.status_code} {respuesta.text[:120]}")
        return respuesta.json()

    def fotos_en_el_mapa() -> int:
        page.goto(f"{BASE}/mapa")
        esperar(lambda: page.inner_text("#dias-lista").strip(), "el mapa no pintó los días")
        return page.inner_text("#dias-lista").count("VERIFICACION_")

    alta = enviar_album(["VERIFICACION_1.jpeg", "VERIFICACION_2.jpeg"])
    if alta.get("guardados") != 2:
        raise Fallo(f"el álbum no guardó las dos fotos: {alta}")
    if fotos_en_el_mapa() != 2:
        raise Fallo("las fotos enviadas no aparecen en el mapa")

    # Reenviar el álbum entero es lo normal (el atajo lo manda completo cada
    # vez): tiene que dejar el viaje igual, no duplicarlo.
    repetido = enviar_album(["VERIFICACION_1.jpeg", "VERIFICACION_2.jpeg"])
    if repetido.get("duplicados") != 2 or fotos_en_el_mapa() != 2:
        raise Fallo(f"reenviar el álbum cambió el mapa: {repetido}")

    # Y quitar una del álbum tiene que quitarla del mapa. Esta es la mitad que
    # no existía hasta la decisión 45: un `INSERT OR IGNORE` nunca borra.
    quitada = enviar_album(["VERIFICACION_1.jpeg"])
    if quitada.get("eliminados") != 1:
        raise Fallo(f"quitar una foto del álbum no la borró: {quitada}")
    if fotos_en_el_mapa() != 1:
        raise Fallo("la foto quitada del álbum sigue en el mapa")

    # Se deja el álbum como se sembró. Esta comprobación va la última del Mapa
    # justamente por esto: `completo` borra lo que no viene, así que cualquier
    # cosa que cuente chinchetas tiene que ir antes o volver a sembrar.
    enviar_album(["IMG_4736", "IMG_4737", "IMG_4738", "IMG_4739"])
    return "2 fotos entran, reenviar no duplica, quitar una la borra del mapa"


def chat(page: Any) -> str:
    page.goto(f"{BASE}/chat")
    esperar(lambda: page.locator("#chat-texto").count() == 1, "no cargó el chat")

    page.fill("#chat-texto", "¿Qué hago esta tarde?")
    page.click("#chat-enviar")
    esperar(
        lambda: "mirador" in page.inner_text("#chat-hilo"),
        "el chat no pintó la respuesta del proveedor falso",
        segundos=20,
    )

    # Guardar y enviar son cosas distintas (decisión 37): la conversación tiene
    # que seguir ahí al recargar, sin volver a llamar al modelo.
    page.reload()
    esperar(
        lambda: "mirador" in page.inner_text("#chat-hilo"),
        "la conversación no sobrevivió a recargar",
    )

    page.click("#chat-borrar")
    esperar(
        lambda: not page.inner_text("#chat-hilo").strip(),
        "borrar la conversación no la borró",
    )
    return "pregunta, respuesta, persiste al recargar y se puede borrar"


def api_sin_cache(sesion: Any) -> str:
    """Ninguna respuesta de `/api/` puede cachearse (decisión 41).

    Se comprueba desde fuera del navegador a propósito: es una cabecera del
    servidor, y si falta el síntoma es una pantalla que enseña lo de antes sin
    dar ningún error.
    """
    faltan = []
    for ruta in ("/api/perfil", "/api/notes", "/api/ruta"):
        cabecera = sesion.get(f"{BASE}{ruta}", timeout=10).headers.get("Cache-Control", "")
        if "no-store" not in cabecera:
            faltan.append(f"{ruta} -> {cabecera or 'sin cabecera'}")
    if faltan:
        raise Fallo("respuestas de la API cacheables: " + "; ".join(faltan))
    return "no-store en las tres rutas comprobadas"


def estaticos_versionados(page: Any) -> str:
    """El HTML y el JavaScript sí se cachean, así que hay que poder invalidarlos.

    Sin la query, Safari puede seguir ejecutando el JavaScript de antes días
    después de desplegar, y entonces se depura el despliegue en vez del código
    (decisión 41).
    """
    page.goto(f"{BASE}/")
    fuentes = page.locator("script[src], link[href]").evaluate_all(
        "nodos => nodos.map(n => n.src || n.href)"
    )
    propios = [u for u in fuentes if "/static/" in u]
    sin_version = [u for u in propios if "?v=" not in u]
    if sin_version:
        raise Fallo("estáticos sin ?v=: " + ", ".join(u.rsplit("/", 1)[-1] for u in sin_version))
    return f"{len(propios)} estáticos con ?v=<mtime>"


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

class Corredor:
    """Ejecuta comprobaciones y las imprime como `tools/diagnostico.py`."""

    def __init__(self, errores: Errores, verboso: bool) -> None:
        self.errores = errores
        self.verboso = verboso
        self.fallos: list[str] = []

    def bloque(self, titulo: str) -> None:
        print(f"\n{titulo}")

    def check(self, nombre: str, fn: Callable[[], str]) -> bool:
        print(f"  {nombre:.<40}", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            detalle = fn() or ""
        except Exception as exc:  # noqa: BLE001
            print(f"FALLO  ({time.monotonic() - t0:.1f}s)")
            print(f"     {type(exc).__name__}: {exc}")
            if self.verboso:
                traceback.print_exc()
            self.fallos.append(nombre)
            return False
        print(f"OK     ({time.monotonic() - t0:.1f}s)  {detalle}")
        return True

    def sin_errores_de_js(self, pantalla: str) -> None:
        """La comprobación que caza los fallos que nadie previó."""
        excepciones = list(self.errores.excepciones)
        consola = self.errores.propios()
        self.errores.limpiar()

        def _comprobar() -> str:
            if excepciones:
                raise Fallo(f"excepción de JavaScript: {excepciones[0]}")
            if consola:
                raise Fallo(f"error en consola: {consola[0]}")
            return "sin excepciones ni errores de consola"

        self.check(f"{pantalla}: JavaScript limpio", _comprobar)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificación de las cuatro pantallas.")
    parser.add_argument("--ver", action="store_true", help="con ventana, no headless")
    parser.add_argument("--lento", action="store_true", help="ralentiza cada acción")
    parser.add_argument("--solo", default="", help="inicio | perfil | mapa | chat")
    parser.add_argument("-v", "--verboso", action="store_true", help="traza completa")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Falta Playwright. Instálalo con:\n"
              "  pip install -r requirements-dev.txt\n"
              "  python -m playwright install chromium")
        return 1

    quiere = (lambda n: not args.solo or args.solo == n)

    datos = Path(tempfile.mkdtemp(prefix="roadtrip-verificacion-"))
    print(f"\nVerificación en el navegador   ({BASE}, datos en {datos})")
    print("=" * 72)

    # No arrancar es un fallo del entorno, no de la app: sale con su motivo y
    # sin traza, como el medidor de pantallas. Una traza aquí hace buscar el
    # error en el código que se iba a verificar, que es el sitio equivocado.
    try:
        servidor = arrancar_servidor(datos)
    except Fallo as error:
        shutil.rmtree(datos, ignore_errors=True)
        print(f"\nNo se pudo arrancar: {error}\n")
        return 1

    errores = Errores()
    corredor = Corredor(errores, args.verboso)

    try:
        sesion = sesion_http()
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(
                headless=not args.ver, slow_mo=250 if args.lento else 0,
                proxy={"server": "per-context"},
            )
            contexto = navegador.new_context(
                locale="es-ES",
                timezone_id="Europe/Madrid",
                # El GPS que iOS da de verdad y que ningún test de Python puede
                # ejercitar. Sin permiso concedido, la pantalla principal se
                # queda esperando un fix que no llega.
                geolocation={"latitude": LAT, "longitude": LON, "accuracy": 18},
                permissions=["geolocation"],
                viewport={"width": 414, "height": 896},  # tamaño de iPhone
                # Nada sale de esta máquina: todo lo que no sea 127.0.0.1 va a un
                # proxy que no escucha nadie. Si algún día una pantalla depende de
                # un CDN, esto lo convierte en un fallo visible en vez de en una
                # app que solo funciona con cobertura.
                #
                # Se bloquea con un proxy y no interceptando peticiones porque
                # **una sola ruta desactiva la caché HTTP del contexto entero**
                # (medido: en caliente pasaba de 0 a 205.913 bytes). Con
                # interceptación, la app se comportaba aquí de una forma que no
                # tiene en ningún navegador de verdad.
                proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"},
            )

            page = contexto.new_page()
            errores.escuchar(page)

            corredor.bloque("ARRANQUE")
            corredor.check("login", lambda: entrar(page, errores))
            corredor.check("estáticos versionados", lambda: estaticos_versionados(page))
            corredor.check("la API no se cachea", lambda: api_sin_cache(sesion))

            if quiere("inicio"):
                corredor.bloque("INICIO   (¿qué hago aquí, ahora?)")
                page.goto(f"{BASE}/")
                errores.limpiar()
                corredor.check("¿Dónde estoy?", lambda: inicio_contexto(page))
                corredor.check("Recomiéndame algo", lambda: inicio_recomendacion(page))
                corredor.check("Buscar sitios cerca", lambda: inicio_pois(page))
                corredor.check("nota con la cola offline",
                               lambda: inicio_nota_offline(page, sesion, errores))
                corredor.sin_errores_de_js("Inicio")

            if quiere("perfil"):
                corredor.bloque("PERFIL   (¿cómo estoy, y de qué me fío?)")
                corredor.check("carga y veredictos", lambda: perfil(page))
                corredor.check("reintento tras un 503",
                               lambda: perfil_reintento(page, errores))
                corredor.sin_errores_de_js("Perfil")

            if quiere("mapa"):
                corredor.bloque("MAPA   (¿dónde he estado?)")
                corredor.check("trayecto y progreso", lambda: mapa(page))
                corredor.check("filtro por año", lambda: mapa_filtro(page))
                corredor.check("revivir el viaje", lambda: mapa_revivir(page))
                corredor.check("álbum de fotos, entrar y salir",
                               lambda: mapa_album_de_fotos(page))
                corredor.sin_errores_de_js("Mapa")

            if quiere("chat"):
                corredor.bloque("CHAT   (preguntar en vez de buscar)")
                corredor.check("preguntar, guardar y borrar", lambda: chat(page))
                corredor.sin_errores_de_js("Chat")

            navegador.close()
    finally:
        servidor.terminate()
        try:
            servidor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            servidor.kill()
        shutil.rmtree(datos, ignore_errors=True)

    print("\n" + "=" * 72)
    if corredor.fallos:
        print(f"FALLA: {len(corredor.fallos)} comprobación(es) -> "
              + ", ".join(corredor.fallos))
        print("No despliegues sin arreglarlo.\n")
        return 1
    print("Todo en verde. Las cuatro pantallas responden en un navegador de verdad.")
    print("Ojo: esto no prueba el GPS de iOS, ni IndexedDB a los 7 días, ni lo que")
    print("tarda en el servidor con un solo worker.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
