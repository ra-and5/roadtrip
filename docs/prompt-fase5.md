# Fase 5 — El contexto, y que se vea

Encargo de la fase. Lee `CLAUDE.md` antes de empezar: sus reglas mandan sobre
este documento si hay conflicto.

---

## 0. De dónde venimos, medido el 28-07-2026

No hace falta redescubrir nada de esto. Todo está comprobado contra el servidor
desplegado, no supuesto.

| Fase | Estado |
|---|---|
| 1, 2, 2b, 2c | ✅ Cerradas |
| **2d** (telemetría) | 🟥 **NO cerrada.** Ver §1 |
| 3 (notas + mapa) | 🟨 Hecha, sin cerrar: **0 notas escritas**, ninguna desde el móvil |
| **3b** (ruta de las fotos) | ✅ **Cerrada** con el atajo del álbum, y automatizada |

Estado del servidor (`tools/diagnostico.py`, en PythonAnywhere):

```
telemetría....... 8 muestras, última 2026-07-28T13:18:17
notas............ 0
puntos de fotos.. 4 puntos, 4 con GPS, de 2026-07-20 a 2026-07-26
Nominatim........ OK   (0.0 s)
Open-Meteo....... OK   (1.0 s)
Overpass......... FALLO (31.3 s)   ← los tres espejos
IA: gemini....... OK   (11.8 s)
LLM_PROVIDER=gemini
```

---

## 1. El veredicto de la telemetría: la 2d sigue sin cerrarse

**No se hace el perfil de pasos y batería.** No se negocia, y aquí está el dato:

```
id  medido_en (UTC)             pasos   bat
23  2026-07-28T13:18:17         12634   48%      ← 12,5 h después
22  2026-07-28T00:48:50         12427   11%
21  2026-07-28T00:13:05         12427   12%
20  2026-07-28T00:12:40         12427   13%
19  2026-07-27T23:52:37         12427   13%
18  2026-07-27T23:52:17         12427   13%
17  2026-07-27T23:51:57         12427   13%
16  2026-07-27T23:32:11         12427   15%
```

Siete de las ocho muestras caen en **1 hora y 16 minutos** de la misma noche, tres
de ellas separadas por **20 segundos**. Eso no es una automatización: es alguien
pulsando el botón mientras montaba el atajo. Después, un hueco de **12,5 horas** y
una sola muestra.

Los pasos confirman lo mismo: `12427` clavado en siete muestras seguidas. Y la
columna `retraso` está a `0 min` en todas, así que **nunca se ha ejercitado una
recuperación tras quedarse sin cobertura**, que es justamente lo que había que
demostrar.

El criterio de cierre no ha cambiado: **que lleguen datos solos y sin huecos
durante varios días.** Lo que falta no es código, es dejarlo corriendo.

> **Acción para el usuario, no para quien programe:** crear las automatizaciones
> de *Hora del día* del atajo `Enviar telemetría`, igual que se hizo con el de
> fotos. iOS solo las tiene diarias, así que hacen falta **una por cada hora de
> envío**; con la ventana solapada, cada 2-3 h basta (subiendo `VENTANA`).
> Ver [`atajo-iphone.md`](atajo-iphone.md) §6.

**Consecuencia para esta fase:** el dashboard se construye con lo que **sí** está
demostrado —ruta, fotos, ubicación, tiempo, luna, notas— y deja el hueco de pasos
y batería preparado pero vacío. El día que la 2d cierre, se rellena sin tocar la
pantalla.

---

## 2. LO PRIMERO: partir `/api/recommendations` en dos

**Es la tarea principal de la fase y va antes que cualquier pantalla.**

Hoy una sola petición hace cuatro cosas: resuelve la ubicación, pide el tiempo,
pide los POIs y llama al modelo. Eso produce tres problemas que **se arreglan
todos con la misma separación**:

1. **No se puede mirar dónde estás y qué tiempo hace sin pagar una
   recomendación.** El usuario pidió que la IA fuera opcional; ya lo es (todo
   cuelga de un botón), pero el contexto no se puede pedir por separado.
2. **La pantalla tarda lo que tarde el peor proveedor.** Medido: Overpass **31 s**
   fallando, más ~12 s del modelo. La ubicación tarda 0,0 s y el tiempo 1,0 s.
3. **No existe la pieza que necesita el chatbot.** El §6 del encargo de la Fase 4
   ya pedía "una función que devuelva el contexto del viaje en un formato que un
   modelo pueda leer". Es exactamente esto.

### Lo que hay que construir

