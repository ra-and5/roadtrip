"""Tests de `metricas.cobertura()`: si la telemetría llega sin huecos.

Es la cifra que decide si se cierra la Fase 2d, y por eso merece tests propios
en vez de calcularse a ojo en el diagnóstico. Lo que fija este archivo:

  - **los huecos se cuentan desde la primera muestra, no desde hace 7 días**,
    porque lo contrario avisaría el día siguiente a montar las automatizaciones
    y un aviso que salta cuando todo va bien se aprende a ignorar;
  - **un día a medias no es un hueco, y tampoco es un día bueno**: seis envíos
    diarios que entregan dos dan cero huecos, y esa serie no es fiable;
  - **el día es el LOCAL**, deshaciendo el desfase con el que llegó la muestra.
    Contarlo en UTC desplaza un día entero del viaje (decisión 29) y no da
    ningún error.

Sin red y sin base de datos: `cobertura()` es pura y recibe las muestras y el
día de hoy, igual que `resumir()`.
"""

from __future__ import annotations

from datetime import date

from app.modules.metricas import ENVIOS_ESPERADOS_POR_DIA, cobertura, revisar_acumulado


def _muestra(medido_en: str, offset: str = "+02:00") -> dict:
    return {"medido_en": medido_en, "offset_original": offset, "fuente": "atajos-iphone"}


def _dia_completo(fecha: str) -> list[dict]:
    """Las seis horas de las automatizaciones, en UTC (España es +02:00)."""
    return [_muestra(f"{fecha}T{h:02d}:00:00+00:00") for h in (6, 10, 14, 16, 18, 21)]


def test_sin_muestras_no_inventa_una_serie() -> None:
    c = cobertura([], date(2026, 7, 29))

    assert c.dias_con_datos == 0
    assert c.huecos == 0
    # Y `sin_huecos` es False: cero días no es "todo correcto", es "no hay nada".
    # Devolver True aquí haría que una tabla vacía pasara por una serie perfecta,
    # que es el fallo silencioso que esta función existe para evitar.
    assert not c.sin_huecos


def test_dias_seguidos_y_completos_no_tienen_huecos() -> None:
    muestras = _dia_completo("2026-07-27") + _dia_completo("2026-07-28")

    c = cobertura(muestras, date(2026, 7, 28))

    assert c.dias_abarcados == 2
    assert c.dias_con_datos == 2
    assert c.huecos == 0
    assert c.dias_incompletos == 0
    assert c.sin_huecos


def test_un_dia_sin_cobertura_sale_como_hueco() -> None:
    """El atajo no tiene cola: un envío fallido se pierde (decisión 23).

    Así que un día sin datos son muestras que FALTAN, y es exactamente lo que
    esta función tiene que enseñar.
    """
    muestras = _dia_completo("2026-07-26") + _dia_completo("2026-07-28")

    c = cobertura(muestras, date(2026, 7, 28))

    assert c.dias_abarcados == 3
    assert c.dias_con_datos == 2
    assert c.huecos == 1
    assert not c.sin_huecos


def test_que_hoy_no_haya_llegado_nada_tambien_es_un_hueco() -> None:
    """La ventana llega hasta HOY, no hasta la última muestra.

    Si acabara en la última, una fuente que dejó de llegar hace tres días
    saldría como "sin huecos" — el peor resultado posible: una serie muerta
    certificada como perfecta.
    """
    c = cobertura(_dia_completo("2026-07-26"), date(2026, 7, 29))

    assert c.dias_abarcados == 4
    assert c.huecos == 3


def test_un_dia_a_medias_no_es_un_hueco_pero_se_cuenta_aparte() -> None:
    """Seis automatizaciones que entregan dos muestras no son una serie fiable.

    Sin esta cifra, "sin huecos" se leería como "cerrado" con un tercio de los
    envíos perdiéndose todos los días.
    """
    muestras = _dia_completo("2026-07-27") + [
        _muestra("2026-07-28T06:00:00+00:00"),
        _muestra("2026-07-28T10:00:00+00:00"),
    ]

    c = cobertura(muestras, date(2026, 7, 28))

    assert c.huecos == 0
    assert c.dias_incompletos == 1
    assert ENVIOS_ESPERADOS_POR_DIA == 6


