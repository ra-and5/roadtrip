"""Punto de entrada WSGI para PythonAnywhere.

En el panel de PythonAnywhere, la sección "Code" → "WSGI configuration file"
debe apuntar a un archivo cuyo contenido sea:

    import sys
    path = '/home/TU_USUARIO/roadtrip'
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application

PythonAnywhere busca por convención una variable llamada `application`.
"""

import os
import sys

# Aseguramos que la raíz del proyecto esté en el path de importación, para que
# `from app.app import app` funcione sin depender de dónde arranque el proceso.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.app import app as application  # noqa: E402
