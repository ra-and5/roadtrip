"""Persistencia: SQLite.

Responsabilidad única: leer y escribir en la base de datos. Ningún otro módulo
debe abrir una conexión ni escribir SQL.

Tablas en uso: `api_cache` (Fase 1) y `telemetria` (Fase 2d). `notes` se creó
ya en la Fase 1 aunque sea de la Fase 3, por la misma razón por la que
`telemetria` nace con la columna `fuente` sin tener más de una: crear una tabla
o una columna vacía es gratis, migrarla con datos reales dentro a mitad del
viaje no.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from app.config import Config

# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

_SCHEMA = """
-- Caché genérica para respuestas de APIs externas (Nominatim, Overpass,
-- Open-Meteo, Claude). Una sola tabla para todas: la clave lleva un prefijo
-- que identifica el origen, p.ej. "geocode:43.362,-8.411".
CREATE TABLE IF NOT EXISTS api_cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,      -- JSON serializado
    created_at  REAL NOT NULL       -- epoch en segundos (UTC)
);

-- Notas geolocalizadas (Fase 3).
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identificador generado en el MÓVIL antes de enviar. Permite reintentar
    -- el envío de una nota sin duplicarla cuando se recupera la cobertura.
    -- Es la pieza clave del modo offline.
    client_id   TEXT NOT NULL UNIQUE,
    text        TEXT NOT NULL DEFAULT '',
    photo_path  TEXT,               -- ruta relativa dentro de UPLOAD_DIR
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    place_name  TEXT,               -- etiqueta corta: "Cudillero, Asturias"
    -- La comunidad autónoma, en su propia columna en vez de deducida partiendo
    -- `place_name` por la coma. El mapa cuenta regiones completadas, y cuando
    -- Nominatim no devuelve región `place_name` es solo el pueblo: partir la
    -- cadena daría "Cudillero" como comunidad autónoma sin dar ningún error,
    -- y el contador del mapa mentiría (decisión 11).
    region      TEXT,
    -- Cuándo se ESCRIBIÓ la nota, en el móvil. Siempre UTC en ISO-8601 y
    -- canonizado a segundos. No es lo mismo que `received_at`: con cola
    -- offline una nota se escribe en un mirador sin cobertura y se guarda seis
    -- horas después.
    created_at  TEXT NOT NULL,
    -- El desfase horario tal y como vino ("+02:00"), para poder reconstruir la
    -- hora local desde una consola del servidor, que corre en UTC.
    offset_original TEXT,
    -- Cuándo la guardó el SERVIDOR (UTC). `received_at - created_at` es la
    -- única prueba objetiva de que la cola offline funcionó: una chincheta se
    -- ve igual llegue al instante o seis horas después.
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);

-- Puntos del viaje sacados de los metadatos de las fotos (Fase 3b).
--
-- Tabla propia y NO una `fuente` más dentro de `telemetria`, que era la
-- alternativa tentadora porque esa tabla ya tiene `fuente`, `lat`, `lon` y su
-- UNIQUE. Se descarta por una razón concreta: la regla vigente del proyecto es
-- que no se construye análisis sobre `telemetria` hasta cerrar la Fase 2d, y
-- meter ahí una fuente que sí es fiable obligaría a recordar un `WHERE fuente`
-- en cada consulta futura para no mezclarlas. Una regla que depende de que
-- alguien se acuerde no es una regla: es un fallo esperando (decisión 11).
--
-- Aquí NO se guarda ninguna foto, solo dónde y cuándo se hizo. Una foto son
-- ~3 MB y el plan gratuito 512 MB; sus metadatos son ~100 bytes y contienen
-- todo lo que el mapa necesita. El trayecto se reconstruye sin subir nada.
CREATE TABLE IF NOT EXISTS waypoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- De dónde sale el punto. Hoy "fotos"; mañana podría ser un GPX o la
    -- telemetría cuando se cierre la 2d.
    fuente      TEXT NOT NULL,
    -- Nombre del archivo, sin ruta. Es la clave de la idempotencia: reimportar
    -- la misma carpeta no puede duplicar el viaje. Sin ruta a propósito, para
    -- que mover las fotos de carpeta no cree puntos nuevos.
    archivo     TEXT NOT NULL,
    -- Hora LOCAL de la cámara, tal y como la escribió, SIN huso:
    -- "2026-07-28T14:32:05". El EXIF no lleva zona en esta etiqueta, y
    -- convertirla a UTC aquí sería inventarse el instante. Es distinto de
    -- `notes.created_at`, que sí es UTC canónico porque allí el navegador sí
    -- manda el huso; llamarlas igual habría escondido esa diferencia.
    capturado_en TEXT,
    -- El desfase, si la cámara lo escribió (iPhone lo hace desde iOS 13).
    -- Con él, `capturado_en` se puede llevar a UTC; sin él, no. NULL significa
    -- "no se sabe", nunca "UTC".
    offset_original TEXT,
    lat         REAL,
    lon         REAL,
    altitud     REAL,
    camara      TEXT,
    importado_en TEXT NOT NULL,
    UNIQUE(fuente, archivo)
);