def test_el_dia_es_el_LOCAL_y_no_el_UTC() -> None:
    """Una muestra de las 00:30 en España es del día ANTERIOR en UTC.

    Contarla en el día UTC partiría en dos un día que está entero, e inventaría
    un hueco donde no lo hay. Es la decisión 29, y por eso `offset_original`
    está guardado en la tabla.
    """
    # 22:30 UTC del día 27 son las 00:30 del día 28 en Madrid.
    muestras = _dia_completo("2026-07-28") + [_muestra("2026-07-27T22:30:00+00:00")]

    c = cobertura(muestras, date(2026, 7, 28))

    # Todo cae en el día 28 local: un solo día, sin huecos.
    assert c.dias_con_datos == 1
    assert c.por_dia == [("2026-07-28", 7)]


def test_la_primera_y_la_ultima_salen_de_las_muestras() -> None:
    c = cobertura(_dia_completo("2026-07-28"), date(2026, 7, 28))

    assert c.primera == "2026-07-28T06:00:00+00:00"
    assert c.ultima == "2026-07-28T21:00:00+00:00"


# --- Revisión del acumulado ------------------------------------------------
#
# Estos tests salen de un fallo real del 30-07-2026: el atajo dejó de sumar las
# muestras de Salud y empezó a mandar UNA sola, así que enviaba 298 pasos donde
# la app decía más de 2.000. No dio ningún error — el dato entraba, se guardaba
# y se pintaba— y solo se vio mirando la tabla a ojo.

def _lectura(medido_en: str, pasos: int, offset: str = "+02:00") -> dict:
    fila = _muestra(medido_en, offset)
    fila["pasos"] = pasos
    return fila


def test_un_acumulado_que_baja_es_imposible_y_se_dice() -> None:
    avisos = revisar_acumulado([
        _lectura("2026-07-30T08:00:00+00:00", 4200),
        _lectura("2026-07-30T16:00:00+00:00", 300),
    ])

    assert len(avisos) == 1
    assert "BAJAN" in avisos[0]
    assert "4200 -> 300" in avisos[0]


def test_el_valor_clavado_cinco_horas_es_sospechoso() -> None:
    """Los datos reales que destaparon el fallo, tal cual llegaron."""
    avisos = revisar_acumulado([
        _lectura("2026-07-29T14:53:00+00:00", 140),
        _lectura("2026-07-29T14:56:00+00:00", 140),
        _lectura("2026-07-29T15:20:00+00:00", 140),
        _lectura("2026-07-29T19:59:00+00:00", 140),
    ])

    assert len(avisos) == 1
    assert "no se movió" in avisos[0]
    assert "140 pasos" in avisos[0]


def test_dos_muestras_seguidas_con_el_mismo_valor_no_avisan() -> None:
    """Estar sentado veinte minutos es lo normal, no una avería.

    Un aviso que salta con todo funcionando se aprende a ignorar, y entonces
    tampoco se lee el día que sí importa.
    """
    assert revisar_acumulado([
        _lectura("2026-07-30T10:00:00+00:00", 2500),
        _lectura("2026-07-30T10:20:00+00:00", 2500),
    ]) == []


def test_una_serie_que_crece_no_avisa_de_nada() -> None:
    assert revisar_acumulado([
        _lectura("2026-07-30T06:00:00+00:00", 120),
        _lectura("2026-07-30T14:00:00+00:00", 4300),
        _lectura("2026-07-30T21:00:00+00:00", 9100),
    ]) == []


def test_el_corte_del_dia_es_el_LOCAL_y_no_el_de_UTC() -> None:
    """El acumulado se reinicia a medianoche en TU huso.

    Con dos muestras que en UTC caen el mismo día pero en local son días
    distintos, el reinicio a 0 se leería como una bajada imposible y saltaría un
    aviso falso. Es la decisión 29 otra vez: los días se cuentan en local.
    """
    assert revisar_acumulado([
        _lectura("2026-07-29T21:00:00+00:00", 9800),   # 29-07 23:00 local
        _lectura("2026-07-29T22:30:00+00:00", 150),    # 30-07 00:30 local
    ]) == []
