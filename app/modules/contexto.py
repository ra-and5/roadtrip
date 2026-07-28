"""El estado del viaje, en un solo sitio y en un formato que sirve a los tres.

Función de entrada: `construir(lat, lon) -> Contexto`.

Esta es la pieza central del proyecto, y conviene entender por qué existe antes
de tocarla. Hasta ahora, "el contexto" se armaba dentro de la vista de
`/api/recommendations`: la ubicación, el tiempo y los POIs se pedían allí y solo
allí. Eso tenía tres consecuencias, y las tres se arreglan con la misma
separación:

  - no se podía mirar dónde estás y qué tiempo hace sin pagar una llamada al
    modelo, porque las cuatro cosas iban en la misma petición;
  - la pantalla tardaba lo que tardase la fuente más lenta;
  - y no existía ninguna forma de pedir "el contexto" sin pedir también una
    recomendación, que es exactamente lo que necesita el chatbot.

Ahora hay **una sola definición** del contexto y tres consumidores: la pantalla
(`/api/contexto`), el recomendador (`ai_orchestrator`) y, cuando llegue, el
chatbot. Es el razonamiento de la decisión 10 —una interfaz, una definición—
aplicado al contexto en vez de al proveedor de LLM: si cada consumidor armara el
suyo, divergirían, y nadie sabría cuál es el bueno.

Dos cosas que este módulo NO hace, a propósito:

  - **No habla con ningún LLM.** Ni lo importa. Construir el contexto tiene que
    ser gratis y rápido; interpretarlo es otro trabajo.
  - **No decide cómo se pinta.** Devuelve datos (`Contexto`), y cada consumidor
    los presenta a su manera: `to_dict()` para el JSON de la pantalla,
    `ai_orchestrator.formatear_para_prompt()` para el texto que lee el modelo.
    Por eso añadir el chatbot no tocará este archivo.

El reparto de responsabilidades dentro del módulo también es deliberado:

    construir()   hace la red (y solo eso)  -> no se puede probar sin doblar HTTP
    ensamblar()   es PURA                   -> se prueba con datos escritos a mano

Toda la lógica interesante (qué hora local es, qué fuente falló, qué avisos
salen) vive en `ensamblar()`. Es lo que permite que la suite compruebe la
degradación sin tocar la red, que es la regla del proyecto.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.location_context import (
    InvalidCoordinates,
    LocationError,
    Place,
    reverse_geocode,
    validate_coords,
)
from app.modules.luna import Efemerides, Luna, LunaError, efemerides, fase, veredicto_nocturno
from app.modules.metricas import Metricas, obtener as obtener_metricas
from app.modules.viaje import Viaje, resumen as resumen_viaje
from app.modules.weather_context import Weather, WeatherError, get_weather

__all__ = [
    "Contexto",
    "Fuente",
    "InvalidCoordinates",
    "LocationError",
    "Momento",
    "construir",
    "ensamblar",
]


# ---------------------------------------------------------------------------
# El estado de una fuente
# ---------------------------------------------------------------------------

# Los cuatro estados en los que puede quedar una fuente del contexto. Que sean
# cuatro y no un booleano es la decisión menos obvia de este módulo, así que
# queda escrita:
#
# Un campo a `null` no distingue "aquí no hay nada" de "no he podido mirar", y
# esa confusión ya costó una decisión en este proyecto: el espejo suizo de
# Overpass se descartó precisamente porque respondía 200 con cero elementos, y
# aceptarlo habría hecho que la app dijera "aquí no hay nada que ver" cuando lo
# que pasaba era "no he podido consultarlo" (decisión 22). El corolario de esa
# decisión —que el código tiene que distinguir las dos cosas— se implementa
# aquí.
#
# La alternativa, dejarlo en `null` + la prosa de `warnings`, sirve para una
# pantalla que enseña texto y NO sirve para el chatbot, que necesita saber si
# callar o avisar. Un estado es un dato; una frase en castellano no lo es.
OK = "ok"
SIN_DATOS = "sin_datos"
FALLO = "fallo"
NO_CONSULTADA = "no_consultada"

# Cómo se nombra cada fuente cuando hay que avisar al usuario de que falló.
# Vive aquí, junto a los estados, para que añadir una fuente sea añadir una
# línea y no recordar dos sitios.
_ETIQUETAS: dict[str, str] = {
    "ubicacion": "Sin ubicación",
    "tiempo": "Sin datos meteorológicos",
    "oleaje": "Sin datos de oleaje",
    "luna": "Sin salida y puesta de la luna",
    "pois": "Sin puntos de interés cercanos",
    "metricas": "Sin métricas del día",
    "viaje": "Sin el registro del viaje",
    "zona_horaria": "Zona horaria sin confirmar",
}


@dataclass(frozen=True)
class Fuente:
    """En qué ha quedado una de las fuentes del contexto.

    `motivo` está vacío cuando `estado` es "ok", y explica el porqué en
    cualquier otro caso. No es decorativo: es lo que convierte un hueco en el
    contexto en algo que se puede razonar en vez de adivinar.
    """

    estado: str
    motivo: str = ""

    @property
    def es_fallo(self) -> bool:
        return self.estado == FALLO

    def to_dict(self) -> dict[str, Any]:
        return {"estado": self.estado, "motivo": self.motivo}


# ---------------------------------------------------------------------------
# El momento
# ---------------------------------------------------------------------------

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

# A dónde se cae cuando no se sabe la zona horaria. El viaje es por el norte de
# España, así que acertará casi siempre — y ese "casi" es justo el motivo de que
# `Momento` lleve `zona_es_supuesta`. Ver `_hora_local()`.
_ZONA_POR_DEFECTO = "Europe/Madrid"


@dataclass(frozen=True)
class Momento:
    """Cuándo es "ahora" en el sitio donde está el viajero.

    Guarda el `datetime` real y deriva de él todo lo demás. Un solo campo del
    que sale el resto es lo que evita que la fecha y la hora se contradigan
    entre sí, que es un fallo que no da error.

    `zona_es_supuesta` no es un adorno, es un fallo silencioso que existía y
    que este campo hace visible. La zona horaria la aporta Open-Meteo
    (`timezone=auto`); si el tiempo falla, no hay zona y hay que caer a
    `Europe/Madrid`. Hasta ahora eso pasaba **sin decírselo a nadie**: en
    Canarias son planes recomendados con una hora de error y ni un mensaje. Es
    la misma regla que ya se aplicó al huso horario del EXIF (decisión 30): no
    se inventa una zona en silencio, se marca que se ha supuesto.
    """

    dt: datetime
    zona: str
    zona_es_supuesta: bool = False

    @property
    def dia_semana(self) -> str:
        return _DIAS[self.dt.weekday()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "iso": self.dt.isoformat(timespec="seconds"),
            "fecha": self.dt.strftime("%Y-%m-%d"),
            "hora": self.dt.strftime("%H:%M"),
            "dia_semana": self.dia_semana,
            "zona": self.zona,
            "zona_es_supuesta": self.zona_es_supuesta,
        }


def _hora_local(tiempo: Weather | None, ahora: datetime | None = None) -> Momento:
    """Qué hora es donde está el viajero.

    Se usa la zona que devuelve Open-Meteo y NO la del servidor: PythonAnywhere
    corre en UTC, y recomendar "un plan de tarde" a las 20:00 UTC cuando en
    Asturias son las 22:00 es un fallo real.

    Cuando no hay zona (porque el tiempo falló) o cuando la que llega no la
    conoce este sistema, se cae a `Europe/Madrid` **marcándolo**. Equivocarse
    en silencio sobre la hora local envenena todo lo que cuelga de ella: la
    franja de la caché de recomendaciones, el "queda poca luz" del prompt y,
    el día que exista, el resumen del día.
    """
    nombre = (tiempo.timezone if tiempo else "") or ""
    supuesta = not nombre

    try:
        tz = ZoneInfo(nombre or _ZONA_POR_DEFECTO)
    except (ZoneInfoNotFoundError, ValueError):
        # Open-Meteo ha devuelto una zona que este sistema no tiene instalada.
        # Es raro, pero pasa en instalaciones mínimas sin tzdata, y hay que
        # tratarlo como lo que es: no sabemos la hora local.
        tz = ZoneInfo(_ZONA_POR_DEFECTO)
        nombre = ""
        supuesta = True

    instante = (ahora or datetime.now(tz)).astimezone(tz)
    return Momento(dt=instante, zona=nombre or _ZONA_POR_DEFECTO, zona_es_supuesta=supuesta)


# ---------------------------------------------------------------------------
# El contexto
# ---------------------------------------------------------------------------

@dataclass
class Contexto:
    """El estado del viaje aquí y ahora.

    Qué es imprescindible y qué no, que es lo que ordena todo lo demás:

        ubicacion  OBLIGATORIA. Sin saber dónde estás no hay contexto ninguno,
                   así que si falla no se construye un Contexto a medias: se
                   propaga la excepción y la vista contesta 502.
        momento    Siempre existe. No se consulta a nadie, se deriva.
        tiempo     Opcional. `None` si Open-Meteo no contestó.
        luna       Siempre está: la fase se CALCULA y no depende de la red.
                   Lo que puede faltar dentro es `efemerides` (met.no).
        metricas   Hueco reservado (pasos y batería). Hoy siempre `None`,
                   porque la Fase 2d no está cerrada y no se construye análisis
                   sobre una fuente que todavía no ha demostrado que llega sin
                   huecos. El día que cierre se rellena sin tocar la pantalla.

    `fuentes` dice en qué ha quedado cada una. `avisos()` sale de ahí en vez de
    ir por su cuenta, y eso es a propósito: así es **imposible** que una fuente
    falle sin que salga su aviso, o que salga un aviso de algo que no ha
    fallado. La invariante la garantiza la construcción, no acordarse de
    añadir la línea — que es la misma idea que poner la idempotencia de la
    ingesta en el UNIQUE de la tabla y no en un SELECT previo (decisión 23).
    """

    ubicacion: Place
    momento: Momento
    tiempo: Weather | None = None
    luna: Luna | None = None
    # El hueco que llevaba declarado y vacío desde la Fase 5, y que ahora se
    # llena (decisión 36). Sigue siendo opcional: la pantalla rápida no lo pide.
    metricas: Metricas | None = None
    # El viaje hasta ahora: notas, días, kilómetros. Opcional por lo mismo.
    viaje: Viaje | None = None
    fuentes: dict[str, Fuente] = field(default_factory=dict)

    def avisos(self) -> list[str]:
        """Lo que ha ido mal HOY, en frases para enseñar al usuario.

        Solo los `fallo`. Un `sin_datos` (no hay oleaje porque esto es tierra
        adentro) y un `no_consultada` (los pasos, mientras la 2d no cierre) no
        son problemas: son estados normales, y avisarlos en cada petición
        durante semanas es exactamente el ruido inútil que hace que se dejen de
        leer los avisos. Están en `fuentes`, que es donde se miran los estados.

        Se serializa como `warnings` porque ese es el nombre que ya consume el
        frontend; cambiarlo no compraría nada y rompería la pantalla.
        """
        return [
            f"{_ETIQUETAS.get(nombre, nombre)}: {fuente.motivo}"
            for nombre, fuente in self.fuentes.items()
            if fuente.es_fallo
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ubicacion": self.ubicacion.to_dict(),
            "momento": self.momento.to_dict(),
            "tiempo": self.tiempo.to_dict() if self.tiempo else None,
            "luna": self.luna.to_dict() if self.luna else None,
            "metricas": self.metricas.to_dict() if self.metricas else None,
            "viaje": self.viaje.to_dict() if self.viaje else None,
            "fuentes": {n: f.to_dict() for n, f in self.fuentes.items()},
            "warnings": self.avisos(),
        }


# ---------------------------------------------------------------------------
# Ensamblado (función PURA: sin red, sin reloj del sistema si se le pasa `ahora`)
# ---------------------------------------------------------------------------

def _armar_luna(
    momento: Momento,
    tiempo: Weather | None,
    efem: Efemerides | None,
) -> Luna:
    """La luna del momento: lo calculado siempre, lo consultado si se pudo.

    La fase y la iluminación son aritmética y no fallan nunca, así que `luna`
    no es None ni con met.no caído: lo que falta entonces es solo la hora de
    salida. Partirlo así es lo que hace que en un camper sin cobertura sigas
    sabiendo qué luna hay esta noche, que es cuando más sirve.
    """
    fase_actual = fase(momento.dt)
    return Luna(
        fase=fase_actual,
        veredicto=veredicto_nocturno(
            fase_actual,
            tiempo.weather_code if tiempo else None,
            hay_efemerides=efem is not None,
        ),
        efemerides=efem,
    )


def ensamblar(
    ubicacion: Place,
    tiempo: Weather | None = None,
    *,
    fallo_tiempo: str = "",
    efem: Efemerides | None = None,
    fallo_luna: str = "",
    metricas: Metricas | None = None,
    viaje: Viaje | None = None,
    ahora: datetime | None = None,
) -> Contexto:
    """Arma el `Contexto` a partir de las piezas ya resueltas.

    Aquí vive toda la lógica que merece pruebas —qué hora local es, en qué ha
    quedado cada fuente, qué se avisa y qué no— y ni una sola llamada de red.
    Es lo que permite comprobar la degradación con datos escritos a mano, sin
    doblar HTTP y sin conexión.

    Args:
        ubicacion: el lugar ya resuelto. Obligatorio.
        tiempo: las condiciones, o None si no se pudieron obtener.
        fallo_tiempo: por qué no se pudieron obtener. Solo tiene sentido con
            `tiempo=None`; es lo que distingue "falló" de "no se pidió".
        ahora: para los tests. En producción se usa el reloj.
    """
    momento = _hora_local(tiempo, ahora)
    fuentes: dict[str, Fuente] = {"ubicacion": Fuente(OK)}

    if tiempo is not None:
        fuentes["tiempo"] = Fuente(OK)
        # El oleaje se informa aparte del tiempo aunque venga en la misma
        # llamada, porque falla de forma distinta y por motivos distintos: la
        # API marina responde 200 con todo a null tierra adentro (decisión 5),
        # y eso NO es un fallo. Meterlos en la misma fuente haría que estar en
        # León pareciera una avería.
        if tiempo.marine.has_data():
            fuentes["oleaje"] = Fuente(OK)
        elif tiempo.marine.fallo:
            fuentes["oleaje"] = Fuente(FALLO, tiempo.marine.fallo)
        else:
            fuentes["oleaje"] = Fuente(
                SIN_DATOS, "Esta ubicación no está junto al mar."
            )
    else:
        fuentes["tiempo"] = Fuente(
            FALLO, fallo_tiempo or "No se pudo consultar la previsión."
        )
        # Sin tiempo no hay oleaje, y decir que "no hay datos de oleaje" sería
        # mentir por omisión: no es que no lo haya, es que no se ha llegado a
        # preguntar.
        fuentes["oleaje"] = Fuente(
            NO_CONSULTADA, "No se llegó a consultar: la previsión falló antes."
        )

    if momento.zona_es_supuesta:
        # Un fallo, no un aviso menor: significa que la hora local que se está
        # enseñando (y con la que razona el modelo) puede estar equivocada.
        fuentes["zona_horaria"] = Fuente(
            FALLO,
            f"se ha supuesto {momento.zona}; la hora local puede no ser exacta.",
        )

    # La luna nunca es None: la fase se calcula. Lo que puede faltar es la hora
    # de salida y puesta, y `fuentes` habla de eso y no de la luna entera — por
    # eso su etiqueta dice "salida y puesta" y no "la luna".
    #
    # Y los tres casos son tres, no dos: que no haya efemérides puede ser
    # porque met.no falló o porque nadie se las ha pedido (el diagnóstico, un
    # test, el chatbot razonando sobre un contexto de ayer). Colapsarlos en
    # `fallo` sacaría un aviso de una avería que no ha ocurrido — el mismo
    # error que se evita al revés con `sin_datos`.
    luna = _armar_luna(momento, tiempo, efem)
    if efem is not None:
        fuentes["luna"] = Fuente(OK)
    elif fallo_luna:
        fuentes["luna"] = Fuente(
            FALLO, f"{fallo_luna} La fase y la iluminación sí están calculadas."
        )
    else:
        fuentes["luna"] = Fuente(
            NO_CONSULTADA,
            "No se han pedido. La fase y la iluminación sí están calculadas.",
        )

    # Las métricas y el viaje son opcionales y CAROS de explicar mal, así que
    # sus tres casos van separados. Un `None` no distingue "no se pidió" de
    # "se miró y la tabla está vacía", y esa es justo la diferencia entre que el
    # chatbot calle y que diga "aún no tengo datos tuyos de hoy".
    if metricas is None:
        fuentes["metricas"] = Fuente(
            NO_CONSULTADA, "No se han pedido: la pantalla rápida no las necesita."
        )
    elif not metricas.hay_datos:
        fuentes["metricas"] = Fuente(
            SIN_DATOS, "No ha llegado ninguna muestra del móvil en los últimos días."
        )
    else:
        fuentes["metricas"] = Fuente(OK)

    if viaje is None:
        fuentes["viaje"] = Fuente(
            NO_CONSULTADA, "No se ha pedido: la pantalla rápida no lo necesita."
        )
    elif not viaje.hay_datos:
        fuentes["viaje"] = Fuente(
            SIN_DATOS, "Todavía no hay ninguna nota ni ninguna foto del viaje."
        )
    else:
        fuentes["viaje"] = Fuente(OK)

    return Contexto(
        ubicacion=ubicacion,
        momento=momento,
        tiempo=tiempo,
        luna=luna,
        metricas=metricas,
        viaje=viaje,
        fuentes=fuentes,
    )


# ---------------------------------------------------------------------------
# Entrada pública (la que hace red)
# ---------------------------------------------------------------------------

def construir(
    lat: float,
    lon: float,
    *,
    ahora: datetime | None = None,
    incluir_historia: bool = False,
) -> Contexto:
    """El estado del viaje en unas coordenadas. Rápido, gratis y sin LLM.

    `incluir_historia` añade las métricas del móvil y el resumen del viaje. Va
    detrás de un interruptor y apagado por defecto por una razón medida: la
    pantalla principal responde hoy por debajo del segundo, y lo hace porque
    pide exactamente lo que enseña. Estas dos fuentes son locales (SQLite, sin
    red) y baratas, pero con un mes de viaje dentro dejan de serlo, y la regla
    del proyecto es que lo que no se enseña no se paga. Lo enciende el chatbot,
    que sí las necesita para razonar sobre el viaje entero.

    Las dos consultas van **en paralelo**. No es micro-optimización: es la
    diferencia entre una pantalla que aparece y una que se espera. En serie se
    paga la suma de las latencias; en paralelo, la mayor de las dos. Son dos
    llamadas de red independientes, así que dos hilos bastan (esperan a la red,
    no gastan CPU).

    Las coordenadas se validan ANTES de lanzar los hilos. Si no, unas
    coordenadas basura lanzarían dos peticiones a servicios ajenos para acabar
    devolviendo un 400 igualmente.

    Raises:
        InvalidCoordinates: las coordenadas no valen (culpa de quien llama).
        LocationError: no se pudo resolver el lugar. Es la ÚNICA fuente cuyo
            fallo impide construir el contexto: sin saber dónde estás, ni el
            tiempo ni la luna ni nada significan nada. Cualquier otra fuente
            degrada y se anota en `fuentes`.
    """
    lat, lon = validate_coords(lat, lon)

    # La fecha y el desfase que met.no necesita salen de la hora local, y la
    # hora local sale de la zona que da Open-Meteo... que aún no ha contestado.
    # Se rompe el nudo con la hora del sistema: para elegir de QUÉ DÍA se piden
    # las efemérides, un desfase de dos horas solo importa a medianoche, y el
    # `Momento` definitivo (el que se enseña y con el que razona el modelo) se
    # calcula después con la zona buena. Encadenarlo al tiempo habría costado
    # poner las dos consultas en serie: el doble de espera para afinar un caso
    # que ocurre unos minutos al día.
    referencia = ahora or datetime.now(ZoneInfo(_ZONA_POR_DEFECTO))

    with ThreadPoolExecutor(max_workers=3) as pool:
        futuro_lugar = pool.submit(reverse_geocode, lat, lon)
        futuro_tiempo = pool.submit(get_weather, lat, lon)
        futuro_luna = pool.submit(efemerides, lat, lon, referencia)

        # Las opcionales se recogen PRIMERO aunque la ubicación sea la
        # importante. Al revés, un fallo de Nominatim propagaría la excepción
        # con los otros hilos aún en vuelo, y salir del `with` esperaría a que
        # terminasen: un 502 que tarda diez segundos en llegar. Recogiendo antes
        # lo que nunca lanza, cuando la excepción sale ya no queda nadie
        # corriendo.
        tiempo: Weather | None = None
        fallo_tiempo = ""
        try:
            tiempo = futuro_tiempo.result()
        except WeatherError as exc:
            fallo_tiempo = str(exc)
        except Exception:  # noqa: BLE001 - una fuente opcional nunca tumba el contexto
            fallo_tiempo = "Error inesperado al consultar la previsión."

        efem: Efemerides | None = None
        fallo_luna = ""
        try:
            efem = futuro_luna.result()
        except LunaError as exc:
            fallo_luna = str(exc)
        except Exception:  # noqa: BLE001
            fallo_luna = "Error inesperado al consultar las efemérides."

        ubicacion = futuro_lugar.result()

    metricas_ = None
    viaje_ = None
    if incluir_historia:
        # Después de los hilos y no dentro: son lecturas de SQLite local, y
        # meter la base de datos en un pool que existe para esperar a la red
        # solo añadiría contención por una conexión que no se comparte.
        # Ninguna de las dos lanza nunca; degradan a vacío.
        zona = (tiempo.timezone if tiempo else "") or _ZONA_POR_DEFECTO
        metricas_ = obtener_metricas(zona, ahora=ahora)
        viaje_ = resumen_viaje()

    return ensamblar(
        ubicacion,
        tiempo,
        fallo_tiempo=fallo_tiempo,
        efem=efem,
        fallo_luna=fallo_luna,
        metricas=metricas_,
        viaje=viaje_,
        ahora=ahora,
    )
