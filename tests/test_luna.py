"""Tests de la luna (Fase 5, §4).

Sin red y sin API keys, como el resto de la suite.

**Los valores de referencia son reales**, no inventados para que cuadren: salen
de consultar `api.met.no` el 28-07-2026 para veinte fechas repartidas por julio
y agosto, y de contrastar la iluminación con tutiempo.net. Un test cuyos valores
esperados se sacan del propio código bajo prueba no comprueba nada: solo fija
el error si lo hay.

Lo que se protege aquí:

  - **Que la fase se calcula bien**, contra met.no, con un margen medido (el
    peor error observado fue 0,46° y 0,31 puntos de iluminación).
  - **Que el nombre de la fase cierra el círculo.** 350° y 5° son la misma luna,
    y la primera versión de este módulo llamaba "menguante cóncava" a una luna
    nueva. No daba error: solo escribía una tontería en la pantalla y en el
    prompt.
  - **Que el veredicto nocturno es una regla explícita** y no algo que se le
    pregunta al modelo (decisión 5), incluido lo que hace cuando NO se sabe si
    el cielo está despejado.
  - **Que un User-Agent de ejemplo no llega a salir a la red.** met.no lo
    rechaza con un 403 de nginx que no explica nada, y sin esto la luna
    quedaría apagada para siempre en un despliegue sin que nadie supiera por qué.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import pytest

from app.config import Config
from app.modules import luna as modulo_luna
from app.modules import storage
from app.modules.luna import (
    Efemerides,
    Fase,
    LunaError,
    _parse_metno,
    efemerides,
    fase,
    veredicto_nocturno,
)

# Fecha -> (moonphase de met.no, iluminación de met.no). Medidos, no supuestos.
# met.no da la fase a las 00:00 de la fecha pedida, en el huso indicado; aquí
# se piden en UTC para que el instante sea inequívoco.
REFERENCIA_METNO: tuple[tuple[str, float, float], ...] = (
    ("2026-07-01", 191.20, 99.05),
    ("2026-07-13", 340.37, 2.91),
    ("2026-07-16", 22.32, 3.75),
    ("2026-07-22", 96.29, 55.48),
    ("2026-07-28", 162.27, 97.63),   # la del encargo: 97,56 % en tutiempo.net
    ("2026-08-06", 268.95, 50.92),
    ("2026-08-12", 350.28, 0.72),
    ("2026-08-27", 166.44, 98.61),
)

# Márgenes: el peor error observado sobre 20 fechas fue 0,46° y 0,31 puntos.
# Se dejan holgados, pero no tanto como para que dejen pasar un error real.
MARGEN_ANGULO = 1.0
MARGEN_ILUMINACION = 1.0


def _utc(fecha: str) -> datetime:
    return datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(
        Config, "NOMINATIM_USER_AGENT", "roadtrip-test/0.1 (alguien@dominio-real.es)"
    )
    storage.init_db()
    yield


# ---------------------------------------------------------------------------
# La fase, contra met.no
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fecha,angulo_metno,ilum_metno", REFERENCIA_METNO)
def test_la_fase_coincide_con_metno(fecha: str, angulo_metno: float,
                                    ilum_metno: float) -> None:
    """Ocho fechas repartidas por dos lunaciones, no una elegida a dedo."""
    calculada = fase(_utc(fecha))

    diferencia = (calculada.angulo - angulo_metno + 180) % 360 - 180
    assert abs(diferencia) < MARGEN_ANGULO, f"{fecha}: {calculada.angulo} vs {angulo_metno}"
    assert abs(calculada.iluminacion_pct - ilum_metno) < MARGEN_ILUMINACION


def test_la_luna_llena_del_encargo() -> None:
    """El caso concreto que trajo el usuario, con su referencia externa.

    tutiempo.net daba 97,56 % para Villajoyosa el 28 de julio de 2026.
    """
    calculada = fase(datetime(2026, 7, 28, 0, 0, tzinfo=ZoneInfo("Europe/Madrid")))

    assert calculada.nombre == "luna llena"
    assert 97.0 < calculada.iluminacion_pct < 98.5


def test_la_fase_no_necesita_red() -> None:
    """Con met.no caído (o bloqueado por el proxy) sigue habiendo luna.

    OJO con lo que este test NO demuestra: no demuestra que la app sirva sin
    cobertura. La app corre en el servidor, así que un móvil sin cobertura no
    llega a pedir nada. Lo que demuestra es que un tercero caído no borra la
    tarjeta entera.

    Si esto dejara de cumplirse, `conftest.py` lo caza — corta los sockets de
    toda la suite.
    """
    assert fase(_utc("2026-07-28")).iluminacion_pct > 0


def test_el_nombre_cierra_el_circulo() -> None:
    """350° y 5° son la misma luna, y la primera versión no lo sabía.

    El tramo de la luna nueva va de 337,5° a 22,5° pasando por cero. Sin cerrar
    el círculo, una luna nueva al 0,8 % de iluminación salía llamada "menguante
    cóncava": no da error, solo escribe una tontería en la pantalla y en el
    prompt del modelo.
    """
    casi_nueva = fase(_utc("2026-08-12"))       # met.no: 350,28°, 0,72 %

    assert casi_nueva.angulo > 337.5
    assert casi_nueva.nombre == "luna nueva"


@pytest.mark.parametrize("fecha,esperado", [
    ("2026-07-16", "luna nueva"),
    ("2026-07-22", "cuarto creciente"),
    ("2026-07-28", "luna llena"),
    ("2026-08-06", "cuarto menguante"),
])
def test_los_nombres_de_las_fases(fecha: str, esperado: str) -> None:
    assert fase(_utc(fecha)).nombre == esperado


def test_creciendo_distingue_las_dos_mitades_del_ciclo() -> None:
    """Un cuarto creciente y uno menguante iluminan igual y no son lo mismo."""
    assert fase(_utc("2026-07-22")).creciendo is True    # 96°
    assert fase(_utc("2026-08-06")).creciendo is False   # 269°


# ---------------------------------------------------------------------------
# El veredicto: una regla en Python, no una pregunta al modelo
# ---------------------------------------------------------------------------

def _fase(iluminacion: float, nombre: str = "luna llena") -> Fase:
    return Fase(angulo=180.0, iluminacion_pct=iluminacion, nombre=nombre, creciendo=True)


def test_luna_llena_y_despejado_se_puede_caminar() -> None:
    veredicto = veredicto_nocturno(_fase(97.6), codigo_meteo=0)

    assert veredicto.hay_luz is True
    assert "sin frontal" in veredicto.motivo


def test_media_luna_no_alumbra_un_camino() -> None:
    """La luz de la luna no es lineal con la fracción visible.

    Al 50 % da del orden de un 8 % de la luz de la llena, así que "media luna"
    no es "media luz". Por eso el umbral está en el 70 % y no en el 50 %.
    """
    veredicto = veredicto_nocturno(_fase(50.0, "cuarto creciente"), codigo_meteo=0)

    assert veredicto.hay_luz is False
    assert "frontal" in veredicto.motivo


def test_luna_llena_con_el_cielo_cubierto_no_vale() -> None:
    veredicto = veredicto_nocturno(_fase(99.0), codigo_meteo=3)   # nublado

    assert veredicto.hay_luz is False
    assert "cubierto" in veredicto.motivo


def test_sin_datos_del_cielo_NO_se_afirma_que_se_pueda_caminar() -> None:
    """Lo importante de este test es a dónde se equivoca.

    Sin saber si está despejado, decir que sí se puede caminar es una respuesta
    segura sobre algo que no se ha comprobado — y aquí eso puede acabar con
    alguien de noche en un monte. Se cae del lado que no hace daño.
    """
    veredicto = veredicto_nocturno(_fase(99.0), codigo_meteo=None)

    assert veredicto.hay_luz is False
    assert "no se sabe" in veredicto.motivo


def test_la_lluvia_tambien_tapa_la_luna() -> None:
    assert veredicto_nocturno(_fase(99.0), codigo_meteo=61).hay_luz is False


def test_sin_efemerides_se_puede_caminar_pero_se_dice() -> None:
    """Se sabe que hay luz; lo que no se sabe es a qué hora asoma."""
    veredicto = veredicto_nocturno(_fase(99.0), codigo_meteo=0, hay_efemerides=False)

    assert veredicto.hay_luz is True
    assert "salida" in veredicto.motivo


# ---------------------------------------------------------------------------
# met.no: el User-Agent, y lo que puede faltar en la respuesta
# ---------------------------------------------------------------------------

def test_un_user_agent_de_ejemplo_ni_llega_a_salir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comprobado contra la API real: met.no devuelve 403 con `example.com`.

    Y el cuerpo del 403 es una página de nginx que no dice por qué. Sin esta
    comprobación, un despliegue que no hubiera tocado `NOMINATIM_USER_AGENT`
    vería la luna caída para siempre y el motivo sería indescifrable. Mejor
    negarse a llamar y nombrar la variable que hay que arreglar.
    """
    monkeypatch.setattr(
        Config, "NOMINATIM_USER_AGENT",
        "roadtrip-companion/0.1 (proyecto-estudiante; contacto@example.com)",
    )

    with pytest.raises(LunaError) as info:
        efemerides(43.56, -6.15, _utc("2026-07-28"))

    assert "NOMINATIM_USER_AGENT" in str(info.value)


