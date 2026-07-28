"""Vuelca las últimas muestras de telemetría recibidas del móvil.

Uso:
    python tools/ver_telemetria.py            # las 20 últimas
    python tools/ver_telemetria.py 50         # las 50 últimas
    python tools/ver_telemetria.py --coords   # con lat/lon en vez del nombre
    python tools/ver_telemetria.py --borrar 3,4    # borra las muestras 3 y 4

Consola, no web: en la Fase 2d no hay ninguna interfaz para estos datos a
propósito (ver el alcance de la fase), pero hay que poder comprobar que llegan
bien. Esto es lo que se abre desde una consola Bash de PythonAnywhere cuando la
pregunta es "¿está entrando algo, y tiene buena pinta?".

Las coordenadas se enseñan **con nombre** ("Cudillero, Asturias") y no en
crudo: `38.39064, -0.51648` no dice nada, y esta tabla se lee para responder
"¿dónde estaba y llegó bien?". Los números siguen guardados y se ven con
`--coords` cuando lo que se depura es el GPS y no el viaje.

El nombre se resuelve **al consultar y no al ingerir**, y esa es una decisión,
no una comodidad. Resolverlo al recibir la muestra metería una llamada de red
dentro de la ruta que tiene que ser rápida y no puede fallar, haría que un
Nominatim caído impidiera **guardar** datos que están perfectos, y convertiría
la tubería en análisis. Un dato crudo se guarda siempre; interpretarlo es otro
trabajo.

Aquí sale casi gratis: en un camper te quedas horas en el mismo sitio, así que
decenas de muestras caen en la **misma clave de caché** (coordenada redondeada a
~110 m) y son una sola petición. Y si no hay red, se enseñan las coordenadas y
ya está: se degrada, no se pierde nada.

Las filas que NO vienen del móvil salen marcadas con `~` y con un aviso arriba.
Hoy eso son las de `tools/simular_telemetria.py`, que existen para poder
construir encima sin esperar semanas. Marcarlas no es cortesía: una tabla en la
que un dato inventado se lee igual que uno medido es exactamente el fallo
silencioso de la decisión 11, y encima en la herramienta con la que se decide
si la fase se puede cerrar.

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
from app.modules.location_context import reverse_geocode  # noqa: E402


# Ancho de la columna "dónde". "San Vicente del Raspeig, Comunidad Valenciana"
# son 44 caracteres y no cabe: se recorta, porque una columna que se ensancha
# sola descuadra la tabla entera en una consola de 80.
_ANCHO_DONDE = 30

# La única fuente que llega de un móvil de verdad. Todo lo demás se marca.
_FUENTE_REAL = "atajos-iphone"


def _nombres(filas: list) -> dict[tuple, str]:
    """Traduce las coordenadas de las muestras a nombres de sitio.

    Se agrupa ANTES de preguntar: se resuelve una vez por coordenada distinta y
    no una por muestra. Nominatim limita a una petición por segundo, así que la
    diferencia entre 50 llamadas y 2 no es cosmética — es que la segunda vez
    esto tarda medio minuto o no tarda nada.

    Nunca lanza: si Nominatim está caído o no hay red, la muestra se queda sin
    nombre y se enseñan sus coordenadas. Que esta herramienta deje de funcionar
    por no poder adornar una columna sería absurdo, y encima justo cuando más
    falta hace (sin cobertura, comprobando si llegaron los datos).
    """
    resueltos: dict[tuple, str] = {}

    for fila in filas:
        if fila["lat"] is None or fila["lon"] is None:
            continue
        clave = (round(fila["lat"], 3), round(fila["lon"], 3))
        if clave in resueltos:
            continue
        try:
            resueltos[clave] = reverse_geocode(fila["lat"], fila["lon"]).short_label()
        except Exception:  # noqa: BLE001 - adornar una columna no puede romper la herramienta
            resueltos[clave] = ""

    return resueltos


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

    crudas = "--coords" in argumentos
    argumentos = [a for a in argumentos if a != "--coords"]

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

    print(f"\nÚltimas {len(filas)} muestras (la más reciente primero):")
    if any(f["fuente"] != _FUENTE_REAL for f in filas):
        print(
            f"\n  ~  = muestra SIMULADA, no llegó del móvil. Para quitarlas:\n"
            f"     python tools/simular_telemetria.py --limpiar"
        )
    print()
    # El id se muestra para poder pasárselo a --borrar. Sin él, la opción de
    # borrar existiría pero no habría forma de saber qué borrar.
    sitios = {} if crudas else _nombres(filas)

    if crudas:
        cabecera = (
            f"{'id':>6} {'medido_en (UTC)':<26} {'huso':>6} {'pasos':>7} {'bat':>4} "
            f"{'lat':>9} {'lon':>10} {'retraso':>8}"
        )
    else:
        cabecera = (
            f"{'id':>6} {'medido_en (UTC)':<26} {'huso':>6} {'pasos':>7} {'bat':>4} "
            f"{'dónde':<{_ANCHO_DONDE}} {'retraso':>8}"
        )
    print(cabecera)
    print("-" * len(cabecera))

    for f in filas:
        pasos = "-" if f["pasos"] is None else str(f["pasos"])
        bateria = "-" if f["bateria"] is None else f"{f['bateria']}%"
        huso = f["offset_original"] or "UTC"
        retraso = _retraso(f["medido_en"], f["recibido_en"])
        # El `~` va pegado al id, en la primera columna: es lo primero que se
        # lee al recorrer la tabla con la vista, y así ninguna fila inventada
        # se cuela como medida ni de reojo.
        etiqueta = f["id"] if f["fuente"] == _FUENTE_REAL else f"~{f['id']}"
        comun = f"{etiqueta:>6} {f['medido_en']:<26} {huso:>6} {pasos:>7} {bateria:>4} "

        if crudas:
            lat = "-" if f["lat"] is None else f"{f['lat']:.5f}"
            lon = "-" if f["lon"] is None else f"{f['lon']:.5f}"
            print(f"{comun}{lat:>9} {lon:>10} {retraso:>8}")
            continue

        # El nombre se recorta a la anchura de la columna. Descuadrarla dejaría
        # `retraso` bailando de fila en fila, y esa es LA columna que hay que
        # poder recorrer con la vista buscando una recuperación tras un hueco.
        if f["lat"] is None or f["lon"] is None:
            # Sin ubicación NO se escribe nada parecido a un sitio: una muestra
            # sin GPS y una en el golfo de Guinea tienen que verse distintas.
            donde = "(sin ubicación)"
        else:
            clave = (round(f["lat"], 3), round(f["lon"], 3))
            # Si Nominatim no contestó, las coordenadas en crudo. Nunca un
            # nombre inventado ni un hueco mudo.
            donde = sitios.get(clave) or f"{f['lat']:.4f}, {f['lon']:.4f}"

        if len(donde) > _ANCHO_DONDE:
            donde = donde[: _ANCHO_DONDE - 1] + "…"

        print(f"{comun}{donde:<{_ANCHO_DONDE}} {retraso:>8}")
    print("\nPara borrar muestras malas:  python tools/ver_telemetria.py --borrar <id>,<id>")
    if not crudas:
        print("Para ver las coordenadas en crudo:  python tools/ver_telemetria.py --coords\n")
    else:
        print()


if __name__ == "__main__":
    main()
