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
    place_name  TEXT,               -- nombre resuelto en el momento de crearla
    -- Siempre UTC en ISO-8601. Convertir a hora local solo al mostrar.
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);

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


def init_db() -> None:
    """Crea el esquema si no existe. Seguro de llamar en cada arranque."""
    Config.ensure_dirs()
    with get_conn() as conn:
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
# Notas (Fase 3) -- pendiente de implementar
# ---------------------------------------------------------------------------
