"""Miniaturas de las fotos del viaje: guardar, servir y borrar.

Las fotos NO se suben (decisión 30): viven en el iPhone y de ellas solo viaja el
EXIF. Lo que sube aquí es otra cosa, mucho más barata: una miniatura de ~200 px
a JPEG bajo, unos 8 KB. Mil fotos son 8 MB de los 512 del plan, y con eso el
diario deja de ser una lista de nombres de archivo y pasa a ser un álbum.

Tres decisiones estaban ya escritas (decisión 27) y aquí solo se aplican:
multipart y no base64, la imagen se reduce ANTES de enviarse, y el nombre del
archivo sale de nosotros y jamás del cliente.

Y una que se toma aquí, porque simplifica el resto: **no hay tabla**. El nombre
de la miniatura se deriva de `(fuente, archivo)` con un hash, así que saber si
una foto tiene miniatura es preguntarle al disco. Una columna en `waypoints`
podría decir que la hay cuando el archivo no está —o al revés— y esa
desincronización no daría ningún error, solo huecos o imágenes rotas. Es la
misma idea que poner la idempotencia en el `UNIQUE` de la tabla y no en un
`SELECT` previo (decisión 23): la invariante la garantiza la construcción.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from app.config import Config

log = logging.getLogger(__name__)


class MiniaturaError(Exception):
    """Una miniatura que no se puede aceptar. El mensaje va al cliente."""


# Solo JPEG. No es purismo: el navegador y Atajos saben producirlo, es lo más
# pequeño para una foto, y aceptar varios formatos obligaría a decidir la
# extensión a partir de algo que manda el cliente — justo lo que la decisión 27
# prohíbe. Los tres bytes son la firma real del formato; la extensión del nombre
# que llega no se mira nunca, porque es del cliente y se puede falsear.
_FIRMA_JPEG = b"\xff\xd8\xff"

# 64 KiB por miniatura. Una de 200×150 a calidad 0,7 ronda los 8 KB, así que
# esto deja un margen holgado y a la vez corta en seco al que mande la foto
# original de 3 MB: sin techo, un atajo mal montado llenaría la cuota en una
# tarde y el fallo aparecería como «disco lleno» tres días después, lejos de su
# causa.
MAX_BYTES = 64 * 1024

# Cuánto pueden ocupar TODAS las miniaturas juntas.
#
# Se mide lo nuestro contra un presupuesto propio y no contra la cuota global de
# la cuenta, y es a propósito. La cuota global la comparten el virtualenv y el
# repositorio, que no crecen; lo único que crece a diario es esto. Y medirlo es
# recorrer un directorio en vez del `$HOME` entero en cada petición, que sería
# pagar un recorrido de disco por foto recibida.
CUOTA_MB = 40.0


def _directorio() -> Path:
    return Path(Config.UPLOAD_DIR) / "miniaturas"


def nombre_de(fuente: str, archivo: str) -> str:
    """El nombre en disco de la miniatura de una foto. Determinista.

    Sale de un hash de `(fuente, archivo)` y **nunca** del nombre que manda el
    cliente. Ese nombre llega de iOS y podría traer `../`, separadores o
    caracteres que el sistema de archivos interprete; sanearlo con
    `secure_filename()` no valdría, porque sanear puede colapsar dos nombres
    distintos en el mismo archivo y entonces una foto se comería la miniatura de
    otra sin dar ningún error (decisión 27).

    El separador `\\0` entre los dos campos importa: sin él, `("fotos", "ab")` y
    `("foto", "sab")` producirían el mismo hash. Es un byte que no puede aparecer
    dentro de ninguno de los dos.
    """
    crudo = f"{fuente}\0{archivo}".encode("utf-8")
    return hashlib.sha256(crudo).hexdigest()[:32] + ".jpg"


def existe(fuente: str, archivo: str) -> bool:
    """¿Tiene esta foto su miniatura en disco?"""
    return (_directorio() / nombre_de(fuente, archivo)).is_file()


def ruta_servible(nombre: str) -> Path | None:
    """La ruta en disco de una miniatura ya guardada, o `None`.

    `nombre` llega de la URL, así que se comprueba que sea exactamente uno de
    los que producimos —32 dígitos hexadecimales y `.jpg`— en vez de intentar
    limpiarlo. Una lista blanca de forma no se puede escapar con `..%2f` ni con
    unicode raro; un saneado sí.
    """
    if len(nombre) != 36 or not nombre.endswith(".jpg"):
        return None
    if not all(c in "0123456789abcdef" for c in nombre[:32]):
        return None
    ruta = _directorio() / nombre
    return ruta if ruta.is_file() else None


def usado_mb() -> float:
    """Cuánto ocupan hoy todas las miniaturas.

    Se suma `st_blocks * 512` y no `st_size` por lo mismo que en la decisión 38:
    es lo que mide una cuota y lo que da `du`, con el que se contrasta desde la
    consola del servidor. Un JPEG de 8 KB ocupa un bloque entero.
    """
    directorio = _directorio()
    if not directorio.is_dir():
        return 0.0
    total = 0
    for entrada in os.scandir(directorio):
        if entrada.is_file():
            total += entrada.stat().st_blocks * 512
    return total / (1024 * 1024)


def _validar(datos: bytes, archivo: str) -> None:
    if not archivo or len(archivo) > 255:
        raise MiniaturaError("nombre de archivo vacío o demasiado largo")
    if not datos:
        raise MiniaturaError(f"{archivo}: llegó vacía")
    if len(datos) > MAX_BYTES:
        raise MiniaturaError(
            f"{archivo}: {len(datos) // 1024} KiB, y el máximo son "
            f"{MAX_BYTES // 1024} KiB. Redúcela antes de enviarla."
        )
    if not datos.startswith(_FIRMA_JPEG):
        raise MiniaturaError(f"{archivo}: no es un JPEG")


def guardar(fuente: str, archivo: str, datos: bytes) -> bool:
    """Guarda una miniatura. Devuelve si se escribió (`False` = ya estaba).

    Escribe a un temporal y luego renombra. `os.replace` es atómico dentro del
    mismo sistema de archivos, así que nunca existe un archivo a medias que el
    diario pueda llegar a servir: o está la miniatura entera, o no está. Escribir
    directo sobre el destino sí deja esa ventana, y con mala cobertura la
    petición se corta a mitad más de lo que parece.
    """
    _validar(datos, archivo)

    directorio = _directorio()
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / nombre_de(fuente, archivo)

    # Reenviar el álbum entero es lo normal (decisión 45), así que la mayoría de
    # las miniaturas de cada envío ya están. No se reescriben: es una escritura
    # de disco por foto que no cambia nada, y en PythonAnywhere el disco es de
    # red y se paga cara.
    if destino.is_file():
        return False

    descriptor, temporal = tempfile.mkstemp(dir=directorio, suffix=".parcial")
    try:
        with os.fdopen(descriptor, "wb") as salida:
            salida.write(datos)
        os.replace(temporal, destino)
    except BaseException:
        # Un temporal huérfano gasta cuota y no lo ve nadie.
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise
    return True


def borrar(fuente: str, archivos: list[str]) -> int:
    """Borra las miniaturas de estas fotos. Devuelve cuántas se fueron.

    Lo llama el borrado de puntos ausentes: quitar una foto del álbum es decir
    «esta no cuenta» (decisión 45), y dejar su miniatura en disco sería gastar
    cuota en una imagen que ya no puede enseñar nadie. El borrado se acota a la
    `fuente` porque el nombre se deriva de ella: el álbum del iPhone no puede
    llevarse por delante lo que entró por la carpeta del portátil.
    """
    idos = 0
    for archivo in archivos:
        try:
            (_directorio() / nombre_de(fuente, archivo)).unlink()
            idos += 1
        except FileNotFoundError:
            # Lo normal: la mayoría de las fotos nunca tuvieron miniatura.
            pass
        except OSError as exc:
            log.warning("No se pudo borrar la miniatura de %s: %s", archivo, exc)
    return idos


def hay_sitio() -> tuple[bool, float]:
    """¿Cabe otra tanda? Devuelve `(cabe, MB usados)`.

    Cuando no cabe **se rechaza y se dice**, no se borran las más antiguas. Es la
    asimetría de la decisión 45: una miniatura de más se ve y se puede quitar a
    mano; una borrada sola es una foto del viaje que desaparece del diario sin
    que nadie lo haya pedido ni se entere. Entre los dos errores, el
    recuperable.
    """
    usado = usado_mb()
    return usado < CUOTA_MB, usado
