"""Notas geolocalizadas: validación, creación idempotente y progreso del mapa.

Función de entrada: `create_note(payload) -> ResultadoCreacion`, que recibe
datos ya deserializados (un `dict`, no una petición de Flask) y devuelve un
resultado tipado. Lanza `NoteError` cuando la nota no sirve.

Que no aparezca `flask` en los imports es el objetivo, no una casualidad: la
validación —que es donde están las decisiones de este módulo— se prueba
llamando a funciones, sin levantar un servidor.

Las dos propiedades que definen este módulo:

**Idempotencia.** El móvil genera el `client_id` antes del primer intento de
envío y lo reutiliza en cada reintento, así que la misma nota llega varias
veces por diseño cuando la cobertura va y viene. La garantía vive en el
`UNIQUE(client_id)` del esquema, no en un `SELECT` previo.

**Una nota escrita no se pierde nunca.** Es la asimetría con la telemetría de
la Fase 2d: los pasos de Salud se pueden volver a consultar hacia atrás, así
que allí una ventana solapada los recupera sin guardar estado; una nota escrita
a mano en un mirador **no existe en ningún otro sitio**. Por eso aquí sí hay
cola en el navegador (decisión 26), y por eso este módulo nunca contesta "vale"
sin haber escrito.

**Fotos: no, todavía.** El MVP es solo texto geolocalizado. `photo_path` sigue
en el esquema y se guarda como NULL, por la misma razón por la que `client_id`
existía desde la Fase 1: una columna vacía es gratis hoy y cara con datos
dentro. El razonamiento sobre cómo viajarán (multipart, no base64) está en el
registro de decisiones para no volver a discutirlo.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from app.modules import storage, timeparse


class NoteError(Exception):
    """La nota no se puede aceptar. El mensaje dice qué campo está mal.

    A diferencia de la ingesta —donde una muestra mala se descarta y el lote
    sigue— aquí una nota mala es un 400: se envían de una en una, y lo que hay
    al otro lado es una cola que necesita saber si esta nota concreta se puede
    dar por resuelta o hay que seguir reintentándola.
    """


# El `client_id` se exige como UUID canónico en minúsculas, que es justo lo que
# devuelve `crypto.randomUUID()`. No es tiquismiquis: este identificador es la
# clave de la idempotencia y será el nombre del archivo de la foto cuando las
# haya, así que restringirlo a hexadecimal y guiones hace que ese nombre sea
# seguro POR CONSTRUCCIÓN en vez de por saneado. Se descarta pasar cualquier
# cadena por `secure_filename()`: sanear puede colapsar dos ids distintos en el
# mismo nombre y una nota se comería el archivo de otra sin dar ningún error.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Cuánto texto cabe en una nota. El límite no está para ahorrar disco (una nota
# larga son 2 KB) sino para que el error sea legible: sin él, un fallo del
# cliente que mandara medio megabyte moriría en el `MAX_CONTENT_LENGTH` global
# con un 413 que no dice qué campo sobra.
MAX_TEXTO = 2000
MAX_PLACE_NAME = 200

# Precisión con la que dos notas cuentan como "el mismo sitio" en el progreso
# del mapa: 2 decimales, ~1,1 km. Deliberadamente MÁS GRUESA que la de la caché
# de APIs (`CACHE_COORD_PRECISION`, 3 decimales, ~110 m), y por un motivo
# distinto en cada caso. Allí la pregunta es "¿puedo reutilizar la respuesta de
# Nominatim?", y a 110 m la respuesta sigue siendo la misma calle. Aquí la
# pregunta es "¿he estado ya en este sitio?", y para eso un sitio es un pueblo o
# una playa, no un mirador 110 m más allá: con la precisión fina, tres notas
# paseando por el mismo pueblo contarían como tres lugares visitados y el
# contador del mapa premiaría caminar en vez de viajar.
PRECISION_LUGAR = 2


# ---------------------------------------------------------------------------
# Tipos de salida
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Note:
    """Una nota ya validada y normalizada, lista para guardar."""

    client_id: str
    text: str
    lat: float
    lon: float
    created_at: str                # UTC canónico a segundos
    offset_original: str | None    # "+02:00" tal y como vino; None si vino en UTC
    place_name: str | None = None
    region: str | None = None

    def to_row(self, received_at: str) -> dict[str, Any]:
        """Fila lista para `storage.insert_note`."""
        return {
            "client_id": self.client_id,
            "text": self.text,
            # Sin fotos en el MVP. La columna se rellena a NULL a propósito en
            # vez de desaparecer: ver la cabecera del módulo.
            "photo_path": None,
            "lat": self.lat,
            "lon": self.lon,
            "place_name": self.place_name,
            "region": self.region,
            "created_at": self.created_at,
            "offset_original": self.offset_original,
            "received_at": received_at,
        }


@dataclass(frozen=True)
class ResultadoCreacion:
    """Qué ha pasado con la nota. Es lo que ve la cola del navegador.

    `creada=False` no es un error: significa que esta nota ya estaba, casi
    siempre porque es un reintento que en realidad había llegado bien. La cola
    la borra igual, y por eso hay que distinguirlo de un fallo.
    """

    id: int
    client_id: str
    creada: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "estado": "creada" if self.creada else "duplicada",
            "id": self.id,
            "client_id": self.client_id,
        }


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------

def _es_numero(valor: Any) -> bool:
    """¿Es un número de verdad?

    `isinstance(True, int)` es `True` en Python, así que sin excluir `bool` un
    `"lat": true` se guardaría como latitud 1.
    """
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _texto_opcional(valor: Any, campo: str, maximo: int) -> str | None:
    """Valida un campo de texto opcional. Devuelve None si no vino o vino vacío."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise NoteError(f"'{campo}' tiene que ser texto")
    valor = valor.strip()
    if not valor:
        return None
    if len(valor) > maximo:
        raise NoteError(f"'{campo}' es demasiado largo ({len(valor)} > {maximo})")
    return valor


