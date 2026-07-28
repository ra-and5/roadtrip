"""Instantes ISO 8601: validar, canonizar a UTC y volver a hora local.

Existe porque dos módulos necesitan exactamente las mismas ~35 líneas de
aritmética de fechas —`ingest` para `medido_en` y `notes` para `created_at`— y
son líneas sutiles: qué se hace con una fecha sin huso, cómo se recorta un
desfase con segundos, qué ventana de fechas es creíble. Duplicarlas es
duplicar los sitios donde arreglar el mismo bug.

La función lanza `ValueError` y no una excepción del proyecto a propósito:
este módulo no debe saber quién lo usa. Cada llamante la traduce a la suya
(`_MuestraInvalida`, `NoteError`), que es lo que hace que el mensaje de error
acabe nombrando el campo que el cliente escribió mal.

Las dos direcciones que hacen falta, y por qué las dos:

- **Hacia UTC** para guardar. Un instante guardado con el huso en el que se
  midió no se puede comparar con otro sin volver a parsearlo.
- **Hacia hora local** para mostrar. En el navegador sobra (`toLocaleString`
  usa el huso del móvil), pero `tools/ver_notas.py` corre en una consola del
  servidor, que va en UTC: sin el desfase original guardado, ahí no hay de
  dónde sacar "las 11:32 de la mañana".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Ventana de fechas aceptable, medida desde "ahora".
#
# Hacia el futuro se admiten 24 h: margen para el desfase del reloj del móvil y
# para cruzar husos, pero no tanto como para que una fecha inventada pase por
# buena. Hacia el pasado, 30 días, porque el pasado es justamente lo que hay
# que poder recuperar tras días sin cobertura (la ventana solapada de la
# telemetría, la cola offline de las notas).
#
# El motivo de acotar: una fecha corrupta no da ningún error, se guarda tan
# ricamente y envenena en silencio cualquier análisis posterior. Es más barato
# rechazarla aquí que descubrir dentro de un mes que hay datos fechados en 1970
# metidos entre los buenos.
MAX_FUTURO = timedelta(hours=24)
MAX_PASADO = timedelta(days=30)


def parse_instant(
    valor: object,
    campo: str,
    *,
    max_futuro: timedelta = MAX_FUTURO,
    max_pasado: timedelta = MAX_PASADO,
) -> tuple[str, str | None]:
    """Valida un instante ISO 8601 y lo canoniza. Devuelve (UTC, desfase original).

    `campo` solo sirve para el mensaje de error, y no es un detalle: al otro
    lado hay alguien depurando desde un móvil, y "fecha inválida" sin decir
    cuál no se puede arreglar.

    Tres cosas se comprueban, y cada una tapa un fallo distinto:

    1. Que sea ISO 8601 parseable.
    2. Que traiga zona horaria. Sin ella no se sabe qué instante es: el mismo
       "2026-07-27T10:00:00" son dos momentos distintos en Madrid y en el
       servidor (que corre en UTC). Suponer una zona es inventarse una hora.
    3. Que caiga dentro de la ventana razonable.

    La canonización a UTC y a segundos es lo que hace efectivos los `UNIQUE`
    del esquema: dos representaciones del mismo instante tienen que producir
    la misma cadena, o un reenvío crearía una fila nueva y la idempotencia
    sería mentira.
    """
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"falta '{campo}' (fecha ISO 8601 con zona horaria)")

    try:
        # `fromisoformat` acepta "Z" desde Python 3.11, que es la versión
        # mínima del proyecto.
        instante = datetime.fromisoformat(valor.strip())
    except ValueError:
        raise ValueError(
            f"'{campo}' no es una fecha ISO 8601 válida: {valor!r}"
        ) from None

    if instante.tzinfo is None:
        raise ValueError(
            f"'{campo}' tiene que llevar zona horaria (p. ej. +02:00 o Z): {valor!r}"
        )

    ahora = datetime.now(timezone.utc)
    if instante > ahora + max_futuro:
        raise ValueError(f"'{campo}' está demasiado en el futuro: {valor!r}")
    if instante < ahora - max_pasado:
        raise ValueError(f"'{campo}' está demasiado en el pasado: {valor!r}")

    utc = instante.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat(timespec="seconds"), _offset_de(instante)


def _offset_de(instante: datetime) -> str | None:
    """El desfase horario como "+02:00", o None si ya venía en UTC.

    Se descarta "+00:00" porque ya está en el instante canónico y repetirlo es
    ruido. Se recortan los cinco primeros caracteres de "%z" porque un huso con
    segundos (los hay históricos, y `fromisoformat` los acepta) devolvería
    "+020000" y guardaríamos "+02:0000".
    """
    offset = instante.strftime("%z")[:5]
    if not offset or offset == "+0000":
        return None
    return f"{offset[:3]}:{offset[3:]}"


def to_local(utc_iso: str, offset_original: str | None) -> datetime:
    """Reconstruye la hora local en la que se tomó el dato.

    Se devuelve un `datetime` con huso y no una cadena ya formateada: quien
    llama decide cómo enseñarlo, y de paso puede agrupar por día o por año sin
    volver a parsear nada. Agrupar por el día LOCAL importa: una nota escrita a
    las 00:30 en Madrid es del día siguiente en UTC, y en un mapa que cuenta
    "días de viaje" eso desplazaría la nota un día entero.

    Ante una fecha corrupta devuelve el instante en UTC en vez de reventar:
    esto se usa para pintar, y una fila rara no debe tumbar el listado entero.
    """
    try:
        instante = datetime.fromisoformat(utc_iso)
    except ValueError:
        raise ValueError(f"instante no parseable: {utc_iso!r}") from None

    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    if not offset_original:
        return instante

    try:
        signo = -1 if offset_original[0] == "-" else 1
        horas, _, minutos = offset_original[1:].partition(":")
        desfase = timedelta(hours=int(horas), minutes=int(minutos or 0)) * signo
    except (ValueError, IndexError):
        return instante

    return instante.astimezone(timezone(desfase))
