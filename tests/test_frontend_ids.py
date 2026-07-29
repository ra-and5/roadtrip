"""Todo id que el JavaScript toca tiene que existir en la plantilla que lo sirve.

Este test existe por un fallo real y del tipo caro (decisión 11): al llevarse las
métricas de Inicio a Perfil (decisión 40) desapareció `metricas-card` del HTML,
pero `app.js` lo seguía escondiendo en `hideAll()`. Resultado: pulsar *¿Dónde
estoy?* —el botón principal de la app— lanzaba un `TypeError` antes del `try`,
así que el botón se quedaba deshabilitado **para siempre** y la pantalla en
blanco, **sin un solo mensaje de error**. La suite entera pasaba: los 506 tests
eran de Python y el HTML lo pinta el navegador.

Lo que se comprueba es lo mínimo que se puede comprobar sin un navegador: que
cada id que el JavaScript nombra existe en el HTML. No sustituye a abrir la
página, pero caza exactamente esta clase de desincronización, que es la que
aparece cada vez que algo se mueve de pantalla.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
JS = RAIZ / "app" / "static" / "js"
PLANTILLAS = RAIZ / "app" / "templates"

# Qué script pinta qué página. `base.html` va siempre porque la navegación y el
# esqueleto viven ahí.
PAGINAS = {
    "app.js": "index.html",
    "notas.js": "index.html",
    "perfil.js": "perfil.html",
    "mapa.js": "mapa.html",
    "chat.js": "chat.html",
}

# Las tres formas que tiene este frontend de nombrar un id: la directa y los dos
# ayudantes (`show`/`text`/`el`) que envuelven `getElementById`.
LLAMADAS = re.compile(r'(?:getElementById|show|text|el)\(\s*"([^"]+)"')

# `SECTIONS` es una lista de ids suelta, sin llamada alrededor: se lee aparte
# porque es justamente donde estaba el id muerto.
LISTA_SECTIONS = re.compile(r"const SECTIONS = \[(.*?)\]", re.DOTALL)


def _ids_referenciados(fuente: str) -> set[str]:
    ids = set(LLAMADAS.findall(fuente))
    for bloque in LISTA_SECTIONS.findall(fuente):
        ids.update(re.findall(r'"([^"]+)"', bloque))
    return ids


def _ids_del_html(*rutas: Path) -> set[str]:
    ids: set[str] = set()
    for ruta in rutas:
        ids.update(re.findall(r'id="([^"]+)"', ruta.read_text(encoding="utf-8")))
    return ids


@pytest.mark.parametrize("script,plantilla", sorted(PAGINAS.items()))
def test_los_ids_del_js_existen_en_su_plantilla(script: str, plantilla: str) -> None:
    referenciados = _ids_referenciados((JS / script).read_text(encoding="utf-8"))
    existentes = _ids_del_html(PLANTILLAS / plantilla, PLANTILLAS / "base.html")

    faltan = sorted(referenciados - existentes)
    assert not faltan, (
        f"{script} toca ids que no existen en {plantilla}: {faltan}. "
        "Un id muerto no da error en la suite: revienta en el navegador y, si "
        "cae fuera de un try, deja el botón bloqueado sin ningún mensaje."
    )
