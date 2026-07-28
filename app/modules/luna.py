"""La luna: fase e iluminación calculadas aquí, salida y puesta de met.no.

Funciones de entrada:
    `fase(instante) -> Fase`                 sin red, siempre funciona
    `efemerides(lat, lon, momento) -> Efemerides`   met.no, puede fallar
    `veredicto_nocturno(...) -> Veredicto`   regla explícita, no se le pregunta al modelo

**Por qué híbrido, y no una de las dos cosas.** El motivo es concreto y está
medido: **met.no da la fase de las 00:00 del día pedido, no la del momento.**
Para el 28-07-2026 devuelve `moonphase: 162.1`, o sea 97,6 % — la luna a
medianoche. A las 17:20 de ese mismo día está al 99,1 %, y cerca de los cuartos
la diferencia llega a varios puntos. Una tarjeta que dice "la luna de esta
noche" enseñando la de medianoche pasada está dando un dato de hace 17 horas.
Sacarlo de la API exigiría pedir dos días e interpolar: más código que las ~25
líneas de aritmética que hay aquí.

El segundo motivo es la degradación: si met.no falla o el proxy lo bloquea,
la luna se queda a medias en vez de desaparecer.

**Lo que NO es un motivo, aunque lo parezca:** "funciona sin cobertura". La app
corre en el servidor, así que un móvil sin cobertura no llega a `/api/contexto`
y no ve nada — ni luna, ni tiempo, ni el nombre del pueblo. El argumento de la
cobertura vale para la cola de notas del navegador (decisión 26), no para esto.
Estuvo escrito aquí y era falso.

La salida, la puesta y el azimut son
bastante más código (dependen de la latitud, del paralaje y de la refracción) y
met.no los da hechos, así que se piden y se degradan como cualquier otra fuente
(decisión 9). Lo que **no** se hace es calcular unas y pedir las otras a la vez
para compararlas: sería trabajo sin destinatario.

Precisión del cálculo, medida y no supuesta. Contrastado contra `api.met.no`
en 20 fechas repartidas por dos meses (julio y agosto de 2026):

    peor error de ángulo de fase ....... 0,46°
    peor error de iluminación .......... 0,31 puntos porcentuales

Es el algoritmo de Meeus (*Astronomical Algorithms*, cap. 48) con sus términos
periódicos principales. Con la aproximación ingenua —"días transcurridos desde
una luna nueva conocida, entre 29,53"— no habría hecho falta ni comparar: esa
supone que la Luna va a velocidad constante, y su órbita es elíptica.

**El veredicto se calcula en Python, no en el prompt.** "Luna llena y despejado:
se puede caminar de noche" es una regla explícita y testeable, igual que
`weather_context.water_sports()` (decisión 5). Preguntárselo al modelo daría una
respuesta distinta cada vez y no se podría auditar.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import requests

from app.config import Config
from app.modules import storage

__all__ = [
    "Efemerides",
    "Fase",
    "Luna",
    "LunaError",
    "Veredicto",
    "efemerides",
    "fase",
    "veredicto_nocturno",
]

_MET_NO_URL = "https://api.met.no/weatherapi/sunrise/3.0/moon"

# La salida y la puesta de la luna en un sitio y un día concretos no cambian
# nunca, y la clave de caché lleva la fecha dentro. Así que se cachea largo: lo
# que se ahorra es una llamada de red en un móvil con mala cobertura.
_EFEMERIDES_CACHE_TTL = 30 * 24 * 3600

# met.no rechaza los User-Agent genéricos y los de ejemplo con un 403 de nginx
# SIN ningún mensaje que lo explique. Comprobado contra la API real: el valor
# por defecto de `NOMINATIM_USER_AGENT` (que lleva `example.com`) devuelve 403,
# y `Mozilla/5.0` también; con un contacto real devuelve 200.
#
# Sin esta comprobación, un despliegue que no hubiera tocado esa variable vería
# la luna "caída" para siempre, y el motivo sería una página HTML de nginx que
# no dice nada. Es la decisión 11 en su forma habitual: no da error, solo un
# hueco permanente que nadie sabe explicar. Mejor negarse a llamar y decir qué
# variable hay que arreglar.
#
# La lista es corta a propósito: solo los dominios que la RFC 2606 reserva para
# ejemplos, que por definición no pueden ser de nadie. Cualquier heurística más
# lista ("¿lleva arroba?", "¿empieza por contacto@?") rechazaría contactos
# buenos, y un falso positivo aquí apaga la luna sin motivo.
_CONTACTOS_DE_EJEMPLO = ("example.com", "example.org", "example.net")


class LunaError(Exception):
    """Error recuperable al consultar las efemérides lunares."""


# ---------------------------------------------------------------------------
# Fase e iluminación: sin red, siempre
# ---------------------------------------------------------------------------

# Los ocho nombres, en tramos de 45° CENTRADOS en cada hito: "luna llena" es de
# 157,5° a 202,5°, no a partir de 180°. El convenio del ángulo es el de met.no:
# 0 = nueva, 90 = cuarto creciente, 180 = llena, 270 = cuarto menguante.
# Comprobado contra la API real, no supuesto: la iluminación crece de 0 a 180 y
# decrece de 180 a 360.
_NOMBRES: tuple[str, ...] = (
    "luna nueva",
    "creciente cóncava",
    "cuarto creciente",
    "creciente gibosa",
    "luna llena",
    "menguante gibosa",
    "cuarto menguante",
    "menguante cóncava",
)


@dataclass(frozen=True)
class Fase:
    """En qué punto de su ciclo está la luna. Se calcula, no se consulta."""

    angulo: float          # 0 = nueva, 180 = llena
    iluminacion_pct: float  # 0 a 100
    nombre: str
    creciendo: bool        # va a más (True) o a menos (False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dia_juliano(instante: datetime) -> float:
    """Días julianos desde el instante dado.

    Se calcula desde el epoch de Unix en vez de con la fórmula de calendario:
    `timestamp()` ya resuelve la zona horaria del `datetime`, así que da igual
    si viene en hora local o en UTC. Una conversión menos es un sitio menos
    donde equivocarse de huso, que en este proyecto ya ha pasado.
    """
    return instante.timestamp() / 86400.0 + 2440587.5


def fase(instante: datetime) -> Fase:
    """Fase e iluminación de la luna en un instante. Sin red y determinista.

    Meeus, cap. 48. `i` es el ángulo de fase visto desde la Tierra (0 = llena,
    180 = nueva), y se convierte al convenio de met.no restándolo de 180 para
    que 0 sea la luna nueva y no haya dos escalas distintas en el proyecto.

    La iluminación sale de `(1 - cos(ángulo)) / 2`: es la fracción del disco
    visible, no una escala inventada. Contrastada contra tutiempo.net para el
    28-07-2026: 97,58 % calculado contra 97,56 % de la referencia.
    """
    t = (_dia_juliano(instante) - 2451545.0) / 36525.0

    # Elongación media de la Luna, anomalía media del Sol y de la Luna.
    d = 297.8501921 + 445267.1114034 * t - 0.0018819 * t * t + t**3 / 545868 - t**4 / 113065000
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t * t + t**3 / 24490000
    mp = 134.9633964 + 477198.8675055 * t + 0.0087414 * t * t + t**3 / 69699 - t**4 / 14712000

    r = math.radians
    angulo_de_fase = (
        180
        - d
        - 6.289 * math.sin(r(mp))
        + 2.100 * math.sin(r(m))
        - 1.274 * math.sin(r(2 * d - mp))
        - 0.658 * math.sin(r(2 * d))
        - 0.214 * math.sin(r(2 * mp))
        - 0.110 * math.sin(r(d))
    )

    angulo = (180 - angulo_de_fase) % 360
    iluminacion = (1 - math.cos(r(angulo))) / 2 * 100

    # El desplazamiento de 22,5° antes del módulo es lo que hace que el tramo
    # de la luna nueva cierre el círculo: 350° y 5° son la misma luna, y sin
    # esto el primero caía en "menguante cóncava".
    nombre = _NOMBRES[int(((angulo + 22.5) % 360) // 45)]

    return Fase(
        angulo=round(angulo, 2),
        iluminacion_pct=round(iluminacion, 1),
        nombre=nombre,
        creciendo=angulo < 180,
    )


# ---------------------------------------------------------------------------
# Salida, puesta y azimut: met.no
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Efemerides:
    """Cuándo sale y se pone la luna, y por dónde."""

    salida: str = ""            # ISO local, "" si ese día no sale
    salida_azimut: float | None = None
    puesta: str = ""
    puesta_azimut: float | None = None
    culminacion: str = ""       # cuando está más alta
    culminacion_elevacion: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _offset_iso(instante: datetime) -> str:
    """El desfase horario como lo quiere met.no: "+02:00", no "+0200"."""
    crudo = instante.strftime("%z") or "+0000"
    return f"{crudo[:3]}:{crudo[3:]}"


def _contacto_valido(user_agent: str) -> bool:
    minusculas = user_agent.lower()
    return bool(user_agent.strip()) and not any(
        marca in minusculas for marca in _CONTACTOS_DE_EJEMPLO
    )


def efemerides(lat: float, lon: float, instante: datetime) -> Efemerides:
    """Salida, puesta y culminación de la luna, de api.met.no.

    Args:
        lat, lon: dónde.
        instante: el momento LOCAL. De aquí salen la fecha y el desfase que se
            le piden a la API. Sin el parámetro `offset` la respuesta viene en
            UTC —comprobado contra la API real—, y entonces "la luna sale a las
            18:54" sería falso por dos horas en España.

    Raises:
        LunaError: no se pudo consultar, o el User-Agent no vale.
    """
    user_agent = Config.NOMINATIM_USER_AGENT
    if not _contacto_valido(user_agent):
        raise LunaError(
            "api.met.no exige un User-Agent con un contacto real y rechaza el de "
            "ejemplo con un 403 sin explicación. Pon un correo o una URL tuyos en "
            "NOMINATIM_USER_AGENT."
        )

    fecha = instante.strftime("%Y-%m-%d")
    clave = f"{storage.cache_key_for_coords('luna', lat, lon)}:{fecha}"
    cacheado = storage.cache_get(clave, _EFEMERIDES_CACHE_TTL)
    if cacheado is not None:
        return _parse_metno(cacheado)

    try:
        respuesta = requests.get(
            _MET_NO_URL,
            params={
                "lat": f"{lat:.4f}",
                "lon": f"{lon:.4f}",
                "date": fecha,
                "offset": _offset_iso(instante),
            },
            headers={"User-Agent": user_agent},
            timeout=Config.HTTP_TIMEOUT,
        )
        respuesta.raise_for_status()
        payload = respuesta.json()
    except requests.Timeout as exc:
        raise LunaError("El servicio de efemérides tardó demasiado.") from exc
    except requests.ConnectionError as exc:
        raise LunaError("Sin conexión con el servicio de efemérides.") from exc
    except requests.HTTPError as exc:
        codigo = exc.response.status_code if exc.response is not None else "?"
        # El 403 se nombra aparte porque su cuerpo es una página de nginx que no
        # explica nada, y la causa casi siempre es el User-Agent.
        if codigo == 403:
            raise LunaError(
                "api.met.no ha rechazado la petición (403). Suele ser el "
                "User-Agent: exige un contacto real."
            ) from exc
        raise LunaError(f"El servicio de efemérides devolvió un error ({codigo}).") from exc
    except ValueError as exc:
        raise LunaError("Respuesta ilegible del servicio de efemérides.") from exc

    if not isinstance(payload, dict) or "properties" not in payload:
        raise LunaError("Respuesta inesperada del servicio de efemérides.")

    storage.cache_set(clave, payload)
    return _parse_metno(payload)


def _parse_metno(payload: dict[str, Any]) -> Efemerides:
    """Convierte la respuesta de met.no en `Efemerides`.

    Los tres bloques pueden faltar y hay que tolerarlo: en latitudes altas hay
    días en los que la luna no sale ni se pone. No se inventa una hora ni se
    pone un cero: se deja vacío, que es lo que significa.
    """
    props = payload.get("properties") or {}

    def bloque(nombre: str) -> dict[str, Any]:
        valor = props.get(nombre)
        return valor if isinstance(valor, dict) else {}

    salida, puesta, alta = bloque("moonrise"), bloque("moonset"), bloque("high_moon")

    return Efemerides(
        salida=str(salida.get("time") or ""),
        salida_azimut=salida.get("azimuth"),
        puesta=str(puesta.get("time") or ""),
        puesta_azimut=puesta.get("azimuth"),
        culminacion=str(alta.get("time") or ""),
        culminacion_elevacion=alta.get("disc_centre_elevation"),
    )


# ---------------------------------------------------------------------------
# El veredicto, en Python
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Veredicto:
    """¿Da la luna luz suficiente para andar de noche sin frontal?"""

    hay_luz: bool
    motivo: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Por debajo de esto la luna no alumbra un camino. El umbral no es redondo por
# gusto: la iluminación de la luna no es lineal con la fracción visible —una
# luna al 50 % da del orden de un 8 % de la luz de la llena, porque el terminador
# proyecta sombras largas sobre el propio disco—, así que "media luna" no es
# "media luz". A partir de una gibosa clara sí se camina.
_ILUMINACION_UTIL = 70.0

# Códigos WMO con nubosidad suficiente para tapar la luna. Aquí no vale
# `is_wet()`: no llueve y aun así un cielo cubierto deja el campo a oscuras.
_CIELO_TAPADO = frozenset({3, 45, 48}) | frozenset(range(51, 100))


def veredicto_nocturno(
    fase_actual: Fase,
    codigo_meteo: int | None,
    hay_efemerides: bool = True,
) -> Veredicto:
    """Regla explícita, testeable y auditable. No se le pregunta al modelo.

    El orden es de lo más restrictivo a lo más permisivo, igual que en
    `water_sports()`, y se devuelve el primer criterio que descarta.

    Con el tiempo caído (`codigo_meteo=None`) NO se afirma que se pueda caminar:
    se dice que no se sabe si está despejado. Afirmarlo a ciegas es el fallo que
    este proyecto persigue —una respuesta segura sobre algo que no se ha
    comprobado— y aquí puede acabar con alguien de noche en un monte.
    """
    if fase_actual.iluminacion_pct < _ILUMINACION_UTIL:
        return Veredicto(
            False,
            f"{fase_actual.nombre.capitalize()} al {fase_actual.iluminacion_pct:.0f} %: "
            f"no alumbra un camino, lleva frontal.",
        )

    if codigo_meteo is None:
        return Veredicto(
            False,
            f"{fase_actual.nombre.capitalize()} al {fase_actual.iluminacion_pct:.0f} %, "
            f"pero no hay datos del cielo: no se sabe si estará tapada.",
        )

    if codigo_meteo in _CIELO_TAPADO:
        return Veredicto(
            False,
            f"{fase_actual.nombre.capitalize()} al {fase_actual.iluminacion_pct:.0f} %, "
            f"pero el cielo está cubierto: no llegará luz al suelo.",
        )

    detalle = "" if hay_efemerides else " (sin la hora de salida: no se sabe cuándo asoma)"
    return Veredicto(
        True,
        f"{fase_actual.nombre.capitalize()} al {fase_actual.iluminacion_pct:.0f} % "
        f"y cielo despejado: se puede caminar sin frontal{detalle}.",
    )


# ---------------------------------------------------------------------------
# Lo que se enseña
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Luna:
    """La luna de esta noche: lo calculado y, si se pudo, lo consultado.

    `fase` siempre está: es aritmética. `efemerides` puede ser None sin que eso
    invalide nada, que es justo el motivo de haberlo partido en dos.
    """

    fase: Fase
    veredicto: Veredicto
    efemerides: Efemerides | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fase": self.fase.to_dict(),
            "veredicto": self.veredicto.to_dict(),
            "efemerides": self.efemerides.to_dict() if self.efemerides else None,
        }
