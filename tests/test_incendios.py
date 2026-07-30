"""Tests de las detecciones de calor por satélite (NASA FIRMS).

Sin red, como el resto de la suite. El CSV de ejemplo son **filas reales**
devueltas por la API el 30-07-2026 para el rectángulo de Alicante, no inventadas:
lo que se protege aquí es que dos detecciones industriales de 0,6 MW no se
anuncien como un incendio, y ese caso hay que probarlo con el dato que lo
produjo.
"""

from __future__ import annotations

import pytest

from app.modules.incendios import (
    FRP_LLAMATIVA_MW,
    IncendioError,
    evaluar,
    parsear,
    url_de_consulta,
)

CABECERA = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight"
)

# Las dos que salieron de verdad a ~2 km de San Vicente del Raspeig.
INDUSTRIA = (
    f"{CABECERA}\n"
    "38.37518,-0.54299,307.65,0.4,0.37,2026-07-30,158,N,VIIRS,n,2.0NRT,294.76,0.62,N\n"
    "38.409,-0.61014,325.82,0.4,0.37,2026-07-30,158,N,VIIRS,n,2.0NRT,292.52,1.85,N\n"
)

AQUI = (38.3958, -0.5253)


def test_dos_puntos_flojos_no_son_un_incendio():
    """El caso real, y el que más importa: no crear una alarma falsa.

    VIIRS ve cualquier anomalía térmica —hornos, antorchas, quemas—. Anunciar
    "incendio a 2 km" por 0,6 MW es la alarma que se aprende a ignorar, y
    entonces tampoco se lee el día que arde el monte de al lado.
    """
    situacion = evaluar(INDUSTRIA, *AQUI)

    assert situacion.hay_algo is True
    assert situacion.cuantas == 2
    assert "incendio" not in situacion.veredicto.lower()
    assert "punto" in situacion.veredicto
    # Y se dice explícitamente de qué suele tratarse.
    assert "industria" in situacion.detalle


def test_un_foco_potente_y_cerca_se_dice_claramente():
    csv = (
        f"{CABECERA}\n"
        "38.4200,-0.5300,340.0,0.4,0.37,2026-07-30,1330,N,VIIRS,h,2.0NRT,300.0,145.7,D\n"
    )
    situacion = evaluar(csv, *AQUI)

    assert "Foco activo" in situacion.veredicto
    assert "146 MW" in situacion.veredicto or "145 MW" in situacion.veredicto
    assert "dormir" in situacion.detalle, "no dice qué hacer con ese aviso"


def test_un_foco_potente_pero_lejos_no_alarma_igual():
    """La distancia cambia el consejo, no solo el número."""
    csv = (
        f"{CABECERA}\n"
        "39.2000,-0.5300,340.0,0.4,0.37,2026-07-30,1330,N,VIIRS,h,2.0NRT,300.0,200.0,D\n"
    )
    situacion = evaluar(csv, *AQUI)

    assert "lejos" in situacion.veredicto
    assert "dormir" not in situacion.detalle


def test_sin_detecciones_se_dice_lo_que_NO_se_ha_visto():
    """Un "todo bien" tiene que declarar sus límites.

    VIIRS pasa dos veces al día y no ve fuegos pequeños ni bajo nubes. Un
    "limpio" sin esa letra pequeña se lee como una garantía que nadie ha dado.
    """
    situacion = evaluar(f"{CABECERA}\n", *AQUI)

    assert situacion.hay_algo is False
    assert situacion.cuantas == 0
    assert "no detecta" in situacion.detalle.lower()


def test_una_clave_invalida_llega_como_200_y_texto_plano():
    """FIRMS contesta los errores con HTTP 200 y un cuerpo de texto.

    Es la trampa de la decisión 5 (la API marina de Open-Meteo) otra vez: un
    200 no significa que la respuesta sirva. Sin esta comprobación, el mensaje
    de error se parsearía como CSV vacío y la pantalla diría "sin detecciones"
    —una afirmación tranquilizadora y falsa— en vez de decir que no lo sabe.
    """
    with pytest.raises(IncendioError) as fallo:
        evaluar("Invalid MAP_KEY.", *AQUI)

    assert "MAP_KEY" in str(fallo.value), "el motivo real tiene que sobrevivir"


def test_un_cuerpo_vacio_no_se_confunde_con_estar_limpio():
    with pytest.raises(IncendioError):
        evaluar("", *AQUI)


def test_las_detecciones_salen_ordenadas_por_cercania():
    csv = (
        f"{CABECERA}\n"
        "39.0000,-0.5300,340.0,0.4,0.37,2026-07-30,1330,N,VIIRS,h,2.0NRT,300.0,10.0,D\n"
        "38.4000,-0.5300,340.0,0.4,0.37,2026-07-30,1330,N,VIIRS,h,2.0NRT,300.0,10.0,D\n"
    )
    detecciones = parsear(csv, *AQUI)

    assert detecciones[0].distancia_km < detecciones[1].distancia_km


def test_una_fila_sin_frp_no_revienta_y_cuenta_como_floja():
    """Una columna que falta no puede tumbar la pantalla, ni inventarse un valor.

    Sin `frp` se queda en 0, que es lo prudente: el veredicto la tratará como
    poca cosa en vez de como un fuego que nadie ha medido.
    """
    csv = f"{CABECERA}\n38.4000,-0.5300,340.0,0.4,0.37,2026-07-30,1330,N,VIIRS,h,2.0NRT,300.0,,D\n"
    situacion = evaluar(csv, *AQUI)

    assert situacion.hay_algo is True
    assert situacion.frp_maxima_mw == 0.0
    assert "Foco activo" not in situacion.veredicto


def test_la_url_lleva_la_caja_alrededor_del_punto_y_el_sensor():
    """La compone el servidor para que sensor, radio y días sean UNA definición.

    Repartidos entre Python y JavaScript, cambiar el radio en un sitio y no en
    el otro daría una caja distinta de la que dicen los tests, sin dar error.
    """
    url = url_de_consulta("CLAVE", 38.3958, -0.5253, dias=1)

    assert "VIIRS_SNPP_NRT" in url
    assert "/CLAVE/" in url
    # El rectángulo va oeste,sur,este,norte y el punto tiene que quedar dentro.
    oeste, sur, este, norte = (float(x) for x in url.split("/")[-2].split(","))
    assert oeste < -0.5253 < este
    assert sur < 38.3958 < norte


def test_el_umbral_de_potencia_deja_fuera_la_industria_medida():
    """El número que separa los dos mensajes, contrastado con el dato real.

    Las detecciones industriales medidas iban de 0,6 a 1,9 MW; un incendio
    forestal declarado pasa de 100 con facilidad. El umbral se queda muy por
    debajo a propósito: equivocarse hacia el lado seguro importa más aquí que
    en ningún otro veredicto.
    """
    assert 1.85 < FRP_LLAMATIVA_MW < 100.0
