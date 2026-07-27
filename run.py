"""Punto de entrada para desarrollo local.

    python run.py

Este archivo NO se usa en producción: PythonAnywhere importa `wsgi.py`.

Está en la raíz del proyecto a propósito. Al ejecutarlo, Python añade la raíz
al path de importación, y por eso los `from app.modules...` funcionan desde
cualquier archivo del proyecto.
"""

from app.app import app

if __name__ == "__main__":
    # Solo escuchamos en 127.0.0.1: la geolocalización del navegador requiere
    # HTTPS o localhost, así que exponerlo a la red local no serviría de nada
    # (y sería exponer una app en modo debug).
    app.run(host="127.0.0.1", port=5000, debug=True)
