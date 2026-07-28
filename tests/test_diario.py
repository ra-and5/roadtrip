"""Tests del registro del primer sitio de cada día (Fase 5, §5).

Sin red y sin API keys.

El usuario pidió que quedara constancia de dónde estaba la primera vez que
preguntaba cada día. Lo que se protege aquí:

  - **La idempotencia**, que vive en el esquema (`UNIQUE(fecha_local)` +
    `INSERT OR IGNORE`) y no en un `SELECT` previo. Es la misma decisión que en
    la ingesta: comprobar-y-luego-insertar tiene una carrera, una restricción de
    unicidad no.
  - **Que el día se cuenta en hora LOCAL.** Preguntar a las 00:30 en España es
    del día siguiente en UTC, y contarlo ahí desplaza un día entero del viaje
    sin dar ningún error (decisión 29).
  - **Que registrar no puede tumbar la pantalla.** Es un efecto lateral de
    mirar el contexto; si la base de datos falla, lo que se pierde es una fila
    de historia, no la app.
  - **Que `construir()` no escribe nada.** Esa función la van a llamar también
    el recomendador y el chatbot, y darle un efecto lateral haría que
    preguntarle algo al chatbot escribiera en la base de datos.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import pytest

from app.config import Config
from app.modules import contexto, diario, storage
from app.modules.contexto import ensamblar
from app.modules.location_context import Place
from app.modules.luna import Efemerides
from app.modules.weather_context import Weather


def _place(nombre: str = "Cudillero", region: str = "Asturias") -> Place:
    return Place(lat=43.5622, lon=-6.1456, name=nombre, region=region,
                 display_name=f"{nombre}, {region}, España")


def _contexto(ahora: datetime, lugar: Place | None = None) -> Any:
    return ensamblar(lugar or _place(), Weather(timezone="Europe/Madrid"), ahora=ahora)


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    storage.init_db()
    yield


# ---------------------------------------------------------------------------
# La idempotencia
# ---------------------------------------------------------------------------

def test_solo_queda_la_primera_del_dia() -> None:
    """Preguntar diez veces al día no son diez filas: es una."""
    manana = datetime(2026, 7, 28, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    tarde = datetime(2026, 7, 28, 19, 30, tzinfo=ZoneInfo("Europe/Madrid"))

    assert diario.registrar_lugar_del_dia(_contexto(manana)) is True
    assert diario.registrar_lugar_del_dia(_contexto(tarde)) is False

    filas = storage.list_lugares_del_dia()
    assert len(filas) == 1
    assert filas[0]["momento_local"].startswith("2026-07-28T09:00")


def test_la_primera_gana_aunque_te_hayas_movido() -> None:
    """"Dónde estaba la PRIMERA vez", no "dónde estoy ahora".

    Si ganara la última, el dato sería otra cosa distinta —el último sitio del
    día— y en un camper que se mueve por la tarde diría algo que nadie ha
    pedido.
    """
    manana = datetime(2026, 7, 28, 8, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    tarde = datetime(2026, 7, 28, 20, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    diario.registrar_lugar_del_dia(_contexto(manana, _place("Cudillero", "Asturias")))
    diario.registrar_lugar_del_dia(_contexto(tarde, _place("Llanes", "Asturias")))

    assert storage.list_lugares_del_dia()[0]["place_name"] == "Cudillero, Asturias"


def test_dias_distintos_son_filas_distintas() -> None:
    for dia in (27, 28, 29):
        instante = datetime(2026, 7, dia, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        assert diario.registrar_lugar_del_dia(_contexto(instante)) is True

    assert len(storage.list_lugares_del_dia()) == 3


# ---------------------------------------------------------------------------
# El día es el LOCAL
# ---------------------------------------------------------------------------

def test_medianoche_larga_cuenta_como_el_dia_local() -> None:
    """Las 00:30 del 28 en España son las 22:30 del 27 en UTC.

    Con la fecha en UTC, esa consulta se apuntaría al día anterior y el viaje
    saldría desplazado un día entero. No daría ningún error: solo mentiría.
    """
    instante = datetime(2026, 7, 28, 0, 30, tzinfo=ZoneInfo("Europe/Madrid"))

    diario.registrar_lugar_del_dia(_contexto(instante))

    assert storage.list_lugares_del_dia()[0]["fecha_local"] == "2026-07-28"


def test_dos_consultas_a_ambos_lados_de_medianoche_utc_son_el_mismo_dia() -> None:
    antes = datetime(2026, 7, 28, 21, 0, tzinfo=ZoneInfo("Europe/Madrid"))   # 19:00 UTC
    despues = datetime(2026, 7, 28, 23, 30, tzinfo=ZoneInfo("Europe/Madrid"))  # 21:30 UTC

    diario.registrar_lugar_del_dia(_contexto(antes))
    diario.registrar_lugar_del_dia(_contexto(despues))

    assert len(storage.list_lugares_del_dia()) == 1


# ---------------------------------------------------------------------------
# Registrar no puede romper nada
# ---------------------------------------------------------------------------

def test_un_fallo_al_registrar_no_propaga(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se pierde una fila de historia, no la pantalla."""

    def _revienta(fila: Any) -> None:
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(storage, "insert_lugar_del_dia", _revienta)

    instante = datetime(2026, 7, 28, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    assert diario.registrar_lugar_del_dia(_contexto(instante)) is False


def test_construir_el_contexto_no_escribe_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    """La frontera que hace reutilizable el contexto.

    `construir()` la van a llamar la pantalla, el recomendador y el chatbot.
    Con un efecto lateral dentro, preguntarle algo al chatbot escribiría en la
    base de datos — y encima marcaría como "el sitio del día" uno que a lo
    mejor era de una consulta sobre ayer.
    """
    monkeypatch.setattr(contexto, "reverse_geocode", lambda lat, lon: _place())
    monkeypatch.setattr(
        contexto, "get_weather", lambda lat, lon: Weather(timezone="Europe/Madrid")
    )
    monkeypatch.setattr(contexto, "efemerides", lambda lat, lon, cuando: Efemerides())

    contexto.construir(43.5622, -6.1456)

    assert storage.list_lugares_del_dia() == []


# ---------------------------------------------------------------------------
# Los huecos, que son lo que decide si esto sirve
# ---------------------------------------------------------------------------

def test_el_resumen_cuenta_los_huecos() -> None:
    """Es la única cifra que decide si se puede construir algo encima.

    Misma vara de medir que la Fase 2d: mientras haya huecos, esto es un
    registro incompleto y no una fuente.
    """
    for dia in (26, 28, 30):        # faltan el 27 y el 29
        instante = datetime(2026, 7, dia, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        diario.registrar_lugar_del_dia(_contexto(instante))

    datos = diario.resumen()

    assert datos["total"] == 3
    assert datos["primero"] == "2026-07-26"
    assert datos["ultimo"] == "2026-07-30"
    assert datos["huecos"] == 2


def test_el_resumen_sin_datos_no_inventa_huecos() -> None:
    datos = diario.resumen()

    assert datos["total"] == 0
    assert datos["huecos"] is None


def test_dias_seguidos_no_tienen_huecos() -> None:
    for dia in (26, 27, 28):
        instante = datetime(2026, 7, dia, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        diario.registrar_lugar_del_dia(_contexto(instante))

    assert diario.resumen()["huecos"] == 0
