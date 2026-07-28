"""Siembra telemetría SIMULADA, con la misma forma que la que manda el iPhone.

Uso:
    python tools/simular_telemetria.py             # 7 días
    python tools/simular_telemetria.py 14          # 14 días
    python tools/simular_telemetria.py 14 --sin-hueco   # sin zona sin cobertura
    python tools/simular_telemetria.py --semilla 5 # otra serie, igual de repetible
    python tools/simular_telemetria.py --ver        # enseña lo que haría, sin guardar
    python tools/simular_telemetria.py --limpiar    # borra TODO lo simulado

Por qué existe
--------------
La Fase 2d está aparcada esperando días de datos reales, y esa espera bloquea
todo lo que va encima: el perfil de actividad, el dashboard y el contexto del
chatbot. El atajo ya está montado y **funciona** -- se pide una ejecución a mano
y la muestra llega completa y bien formada--, así que lo que falta no es
comprobar que el formato es correcto, es tener volumen. Esto lo fabrica.

La decisión que hace que esto sea seguro
----------------------------------------
Las muestras se guardan con `fuente = "simulado"`, **nunca** como
`atajos-iphone`. Es lo único que separa esto de ser exactamente el fallo del
que avisa la decisión 11 de `CLAUDE.md`: una serie inventada que más adelante
se lee como si fuera real y produce conclusiones equivocadas sin dar ningún
error. Con fuente propia:

- `UNIQUE(fuente, medido_en)` mantiene las dos series en paralelo, así que
  sembrar no puede tapar ni desplazar una muestra real del mismo instante;
- `tools/ver_telemetria.py` marca las filas simuladas con `~` y avisa arriba;
- `--limpiar` las borra todas de golpe y deja solo lo que llegó de verdad.

**Y la regla del proyecto no se levanta:** cuando toque decidir si la
telemetría es fiable, eso se mira sobre las filas `atajos-iphone` y sobre
ninguna otra. Esto sirve para escribir y probar lo que va encima, no para
declarar cerrada la 2d.

Qué se simula, y con qué criterio
---------------------------------
Todo pasa por `ingest.ingest()`, el mismo camino que una petición del móvil:
misma validación, mismo `INSERT OR IGNORE`, mismos recuentos. Un simulador que
escribiera directo en la tabla podría generar datos que el endpoint real
rechazaría, y entonces estaríamos construyendo sobre una forma que no existe.

- **Seis envíos al día**, en las horas de las automatizaciones que hay puestas
  (08:00, 12:00, 16:00, 18:00, 20:00 y 23:55, hora local).
- **Los pasos son acumulados del día y se reinician a medianoche**, que es lo
  que significa la columna desde que el atajo usa el filtro `es hoy`
  (decisión 25). Son monótonos dentro del día por construcción: el total del
  día es el máximo de sus muestras, y lo pone la de las 23:55.
- **Un día sin cobertura**, y se simula como lo que de verdad pasaría: las
  muestras de esas horas **no existen**, no llegan tarde. El atajo no tiene
  cola ni reintenta (decisión 23), así que un envío que falla se pierde; lo que
  se cura solo es el contador, porque el siguiente envío ya trae el acumulado
  incluyendo lo andado durante el hueco. Fabricar filas retrasadas habría
  dibujado un comportamiento que este sistema no tiene.
- **La serie es determinista** (`--semilla`). Volver a ejecutarlo con los
  mismos parámetros no duplica nada: sale `duplicadas`, que además es la
  comprobación gratis de que la idempotencia del esquema funciona.

Lo que NO hace: enviar a producción. Escribe en la base de datos local que diga
`Config.DATABASE_PATH`. Sembrar el servidor desplegado con datos inventados es
otra decisión, y tendría que tomarse a propósito y no de rebote.
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, time, timedelta, timezone
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

# Este script vive en tools/, así que Python pone tools/ en el path, no la raíz
# del proyecto. Sin esto, `from app.config import Config` falla.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules import ingest, storage  # noqa: E402  (después del sys.path, a la fuerza)

FUENTE = "simulado"

# La zona del viaje. Se usa `ZoneInfo` y no un "+02:00" fijo porque el desfase
# de España cambia el último domingo de octubre, y un viaje de un mes puede
# cruzarlo: con el desfase escrito a mano, media serie quedaría una hora
# corrida. Es la misma regla del huso horario del EXIF (decisión 30): no se
# inventa una zona, se usa la de verdad.
ZONA = ZoneInfo("Europe/Madrid")

# Las seis automatizaciones de *Hora del día* que hay puestas en el iPhone
# (§2 de docs/prompt-fase6.md). Se simulan estas y no unas horas redondas
# inventadas: la serie tiene que parecerse a la que va a llegar, no a una
# bonita.
HORAS_ENVIO: tuple[time, ...] = (
    time(8, 0),
    time(12, 0),
    time(16, 0),
    time(18, 0),
    time(20, 0),
    time(23, 55),
)

# Qué fracción del total del día se lleva andada a cada una de esas horas.
# Creciente por construcción: es lo que garantiza que los pasos nunca bajen
# dentro de un mismo día, que es la propiedad que rompería cualquier análisis
# construido encima. La curva es la de un día de viaje: poco antes del
# desayuno, el grueso entre la mañana y la tarde, y algo después de cenar.
FRACCION_ACUMULADA: tuple[float, ...] = (0.06, 0.30, 0.52, 0.66, 0.86, 1.00)

# Batería típica a cada una de esas horas: se carga de noche y baja durante el
# día. No sube nunca dentro del día porque un camper carga por la noche; si
# alguna vez importa el caso contrario (enchufado a mediodía), se añade aquí.
BATERIA_TIPICA: tuple[int, ...] = (96, 78, 63, 52, 40, 27)

# Un alto por día, de este a oeste por la costa norte. Son sitios reales: con
# coordenadas inventadas, `ver_telemetria.py` resolvería nombres absurdos y la
# tabla dejaría de parecerse a la que se va a leer de verdad.
RUTA: tuple[tuple[float, float, str], ...] = (
    (43.3183, -1.9812, "San Sebastián"),
    (43.4106, -3.4110, "Laredo"),
    (43.4623, -3.8100, "Santander"),
    (43.1540, -4.6220, "Potes"),
    (43.4210, -4.7554, "Llanes"),
    (43.5453, -5.6619, "Gijón"),
    (43.5622, -6.1456, "Cudillero"),
    (43.5424, -6.5372, "Luarca"),
    (43.5366, -7.0410, "Ribadeo"),
    (43.6620, -7.5930, "Viveiro"),
    (43.3623, -8.4115, "A Coruña"),
    (42.9083, -9.2646, "Finisterre"),
    (42.7767, -9.0600, "Muros"),
    (42.2406, -8.7207, "Vigo"),
)

# Cuánto se mueve uno dentro del mismo alto. ~0,003° son unos 300 m: lo que se
# anda alrededor de donde está aparcado el camper. Más que eso convertiría cada
# día en un viaje, y menos haría que las seis muestras cayeran en el mismo
# punto exacto, que tampoco es real.
JITTER_GRADOS = 0.003

# El máximo que acepta la validación es 30 días (`timeparse.MAX_PASADO`). Se
# corta en 29 y no en 30 para no depender de en qué segundo se ejecute esto:
# justo en el borde, las muestras más antiguas del lote se descartarían y el
# primer día saldría a medias sin que se entienda por qué.
MAX_DIAS = 29


class Envio(NamedTuple):
    """Una ejecución del atajo: un instante de recepción y su muestra.

    Se modela el envío y no la muestra suelta porque `recibido_en` es propiedad
    del envío, no de la medida. Hoy cada envío lleva una sola muestra, que es lo
    que hace el atajo; el tipo admite varias sin cambiar nada el día que vuelva
    a haber ventana solapada.
    """

    recibido_en: str
    muestras: list[dict[str, Any]]


def _canonico_utc(momento: datetime) -> str:
    """Instante en UTC con precisión de segundos, como lo guarda la tabla."""
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat(
        timespec="seconds"
    )


def generar_envios(
    dias: int,
    ahora: datetime,
    semilla: int = 20260728,
    con_hueco: bool = True,
) -> list[Envio]:
    """Construye la serie. Función pura: no toca la base de datos ni el reloj.

    `ahora` se recibe en vez de leerse aquí para que sea comprobable: los tests
    fijan un instante y verifican que los pasos no bajan dentro de un día, que
    se reinician a medianoche y que no se inventa ninguna muestra en el futuro.
    Es la misma razón por la que `ensamblar()` del contexto es pura y
    `construir()` no (decisión 32).
    """
    dias = max(1, min(dias, MAX_DIAS))
    rng = random.Random(semilla)

    hoy = ahora.astimezone(ZONA).date()
    primer_dia = hoy - timedelta(days=dias - 1)

    # El hueco de cobertura cae en el antepenúltimo día, a media jornada. Se
    # fija en vez de sortearse para que la serie siga siendo reproducible y
    # para poder decir en la salida exactamente qué día mirar.
    dia_del_hueco = dias - 3 if (con_hueco and dias >= 3) else None
    horas_del_hueco = {1, 2}  # 12:00 y 16:00

    envios: list[Envio] = []

    for indice_dia in range(dias):
        fecha = primer_dia + timedelta(days=indice_dia)
        lat_base, lon_base, _ = RUTA[indice_dia % len(RUTA)]

        # Un día de viaje anda entre 4.000 y 18.000 pasos: hay días de ruta y
        # días de playa, y una serie en la que todos los días son iguales no
        # sirve para probar nada que dependa de distinguirlos.
        total_pasos = rng.randint(4_000, 18_000)

        for indice_hora, hora in enumerate(HORAS_ENVIO):
            if dia_del_hueco == indice_dia and indice_hora in horas_del_hueco:
                # Sin cobertura: el envío falla y esa muestra no existe. No se
                # guarda "más tarde": el atajo no reintenta.
                continue

            medido = datetime.combine(fecha, hora, tzinfo=ZONA)
            if medido > ahora:
                # Nunca se siembra el futuro. La validación lo toleraría hasta
                # 24 h, pero una muestra "medida" dentro de tres horas es una
                # mentira que además rompería el "total del día es el máximo".
                continue

            pasos = round(total_pasos * FRACCION_ACUMULADA[indice_hora])
            bateria = max(3, min(100, BATERIA_TIPICA[indice_hora] + rng.randint(-6, 6)))

            muestra = {
                "medido_en": medido.isoformat(timespec="seconds"),
                "pasos": pasos,
                "bateria": bateria,
                "lat": round(lat_base + rng.uniform(-JITTER_GRADOS, JITTER_GRADOS), 5),
                "lon": round(lon_base + rng.uniform(-JITTER_GRADOS, JITTER_GRADOS), 5),
            }

            # El envío tarda unos segundos en salir y llegar: la automatización
            # no dispara en el segundo exacto y la petición viaja por LTE. Sin
            # este ruido, `retraso` saldría a cero clavado en todas las filas,
            # que es lo único que no se ve nunca en los datos reales.
            recibido = medido + timedelta(seconds=rng.randint(3, 170))
            envios.append(Envio(_canonico_utc(recibido), [muestra]))

    return envios


def _sembrar(envios: list[Envio]) -> tuple[int, int, int]:
    """Mete la serie por el mismo camino que una petición del móvil."""
    guardadas = duplicadas = descartadas = 0
    for envio in envios:
        resultado = ingest.ingest(
            {"fuente": FUENTE, "muestras": envio.muestras},
            recibido_en=envio.recibido_en,
        )
        guardadas += resultado.guardadas
        duplicadas += resultado.duplicadas
        descartadas += resultado.descartadas
        for error in resultado.errores:
            # Que un descarte no pase desapercibido: significa que el simulador
            # está generando algo que el endpoint real rechazaría, y entonces la
            # serie no vale para lo que se quiere.
            print(f"  descartada: {error}")
    return guardadas, duplicadas, descartadas


def _motivo_de_los_que_faltan(indices_presentes: set[int]) -> str:
    """Por qué a un día le faltan envíos. Distinguirlo NO es cosmético.

    Un día al que le faltan las muestras del final está simplemente **en
    curso**: son las 23:11 y la de las 23:55 no ha ocurrido todavía. Un día al
    que le falta algo por en medio es el hueco de cobertura. Llamar "sin
    cobertura" a los dos haría creer que el simulador pierde datos cada día que
    se ejecuta, y a partir de ahí nadie se fiaría de la única señal que esta
    columna tiene que dar.
    """
    faltan = sorted(set(range(len(HORAS_ENVIO))) - indices_presentes)
    if not faltan:
        return ""
    # ¿Son todos los del final? Entonces el día no ha terminado.
    if faltan == list(range(len(HORAS_ENVIO) - len(faltan), len(HORAS_ENVIO))):
        return "  ← el día aún no ha terminado"
    return f"  ← {len(faltan)} envíos perdidos (sin cobertura)"


def _resumir(envios: list[Envio]) -> None:
    """Enseña la serie por días, que es como se va a leer."""
    por_dia: dict[str, list[dict[str, Any]]] = {}
    for envio in envios:
        for muestra in envio.muestras:
            dia = muestra["medido_en"][:10]
            por_dia.setdefault(dia, []).append(muestra)

    # De la hora local de cada muestra al envío al que corresponde, para poder
    # saber CUÁLES faltan y no solo cuántos.
    indice_de_hora = {hora.isoformat(): i for i, hora in enumerate(HORAS_ENVIO)}

    print(f"\n{'día':<12} {'envíos':>7} {'pasos del día':>14}  dónde")
    print("-" * 60)
    for dia, muestras in sorted(por_dia.items()):
        # El total del día es el MÁXIMO de sus muestras, no la suma: la columna
        # es un acumulado que se reinicia a medianoche (decisión 25). Sumarla
        # daría cerca del cuádruple, y sin dar ningún error.
        total = max(m["pasos"] for m in muestras)
        lat, lon = muestras[0]["lat"], muestras[0]["lon"]
        sitio = min(RUTA, key=lambda p: abs(p[0] - lat) + abs(p[1] - lon))[2]
        presentes = {
            indice_de_hora[m["medido_en"][11:19]]
            for m in muestras
            if m["medido_en"][11:19] in indice_de_hora
        }
        aviso = _motivo_de_los_que_faltan(presentes)
        print(f"{dia:<12} {len(muestras):>7} {total:>14,}  {sitio}{aviso}")


def _limpiar() -> None:
    borradas = storage.delete_telemetry_by_source(FUENTE)
    print(f"\nBorradas {borradas} muestras simuladas.")
    print("Las muestras reales (atajos-iphone) siguen intactas.\n")


def main() -> None:
    argumentos = sys.argv[1:]

    if "--limpiar" in argumentos:
        storage.init_db()
        _limpiar()
        return

    solo_ver = "--ver" in argumentos
    con_hueco = "--sin-hueco" not in argumentos
    semilla = 20260728

    if "--semilla" in argumentos:
        posicion = argumentos.index("--semilla")
        if posicion + 1 >= len(argumentos):
            print("Falta el número. Ejemplo: --semilla 5")
            sys.exit(1)
        try:
            semilla = int(argumentos[posicion + 1])
        except ValueError:
            print(f"'{argumentos[posicion + 1]}' no es un número.")
            sys.exit(1)
        argumentos = argumentos[:posicion] + argumentos[posicion + 2 :]

    posicionales = [a for a in argumentos if not a.startswith("--")]
    dias = 7
    if posicionales:
        try:
            dias = int(posicionales[0])
        except ValueError:
            print(f"'{posicionales[0]}' no es un número de días.")
            sys.exit(1)

    if dias > MAX_DIAS:
        # No se recorta en silencio: una serie de 60 días que sale con 29 y sin
        # decirlo haría buscar el bug en otro sitio.
        print(
            f"\nEl máximo son {MAX_DIAS} días: la validación de `ingest` rechaza "
            f"cualquier medida de hace más de {ingest.MAX_PASADO.days} días.\n"
            f"Se generan {MAX_DIAS}."
        )

    ahora = datetime.now(timezone.utc)
    envios = generar_envios(dias, ahora, semilla=semilla, con_hueco=con_hueco)

    print(f"\nSerie simulada: {len(envios)} envíos, semilla {semilla}")
    _resumir(envios)

    if solo_ver:
        print("\n--ver: no se ha guardado nada.\n")
        return

    storage.init_db()
    guardadas, duplicadas, descartadas = _sembrar(envios)

    print(
        f"\nGuardadas: {guardadas}   duplicadas: {duplicadas}   descartadas: {descartadas}"
    )
    if duplicadas and not guardadas:
        print("Ya estaban todas: la misma semilla genera la misma serie.")
    print(
        "\nEstas muestras son INVENTADAS y llevan fuente 'simulado'.\n"
        "  Verlas:   python tools/ver_telemetria.py 50\n"
        "  Borrarlas: python tools/simular_telemetria.py --limpiar\n"
    )


if __name__ == "__main__":
    main()
