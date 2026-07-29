"""Arranca la app REAL con todo lo de fuera doblado. Lo usa `tools/verificar.py`.

Por qué existe: el guion de verificación tiene que recorrer las cuatro pantallas
en un navegador de verdad, y eso exige un servidor. Lo que NO puede exigir es
cobertura ni API keys (§2 del `CLAUDE.md`): un guion que necesita internet no
sirve en un camper, y uno que llama al modelo cuesta dinero cada vez que se
ejecuta, así que se dejaría de ejecutar.

Lo que se dobla, y solo eso:

  - `requests.get` / `requests.post` -> respuestas enlatadas de Nominatim,
    Open-Meteo, la API marina, met.no y Overpass. Se dobla al nivel del HTTP y
    no de las funciones del módulo a propósito: así los parseadores reales
    (`_parse_nominatim`, `_parse_forecast`, `_parse_metno`, `_parse_overpass`)
    entran en el recorrido en vez de saltárselo.
  - `build_provider` en `ai_orchestrator` y en `chat` -> un proveedor falso. Se
    parchea en los dos módulos y no en `llm_providers` porque los dos hicieron
    `from ... import build_provider`, así que tienen su propia referencia.

Todo lo demás es la app de producción, sin tocar. Un servidor de prueba que se
salta el camino real prueba una forma que no existe (decisión 36).

Una URL que no esté en el enrutador revienta en vez de degradar: si mañana
alguien añade una fuente externa, este archivo tiene que enterarse. Degradar
en silencio dejaría el guion en verde probando media app.

Uso (normalmente lo lanza `verificar.py`, no una persona):

    python tools/servidor_de_prueba.py --puerto 5099 --datos /tmp/x
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

PUERTO = 5099
CONTRASENA = "verificacion"
TOKEN_INGESTA = "token-de-verificacion-0123456789abcdef"

# met.no rechaza con un 403 sin cuerpo cualquier User-Agent de ejemplo
# (decisión 34), y `luna.py` se niega a llamar si detecta uno. El del guion
# tiene que pasar ese filtro o la luna saldría a medias por el motivo
# equivocado.
USER_AGENT = "roadtrip-verificacion/1.0 (verificacion@roadtrip.test)"

# Dónde "está" el navegador durante la verificación. Cudillero, que es donde
# caen las notas sembradas.
LAT, LON = 43.5622, -6.1456


# ---------------------------------------------------------------------------
# Entorno
# ---------------------------------------------------------------------------

def preparar_entorno(datos: Path) -> None:
    """Deja el entorno listo ANTES de importar `app.config`.

    `Config` resuelve las variables una vez, al importarse, así que este orden
    no es opcional.
    """
    from werkzeug.security import generate_password_hash

    # PBKDF2 con pocas iteraciones: el login del guion se ejecuta muchas veces
    # y aquí no hay ningún secreto que proteger.
    rapido = {"method": "pbkdf2:sha256:1000"}

    datos.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "SECRET_KEY": "clave-de-verificacion-no-secreta",
            "APP_PASSWORD_HASH": generate_password_hash(CONTRASENA, **rapido),
            "INGEST_TOKEN_HASH": generate_password_hash(TOKEN_INGESTA, **rapido),
            "DATA_DIR": str(datos),
            # El navegador entra por http://127.0.0.1. Chromium trata esa
            # dirección como contexto seguro y aceptaría la cookie `Secure`,
            # pero dejarlo al criterio del navegador es justo la trampa de la
            # decisión 15: si no la acepta, el síntoma es un bucle de login sin
            # ningún mensaje. Aquí se apaga a propósito, que es el único caso
            # que la decisión 15 contempla (probar por http).
            "SESSION_COOKIE_SECURE": "0",
            "NOMINATIM_USER_AGENT": USER_AGENT,
            # Nunca se llama: `build_provider` está parcheado. La key existe
            # para que `/healthz` informe `ia_configurada: true`, que es lo que
            # diría en producción.
            "LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "clave-de-mentira-para-la-verificacion",
            # Con esto la barra enseña los botones de lanzar los atajos.
            "SHORTCUT_FOTOS": "Fotos del viaje",
            "SHORTCUT_TELEMETRIA": "Telemetria del viaje",
        }
    )


# ---------------------------------------------------------------------------
# Las APIs de fuera, enlatadas
# ---------------------------------------------------------------------------

class _Respuesta:
    """Lo mínimo de `requests.Response` que usan los módulos."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)


NOMINATIM = {
    "display_name": "Cudillero, Asturias, España",
    "address": {
        "village": "Cudillero",
        "municipality": "Cudillero",
        "county": "Asturias",
        "state": "Asturias",
        "country": "España",
    },
}

