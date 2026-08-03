"""Todo archivo estático que se nombra tiene que existir en el disco.

Este test existe por un fallo real y de los mudos. Al integrar la marca, los
iconos se movieron de `static/icons/` a `static/brand/` y se repuntaron la
plantilla base y el manifest — pero `chat.js` seguía pidiendo
`/static/icons/icon-192.png` para la notificación del chat. Un icono que no
existe **no da ningún error**: la notificación sale igual, sin icono, y nadie se
entera hasta que mira una captura.

Es la familia de la decisión 42 (`test_frontend_ids.py`) aplicada a los archivos
en vez de a los ids: la frontera entre lo que el código nombra y lo que hay en el
disco no tiene red, y es justo por donde pasan las mudanzas de assets.

Se comprueban las dos formas de nombrar un estático en este proyecto:

  - `url_for('static', filename='…')` en las plantillas Jinja;
  - una ruta `/static/…` escrita a mano en el JavaScript, que es la que se coló.

También se comprueba el manifest, que apunta a los iconos de la PWA y es el otro
sitio donde una ruta rota no da la cara: el navegador se limita a no instalar el
icono.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = RAIZ / "app" / "static"
PLANTILLAS = RAIZ / "app" / "templates"
JS = ESTATICOS / "js"

# `url_for('static', filename='algo')`, con comillas simples o dobles.
URL_FOR = re.compile(r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]""")

# Una ruta absoluta a /static/ escrita a mano.
#
# Se exige que acabe en una EXTENSIÓN y no solo que empiece por `/static/`: los
# comentarios del código citan directorios («los tiles se sirven desde
# /static/vendor/leaflet/») y sin esa condición el test se inventaba archivos que
# nadie pide y fallaba por un texto en prosa.
RUTA_CRUDA = re.compile(r"""/static/([A-Za-z0-9_\-./]+\.[A-Za-z0-9]{2,5})""")


def _fuentes(*directorios: Path, sufijos: tuple[str, ...]) -> list[Path]:
    archivos: list[Path] = []
    for directorio in directorios:
        for sufijo in sufijos:
            archivos.extend(sorted(directorio.glob(f"*{sufijo}")))
    return archivos


def _referencias() -> set[tuple[str, str]]:
    """Devuelve pares (archivo que la nombra, ruta relativa dentro de static)."""
    encontradas: set[tuple[str, str]] = set()

    for ruta in _fuentes(PLANTILLAS, sufijos=(".html",)):
        texto = ruta.read_text(encoding="utf-8")
        for destino in URL_FOR.findall(texto):
            encontradas.add((ruta.name, destino))

    for ruta in _fuentes(JS, sufijos=(".js",)):
        texto = ruta.read_text(encoding="utf-8")
        for destino in RUTA_CRUDA.findall(texto):
            encontradas.add((ruta.name, destino))

    return encontradas


def test_hay_referencias_que_comprobar():
    """Si el buscador deja de encontrar nada, el test pasaría sin probar nada.

    Es el fallo silencioso de este propio archivo: una expresión regular que deja
    de casar no falla, simplemente no comprueba, y el test sigue en verde para
    siempre. Se fija un suelo.
    """
    referencias = _referencias()
    assert len(referencias) >= 10, f"solo se encontraron {len(referencias)} referencias"


@pytest.mark.parametrize("origen,destino", sorted(_referencias()))
def test_el_estatico_existe(origen: str, destino: str):
    archivo = ESTATICOS / destino
    assert archivo.is_file(), f"{origen} pide /static/{destino} y no está en el disco"


def test_los_iconos_del_manifest_existen():
    manifest = json.loads(
        (ESTATICOS / "manifest.webmanifest").read_text(encoding="utf-8")
    )
    for icono in manifest["icons"]:
        # En el manifest la URL es pública (`/static/…`); en el repo cuelga de
        # `app/static/`.
        destino = icono["src"].removeprefix("/static/")
        assert (ESTATICOS / destino).is_file(), f"el manifest pide {icono['src']}"
