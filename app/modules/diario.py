"""El registro del viaje día a día. Hoy: dónde estabas la primera vez que hoy
preguntaste.

Función de entrada: `registrar_lugar_del_dia(contexto) -> bool`.

**Por qué esto no vive dentro de `contexto.construir()`.** Sería lo cómodo —ya
tiene el sitio y la hora resueltos— y sería un error: `construir()` es la pieza
que van a llamar la pantalla, el recomendador y el chatbot, y darle un efecto
secundario significa que preguntarle algo al chatbot escribiría en la base de
datos. Una función que consulta y una que registra son dos trabajos, y mezclarlos
convierte cada consulta futura en una escritura que nadie pidió.

**Por qué tampoco vive en `app.py`.** La regla del proyecto es que las vistas
validan, llaman a un módulo y formatean. Una vista que arma un `dict` y llama a
`storage` ya es lógica de negocio escondida en el sitio donde nadie la busca.

**Qué es este dato y qué no es.** Es "el primer sitio desde el que pregunté cada
día", no un registro de por dónde pasé. Los días que no abras la app no dejan
fila, y eso no es un fallo: es lo que significa. Por eso, y siguiendo la misma
regla que mantiene aparcada la Fase 2d, **no se construye ningún análisis encima
mientras no demuestre que llega sin huecos**. Se registra desde ya para que,
cuando llegue el día de mirarlo, haya historia que mirar — que es la decisión 4
otra vez: una tabla vacía es gratis hoy y cara con un mes de viaje dentro.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules import storage
from app.modules.contexto import Contexto

__all__ = ["registrar_lugar_del_dia", "resumen"]


def registrar_lugar_del_dia(contexto: Contexto) -> bool:
    """Anota dónde estabas hoy. Devuelve True solo si era la primera vez.

    La fecha es la LOCAL del sitio, no la del servidor: PythonAnywhere corre en
    UTC, y abrir la app a las 00:30 en España cuenta como el día siguiente en
    UTC. Eso desplazaría un día entero del viaje sin dar ningún error, que es
    exactamente el motivo por el que los días de las notas ya se cuentan en
    hora local (decisión 29).

    No lanza nunca por un fallo de escritura: registrar es un efecto lateral de
    mirar el contexto, y no puede tumbar la pantalla. Si la base de datos falla,
    lo que se pierde es una fila de historia, no la app.
    """
    momento = contexto.momento

    fila: dict[str, Any] = {
        "fecha_local": momento.dt.strftime("%Y-%m-%d"),
        "lat": contexto.ubicacion.lat,
        "lon": contexto.ubicacion.lon,
        "place_name": contexto.ubicacion.short_label(),
        "region": contexto.ubicacion.region or None,
        "momento_local": momento.dt.isoformat(timespec="seconds"),
        "registrado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    try:
        return storage.insert_lugar_del_dia(fila)
    except Exception:  # noqa: BLE001 - un fallo al registrar no tumba la consulta
        return False


def resumen() -> dict[str, Any]:
    """Cuántos días hay registrados, y si tienen huecos.

    `huecos` es la cifra que decide si este dato sirve para algo: los días que
    van del primero al último menos los que de verdad hay. Mientras no sea cero
    de forma sostenida, esto es un registro incompleto y no una fuente sobre la
    que construir — la misma vara de medir que la Fase 2d.
    """
    datos = storage.lugares_del_dia_stats()

    huecos = None
    if datos["primero"] and datos["ultimo"]:
        primero = datetime.strptime(datos["primero"], "%Y-%m-%d")
        ultimo = datetime.strptime(datos["ultimo"], "%Y-%m-%d")
        abarcados = (ultimo - primero).days + 1
        huecos = abarcados - datos["total"]

    return {**datos, "huecos": huecos}
