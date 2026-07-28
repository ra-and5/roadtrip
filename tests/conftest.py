"""Corta la red para TODA la suite.

`CLAUDE.md` dice desde la Fase 1 que los tests no deben necesitar conexión ni
API keys: tienen que poder correr en un camper sin cobertura. Hasta ahora eso
era una regla escrita y no comprobada, y por eso se coló: al añadir la luna, un
fixture dobló Nominatim y Open-Meteo pero **no** met.no, así que tres tests
salieron a internet de verdad. Pasaban en un portátil con wifi y habrían
fallado justo donde importa.

Un fallo así no da error mientras haya red. Lo que hace este archivo es
convertirlo en imposible: cualquier intento de abrir un socket revienta con un
mensaje que dice qué test lo ha hecho y qué doblar.

`pytest` carga `conftest.py` automáticamente, sin importarlo nadie: por eso
basta con que exista.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest


class RedProhibida(RuntimeError):
    """Un test ha intentado salir a internet."""


_connect_real = socket.socket.connect


@pytest.fixture(autouse=True)
def sin_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nadie sale a la red durante un test.

    Se corta en `socket.connect` y no en `requests`, a propósito: `requests` es
    la librería que se usa hoy, y bloquear solo a ella dejaría pasar cualquier
    cosa que mañana use `urllib` o un SDK con su propio cliente HTTP. El socket
    es el sitio por el que pasan todos.

    Se deja pasar `AF_UNIX`: no es internet, y algunas herramientas lo usan
    para hablar consigo mismas.
    """

    def _bloqueado(self: socket.socket, direccion: Any, *args: Any, **kwargs: Any) -> None:
        if self.family == getattr(socket, "AF_UNIX", None):
            return _connect_real(self, direccion, *args, **kwargs)
        raise RedProhibida(
            f"Un test ha intentado conectarse a {direccion!r}. La suite tiene que "
            f"correr sin cobertura: dobla esa llamada en vez de dejarla salir."
        )

    monkeypatch.setattr(socket.socket, "connect", _bloqueado)
