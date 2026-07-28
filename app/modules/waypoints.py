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
    errores: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardados": self.guardados,
            "duplicados": self.duplicados,
            "descartados": self.descartados,
            "errores": self.errores,
        }


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _fecha_local(valor: Any) -> str | None:
    """Valida la hora de la cámara: local, sin huso, y creíble.

    No se canoniza a UTC ni se le pone zona, y eso es la decisión: el EXIF no
    lleva huso en esta etiqueta. Ponerle uno "porque el viaje es por España"
    sería inventarse el instante, y la misma foto hecha en Canarias quedaría
    una hora corrida sin que nadie se entere.

    No se acota la fecha hacia el pasado, al revés que en la telemetría y las
    notas: aquí importar fotos de hace tres años es justo lo que se quiere para
    comparar viajes de distintos años. Hacia el futuro sí, porque una foto
    fechada en 2040 es un reloj mal puesto y desplazaría el final del viaje.
    """
    if valor is None:
        return None
    if not isinstance(valor, str) or len(valor) < 19:
        raise _PuntoInvalido("'capturado_en' tiene que ser 'AAAA-MM-DDTHH:MM:SS'")

    # Se parsea la cadena ENTERA antes de recortarla. Recortar a 19 caracteres
    # primero parecía inofensivo y no lo era: se comía el "+02:00" antes de
    # mirarlo, así que una fecha con huso se aceptaba **tirando el huso en
    # silencio** y quedaba guardada como si fuera hora local. El dato no daba
    # ningún error: solo estaba desplazado dos horas para siempre.
    texto = valor.strip()
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        raise _PuntoInvalido(f"'capturado_en' no es una fecha válida: {valor!r}") from None
    if instante.tzinfo is not None:
        raise _PuntoInvalido(
            "'capturado_en' es la hora local de la cámara y va sin huso; "
            "el desfase, si lo hay, va en 'offset_original'"
        )
    instante = instante.replace(microsecond=0)

    # Comparación ingenua contra la hora local del servidor, con un día de
    # margen: no se sabe el huso de la foto, así que afinar más sería fingir
    # una precisión que no hay. Basta para cazar un 2040.
    if (instante - datetime.now()).days > 1:
        raise _PuntoInvalido(f"'capturado_en' está en el futuro: {valor!r}")

    return instante.isoformat(timespec="seconds")


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

    capturado_en = _fecha_local(datos.get("capturado_en"))

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
    """Valida un lote de puntos y guarda los buenos."""
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

    return resultado
