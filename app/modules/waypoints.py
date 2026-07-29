"""Puntos del viaje sacados de los metadatos de las fotos.

Función de entrada: `import_waypoints(payload) -> ResultadoImportacion`, que
recibe datos ya deserializados y devuelve un resultado tipado. Lanza
`WaypointError` cuando el lote entero es inaceptable.

Las dos propiedades, heredadas de la ingesta de la Fase 2d porque el problema
es el mismo:

**Idempotencia.** Volcar las fotos del móvil e importar la carpeta entera es lo
normal, y se hace cada dos por tres. Importarla diez veces tiene que dejar el
viaje igual que importarla una. Lo garantiza el `UNIQUE(fuente, archivo)` del
esquema.

**Un punto malo no tumba el lote.** Una foto con el EXIF corrupto entre mil
buenas se descarta, se cuenta y se informa. Lo contrario haría que una sola
foto rara tirase la importación de un viaje entero.

Aquí no se sube ninguna foto: solo cuándo y dónde se hizo. Ver la cabecera de
`photo_meta` para el porqué.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.modules import storage

# Hoy solo hay una. Lista blanca y no texto libre por lo mismo que en la
# ingesta: una errata ("foto" en vez de "fotos") crearía una serie paralela sin
# dar ningún error, y como el UNIQUE es (fuente, archivo), la deduplicación
# dejaría de funcionar en silencio.
FUENTES_VALIDAS = frozenset({"fotos"})
FUENTE_POR_DEFECTO = "fotos"

MAX_PUNTOS = 5000
MAX_ERRORES_REPORTADOS = 20
MAX_ARCHIVO = 255


class WaypointError(Exception):
    """El lote entero es inaceptable. El mensaje dice qué está mal."""


class _PuntoInvalido(Exception):
    """Un solo punto no sirve. Interna: fuera solo se ve el recuento."""


@dataclass(frozen=True)
class Waypoint:
    """Un punto ya validado, listo para guardar."""

    fuente: str
    archivo: str
    capturado_en: str | None
    offset_original: str | None
    lat: float | None
    lon: float | None
    altitud: float | None
    camara: str | None

    def to_row(self, importado_en: str) -> dict[str, Any]:
        return {
            "fuente": self.fuente,
            "archivo": self.archivo,
            "capturado_en": self.capturado_en,
            "offset_original": self.offset_original,
            "lat": self.lat,
            "lon": self.lon,
            "altitud": self.altitud,
            "camara": self.camara,
            "importado_en": importado_en,
        }


@dataclass
class ResultadoImportacion:
    guardados: int = 0
    duplicados: int = 0
    descartados: int = 0
    eliminados: int = 0
    errores: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardados": self.guardados,
            "duplicados": self.duplicados,
            "descartados": self.descartados,
            "eliminados": self.eliminados,
            "errores": self.errores,
        }


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _fecha_local(valor: Any) -> tuple[str | None, str | None]:
    """Valida la hora de la cámara. Devuelve (hora local sin huso, desfase).

    La hora **no** se canoniza a UTC, y eso es la decisión: el EXIF guarda la
    que marcaba el reloj de la cámara. Convertirla exigiría saber la zona, y
    ponerle una "porque el viaje es por España" sería inventarse el instante:
    la misma foto hecha en Canarias quedaría una hora corrida sin que nadie se
    entere.

    Pero si la fecha **viene** con huso, tampoco se rechaza: se separa. La
    parte local va a `capturado_en` y el desfase a `offset_original`, que es su
    sitio. Aceptarlo importa porque el iPhone lo escribe así: la acción *Formato
    de fecha → ISO 8601* de Atajos devuelve `...T14:23:37+02:00`, y rechazarlo
    obligaría a montar un *Reemplazar texto* en el móvil para tirar información
    que aquí sí se sabe guardar.

    Lo que **no** se hace nunca es recortar la cadena antes de mirarla. Esa fue
    la primera versión y era un fallo silencioso: se comía el "+02:00" sin
    verlo, y la fecha quedaba guardada como local estando desplazada dos horas.
    Separar es lo contrario de descartar.

    No se acota la fecha hacia el pasado, al revés que en la telemetría y las
    notas: aquí importar fotos de hace tres años es justo lo que se quiere para
    comparar viajes de distintos años. Hacia el futuro sí, porque una foto
    fechada en 2040 es un reloj mal puesto y desplazaría el final del viaje.
    """
    if valor is None:
        return None, None
    if not isinstance(valor, str) or len(valor) < 19:
        raise _PuntoInvalido("'capturado_en' tiene que ser 'AAAA-MM-DDTHH:MM:SS'")

    texto = valor.strip()
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        raise _PuntoInvalido(f"'capturado_en' no es una fecha válida: {valor!r}") from None

    desfase = None
    if instante.tzinfo is not None:
        offset = instante.strftime("%z")[:5]
        if offset and offset != "+0000":
            desfase = f"{offset[:3]}:{offset[3:]}"
        # Se quita la zona conservando la hora que se lee: son las 14:23 de la
        # cámara, no las 12:23 de UTC.
        instante = instante.replace(tzinfo=None)

    instante = instante.replace(microsecond=0)

    # Comparación ingenua contra la hora local del servidor, con un día de
    # margen: no se sabe el huso de la foto, así que afinar más sería fingir
    # una precisión que no hay. Basta para cazar un 2040.
    if (instante - datetime.now()).days > 1:
        raise _PuntoInvalido(f"'capturado_en' está en el futuro: {valor!r}")

    return instante.isoformat(timespec="seconds"), desfase


def _parse_punto(datos: Any, fuente: str) -> Waypoint:
    if not isinstance(datos, dict):
        raise _PuntoInvalido("no es un objeto JSON")

    archivo = datos.get("archivo")
    if not isinstance(archivo, str) or not archivo.strip():
        raise _PuntoInvalido("falta 'archivo'")
    archivo = archivo.strip()
    if len(archivo) > MAX_ARCHIVO:
        raise _PuntoInvalido(f"'archivo' demasiado largo ({len(archivo)})")
    if "/" in archivo or "\\" in archivo or "\x00" in archivo:
        # No se usa para abrir nada, pero se guarda y se muestra. Una ruta
        # entera aquí sería además una fuga de cómo está organizado el disco de
        # quien importa.
        raise _PuntoInvalido("'archivo' tiene que ser un nombre, no una ruta")

    lat, lon = datos.get("lat"), datos.get("lon")
    if lat is None or lon is None:
        # Una foto sin GPS es válida: ordena el relato aunque no vaya al mapa.
        lat = lon = None
    else:
        if not _es_numero(lat) or not _es_numero(lon):
            raise _PuntoInvalido("'lat' y 'lon' tienen que ser números")
        if not -90 <= lat <= 90:
            raise _PuntoInvalido(f"'lat' fuera del rango [-90, 90]: {lat}")
        if not -180 <= lon <= 180:
            raise _PuntoInvalido(f"'lon' fuera del rango [-180, 180]: {lon}")
        lat, lon = float(lat), float(lon)

    altitud = datos.get("altitud")
    if altitud is not None:
        if not _es_numero(altitud):
            raise _PuntoInvalido("'altitud' tiene que ser un número")
        # El Everest son 8849 m y la fosa del Mar Muerto -430. Fuera de ahí es
        # un EXIF corrupto, y una altitud absurda estropearía cualquier gráfica.
        if not -500 <= altitud <= 9000:
            raise _PuntoInvalido(f"'altitud' fuera de lo posible: {altitud}")
        altitud = round(float(altitud), 1)

    capturado_en, desfase_en_la_fecha = _fecha_local(datos.get("capturado_en"))

    # Un punto sin fecha y sin coordenadas no aporta nada al viaje: sería una
    # fila que solo dice que existe un archivo.
    if capturado_en is None and lat is None:
        raise _PuntoInvalido("no trae ni fecha ni coordenadas")

    camara = datos.get("camara")
    if camara is not None and not isinstance(camara, str):
        raise _PuntoInvalido("'camara' tiene que ser texto")

    offset = datos.get("offset_original")
    if offset is not None and not isinstance(offset, str):
        raise _PuntoInvalido("'offset_original' tiene que ser texto")
    # El campo explícito manda sobre el que venía pegado a la fecha: quien se
    # molesta en mandarlo aparte lo ha sacado del EXIF, que es la fuente buena.
    offset = (offset or "").strip() or desfase_en_la_fecha

    return Waypoint(
        fuente=fuente,
        archivo=archivo,
        capturado_en=capturado_en,
        offset_original=(offset or None),
        lat=lat,
        lon=lon,
        altitud=altitud,
        camara=(camara.strip() or None) if isinstance(camara, str) else None,
    )


def import_waypoints(payload: Any) -> ResultadoImportacion:
    """Valida un lote de puntos y guarda los buenos.

    Con `"completo": true` en el cuerpo, además **borra los que ya no están**.

    Hace falta porque el álbum no solo crece: quitar una foto de `Viaje` es
    decir "esta no cuenta", y hasta ahora el mapa se quedaba con ella para
    siempre. La curación es el dato, y una curación que solo suma no es una
    curación. Con la bandera puesta, un envío es el estado del álbum y no una
    lista de altas.

    Y va detrás de una bandera explícita en vez de ser el comportamiento normal,
    porque el precio de equivocarse no es simétrico: una foto de más se ve y se
    quita, y una foto de menos **es historia borrada**. Un cliente que mande un
    lote parcial —una prueba a mano, un atajo a medio montar, otro camino de
    importación— borraría el viaje sin querer y sin dar ningún error. Quien
    manda el álbum entero lo sabe y lo dice; los demás no tienen que saber nada.

    Las dos protecciones que lo hacen seguro, más allá de la bandera:

    - **el borrado se acota a la `fuente` del envío.** El atajo del álbum no
      puede tocar lo que entró por `tools/importar_fotos.py`.
    - **una lista vacía no llega hasta aquí**: se rechaza antes con un 400. Es
      el caso que más miedo da —un atajo roto que manda cero puntos y se lleva
      por delante el viaje entero— y por eso la protección no está aquí, sino
      arriba, donde no depende de recordar nada.

    Ojo con el límite del atajo: manda como mucho `MAX_PUNTOS` fotos, y el de
    iPhone está puesto en 300. Si el álbum crece por encima de eso, un envío
    deja de ser el álbum entero y el borrado se llevaría lo que no cupo. Por eso
    `eliminados` sale en la respuesta y en el log: un número raro ahí es la
    señal de que el límite se ha quedado corto.
    """
    if not isinstance(payload, dict):
        raise WaypointError(
            'el cuerpo tiene que ser {"fuente": ..., "puntos": [...]}'
        )

    crudos = payload.get("puntos")
    if not isinstance(crudos, list):
        raise WaypointError("falta 'puntos' o no es una lista")
    if not crudos:
        raise WaypointError("'puntos' está vacío: no hay nada que importar")
    if len(crudos) > MAX_PUNTOS:
        raise WaypointError(
            f"demasiados puntos: {len(crudos)} (el máximo es {MAX_PUNTOS})"
        )

    fuente = payload.get("fuente", FUENTE_POR_DEFECTO)
    if not isinstance(fuente, str) or fuente.strip() not in FUENTES_VALIDAS:
        raise WaypointError(
            f"'fuente' desconocida: {fuente!r}. "
            f"Válidas: {', '.join(sorted(FUENTES_VALIDAS))}"
        )
    fuente = fuente.strip()

    resultado = ResultadoImportacion()
    validos: list[Waypoint] = []
    for indice, crudo in enumerate(crudos):
        try:
            validos.append(_parse_punto(crudo, fuente))
        except _PuntoInvalido as exc:
            resultado.descartados += 1
            if len(resultado.errores) < MAX_ERRORES_REPORTADOS:
                resultado.errores.append(f"punto {indice}: {exc}")

    if resultado.descartados > len(resultado.errores):
        resultado.errores.append(
            f"...y {resultado.descartados - len(resultado.errores)} descartes más"
        )

    if validos:
        importado_en = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")
        )
        guardados, duplicados = storage.insert_waypoints(
            [w.to_row(importado_en) for w in validos]
        )
        resultado.guardados = guardados
        resultado.duplicados = duplicados

        # El borrado va DESPUÉS de insertar, y ese orden es la garantía que
        # importa: si el insert falla, no se ha borrado nada. Al revés, un fallo
        # a mitad dejaría el viaje vacío y sin nada que lo repueble.
        if payload.get("completo") is True:
            resultado.eliminados = storage.delete_waypoints_ausentes(
                fuente, [w.archivo for w in validos]
            )

    return resultado