- **`/api/contexto`** — rápido, gratis, sin LLM. Ubicación, tiempo, luna, y (si
  la 2d cierra) las métricas del día. Es lo que pinta la pantalla al abrirla.
- **`/api/recommendations`** — se queda como está, bajo botón, y **recibe el
  contexto ya construido** en vez de volver a resolverlo.
- **`consultas.py`** (o el nombre que toque) — el módulo con la función pura que
  arma ese contexto. Una sola definición, tres consumidores: pantalla, chatbot y
  recomendador. Si cada uno arma el suyo, divergirán, que es el razonamiento de
  la decisión 10 aplicado al contexto en vez de al proveedor.

**Decidir y dejar escrito:** qué pasa cuando una fuente del contexto falla. La
respuesta previsible es la decisión 9 —degradar y avisar—, pero hay que decir si
`/api/contexto` puede devolver `200` con partes vacías o tiene que fallar.
Recuerda las decisiones 5 y 20: **un `200` no significa que la respuesta sirva**.

---

## 3. Overpass: quitar la fuente, no el aviso

El usuario pidió "quitar los avisos porque no funcionan". **El aviso tiene razón**
y quitarlo sería el error: la decisión 9 dice que una app que oculta que le falta
la mitad del contexto no es fiable, es opaca.

Lo que hay que quitar es **la fuente**, que está muerta y medida: los tres
espejos fallan y cuestan **31,3 s por petición** (decisión 22). Ese es el 70 % de
lo que tarda la pantalla, gastado en no obtener nada.

Elige y explica la alternativa que descartas:

