"""AEMET OpenData para el copiloto territorial. Sin red ni API keys."""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from app.config import Config
from app.modules import aemet, storage


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    yield


def test_parsea_avisos_cap_compactos() -> None:
    xml = """\
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <info>
    <event>Lluvias</event>
    <urgency>Future</urgency>
    <severity>Severe</severity>
    <area><areaDesc>Cantábrico occidental</areaDesc></area>
    <onset>2026-07-31T12:00:00+02:00</onset>
    <expires>2026-07-31T20:00:00+02:00</expires>
  </info>
</alert>
"""

    avisos = aemet._parse_avisos_cap(xml)  # noqa: SLF001

    assert len(avisos) == 1
    assert "Lluvias" in avisos[0]
    assert "Cantábrico occidental" in avisos[0]
    assert "Severe" in avisos[0]


def test_parsea_prediccion_json_de_aemet() -> None:
    texto = '[{"texto": "Chubascos en el norte y calor en el sur."}]'

    assert aemet._parse_prediccion_textual(texto) == [
        "Chubascos en el norte y calor en el sur."
    ]


def test_sin_key_degrada_por_piezas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "AEMET_API_KEY", "")

    informe = aemet.informe_territorio()

    assert not informe.prediccion
    assert not informe.avisos
    assert any("AEMET_API_KEY" in aviso for aviso in informe.avisos_herramienta)


class AemetFalsa:
    def prediccion_nacional(self) -> list[str]:
        return ["hoy: lluvia en Galicia"]

    def avisos_espana(self) -> list[str]:
        return ["Lluvias · Severe · Galicia"]

    def radar_nacional(self) -> str:
        return "Radar nacional disponible: https://aemet.example/radar.png"


def test_formatea_informe_para_prompt() -> None:
    informe = aemet.informe_territorio(incluir_radar=True, client=AemetFalsa())  # type: ignore[arg-type]

    lineas = aemet.formatear(informe)

    assert "AEMET_TERRITORIO:" in lineas
    assert any("lluvia en Galicia" in linea for linea in lineas)
    assert any("Radar nacional" in linea for linea in lineas)
