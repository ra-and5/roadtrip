"""Tests del lector de EXIF (Fase 3b).

El problema de probar esto es que hace falta una foto con metadatos, y meter
un JPEG binario en el repositorio es meter un archivo que nadie puede leer ni
revisar en un diff. Así que aquí las fotos **se fabrican**: `_jpeg_con_exif()`
construye un JPEG mínimo con el EXIF que se le pida, byte a byte.

Eso tiene una ventaja que va más allá de la comodidad: el test sabe qué
coordenadas metió, así que puede comprobar que salen **exactamente** esas. Con
una foto de verdad solo se podría comprobar que sale "algo".

Y una limitación que hay que decir: esto prueba el lector contra un EXIF
correcto fabricado por nosotros. Que las fotos de un iPhone real traigan estas
etiquetas —sobre todo `OffsetTimeOriginal`, que es la que da el huso— solo se
sabe pasándole fotos reales. Ver el informe de `tools/importar_fotos.py`.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from app.modules import photo_meta
from app.modules.photo_meta import PhotoMetaError, read_metadata


# ---------------------------------------------------------------------------
# Fabricar un JPEG con el EXIF que queramos
# ---------------------------------------------------------------------------

def _ascii(texto: str) -> tuple[int, int, bytes]:
    return 2, len(texto) + 1, texto.encode("utf-8") + b"\x00"


def _short(valor: int) -> tuple[int, int, bytes]:
    return 3, 1, struct.pack("<H", valor)


def _long(valor: int) -> tuple[int, int, bytes]:
    return 4, 1, struct.pack("<I", valor)


def _rational(pares: list[tuple[int, int]]) -> tuple[int, int, bytes]:
    return 5, len(pares), b"".join(struct.pack("<II", n, d) for n, d in pares)


def _ifd(entradas: dict[int, tuple[int, int, bytes]], inicio_datos: int,
         datos: bytearray) -> bytes:
    """Serializa un IFD. Lo que no cabe en 4 bytes se va al área de datos."""
    salida = struct.pack("<H", len(entradas))
    for etiqueta in sorted(entradas):  # EXIF pide las etiquetas en orden
        tipo, cuenta, carga = entradas[etiqueta]
        if len(carga) <= 4:
            valor = carga.ljust(4, b"\x00")
        else:
            valor = struct.pack("<I", inicio_datos + len(datos))
            datos.extend(carga)
        salida += struct.pack("<HHI", etiqueta, tipo, cuenta) + valor
    return salida + struct.pack("<I", 0)  # no hay IFD siguiente


def _tiff(ifd0: dict, exif: dict, gps: dict) -> bytes:
    """Monta el TIFF completo con sus tres directorios encadenados."""
    tam = lambda d: 2 + 12 * len(d) + 4  # noqa: E731

    off_ifd0 = 8
    # IFD0 lleva dos entradas más que las que se piden: los punteros al IFD de
    # EXIF y al de GPS. Hay que contarlas para saber dónde empieza cada bloque.
    n_ifd0 = len(ifd0) + (1 if exif else 0) + (1 if gps else 0)
    off_exif = off_ifd0 + (2 + 12 * n_ifd0 + 4)
    off_gps = off_exif + (tam(exif) if exif else 0)
    inicio_datos = off_gps + (tam(gps) if gps else 0)

    completo = dict(ifd0)
    if exif:
        completo[photo_meta._EXIF_IFD] = _long(off_exif)
    if gps:
        completo[photo_meta._GPS_IFD] = _long(off_gps)

    datos = bytearray()
    bloques = _ifd(completo, inicio_datos, datos)
    if exif:
        bloques += _ifd(exif, inicio_datos, datos)
    if gps:
        bloques += _ifd(gps, inicio_datos, datos)

    return b"II" + struct.pack("<HI", 42, off_ifd0) + bloques + bytes(datos)


def _jpeg_con_exif(tiff: bytes | None) -> bytes:
    """Un JPEG mínimo: SOI, el APP1 con el EXIF, y EOI."""
    jpeg = b"\xff\xd8"
    if tiff is not None:
        cuerpo = b"Exif\x00\x00" + tiff
        jpeg += b"\xff\xe1" + struct.pack(">H", len(cuerpo) + 2) + cuerpo
    # Un segmento de comentario y el fin. Suficiente para que sea recorrible.
    jpeg += b"\xff\xfe" + struct.pack(">H", 6) + b"nada"
    return jpeg + b"\xff\xd9"


def _foto(tmp_path: Path, *, fecha: str | None = "2026:07:28 14:32:05",
          offset: str | None = "+02:00", gps: bool = True,
          camara: bool = True, nombre: str = "IMG_4213.JPG") -> Path:
    """Una foto de mentira con el EXIF que se le pida."""
    ifd0: dict[int, Any] = {}
    if camara:
        ifd0[photo_meta._MAKE] = _ascii("Apple")
        ifd0[photo_meta._MODEL] = _ascii("iPhone 15")

    exif: dict[int, Any] = {}
    if fecha:
        exif[photo_meta._DATETIME_ORIGINAL] = _ascii(fecha)
    if offset:
        exif[photo_meta._OFFSET_TIME_ORIGINAL] = _ascii(offset)

    gps_ifd: dict[int, Any] = {}
    if gps:
        # Cudillero: 43° 33' 42.84" N, 6° 8' 48.12" W
        gps_ifd[photo_meta._GPS_LAT_REF] = _ascii("N")
        gps_ifd[photo_meta._GPS_LAT] = _rational([(43, 1), (33, 1), (4284, 100)])
        gps_ifd[photo_meta._GPS_LON_REF] = _ascii("W")
        gps_ifd[photo_meta._GPS_LON] = _rational([(6, 1), (8, 1), (4812, 100)])
        gps_ifd[photo_meta._GPS_ALT_REF] = _short(0)
        gps_ifd[photo_meta._GPS_ALT] = _rational([(1234, 10)])

    ruta = tmp_path / nombre
    ruta.write_bytes(_jpeg_con_exif(_tiff(ifd0, exif, gps_ifd)))
    return ruta


# ---------------------------------------------------------------------------
# Lo que sí trae metadatos
# ---------------------------------------------------------------------------

def test_lee_fecha_coordenadas_altitud_y_camara(tmp_path: Path) -> None:
    meta = read_metadata(_foto(tmp_path))

    assert meta.archivo == "IMG_4213.JPG"
    assert meta.formato == "JPEG"
    assert meta.capturado_en == "2026-07-28T14:32:05"
    assert meta.offset_original == "+02:00"
    assert meta.lat == pytest.approx(43.5619, abs=1e-4)
    assert meta.lon == pytest.approx(-6.1467, abs=1e-4)
    assert meta.altitud == pytest.approx(123.4)
    assert meta.camara == "Apple iPhone 15"
    assert meta.sirve and meta.ubicada


def test_el_hemisferio_sur_y_el_oeste_salen_negativos(tmp_path: Path) -> None:
    """Es el fallo clásico de leer GPS: quedarse con los grados e ignorar la
    referencia. Una foto de Chile aparecería en Mongolia, y el mapa no daría
    ningún error: solo estaría mal."""
    gps = {
        photo_meta._GPS_LAT_REF: _ascii("S"),
        photo_meta._GPS_LAT: _rational([(33, 1), (26, 1), (5040, 100)]),
        photo_meta._GPS_LON_REF: _ascii("W"),
        photo_meta._GPS_LON: _rational([(70, 1), (39, 1), (3600, 100)]),
    }
    ruta = tmp_path / "santiago.jpg"
    ruta.write_bytes(_jpeg_con_exif(_tiff({}, {}, gps)))

    meta = read_metadata(ruta)
    assert meta.lat == pytest.approx(-33.4473, abs=1e-3)
    assert meta.lon == pytest.approx(-70.6600, abs=1e-3)


def test_la_altitud_bajo_el_nivel_del_mar_sale_negativa(tmp_path: Path) -> None:
    gps = {
        photo_meta._GPS_ALT_REF: _short(1),
        photo_meta._GPS_ALT: _rational([(150, 10)]),
    }
    ruta = tmp_path / "bajo.jpg"
    ruta.write_bytes(_jpeg_con_exif(_tiff({}, {}, gps)))

    assert read_metadata(ruta).altitud == pytest.approx(-15.0)


# ---------------------------------------------------------------------------
# Lo que NO trae metadatos: el caso frecuente, y no es un error
# ---------------------------------------------------------------------------

def test_una_foto_sin_exif_no_es_un_error_sino_un_resultado(tmp_path: Path) -> None:
    """Comprobado contra archivos reales: WhatsApp borra el EXIF entero.

    Que esto lanzara una excepción haría que importar una carpeta con fotos
    reenviadas se parase en la primera. Es un resultado que hay que contar, no
    un fallo que haya que atrapar.
    """
    ruta = tmp_path / "whatsapp.jpg"
    ruta.write_bytes(_jpeg_con_exif(None))

    meta = read_metadata(ruta)
    assert meta.formato == "JPEG"
    assert meta.capturado_en is None
    assert meta.lat is None
    assert not meta.sirve


def test_una_foto_con_fecha_pero_sin_gps_sigue_sirviendo(tmp_path: Path) -> None:
    """Ordena el relato del viaje aunque no ponga un punto en el mapa. Pasa
    siempre que se hace una foto con la ubicación desactivada."""
    meta = read_metadata(_foto(tmp_path, gps=False))

    assert meta.capturado_en == "2026-07-28T14:32:05"
    assert meta.lat is None
    assert meta.sirve
    assert not meta.ubicada


def test_sin_el_desfase_horario_no_se_inventa_ninguno(tmp_path: Path) -> None:
    """`DateTimeOriginal` es hora local SIN huso. Muchas cámaras no escriben
    `OffsetTimeOriginal`, y suponer "+02:00" porque el viaje es por España
    sería inventarse el instante: la misma foto en Canarias estaría una hora
    corrida, y nadie se enteraría."""
    meta = read_metadata(_foto(tmp_path, offset=None))

    assert meta.capturado_en == "2026-07-28T14:32:05"
    assert meta.offset_original is None


def test_media_coordenada_se_descarta_entera(tmp_path: Path) -> None:
    """Una latitud sola no ubica nada, y guardada parecería significar algo.
    Misma regla que en la ingesta y en las notas."""
    gps = {
        photo_meta._GPS_LAT_REF: _ascii("N"),
        photo_meta._GPS_LAT: _rational([(43, 1), (33, 1), (0, 1)]),
    }
    ruta = tmp_path / "media.jpg"
    ruta.write_bytes(_jpeg_con_exif(_tiff({}, {}, gps)))

    meta = read_metadata(ruta)
    assert meta.lat is None and meta.lon is None


# ---------------------------------------------------------------------------
# Archivos rotos: nunca tumban la importación, nunca inventan datos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "contenido, motivo",
    [
        (b"\xff\xd8\xff\xe1\x00\x08Exif\x00\x00", "APP1 truncado a mitad"),
        (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", 10) + b"Exif\x00\x00XX", "TIFF de 2 bytes"),
        (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", 18) + b"Exif\x00\x00" + b"XX" + b"\x00" * 8,
         "orden de bytes desconocido"),
        (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", 18) + b"Exif\x00\x00" + b"II"
         + struct.pack("<HI", 99, 8) + b"\x00" * 4, "número mágico que no es 42"),
        (b"esto no es una imagen en absoluto", "ni siquiera es JPEG"),
        (b"\xff\xd8\xff\xd9", "JPEG vacío"),
    ],
)
def test_un_archivo_roto_devuelve_vacio_y_no_revienta(
    tmp_path: Path, contenido: bytes, motivo: str
) -> None:
    """Importar mil fotos no puede pararse porque una esté corrupta, y sobre
    todo no puede sacar coordenadas de bytes que rimaban."""
    ruta = tmp_path / "roto.jpg"
    ruta.write_bytes(contenido)

    meta = read_metadata(ruta)
    assert meta.capturado_en is None, motivo
    assert meta.lat is None, motivo


def test_no_se_buscan_metadatos_dentro_de_la_imagen_comprimida(tmp_path: Path) -> None:
    """En un JPEG se recorren los segmentos, no se busca la palabra "Exif".

    Un JPEG puede llevar esos bytes dentro de la imagen comprimida por pura
    casualidad. Fabricar coordenadas a partir de eso sería el peor fallo
    posible aquí: una chincheta convincente en un sitio inventado.
    """
    falso = b"Exif\x00\x00II" + struct.pack("<HI", 42, 8) + b"\x00" * 64
    ruta = tmp_path / "trampa.jpg"
    # Los bytes van DESPUÉS del inicio de los datos de imagen (0xFFDA).
    ruta.write_bytes(b"\xff\xd8\xff\xda\x00\x08" + b"\x00" * 4 + falso + b"\xff\xd9")

    meta = read_metadata(ruta)
    assert meta.lat is None
    assert meta.capturado_en is None


def test_un_archivo_que_no_existe_si_es_un_error(tmp_path: Path) -> None:
    """Aquí sí se lanza: "no encuentro el archivo" es un problema de quien
    llama, no una foto sin metadatos."""
    with pytest.raises(PhotoMetaError):
        read_metadata(tmp_path / "no-existe.jpg")


def test_un_archivo_vacio_es_un_error(tmp_path: Path) -> None:
    ruta = tmp_path / "vacio.jpg"
    ruta.write_bytes(b"")

    with pytest.raises(PhotoMetaError):
        read_metadata(ruta)


def test_reconoce_un_contenedor_heic_por_su_cabecera(tmp_path: Path) -> None:
    """El formato se informa aunque no se saque nada: quien importa tiene que
    poder ver "500 HEIC sin metadatos" y saber que el problema es el formato,
    no sus fotos."""
    ruta = tmp_path / "IMG_0001.HEIC"
    ruta.write_bytes(struct.pack(">I", 24) + b"ftypheic" + b"\x00" * 32)

    assert read_metadata(ruta).formato == "HEIC"


def test_en_un_heic_si_se_busca_el_bloque_exif(tmp_path: Path) -> None:
    """La heurística del contenedor, con su validación: solo se acepta si
    detrás viene una cabecera TIFF que cuadra entera."""
    tiff = _tiff({}, {photo_meta._DATETIME_ORIGINAL: _ascii("2026:08:01 09:15:00")}, {})
    ruta = tmp_path / "IMG_0002.HEIC"
    ruta.write_bytes(
        struct.pack(">I", 24) + b"ftypheic" + b"\x00" * 16 + b"Exif\x00\x00" + tiff
    )

    assert read_metadata(ruta).capturado_en == "2026-08-01T09:15:00"
