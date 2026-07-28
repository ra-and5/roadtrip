"""Tests del generador de telemetría simulada (`tools/simular_telemetria.py`).

Un simulador sin tests parece lo último que merece pruebas, y es al revés: es
la fuente sobre la que se van a escribir el perfil de actividad, el dashboard y
el contexto del chatbot mientras no haya semanas de datos reales. Si genera
pasos que bajan dentro de un día, o muestras en el futuro, lo que se construya
encima parecerá roto por un motivo inventado -- y se buscará el bug en el sitio
equivocado.

Lo que se fija aquí es justo lo que un análisis va a dar por supuesto:

  - los pasos son un acumulado del día, así que **no bajan** hasta medianoche;
  - **se reinician** cada día, que es lo que significa la columna desde que el
    atajo usa `es hoy` (decisión 25);
  - **no hay muestras en el futuro**;
  - un día sin cobertura son muestras que **faltan**, no muestras que lleguen
    tarde: el atajo no tiene cola ni reintenta (decisión 23).

`generar_envios` es pura y recibe el `ahora`, así que todo esto se comprueba
con un instante fijo y sin tocar ni el reloj ni la base de datos.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# `tools/` son scripts, no un paquete: se cargan por ruta. Es feo y es la
# alternativa buena a convertir tools/ en paquete solo para poder testear una
# función, o a meter un simulador dentro de `app/modules/`, que es código de
# producción y no debe cargar con esto.
_RUTA = Path(__file__).resolve().parent.parent / "tools" / "simular_telemetria.py"
_spec = importlib.util.spec_from_file_location("simular_telemetria", _RUTA)
assert _spec and _spec.loader
simulador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(simulador)


# Un instante fijo, y elegido con cuidado: 21:58 UTC son las 23:58 en Madrid,
# así que el último día sale COMPLETO (sus seis envíos ya han pasado) sin
# haberse pasado de medianoche local, que abriría un día séptimo vacío. Fijarlo
# es lo que hace que los tests no cambien de resultado según la hora a la que
# se ejecute la suite.
AHORA = datetime(2026, 7, 28, 21, 58, tzinfo=timezone.utc)


def _por_dia(envios: list[Any]) -> dict[str, list[dict[str, Any]]]:
    dias: dict[str, list[dict[str, Any]]] = {}
    for envio in envios:
        for muestra in envio.muestras:
            dias.setdefault(muestra["medido_en"][:10], []).append(muestra)
    return dias


def test_los_pasos_no_bajan_dentro_de_un_dia() -> None:
    """Es un acumulado. Un bajón sería un contador que se ha reiniciado a
    mitad del día, y cualquier cálculo de "cuánto anduve" sobre eso daría un
    número menor que la verdad sin dar ningún error."""
    for dia, muestras in _por_dia(simulador.generar_envios(10, AHORA)).items():
        pasos = [m["pasos"] for m in sorted(muestras, key=lambda m: m["medido_en"])]
        assert pasos == sorted(pasos), f"{dia}: los pasos bajan ({pasos})"


def test_los_pasos_se_reinician_cada_dia() -> None:
    """Con el filtro `es hoy`, la primera muestra del día empieza casi de cero.

    Si arrastrase el día anterior estaríamos simulando la ventana rodante de
    24 h que se corrigió a propósito: el mismo dato con otro significado en la
    misma columna, que es el análisis silenciosamente equivocado del que avisa
    la decisión 25.
    """
    dias = _por_dia(simulador.generar_envios(10, AHORA))
    for muestras in dias.values():
        primera = min(muestras, key=lambda m: m["medido_en"])
        maximo = max(m["pasos"] for m in muestras)
        assert primera["pasos"] < maximo * 0.2


def test_no_se_inventan_muestras_en_el_futuro() -> None:
    """La validación toleraría hasta 24 h por delante; sembrarlas no."""
    envios = simulador.generar_envios(10, AHORA)
    for envio in envios:
        for muestra in envio.muestras:
            assert datetime.fromisoformat(muestra["medido_en"]) <= AHORA


def test_la_recepcion_va_despues_de_la_medida_y_por_poco() -> None:
    """Un `retraso` negativo o de horas en una serie normal la haría inútil
    para lo que se mira: distinguir un envío corriente de una recuperación."""
    for envio in simulador.generar_envios(10, AHORA):
        for muestra in envio.muestras:
            medido = datetime.fromisoformat(muestra["medido_en"])
            recibido = datetime.fromisoformat(envio.recibido_en)
            assert 0 < (recibido - medido).total_seconds() < 600


def test_la_misma_semilla_da_la_misma_serie() -> None:
    """Reejecutar no puede ensuciar la tabla con una serie parecida pero
    distinta: tiene que salir `duplicadas`, que además comprueba gratis que la
    idempotencia del esquema funciona."""
    primera = simulador.generar_envios(7, AHORA, semilla=1)
    segunda = simulador.generar_envios(7, AHORA, semilla=1)
    distinta = simulador.generar_envios(7, AHORA, semilla=2)

    assert primera == segunda
    assert primera != distinta


def test_el_dia_sin_cobertura_pierde_muestras_no_las_retrasa() -> None:
    """Un envío que falla se pierde: el atajo no tiene cola (decisión 23).

    Simularlo como muestras que llegan tarde dibujaría un comportamiento que
    este sistema no tiene, y luego alguien construiría encima contando con él.
    """
    dias = _por_dia(simulador.generar_envios(7, AHORA, con_hueco=True))
    conteos = sorted(len(m) for m in dias.values())

    assert conteos[0] == len(simulador.HORAS_ENVIO) - 2
    assert conteos[1:] == [len(simulador.HORAS_ENVIO)] * (len(dias) - 1)


def test_el_acumulado_se_cura_solo_despues_del_hueco() -> None:
    """Lo que sí recupera un contador acumulado: la muestra siguiente al hueco
    ya trae lo andado durante el hueco. Es la propiedad por la que la decisión
    25 pudo quitar el bucle de cubos horarios."""
    dias = _por_dia(simulador.generar_envios(7, AHORA, con_hueco=True))
    con_hueco = min(dias.values(), key=len)
    ordenadas = sorted(con_hueco, key=lambda m: m["medido_en"])

    # Entre las 08:00 y las 18:00 (12:00 y 16:00 se perdieron) el contador ha
    # seguido subiendo: no se ha perdido ni un paso, solo el desglose.
    assert ordenadas[1]["pasos"] > ordenadas[0]["pasos"]


def test_sin_hueco_estan_todos_los_envios() -> None:
    dias = _por_dia(simulador.generar_envios(7, AHORA, con_hueco=False))

    assert all(len(m) == len(simulador.HORAS_ENVIO) for m in dias.values())


@pytest.mark.parametrize("pedidos,esperados", [(0, 1), (1, 1), (60, simulador.MAX_DIAS)])
def test_los_dias_se_recortan_a_lo_que_acepta_la_validacion(
    pedidos: int, esperados: int
) -> None:
    """`timeparse.MAX_PASADO` son 30 días: pedir 60 no puede generar muestras
    que el endpoint real rechazaría, porque entonces la serie saldría a medias
    y parecería un bug del simulador."""
    dias = _por_dia(simulador.generar_envios(pedidos, AHORA))

    assert len(dias) == esperados


def test_las_muestras_pasan_la_validacion_del_endpoint_real() -> None:
    """La comprobación que de verdad importa: que esto genera lo que el móvil
    manda, y no algo parecido. Si el simulador y el endpoint se separan, todo
    lo que se pruebe contra estos datos estará probado contra una forma que no
    existe."""
    from app.modules import ingest

    # Aquí sí se usa el reloj real, y no `AHORA`: la validación de fechas mide
    # contra el ahora de verdad (`MAX_PASADO`, 30 días), así que con un
    # instante fijo este test se convertiría en una bomba de relojería que
    # empieza a fallar un mes después de escribirlo.
    for envio in simulador.generar_envios(7, datetime.now(timezone.utc)):
        for muestra in envio.muestras:
            # Función interna a propósito: es la que decide si una muestra vale,
            # y lo que se quiere fijar es que ninguna generada se descarta.
            ingest._parse_muestra(muestra, simulador.FUENTE)