- **Sacar Overpass del camino normal** y dejarlo bajo botón (*"buscar sitios
  cerca"*), donde esperar 30 s es una decisión del usuario y no un peaje.
- **Quitarlo del todo** hasta que haya espejos vivos.

Lo que **no** vale es dejar la llamada y silenciar el aviso: convertiría un fallo
ruidoso en uno silencioso, que es lo que se evitó a propósito al descartar el
espejo suizo que respondía `200` con cero elementos.

Si se queda, el criterio para validar un espejo sigue siendo el de la decisión
22: **que devuelva elementos para una coordenada española conocida**, no que
responda `200`.

---

## 4. La luna

El usuario la quiere completa: fase, iluminación, salida y puesta. Hay dos
caminos y **los dos están verificados contra la realidad**, así que la decisión
es de diseño, no de disponibilidad.

### Lo comprobado el 28-07-2026

**Open-Meteo NO tiene datos de luna.** Comprobado contra la API real:

```
daily=moonrise → {"error":true,"reason":"... invalid String value moonrise"}
```

**`api.met.no` sí, y está EN LA LISTA BLANCA de PythonAnywhere.** Verificado
sobre el HTML de la página de la lista, como se hizo con Kimi (decisión 21):
aparecen `api.met.no`, `frost.met.no` y `eklima.met.no`. Respuesta real:

```json
GET https://api.met.no/weatherapi/sunrise/3.0/moon?lat=38.39&lon=-0.52&date=2026-07-28&offset=+02:00
{"properties":{
  "moonrise":{"time":"2026-07-28T20:54+02:00","azimuth":120.88},
  "moonset": {"time":"2026-07-28T05:33+02:00","azimuth":236.59},
  "high_moon":{"time":"2026-07-29T01:42+02:00","disc_centre_elevation":27.81},
  "moonphase":162.1}}
```

Contrastado con la referencia que trajo el usuario (tutiempo.net, Villajoyosa,
28 de julio): salida **20:54** — coincide exactamente. Y `moonphase: 162.1` da
una iluminación de `(1-cos 162,1°)/2 = 97,58 %`, contra el **97,56 %** de la
referencia. La fuente es buena.

Exige cabecera `User-Agent` con un contacto; sin ella deniega.

### La decisión que hay que tomar

|  | Calcular en Python | `api.met.no` |
|---|---|---|
| Fase e iluminación | Trivial, exacto | Lo da hecho |
| Salida y puesta | Bastante más código | Lo da hecho |
| Azimut y elevación | No | Lo da hecho |
| Sin cobertura | **Siempre funciona** | No hay dato |
| Tests | Sin red, por construcción | Hay que doblar la API |
| Dependencia de terceros | Ninguna | Una más que se puede caer |

**Recomendación: híbrido, y no por indecisión.** Fase e iluminación **en Python**,
porque son treinta líneas de aritmética exacta y porque en un camper sin
cobertura seguir sabiendo qué luna hay esta noche es justo cuando más sirve.
Salida, puesta y azimut **de met.no**, degradando como cualquier otra fuente
(decisión 9). Si se elige otra cosa, escribir por qué.

Y la trampa a evitar, que es la decisión 5 otra vez: **el veredicto se calcula en
Python, no en el prompt.** "Luna llena y despejado: se puede caminar de noche" es
una regla explícita y testeable, no algo que se le pregunta al modelo.

---

## 5. La pantalla principal

- **Fuera las coordenadas crudas.** `38.39099, -0.52101 · ±1020 m` no le dice
  nada a nadie. Se queda el nombre del pueblo, la comunidad y la altitud.
  El dato de precisión puede seguir existiendo en el detalle o en el log, pero no
  preside la tarjeta.
- **Que quede constancia de la ubicación de la primera petición del día.** Lo
  pidió el usuario explícitamente. Decidir dónde vive eso: es un dato nuevo, así
  que aplica la regla del §1 —tiene que llegar solo y sin huecos para poder
  construir encima—.
- **El tiempo, mejor presentado.** El usuario lo aplaza a propósito ("eso con el
  tiempo"): **los datos primero, la estética después**. No se invierta el orden.
- **El hueco de pasos y batería**, preparado y vacío mientras la 2d no cierre.

---

## 6. Lo que se deja preparado y NO se monta

El **chatbot** sigue siendo la fase siguiente. Lo que esta tiene que dejar listo
es la función de contexto del §2, probada. Si eso queda hecho, el chatbot es
conectarla a `llm_providers`.

---

## 7. Alcance: qué NO se hace

- **El perfil de pasos y batería**, mientras la 2d no cierre (§1).
- **Nada de gráficas** hasta que haya datos que dibujar. Un gráfico de tres
  puntos miente más que una tabla.
- **Ni compartir, ni exportar, ni multiusuario.**
- **Ninguna edición de notas ni de puntos desde la web.**
- **Rediseño visual completo.** Esta fase ordena datos; la estética viene después.

---

## 8. Dos cosas que salieron midiendo y hay que arreglar

**1. El aviso de disco no funciona en el servidor.** `tools/diagnostico.py`
informa `1610971 MB libres` en PythonAnywhere, o sea 1,6 TB: está leyendo el
sistema de archivos subyacente, no la **cuota de 512 MB** de la cuenta, que es un
límite impuesto aparte. Así que el aviso "por debajo de 50 MB" **no va a saltar
nunca**, y el plan de miniaturas de la Fase 4 depende de él. Hay que medir lo que
ocupa lo nuestro (`du` sobre el home) en vez de preguntar por el volumen.

**2. `CLAUDE.md` dice que el proveedor activo es Kimi y el servidor corre
Gemini.** El diagnóstico dice `LLM_PROVIDER=gemini`. Corregir el documento, o el
`.env`, según cuál sea la intención.

---

## 9. Verificación

- **Tests sin red y sin API keys**, con el test client de Flask:
  - `/api/contexto` responde sin llamar a ningún LLM. Un `FakeProvider` que
    explote si lo invocan lo deja fijado.
  - El contexto degrada: con el tiempo caído sigue devolviendo ubicación y luna,
    y lo dice en `warnings`.
  - La fase lunar calculada en Python, contra valores conocidos (la del
    28-07-2026 es 97,6 % de iluminación).
  - El token de ingesta no abre las rutas de sesión, y al revés.
- **Medir la pantalla antes y después.** Hoy tarda ~43 s en el peor caso (31 s de
  Overpass + 12 s del modelo). Si tras la fase `/api/contexto` no baja de 2 s, la
  separación no ha servido para nada y hay que decirlo.

---

## 10. Documentación

- **`CLAUDE.md`**: decisiones nuevas (la separación del contexto, qué se hace con
  Overpass, el híbrido de la luna y por qué). Tabla de estado y arquitectura.
- **`README.md`**: rutas nuevas y variables nuevas.
- **`docs/troubleshooting.md`**: síntomas nuevos.
- **`.env.example`**: las variables nuevas con su explicación.

---

## 11. Cómo trabajar

1. **Empieza por el §2.** Si la separación del contexto queda bien, lo demás cae
   solo. Si queda mal, la pantalla y el chatbot heredan el problema.
2. **Antes de escribir código**, enseña la forma del contexto: qué campos lleva y
   qué pasa cuando falta cada uno. Si eso está mal, lo demás no importa.
3. Implementa por partes con la suite en verde, y **commit al terminar cada
   parte**.
4. Al terminar, di **qué has verificado de verdad y qué no**.
5. **No amplíes el alcance.** Lo que merezca la pena y no esté aquí, se anota al
   final.
6. Cuando estés a punto de decidir algo no obvio, **dilo y explica la alternativa
   que descartas**.
