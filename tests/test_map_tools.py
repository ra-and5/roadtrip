"""Herramientas de mapa para el chat.

Sin red y sin API keys. Aquí se prueba el contrato: qué se detecta, cómo se
normaliza y cómo se degrada cuando Google no está configurado.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from app.config import Config
from app.modules import aemet, map_tools, storage
from app.modules.location_context import Place


@pytest.fixture(autouse=True)
def entorno(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    storage.init_db()
    yield


def test_detecta_sitios_practicos() -> None:
    assert map_tools.detectar_sitio("bar más cerca") == "bar"
    assert map_tools.detectar_sitio("necesito una farmacia abierta") == "farmacia"
    assert map_tools.detectar_sitio("área camper para dormir") == "area camper"


def test_detecta_plan_cerca_como_varias_consultas_acotadas() -> None:
    assert map_tools.detectar_consultas_sitios("qué hago cerca para cenar?") == [
        "restaurante",
        "bar",
        "cafe",
    ]
    assert map_tools.detectar_consultas_sitios("dónde puedo dormir y comprar agua?") == [
        "area camper",
        "camping",
        "supermercado",
    ]


def test_detecta_ruta_de_origen_a_destino() -> None:
    assert map_tools.detectar_ruta("cuánto tardo de Burgos a Vitoria?") == (
        "Burgos",
        "Vitoria",
    )


def test_detecta_ruta_desde_ubicacion_actual() -> None:
    lugar = Place(lat=38.39, lon=-0.51, name="San Vicente")

    assert map_tools.detectar_ruta("cuánto tardo a Vitoria?", lugar) == (
        "San Vicente",
        "Vitoria",
    )


def test_sin_key_no_revienta_y_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "GOOGLE_MAPS_API_KEY", "")
    lugar = Place(lat=38.39, lon=-0.51, name="San Vicente")

    bundle = map_tools.ejecutar("bar más cerca", lugar)

    assert not bundle.sitios
    assert any("GOOGLE_MAPS_API_KEY" in aviso for aviso in bundle.avisos)


def test_parsea_places_con_field_mask_minimo() -> None:
    payload = {
        "places": [
            {
                "displayName": {"text": "Bar Pepe"},
                "primaryType": "bar",
                "formattedAddress": "Calle Mayor 1",
                "location": {"latitude": 38.4, "longitude": -0.5},
                "rating": 4.2,
                "currentOpeningHours": {"openNow": True},
                "googleMapsLinks": {"placeUri": "https://maps.google.com/?cid=1"},
            }
        ]
    }

    sitio = map_tools._parse_places(payload)[0]  # noqa: SLF001

    assert sitio.nombre == "Bar Pepe"
    assert sitio.abierto == "abierto ahora"
    assert sitio.rating == 4.2


def test_parsea_route_y_url_de_maps() -> None:
    payload = {
        "routes": [
            {
                "distanceMeters": 121000,
                "duration": "5400s",
                "staticDuration": "5100s",
            }
        ]
    }

    ruta = map_tools._parse_route(payload, "Burgos", "Vitoria")  # noqa: SLF001

    assert ruta.distancia_km == 121.0
    assert ruta.duracion_trafico_min == 90
    assert "google.com/maps/dir" in ruta.maps_url


def test_formatea_resultados_para_prompt() -> None:
    bundle = map_tools.ToolBundle(
        sitios=[
            map_tools.ToolPlace(
                nombre="Bar Pepe",
                abierto="abierto ahora",
                rating=4.2,
                direccion="Calle Mayor 1",
                maps_url="https://maps.example/bar",
            )
        ],
        ruta=map_tools.ToolRoute(
            origen="Burgos",
            destino="Vitoria",
            distancia_km=121,
            duracion_trafico_min=90,
            maps_url="https://maps.example/ruta",
        ),
    )

    texto = map_tools.formatear(bundle)

    assert "SITIOS CONSULTADOS" in texto
    assert "Bar Pepe" in texto
    assert "RUTA CONSULTADA" in texto
    assert "90 min con tráfico" in texto


class FakeMaps:
    def __init__(self) -> None:
        self.consultas: list[str] = []
        self.salidas: list[object] = []

    def buscar_sitios(self, consulta: str, lat: float, lon: float) -> list[map_tools.ToolPlace]:
        self.consultas.append(consulta)
        return [
            map_tools.ToolPlace(nombre=f"{consulta} 1", direccion="cerca")
        ]

    def calcular_ruta(
        self, origen: str, destino: str, salida: object | None = None
    ) -> map_tools.ToolRoute:
        self.salidas.append(salida)
        return map_tools.ToolRoute(origen=origen, destino=destino)

    def horas_de_salida(
        self, origen: str, destino: str, ahora: object, horas: int = 4
    ) -> map_tools.ToolHorarios:
        return map_tools.ToolHorarios(
            origen=origen,
            destino=destino,
            opciones=(
                map_tools.Salida(hora="10:00", duracion_min=80, retraso_min=10),
                map_tools.Salida(hora="11:00", duracion_min=70, retraso_min=0),
            ),
        )


class FakeAemet(aemet.AemetClient):
    def __init__(self) -> None:
        pass

    def prediccion_nacional(self) -> list[str]:
        return ["hoy: chubascos fuertes en el norte"]

    def avisos_espana(self) -> list[str]:
        return ["Tormentas · Severe · Pirineo"]

    def radar_nacional(self) -> str:
        return "Radar nacional disponible: https://aemet.example/radar.png"


def test_ejecuta_varias_herramientas_de_plan_sin_pasarse() -> None:
    provider = FakeMaps()

    bundle = map_tools.ejecutar(
        "plan para cenar, aparcar y comprar cerca",
        Place(lat=38.39, lon=-0.51, name="San Vicente"),
        provider=provider,
    )

    assert provider.consultas == ["restaurante", "bar", "cafe"]
    assert len(bundle.sitios) == 3


def test_pregunta_de_territorio_mete_aemet_en_lecturas() -> None:
    bundle = map_tools.ejecutar(
        "cómo está España de avisos y radar de lluvia?",
        Place(lat=38.39, lon=-0.51, name="San Vicente"),
        provider=FakeMaps(),
        aemet_client=FakeAemet(),
    )

    texto = map_tools.formatear(bundle)

    assert "AEMET_TERRITORIO" in texto
    assert "chubascos fuertes" in texto
    assert "Tormentas" in texto
    assert "Radar nacional" in texto


def test_memoria_basica_lee_sqlite() -> None:
    storage.insert_note(
        {
            "client_id": "n1",
            "text": "Dormí junto al puerto",
            "photo_path": None,
            "lat": 43.0,
            "lon": -5.0,
            "place_name": "Puerto bonito",
            "region": "Asturias",
            "created_at": "2026-07-30T22:00:00+00:00",
            "offset_original": "+02:00",
            "received_at": "2026-07-30T22:01:00+00:00",
        }
    )

    bundle = map_tools.ejecutar(
        "dónde dormí ayer?",
        Place(lat=43.0, lon=-5.0, name="Puerto bonito"),
        provider=map_tools.GoogleMapsProvider(api_key=""),
    )

    assert any("Dormí junto al puerto" in linea for linea in bundle.memoria)


# ---------------------------------------------------------------------------
# Tráfico
# ---------------------------------------------------------------------------

def test_sin_tiempo_libre_el_trafico_es_sin_datos_y_no_fluido() -> None:
    """El fallo que este veredicto existe para no cometer.

    Si Google no devuelve `staticDuration` no hay con qué comparar. Contestar
    "fluido" ahí sería tranquilizar sin haber mirado, que es justo lo que la
    decisión 22 prohíbe: hay que distinguir "no hay retención" de "no lo sé".
    """
    estado, _motivo, retraso = map_tools.veredicto_trafico(90, None)
    assert estado == "sin_datos"
    assert retraso is None


@pytest.mark.parametrize(
    "con_trafico,libre,esperado",
    [
        (70, 70, "fluido"),      # exacto
        (66, 70, "fluido"),      # más rápido que en libre: de madrugada pasa
        (72, 70, "fluido"),      # 2 min: no llega al suelo absoluto de 4 min
        (76, 70, "fluido"),      # 6 min pasa el suelo, pero 8.6% < 12%: no es denso
        (79, 70, "denso"),       # 9 min = 12.9%, ya se nota
        (95, 70, "atasco"),      # 25 min = 36%
    ],
)
def test_umbrales_del_veredicto(con_trafico: int, libre: int, esperado: str) -> None:
    estado, _motivo, _retraso = map_tools.veredicto_trafico(con_trafico, libre)
    assert estado == esperado


def test_un_retraso_corto_en_trayecto_corto_no_es_atasco() -> None:
    """El suelo absoluto. Sin él, 2 minutos sobre 6 son un 33% y saldría
    "atasco" por un semáforo."""
    estado, _motivo, _retraso = map_tools.veredicto_trafico(8, 6)
    assert estado == "fluido"


def test_la_hora_de_salida_entra_en_la_clave_de_cache() -> None:
    """Sin esto, "¿y si salgo a las 8?" devolvería la respuesta cacheada de
    "¿y si salgo a las 20?" — mismas cifras para todas las horas, sin error."""
    a = map_tools._cache_key("google_routes", "Bilbao", "Vitoria", "2026-08-05T08")
    b = map_tools._cache_key("google_routes", "Bilbao", "Vitoria", "2026-08-05T20")
    assert a != b


def test_preguntar_por_trafico_avisa_de_lo_que_no_se_ve() -> None:
    """Google da congestión y NO da accidentes ni cortes. Si el modelo no lo
    sabe, contesta "no hay incidencias" desde una ausencia de datos."""
    bundle = map_tools.ejecutar(
        "¿hay algún corte o accidente en la A-8?",
        Place(lat=43.3, lon=-2.9, name="Bilbao"),
        provider=FakeMaps(),
    )
    texto = map_tools.formatear(bundle)
    assert "COBERTURA DEL TRÁFICO" in texto
    assert "NO ve accidentes" in texto


def test_las_horas_de_salida_solo_si_las_piden() -> None:
    """Son cuatro llamadas de pago contra una: no pueden dispararse de rebote
    en un simple "¿cuánto tardo?"."""
    provider = FakeMaps()
    lugar = Place(lat=43.3, lon=-2.9, name="Bilbao")

    solo_ruta = map_tools.ejecutar("¿cuánto tardo a Vitoria?", lugar, provider=provider)
    assert solo_ruta.ruta is not None
    assert solo_ruta.horarios is None

    con_horas = map_tools.ejecutar(
        "¿a qué hora salgo a Vitoria para evitar el atasco?", lugar, provider=provider
    )
    assert con_horas.horarios is not None


def test_la_mejor_hora_es_la_mas_rapida() -> None:
    horarios = map_tools.ToolHorarios(
        origen="a",
        destino="b",
        opciones=(
            map_tools.Salida(hora="10:00", duracion_min=80, retraso_min=10),
            map_tools.Salida(hora="11:00", duracion_min=70, retraso_min=0),
        ),
    )
    mejor = horarios.mejor()
    assert mejor is not None and mejor.hora == "11:00"
    assert map_tools.ToolHorarios(origen="a", destino="b").mejor() is None


def test_la_hora_de_salida_se_manda_en_utc() -> None:
    """Una hora local mandada como si fuera UTC desplaza la predicción dos horas
    en verano, y no da ningún error: solo el tráfico de otro momento."""
    from datetime import datetime, timedelta, timezone

    madrid = timezone(timedelta(hours=2))
    assert map_tools._rfc3339(datetime(2026, 8, 5, 20, 0, tzinfo=madrid)) == "2026-08-05T18:00:00Z"


def test_se_cuentan_los_tramos_de_los_dos_sitios_donde_google_los_pone() -> None:
    ruta = {
        "travelAdvisory": {"speedReadingIntervals": [{"speed": "TRAFFIC_JAM"}]},
        "legs": [{"travelAdvisory": {"speedReadingIntervals": [
            {"speed": "SLOW"}, {"speed": "NORMAL"}
        ]}}],
    }
    assert map_tools._contar_tramos(ruta) == (1, 1)


@pytest.mark.parametrize(
    "pregunta,esperado",
    [
        # El bug que salió al probar contra la API real: el patrón de «a …»
        # enganchaba la «a» de «a qué hora» y mandaba a Google la frase entera
        # como destino. Google la geocodifica sin protestar y devuelve una ruta
        # con su distancia y su tiempo, así que NO da error: da un viaje
        # convincente a un sitio que nadie ha pedido (decisión 11).
        ("¿a qué hora salgo a Vitoria para evitar el atasco?", ("Bilbao", "Vitoria")),
        ("¿a qué hora me voy a Santander mañana?", ("Bilbao", "Santander")),
        ("cuánto tardo a Vitoria?", ("Bilbao", "Vitoria")),
        ("cuánto tardo de Burgos a Vitoria?", ("Burgos", "Vitoria")),
        ("¿cuánto se tarda hasta Oviedo?", ("Bilbao", "Oviedo")),
        ("cuánto tardo a Vitoria-Gasteiz?", ("Bilbao", "Vitoria-Gasteiz")),
        # Y lo que NO puede convertirse en una ruta.
        ("me voy a dormir", None),
        ("¿hay atasco?", None),
        ("dónde ceno cerca?", None),
    ],
)
def test_el_destino_es_un_sitio_y_no_media_frase(
    pregunta: str, esperado: tuple[str, str] | None
) -> None:
    lugar = Place(lat=43.26, lon=-2.93, name="Bilbao")
    assert map_tools.detectar_ruta(pregunta, lugar) == esperado


def test_un_destino_larguisimo_se_descarta() -> None:
    """El último cinturón: si algo se escapa de los patrones, un topónimo de
    media línea no puede llegar a Google."""
    assert map_tools._limpiar_lugar("x" * 60) is None  # noqa: SLF001
