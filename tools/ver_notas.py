"""Vuelca las últimas notas del viaje y el progreso del mapa.

Uso:
    python tools/ver_notas.py                # las 20 últimas
    python tools/ver_notas.py 50             # las 50 últimas
    python tools/ver_notas.py --borrar 3,4   # borra las notas 3 y 4

Consola, no web: la Fase 3 no tiene edición ni borrado desde el navegador a
propósito (ver el alcance del encargo), y una nota mala hay que poder quitarla
igualmente. Es el mismo patrón que `ver_telemetria.py`, que ya funciona.

La columna que de verdad importa es **retraso**: `received_at - created_at`.
En régimen normal son segundos. Varias horas significa que la nota se escribió
sin cobertura y la cola offline la recuperó al volver la señal, que es
exactamente lo que esta fase quiere demostrar que funciona. Una chincheta en el
mapa se ve igual en los dos casos; esta columna es la única forma de saberlo.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

# Este script vive en tools/, así que Python pone tools/ en el path, no la raíz
# del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules import notes, storage  # noqa: E402  (después del sys.path, a la fuerza)

ANCHO_TEXTO = 42


def _retraso(created_at: str, received_at: str) -> str:
    """Cuánto tardó la nota en llegar al servidor, en formato legible."""
    try:
        delta = datetime.fromisoformat(received_at) - datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return "?"

    segundos = int(delta.total_seconds())
    if segundos < 0:
        # El móvil dice haber escrito la nota después de que llegara. Con la
        # validación de `notes` solo puede pasar por desfase del reloj.
        return f"-{-segundos // 60}min"
    if segundos < 60:
        return f"{segundos}s"
    if segundos < 3600:
        return f"{segundos // 60}min"
    return f"{segundos // 3600}h{(segundos % 3600) // 60:02d}"


def _recorta(texto: str, ancho: int) -> str:
    """Una línea, sin saltos, que quepa en la tabla.

    Los saltos de línea se sustituyen en vez de dejarlos pasar: una nota de
    tres párrafos rompería la tabla y haría ilegible todo lo que viene después.
    """
    plano = " ".join(texto.split())
    return plano if len(plano) <= ancho else plano[: ancho - 1] + "…"


def _borrar(argumento: str) -> None:
    """Borra las notas cuyos ids se pasen separados por comas."""
    try:
        ids = [int(trozo) for trozo in argumento.split(",") if trozo.strip()]
    except ValueError:
        print(f"'{argumento}' no es una lista de ids. Ejemplo: --borrar 3,4")
        sys.exit(1)

    borradas = storage.delete_notes(ids)
    print(f"\nBorradas {borradas} de {len(ids)} notas pedidas.")
    if borradas < len(ids):
        # Desde una consola, esta es la diferencia entre haber hecho el trabajo
        # y creer que lo has hecho.
        print("Alguno de esos ids no existía. Comprueba la columna id.\n")


def _imprimir_progreso(todas: list[dict]) -> None:
    p = notes.progreso(todas)
    tablero = p["tablero"]

    print(
        f"\nProgreso: {p['total']} notas · {p['lugares']} sitios · "
        f"{p['dias']} días con nota · racha máxima {p['racha_maxima']}"
    )
    print(f"Comunidades: {tablero['completadas']} de {tablero['total']}")

    visitadas = [c["nombre"] for c in tablero["casillas"] if c["visitada"]]
    if visitadas:
        print(f"  hechas:  {', '.join(visitadas)}")
    faltan = [c["nombre"] for c in tablero["casillas"] if not c["visitada"]]
    if faltan:
        print(f"  faltan:  {', '.join(faltan)}")
    if tablero["otras"]:
        print(f"  fuera del tablero: {', '.join(tablero['otras'])}")

    if len(p["por_anio"]) > 1:
        # Solo cuando hay más de un año: comparar un año consigo mismo no dice
        # nada y ocupa dos líneas.
        print("\nPor año:")
        for anio, datos in sorted(p["por_anio"].items(), reverse=True):
            print(
                f"  {anio}: {datos['notas']:>4} notas · {datos['lugares']:>3} sitios · "
                f"{datos['dias']:>3} días · racha {datos['racha_maxima']}"
            )

    if p["mas_visitados"]:
        print("\nSitios a los que vuelves:")
        for lugar in p["mas_visitados"]:
            print(f"  {lugar['etiqueta']:<28} {lugar['dias']} días, {lugar['visitas']} notas")


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
            print(f"'{argumentos[0]}' no es un número. Uso: python tools/ver_notas.py [n]")
            sys.exit(1)

    storage.init_db()
    todas = notes.get_notes()

    if not todas:
        print(
            "\nTodavía no hay ninguna nota.\n"
            "Si el móvil dice que las guardó, siguen en la cola del navegador:\n"
            "abre la app con cobertura y se enviarán solas. Si no, mira\n"
            "  python tools/diagnostico.py\n"
            "y docs/troubleshooting.md.\n"
        )
        return

    _imprimir_progreso(todas)

    filas = todas[:limite]
    print(f"\nÚltimas {len(filas)} notas (la más reciente primero):\n")
    # El id se enseña para poder pasárselo a --borrar. Sin él, la opción de
    # borrar existiría pero no habría forma de saber qué borrar.
    cabecera = (
        f"{'id':>5} {'escrita (local)':<20} {'lugar':<22} "
        f"{'texto':<{ANCHO_TEXTO}} {'retraso':>8}"
    )
    print(cabecera)
    print("-" * len(cabecera))

    for nota in filas:
        # La hora local, no la de UTC: es la que se recuerda. El servidor corre
        # en UTC, así que sin `offset_original` guardado esto no se podría
        # reconstruir desde aquí.
        local = (nota["created_at_local"] or "")[:16].replace("T", " ")
        lugar = _recorta(nota["place_name"] or "—", 22)
        print(
            f"{nota['id']:>5} {local:<20} {lugar:<22} "
            f"{_recorta(nota['text'], ANCHO_TEXTO):<{ANCHO_TEXTO}} "
            f"{_retraso(nota['created_at'], nota['received_at']):>8}"
        )

    print("\nPara borrar notas malas:  python tools/ver_notas.py --borrar <id>,<id>\n")


if __name__ == "__main__":
    main()
