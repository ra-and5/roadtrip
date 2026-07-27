"""Genera el token de ingesta del iPhone y su hash para el .env.

Uso:
    python tools/token_ingesta.py

Imprime DOS cosas distintas y hay que llevarlas a sitios distintos:

    INGEST_TOKEN       -> se pega dentro del atajo del iPhone. NO va al .env.
    INGEST_TOKEN_HASH  -> se pega en el .env del servidor. NO va al atajo.

El token se genera aquí, con `secrets.token_urlsafe(32)`, y no se lo inventa
nadie a mano: un token elegido por una persona es corto, memorizable y por
tanto adivinable, y este endpoint está expuesto en internet las 24 horas.

Va en su propio script y no dentro de `hash_password.py` porque son dos
secretos con vidas independientes: si pierdes el móvil quieres rotar el token
sin tocar la contraseña de la web, y al revés. Un script que los regenerase
juntos empujaría justo a lo contrario.
"""

import secrets
import sys

from werkzeug.security import generate_password_hash

_LONGITUD_BYTES = 32

# Se fija PBKDF2 en vez de dejar el algoritmo por defecto de Werkzeug, que hoy
# es **scrypt**. No es una preferencia criptográfica —scrypt es mejor función de
# contraseña— sino una consecuencia de dónde corre esto y de quién puede
# llamarlo:
#
#   - scrypt es "memory-hard" a propósito: con los parámetros por defecto
#     (32768:8:1) cada verificación reserva ~32 MB de RAM.
#   - Este endpoint es PÚBLICO, y cada intento fallido paga esa verificación.
#     Quien quiera hacer daño solo tiene que enviar tokens al azar.
#   - En PythonAnywhere gratuito la memoria del worker es escasa, y quedarse
#     sin memoria no degrada: tira el proceso, y con él toda la app.
#
# PBKDF2 es igual de constante en tiempo y gasta memoria despreciable: el coste
# se queda en CPU (~100 ms), que a una petición por hora es irrelevante y que,
# si alguien insiste, ralentiza pero no mata.
_METODO = "pbkdf2:sha256"


def main() -> None:
    token = secrets.token_urlsafe(_LONGITUD_BYTES)

    print("\n" + "=" * 64)
    print("1. ESTO va en el .env del servidor (y en PythonAnywhere):")
    print("=" * 64)
    print(f"INGEST_TOKEN_HASH={generate_password_hash(token, method=_METODO)}")

    print("\n" + "=" * 64)
    print("2. ESTO va dentro del atajo del iPhone, en la cabecera")
    print("   Authorization, con el prefijo 'Bearer ' delante:")
    print("=" * 64)
    print(f"Bearer {token}")

    print(
        "\nAvisos:\n"
        "  - El token en claro NO se guarda en ningún sitio del servidor. Si lo\n"
        "    pierdes, no se recupera: se genera otro y se actualizan las dos\n"
        "    puntas (el .env y el atajo).\n"
        "  - NO uses aquí la contraseña de la app, ni al revés. Son dos secretos\n"
        "    distintos: este vive en claro dentro del iPhone.\n"
        "  - El atajo que contenga este token NO se comparte con nadie: al\n"
        "    compartir un atajo se comparte también lo que lleva escrito dentro.\n"
        "  - Tras cambiar el .env en PythonAnywhere hay que pulsar Reload.\n"
    )

    if not sys.stdout.isatty():
        print("(Salida redirigida: acuérdate de borrar el archivo al terminar.)")


if __name__ == "__main__":
    main()
