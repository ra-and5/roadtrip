"""Semáforo de predespliegue.

Estos tests protegen falsos rojos en producción: el servidor puede tener un
symlink `static` y no tener pytest instalado, y ninguna de las dos cosas debe
bloquear un reload de la app.
"""

from __future__ import annotations

from typing import Any

from tools import pre_despliegue


class ResultadoFalso(pre_despliegue.Resultado):
    def __init__(self) -> None:
        super().__init__()
        self.eventos: list[tuple[str, str, str]] = []

    def ok(self, nombre: str, detalle: str = "") -> None:
        self.eventos.append(("ok", nombre, detalle))

    def aviso(self, nombre: str, detalle: str) -> None:
        super().aviso(nombre, detalle)
        self.eventos.append(("aviso", nombre, detalle))

    def fallo(self, nombre: str, detalle: str) -> None:
        super().fallo(nombre, detalle)
        self.eventos.append(("fallo", nombre, detalle))


def test_pytest_ausente_es_aviso_no_fallo(monkeypatch: Any) -> None:
    res = ResultadoFalso()
    monkeypatch.setattr(pre_despliegue.importlib.util, "find_spec", lambda nombre: None)

    pre_despliegue.comprobar_pytest(res)

    assert res.fallos == 0
    assert res.avisos == 1
    assert res.eventos[0][0] == "aviso"
    assert res.eventos[0][1] == "pytest"
    assert "requirements-dev.txt" in res.eventos[0][2]


def test_static_raiz_esta_ignorado() -> None:
    gitignore = (pre_despliegue.RAIZ / ".gitignore").read_text(encoding="utf-8")

    assert "/static/" in gitignore
    assert "/static" in gitignore
