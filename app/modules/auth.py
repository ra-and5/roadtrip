"""Autenticación mínima para un solo usuario.

No hay tabla de usuarios ni registro: hay UNA contraseña, guardada como hash
en una variable de entorno. Si más adelante hiciera falta multiusuario, este
módulo es el único que habría que reescribir.

Por qué no Flask-Login: Flask-Login existe para gestionar *conjuntos* de
usuarios (cargar un usuario por id, "recordarme", vistas anónimas...). Con un
usuario, todo eso es peso muerto. Esto son 30 líneas que entiendes al
completo.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash

from app.config import Config

# Clave que ponemos en la cookie de sesión (firmada por Flask con SECRET_KEY,
# así que el cliente no puede falsificarla).
_SESSION_KEY = "authenticated"

F = TypeVar("F", bound=Callable[..., Any])


def check_password(password: str) -> bool:
    """Comprueba la contraseña contra el hash configurado.

    `check_password_hash` de Werkzeug hace la comparación en tiempo constante,
    lo que evita ataques de temporización. Nunca compares hashes con `==`.
    """
    if not password:
        return False
    return check_password_hash(Config.APP_PASSWORD_HASH, password)


def login_user() -> None:
    """Marca la sesión actual como autenticada."""
    session[_SESSION_KEY] = True
    # La sesión sobrevive al cierre del navegador. Es lo que quieres en una
    # PWA instalada en el móvil: no quieres teclear la contraseña cada vez que
    # abres la app en mitad de la montaña.
    session.permanent = True


def logout_user() -> None:
    """Cierra la sesión."""
    session.pop(_SESSION_KEY, None)


def is_logged_in() -> bool:
    """¿La petición actual viene de una sesión autenticada?"""
    return bool(session.get(_SESSION_KEY))


def login_required(view: F) -> F:
    """Decorador que protege una vista.

    Un decorador en Python envuelve una función en otra. Aquí: antes de
    ejecutar la vista, comprobamos la sesión; si no hay, redirigimos al login.

    `@wraps(view)` copia el nombre y el docstring de la función original al
    wrapper. Sin eso, Flask vería todas las vistas llamadas "wrapper" y las
    rutas chocarían entre sí.
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not is_logged_in():
            # Las rutas de API devuelven JSON, no una redirección HTML: si el
            # fetch() de JavaScript recibe una página de login, se rompe de
            # forma confusa. Un 401 explícito es manejable desde el cliente.
            if request.path.startswith("/api/"):
                return {"error": "no_autenticado"}, 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return cast(F, wrapper)