CREATE INDEX IF NOT EXISTS idx_waypoints_capturado_en ON waypoints(capturado_en);

-- Telemetría del móvil (Fase 2d): pasos, ubicación y batería que el iPhone
-- envía cada hora desde un atajo de la app Atajos.
CREATE TABLE IF NOT EXISTS telemetria (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- De dónde viene la muestra. Hoy solo hay "atajos-iphone", pero la columna
    -- está desde el primer día por el mismo criterio que `client_id`: añadirla
    -- a una tabla vacía es gratis, hacerlo con datos reales dentro no.
    fuente          TEXT NOT NULL,
    -- Instante de la MEDIDA, en UTC y canonizado a segundos:
    -- "2026-07-27T08:00:00+00:00". Canonizar no es cosmética, es lo que hace
    -- que el UNIQUE de abajo funcione: "…T10:00:00+02:00" y "…T08:00:00Z" son
    -- el mismo instante, y guardados tal cual serían dos filas distintas.
    medido_en       TEXT NOT NULL,
    -- El desfase horario tal y como vino ("+02:00"), para no perder la
    -- información de en qué huso se tomó la medida. NULL si ya vino en UTC.
    offset_original TEXT,
    pasos           INTEGER,
    bateria         INTEGER,          -- porcentaje [0, 100]
    lat             REAL,
    lon             REAL,
    -- Cuándo lo recibió el SERVIDOR (UTC). Todas las muestras de un mismo
    -- envío comparten este valor, así que además identifica el lote.
    -- `recibido_en - medido_en` es la medida directa de lo que esta fase
    -- quiere comprobar: cuánto se retrasan los datos por falta de cobertura.
    -- Sin esta columna, un retraso de 5 h y un reloj mal puesto son
    -- indistinguibles.
    recibido_en     TEXT NOT NULL,
    -- La idempotencia del endpoint, escrita en el esquema en vez de confiada
    -- al código: cada envío repite a propósito las últimas horas de muestras
    -- (ver decisión 23), así que la MISMA muestra llega varias veces por
    -- diseño y solo puede quedar una.
    UNIQUE(fuente, medido_en)
);

-- Toda consulta futura sobre esto será por rango de fechas.
CREATE INDEX IF NOT EXISTS idx_telemetria_medido_en ON telemetria(medido_en);

