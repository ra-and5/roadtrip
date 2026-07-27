"""Persistencia: SQLite.

Responsabilidad única: leer y escribir en la base de datos. Ningún otro módulo
debe abrir una conexión ni escribir SQL.

En la Fase 1 solo usamos la tabla `api_cache`. El resto del esquema (notas) se
crea ya para no tener que migrar la base de datos a mitad del viaje: crear una
tabla vacía es gratis, migrar una con datos reales no.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

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
# Notas (Fase 3) -- pendiente de implementar
# ---------------------------------------------------------------------------
