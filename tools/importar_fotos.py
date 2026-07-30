"""Lee los metadatos de una carpeta de fotos y los convierte en ruta del viaje.

Uso:
    python tools/importar_fotos.py ~/Fotos/viaje              # solo mira e informa
    python tools/importar_fotos.py ~/Fotos/viaje --importar   # guarda en la BD local
    python tools/importar_fotos.py ~/Fotos/viaje --enviar https://tuapp.pythonanywhere.com
    python tools/importar_fotos.py ~/Fotos/viaje --detalle    # foto a foto
    python tools/importar_fotos.py --limpiar                  # vacía los puntos locales

**Por defecto no guarda nada.** Solo mira las fotos y te dice qué traen. Es a
propósito: lo primero que hay que saber es si tus fotos conservan la fecha y el
GPS, y eso depende de cómo hayan salido del móvil. Importar a ciegas una
carpeta que perdió los metadatos llenaría el viaje de puntos vacíos.

La foto ORIGINAL nunca se copia ni se sube: son ~3 MB y el plan gratuito tiene
512. Solo se leen los primeros kilobytes de cada archivo para sacar cuándo y
dónde se hizo. Con `--enviar` sí viaja una MINIATURA de cada una —unos 8 KB,
reducida aquí mismo con Pillow si lo tienes instalado (`pip install Pillow`,
y `pillow-heif` si tus fotos son HEIC)— porque es lo que hace que el diario
enseñe la foto y no el nombre del archivo. Sin Pillow, los puntos se mandan
igual y las miniaturas se saltan con un aviso, no con un error.

Las fotos se quedan donde están: sus metadatos ocupan ~100 bytes y contienen
todo lo que el mapa necesita.

Lo que hay que saber antes de usarlo, comprobado y no supuesto:

  - **WhatsApp borra el EXIF entero.** Una foto reenviada por WhatsApp no trae
    ni fecha, ni GPS, ni cámara. Para esto solo sirven los originales.
  - **El GPS de la foto depende de un permiso.** Si la cámara del móvil tenía
    la ubicación desactivada, la foto tiene fecha pero no sitio. Sigue
    sirviendo: ordena el relato aunque no ponga una chincheta.
  - **El huso horario es opcional en el EXIF.** El iPhone lo escribe; muchas
    cámaras no. Sin él se guarda la hora local tal cual, que es la que se
    recuerda, y no se inventa ninguna zona.

Para enviar al servidor hace falta el token de ingesta EN CLARO, el mismo del
atajo del iPhone. Va en la variable `INGEST_TOKEN` del `.env` de tu portátil.
Ojo: en el servidor vive solo el HASH (`INGEST_TOKEN_HASH`), y así tiene que
seguir; el token en claro no debe estar nunca allí.
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

# Este script vive en tools/, así que Python pone tools/ en el path, no la raíz
# del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules import miniaturas, photo_meta, storage, waypoints  # noqa: E402

# Extensiones que se miran. Los vídeos quedan fuera de momento: llevan sus
# propios metadatos en otro formato y son un trabajo aparte.
EXTENSIONES = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".dng"}

# Cuántos puntos van en cada petición al servidor. El techo del cuerpo son
# 128 KiB (MAX_CONTENT_LENGTH) y se aplica ANTES de parsear el JSON, que es
# donde se gasta la CPU -- y en PythonAnywhere la CPU es cuota diaria. Con
# ~150 bytes por punto, 250 son ~37 KB: margen de sobra. Trocear además hace
# que una carpeta de 3000 fotos se pueda enviar por una red mala, porque cada
# lote que llega ya está guardado y el reenvío no duplica nada.
LOTE = 250


def _fotos_de(carpeta: Path) -> list[Path]:
    """Todos los archivos de imagen de la carpeta y sus subcarpetas, ordenados."""
    return sorted(
        p for p in carpeta.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTENSIONES
    )


def _leer(
    rutas: list[Path],
) -> tuple[list[photo_meta.PhotoMeta], list[Path], list[str]]:
    """Lee los metadatos de todas. Un archivo ilegible no para la importación.

    Devuelve también `rutas_ok`, alineada índice a índice con `metadatos`:
    un archivo que falla NO añade una entrada a ninguna de las dos, así que
    `rutas` (con fallos) y `metadatos` (sin ellos) no se pueden cruzar por
    posición. Hace falta para las miniaturas, que necesitan volver al
    archivo de origen y no solo a lo que se sacó de él.
    """
    metadatos: list[photo_meta.PhotoMeta] = []
    rutas_ok: list[Path] = []
    fallos: list[str] = []
    for ruta in rutas:
        try:
            metadatos.append(photo_meta.read_metadata(ruta))
            rutas_ok.append(ruta)
        except photo_meta.PhotoMetaError as exc:
            fallos.append(str(exc))
    return metadatos, rutas_ok, fallos


def _informe(metadatos: list[photo_meta.PhotoMeta], fallos: list[str]) -> None:
    """Qué traen las fotos. Es la parte que de verdad importa de esta herramienta."""
    total = len(metadatos)
    con_fecha = sum(1 for m in metadatos if m.capturado_en)
    con_gps = sum(1 for m in metadatos if m.ubicada)
    con_huso = sum(1 for m in metadatos if m.offset_original)
    utiles = sum(1 for m in metadatos if m.sirve)

    print(f"\n{total} fotos leídas" + (f", {len(fallos)} ilegibles" if fallos else ""))
    print(f"  con fecha ............ {con_fecha:>5}  ({_pct(con_fecha, total)})")
    print(f"  con GPS .............. {con_gps:>5}  ({_pct(con_gps, total)})")
    print(f"  con huso horario ..... {con_huso:>5}  ({_pct(con_huso, total)})")
    print(f"  aprovechables ........ {utiles:>5}  ({_pct(utiles, total)})")

    formatos = Counter(m.formato for m in metadatos)
    print("\nFormatos: " + ", ".join(f"{f} {n}" for f, n in formatos.most_common()))

    camaras = Counter(m.camara for m in metadatos if m.camara)
    if camaras:
        print("Cámaras:  " + ", ".join(f"{c} ({n})" for c, n in camaras.most_common(4)))

    fechas = sorted(m.capturado_en for m in metadatos if m.capturado_en)
    if fechas:
        print(f"Tramo:    {fechas[0][:10]}  →  {fechas[-1][:10]}")

    # El diagnóstico, que es lo que evita perder una tarde suponiendo.
    if total and utiles == 0:
        print(
            "\n⚠  Ninguna foto trae metadatos. Las causas habituales, por orden:\n"
            "   1. Pasaron por WhatsApp o Telegram, que borran el EXIF al comprimir.\n"
            "      Comprobado contra archivos reales: no queda ni un byte.\n"
            "   2. Se exportaron con alguna opción de 'quitar información de ubicación'.\n"
            "   Prueba con los ORIGINALES del carrete."
        )
    elif con_gps == 0 and con_fecha:
        print(
            "\n⚠  Hay fechas pero ningún GPS. La cámara tenía la ubicación\n"
            "   desactivada (Ajustes → Privacidad → Localización → Cámara).\n"
            "   El viaje se podrá ordenar en el tiempo, pero no dibujar en el mapa."
        )
    elif con_fecha and con_huso == 0:
        print(
            "\nℹ  Ninguna foto trae el huso horario. Se guarda la hora local tal\n"
            "   cual, que es la que recuerdas. No se inventa ninguna zona."
        )

    if fallos:
        print("\nArchivos ilegibles:")
        for fallo in fallos[:5]:
            print(f"  {fallo}")
        if len(fallos) > 5:
            print(f"  ...y {len(fallos) - 5} más")


def _pct(parte: int, total: int) -> str:
    return f"{100 * parte // total}%" if total else "—"


def _detalle(metadatos: list[photo_meta.PhotoMeta]) -> None:
    cabecera = f"{'archivo':<34} {'cuándo':<20} {'huso':>6} {'lat':>10} {'lon':>11} {'alt':>7}"
    print("\n" + cabecera)
    print("-" * len(cabecera))
    for m in metadatos:
        print(
            f"{m.archivo[:34]:<34} {(m.capturado_en or '—').replace('T', ' '):<20} "
            f"{m.offset_original or '—':>6} "
            f"{'—' if m.lat is None else f'{m.lat:.5f}':>10} "
            f"{'—' if m.lon is None else f'{m.lon:.5f}':>11} "
            f"{'—' if m.altitud is None else f'{m.altitud:.0f}m':>7}"
        )


def _cuerpos(metadatos: list[photo_meta.PhotoMeta]) -> list[dict]:
    """Solo las que aportan algo. Las vacías no se envían ni se guardan."""
    return [m.to_dict() for m in metadatos if m.sirve]


def _importar_local(puntos: list[dict]) -> None:
    storage.init_db()
    total = {"guardados": 0, "duplicados": 0, "descartados": 0}
    for i in range(0, len(puntos), LOTE):
        resultado = waypoints.import_waypoints(
            {"fuente": "fotos", "puntos": puntos[i : i + LOTE]}
        )
        for clave in total:
            total[clave] += getattr(resultado, clave)
        for error in resultado.errores[:3]:
            print(f"  {error}")

    print(
        f"\nEn la base de datos LOCAL: {total['guardados']} nuevos, "
        f"{total['duplicados']} ya estaban, {total['descartados']} descartados."
    )
    if total["duplicados"]:
        print("Los duplicados son lo esperado al reimportar la misma carpeta.")


def url_segura(base: str) -> bool:
    """¿Se puede mandar el token a esta URL sin que viaje en claro?

    Por `http://` la cabecera `Authorization` va sin cifrar: cualquiera en el
    wifi de un camping se lleva el token, y con él escribe en tu viaje. Se
    bloquea aquí en vez de confiar en que nadie se equivoque escribiendo la
    URL, porque el fallo es silencioso —la petición funciona igual— y el
    secreto ya estaría comprometido cuando te enteraras.

    La excepción es la máquina local: en `localhost` no hay red que espiar, y
    sin ella no se podría probar nada sin desplegar.
    """
    base = base.strip().lower()
    if base.startswith("https://"):
        return True
    if not base.startswith("http://"):
        return False
    host = base[len("http://") :].split("/")[0].split(":")[0]
    return host in ("localhost", "127.0.0.1", "[::1]", "::1")


def _enviar(puntos: list[dict], base: str) -> str:
    """Manda los puntos. Devuelve el token ya validado, para reusarlo con las
    miniaturas sin tener que volver a leerlo ni a comprobar la URL."""
    if not url_segura(base):
        print(
            f"\nNo se envía nada a {base}\n"
            "Por http:// la cabecera con el token viaja SIN CIFRAR, y quien esté\n"
            "en la misma red se lo lleva. Usa https:// (PythonAnywhere lo da), o\n"
            "http://127.0.0.1 si estás probando en tu propia máquina."
        )
        sys.exit(1)

    token = os.environ.get("INGEST_TOKEN", "").strip()
    if not token:
        print(
            "\nFalta INGEST_TOKEN: el token EN CLARO, el mismo que usa el atajo del\n"
            "iPhone (lo que va después de 'Bearer ' en su cabecera Authorization).\n"
            "\n"
            "  A mano:            ponlo en el .env de este portátil\n"
            "  Carpeta vigilada:  ~/.config/roadtrip/fotos.env\n"
            "\n"
            "En el SERVIDOR no: allí vive solo el hash (INGEST_TOKEN_HASH), y esa\n"
            "asimetría es lo que hace que un .env filtrado no entregue el token.\n"
            "Si lo has perdido, `python tools/token_ingesta.py` genera otro, y hay\n"
            "que actualizar el hash en el servidor Y el token en el atajo."
        )
        sys.exit(1)

    url = base.rstrip("/") + "/api/waypoints"
    total = {"guardados": 0, "duplicados": 0, "descartados": 0}

    for i in range(0, len(puntos), LOTE):
        lote = puntos[i : i + LOTE]
        cuerpo = json.dumps({"fuente": "fotos", "puntos": lote}).encode("utf-8")
        peticion = urllib.request.Request(
            url,
            data=cuerpo,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        etiqueta = f"lote {i // LOTE + 1} ({len(lote)} puntos)"
        try:
            with urllib.request.urlopen(peticion, timeout=60) as respuesta:
                datos = json.loads(respuesta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", "replace")[:300]
            print(f"  {etiqueta}: HTTP {exc.code} — {detalle}")
            if exc.code == 401:
                print("  El token no es el bueno. Ver docs/troubleshooting.md.")
            # Se para: si un lote falla por credenciales o por forma del cuerpo,
            # los siguientes fallarán igual y solo servirían para llenar la
            # pantalla del mismo error.
            sys.exit(1)
        except urllib.error.URLError as exc:
            print(f"  {etiqueta}: no se pudo conectar — {exc.reason}")
            print("  Los lotes ya enviados están guardados; vuelve a lanzarlo")
            print("  cuando tengas red: reenviar lo mismo no duplica nada.")
            sys.exit(1)

        for clave in total:
            total[clave] += datos.get(clave, 0)
        print(f"  {etiqueta}: {datos.get('guardados', 0)} nuevos, "
              f"{datos.get('duplicados', 0)} ya estaban")

    print(
        f"\nEn {base}: {total['guardados']} nuevos, {total['duplicados']} ya estaban, "
        f"{total['descartados']} descartados."
    )
    return token


# --- Miniaturas -------------------------------------------------------------
#
# Esto es lo que convierte "📷 IMG_4736.HEIC" en una foto de verdad en el
# diario, y responde a la pregunta de fondo: las fotos NO se suben (decisión
# 30, ~3 MB cada una y el plan gratuito tiene 512), pero una miniatura de
# ~400 px sí, porque son ~8 KB.
#
# Hay DOS caminos para que lleguen, y no compiten, se complementan:
#
#   - El atajo del iPhone, automático de verdad: cada envío del álbum manda
#     también las miniaturas (docs/atajo-fotos.md §4b). Es el camino pensado
#     para el día a día en marcha.
#   - Este, desde el portátil: cuando ya tienes las fotos en una carpeta —al
#     volcar el carrete, o mientras montas el atajo— se generan aquí y se
#     mandan en el mismo `--enviar`. Ninguna CPU del servidor se gasta: la
#     redujiste tú, en tu máquina, exactamente como pide la decisión 27
#     ("redimensionar donde estén los píxeles, nunca en el servidor").
#
# Y por eso Pillow NO está en requirements.txt (decisión 27 lo dice también):
# el servidor nunca decodifica una imagen. Aquí sí hace falta —no hay forma
# razonable de reescalar un JPEG o un HEIC sin un códec de verdad— así que es
# una dependencia OPCIONAL de esta herramienta, que se instala aparte:
#
#   pip install Pillow            # JPEG, PNG, TIFF, DNG
#   pip install pillow-heif       # además HEIC/HEIF, el formato por defecto
#                                  # del iPhone si no cambiaste "Más compatible"
#
# Sin Pillow, el envío de puntos funciona exactamente igual que siempre: las
# miniaturas se saltan con un aviso, no con un error que para la importación.

MINIATURA_ANCHO = 400

# Cuántas imágenes van en cada petición a /api/miniaturas. El servidor sube el
# techo del cuerpo a 2 MiB SOLO en esa ruta (ver `api_miniaturas` en app.py);
# con 64 KiB de máximo por imagen (`miniaturas.MAX_BYTES`), 20 caben de sobra
# y dejan margen. Un lote más grande que el de los puntos (`LOTE`) porque cada
# imagen pesa mucho más que un punto: 250 imágenes serían 16 MB en el peor caso.
LOTE_MINIATURAS = 20


def _abrir_heif_si_hay() -> None:
    """Registra el lector de HEIC en Pillow, si `pillow-heif` está instalado.

    Va en una función y no en el import de arriba del archivo porque es
    doblemente opcional: puede faltar Pillow entero, y puede faltar solo el
    complemento de HEIC. Cada ausencia se trata por separado, porque cada una
    se arregla con una instalación distinta.
    """
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass


def _generar_miniatura(ruta: Path) -> bytes | None:
    """Una miniatura JPEG de la foto, o `None` si no se pudo.

    Se reduce el tamaño primero y la calidad después, hasta caber en el techo
    del servidor (`miniaturas.MAX_BYTES`, 64 KiB) o agotar los intentos. No se
    manda una miniatura que sabemos que el servidor va a rechazar: sería una
    petición gastada para nada.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(ruta) as imagen:
            # Respeta la orientación EXIF: sin esto, media cámara sale girada
            # noventa grados en la miniatura aunque se vea bien en el móvil.
            imagen = ImageOps.exif_transpose(imagen)
            imagen = imagen.convert("RGB")

            if imagen.width > MINIATURA_ANCHO:
                alto = round(imagen.height * (MINIATURA_ANCHO / imagen.width))
                imagen = imagen.resize((MINIATURA_ANCHO, alto), Image.LANCZOS)

            ultimo: bytes | None = None
            for calidad in (70, 55, 40, 25):
                buffer = io.BytesIO()
                # Sin metadatos: el EXIF de la miniatura repetiría las
                # coordenadas, que ya viajaron en el JSON de puntos, y solo
                # pesaría más sin decir nada nuevo.
                imagen.save(buffer, format="JPEG", quality=calidad, optimize=True)
                ultimo = buffer.getvalue()
                if len(ultimo) <= miniaturas.MAX_BYTES:
                    return ultimo
            return ultimo  # lo mejor que salió; el servidor dirá si no cabe
    except (UnidentifiedImageError, OSError):
        return None


