"""Miniaturas: nombres seguros, presupuesto y borrado.

Lo que se prueba aquí es lo que decide algo: que el nombre no salga nunca del
cliente, que una imagen mala no tumbe el lote, que el presupuesto se respete y
que quitar una foto del álbum se lleve su miniatura. Lo demás es escribir un
archivo, que ya sabe hacer el sistema operativo.
"""

from __future__ import annotations

import pytest

from app.modules import miniaturas

# Un JPEG mínimo válido para lo que mira `guardar()`: la firma real del formato.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(autouse=True)
def directorio_temporal(tmp_path, monkeypatch):
    """Cada test con su carpeta. Sin esto se escribiría en la de verdad."""
    monkeypatch.setattr(miniaturas.Config, "UPLOAD_DIR", tmp_path)
    return tmp_path


# --- El nombre ------------------------------------------------------------

def test_el_nombre_no_contiene_nada_del_cliente():
    """Un nombre hostil no puede convertirse en una ruta.

    Es la decisión 27: el nombre sale de nosotros. Si en vez de derivarlo se
    saneara, `secure_filename()` podría colapsar dos nombres distintos en el
    mismo archivo y una foto se comería la miniatura de otra sin dar error.
    """
    nombre = miniaturas.nombre_de("fotos", "../../etc/passwd")
    assert "/" not in nombre and ".." not in nombre
    assert nombre.endswith(".jpg") and len(nombre) == 36


def test_el_nombre_es_determinista_y_distingue_fuentes():
    assert miniaturas.nombre_de("fotos", "A.jpg") == miniaturas.nombre_de("fotos", "A.jpg")
    assert miniaturas.nombre_de("fotos", "A.jpg") != miniaturas.nombre_de("otra", "A.jpg")


def test_el_separador_impide_colisiones_entre_campos():
    """Sin el `\\0`, ("ab", "c") y ("a", "bc") darían el mismo hash."""
    assert miniaturas.nombre_de("ab", "c") != miniaturas.nombre_de("a", "bc")


# --- Servir ---------------------------------------------------------------

@pytest.mark.parametrize("nombre", [
    "../../../etc/passwd",
    "..%2fetc%2fpasswd",
    "a" * 32 + ".png",
    "ZZZZ" + "0" * 28 + ".jpg",   # no es hexadecimal
    "corto.jpg",
])
def test_no_se_sirve_nada_que_no_tenga_nuestra_forma(nombre):
    assert miniaturas.ruta_servible(nombre) is None


def test_se_sirve_lo_que_hemos_guardado():
    miniaturas.guardar("fotos", "IMG_1.HEIC", JPEG)
    ruta = miniaturas.ruta_servible(miniaturas.nombre_de("fotos", "IMG_1.HEIC"))
    assert ruta is not None and ruta.read_bytes() == JPEG


def test_un_nombre_con_nuestra_forma_pero_sin_archivo_no_se_sirve():
    assert miniaturas.ruta_servible("0" * 32 + ".jpg") is None


# --- Validación -----------------------------------------------------------

def test_lo_que_no_es_jpeg_se_rechaza():
    """Se mira la FIRMA, no la extensión: la extensión la pone el cliente."""
    with pytest.raises(miniaturas.MiniaturaError, match="no es un JPEG"):
        miniaturas.guardar("fotos", "mentira.jpg", b"<html>no soy una foto</html>")


def test_una_imagen_demasiado_grande_se_rechaza():
    grande = b"\xff\xd8\xff" + b"\x00" * miniaturas.MAX_BYTES
    with pytest.raises(miniaturas.MiniaturaError, match="KiB"):
        miniaturas.guardar("fotos", "gorda.jpg", grande)


def test_una_imagen_vacia_se_rechaza():
    with pytest.raises(miniaturas.MiniaturaError, match="vacía"):
        miniaturas.guardar("fotos", "vacia.jpg", b"")


def test_un_nombre_vacio_se_rechaza():
    with pytest.raises(miniaturas.MiniaturaError):
        miniaturas.guardar("fotos", "", JPEG)


def test_un_rechazo_no_deja_archivos_a_medias(directorio_temporal):
    """Ni el temporal ni el destino: un huérfano gasta cuota y no lo ve nadie."""
    with pytest.raises(miniaturas.MiniaturaError):
        miniaturas.guardar("fotos", "mala.jpg", b"no")
    directorio = directorio_temporal / "miniaturas"
    assert not directorio.exists() or list(directorio.iterdir()) == []


# --- Guardar --------------------------------------------------------------

def test_reenviar_la_misma_no_reescribe():
    """Reenviar el álbum entero es lo NORMAL (decisión 45), no una avería."""
    assert miniaturas.guardar("fotos", "IMG_1.HEIC", JPEG) is True
    assert miniaturas.guardar("fotos", "IMG_1.HEIC", JPEG) is False


def test_existe_dice_la_verdad():
    assert miniaturas.existe("fotos", "IMG_1.HEIC") is False
    miniaturas.guardar("fotos", "IMG_1.HEIC", JPEG)
    assert miniaturas.existe("fotos", "IMG_1.HEIC") is True


def test_no_quedan_temporales_tras_guardar(directorio_temporal):
    miniaturas.guardar("fotos", "IMG_1.HEIC", JPEG)
    archivos = list((directorio_temporal / "miniaturas").iterdir())
    assert len(archivos) == 1 and not archivos[0].name.endswith(".parcial")


# --- Borrado --------------------------------------------------------------

def test_borrar_se_lleva_solo_lo_pedido():
    miniaturas.guardar("fotos", "SE_QUEDA.jpg", JPEG)
    miniaturas.guardar("fotos", "SE_VA.jpg", JPEG)

    assert miniaturas.borrar("fotos", ["SE_VA.jpg"]) == 1
    assert miniaturas.existe("fotos", "SE_VA.jpg") is False
    assert miniaturas.existe("fotos", "SE_QUEDA.jpg") is True


def test_borrar_lo_que_nunca_tuvo_miniatura_no_es_un_error():
    """Lo normal: la mayoría de las fotos antiguas no tienen miniatura."""
    assert miniaturas.borrar("fotos", ["JAMAS_EXISTIO.jpg"]) == 0


def test_borrar_esta_acotado_a_la_fuente():
    """El álbum del iPhone no puede tocar lo que entró por el portátil."""
    miniaturas.guardar("fotos", "A.jpg", JPEG)
    assert miniaturas.borrar("otra-fuente", ["A.jpg"]) == 0
    assert miniaturas.existe("fotos", "A.jpg") is True


# --- Presupuesto ----------------------------------------------------------

def test_con_el_directorio_vacio_hay_sitio():
    cabe, usado = miniaturas.hay_sitio()
    assert cabe is True and usado == 0.0


def test_cuando_se_pasa_la_cuota_no_cabe(monkeypatch):
    """Se rechaza y se dice; NO se borran las más antiguas.

    Es la asimetría de la decisión 45: una miniatura de más se quita a mano, y
    una borrada sola es una foto del viaje que desaparece sin que nadie lo pida.
    """
    miniaturas.guardar("fotos", "A.jpg", JPEG)
    monkeypatch.setattr(miniaturas, "CUOTA_MB", 0.0)
    cabe, usado = miniaturas.hay_sitio()
    assert cabe is False and usado > 0