def _parse_coordenadas(payload: dict[str, Any]) -> tuple[float, float]:
    """Valida lat/lon. Aquí son OBLIGATORIAS, al revés que en la telemetría.

    Una nota sin coordenadas no es una nota geolocalizada: es una nota suelta
    que no puede aparecer en el mapa, que es lo único que esta fase construye.
    Aceptarla dejaría filas invisibles en la base de datos y el usuario creería
    haber marcado un sitio que no está en ninguna parte.
    """
    lat, lon = payload.get("lat"), payload.get("lon")
    if lat is None or lon is None:
        raise NoteError("faltan 'lat' y/o 'lon': una nota sin coordenadas no va al mapa")
    if not _es_numero(lat) or not _es_numero(lon):
        raise NoteError("'lat' y 'lon' tienen que ser números")
    if not -90 <= lat <= 90:
        raise NoteError(f"'lat' fuera del rango [-90, 90]: {lat}")
    if not -180 <= lon <= 180:
        raise NoteError(f"'lon' fuera del rango [-180, 180]: {lon}")
    return float(lat), float(lon)


def parse_note(payload: Any) -> Note:
    """Valida una nota cruda y la normaliza. Lanza `NoteError` con el campo culpable.

    Está separada de `create_note` para poder probar cada regla sin tocar la
    base de datos, que es lo que hace que la suite corra en cualquier sitio.
    """
    if not isinstance(payload, dict):
        raise NoteError("el cuerpo tiene que ser un objeto JSON")

    client_id = payload.get("client_id")
    if not isinstance(client_id, str) or not _UUID.match(client_id.strip()):
        raise NoteError(
            "'client_id' tiene que ser un UUID en minúsculas "
            "(el que genera crypto.randomUUID() en el navegador)"
        )
    client_id = client_id.strip()

    texto = payload.get("text")
    if texto is not None and not isinstance(texto, str):
        raise NoteError("'text' tiene que ser texto")
    texto = (texto or "").strip()
    # Sin fotos en el MVP, una nota sin texto no guarda absolutamente nada: solo
    # una chincheta muda en un sitio por el que pasaste. Cuando haya fotos, la
    # regla pasará a ser "texto o foto, al menos uno".
    if not texto:
        raise NoteError("la nota está vacía: escribe algo")
    if len(texto) > MAX_TEXTO:
        raise NoteError(f"'text' es demasiado largo ({len(texto)} > {MAX_TEXTO})")

    lat, lon = _parse_coordenadas(payload)

    try:
        created_at, offset_original = timeparse.parse_instant(
            payload.get("created_at"), "created_at"
        )
    except ValueError as exc:
        raise NoteError(str(exc)) from None

    return Note(
        client_id=client_id,
        text=texto,
        lat=lat,
        lon=lon,
        created_at=created_at,
        offset_original=offset_original,
        place_name=_texto_opcional(payload.get("place_name"), "place_name", MAX_PLACE_NAME),
        region=_texto_opcional(payload.get("region"), "region", MAX_PLACE_NAME),
    )


# ---------------------------------------------------------------------------
# Entrada del módulo
# ---------------------------------------------------------------------------

