"""El panel: cuánto llevas de viaje y si te puedes fiar de esas cifras.

Función de entrada: `construir(zona) -> Panel`.

No calcula nada por su cuenta: resume lo que ya resumen `metricas`, `viaje`,
`notes.progreso()` y `diario`. Sin red, sin GPS y sin LLM — todo sale de SQLite.
El *aquí y ahora* (lugar, tiempo, luna) es otra pregunta y ya tiene su endpoint:
lo pide el navegador a `/api/contexto`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules import diario, notes, storage
from app.modules.metricas import (
    ENVIOS_ESPERADOS_POR_DIA,
    FUENTE_REAL,
    Cobertura,
    Metricas,
    cobertura,
    resumir,
)
from app.modules.timeparse import hace_cuanto
from app.modules.viaje import Viaje, armar as armar_viaje

__all__ = ["Barra", "EstadoFuente", "Panel", "armar", "construir", "serie"]

# Los mismos 7 días que `metricas.DIAS_DE_CONTEXTO`: pedir más aquí dibujaría
# huecos permanentes al principio, porque `metricas` no los trae.
DIAS_DE_SERIE = 7

_DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_ZONA_POR_DEFECTO = "Europe/Madrid"

# Vocabulario distinto del de `contexto.Fuente` (ok/sin_datos/fallo/no_consultada)
# porque la pregunta es otra: allí "¿respondió la consulta?", aquí "¿se puede
# construir algo encima?". Una fuente que contesta bien puede llevar tres días
# sin llegar.
DEMOSTRADA = "demostrada"
CON_HUECOS = "con_huecos"
SIN_DATOS = "sin_datos"
SIMULADA = "simulada"


@dataclass(frozen=True)
class Barra:
    """Un día de la serie de pasos.

    `pasos=None` es un día sin muestras y NO un cero: un cero dice "no anduvo" y
    un hueco dice "no lo sé". Pintarlos igual convierte un día sin cobertura en
    un día de sofá.
    """

    fecha: str
    etiqueta: str
    pasos: int | None
    es_hoy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "fecha": self.fecha,
            "etiqueta": self.etiqueta,
            "pasos": self.pasos,
            "es_hoy": self.es_hoy,
        }


@dataclass(frozen=True)
class EstadoFuente:
    """El veredicto sobre una fuente, con la cifra que lo sostiene."""

    clave: str
    nombre: str
    estado: str
    detalle: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "clave": self.clave,
            "nombre": self.nombre,
            "estado": self.estado,
            "detalle": self.detalle,
        }


@dataclass(frozen=True)
class Panel:
    """Todo lo que enseña el panel, ya resuelto."""

    hoy: str
    dia_del_viaje: int | None = None
    cuerpo: Metricas = field(default_factory=Metricas)
    serie: list[Barra] = field(default_factory=list)
    viaje: Viaje = field(default_factory=Viaje)
    progreso: dict[str, Any] = field(default_factory=dict)
    fuentes: list[EstadoFuente] = field(default_factory=list)
    # Sube hasta aquí para poder marcar los pasos en la propia cifra, no en un
    # campo aparte que nadie mira (decisión 37).
    hay_simulado: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hoy": self.hoy,
            "dia_del_viaje": self.dia_del_viaje,
            "cuerpo": self.cuerpo.to_dict(),
            "serie": [b.to_dict() for b in self.serie],
            "viaje": self.viaje.to_dict(),
            "progreso": self.progreso,
            "fuentes": [f.to_dict() for f in self.fuentes],
            "hay_simulado": self.hay_simulado,
        }


def serie(metricas: Metricas, hoy: date, *, dias: int = DIAS_DE_SERIE) -> list[Barra]:
    """Los últimos `dias` días de pasos, con los huecos dentro. Función PURA.

    `pasos_por_dia` solo trae los días con dato, así que pintarlo tal cual
    dibujaría una semana continua con tres barras. La ventana acaba HOY: una
    serie que se paró el viernes tiene que verse parada.
    """
    por_dia = {fecha: pasos for fecha, pasos in metricas.pasos_por_dia}
    barras = []
    for atras in range(dias - 1, -1, -1):
        dia = hoy - timedelta(days=atras)
        barras.append(Barra(
            fecha=dia.isoformat(),
            etiqueta=_DIAS_CORTOS[dia.weekday()],
            pasos=por_dia.get(dia.isoformat()),
            es_hoy=atras == 0,
        ))
    return barras


def _fecha_de(iso: str | None) -> date | None:
    """El día de un instante ISO. Se recorta en vez de parsear porque los dos
    campos que pasan por aquí traen husos distintos (decisión 30)."""
    if not iso or len(iso) < 10:
        return None
    try:
        return date.fromisoformat(iso[:10])
    except ValueError:
        return None


def _fuente_telemetria(cob: Cobertura, ahora: datetime) -> EstadoFuente:
    """El veredicto sobre lo que cierra la Fase 2d. Solo muestras reales."""
    if not cob.dias_con_datos:
        return EstadoFuente(
            "telemetria", "Telemetría del móvil", SIN_DATOS,
            f"Ninguna muestra de «{FUENTE_REAL}». Los pasos no son medidos.",
        )

    trozos = [f"{cob.dias_con_datos} de {cob.dias_abarcados} días"]
    if cob.huecos:
        trozos.append(f"{cob.huecos} días sin nada")
    if cob.dias_incompletos:
        trozos.append(f"{cob.dias_incompletos} a medias (de {ENVIOS_ESPERADOS_POR_DIA})")
    trozos.append(f"última {hace_cuanto(cob.ultima, ahora)}")

    # Sin huecos no basta: seis envíos al día que entregan dos dan cero huecos y
    # no son una serie fiable.
    completa = cob.sin_huecos and not cob.dias_incompletos
    return EstadoFuente(
        "telemetria", "Telemetría del móvil",
        DEMOSTRADA if completa else CON_HUECOS,
        " · ".join(trozos),
    )


def armar(
    muestras_reales: list[dict[str, Any]],
    muestras_todas: list[dict[str, Any]],
    notas: list[dict[str, Any]],
    puntos: list[dict[str, Any]],
    dias_registrados: dict[str, Any],
    hoy: date,
    *,
    ahora: datetime | None = None,
) -> Panel:
    """Arma el panel con las listas ya leídas. Función PURA.

    La telemetría entra dos veces a propósito: `muestras_todas` es lo que se
    enseña (si no, con solo datos simulados el panel saldría vacío) y
    `muestras_reales` es lo único que puede declarar una fuente demostrada.
    Pasar la misma lista a los dos haría que una simulación certificara su propia
    fiabilidad (decisión 36).
    """
    ahora = ahora or datetime.now(timezone.utc)

    cuerpo = resumir(muestras_todas, hoy)
    viaje = armar_viaje(notas, puntos)

    fuentes = [_fuente_telemetria(cobertura(muestras_reales, hoy), ahora)]

    simuladas = len(muestras_todas) - len(muestras_reales)
    if simuladas > 0:
        fuentes.append(EstadoFuente(
            "simulado", "Muestras simuladas", SIMULADA,
            f"{simuladas} sembradas con tools/simular_telemetria.py. "
            "Sirven para construir la pantalla; no cierran la Fase 2d.",
        ))

    fuentes.append(EstadoFuente(
        "notas", "Notas del viaje",
        DEMOSTRADA if notas else SIN_DATOS,
        f"{len(notas)} notas · la última "
        f"{hace_cuanto(max(n['created_at'] for n in notas), ahora)}"
        if notas else "Todavía no has escrito ninguna.",
    ))

    con_gps = sum(1 for p in puntos if p.get("lat") is not None)
    fuentes.append(EstadoFuente(
        "fotos", "Fotos del viaje",
        DEMOSTRADA if puntos else SIN_DATOS,
        f"{len(puntos)} puntos · {con_gps} con GPS"
        if puntos else "Ninguna foto importada todavía.",
    ))

    huecos_dias = dias_registrados.get("huecos")
    total_dias = dias_registrados.get("total", 0)
    fuentes.append(EstadoFuente(
        "lugar_del_dia", "Dónde amaneces",
        SIN_DATOS if not total_dias else DEMOSTRADA if not huecos_dias else CON_HUECOS,
        f"{total_dias} días registrados"
        + (f" · {huecos_dias} sin registrar" if huecos_dias else "")
        if total_dias else "Se registra solo al abrir la app cada día.",
    ))

    # Días de calendario desde el primer momento registrado. No es `viaje.dias`,
    # que cuenta los días CON algo dentro: un día de carretera sin notas sigue
    # siendo día de viaje.
    primer_dia = _fecha_de(viaje.primera)
    dia_del_viaje = (hoy - primer_dia).days + 1 if primer_dia else None
    if dia_del_viaje is not None and dia_del_viaje < 1:
        dia_del_viaje = None  # reloj del móvil adelantado; "día 0" parece un bug

    return Panel(
        hoy=hoy.isoformat(),
        dia_del_viaje=dia_del_viaje,
        cuerpo=cuerpo,
        serie=serie(cuerpo, hoy),
        viaje=viaje,
        progreso=notes.progreso(notas),
        fuentes=fuentes,
        hay_simulado=cuerpo.es_simulado,
    )


def construir(zona: str = _ZONA_POR_DEFECTO, *, ahora: datetime | None = None) -> Panel:
    """Lee la base de datos y arma el panel.

    La zona llega de fuera porque el día es el LOCAL (decisión 29). Nunca lanza:
    lo que no se pueda leer sale vacío y su fuente lo dice.
    """
    try:
        tz = ZoneInfo(zona)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo(_ZONA_POR_DEFECTO)

    referencia = (ahora or datetime.now(tz)).astimezone(tz)
    hoy = referencia.date()
    desde = (
        (referencia - timedelta(days=DIAS_DE_SERIE))
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
    )

    try:
        muestras_todas = storage.telemetry_since(desde)
        muestras_reales = storage.telemetry_since(desde, fuentes=[FUENTE_REAL])
        notas = [notes.to_public(f) for f in storage.list_notes()]
        puntos = storage.list_waypoints()
        dias_registrados = diario.resumen()
    except Exception:  # noqa: BLE001 - una pantalla de lectura no tumba nada
        return Panel(hoy=hoy.isoformat())

    return armar(
        muestras_reales, muestras_todas, notas, puntos, dias_registrados, hoy,
        ahora=referencia.astimezone(timezone.utc),
    )
