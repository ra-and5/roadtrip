"""AEMET OpenData para el copiloto territorial. Sin red ni API keys."""

from __future__ import annotations

import io
import tarfile
from typing import Any, Iterator

import pytest

from app.config import Config
from app.modules import aemet, storage


def _alert_xml(evento: str, severidad: str, zona: str) -> str:
    return f"""\
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <info>
    <event>{evento}</event>
    <urgency>Expected</urgency>
    <severity>{severidad}</severity>
    <area><areaDesc>{zona}</areaDesc></area>
    <onset>2026-08-03T11:00:00+02:00</onset>
    <expires>2026-08-03T20:00:00+02:00</expires>
  </info>
</alert>
"""


def _fabricar_tar(archivos: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for nombre, contenido in archivos.items():
            datos = contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(datos)
            tar.addfile(info, io.BytesIO(datos))
    return buffer.getvalue()


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


def test_el_tar_descarta_el_nivel_verde_y_ordena_por_severidad() -> None:
    """AEMET empaqueta un CAP XML por zona en un TAR, y la mayoría son
    'nivel verde' (severity=Minor): la referencia sin riesgo que emite a
    diario para cada provincia. Enseñarlos ahogaría el aviso rojo real."""
    contenido = _fabricar_tar(
        {
            "z1.xml": _alert_xml("Aviso de lluvias de nivel verde", "Minor", "Álava"),
            "z2.xml": _alert_xml("Aviso de calor de nivel amarillo", "Moderate", "Sevilla"),
            "z3.xml": _alert_xml("Aviso de calor de nivel rojo", "Extreme", "Gran Canaria"),
        }
    )

    avisos = aemet._parse_avisos_tar(contenido)  # noqa: SLF001

    assert len(avisos) == 2
    assert "Gran Canaria" in avisos[0]
    assert "Sevilla" in avisos[1]
    assert not any("Álava" in a for a in avisos)


def test_un_tar_roto_cae_al_parseo_de_xml_plano() -> None:
    """Si AEMET cambia el envoltorio, no se rinde de golpe: lo intenta como
    el XML plano que devolvía antes de empaquetar en TAR (decisión 9)."""
    xml = _alert_xml("Aviso de tormentas de nivel rojo", "Extreme", "Huesca").encode("utf-8")

    avisos = aemet._parse_avisos_tar(xml)  # noqa: SLF001

    assert len(avisos) == 1
    assert "Huesca" in avisos[0]


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
