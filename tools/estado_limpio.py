#!/usr/bin/env python3
"""¿Está la app limpia para empezar el viaje de verdad?

Uso:
    python tools/estado_limpio.py              # el inventario, sin tocar nada
    python tools/estado_limpio.py --limpiar    # borra SOLO lo simulado
    python tools/estado_limpio.py --borrar-todo-el-viaje   # el reset completo

Existe porque las limpiezas estaban repartidas por cuatro herramientas
(`simular_telemetria --limpiar`, `ver_notas --borrar`, `ver_telemetria --borrar`,
`importar_fotos --limpiar`) y ninguna contestaba la pregunta que de verdad se
hace antes de estrenar: **¿queda algo aquí que no sea de verdad?** Con las
respuestas repartidas, la forma de saberlo era acordarse de las cuatro, y una
regla que depende de que alguien se acuerde no es una regla.

LA DISTINCIÓN QUE ORDENA TODO ESTE ARCHIVO, y por la que hay dos banderas y no
una: hay dos cosas muy distintas que se confunden al decir "limpiar".

  1. **Lo simulado.** Muestras sembradas por `tools/simular_telemetria.py` para
     poder construir el dashboard antes de tener datos (decisión 36). Llevan
     `fuente = "simulado"`, así que se reconocen solas y borrarlas no puede
     perder nada: son inventadas por definición.

  2. **El viaje.** Tus notas, tus fotos y tus conversaciones. Son datos REALES,
     aunque sean de una prueba en Albatera. Nada en la base de datos distingue
     "una nota de prueba" de "una nota del viaje" — solo tú lo sabes.

Borrar (1) es seguro y va con `--limpiar`. Borrar (2) es irreversible y va detrás
de una bandera larga que hay que escribir entera. Es la asimetría de la
decisión 45: una fila de más se ve y se quita, una de menos es historia borrada.

Y no hay ningún `--todo` que haga las dos cosas a la vez: juntarlas sería
esconder la irreversible detrás de la inocua.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.config import Config  # noqa: E402
from app.modules import metricas, miniaturas, storage  # noqa: E402

VERDE = "\033[32m"
AMARILLO = "\033[33m"
ROJO = "\033[31m"
GRIS = "\033[90m"
FIN = "\033[0m"


def _contar(sql: str, *parametros: object) -> int:
    with storage.get_conn() as conn:
        return int(conn.execute(sql, parametros).fetchone()[0])


def inventario() -> dict[str, int]:
    """Qué hay hoy en la base de datos, por categoría."""
    return {
        "notas": _contar("SELECT COUNT(*) FROM notes"),
        "fotos": _contar("SELECT COUNT(*) FROM waypoints"),
        "chat": _contar("SELECT COUNT(*) FROM chat_mensajes"),
        "dias": _contar("SELECT COUNT(*) FROM lugar_del_dia"),
        "telemetria_real": _contar(
            "SELECT COUNT(*) FROM telemetria WHERE fuente = ?", metricas.FUENTE_REAL
        ),
        "telemetria_simulada": _contar(
            "SELECT COUNT(*) FROM telemetria WHERE fuente = ?", "simulado"
        ),
        "telemetria_otras": _contar(
            "SELECT COUNT(*) FROM telemetria WHERE fuente NOT IN (?, ?)",
            metricas.FUENTE_REAL,
            "simulado",
        ),
        "cache": _contar("SELECT COUNT(*) FROM api_cache"),
    }


def _miniaturas_en_disco() -> int:
    directorio = Path(Config.UPLOAD_DIR) / "miniaturas"
    if not directorio.is_dir():
        return 0
    return sum(1 for hijo in directorio.iterdir() if hijo.is_file())


def informar(datos: dict[str, int]) -> bool:
    """Imprime el inventario. Devuelve si hay algo simulado."""
    print(f"\n  {GRIS}Base de datos:{FIN} {Config.DB_PATH}\n")

    simulado = datos["telemetria_simulada"]
    minis = _miniaturas_en_disco()

    print(f"  {GRIS}EL VIAJE{FIN}   datos reales tuyos; nadie más sabe si son de prueba")
    print(f"    notas ............... {datos['notas']}")
    print(f"    fotos (puntos) ...... {datos['fotos']}")
    print(f"    miniaturas .......... {minis}  ({miniaturas.usado_mb():.1f} MB)")
    print(f"    mensajes de chat .... {datos['chat']}")
    print(f"    primer sitio del día  {datos['dias']}")

    print(f"\n  {GRIS}TELEMETRÍA{FIN}")
    print(f"    reales ({metricas.FUENTE_REAL}) .. {datos['telemetria_real']}")
    color = AMARILLO if simulado else VERDE
    print(f"    {color}simuladas ........... {simulado}{FIN}")
    if datos["telemetria_otras"]:
        print(f"    {AMARILLO}otras fuentes ....... {datos['telemetria_otras']}{FIN}")

    # La caché no es un dato del viaje: son respuestas de Nominatim y Open-Meteo
    # que se regeneran solas. Se cuenta porque ocupa, no porque haya que
    # limpiarla; borrarla solo hace que la primera consulta de cada sitio vuelva
    # a pagarse.
    print(f"\n  {GRIS}CACHÉ{FIN}  (se regenera sola; no es dato del viaje)")
    print(f"    respuestas guardadas  {datos['cache']}")

    print()
    if simulado:
        print(f"  {AMARILLO}Hay {simulado} muestras SIMULADAS.{FIN} No son medidas: las sembró")
        print("  tools/simular_telemetria.py para poder construir el dashboard")
        print("  antes de tener datos (decisión 36).")
        print(f"\n     Quítalas con:  {VERDE}python tools/estado_limpio.py --limpiar{FIN}\n")
        return True

    print(f"  {VERDE}Sin datos simulados.{FIN} Todo lo que hay llegó de verdad.\n")

    # Y lo que NO puede contestar esta herramienta, dicho en voz alta: que no
    # haya nada inventado no significa que las fuentes estén demostradas. Eso lo
    # dice `diagnostico.py` mirando la continuidad, y son preguntas distintas
    # (decisión 39).
    print(f"  {GRIS}Ojo: «limpio» no es «demostrado». Que la telemetría llegue sola{FIN}")
    print(f"  {GRIS}y sin huecos lo dice:  python tools/diagnostico.py{FIN}\n")
    return False


def limpiar_simulado() -> None:
    idas = storage.delete_telemetry_by_source("simulado")
    print(f"\n  {VERDE}Borradas {idas} muestras simuladas.{FIN} Lo real no se ha tocado.\n")


def borrar_el_viaje() -> None:
    """El reset completo. Irreversible, y por eso pregunta."""
    datos = inventario()
    total = datos["notas"] + datos["fotos"] + datos["chat"] + datos["dias"]
    minis = _miniaturas_en_disco()

    print(f"\n  {ROJO}Esto borra el viaje entero y NO se puede deshacer:{FIN}")
    print(f"    {datos['notas']} notas, {datos['fotos']} fotos, {minis} miniaturas,")
    print(f"    {datos['chat']} mensajes de chat, {datos['dias']} días registrados")
    print(f"    y las {datos['telemetria_real'] + datos['telemetria_simulada']} muestras de telemetría.\n")

    if total == 0 and minis == 0:
        print(f"  {VERDE}No hay nada que borrar.{FIN}\n")
        return

    # Se escribe la palabra entera. Un `s/n` se contesta por inercia, y aquí al
    # otro lado hay un mes de viaje.
    respuesta = input("  Escribe BORRAR para confirmar: ").strip()
    if respuesta != "BORRAR":
        print(f"\n  {VERDE}No se ha tocado nada.{FIN}\n")
        return

    with storage.get_conn() as conn:
        for tabla in ("notes", "waypoints", "chat_mensajes", "lugar_del_dia", "telemetria"):
            conn.execute(f"DELETE FROM {tabla}")  # noqa: S608  nombres literales

    # Las miniaturas van después de las filas, por lo mismo que en el borrado del
    # álbum: si el DELETE falla, no se ha perdido ninguna imagen.
    directorio = Path(Config.UPLOAD_DIR) / "miniaturas"
    if directorio.is_dir():
        for hijo in directorio.iterdir():
            if hijo.is_file():
                hijo.unlink()

    print(f"\n  {VERDE}Borrado. La app arranca como el primer día.{FIN}")
    print(f"  {GRIS}La caché de Nominatim y Open-Meteo se ha dejado: no es dato del{FIN}")
    print(f"  {GRIS}viaje y borrarla solo haría más lenta la primera consulta.{FIN}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limpiar", action="store_true", help="borra SOLO lo simulado")
    parser.add_argument(
        "--borrar-todo-el-viaje",
        action="store_true",
        help="borra notas, fotos, chat y telemetría. IRREVERSIBLE",
    )
    args = parser.parse_args()

    storage.init_db()

    if args.borrar_todo_el_viaje:
        borrar_el_viaje()
        return 0

    if args.limpiar:
        limpiar_simulado()
        return 0

    # Código 1 cuando queda algo simulado, para que sirva de comprobación antes
    # de estrenar sin tener que leer la salida.
    return 1 if informar(inventario()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
