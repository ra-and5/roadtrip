"""La herramienta que dice si la app está limpia, y la que borra.

Lo que se prueba es lo único que puede hacer daño: que `--limpiar` toque SOLO lo
simulado, y que el reset completo no borre nada sin la palabra escrita entera. Un
fallo aquí no da error: se lleva el viaje por delante y se ve al día siguiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules import metricas, storage  # noqa: E402
from tools import estado_limpio  # noqa: E402


def _muestra(fuente: str, medido_en: str) -> dict[str, object]:
    return {
        "fuente": fuente,
        "medido_en": medido_en,
        "offset_original": "+02:00",
        "recibido_en": medido_en,
        "pasos": 1000,
        "bateria": 50,
        "lat": 43.5,
        "lon": -6.1,
    }


@pytest.fixture
def con_datos(tmp_path, monkeypatch):
    """Una base de datos con un poco de todo: real, simulado y viaje."""
    monkeypatch.setattr(storage.Config, "DB_PATH", tmp_path / "prueba.db")
    monkeypatch.setattr(estado_limpio.Config, "UPLOAD_DIR", tmp_path)
    storage.init_db()

    storage.insert_telemetry([
        _muestra(metricas.FUENTE_REAL, "2026-07-30T08:00:00+00:00"),
        _muestra(metricas.FUENTE_REAL, "2026-07-30T09:00:00+00:00"),
        _muestra("simulado", "2026-07-29T08:00:00+00:00"),
        _muestra("simulado", "2026-07-29T09:00:00+00:00"),
        _muestra("simulado", "2026-07-29T10:00:00+00:00"),
    ])
    storage.insert_note({
        "client_id": "11111111-1111-4111-8111-111111111111",
        "text": "Una nota de verdad",
        "photo_path": None,
        "lat": 43.5, "lon": -6.1,
        "place_name": "Cudillero", "region": "Asturias",
        "created_at": "2026-07-30T10:00:00+00:00",
        "offset_original": "+02:00",
        "received_at": "2026-07-30T10:00:01+00:00",
    })
    return tmp_path


def test_el_inventario_separa_lo_real_de_lo_simulado(con_datos):
    datos = estado_limpio.inventario()
    assert datos["telemetria_real"] == 2
    assert datos["telemetria_simulada"] == 3
    assert datos["notas"] == 1


def test_limpiar_borra_lo_simulado_y_NO_toca_lo_real(con_datos):
    """La garantía de la decisión 36 en el momento de borrar.

    Las dos series viven en paralelo gracias a `UNIQUE(fuente, medido_en)`. Si
    esta limpieza se llevara por delante una muestra real, se estaría destruyendo
    justamente lo único que puede cerrar la Fase 2d.
    """
    estado_limpio.limpiar_simulado()

    datos = estado_limpio.inventario()
    assert datos["telemetria_simulada"] == 0
    assert datos["telemetria_real"] == 2
    assert datos["notas"] == 1, "limpiar lo simulado no puede tocar el viaje"


def test_limpiar_dos_veces_no_falla(con_datos):
    estado_limpio.limpiar_simulado()
    estado_limpio.limpiar_simulado()
    assert estado_limpio.inventario()["telemetria_simulada"] == 0


def test_el_reset_no_borra_nada_sin_la_palabra_exacta(con_datos, monkeypatch):
    """Un `s/n` se contesta por inercia; al otro lado hay un mes de viaje."""
    for respuesta in ("", "s", "si", "borrar", "BORRAR TODO"):
        monkeypatch.setattr("builtins.input", lambda _="": respuesta)
        estado_limpio.borrar_el_viaje()
        assert estado_limpio.inventario()["notas"] == 1, f"borró con {respuesta!r}"


def test_el_reset_borra_cuando_se_confirma(con_datos):
    import builtins

    original = builtins.input
    builtins.input = lambda _="": "BORRAR"
    try:
        estado_limpio.borrar_el_viaje()
    finally:
        builtins.input = original

    datos = estado_limpio.inventario()
    assert datos["notas"] == 0
    assert datos["fotos"] == 0
    assert datos["telemetria_real"] == 0
    assert datos["telemetria_simulada"] == 0


def test_el_reset_deja_la_cache(con_datos):
    """La caché no es dato del viaje: borrarla solo hace lenta la vuelta."""
    storage.cache_set("una-clave", {"algo": 1})
    assert estado_limpio.inventario()["cache"] == 1

    import builtins

    original = builtins.input
    builtins.input = lambda _="": "BORRAR"
    try:
        estado_limpio.borrar_el_viaje()
    finally:
        builtins.input = original

    assert estado_limpio.inventario()["cache"] == 1


def test_informar_avisa_solo_cuando_hay_simulado(con_datos, capsys):
    assert estado_limpio.informar(estado_limpio.inventario()) is True
    estado_limpio.limpiar_simulado()
    assert estado_limpio.informar(estado_limpio.inventario()) is False
    assert "Sin datos simulados" in capsys.readouterr().out
