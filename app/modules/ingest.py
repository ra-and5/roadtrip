"""Ingesta de telemetría del móvil: pasos, ubicación y batería.

Función de entrada: `ingest(payload) -> ResultadoIngesta`, que recibe datos ya
deserializados (un `dict`, no una petición de Flask) y devuelve un resultado
tipado. Lanza `IngestError` cuando el lote entero es inaceptable.

Que no aparezca `flask` en los imports es el objetivo, no una casualidad: toda
la validación —que es donde están las decisiones interesantes de este módulo—
se prueba llamando a funciones, sin levantar un servidor ni fabricar peticiones.

Las dos propiedades que definen este módulo:

**Idempotencia.** El móvil manda en cada envío las últimas horas de muestras,
no solo la última (decisión 23 de CLAUDE.md). Recibir la misma muestra varias
veces es el funcionamiento normal, no un error: se cuenta como duplicada y no
se guarda dos veces. Lo garantiza el `UNIQUE(fuente, medido_en)` del esquema,
no un `SELECT` previo, que con dos peticiones a la vez tendría una carrera.

**Una muestra mala no tumba el lote.** Si la de las 11:00 viene corrupta, las
otras cinco se guardan igual y la respuesta dice cuántas se descartaron y por
qué. Lo contrario haría que un solo dato raro del iPhone tirase seis horas de
datos buenos, justo en el escenario para el que se diseñó la ventana solapada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from werkzeug.security import check_password_hash

from app.config import Config
from app.modules import storage, timeparse


class IngestError(Exception):
    """El lote entero es inaceptable. El mensaje dice qué campo está mal.

    Se reserva para lo que invalida la petición completa (el cuerpo no es lo
    que decimos, vienen demasiadas muestras, la fuente es desconocida). Una
    muestra individual mal formada NO lanza esto: se descarta y se informa.
    """


# Fuentes admitidas. Es una lista blanca y no texto libre a propósito: una
# errata en el atajo del iPhone ("atajos-iphone " con un espacio final) crearía
# una serie paralela en la tabla sin dar ningún error, y como el UNIQUE es
# (fuente, medido_en), la deduplicación dejaría de funcionar en silencio. Es la
# clase de fallo de la decisión 11 —no da error, da datos equivocados— y aquí
# cuesta una línea convertirlo en un 400 que se ve al primer envío.
#
# `simulado` es la segunda fuente, y no es un descuido: son las muestras que
# genera `tools/simular_telemetria.py` para poder construir encima (dashboard,
# chatbot, perfil de actividad) sin esperar semanas a que se acumulen datos
# reales. Que tenga su propia fuente es la única razón por la que sembrarlas es
# seguro: `UNIQUE(fuente, medido_en)` las mantiene en una serie paralela que no
# choca con la del iPhone, se ven marcadas en `ver_telemetria.py`, y se borran
# enteras con `--limpiar`. Mezclarlas bajo `atajos-iphone` habría sido el fallo
# de la decisión 11 en su peor forma: una serie inventada que se lee como real.
FUENTES_VALIDAS = frozenset({"atajos-iphone", "simulado"})
FUENTE_POR_DEFECTO = "atajos-iphone"

# La ventana de fechas aceptable vive en `timeparse`, compartida con las notas
# de la Fase 3: el criterio de "qué fecha es creíble" es el mismo para un dato
# que recupera la ventana solapada y para una nota que recupera la cola
# offline. Se reexportan para no romper a quien las importara de aquí.
MAX_FUTURO = timeparse.MAX_FUTURO
MAX_PASADO = timeparse.MAX_PASADO

# Cuántos mensajes de descarte se devuelven como mucho. Un lote de 500 muestras
# malas no debe generar una respuesta de 500 líneas; el recuento sí es exacto.
MAX_ERRORES_REPORTADOS = 20

# Cuántas muestras se resumen en la respuesta. Pocas a propósito: quien lee esto
# es una persona mirando la alerta de un atajo en la pantalla del móvil, no un
# programa. En régimen normal el lote son ~6 muestras y caben casi todas.
MAX_DETALLE = 5

_PREFIJO_BEARER = "bearer"


# ---------------------------------------------------------------------------
# Autenticación: token propio, nunca la sesión
# ---------------------------------------------------------------------------

def token_valido(cabecera: str | None) -> bool:
    """¿La cabecera `Authorization` trae el token de ingesta correcto?

    Devuelve un booleano y no lanza: los tres motivos de fallo (sin cabecera,
    mal formada, token incorrecto) tienen que ser indistinguibles desde fuera.
    Si esta función distinguiera casos, tarde o temprano alguien los traduciría
    a mensajes distintos y el endpoint pasaría a contarle a un atacante si el
    token existe o si solo está mal escrito.

    Por qué token y no `auth.login_required`: este endpoint es público en
    internet y Atajos no inicia sesión ni guarda cookies. Y por qué NO se acepta
    además la cookie: dos caminos de autenticación hacia el mismo sitio es como
    se cuelan los fallos de "confused deputy" —un formulario en otra pestaña, un
    CSRF, un fetch desde la propia app— y aquí no aporta nada, porque el único
    cliente que va a llamar es el atajo.

    `check_password_hash` compara en tiempo constante. Que PBKDF2 tarde ~100 ms
    da igual a una petición por hora, y a cambio un token robado del iPhone no
    se puede reconstruir a partir del hash del `.env`.
    """
    if not Config.INGEST_TOKEN_HASH:
        # Sin token configurado, el endpoint está cerrado. Nunca "abierto por
        # no estar configurado": equivocarse hacia el lado seguro.
        return False
    if not cabecera:
        return False

    esquema, _, token = cabecera.partition(" ")
    # El esquema es insensible a mayúsculas por RFC 7235; el token no.
    if esquema.lower() != _PREFIJO_BEARER:
        return False
    token = token.strip()
    if not token:
        return False

    try:
        return check_password_hash(Config.INGEST_TOKEN_HASH, token)
    except ValueError:
        # Hash mal copiado en el .env (método desconocido, formato roto).
        # Se trata como "no autorizado", no como error del cliente: el problema
        # es nuestro y el diagnóstico lo dirá; el atacante no aprende nada.
        return False


# ---------------------------------------------------------------------------
# Tipos de salida
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Muestra:
    """Una muestra ya validada y normalizada, lista para guardar."""

    fuente: str
    medido_en: str                 # UTC canónico, segundos: 2026-07-27T08:00:00+00:00
    offset_original: str | None    # "+02:00" tal y como vino; None si vino en UTC
    pasos: int | None = None
    bateria: int | None = None
    lat: float | None = None
    lon: float | None = None

    def resumen(self) -> str:
        """Una línea con lo que trae esta muestra. Se devuelve al cliente.

        Solo aparecen los campos que llegaron con valor. Esa omisión ES la
        información: `guardadas: 1` a secas no distingue una muestra completa de
        una a la que se le perdió la ubicación por una clave mal escrita
        (`"lat:"` en vez de `"lat"` es JSON válido y se guarda como NULL sin
        que nadie proteste). Con el resumen, que falte `lat=` se ve al instante
        desde el móvil, sin abrir una consola en el servidor.
        """
        partes = [self.medido_en]
        if self.pasos is not None:
            partes.append(f"pasos={self.pasos}")
        if self.bateria is not None:
            partes.append(f"bat={self.bateria}%")
        if self.lat is not None and self.lon is not None:
            partes.append(f"lat={self.lat:.5f} lon={self.lon:.5f}")
        return " ".join(partes)

    def to_row(self, recibido_en: str) -> dict[str, Any]:
        """Fila lista para `storage.insert_telemetry`."""
        return {
            "fuente": self.fuente,
            "medido_en": self.medido_en,
            "offset_original": self.offset_original,
            "pasos": self.pasos,
            "bateria": self.bateria,
            "lat": self.lat,
            "lon": self.lon,
            "recibido_en": recibido_en,
        }


@dataclass
class ResultadoIngesta:
    """Qué ha pasado con el lote. Es lo que ve el móvil.

    Los tres recuentos suman siempre el número de muestras enviadas, y decir
    "duplicadas" en vez de callarlas es lo que permite mirar la respuesta del
    atajo y saber si la ventana solapada está haciendo su trabajo: en régimen
    normal la mayoría de cada envío son duplicadas, y eso es exactamente lo
    esperado.
    """

    guardadas: int = 0
    duplicadas: int = 0
    descartadas: int = 0
    errores: list[str] = field(default_factory=list)
    detalle: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardadas": self.guardadas,
            "duplicadas": self.duplicadas,
            "descartadas": self.descartadas,
            "errores": self.errores,
            "detalle": self.detalle,
        }


# ---------------------------------------------------------------------------
# Validación de campos
# ---------------------------------------------------------------------------

class _MuestraInvalida(Exception):
    """Una sola muestra no sirve. Interna: fuera solo se ve el recuento."""


def _es_numero(valor: Any) -> bool:
    """¿Es un número de verdad?

    `isinstance(True, int)` es `True` en Python, así que sin excluir `bool`
    un `"pasos": true` se guardaría como 1 pasos. Es poco probable y es
    exactamente el tipo de dato que luego nadie sabe de dónde salió.
    """
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _entero(valor: Any, campo: str) -> int:
    """Convierte a entero exacto, aceptando un float con parte decimal nula.

    Se aceptan `4213` y `4213.0`, se rechaza `4213.5`. El motivo no es
    laxitud: JSON no distingue enteros de reales, y varias acciones de Atajos
    devuelven los números de Salud como `4213.0`. Rechazarlos sería rechazar
    datos perfectamente buenos por una diferencia que solo existe en el
    serializador. `4213.5` sí se rechaza: eso ya no es un entero disfrazado.
    """
    if not _es_numero(valor):
        raise _MuestraInvalida(f"'{campo}' tiene que ser un número entero")
    if isinstance(valor, float) and not valor.is_integer():
        raise _MuestraInvalida(f"'{campo}' tiene que ser un entero, no {valor}")
    return int(valor)


def _parse_medido_en(valor: Any) -> tuple[str, str | None]:
    """Valida el instante y lo canoniza. Devuelve (UTC canónico, offset original).

    La aritmética vive en `timeparse` porque las notas de la Fase 3 necesitan
    exactamente la misma, y las fechas son justo donde un bug corregido en un
    sitio y no en el otro pasa desapercibido durante semanas. Aquí solo queda
    la traducción de la excepción: `timeparse` lanza `ValueError` porque no
    sabe quién lo usa, y este módulo la convierte en su descarte de muestra.
    """
    try:
        return timeparse.parse_instant(valor, "medido_en")
    except ValueError as exc:
        raise _MuestraInvalida(str(exc)) from None


def _parse_coordenadas(datos: dict[str, Any]) -> tuple[float | None, float | None]:
    """Valida lat/lon. Opcionales, pero o vienen las dos o no viene ninguna.

    Media coordenada no es medio dato, es un dato inservible: una latitud sola
    no ubica nada. Y si se aceptara, quedaría guardada como si significara algo.
    """
    lat, lon = datos.get("lat"), datos.get("lon")
    if lat is None and lon is None:
        return None, None
    if lat is None or lon is None:
        raise _MuestraInvalida("'lat' y 'lon' van juntas: no se puede enviar solo una")

    if not _es_numero(lat) or not _es_numero(lon):
        raise _MuestraInvalida("'lat' y 'lon' tienen que ser números")
    if not -90 <= lat <= 90:
        raise _MuestraInvalida(f"'lat' fuera del rango [-90, 90]: {lat}")
    if not -180 <= lon <= 180:
        raise _MuestraInvalida(f"'lon' fuera del rango [-180, 180]: {lon}")

    return float(lat), float(lon)


def _parse_muestra(datos: Any, fuente: str) -> Muestra:
    """Valida una muestra suelta. Lanza `_MuestraInvalida` con el campo culpable."""
    if not isinstance(datos, dict):
        raise _MuestraInvalida("no es un objeto JSON")

    medido_en, offset_original = _parse_medido_en(datos.get("medido_en"))

    pasos = None
    if datos.get("pasos") is not None:
        pasos = _entero(datos["pasos"], "pasos")
        if pasos < 0:
            raise _MuestraInvalida(f"'pasos' no puede ser negativo: {pasos}")

    bateria = None
    if datos.get("bateria") is not None:
        bateria = _entero(datos["bateria"], "bateria")
        if not 0 <= bateria <= 100:
            raise _MuestraInvalida(f"'bateria' fuera del rango [0, 100]: {bateria}")

    lat, lon = _parse_coordenadas(datos)

    # Una muestra que solo trae la hora no mide nada. Guardarla llenaría la
    # tabla de filas vacías que además ocupan el hueco de (fuente, medido_en):
    # si más tarde llegara la muestra buena de ese mismo instante, el UNIQUE la
    # rechazaría como duplicada. El dato bueno se perdería por culpa del vacío.
    if pasos is None and bateria is None and lat is None:
        raise _MuestraInvalida("no trae ningún dato: solo 'medido_en'")

    return Muestra(
        fuente=fuente,
        medido_en=medido_en,
        offset_original=offset_original,
        pasos=pasos,
        bateria=bateria,
        lat=lat,
        lon=lon,
    )


# ---------------------------------------------------------------------------
# Entrada del módulo
# ---------------------------------------------------------------------------

def _fuente_de(payload: dict[str, Any]) -> str:
    fuente = payload.get("fuente", FUENTE_POR_DEFECTO)
    if not isinstance(fuente, str):
        raise IngestError("'fuente' tiene que ser texto")
    fuente = fuente.strip()
    if fuente not in FUENTES_VALIDAS:
        # Se nombran las válidas: esto no es información sensible y el cliente
        # legítimo es un atajo que alguien está escribiendo a mano.
        raise IngestError(
            f"'fuente' desconocida: {fuente!r}. "
            f"Válidas: {', '.join(sorted(FUENTES_VALIDAS))}"
        )
    return fuente


def ingest(payload: Any, recibido_en: str | None = None) -> ResultadoIngesta:
    """Valida un lote de muestras y guarda las buenas.

    `payload` viene ya deserializado. Devuelve el recuento; lanza `IngestError`
    si el lote entero no sirve.

    `recibido_en` se inyecta, igual que `get_recommendations()` acepta un
    `provider`: en producción no se pasa y sale del reloj, que es el dato
    correcto. Lo pasa `tools/simular_telemetria.py`, y no por comodidad: siembra
    días pasados, así que con el reloj real cada muestra de hace cinco días
    quedaría con un `retraso` de cinco días. La columna que esta fase existe
    para mirar es justo esa, y una serie entera con retrasos falsos de días la
    dejaría inservible para lo único que sirve -- distinguir un envío normal de
    una recuperación tras quedarse sin cobertura.

    El orden de las comprobaciones importa y va de barato a caro: primero la
    forma del cuerpo, luego el número de muestras, y solo después se valida
    muestra a muestra y se toca la base de datos. Un array de un millón de
    elementos muere en la segunda línea, sin haber recorrido nada. (Lo
    realmente barato pasa antes de llegar aquí: `MAX_CONTENT_LENGTH` corta el
    cuerpo enorme **antes** de parsear el JSON, que es donde se va la CPU.)
    """
    if not isinstance(payload, dict):
        raise IngestError(
            "el cuerpo tiene que ser un objeto JSON con la forma "
            '{"fuente": ..., "muestras": [...]}'
        )

    muestras_crudas = payload.get("muestras")
    if not isinstance(muestras_crudas, list):
        raise IngestError("falta 'muestras' o no es una lista")

    # Un lote vacío se rechaza en vez de responder "0 guardadas". Un atajo que
    # manda cero muestras no está funcionando: no ha podido leer nada de Salud.
    # Devolverle 200 lo dejaría fallando en silencio durante días, que es
    # exactamente el fallo que esta fase existe para detectar.
    if not muestras_crudas:
        raise IngestError("'muestras' está vacío: no hay nada que guardar")

    if len(muestras_crudas) > Config.INGEST_MAX_SAMPLES:
        raise IngestError(
            f"demasiadas muestras: {len(muestras_crudas)} "
            f"(el máximo es {Config.INGEST_MAX_SAMPLES})"
        )

    fuente = _fuente_de(payload)

    resultado = ResultadoIngesta()
    validas: list[Muestra] = []
    for indice, cruda in enumerate(muestras_crudas):
        try:
            validas.append(_parse_muestra(cruda, fuente))
        except _MuestraInvalida as exc:
            resultado.descartadas += 1
            if len(resultado.errores) < MAX_ERRORES_REPORTADOS:
                resultado.errores.append(f"muestra {indice}: {exc}")

    if resultado.descartadas > len(resultado.errores):
        resultado.errores.append(
            f"...y {resultado.descartadas - len(resultado.errores)} descartes más"
        )

    # El resumen se construye sobre las muestras VÁLIDAS, no sobre las
    # guardadas: una muestra duplicada también hay que poder verla, porque en
    # régimen normal la mayoría del lote lo son y el cliente necesita
    # comprobar que lo que reenvía es lo que cree.
    resultado.detalle = [m.resumen() for m in validas[:MAX_DETALLE]]
    if len(validas) > MAX_DETALLE:
        resultado.detalle.append(f"...y {len(validas) - MAX_DETALLE} muestras más")

    if validas:
        # Un solo instante de recepción para todo el lote: además de ser el
        # dato correcto (llegaron en la misma petición), agrupa las muestras
        # por envío, que es como se depura "esto llegó todo junto tras 5 h sin
        # cobertura".
        instante = recibido_en or datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(timespec="seconds")
        guardadas, duplicadas = storage.insert_telemetry(
            [m.to_row(instante) for m in validas]
        )
        resultado.guardadas = guardadas
        resultado.duplicadas = duplicadas

    return resultado