# Tiempo de costa y verano: bueno para estar fuera, y con oleaje suficiente
# para que `water_sports()` tenga algo que decidir.
OPEN_METEO = {
    "timezone": "Europe/Madrid",
    "elevation": 47.0,
    "current": {
        "temperature_2m": 24.1,
        "apparent_temperature": 25.0,
        "precipitation": 0.0,
        "weather_code": 1,
        "wind_speed_10m": 11.0,
        "wind_gusts_10m": 19.0,
        "is_day": 1,
    },
    "daily": {
        "temperature_2m_max": [27.0],
        "temperature_2m_min": [16.0],
        "precipitation_probability_max": [10],
        "sunrise": ["2026-07-29T07:12"],
        "sunset": ["2026-07-29T21:44"],
    },
}

MARINE = {
    "current": {
        "wave_height": 0.6,
        "wave_period": 7.0,
        "wind_wave_height": 0.3,
        "swell_wave_height": 0.5,
        "sea_surface_temperature": 20.0,
    }
}

MET_NO = {
    "properties": {
        "moonrise": {"time": "2026-07-29T21:05+02:00", "azimuth": 118.4},
        "moonset": {"time": "2026-07-29T05:41+02:00", "azimuth": 243.2},
        "high_moon": {"time": "2026-07-29T01:22+02:00", "disc_centre_elevation": 41.0},
    }
}

OVERPASS = {
    "elements": [
        {"type": "node", "lat": 43.5650, "lon": -6.1500,
         "tags": {"name": "Playa de la Concha de Artedo", "natural": "beach"}},
        {"type": "node", "lat": 43.5661, "lon": -6.1442,
         "tags": {"name": "Mirador de la Garita", "tourism": "viewpoint"}},
        {"type": "way", "center": {"lat": 43.5590, "lon": -6.1521},
         "tags": {"name": "Faro de Cudillero", "historic": "tower"}},
        {"type": "node", "lat": 43.5701, "lon": -6.1390,
         "tags": {"name": "Camping L'Amuravela", "tourism": "camp_site"}},
        # Sin nombre: el parseador real tiene que descartarlo.
        {"type": "node", "lat": 43.5600, "lon": -6.1400, "tags": {"natural": "peak"}},
    ]
}


def doblar_red() -> None:
    """Sustituye `requests.get` y `requests.post` por el enrutador enlatado."""
    import requests

    from app.config import Config
    from app.modules.luna import _MET_NO_URL
    from app.modules.weather_context import _MARINE_URL

    def get(url: str, **_: Any) -> _Respuesta:
        if url.startswith(Config.NOMINATIM_URL):
            return _Respuesta(NOMINATIM)
        if url.startswith(_MARINE_URL):
            return _Respuesta(MARINE)
        if url.startswith(Config.OPEN_METEO_URL):
            return _Respuesta(OPEN_METEO)
        if url.startswith(_MET_NO_URL):
            return _Respuesta(MET_NO)
        raise RuntimeError(f"GET no previsto en el servidor de prueba: {url}")

    def post(url: str, **_: Any) -> _Respuesta:
        if "overpass" in url or "interpreter" in url:
            return _Respuesta(OVERPASS)
        raise RuntimeError(f"POST no previsto en el servidor de prueba: {url}")

    requests.get = get  # type: ignore[assignment]
    requests.post = post  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# El proveedor falso
# ---------------------------------------------------------------------------