def create_note(payload: Any) -> ResultadoCreacion:
    """Valida una nota y la guarda. Idempotente por `client_id`.

    El servidor pone `received_at` y nunca lo acepta del cliente: es SU medida
    de cuándo se enteró, y un cliente que la enviara podría hacer que el
    retraso de la cola offline pareciera cero justo cuando más interesa saber
    que no lo fue.
    """
    nota = parse_note(payload)
    received_at = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")
    )
    id_, creada = storage.insert_note(nota.to_row(received_at))
    return ResultadoCreacion(id=id_, client_id=nota.client_id, creada=creada)


def _fecha_local(nota: dict[str, Any]) -> date | None:
    """El día LOCAL en que se escribió la nota.

    Agrupar por el día local y no por el de UTC importa de verdad: una nota
    escrita a las 00:30 en España es del día siguiente en UTC, y contarla ahí
    desplazaría un día entero del viaje en el mapa.

    Trabaja sobre `created_at_local`, que ya calculó `to_public()`. Es a
    propósito: hay UNA representación de una nota fuera de este módulo, y todo
    lo que la consume —el mapa, el progreso, `ver_notas.py`— mira el mismo
    campo. Con dos formas conviviendo, la que se olvidara del desfase contaría
    los días con una hora de error y nadie lo vería.
    """
    try:
        return datetime.fromisoformat(nota["created_at_local"]).date()
    except (ValueError, KeyError, TypeError):
        return None


def _clave_lugar(lat: float, lon: float) -> str:
    """Clave con la que dos notas cuentan como el mismo sitio. Ver PRECISION_LUGAR."""
    p = PRECISION_LUGAR
    return f"{round(lat, p):.{p}f},{round(lon, p):.{p}f}"


def to_public(fila: dict[str, Any]) -> dict[str, Any]:
    """La forma de una nota tal y como la ve el navegador.

    Se añade `created_at_local` ya calculado en el servidor en vez de dejar que
    el navegador lo deduzca: el móvil acertaría (está en el huso del viaje),
    pero un portátil desde casa en invierno enseñaría las notas de agosto una
    hora corridas. La hora de una nota es la del sitio donde se escribió.
    """
    local = None
    try:
        local = timeparse.to_local(fila["created_at"], fila.get("offset_original")).isoformat()
    except (ValueError, KeyError, TypeError):
        local = fila.get("created_at")

    return {
        "id": fila["id"],
        "client_id": fila["client_id"],
        "text": fila["text"],
        "lat": fila["lat"],
        "lon": fila["lon"],
        "place_name": fila.get("place_name"),
        "region": fila.get("region"),
        "created_at": fila["created_at"],
        "created_at_local": local,
        "received_at": fila.get("received_at"),
        # Siempre None en el MVP. Va en la respuesta para que el día que haya
        # fotos el frontend no tenga que cambiar de forma.
        "photo_url": None,
    }


def get_notes(limit: int = 1000) -> list[dict[str, Any]]:
    """Las notas para el mapa, de la más reciente a la más antigua."""
    return [to_public(f) for f in storage.list_notes(limit)]


def solo_del_anio(notas: list[dict[str, Any]], year: int | None) -> list[dict[str, Any]]:
    """Filtra por año LOCAL. `year=None` no filtra nada.

    Se filtra en Python y no en el SQL porque hay que comparar con el año
    local, no con los cuatro primeros caracteres del instante en UTC: una nota
    del 1 de enero a las 00:30 en España es del año anterior en UTC, y el
    filtro la escondería justo del año al que pertenece. Con un viaje de un mes
    son cientos de filas: no hay nada que optimizar aquí, y sí una forma barata
    de equivocarse que se evita.
    """
    if year is None:
        return notas
    return [n for n in notas if (d := _fecha_local(n)) and d.year == year]


# ---------------------------------------------------------------------------
# Progreso del mapa
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Lugar:
    """Un sitio del mapa y cuántas veces se ha estado en él."""

    clave: str
    etiqueta: str
    lat: float
    lon: float
    visitas: int
    dias: int
    ultima: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "clave": self.clave,
            "etiqueta": self.etiqueta,
            "lat": self.lat,
            "lon": self.lon,
            "visitas": self.visitas,
            "dias": self.dias,
            "ultima": self.ultima,
        }


def _racha_maxima(dias: Iterable[date]) -> int:
    """El mayor número de días seguidos con al menos una nota.

    Es la métrica que premia lo que de verdad cuesta —salir todos los días— y
    no lo que es gratis: escribir diez notas sentado en el mismo bar.
    """
    ordenados = sorted(set(dias))
    if not ordenados:
        return 0
    mejor = actual = 1
    for anterior, siguiente in zip(ordenados, ordenados[1:]):
        actual = actual + 1 if siguiente - anterior == timedelta(days=1) else 1
        mejor = max(mejor, actual)
    return mejor


