"""Tests de las peticiones escalonadas a Overpass.

Sustituimos `_ask_mirror` por espejos simulados, así que no hay red: podemos
provocar caídas y lentitud a voluntad y comprobar que la estrategia aguanta.

Esto es lo que evita el fallo medido en producción: probar los espejos en
serie sumaba sus timeouts (53 s en Llanes). El escalonado acota la latencia.
"""

import time

import pytest

from app.modules import location_context as lc
from app.modules.location_context import LocationError, _fetch_overpass


@pytest.fixture
def espejos_rapidos(monkeypatch):
    """Acorta los tiempos para que los tests corran en milisegundos."""
    monkeypatch.setattr(lc, "_HEDGE_DELAY", 0.15)
    monkeypatch.setattr(lc, "_OVERPASS_TOTAL_BUDGET", 2.0)


def _simular(comportamiento: dict[str, object]):
    """Devuelve un `_ask_mirror` falso según el comportamiento por espejo.

    Cada valor puede ser: un float (segundos que tarda antes de responder),
    o una excepción (que se lanza).
    """
    def fake_ask_mirror(mirror: str, query: str):
        accion = comportamiento[mirror.split("/")[2]]
        if isinstance(accion, Exception):
            raise accion
        time.sleep(float(accion))
        return {"elements": [{"mirror": mirror.split("/")[2]}]}

    return fake_ask_mirror


def test_devuelve_la_respuesta_del_primer_espejo_si_responde_rapido(
    monkeypatch, espejos_rapidos
):
    monkeypatch.setattr(lc, "_ask_mirror", _simular({
        "overpass-api.de": 0.01,
        "overpass.kumi.systems": 5.0,
        "overpass.private.coffee": 5.0,
    }))
    resultado = _fetch_overpass("query")
    assert resultado["elements"][0]["mirror"] == "overpass-api.de"


def test_escala_al_segundo_espejo_cuando_el_primero_va_lento(
    monkeypatch, espejos_rapidos
):
    """El caso real: el primer espejo no está caído, solo saturado.

    En serie esperaríamos su timeout completo antes de probar otro. Escalonado,
    lanzamos el segundo a los _HEDGE_DELAY y ganamos el que llegue antes.
    """
    monkeypatch.setattr(lc, "_ask_mirror", _simular({
        "overpass-api.de": 5.0,          # saturado
        "overpass.kumi.systems": 0.05,   # sano
        "overpass.private.coffee": 5.0,
    }))
    t0 = time.time()
    resultado = _fetch_overpass("query")
    transcurrido = time.time() - t0

    assert resultado["elements"][0]["mirror"] == "overpass.kumi.systems"
    # No esperamos al timeout del primero: respondemos poco después del relevo.
    assert transcurrido < 1.0


def test_ignora_un_espejo_caido_y_sigue_con_el_siguiente(
    monkeypatch, espejos_rapidos
):
    monkeypatch.setattr(lc, "_ask_mirror", _simular({
        "overpass-api.de": ConnectionError("caído"),
        "overpass.kumi.systems": 0.05,
        "overpass.private.coffee": 5.0,
    }))
    resultado = _fetch_overpass("query")
    assert resultado["elements"][0]["mirror"] == "overpass.kumi.systems"


def test_error_claro_cuando_fallan_todos(monkeypatch, espejos_rapidos):
    monkeypatch.setattr(lc, "_ask_mirror", _simular({
        "overpass-api.de": ConnectionError("caído"),
        "overpass.kumi.systems": ValueError("respuesta corrupta"),
        "overpass.private.coffee": ConnectionError("caído"),
    }))
    with pytest.raises(LocationError) as exc_info:
        _fetch_overpass("query")

    mensaje = str(exc_info.value)
    # El error nombra los espejos concretos: sin eso, depurar esto en un
    # camper sin cobertura decente es imposible.
    assert "overpass-api.de" in mensaje
    assert "ConnectionError" in mensaje


def test_respeta_el_presupuesto_total_si_todos_van_lentos(
    monkeypatch, espejos_rapidos
):
    """Ningún espejo responde: nos rendimos dentro del presupuesto, no colgados."""
    monkeypatch.setattr(lc, "_ask_mirror", _simular({
        "overpass-api.de": 10.0,
        "overpass.kumi.systems": 10.0,
        "overpass.private.coffee": 10.0,
    }))
    t0 = time.time()
    with pytest.raises(LocationError):
        _fetch_overpass("query")
    transcurrido = time.time() - t0

    # _OVERPASS_TOTAL_BUDGET son 2.0 s en el fixture; damos medio segundo de holgura.
    assert transcurrido < 2.5


def test_query_es_determinista():
    """Dos llamadas iguales generan la misma cadena: es lo que hace fiable la caché."""
    a = lc._build_overpass_query(43.5, -6.1, 12_000)
    b = lc._build_overpass_query(43.5, -6.1, 12_000)
    assert a == b


def test_query_agrupa_por_clave_osm():
    """`tourism` está en 3 categorías: debe emitirse en una sola cláusula por tipo.

    Agrupar es lo que evita que cada categoría nueva cueste dos búsquedas
    espaciales más en un servidor comunitario gratuito.

    El número esperado se **deriva** de las categorías en vez de escribirse a
    mano. Estaba fijado a 8 y una categoría nueva lo rompía, y eso enseña a
    subir el número en vez de a mirar si de verdad se agrupó — que es lo único
    que este test protege.
    """
    query = lc._build_overpass_query(43.5, -6.1, 12_000)
    claves = {osm_key for osm_key, _ in lc._POI_CATEGORIES.values()}

    assert query.count('node["tourism"') == 1
    assert query.count('way["tourism"') == 1
    assert query.count("(around:") == len(claves) * 2, (
        f"se esperaban {len(claves)} claves OSM x 2 tipos (node y way)"
    )