class ProveedorFalso:
    """Contesta lo que pida el esquema, sin red y sin gastar un token.

    No hereda de `LLMProvider` para no arrastrar el módulo entero; cumple el
    contrato, que es lo único que se le pide (`name`, `model`, `generate`).
    """

    name = "falso"
    model = "verificacion"

    def __init__(self) -> None:
        self.last_usage = None

    def generate(self, *, system: str, context: str, schema: dict[str, Any]) -> str:
        # El esquema dice qué se está pidiendo: el del chat tiene una sola
        # clave, el de las recomendaciones tres. Distinguir por el esquema y no
        # por un parámetro mantiene el contrato tal cual.
        if "actividades" in schema.get("properties", {}):
            return json.dumps(
                {
                    "resumen": "Tarde despejada en la costa asturiana, buena para salir.",
                    "aviso": "El sendero del faro está expuesto al viento.",
                    "actividades": [
                        {
                            "titulo": "Mirador de la Garita",
                            "descripcion": "Balcón sobre el puerto, con la villa a los pies.",
                            "categoria": "naturaleza",
                            "por_que_ahora": "Cae el sol y el cielo está limpio.",
                            "duracion": "30-45 minutos",
                            "distancia": "a 400 m",
                            "origen": "lista_cercana",
                        },
                        {
                            "titulo": "Playa de la Concha de Artedo",
                            "descripcion": "Playa de cantos con acceso rodado.",
                            "categoria": "descanso",
                            "por_que_ahora": "El oleaje está bajo.",
                            "duracion": "2 horas",
                            "distancia": "a 5 km",
                            "origen": "conocimiento_general",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"respuesta": "Estás en Cudillero y hace buena tarde: yo subiría al mirador."},
            ensure_ascii=False,
        )


def doblar_proveedor() -> None:
    from app.modules import ai_orchestrator, chat

    ai_orchestrator.build_provider = lambda *a, **k: ProveedorFalso()  # type: ignore[assignment]
    chat.build_provider = lambda *a, **k: ProveedorFalso()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Datos de partida
# ---------------------------------------------------------------------------

def sembrar() -> None:
    """Notas, fotos y telemetría conocidas, para poder afirmar cifras exactas.

    Los números que el guion comprueba salen de aquí: 5 notas, 4 fotos, 2 años.
    Sembrar por `storage` y no con SQL a mano deja el esquema real de por medio.
    """
    from app.modules import storage

    storage.init_db()
    ahora = datetime.now(timezone.utc)

    notas = [
        # (dias_atras, texto, lat, lon, lugar, region)
        (0, "Llegada al puerto. Huele a mar.", 43.5622, -6.1456, "Cudillero, Asturias", "Asturias"),
        (1, "Amanecer desde el faro.", 43.5590, -6.1521, "Cudillero, Asturias", "Asturias"),
        (2, "Bufones de Pría, con marea alta.", 43.4210, -4.7560, "Llanes, Asturias", "Asturias"),
        (3, "Noche en Laredo, luna casi llena.", 43.4110, -3.4110, "Laredo, Cantabria", "Cantabria"),
        # Del año pasado: es lo que hace que el filtro de años del mapa tenga
        # dos opciones y se pueda probar de verdad.
        (400, "Primer viaje al norte.", 43.3620, -8.4110, "A Coruña, Galicia", "Galicia"),
    ]

    for indice, (dias, texto, lat, lon, lugar, region) in enumerate(notas):
        instante = ahora - timedelta(days=dias, hours=3)
        storage.insert_note(
            {
                "client_id": f"00000000-0000-4000-8000-{indice:012d}",
                "text": texto,
                "photo_path": None,
                "lat": lat,
                "lon": lon,
                "place_name": lugar,
                "region": region,
                "created_at": instante.isoformat(),
                "offset_original": "+02:00",
                "received_at": instante.isoformat(),
            }
        )

    fotos = [
        ("IMG_4736", 0, 43.5622, -6.1456),
        ("IMG_4737", 1, 43.5590, -6.1521),
        ("IMG_4738", 2, 43.4210, -4.7560),
        ("IMG_4739", 3, 43.4110, -3.4110),
    ]
    storage.insert_waypoints(
        [
            {
                "fuente": "atajos-iphone",
                "archivo": archivo,
                "capturado_en": (ahora - timedelta(days=dias, hours=5))
                .replace(tzinfo=None)
                .isoformat(timespec="seconds"),
                "offset_original": "+02:00",
                "lat": lat,
                "lon": lon,
                "altitud": 30.0,
                "camara": "Apple iPhone 13",
                "importado_en": ahora.isoformat(),
            }
            for archivo, dias, lat, lon in fotos
        ]
    )

    # Telemetría en las dos fuentes, que es lo que exige la decisión 36: la
    # pantalla las pinta juntas pero solo `atajos-iphone` certifica nada.
    muestras = []
    for dias in range(3):
        for hora, pasos in ((9, 1200), (14, 5400), (20, 9100)):
            medido = (ahora - timedelta(days=dias)).replace(
                hour=hora, minute=0, second=0, microsecond=0
            )
            muestras.append(
                {
                    "fuente": "atajos-iphone" if dias < 2 else "simulado",
                    "medido_en": medido.isoformat(),
                    "offset_original": "+02:00",
                    "pasos": pasos,
                    "bateria": 80 - hora,
                    "lat": LAT,
                    "lon": LON,
                    "recibido_en": medido.isoformat(),
                }
            )
    storage.insert_telemetry(muestras)

    storage.insert_lugar_del_dia(
        {
            "fecha_local": ahora.strftime("%Y-%m-%d"),
            "lat": LAT,
            "lon": LON,
            "place_name": "Cudillero, Asturias",
            "region": "Asturias",
            "momento_local": ahora.isoformat(),
            "registrado_en": ahora.isoformat(),
        }
    )


# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor de prueba con todo doblado.")
    parser.add_argument("--puerto", type=int, default=PUERTO)
    parser.add_argument("--datos", type=Path, required=True, help="Directorio de datos (temporal).")
    args = parser.parse_args()

    preparar_entorno(args.datos)

    doblar_red()
    doblar_proveedor()
    sembrar()

    from app.app import app

    # `threaded=True` para que un `fetch` en vuelo no bloquee los estáticos de
    # la siguiente pantalla. En PythonAnywhere gratuito hay UN worker, así que
    # esto NO reproduce ese cuello de botella: medirlo es trabajo del §2 y se
    # hace en el servidor, no aquí (decisión 43).
    app.run(host="127.0.0.1", port=args.puerto, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
