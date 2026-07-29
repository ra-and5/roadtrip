#!/usr/bin/env python3
"""Dónde se va el tiempo de `contexto.construir()`.

`tools/diagnostico.py` dice **si** el contexto cumple su contrato (por debajo de
un segundo) y falla cuando pasa de dos. Lo que no dice es **cuál** de las tres
fuentes se lo come, y esa es justamente la pregunta cuando el diagnóstico
suspende en el servidor pero cada fuente por separado sale rápida un par de
líneas más arriba.

Mide cada una dos veces, en frío y en caliente, y luego `construir()` entera.
Leer la salida:

  - una fuente lenta la 1ª vez y rápida la 2ª  → es la red; la caché funciona
  - una fuente lenta las DOS veces             → no se está cacheando
  - las tres rápidas y `construir()` lenta     → el coste está en los hilos, no
                                                 en la red

Se le pueden pasar coordenadas, igual que al diagnóstico. Por defecto usa las
mismas (Cudillero), para poder comparar las dos salidas sin pensar.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules import contexto  # noqa: E402
from app.modules.location_context import reverse_geocode  # noqa: E402
from app.modules.luna import efemerides  # noqa: E402
from app.modules.weather_context import get_weather  # noqa: E402

_ZONA = "Europe/Madrid"


def main() -> int:
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 43.5622
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else -6.1456

    # La misma referencia que usa `construir()`: de ella sale la FECHA con la
    # que se piden las efemérides, y la fecha va dentro de la clave de caché.
    referencia = datetime.now(ZoneInfo(_ZONA))

    print(f"Midiendo {lat}, {lon}")
    print(f"hora local de referencia: {referencia}")
    print(f"reloj del sistema (UTC):  {datetime.now(timezone.utc)}")
    print()

    fuentes = [
        ("reverse_geocode", lambda: reverse_geocode(lat, lon)),
        ("get_weather", lambda: get_weather(lat, lon)),
        ("efemerides", lambda: efemerides(lat, lon, referencia)),
    ]

    for nombre, llamada in fuentes:
        for vuelta in ("1a", "2a"):
            inicio = time.time()
            try:
                llamada()
                veredicto = "ok"
            except Exception as exc:  # noqa: BLE001 - aquí se mide, no se degrada
                veredicto = f"{type(exc).__name__}: {str(exc)[:70]}"
            print(f"  {nombre:<16} {vuelta}  {time.time() - inicio:6.2f}s  {veredicto}")

    print()
    for vuelta in ("1a", "2a"):
        inicio = time.time()
        try:
            estado = contexto.construir(lat, lon)
            caidas = [n for n, f in estado.fuentes.items() if f.estado == contexto.FALLO]
            veredicto = f"{len(estado.fuentes) - len(caidas)}/{len(estado.fuentes)} fuentes"
            if caidas:
                veredicto += f" (caídas: {', '.join(caidas)})"
        except Exception as exc:  # noqa: BLE001
            veredicto = f"{type(exc).__name__}: {str(exc)[:70]}"
        print(f"  {'construir()':<16} {vuelta}  {time.time() - inicio:6.2f}s  {veredicto}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
