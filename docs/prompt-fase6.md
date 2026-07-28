# Fase 6 — Que los datos sean ciertos, y luego el chatbot

Encargo de la fase. Lee `CLAUDE.md` antes de empezar: sus reglas mandan sobre
este documento si hay conflicto.

---

## 0. De dónde venimos, medido el 28-07-2026

Todo esto está comprobado contra el servidor desplegado y contra un iPhone real,
no supuesto. No hace falta redescubrirlo.

| Fase | Estado |
|---|---|
| 1, 2, 2b, 2c | ✅ Cerradas |
| **2d** (telemetría) | 🟥 **NO cerrada.** Automatizaciones puestas hoy; ver §2 |
| 3 (notas + mapa) | 🟨 Hecha, sin cerrar: **0 notas escritas** |
| 3b (ruta de las fotos) | ✅ Cerrada |
| **5** (contexto, Overpass, luna, pantalla) | 🟨 **Hecha y desplegada**, sin cerrar del todo; ver §4 |

Estado del servidor (`tools/diagnostico.py`, en PythonAnywhere, con el
virtualenv activado):

```
telemetría....... 0 muestras (borradas a propósito, ver §1)
notas............ 0
puntos de fotos.. 4 puntos, 4 con GPS
lugar del día.... 0 días
Nominatim........ OK   (0.0 s)
Open-Meteo....... OK   (0.9 s)
luna: fase....... OK   (calculada, sin red)
api.met.no....... OK   (0.5 s)   ← pasa la lista blanca del proxy
Overpass......... OK   (4.5 s)   ← hoy vivo; es intermitente
IA: gemini....... OK   (13.0 s)
LLM_PROVIDER=gemini
```

Medido en el móvil, con LTE y contra el servidor: `/api/contexto` responde
**por debajo de un segundo**, y las salidas de la luna coinciden con la
referencia externa (tutiempo.net: salida 20:54).

---

## 1. LO PRIMERO: los pasos se cuentan dos veces

**Es la tarea principal de la fase y va antes que cualquier otra cosa.** No es
una mejora: es que el dato que se está guardando **es falso**.

```
App Salud (pasos de hoy, 28-07-2026) .......  5.428
Lo que enviaba el atajo .................... 10.675
```

Casi exactamente el doble. Con más de una fuente escribiendo pasos en HealthKit
—lo normal en cuanto hay un **Apple Watch**—, las muestras de cada dispositivo se
guardan por separado. La app Salud enseña el total ya deduplicado; el atajo hace
`Buscar muestras de salud` + `Calcular Suma` sobre las muestras crudas, **y las
suma todas**.

**No da ningún error.** Es el fallo silencioso de la decisión 11 en la fuente
sobre la que iba a construirse el perfil de actividad.

### Qué hay que hacer

- En el atajo, `Buscar muestras de salud` → *Añadir filtro* → **`Origen`** → un
  **solo** dispositivo. Cuál conviene es una decisión con contrapartida y hay que
  dejarla escrita: el **reloj** es más preciso pero solo cuenta cuando se lleva
  puesto; el **iPhone** cuenta siempre que vaya en el bolsillo. Para un viaje en
  camper de un mes, probablemente el iPhone.
- **La comprobación que lo cierra, y que hay que repetir en cualquier móvil
  nuevo:** ejecutar el atajo y comparar con lo que enseña la app Salud para hoy.
  Si no coinciden, no se sigue.
- Y **borrar lo que se haya acumulado antes del arreglo**. Mezclar días al doble
  con días buenos en la misma columna es peor que no tener datos.

> **Corolario que vale para todo lo que venga después** (Hevy, sueño, frecuencia
> cardíaca): antes de guardar una métrica nueva hay que **contrastarla contra la
> app que ya la enseña**. Una API que responde 200 no garantiza que el número
> signifique lo que crees — es la decisión 5 (Open-Meteo marino), la 20 (Kimi) y
> ahora también HealthKit.

---

## 2. Cerrar la Fase 2d: ahora solo falta esperar

El 28-07-2026 se montaron **seis automatizaciones** de *Hora del día* apuntando a
`Enviar telemetría`: **08:00, 12:00, 16:00, 20:00 y 23:55**, más una a las 18:00
que quedó suelta. Y se comprobó que **disparan solas**: la muestra `id 24` entró
a las `16:00:01` UTC con su batería y sus coordenadas, sin que nadie tocara el
móvil.

También se cambió qué significa `pasos`: antes era una **ventana rodante de 24 h**
(el filtro `los últimos 1 día`), que no es ni el día ni el viaje; ahora el filtro
es **`es hoy`**, así que cada muestra dice *pasos de hoy hasta este momento*, se
reinicia sola a medianoche y **el total del día es el máximo de sus muestras** —
que lo pone la de las 23:55.

**El criterio de cierre no ha cambiado y no se negocia:** que lleguen datos solos
y sin huecos durante varios días, y que la columna `retraso` enseñe alguna
recuperación real tras pasar por una zona sin cobertura. Se mira con:

```bash
python tools/ver_telemetria.py 50
```

Mientras eso no esté demostrado **y** el §1 no esté arreglado, sigue sin
construirse ningún análisis encima.

---

## 3. El chatbot

Es lo que la Fase 5 dejó preparado y no montó. La pieza que hacía falta ya
existe, está probada y está en producción: **`contexto.construir(lat, lon)`**
devuelve el estado del viaje en un formato que un modelo puede leer, y
`ai_orchestrator.formatear_para_prompt()` lo renderiza como texto.