def test_un_user_agent_vacio_tampoco(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "NOMINATIM_USER_AGENT", "   ")

    with pytest.raises(LunaError):
        efemerides(43.56, -6.15, _utc("2026-07-28"))


def test_un_contacto_real_no_se_bloquea(monkeypatch: pytest.MonkeyPatch) -> None:
    """El filtro solo mira los dominios que la RFC 2606 reserva para ejemplos.

    Una heurística más lista rechazaría contactos buenos, y un falso positivo
    aquí apaga la luna sin motivo.
    """
    assert modulo_luna.contacto_valido("roadtrip/0.1 (ram58@alu.ua.es)")
    assert modulo_luna.contacto_valido("roadtrip/0.1 (https://ejemplo.pythonanywhere.com)")
    assert not modulo_luna.contacto_valido("roadtrip/0.1 (yo@example.com)")


def test_el_offset_va_con_dos_puntos() -> None:
    """met.no quiere "+02:00". Sin el parámetro devuelve UTC — comprobado contra
    la API real—, y entonces "la luna sale a las 18:54" sería falso por dos
    horas en España."""
    madrid = datetime(2026, 7, 28, 12, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    assert modulo_luna._offset_iso(madrid) == "+02:00"
    assert modulo_luna._offset_iso(_utc("2026-07-28")) == "+00:00"


def test_la_respuesta_real_de_metno_se_entiende() -> None:
    """Cuerpo copiado de una respuesta de verdad, con sus campos y su formato."""
    payload = {
        "properties": {
            "body": "Moon",
            "moonrise": {"time": "2026-07-28T20:54+02:00", "azimuth": 120.88},
            "moonset": {"time": "2026-07-28T05:33+02:00", "azimuth": 236.59},
            "high_moon": {"time": "2026-07-29T01:42+02:00",
                          "disc_centre_elevation": 27.81, "visible": True},
            "low_moon": {"time": "2026-07-28T13:17+02:00",
                         "disc_centre_elevation": -76.46, "visible": False},
            "moonphase": 162.1,
        }
    }

    efem = _parse_metno(payload)

    assert efem.salida == "2026-07-28T20:54+02:00"
    assert efem.salida_azimut == 120.88
    assert efem.puesta == "2026-07-28T05:33+02:00"
    assert efem.culminacion_elevacion == 27.81


def test_un_dia_sin_salida_de_luna_no_revienta() -> None:
    """En latitudes altas hay días en los que la luna no sale ni se pone.

    No se inventa una hora ni se pone un cero: se deja vacío, que es lo que
    significa. Un cero aquí sería medianoche, que es una hora perfectamente
    creíble y completamente falsa.
    """
    efem = _parse_metno({"properties": {"moonphase": 78.4}})

    assert efem == Efemerides()
    assert efem.salida == ""
    assert efem.salida_azimut is None
