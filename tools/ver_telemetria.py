"""Vuelca las últimas muestras de telemetría recibidas del móvil.

Uso:
    python tools/ver_telemetria.py            # las 20 últimas
    python tools/ver_telemetria.py 50         # las 50 últimas
    python tools/ver_telemetria.py --borrar 3,4    # borra las muestras 3 y 4

Consola, no web: en la Fase 2d no hay ninguna interfaz para estos datos a
propósito (ver el alcance de la fase), pero hay que poder comprobar que llegan
bien. Esto es lo que se abre desde una consola Bash de PythonAnywhere cuando la
pregunta es "¿está entrando algo, y tiene buena pinta?".

La columna que de verdad importa es **retraso**: `recibido_en - medido_en`. En
régimen normal son minutos. Varias horas significa que el móvil estuvo sin
cobertura y la ventana solapada recuperó esas muestras al volver la señal, que
es justo lo que esta fase quiere demostrar que funciona.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

# Este script vive en tools/, así que Python pone tools/ en el path, no la raíz
# del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules import storage  # noqa: E402  (después del sys.path, a la fuerza)


def _retraso(medido_en: str, recibido_en: str) -> str:
    """Cuánto tardó la muestra en llegar, en formato legible."""
    try:
        delta = datetime.fromisoformat(recibido_en) - datetime.fromisoformat(medido_en)
    except ValueError:
        return "?"

    segundos = int(delta.total_seconds())
    if segundos < 0:
        # El móvil dice haber medido en el futuro respecto a cuando llegó.
        # Con la validación de `ingest` solo puede pasar por desfase del reloj.
        return f"-{-segundos // 60}min"
    if segundos < 3600:
        return f"{segundos // 60}min"
    return f"{segundos // 3600}h{(segundos % 3600) // 60:02d}"


def _borrar(argumento: str) -> None:
    """Borra las muestras cuyos ids se pasen separados por comas."""
    try:
        ids = [int(trozo) for trozo in argumento.split(",") if trozo.strip()]
    except ValueError:
        print(f"'{argumento}' no es una lista de ids. Ejemplo: --borrar 3,4")
        sys.exit(1)

    borradas = storage.delete_telemetry(ids)
    print(f"\nBorradas {borradas} de {len(ids)} muestras pedidas.")
    if borradas < len(ids):
        # No es un detalle menor: significa que has borrado menos de lo que
        # crees, y desde una consola nadie lo comprobaría por su cuenta.
        print("Alguno de esos ids no existía. Comprueba la columna id.\n")


def main() -> None:
    limite = 20
    argumentos = sys.argv[1:]

    if "--borrar" in argumentos:
        posicion = argumentos.index("--borrar")
        if posicion + 1 >= len(argumentos):
            print("Falta la lista de ids. Ejemplo: --borrar 3,4")
            sys.exit(1)
        storage.init_db()
        _borrar(argumentos[posicion + 1])
        argumentos = argumentos[:posicion] + argumentos[posicion + 2 :]

    if argumentos:
        try:
            limite = max(1, int(argumentos[0]))
        except ValueError:
            print(f"'{argumentos[0]}' no es un número. Uso: python tools/ver_telemetria.py [n]")
            sys.exit(1)

    storage.init_db()
    stats = storage.telemetry_stats()

    print(f"\nTelemetría: {stats['total']} muestras en total")
    if stats["por_fuente"]:
        detalle = ", ".join(f"{f}: {n}" for f, n in stats["por_fuente"].items())
        print(f"Por fuente: {detalle}")
    if stats["ultima_medida"]:
        print(f"Última medida:    {stats['ultima_medida']}")
        print(f"Última recepción: {stats['ultima_recepcion']}")

    filas = storage.recent_telemetry(limite)
    if not filas:
        print(
            "\nNo hay ninguna muestra todavía.\n"
            "Si el atajo dice que envió bien, comprueba con\n"
            "  python tools/diagnostico.py\n"
            "que INGEST_TOKEN_HASH está configurado, y mira docs/atajo-iphone.md.\n"
        )
        return

    print(f"\nÚltimas {len(filas)} muestras (la más reciente primero):\n")
    # El id se muestra para poder pasárselo a --borrar. Sin él, la opción de
    # borrar existiría pero no habría forma de saber qué borrar.
    cabecera = (
        f"{'id':>5} {'medido_en (UTC)':<26} {'huso':>6} {'pasos':>7} {'bat':>4} "
        f"{'lat':>9} {'lon':>10} {'retraso':>8}"
    )
    print(cabecera)
    print("-" * len(cabecera))

    for f in filas:
        pasos = "-" if f["pasos"] is None else str(f["pasos"])
        bateria = "-" if f["bateria"] is None else f"{f['bateria']}%"
        lat = "-" if f["lat"] is None else f"{f['lat']:.5f}"
        lon = "-" if f["lon"] is None else f"{f['lon']:.5f}"
        huso = f["offset_original"] or "UTC"
        print(
            f"{f['id']:>5} {f['medido_en']:<26} {huso:>6} {pasos:>7} {bateria:>4} "
            f"{lat:>9} {lon:>10} {_retraso(f['medido_en'], f['recibido_en']):>8}"
        )
    print("\nPara borrar muestras malas:  python tools/ver_telemetria.py --borrar <id>,<id>\n")


if __name__ == "__main__":
    main()
