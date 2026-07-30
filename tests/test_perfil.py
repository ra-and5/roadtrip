"""Tests del perfil. Sin red y sin API keys.

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
from app.modules import perfil, storage

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


def _armar(reales: list[dict], todas: list[dict] | None = None, **kwargs: Any) -> perfil.Perfil:
    return perfil.armar(
        reales,
        todas if todas is not None else reales,
        kwargs.get("notas", []),
        kwargs.get("puntos", []),
        kwargs.get("dias", {"total": 0, "primero": None, "ultimo": None, "huecos": None}),
        HOY,
        ahora=AHORA,
    )


def _fuente(p: perfil.Perfil, clave: str) -> perfil.EstadoFuente | None:
    return next((f for f in p.fuentes if f.clave == clave), None)


# --- Días parciales: un suelo no es un total ---------------------------------

def test_perder_el_ultimo_envio_del_dia_deja_un_suelo_y_se_marca() -> None:
    """El caso caro, y es MUDO: la cifra que queda es plausible.

    La columna de pasos es un acumulado (decisión 25), así que el total del día
    lo trae su último envío. Si ese envío se pierde —estás en un valle a las
    23:55— lo que queda es lo que llevabas a las 18:00: un SUELO. Pintado como
    total, ese día parece de descanso y arrastra la media hacia abajo sin dar
    ningún error.
    """
    muestras = _dia_completo("2026-07-27", 15000) + [
        _muestra("2026-07-28", 6, 900),
        _muestra("2026-07-28", 18, 12900),  # y ya no llega nada más ese día
    ]

    p = _armar(muestras)
    por_fecha = {b.fecha: b for b in p.serie}

    assert por_fecha["2026-07-28"].pasos == 12900
    assert por_fecha["2026-07-28"].parcial
    assert not por_fecha["2026-07-27"].parcial
    # Y lo que de verdad se estaba estropeando: la media solo cuenta el día
    # cerrado, en vez de promediar 15.000 con un 12.900 que no es el total.
    assert p.cuerpo.media_diaria == 15000


def test_perder_los_envios_de_ENMEDIO_no_trunca_el_dia() -> None:
    """La otra mitad, y es la que impide pasarse de celoso.

    Un acumulado se cura solo: perder las 10:00 y las 14:00 no pierde nada
    porque el de las 23:55 ya trae el total. Marcar ese día como parcial sería
    tirar un día bueno de la media y avisar de un problema que no existe.
    Comprobado sobre la serie simulada del 27-07-2026.
    """
    muestras = [
        _muestra("2026-07-27", 6, 600),
        _muestra("2026-07-27", 21, 10051),  # 23:55 locales: el día está cerrado
        _muestra("2026-07-28", 6, 900),
        _muestra("2026-07-28", 21, 14974),
    ]

    p = _armar(muestras)
    por_fecha = {b.fecha: b for b in p.serie}

    assert not por_fecha["2026-07-27"].parcial
    assert not por_fecha["2026-07-28"].parcial
    assert p.cuerpo.media_diaria == round((10051 + 14974) / 2)


def test_hoy_siempre_es_parcial_aunque_haya_llegado_todo() -> None:
    """A las 12:00 llevas los pasos de media mañana, no los del día."""
    p = _armar(_dia_completo("2026-07-29", 5000))

    hoy = next(b for b in p.serie if b.es_hoy)
    assert hoy.parcial


def test_sin_muestra_de_hoy_ayer_no_se_cae_de_la_media() -> None:
    """La media descartaba el ÚLTIMO elemento dando por hecho que era hoy.

    Pero `pasos_por_dia` solo trae los días CON dato: si hoy no ha llegado nada
    todavía, el último elemento es ayer, y se estaba tirando un día completo.
    """
    muestras = _dia_completo("2026-07-27", 10000) + _dia_completo("2026-07-28", 12000)

    p = _armar(muestras)

    assert p.cuerpo.media_diaria == 11000


def test_un_dia_sin_dato_no_se_llama_ademas_parcial() -> None:
    """`pasos=None` ya dice "no lo sé". Marcarlo también parcial sería decir dos
    veces lo mismo con dos palabras que significan cosas distintas."""
    p = _armar(_dia_completo("2026-07-28", 9000))

    vacio = next(b for b in p.serie if b.fecha == "2026-07-25")
    assert vacio.pasos is None
    assert not vacio.parcial


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

    assert len(p.serie) == perfil.DIAS_DE_SERIE
    assert p.serie[-1].fecha == HOY.isoformat()
    assert p.serie[-1].es_hoy
    assert p.serie[-1].pasos is None


# --- Fiabilidad --------------------------------------------------------------

def test_lo_simulado_no_puede_declararse_fiable_a_si_mismo() -> None:
    """La decisión 36 separa las series en la tabla; leerlas juntas la anularía."""
    simuladas = _dia_completo("2026-07-28", 12000, "simulado") + \
        _dia_completo("2026-07-29", 9000, "simulado")

    p = _armar([], simuladas)

    # Los pasos se enseñan (si no, el perfil saldría vacío y no se entendería),
    # pero la fuente dice que no hay ni una muestra real.
    assert p.cuerpo.pasos_hoy == 9000
    assert p.hay_simulado
    assert _fuente(p, "telemetria").estado == perfil.SIN_DATOS
    assert _fuente(p, "simulado").estado == perfil.SIMULADA


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

    assert _fuente(p, "telemetria").estado == perfil.DEMOSTRADA


def test_dias_a_medias_no_son_huecos_pero_tampoco_son_fiables() -> None:
    """Seis envíos al día que entregan dos dan cero huecos y no son una serie."""
    reales = [_muestra("2026-07-28", 10, 4000), _muestra("2026-07-29", 10, 3000)]

    p = _armar(reales)

    fuente = _fuente(p, "telemetria")
    assert fuente.estado == perfil.CON_HUECOS
    assert "a medias" in fuente.detalle


def test_una_fuente_que_ha_dejado_de_llegar_se_llama_parada() -> None:
    """El caso real del 29-07-2026: cinco muestras disparadas a mano una noche y
    ninguna automatización detrás.

    Contado como un hueco más salía "con huecos", que es lo mismo que dice una
    serie que va regular y se cura sola. Aquí no se cura nada: no hay nada
    corriendo. Dos averías distintas con el mismo nombre hacen esperar en vez de
    ir a mirar Atajos.
    """
    reales = [_muestra("2026-07-28", 20, 5688), _muestra("2026-07-28", 21, 5688)]

    fuente = _fuente(_armar(reales), "telemetria")

    assert fuente.estado == perfil.PARADA
    assert "SIN LLEGAR" in fuente.detalle
    assert "automatizaciones" in fuente.detalle


def test_el_hueco_de_la_noche_no_da_una_fuente_por_parada() -> None:
    """La otra mitad, y es la que evita el aviso que se aprende a ignorar.

    A las 12:00 la última muestra normal es la de las 06:00 —o la de anoche si
    hoy aún no ha entrado ninguna—, así que el umbral tiene que aguantar el
    hueco nocturno entero sin saltar.
    """
    reales = _dia_completo("2026-07-28", 9000) + [_muestra("2026-07-29", 6, 700)]

    assert _fuente(_armar(reales), "telemetria").estado != perfil.PARADA


def test_sin_ninguna_muestra_no_se_dice_parada_sino_sin_datos() -> None:
    """"Parada" afirma que llegó y dejó de llegar. Sin una sola muestra eso es
    falso, y manda a revisar una automatización que a lo mejor nunca se montó."""
    assert _fuente(_armar([]), "telemetria").estado == perfil.SIN_DATOS


def test_las_notas_y_las_fotos_son_fuentes_demostradas() -> None:
    p = _armar(
        [],
        notas=[_nota("2026-07-28T10:00:00+00:00")],
        puntos=[{"lat": 43.5, "lon": -6.1, "capturado_en": "2026-07-28T10:00:00"}],
    )

    assert _fuente(p, "notas").estado == perfil.DEMOSTRADA
    assert _fuente(p, "fotos").estado == perfil.DEMOSTRADA


def test_dias_registrados_con_huecos_no_pasan_por_completos() -> None:
    p = _armar([], dias={"total": 3, "primero": "2026-07-25", "ultimo": "2026-07-29",
                         "huecos": 2})

    fuente = _fuente(p, "lugar_del_dia")
    assert fuente.estado == perfil.CON_HUECOS
    assert "2 sin registrar" in fuente.detalle


# --- El día del viaje --------------------------------------------------------

def test_el_dia_del_viaje_cuenta_calendario_y_no_dias_con_notas() -> None:
    """Dos notas en dos días sueltos, pero el viaje lleva diez días."""
    p = _armar([], notas=[
        _nota("2026-07-20T10:00:00+00:00"),
        _nota("2026-07-29T10:00:00+00:00"),
    ])

    assert p.dia_del_viaje == 10      # del 20 al 29, ambos incluidos


def test_una_foto_sola_ya_empieza_el_viaje() -> None:
    """El primer momento puede ser una foto: no hace falta haber escrito nada."""
    p = _armar([], puntos=[{"lat": 43.5, "lon": -6.1, "capturado_en": "2026-07-26T14:23:37"}])

    assert p.dia_del_viaje == 4


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


def test_el_perfil_responde_con_la_base_de_datos_vacia(sesion: Any) -> None:
    """Es una pantalla de lectura: sin datos enseña huecos, no un 500."""
    respuesta = sesion.get("/api/perfil?zona=Europe/Madrid")

    assert respuesta.status_code == 200
    datos = respuesta.get_json()
    assert len(datos["serie"]) == perfil.DIAS_DE_SERIE
    assert datos["dia_del_viaje"] is None
    assert {f["clave"] for f in datos["fuentes"]} >= {"telemetria", "notas", "fotos"}


def test_una_zona_horaria_absurda_no_tumba_el_perfil(sesion: Any) -> None:
    respuesta = sesion.get("/api/perfil?zona=../../etc/passwd")

    assert respuesta.status_code == 200


def test_la_api_no_se_puede_cachear(sesion: Any) -> None:
    """Sin esto, importas una foto y el mapa sigue enseñando lo de antes.

    Un GET sin `Cache-Control` lo puede reutilizar el navegador por su cuenta
    (Safari en iOS lo hace), y el fallo es mudo: parece que la importación no
    llegó. Las páginas SÍ se cachean, que es lo que las hace abrir sin cobertura.
    """
    assert sesion.get("/api/perfil").headers["Cache-Control"] == "no-store"
    assert sesion.get("/api/ruta").headers["Cache-Control"] == "no-store"
    assert sesion.get("/api/notes").headers["Cache-Control"] == "no-store"
    assert "no-store" not in sesion.get("/perfil").headers.get("Cache-Control", "")


def test_el_perfil_pide_sesion() -> None:
    flask_app.config["TESTING"] = True
    respuesta = flask_app.test_client().get("/api/perfil")

    assert respuesta.status_code in (302, 401)


def test_una_serie_puntual_con_los_numeros_mal_se_dice_en_el_perfil() -> None:
    """Llegar sin huecos y decir la verdad son dos cosas distintas.

    El 30-07-2026 el atajo dejó de sumar las muestras de Salud y mandaba UNA:
    298 pasos donde la app decía más de 2.000. La cobertura salía impecable
    —los envíos llegaban puntuales— y el número era falso. Contar envíos no
    responde a si el envío dice algo cierto, así que va como fuente aparte.
    """
    from app.modules.perfil import armar

    hoy = date(2026, 7, 29)
    ahora = datetime(2026, 7, 29, 22, 30, tzinfo=timezone.utc)
    clavadas = [
        {"fuente": "atajos-iphone", "medido_en": f"2026-07-29T{h:02d}:00:00+00:00",
         "offset_original": "+02:00", "pasos": 140, "bateria": 80,
         "lat": 38.39, "lon": -0.52, "recibido_en": f"2026-07-29T{h:02d}:00:00+00:00"}
        for h in (14, 15, 17, 19)
    ]

    perfil = armar(clavadas, clavadas, [], [], {}, hoy, ahora=ahora)
    claves = {f.clave: f for f in perfil.fuentes}

    assert "pasos_coherentes" in claves, "el perfil no avisa de unos pasos imposibles"
    assert "no se movió" in claves["pasos_coherentes"].detalle
    assert "Salud" in claves["pasos_coherentes"].detalle, "no dice cómo comprobarlo"


def test_una_serie_que_crece_no_saca_ese_aviso() -> None:
    """Un aviso que sale con todo bien se aprende a ignorar."""
    from app.modules.perfil import armar

    hoy = date(2026, 7, 29)
    ahora = datetime(2026, 7, 29, 22, 30, tzinfo=timezone.utc)
    creciendo = [
        {"fuente": "atajos-iphone", "medido_en": f"2026-07-29T{h:02d}:00:00+00:00",
         "offset_original": "+02:00", "pasos": pasos, "bateria": 80,
         "lat": 38.39, "lon": -0.52, "recibido_en": f"2026-07-29T{h:02d}:00:00+00:00"}
        for h, pasos in ((6, 120), (10, 2400), (14, 5100), (19, 8300))
    ]

    perfil = armar(creciendo, creciendo, [], [], {}, hoy, ahora=ahora)
    assert "pasos_coherentes" not in {f.clave for f in perfil.fuentes}