def _lugares(notas: list[dict[str, Any]]) -> list[Lugar]:
    """Agrupa las notas en sitios. Ver `PRECISION_LUGAR` para qué es un sitio."""
    grupos: dict[str, list[dict[str, Any]]] = {}
    for nota in notas:
        grupos.setdefault(_clave_lugar(nota["lat"], nota["lon"]), []).append(nota)

    lugares: list[Lugar] = []
    for clave, notas in grupos.items():
        # La etiqueta del sitio es el nombre más repetido entre sus notas, no el
        # de la primera: Nominatim puede devolver el barrio en una y el
        # municipio en otra, y quedarse con la primera haría que el nombre del
        # sitio dependiera del orden en que se escribieron las notas.
        nombres = Counter(n["place_name"] for n in notas if n.get("place_name"))
        etiqueta = nombres.most_common(1)[0][0] if nombres else clave
        dias = {d for n in notas if (d := _fecha_local(n))}
        lugares.append(
            Lugar(
                clave=clave,
                etiqueta=etiqueta,
                lat=round(sum(n["lat"] for n in notas) / len(notas), 5),
                lon=round(sum(n["lon"] for n in notas) / len(notas), 5),
                visitas=len(notas),
                # Las visitas son notas; los días son días. Cinco notas en una
                # tarde no son cinco visitas a un sitio, y separarlo es lo que
                # distingue "aquí vuelvo siempre" de "aquí escribí mucho".
                dias=len(dias),
                ultima=max((n["created_at"] for n in notas), default=None),
            )
        )

    lugares.sort(key=lambda l: (-l.dias, -l.visitas, l.etiqueta))
    return lugares


def progreso(notas: list[dict[str, Any]]) -> dict[str, Any]:
    """El resumen que convierte el mapa en algo que se va completando.

    Recibe notas ya en su forma pública (`to_public`). Función pura: no toca la
    base de datos, así que se prueba con listas escritas a mano y no hace falta
    inventar un viaje entero para comprobar que la racha cuenta bien.

    Todo sale de las NOTAS y de nada más. La telemetría del iPhone está
    aparcada a la espera de demostrar que llega sin huecos, y construir el
    progreso sobre una fuente que aún no es fiable es trabajo que habría que
    tirar (ver *Estado actual* de CLAUDE.md).
    """
    if not notas:
        return {
            "total": 0,
            "lugares": 0,
            "regiones": [],
            "dias": 0,
            "racha_maxima": 0,
            "primera": None,
            "ultima": None,
            "por_anio": {},
            "mas_visitados": [],
        }

    dias = [d for n in notas if (d := _fecha_local(n))]
    lugares = _lugares(notas)
    regiones = sorted({r for n in notas if (r := n.get("region"))})

    por_anio: dict[str, dict[str, Any]] = {}
    for nota in notas:
        dia = _fecha_local(nota)
        if dia is None:
            continue
        anio = por_anio.setdefault(
            str(dia.year), {"notas": 0, "_dias": set(), "_lugares": set(), "_regiones": set()}
        )
        anio["notas"] += 1
        anio["_dias"].add(dia)
        anio["_lugares"].add(_clave_lugar(nota["lat"], nota["lon"]))
        if nota.get("region"):
            anio["_regiones"].add(nota["region"])

    # Los conjuntos se colapsan a recuentos al final: sirven para no contar dos
    # veces mientras se acumula, pero no son serializables a JSON.
    resumen_anios = {
        anio: {
            "notas": datos["notas"],
            "dias": len(datos["_dias"]),
            "lugares": len(datos["_lugares"]),
            "regiones": sorted(datos["_regiones"]),
            "racha_maxima": _racha_maxima(datos["_dias"]),
        }
        for anio, datos in sorted(por_anio.items())
    }

    return {
        "total": len(notas),
        "lugares": len(lugares),
        "regiones": regiones,
        "dias": len(set(dias)),
        "racha_maxima": _racha_maxima(dias),
        "primera": min(n["created_at"] for n in notas),
        "ultima": max(n["created_at"] for n in notas),
        "por_anio": resumen_anios,
        # Solo los sitios a los que se ha vuelto: una lista donde todo aparece
        # con "1 visita" no dice nada. Estos son "los sitios a los que voy
        # siempre", que es la pregunta que responde.
        "mas_visitados": [l.to_dict() for l in lugares if l.dias > 1][:10],
    }