Así que el chatbot es **conectar esas dos piezas a `llm_providers`**, no
construir nada nuevo. Lo que sí hay que decidir:

- **Qué más entra en el contexto del chatbot.** Hoy `Contexto` lleva ubicación,
  momento, tiempo y luna. El chatbot querrá además **las notas y la ruta** —que
  ya existen y son la única fuente demostrada— y, cuando el §1 y el §2 estén
  cerrados, las métricas. Añadirlo es un campo más en el dataclass; el sitio ya
  está.
- **Si hay historial de conversación y dónde vive.** Un chatbot sin memoria de
  los tres mensajes anteriores es un buscador con otra cara. Pero guardar
  conversaciones es una tabla nueva y una decisión de retención, así que hay que
  pensarla antes y no después.
- **Qué pasa con el coste.** Cada mensaje es una llamada al modelo. La decisión
  12 (sin reintento ante un 429) sigue aplicando aquí, porque hay alguien
  esperando.

---

## 4. Lo que la Fase 5 dejó sin cerrar

Está **hecha y desplegada**, y funciona en el iPhone. Lo que falta:

- **La presentación del tiempo.** Aplazada a propósito por el usuario ("eso con
  el tiempo"): los datos primero, la estética después. No se invierta el orden.
- **`/api/recommendations` devuelve `place` y `weather` duplicados**, en la raíz
  y dentro de `contexto`. Fue deuda deliberada para no romper el frontend a
  mitad de fase. Ya no los usa nadie desde que la pantalla pinta con
  `renderContexto`: se quitan en un commit.
- **Las tres acciones sobrantes del atajo** (`Fecha actual`, `Obtener inicio del
  día`, `INICIO_DIA`), que quedaron inútiles al pasar a `es hoy`. Inofensivas,
  pero sobran.
- **El aviso de disco sigue roto**, y esto viene del §8 del encargo anterior sin
  hacer: `tools/diagnostico.py` informa `1610543 MB libres` en PythonAnywhere
  (1,6 TB), o sea que lee el sistema de archivos subyacente y **no la cuota de
  512 MB** de la cuenta. El aviso de "por debajo de 50 MB" **no va a saltar
  nunca**, y el plan de miniaturas depende de él. Hay que medir lo que ocupa lo
  nuestro (`du` sobre el home) en vez de preguntar por el volumen.

---

## 5. `lugar_del_dia`: registrado, no analizado

La Fase 5 empezó a guardar dónde estabas la primera vez que abriste la app cada
día (tabla `lugar_del_dia`, módulo `diario.py`). Se le aplica **la misma vara de
medir que a la telemetría**: es un dato nuevo, así que no se construye nada
encima hasta que demuestre que llega sin huecos. `tools/diagnostico.py` enseña
los **huecos** y no el total, precisamente porque un total alto con huecos no es
una serie.

---

## 6. Alcance: qué NO se hace

- **Ningún análisis sobre la telemetría** mientras el §1 y el §2 no estén
  cerrados. Ni gráficas, ni perfil, ni resúmenes.
- **Ni compartir, ni exportar, ni multiusuario.**
- **Ninguna edición de notas ni de puntos desde la web.**
- **Rediseño visual completo.** Sigue valiendo lo de siempre: los datos primero.
- **Los POIs no se pintan en `/mapa`.** Ese mapa es el registro de dónde has
  estado; los POIs son dónde podrías ir. Ya está razonado en la decisión 33.

---

## 7. Verificación

- **Tests sin red y sin API keys.** La suite son **434** y `tests/conftest.py`
  **corta los sockets**, así que "sin red" ya no es una promesa sino algo que
  falla ruidosamente si alguien lo incumple.
- **Los pasos, contra la app Salud.** Es la única comprobación que cierra el §1,
  y no la puede hacer la suite: hay que mirar el móvil.
- **Los huecos, contra el calendario.** `ver_telemetria.py 50` y contar: seis
  muestras por día, todos los días.
- **Si se toca el contexto, medir otra vez.** Hoy `/api/contexto` está por debajo
  de un segundo. Si sube de dos, algo ha vuelto al camino normal que no debía.

---

## 8. Documentación

- **`CLAUDE.md`**: decisiones nuevas y tabla de estado.
- **`docs/atajo-iphone.md`**: ya está corregido con `es hoy` y con la trampa del
  doble conteo. Si se toca el atajo, se actualiza **a la vez**, no después: hoy
  ese documento describía durante meses un atajo que no existía.
- **`README.md`** y **`.env.example`** si aparecen rutas o variables nuevas.

---

## 9. Cómo trabajar

1. **Empieza por el §1.** Todo lo demás construye encima de esos datos.
2. **Un cambio cada vez, y comprobar en medio.** La tarde del 28-07 se perdió una
   hora buscando un chip roto que no existía, porque se cambiaron varias cosas a
   la vez y el síntoma (una variable vacía) tenía dos causas posibles.
3. **Antes de diseñar encima de una herramienta, mira qué sabe hacer.** Se montó
   un rodeo de tres acciones y aritmética de fechas para obtener "los pasos de
   hoy", y Atajos ya traía un filtro **`es hoy`**. La regla de verificar en vez
   de suponer no es solo para las APIs.
4. **La suite en verde y commit al terminar cada parte.** Es lo que garantiza que
   siempre haya una versión usable a la que volver con `git reset --hard`.
5. **No amplíes el alcance.** Lo que merezca la pena y no esté aquí, se anota al
   final.
6. Cuando decidas algo no obvio, **dilo y explica la alternativa que descartas**.