def _generar_miniaturas(
    metadatos: list[photo_meta.PhotoMeta], rutas_ok: list[Path]
) -> tuple[list[tuple[str, bytes]], Counter]:
    """Genera lo que se pueda. Cuenta lo que no, y por qué, en vez de callarlo.

    `metadatos` y `rutas_ok` están alineadas por `_leer()`, así que se recorren
    juntas con `zip` — es lo que hace innecesario buscar cada archivo por
    nombre.
    """
    stats: Counter = Counter()

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        stats["sin_pillow"] = sum(1 for m in metadatos if m.sirve)
        return [], stats

    _abrir_heif_si_hay()

    imagenes: list[tuple[str, bytes]] = []
    for metadato, ruta in zip(metadatos, rutas_ok):
        if not metadato.sirve:
            continue
        datos = _generar_miniatura(ruta)
        if datos is None:
            stats["formato_no_soportado"] += 1
            continue
        imagenes.append((metadato.archivo, datos))
        stats["generadas"] += 1
    return imagenes, stats


def _cuerpo_multipart(fuente: str, imagenes: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    """Construye el cuerpo `multipart/form-data` a mano.

    Sin `requests`: `_enviar()` ya manda los puntos con `urllib.request` a
    propósito, para que esta herramienta siga funcionando con un Python
    normal y no arrastre una dependencia más. Un multipart son cuatro líneas
    de plantilla por parte; no hace falta una librería para eso.
    """
    limite = uuid.uuid4().hex
    trozos: list[bytes] = []

    def campo(nombre: str, valor: str) -> None:
        trozos.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n'
            f"{valor}\r\n".encode("utf-8")
        )

    campo("fuente", fuente)
    for archivo, datos in imagenes:
        trozos.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="imagen"; '
            f'filename="{archivo}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode("utf-8")
        )
        trozos.append(datos)
        trozos.append(b"\r\n")
    trozos.append(f"--{limite}--\r\n".encode("utf-8"))

    return b"".join(trozos), f"multipart/form-data; boundary={limite}"


