"""Tests del panel. Sin red y sin API keys.

Lo que se protege aquí, que es lo que hace que la pantalla no mienta:

  - un día sin muestras se dibuja como HUECO y no como cero;
  - una serie simulada no puede declararse fiable a sí misma (decisión 36);
  - sin huecos no basta para dar la telemetría por buena: seis envíos diarios
    que entregan dos dan cero huecos y no son una serie;
  - el día del viaje cuenta días de calendario, no días con notas.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterator

import pytest

from app.app import app as flask_app
from app.config import Config
from app.modules import panel, storage

HOY = date(2026, 7, 29)
AHORA = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _muestra(fecha: str, hora: int, pasos: int, fuente: str = "atajos-iphone") -> dict:
    return {
        "medido_en": f"{fecha}T{hora:02d}:00:00+00:00",
        "offset_original": "+02:00",
        "fuente": fuente,
        "pasos": pasos,
        "bateria": 70,
    }


def _dia_completo(fecha: str, pasos: int, fuente: str = "atajos-iphone") -> list[dict]:
    """Las seis automatizaciones del día, con el acumulado creciendo."""
    return [
        _muestra(fecha, hora, round(pasos * (i + 1) / 6), fuente)
        for i, hora in enumerate((6, 10, 14, 16, 18, 21))
    ]


def _nota(created_at: str, texto: str = "Aquí", region: str = "Asturias") -> dict:
    return {
        "id": 1, "client_id": "x", "text": texto, "lat": 43.56, "lon": -6.14,
        "place_name": "Cudillero", "region": region,
        "created_at": created_at, "created_at_local": created_at,
        "offset_original": "+02:00",
    }


def _armar(reales: list[dict], todas: list[dict] | None = None, **kwargs: Any) -> panel.Panel:
    return panel.armar(
        reales,
        todas if todas is not None else reales,
        kwargs.get("notas", []),
        kwargs.get("puntos", []),
        kwargs.get("dias", {"total": 0, "primero": None, "ultimo": None, "huecos": None}),
        HOY,
        ahora=AHORA,
    )


def _fuente(p: panel.Panel, clave: str) -> panel.EstadoFuente | None:
    return next((f for f in p.fuentes if f.clave == clave), None)


# --- La serie de pasos -------------------------------------------------------

def test_un_dia_sin_muestras_es_un_hueco_y_no_un_cero() -> None:
    """Un cero dice "no anduvo"; un hueco dice "no lo sé"."""
    p = _armar(_dia_completo("2026-07-27", 8000) + _dia_completo("2026-07-29", 5000))

    por_fecha = {b.fecha: b.pasos for b in p.serie}
    assert por_fecha["2026-07-27"] == 8000
    assert por_fecha["2026-07-28"] is None
    assert por_fecha["2026-07-29"] == 5000


def test_la_serie_llega_hasta_hoy_aunque_la_fuente_se_haya_parado() -> None:
    """Una serie que se paró el viernes tiene que verse parada."""
    p = _armar(_dia_completo("2026-07-25", 9000))

    assert len(p.serie) == panel.DIAS_DE_SERIE
    assert p.serie[-1].fecha == HOY.isoformat()
    assert p.serie[-1].es_hoy
    assert p.serie[-1].pasos is None


# --- Fiabilidad --------------------------------------------------------------

def test_lo_simulado_no_puede_declararse_fiable_a_si_mismo() -> None:
    """La decisión 36 separa las series en la tabla; leerlas juntas la anularía."""
    simuladas = _dia_completo("2026-07-28", 12000, "simulado") + \
        _dia_completo("2026-07-29", 9000, "simulado")

    p = _armar([], simuladas)

    # Los pasos se enseñan (si no, el panel saldría vacío y no se entendería),
    # pero la fuente dice que no hay ni una muestra real.
    assert p.cuerpo.pasos_hoy == 9000
    assert p.hay_simulado
    assert _fuente(p, "telemetria").estado == panel.SIN_DATOS
    assert _fuente(p, "simulado").estado == panel.SIMULADA


def test_sin_nada_simulado_no_aparece_esa_fila() -> None:
    p = _armar(_dia_completo("2026-07-29", 7000))

    assert _fuente(p, "simulado") is None
    assert not p.hay_simulado


def test_dias_seguidos_y_completos_dan_la_telemetria_por_demostrada() -> None:
    reales = [
        m
        for dia in ("2026-07-27", "2026-07-28", "2026-07-29")
        for m in _dia_completo(dia, 8000)
    ]

    p = _armar(reales)

    assert _fuente(p, "telemetria").estado == panel.DEMOSTRADA


def test_dias_a_medias_no_son_huecos_pero_tampoco_son_fiables() -> None:
    """Seis envíos al día que entregan dos dan cero huecos y no son una serie."""
    reales = [_muestra("2026-07-28", 10, 4000), _muestra("2026-07-29", 10, 3000)]

    p = _armar(reales)

    fuente = _fuente(p, "telemetria")
    assert fuente.estado == panel.CON_HUECOS
    assert "a medias" in fuente.detalle


def test_las_notas_y_las_fotos_son_fuentes_demostradas() -> None:
    p = _armar(
        [],
        notas=[_nota("2026-07-28T10:00:00+00:00")],
        puntos=[{"lat": 43.5, "lon": -6.1, "capturado_en": "2026-07-28T10:00:00"}],
    )

    assert _fuente(p, "notas").estado == panel.DEMOSTRADA
    assert _fuente(p, "fotos").estado == panel.DEMOSTRADA


def test_dias_registrados_con_huecos_no_pasan_por_completos() -> None:
    p = _armar([], dias={"total": 3, "primero": "2026-07-25", "ultimo": "2026-07-29",
                         "huecos": 2})

    fuente = _fuente(p, "lugar_del_dia")
    assert fuente.estado == panel.CON_HUECOS
    assert "2 sin registrar" in fuente.detalle


# --- El día del viaje --------------------------------------------------------

def test_el_dia_del_viaje_cuenta_calendario_y_no_dias_con_notas() -> None:
    """Un día de carretera sin notas sigue siendo un día de viaje."""
    p = _armar([], notas=[
        _nota("2026-07-20T10:00:00+00:00"),
        _nota("2026-07-29T10:00:00+00:00"),
    ])

    assert p.viaje.dias == 2          # días con algo dentro
    assert p.dia_del_viaje == 10      # del 20 al 29, ambos incluidos


def test_sin_nada_registrado_no_se_inventa_un_dia_del_viaje() -> None:
    assert _armar([]).dia_del_viaje is None


# --- El endpoint -------------------------------------------------------------

@pytest.fixture
def sesion(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    storage.init_db()
    flask_app.config["TESTING"] = True
    cliente = flask_app.test_client()
    with cliente.session_transaction() as s:
        s["authenticated"] = True
    yield cliente


def test_el_panel_responde_con_la_base_de_datos_vacia(sesion: Any) -> None:
    """Es una pantalla de lectura: sin datos enseña huecos, no un 500."""
    respuesta = sesion.get("/api/panel?zona=Europe/Madrid")

    assert respuesta.status_code == 200
    datos = respuesta.get_json()
    assert len(datos["serie"]) == panel.DIAS_DE_SERIE
    assert datos["dia_del_viaje"] is None
    assert {f["clave"] for f in datos["fuentes"]} >= {"telemetria", "notas", "fotos"}


def test_una_zona_horaria_absurda_no_tumba_el_panel(sesion: Any) -> None:
    respuesta = sesion.get("/api/panel?zona=../../etc/passwd")

    assert respuesta.status_code == 200


def test_el_panel_pide_sesion() -> None:
    flask_app.config["TESTING"] = True
    respuesta = flask_app.test_client().get("/api/panel")

    assert respuesta.status_code in (302, 401)