-- Dónde estabas la PRIMERA vez que abriste la app cada día (Fase 5).
--
-- Tabla propia y no una fila más en `telemetria`, por el mismo motivo que los
-- waypoints: la regla vigente es que no se construye análisis sobre
-- `telemetria` hasta cerrar la 2d, y meter ahí una fuente distinta obligaría a
-- recordar un `WHERE fuente` en cada consulta futura.
--
-- La idempotencia va en el esquema y no en el código: `UNIQUE(fecha_local)`
-- más `INSERT OR IGNORE` significa que la PRIMERA del día gana y las demás
-- rebotan solas. Comprobar-y-luego-insertar tendría una carrera con dos
-- peticiones a la vez; una restricción de unicidad no (misma idea que la
-- decisión 23).
--
-- `fecha_local` y no UTC: abrir la app a las 00:30 en España es del día
-- siguiente en UTC, y eso desplazaría un día entero del viaje. Es la misma
-- decisión que ya se tomó para contar los días de las notas (decisión 29).
--
-- Ojo con lo que este dato ES y lo que NO es: es "el primer sitio desde el que
-- pregunté cada día", no un registro de por dónde pasé. Los días que no abras
-- la app no dejan fila, y eso no es un fallo. Mientras no demuestre que llega
-- sin huecos, no se construye análisis encima (la regla de la 2d).
CREATE TABLE IF NOT EXISTS lugar_del_dia (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_local   TEXT NOT NULL,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    place_name    TEXT,
    region        TEXT,
    -- El instante local completo con su huso, para saber a qué hora del día
    -- fue esa primera consulta sin tener que deducirlo.
    momento_local TEXT NOT NULL,
    registrado_en TEXT NOT NULL,
    UNIQUE(fecha_local)
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Abre una conexión a SQLite y la cierra pase lo que pase.

    `with get_conn() as conn:` garantiza el cierre igual que un destructor RAII
    en C++: al salir del bloque (por return, por excepción, por lo que sea) se
    ejecuta el `finally`.
    """
    conn = sqlite3.connect(Config.DB_PATH, timeout=10)
    # row_factory=Row permite acceder por nombre de columna (row["lat"]) en vez
    # de por índice (row[3]), que es ilegible y frágil.
    conn.row_factory = sqlite3.Row
    try:
        # WAL: permite leer mientras se escribe. Con un solo usuario da igual,
        # pero PythonAnywhere puede levantar varios procesos worker y esto
        # evita errores de "database is locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columnas que la Fase 3 añadió a `notes`, que nació en la Fase 1.
_NOTES_COLUMNAS_FASE3 = ("region", "offset_original", "received_at")


class SchemaError(Exception):
    """El esquema de la base de datos no es el que este código espera.

    Es un error de arranque, no de petición: mejor no arrancar que arrancar
    escribiendo en una tabla con la forma equivocada.
    """


def _migrar_notes(conn: sqlite3.Connection) -> None:
    """Lleva una tabla `notes` de la Fase 1 a la forma de la Fase 3.

    `CREATE TABLE IF NOT EXISTS` no añade columnas a una tabla que ya existe,
    así que un despliegue con la base de datos de la Fase 1 seguiría corriendo
    con la tabla vieja y el primer INSERT fallaría en producción.

    La migración es *recrear*, y eso solo es admisible por una razón que se
    comprueba en vez de suponerse: la tabla tiene que estar **vacía**. Lo está
    por construcción, porque este es el primer código del proyecto que escribe
    notas. Si algún día no lo estuviera, se levanta `SchemaError` en el
    arranque en lugar de borrar nada: perder las notas del viaje para ahorrar
    una migración a mano sería el peor intercambio posible.

    Se recrea en vez de ir con `ALTER TABLE ADD COLUMN` porque `received_at` es
    `NOT NULL` y añadirla así obligaría a darle un valor por defecto vacío. El
    esquema de una instalación nueva y el de una migrada divergirían, y esa
    clase de divergencia no da error: da una columna obligatoria que acepta
    cadenas vacías solo en los servidores viejos.
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(notes)")}
    if not columnas:
        return  # La tabla no existe todavía; el CREATE la hará bien.
    faltan = [c for c in _NOTES_COLUMNAS_FASE3 if c not in columnas]
    if not faltan:
        return

    filas = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    if filas:
        raise SchemaError(
            f"La tabla 'notes' es de la Fase 1 (le faltan: {', '.join(faltan)}) "
            f"y tiene {filas} filas. Migra a mano antes de arrancar: exporta las "
            f"notas, borra la tabla y vuelve a importarlas."
        )

    conn.execute("DROP TABLE notes")


def init_db() -> None:
    """Crea el esquema si no existe. Seguro de llamar en cada arranque."""
    Config.ensure_dirs()
    with get_conn() as conn:
        _migrar_notes(conn)
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Caché de APIs externas
# ---------------------------------------------------------------------------

def cache_get(key: str, max_age_seconds: float) -> Any | None:
    """Devuelve el valor cacheado, o None si no existe o ha caducado.

    `max_age_seconds` lo decide quien llama, porque cada dato caduca a un
    ritmo distinto: el nombre de un pueblo no cambia nunca, el tiempo cambia
    cada hora.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value, created_at FROM api_cache WHERE key = ?", (key,)
        ).fetchone()

    if row is None:
        return None
    if time.time() - row["created_at"] > max_age_seconds:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        # Entrada corrupta: la tratamos como si no existiera.
        return None


def cache_set(key: str, value: Any) -> None:
    """Guarda (o reemplaza) un valor en la caché."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO api_cache (key, value, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )


def cache_key_for_coords(prefix: str, lat: float, lon: float) -> str:
    """Construye una clave de caché estable a partir de unas coordenadas.

    Redondear es lo que hace la caché útil: sin ello, moverte un metro genera
    una clave nueva y nunca aciertas.
    """
    p = Config.CACHE_COORD_PRECISION
    return f"{prefix}:{round(lat, p):.{p}f},{round(lon, p):.{p}f}"


# ---------------------------------------------------------------------------
# Telemetría del móvil (Fase 2d)
# ---------------------------------------------------------------------------

# Orden de columnas de una fila de telemetría, en un solo sitio. Se usa tanto
# para el INSERT como para leer: si mañana se añade una métrica, no hay dos
# listas que puedan desalinearse en silencio.
_TELEMETRIA_COLS = (
    "fuente",
    "medido_en",
    "offset_original",
    "pasos",
    "bateria",
    "lat",
    "lon",
    "recibido_en",
)


def insert_telemetry(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """Inserta muestras ignorando las que ya existan. Devuelve (nuevas, repetidas).

    `INSERT OR IGNORE` contra el `UNIQUE(fuente, medido_en)` es lo que hace el
    endpoint idempotente: recibir dos veces el mismo lote deja la base de datos
    exactamente igual que recibirlo una. No es un caso raro que haya que tolerar
    sino el funcionamiento normal, porque cada envío del móvil repite a
    propósito las últimas horas (decisión 23).

    Se insertan de una en una, y no con `executemany`, porque `rowcount` de un
    `executemany` no distingue cuántas filas entraron de verdad: es justo el
    dato que hay que devolverle al móvil. Con un máximo de 500 filas por
    petición y una petición por hora, el coste es irrelevante.

    Todo dentro de una única transacción: o entra el lote entero o no entra
    nada. Un corte a mitad no puede dejar media ventana guardada y hacer que la
    siguiente parezca completa.
    """
    if not rows:
        return 0, 0

    columnas = ", ".join(_TELEMETRIA_COLS)
    marcadores = ", ".join("?" for _ in _TELEMETRIA_COLS)
    sql = f"INSERT OR IGNORE INTO telemetria ({columnas}) VALUES ({marcadores})"

    nuevas = 0
    with get_conn() as conn:
        for row in rows:
            cur = conn.execute(sql, tuple(row[c] for c in _TELEMETRIA_COLS))
            # rowcount es 1 si entró y 0 si el UNIQUE la descartó.
            nuevas += cur.rowcount

    return nuevas, len(rows) - nuevas


def telemetry_stats() -> dict[str, Any]:
    """Cuántas muestras hay y cuál es la última. Para el diagnóstico.

    Es la respuesta a la única pregunta que cierra esta fase: ¿siguen llegando
    los datos? Un total que no crece o una última muestra de anteayer dicen que
    no, y eso hay que poder verlo desde una consola del servidor.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(medido_en) AS ultima_medida, "
            "       MAX(recibido_en) AS ultima_recepcion "
            "FROM telemetria"
        ).fetchone()
        por_fuente = conn.execute(
            "SELECT fuente, COUNT(*) AS n FROM telemetria GROUP BY fuente ORDER BY n DESC"
        ).fetchall()

    return {
        "total": row["total"] or 0,
        "ultima_medida": row["ultima_medida"],
        "ultima_recepcion": row["ultima_recepcion"],
        "por_fuente": {r["fuente"]: r["n"] for r in por_fuente},
    }


def delete_telemetry(ids: Sequence[int]) -> int:
    """Borra muestras por id. Devuelve cuántas se han borrado de verdad.

    Existe porque los datos reales se ensucian: pruebas del atajo, una fecha
    escrita a mano, un día en que la métrica venía mal. Borrarlas es más honesto
    que dejarlas y acordarse de filtrarlas al analizar -- ese "acordarse" no
    sobrevive a un mes de viaje.

    Devolver el recuento real (y no `len(ids)`) es lo que permite distinguir
    "borradas" de "esos ids no existían", que desde una consola es la diferencia
    entre haber hecho el trabajo y creer que lo has hecho.
    """
    if not ids:
        return 0
    marcadores = ", ".join("?" for _ in ids)
    with get_conn() as conn:
        cur = conn.execute(
            f"DELETE FROM telemetria WHERE id IN ({marcadores})", tuple(ids)
        )
        return cur.rowcount


def delete_telemetry_by_source(fuente: str) -> int:
    """Borra TODAS las muestras de una fuente. Devuelve cuántas.

    Existe por los datos simulados: si se pueden sembrar, tiene que haber una
    forma de un solo comando de volver a dejar la tabla con lo que llegó de
    verdad. Sin ella, quitar la simulación sería una lista de ids copiada a mano
    desde una consola -- justo el trabajo que nadie hace bien a la tercera vez,
    y que dejaría muestras falsas mezcladas con las buenas sin dar ningún error.

    Se borra por fuente y no por rango de fechas a propósito: la fuente es lo
    que separa las dos series, y borrar "desde el día tal" se llevaría por
    delante muestras reales del mismo periodo.
    """
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM telemetria WHERE fuente = ?", (fuente,))
        return cur.rowcount


def recent_telemetry(limit: int = 20) -> list[dict[str, Any]]:
    """Las últimas muestras por instante de medida, la más reciente primero.

    Devuelve diccionarios y no `sqlite3.Row` a propósito: fuera de este módulo
    nadie debe manejar tipos de sqlite3, igual que fuera de `llm_providers`
    nadie maneja tipos de Anthropic.
    """
    with get_conn() as conn:
        filas = conn.execute(
            f"SELECT id, {', '.join(_TELEMETRIA_COLS)} FROM telemetria "
            "ORDER BY medido_en DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [dict(f) for f in filas]


# ---------------------------------------------------------------------------
# Notas geolocalizadas (Fase 3)
# ---------------------------------------------------------------------------

# Igual que `_TELEMETRIA_COLS`: el orden de las columnas en un solo sitio, para
# que el INSERT y la lectura no puedan desalinearse en silencio.
_NOTES_COLS = (
    "client_id",
    "text",
    "photo_path",
    "lat",
    "lon",
    "place_name",
    "region",
    "created_at",
    "offset_original",
    "received_at",
)


def insert_note(row: dict[str, Any]) -> tuple[int, bool]:
    """Guarda una nota. Devuelve (id, creada) -- `creada=False` si ya existía.

    `INSERT OR IGNORE` contra el `UNIQUE(client_id)` es lo que hace idempotente
    la creación: el móvil genera el `client_id` ANTES del primer intento y lo
    reutiliza en cada reintento, así que reenviar una nota tras recuperar la
    cobertura no puede duplicarla. La garantía vive en el esquema y no en un
    `SELECT` previo porque comprobar-y-luego-insertar tiene una carrera con dos
    peticiones a la vez; el `UNIQUE` no.

    Devolver el id también cuando ya existía no es un adorno: la respuesta del
    servidor tiene que decirle al cliente CUÁL es la nota que ya tenía, o la
    cola local no podría enlazar lo que borra con lo que hay en el servidor.

    El `SELECT` posterior va dentro de la misma conexión y transacción que el
    `INSERT`, así que ve el estado ya escrito.
    """
    columnas = ", ".join(_NOTES_COLS)
    marcadores = ", ".join("?" for _ in _NOTES_COLS)

    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO notes ({columnas}) VALUES ({marcadores})",
            tuple(row[c] for c in _NOTES_COLS),
        )
        # rowcount es 1 si entró y 0 si el UNIQUE la descartó.
        creada = cur.rowcount == 1
        fila = conn.execute(
            "SELECT id FROM notes WHERE client_id = ?", (row["client_id"],)
        ).fetchone()

    return int(fila["id"]), creada


def list_notes(limit: int = 1000) -> list[dict[str, Any]]:
    """Las notas, de la más reciente a la más antigua.

    Se ordena por `created_at` (cuándo se escribió) y no por `id` (en qué orden
    llegaron), porque con cola offline no son lo mismo: una nota escrita el
    martes sin cobertura entra en la base de datos después de otra del
    miércoles. El orden que le importa a un mapa del viaje es el de los hechos,
    no el del tráfico de red.

    Devuelve diccionarios y no `sqlite3.Row`: fuera de este módulo nadie debe
    manejar tipos de sqlite3.
    """
    with get_conn() as conn:
        filas = conn.execute(
            f"SELECT id, {', '.join(_NOTES_COLS)} FROM notes "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [dict(f) for f in filas]


def notes_stats() -> dict[str, Any]:
    """Cuántas notas hay y de cuándo son. Para el diagnóstico y `ver_notas.py`."""
    with get_conn() as conn:
        fila = conn.execute(
            "SELECT COUNT(*) AS total, MIN(created_at) AS primera, "
            "       MAX(created_at) AS ultima, MAX(received_at) AS ultima_recepcion, "
            "       COUNT(photo_path) AS con_foto "
            "FROM notes"
        ).fetchone()

    return {
        "total": fila["total"] or 0,
        "primera": fila["primera"],
        "ultima": fila["ultima"],
        "ultima_recepcion": fila["ultima_recepcion"],
        "con_foto": fila["con_foto"] or 0,
    }


_WAYPOINT_COLS = (
    "fuente",
    "archivo",
    "capturado_en",
    "offset_original",
    "lat",
    "lon",
    "altitud",
    "camara",
    "importado_en",
)


def insert_waypoints(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """Guarda puntos del viaje ignorando los que ya existan. (nuevos, repetidos).

    Misma mecánica que `insert_telemetry` y por el mismo motivo: reimportar la
    carpeta de fotos entera es lo normal —se hace cada vez que se vuelcan las
    del móvil— y tiene que dejar la base de datos igual que importarla una vez.
    Lo garantiza el `UNIQUE(fuente, archivo)`, no un `SELECT` previo.
    """
    if not rows:
        return 0, 0

    columnas = ", ".join(_WAYPOINT_COLS)
    marcadores = ", ".join("?" for _ in _WAYPOINT_COLS)
    sql = f"INSERT OR IGNORE INTO waypoints ({columnas}) VALUES ({marcadores})"

    nuevos = 0
    with get_conn() as conn:
        for row in rows:
            cur = conn.execute(sql, tuple(row[c] for c in _WAYPOINT_COLS))
            nuevos += cur.rowcount

    return nuevos, len(rows) - nuevos


def list_waypoints(limit: int = 5000) -> list[dict[str, Any]]:
    """Los puntos del viaje, del más antiguo al más reciente.

    Al revés que las notas, y a propósito: una nota se lee como un mensaje (lo
    último primero) y un trayecto se recorre hacia delante. Este orden es el
    del recorrido, que es para lo que existe la tabla.

    Los que no traen fecha van al final: SQLite ordena NULL primero, y abrir la
    línea del viaje con las fotos que no se sabe cuándo se hicieron sería
    empezar el relato por lo que no se puede contar.
    """
    with get_conn() as conn:
        filas = conn.execute(
            f"SELECT id, {', '.join(_WAYPOINT_COLS)} FROM waypoints "
            "ORDER BY capturado_en IS NULL, capturado_en ASC LIMIT ?",
            (limit,),
        ).fetchall()

    return [dict(f) for f in filas]


def waypoints_stats() -> dict[str, Any]:
    """Cuántos puntos hay, cuántos ubicados, y el tramo que cubren."""
    with get_conn() as conn:
        fila = conn.execute(
            "SELECT COUNT(*) AS total, COUNT(lat) AS ubicados, "
            "       MIN(capturado_en) AS primera, MAX(capturado_en) AS ultima "
            "FROM waypoints"
        ).fetchone()
        por_fuente = conn.execute(
            "SELECT fuente, COUNT(*) AS n FROM waypoints GROUP BY fuente ORDER BY n DESC"
        ).fetchall()

    return {
        "total": fila["total"] or 0,
        "ubicados": fila["ubicados"] or 0,
        "primera": fila["primera"],
        "ultima": fila["ultima"],
        "por_fuente": {r["fuente"]: r["n"] for r in por_fuente},
    }


def delete_waypoints(ids: Sequence[int]) -> int:
    """Borra puntos por id. Devuelve cuántos se han borrado de verdad."""
    if not ids:
        return 0
    marcadores = ", ".join("?" for _ in ids)
    with get_conn() as conn:
        cur = conn.execute(
            f"DELETE FROM waypoints WHERE id IN ({marcadores})", tuple(ids)
        )
        return cur.rowcount


def delete_notes(ids: Sequence[int]) -> int:
    """Borra notas por id. Devuelve cuántas se han borrado de verdad.

    Mismo criterio que `delete_telemetry`: se devuelve el recuento real y no
    `len(ids)` porque desde una consola esa es la diferencia entre haber hecho
    el trabajo y creer que lo has hecho.

    Borrar es la única forma de corregir una nota, y es a propósito: la Fase 3
    no tiene edición desde la web (ver el alcance del encargo).
    """
    if not ids:
        return 0
    marcadores = ", ".join("?" for _ in ids)
    with get_conn() as conn:
        cur = conn.execute(f"DELETE FROM notes WHERE id IN ({marcadores})", tuple(ids))
        return cur.rowcount


# ---------------------------------------------------------------------------
# El lugar del día (Fase 5)
# ---------------------------------------------------------------------------

def insert_lugar_del_dia(fila: dict[str, Any]) -> bool:
    """Guarda dónde estabas hoy, si es la primera vez que se pregunta.

    Devuelve True solo si se ha insertado de verdad. `INSERT OR IGNORE` contra
    el `UNIQUE(fecha_local)` hace que la primera del día gane y las demás
    reboten solas, sin `SELECT` previo y sin carrera.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO lugar_del_dia "
            "(fecha_local, lat, lon, place_name, region, momento_local, registrado_en) "
            "VALUES (:fecha_local, :lat, :lon, :place_name, :region, "
            ":momento_local, :registrado_en)",
            fila,
        )
        return cur.rowcount > 0


def list_lugares_del_dia(limit: int = 400) -> list[dict[str, Any]]:
    """Los días registrados, del más reciente al más antiguo."""
    with get_conn() as conn:
        filas = conn.execute(
            "SELECT id, fecha_local, lat, lon, place_name, region, "
            "       momento_local, registrado_en "
            "FROM lugar_del_dia ORDER BY fecha_local DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [dict(f) for f in filas]


def lugares_del_dia_stats() -> dict[str, Any]:
    """Cuántos días hay registrados y cuáles son el primero y el último.

    Sirve para lo único que decide si este dato se puede usar: ¿hay huecos?
    Un total muy por debajo de los días transcurridos entre `primero` y
    `ultimo` significa que no llega solo, y entonces no se construye encima.
    """
    with get_conn() as conn:
        fila = conn.execute(
            "SELECT COUNT(*) AS total, MIN(fecha_local) AS primero, "
            "       MAX(fecha_local) AS ultimo FROM lugar_del_dia"
        ).fetchone()

    return {
        "total": fila["total"] or 0,
        "primero": fila["primero"],
        "ultimo": fila["ultimo"],
    }
