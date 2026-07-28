"""Lee los metadatos de una carpeta de fotos y los convierte en ruta del viaje.

Uso:
    python tools/importar_fotos.py ~/Fotos/viaje              # solo mira e informa
    python tools/importar_fotos.py ~/Fotos/viaje --importar   # guarda en la BD local
    python tools/importar_fotos.py ~/Fotos/viaje --enviar https://tuapp.pythonanywhere.com
    python tools/importar_fotos.py ~/Fotos/viaje --detalle    # foto a foto

**Por defecto no guarda nada.** Solo mira las fotos y te dice qué traen. Es a
propósito: lo primero que hay que saber es si tus fotos conservan la fecha y el
GPS, y eso depende de cómo hayan salido del móvil. Importar a ciegas una
carpeta que perdió los metadatos llenaría el viaje de puntos vacíos.

NO se copia, ni se sube, ni se toca ninguna foto. Solo se leen los primeros
kilobytes de cada archivo para sacar cuándo y dónde se hizo. Las fotos se
quedan donde están: una son ~3 MB y el plan gratuito de PythonAnywhere tiene
512 MB, mientras que sus metadatos ocupan ~100 bytes y contienen todo lo que el
mapa necesita.

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

import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

# Este script vive en tools/, así que Python pone tools/ en el path, no la raíz
# del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules import photo_meta, storage, waypoints  # noqa: E402

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


def _leer(rutas: list[Path]) -> tuple[list[photo_meta.PhotoMeta], list[str]]:
    """Lee los metadatos de todas. Un archivo ilegible no para la importación."""
    metadatos: list[photo_meta.PhotoMeta] = []
    fallos: list[str] = []
    for ruta in rutas:
        try:
            metadatos.append(photo_meta.read_metadata(ruta))
        except photo_meta.PhotoMetaError as exc:
            fallos.append(str(exc))
    return metadatos, fallos


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


def _enviar(puntos: list[dict], base: str) -> None:
    token = os.environ.get("INGEST_TOKEN", "").strip()
    if not token:
        print(
            "\nFalta INGEST_TOKEN (el token EN CLARO, el mismo del atajo del iPhone).\n"
            "Ponlo en el .env de tu portátil, NO en el del servidor: allí solo\n"
            "debe vivir el hash. Si lo has perdido, genera uno nuevo con\n"
            "  python tools/token_ingesta.py\n"
            "y actualiza el hash en el servidor y el token en el atajo."
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


def main() -> None:
    argumentos = sys.argv[1:]
    if not argumentos or argumentos[0] in ("-h", "--help"):
        print(__doc__)
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

    metadatos, fallos = _leer(rutas)
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
        _enviar(puntos, argumentos[posicion + 1])
    else:
        print(
            f"\n{len(puntos)} fotos aprovechables. No se ha guardado nada.\n"
            f"  --importar                     para guardarlas en la BD local\n"
            f"  --enviar https://tuapp…        para mandarlas al servidor"
        )


if __name__ == "__main__":
    main()