def _enviar_miniaturas(imagenes: list[tuple[str, bytes]], base: str, token: str) -> None:
    """Manda las miniaturas ya generadas a `/api/miniaturas`, en lotes."""
    url = base.rstrip("/") + "/api/miniaturas"
    total = {"guardadas": 0, "duplicadas": 0}

    for i in range(0, len(imagenes), LOTE_MINIATURAS):
        lote = imagenes[i : i + LOTE_MINIATURAS]
        cuerpo, content_type = _cuerpo_multipart("fotos", lote)
        peticion = urllib.request.Request(
            url, data=cuerpo,
            headers={"Content-Type": content_type, "Authorization": f"Bearer {token}"},
        )
        etiqueta = f"miniaturas {i // LOTE_MINIATURAS + 1} ({len(lote)})"
        try:
            with urllib.request.urlopen(peticion, timeout=60) as respuesta:
                datos = json.loads(respuesta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 507:
                # El presupuesto de disco está lleno: no es un fallo de ESTE
                # lote, es que ya no cabe nada más. Insistir con los lotes que
                # quedan solo repetiría el mismo 507.
                print(f"  {etiqueta}: sin sitio para más miniaturas — {detalle}")
                print("  Se paran los envíos de miniaturas; los puntos ya están guardados.")
                return
            print(f"  {etiqueta}: HTTP {exc.code} — {detalle}")
            return
        except urllib.error.URLError as exc:
            print(f"  {etiqueta}: no se pudo conectar — {exc.reason}")
            print("  Vuelve a lanzar el envío cuando tengas red: reenviar no duplica.")
            return

        total["guardadas"] += datos.get("guardadas", 0)
        total["duplicadas"] += datos.get("duplicadas", 0)
        for motivo in datos.get("rechazadas", [])[:3]:
            print(f"    rechazada: {motivo}")

    print(
        f"Miniaturas en {base}: {total['guardadas']} nuevas, "
        f"{total['duplicadas']} ya estaban."
    )


def _limpiar() -> None:
    """Borra TODOS los puntos importados de fotos, en la base de datos local.

    Por qué se borra todo de golpe y no punto a punto, al revés que las notas:
    un punto de foto **se puede volver a generar** leyendo la carpeta otra vez,
    mientras que una nota escrita en un mirador no existe en ningún otro sitio.
    Con datos regenerables la operación útil es "vaciar y reimportar", y una
    lista de ids sería una forma incómoda de hacer lo mismo.

    No toca las notas ni la telemetría: solo la tabla `waypoints`.
    """
    storage.init_db()
    ids = [p["id"] for p in storage.list_waypoints(100000)]
    if not ids:
        print("No hay ningún punto que borrar.")
        return
    borrados = storage.delete_waypoints(ids)
    print(
        f"Borrados {borrados} puntos de la base de datos LOCAL.\n"
        "Las notas y la telemetría no se han tocado.\n"
        "Vuelve a importar la carpeta cuando quieras: se regeneran enteros."
    )


def main() -> None:
    argumentos = sys.argv[1:]
    if not argumentos or argumentos[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    if argumentos[0] == "--limpiar":
        _limpiar()
        sys.exit(0)

    carpeta = Path(argumentos[0]).expanduser()
    if not carpeta.is_dir():
        print(f"No existe la carpeta: {carpeta}")
        sys.exit(1)

    print(f"Leyendo {carpeta}…")
    rutas = _fotos_de(carpeta)
    if not rutas:
        print(
            f"No hay ninguna imagen en {carpeta}.\n"
            f"Se buscan: {', '.join(sorted(EXTENSIONES))}"
        )
        sys.exit(0)

    metadatos, rutas_ok, fallos = _leer(rutas)
    _informe(metadatos, fallos)

    if "--detalle" in argumentos:
        _detalle(metadatos)

    puntos = _cuerpos(metadatos)
    if not puntos:
        print("\nNo hay nada que importar: ninguna foto aporta fecha ni ubicación.")
        return

    if "--importar" in argumentos:
        _importar_local(puntos)
    elif "--enviar" in argumentos:
        posicion = argumentos.index("--enviar")
        if posicion + 1 >= len(argumentos):
            print("Falta la URL. Ejemplo: --enviar https://tuapp.pythonanywhere.com")
            sys.exit(1)
        base = argumentos[posicion + 1]
        token = _enviar(puntos, base)

        # Las miniaturas van SIEMPRE que se manda al servidor, sin flag aparte:
        # es la respuesta a "¿tendré que subirlas o se pueden subir
        # automático?" — si tienes Pillow, sí, solas. Si no, un aviso claro y
        # los puntos siguen entrando igual; no es un fallo que pare nada.
        print()
        imagenes, stats = _generar_miniaturas(metadatos, rutas_ok)
        if stats["sin_pillow"]:
            print(
                f"{stats['sin_pillow']} fotos podrían tener miniatura y no la tienen: "
                "falta Pillow.\n"
                "  pip install Pillow           # y así el diario enseña la foto,\n"
                "                                # no el nombre del archivo"
            )
        if stats["formato_no_soportado"]:
            print(
                f"{stats['formato_no_soportado']} no se pudieron abrir (probablemente "
                "HEIC sin el complemento):\n"
                "  pip install pillow-heif"
            )
        if imagenes:
            print(f"Enviando {len(imagenes)} miniaturas…")
            _enviar_miniaturas(imagenes, base, token)
    else:
        print(
            f"\n{len(puntos)} fotos aprovechables. No se ha guardado nada.\n"
            f"  --importar                     para guardarlas en la BD local\n"
            f"  --enviar https://tuapp…        para mandarlas al servidor"
        )


if __name__ == "__main__":
    main()
