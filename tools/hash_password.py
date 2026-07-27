"""Genera el valor de APP_PASSWORD_HASH para el archivo .env.

Uso:
    python tools/hash_password.py

Pide la contraseña sin mostrarla por pantalla y escupe el hash. Copia esa
línea entera en tu .env (y en las variables de entorno de PythonAnywhere).

La contraseña en claro nunca se guarda en ningún sitio: solo su hash. Si
alguien te roba el .env, no puede recuperar la contraseña a partir del hash.
"""

import getpass
import secrets

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass.getpass("Contraseña nueva: ")
    if len(password) < 8:
        print("Demasiado corta: usa al menos 8 caracteres.")
        return
    if password != getpass.getpass("Repítela: "):
        print("No coinciden.")
        return

    print("\nCopia estas líneas en tu .env:\n")
    print(f"SECRET_KEY={secrets.token_hex(32)}")
    print(f"APP_PASSWORD_HASH={generate_password_hash(password)}")


if __name__ == "__main__":
    main()
