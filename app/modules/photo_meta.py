"""Metadatos EXIF de una foto: cuándo y dónde se hizo.

Función de entrada: `read_metadata(ruta) -> PhotoMeta`. Lanza `PhotoMetaError`
cuando el archivo no se puede leer; que una foto **no traiga** metadatos no es
un error, es un resultado (`PhotoMeta` con los campos a `None`), porque es el
caso más frecuente y hay que poder contarlo.

Por qué esto en vez de subir las fotos al servidor: una foto son ~3 MB y el
plan gratuito tiene 512 MB. Sus metadatos son ~100 bytes y contienen lo único
que hace falta para dibujar el viaje —cuándo y dónde— así que el trayecto se
reconstruye **sin subir un solo megabyte**. Las fotos se quedan donde están.

Sin dependencias, a propósito. Un lector de EXIF completo (Pillow, exifread)
traería un paquete más para leer cuatro etiquetas, y este proyecto ya decidió
lo mismo con el SDK de OpenAI (decisión 18). Aquí además se puede: el EXIF es
un TIFF incrustado, y sacar cuatro etiquetas de un TIFF son cien líneas que se
prueban con bytes fabricados a mano, sin archivos binarios en el repositorio.

**Comprobado contra archivos reales, no supuesto:** las fotos que pasan por
WhatsApp llegan con **cero** EXIF —ni fecha, ni GPS, ni cámara— porque se lo
quita al comprimir. Para esto solo sirven los originales del carrete.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Cuánto se lee de entrada. En un JPEG el EXIF es el segundo segmento, así que
# con esto sobra siempre y no hace falta cargar 3 MB para leer cuatro etiquetas:
# un millar de fotos leyéndose enteras sería un minuto de disco por nada.
MAX_CABECERA = 512 * 1024

# Pero en un HEIC no basta, y es justo el formato por defecto del iPhone. La
# cabecera declara DÓNDE está el EXIF, y el bloque en sí vive en el `mdat`, que
# va detrás de la imagen: en una foto de 3 MB puede estar en el megabyte 2. Con
# solo la cabecera, esas fotos darían "sin metadatos" y **no fallaría nada**:
# saldrían como si el iPhone no hubiera guardado la ubicación.
#
# Así que si el primer intento no encuentra nada en un contenedor, se lee el
# archivo entero. Es el caso raro (un JPEG nunca llega aquí), y a cambio la
# función no miente. El tope existe para que un vídeo de 4 GB mal nombrado no
# se cargue en memoria.
MAX_ARCHIVO_COMPLETO = 64 * 1024 * 1024


class PhotoMetaError(Exception):
    """El archivo no se puede leer o no es una imagen reconocible."""


# --- Etiquetas EXIF que interesan -------------------------------------------
# Solo estas. Un lector completo tendría cientos; aquí cada una tiene un motivo.
_MAKE = 0x010F
_MODEL = 0x0110
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_DATETIME_ORIGINAL = 0x9003
# La que hace que todo esto sirva: el desfase horario. DateTimeOriginal es hora
# local SIN huso, así que sin esta etiqueta no se sabe qué instante fue. iPhone
# la escribe desde iOS 13; muchas cámaras no.
_OFFSET_TIME_ORIGINAL = 0x9011
_GPS_LAT_REF, _GPS_LAT = 0x0001, 0x0002
_GPS_LON_REF, _GPS_LON = 0x0003, 0x0004
_GPS_ALT_REF, _GPS_ALT = 0x0005, 0x0006

# Tamaño en bytes de cada tipo de dato TIFF, por su código.
_TAMANOS = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


@dataclass(frozen=True)
class PhotoMeta:
    """Lo que se ha podido sacar de una foto. Cualquier campo puede ser None."""

    archivo: str
    formato: str                      # "JPEG", "HEIC", "desconocido"
    capturado_en: str | None = None   # hora LOCAL de la cámara: 2026-07-28T14:32:05
    offset_original: str | None = None  # "+02:00", o None si la cámara no lo escribió
    lat: float | None = None
    lon: float | None = None
    altitud: float | None = None      # metros sobre el nivel del mar
    camara: str | None = None         # "Apple iPhone 15"

    @property
    def sirve(self) -> bool:
        """¿Aporta algo al mapa? Sin fecha y sin coordenadas, no.

        La fecha sola sí sirve: ordena el relato del viaje aunque no ponga el
        punto en el mapa.
        """
        return self.capturado_en is not None or self.lat is not None

    @property
    def ubicada(self) -> bool:
        return self.lat is not None and self.lon is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "archivo": self.archivo,
            "formato": self.formato,
            "capturado_en": self.capturado_en,
            "offset_original": self.offset_original,
            "lat": self.lat,
            "lon": self.lon,
            "altitud": self.altitud,
            "camara": self.camara,
        }


# ---------------------------------------------------------------------------
# Localizar el bloque EXIF dentro del archivo
# ---------------------------------------------------------------------------

def _exif_de_jpeg(datos: bytes) -> bytes | None:
    """Recorre los segmentos del JPEG hasta el APP1 con EXIF.

    Se recorre de verdad en vez de buscar la cadena "Exif" por el archivo: un
    JPEG puede llevar la palabra dentro de la propia imagen comprimida, y
    fabricar coordenadas a partir de un byte que rimaba sería el peor fallo
    posible aquí -- pondría una chincheta convincente en un sitio inventado.
    """
    if datos[:2] != b"\xff\xd8":
        return None

    i = 2
    while i < len(datos) - 4:
        if datos[i] != 0xFF:
            return None  # Estructura rota; mejor no leer nada que leer basura.
        marca = datos[i + 1]
        if marca == 0xDA or marca == 0xD9:
            return None  # Empiezan los datos de imagen: ya no habrá metadatos.
        if marca in (0xD8,) or 0xD0 <= marca <= 0xD7 or marca == 0x01:
            i += 2
            continue
        if i + 4 > len(datos):
            return None
        largo = struct.unpack(">H", datos[i + 2 : i + 4])[0]
        if largo < 2:
            return None
        cuerpo = datos[i + 4 : i + 2 + largo]
        if marca == 0xE1 and cuerpo[:6] == b"Exif\x00\x00":
            return cuerpo[6:]
        i += 2 + largo
    return None


def _exif_de_contenedor(datos: bytes) -> bytes | None:
    """Busca el bloque EXIF en un HEIC/HEIF, y en cualquier otro contenedor.

    Aquí sí se busca la marca por el archivo, y hay que decir por qué es
    aceptable cuando en el JPEG no lo era: un HEIC es una caja ISO-BMFF y
    recorrer su árbol de cajas para localizar el ítem `Exif` son otras cien
    líneas que **no puedo probar contra un HEIC real** ahora mismo. Buscar la
    marca es una heurística, pero no una adivinanza: solo se acepta si detrás
    viene una cabecera TIFF válida (orden de bytes + el número mágico 42) y las
    etiquetas se leen de una estructura que tiene que cuadrar entera. Un falso
    positivo tendría que ser un TIFF válido por casualidad.

    Y si falla, falla en silencio hacia el lado seguro: no encuentra nada y la
    foto se cuenta como "sin metadatos", que es visible en el resumen.
    """
    marca = datos.find(b"Exif\x00\x00")
    if marca == -1:
        return None
    return datos[marca + 6 :]


def _formato_de(datos: bytes) -> str:
    if datos[:2] == b"\xff\xd8":
        return "JPEG"
    # ISO-BMFF: [4 bytes de tamaño]"ftyp"[marca]. HEIC, HEIF, AVIF y los .MOV.
    if len(datos) > 12 and datos[4:8] == b"ftyp":
        return datos[8:12].decode("ascii", "replace").strip().upper() or "ISO-BMFF"
    return "desconocido"


# ---------------------------------------------------------------------------
# Leer el TIFF que hay dentro del EXIF
# ---------------------------------------------------------------------------

def _leer_ifd(tiff: bytes, offset: int, orden: str) -> dict[int, Any]:
    """Lee un directorio de etiquetas (IFD) y devuelve {etiqueta: valor}.

    Un IFD es: número de entradas (2 bytes), las entradas (12 bytes cada una) y
    el offset al siguiente IFD. Cada entrada dice qué etiqueta es, de qué tipo,
    cuántos valores, y o bien el valor (si cabe en 4 bytes) o dónde está.

    Nunca lanza por datos corruptos: una entrada que no cuadra se salta. Un
    archivo raro tiene que dar "esta foto no trae metadatos", no tumbar la
    importación de las otras mil.
    """
    if offset <= 0 or offset + 2 > len(tiff):
        return {}

    try:
        (n_entradas,) = struct.unpack(orden + "H", tiff[offset : offset + 2])
    except struct.error:
        return {}

    # Un IFD con miles de entradas es basura, no una foto. Cortar aquí evita
    # recorrer un archivo corrupto entero.
    if n_entradas > 512:
        return {}

    valores: dict[int, Any] = {}
    for i in range(n_entradas):
        base = offset + 2 + i * 12
        if base + 12 > len(tiff):
            break
        etiqueta, tipo, cuenta = struct.unpack(orden + "HHI", tiff[base : base + 8])
        tamano = _TAMANOS.get(tipo)
        if tamano is None or cuenta > 10000:
            continue

        total = tamano * cuenta
        if total <= 4:
            crudo = tiff[base + 8 : base + 8 + total]
        else:
            (donde,) = struct.unpack(orden + "I", tiff[base + 8 : base + 12])
            if donde + total > len(tiff):
                continue
            crudo = tiff[donde : donde + total]

        valor = _decodificar(crudo, tipo, cuenta, orden)
        if valor is not None:
            valores[etiqueta] = valor

    return valores


def _decodificar(crudo: bytes, tipo: int, cuenta: int, orden: str) -> Any:
    """Convierte los bytes de una entrada al valor de Python que le toca."""
    try:
        if tipo == 2:  # ASCII, terminado en \0
            return crudo.split(b"\x00")[0].decode("utf-8", "replace").strip() or None
        if tipo == 3:  # SHORT
            nums = struct.unpack(orden + "H" * cuenta, crudo[: 2 * cuenta])
            return nums[0] if cuenta == 1 else list(nums)
        if tipo == 4:  # LONG
            nums = struct.unpack(orden + "I" * cuenta, crudo[: 4 * cuenta])
            return nums[0] if cuenta == 1 else list(nums)
        if tipo in (5, 10):  # RATIONAL / SRATIONAL: pares numerador/denominador
            letra = "II" if tipo == 5 else "ii"
            fracciones = []
            for i in range(cuenta):
                num, den = struct.unpack(orden + letra, crudo[i * 8 : i * 8 + 8])
                # Denominador cero: el archivo dice "sin dato". Devolver 0 sería
                # inventarse una coordenada en el ecuador.
                fracciones.append(None if den == 0 else num / den)
            return fracciones[0] if cuenta == 1 else fracciones
    except (struct.error, UnicodeDecodeError):
        return None
    return None


def _grados(partes: Any, referencia: Any) -> float | None:
    """Convierte [grados, minutos, segundos] + "N"/"S" en un número decimal."""
    if not isinstance(partes, list) or len(partes) != 3 or any(p is None for p in partes):
        return None
    grados, minutos, segundos = partes
    valor = grados + minutos / 60 + segundos / 3600
    if isinstance(referencia, str) and referencia.upper() in ("S", "W"):
        valor = -valor
    # Una coordenada fuera de rango es un archivo corrupto, no un sitio raro.
    if not -90 <= valor <= 90 and referencia in ("N", "S"):
        return None
    if not -180 <= valor <= 180:
        return None
    return round(valor, 7)


def _fecha(bruto: Any) -> str | None:
    """Pasa "2026:07:28 14:32:05" a "2026-07-28T14:32:05".

    Sigue siendo hora LOCAL y sin huso: el EXIF no lo lleva en esta etiqueta.
    Convertirla a UTC aquí sería inventarse la zona, así que no se hace: el
    desfase, si la cámara lo escribió, va en su propio campo.
    """
    if not isinstance(bruto, str) or len(bruto) < 19:
        return None
    fecha, _, hora = bruto.strip().partition(" ")
    fecha = fecha.replace(":", "-")
    if not hora or fecha.startswith("0000"):
        return None
    return f"{fecha}T{hora[:8]}"


def _offset(bruto: Any) -> str | None:
    """Valida "+02:00" tal y como lo escribe la cámara."""
    if not isinstance(bruto, str) or len(bruto) != 6:
        return None
    if bruto[0] not in "+-" or bruto[3] != ":":
        return None
    if not (bruto[1:3].isdigit() and bruto[4:6].isdigit()):
        return None
    return bruto


# ---------------------------------------------------------------------------
# Entrada del módulo
# ---------------------------------------------------------------------------

def read_metadata(ruta: str | Path) -> PhotoMeta:
    """Lee los metadatos de una foto. No abre la imagen ni la descomprime."""
    camino = Path(ruta)
    try:
        with camino.open("rb") as f:
            datos = f.read(MAX_CABECERA)
            if not datos:
                raise PhotoMetaError(f"{camino.name} está vacío")

            formato = _formato_de(datos)
            if formato == "JPEG":
                tiff = _exif_de_jpeg(datos)
            else:
                tiff = _exif_de_contenedor(datos)
                # Segundo intento solo para contenedores (HEIC y compañía): el
                # bloque EXIF puede estar detrás de la imagen. Ver
                # MAX_ARCHIVO_COMPLETO.
                if tiff is None and camino.stat().st_size > len(datos):
                    f.seek(0)
                    tiff = _exif_de_contenedor(f.read(MAX_ARCHIVO_COMPLETO))
    except PhotoMetaError:
        raise
    except OSError as exc:
        raise PhotoMetaError(f"no se puede leer {camino.name}: {exc}") from exc

    vacia = PhotoMeta(archivo=camino.name, formato=formato)
    if not tiff or len(tiff) < 8:
        return vacia

    # Cabecera TIFF: orden de bytes, el número mágico 42, y dónde empieza el
    # primer IFD. Si esto no cuadra, lo que se encontró no era EXIF.
    marca_orden = tiff[:2]
    if marca_orden == b"II":
        orden = "<"
    elif marca_orden == b"MM":
        orden = ">"
    else:
        return vacia

    try:
        magico, offset_ifd0 = struct.unpack(orden + "HI", tiff[2:8])
    except struct.error:
        return vacia
    if magico != 42:
        return vacia

    ifd0 = _leer_ifd(tiff, offset_ifd0, orden)
    exif = _leer_ifd(tiff, ifd0.get(_EXIF_IFD, 0), orden) if _EXIF_IFD in ifd0 else {}
    gps = _leer_ifd(tiff, ifd0.get(_GPS_IFD, 0), orden) if _GPS_IFD in ifd0 else {}

    lat = _grados(gps.get(_GPS_LAT), gps.get(_GPS_LAT_REF))
    lon = _grados(gps.get(_GPS_LON), gps.get(_GPS_LON_REF))
    # Media coordenada no es medio dato, es un dato inservible (misma regla que
    # en la ingesta y en las notas).
    if lat is None or lon is None:
        lat = lon = None

    altitud = gps.get(_GPS_ALT)
    if isinstance(altitud, (int, float)):
        # GPSAltitudeRef: 1 significa "por debajo del nivel del mar".
        if gps.get(_GPS_ALT_REF) == 1:
            altitud = -altitud
        altitud = round(float(altitud), 1)
    else:
        altitud = None

    fabricante = ifd0.get(_MAKE)
    modelo = ifd0.get(_MODEL)
    camara = " ".join(p for p in (fabricante, modelo) if isinstance(p, str)) or None

    return PhotoMeta(
        archivo=camino.name,
        formato=formato,
        capturado_en=_fecha(exif.get(_DATETIME_ORIGINAL)),
        offset_original=_offset(exif.get(_OFFSET_TIME_ORIGINAL)),
        lat=lat,
        lon=lon,
        altitud=altitud,
        camara=camara,
    )
