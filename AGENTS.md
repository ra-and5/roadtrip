AGENTS.md


Documento de trabajo del proyecto. Lo lee Codex Code al empezar cada sesión.

---

## 1. Qué es esto

Aplicación web (PWA) para un viaje de un mes por el norte de España en coche
camperizado. Usa el GPS del móvil para saber dónde estás, recomienda qué hacer
con ayuda de un LLM, guarda notas geolocalizadas y construye el mapa acumulado
del viaje.

Es un proyecto de portfolio **que se va a usar de verdad durante el viaje**. Eso
condiciona todo: la fiabilidad con mala cobertura importa más que las features.

### Hacia dónde va: el objetivo

Conviene tenerlo escrito porque ordena todas las decisiones que quedan.

Esto acaba siendo **un cuaderno de a bordo** que sirve para dos cosas distintas,
y hoy están repartidas por pantallas sueltas:

- **Decidir**, en el momento. *¿Qué hago hoy?* respondido con el contexto que de
  verdad importa: dónde estoy, qué tiempo hace y qué dice el mar, cuánto he
  andado, cómo he dormido, qué luna hay esta noche. Un asistente que sabe todo
  eso recomienda otra cosa que uno que solo sabe la temperatura.
- **Recordar**, después. Revivir el viaje entero: la ruta que salió de las fotos,
  las notas como diario, un relato que lo cuente. Que quede constancia.

Las dos se apoyan en lo mismo: **un contexto único, fiable y consultable**. Por
eso la pieza central no es una pantalla, es **una función que devuelve el estado
del viaje en un formato que un modelo pueda leer**. De ahí salen las tres caras:

| Cara | Qué es | Para qué |
|---|---|---|
| **Dashboard** | Los datos crudos, bien enseñados | Que *tú* veas y decidas |
| **Chatbot** | El mismo contexto, conversable | Preguntar en vez de buscar |
| **Relato y mapa** | El viaje contado y dibujado | Revivirlo |

**Y la regla que lo ordena todo, que ya está pagada con experiencia propia:**
*un dato no entra en el dashboard ni en el contexto del modelo hasta que ha
demostrado que llega solo y sin huecos.* No es purismo: construir análisis sobre
una fuente que aún no se ha demostrado es trabajo que hay que tirar, y peor,
produce conclusiones equivocadas sin dar ningún error (decisión 11). Por eso la
Fase 2d sigue aparcada y por eso el progreso del mapa se calcula solo con notas.

La consecuencia práctica: **la UI bonita va al final, no al principio.** Un
dashboard precioso sobre datos que llegan a ratos es peor que una tabla fea
sobre datos ciertos, porque el primero se cree.

## 2. Cómo trabajamos

- **Fases cerradas.** No se empieza una fase sin la anterior funcionando de
  extremo a extremo. Antes que una feature avanzada a medias, algo completo.
- **Verificar, no suponer.** Las APIs externas se comprueban contra la API real
  antes de escribir el módulo que las consume. Varios bugs de este proyecto
  salieron así (ver decisiones 5 y 7).
- **Explicar el porqué, no el qué.** Los comentarios del código justifican
  decisiones no obvias. El "qué hace" ya lo dice el código.
- **Tests sin red.** La suite no debe necesitar conexión ni API keys: tiene que
  poder correr en un camper sin cobertura. Vale igual para `tools/verificar.py`,
  que además corta la red del navegador a propósito (decisión 47).
- **Nunca hardcodear secretos.** Todo por variables de entorno.

## 3. Arquitectura

```
app/
  app.py                     Rutas Flask. SIN lógica de negocio.
  config.py                  Configuración desde variables de entorno
  modules/
    contexto.py              El estado del viaje. UNA definición, tres consumidores
    chat.py                  El chatbot. Decide QUÉ se le manda al modelo y qué no
    metricas.py              Pasos y batería, resumidos. Y si la serie tiene huecos
    perfil.py                Cómo estás tú: pasos, batería y de qué fiarse
    viaje.py                 El viaje hasta ahora: agregados y últimas notas
    luna.py                  Fase e iluminación en Python; salida y puesta de met.no
    diario.py                El primer sitio de cada día. Registra; NO analiza
    location_context.py      Nominatim (dónde estoy) + Overpass (qué hay cerca)
    weather_context.py       Open-Meteo (tiempo + oleaje) e interpretación
    ai_orchestrator.py       Prompt, esquema de salida y caché. AGNÓSTICO del proveedor.
    llm_providers.py         Único módulo que conoce Anthropic / Gemini / Kimi / Ollama
    ingest.py                Telemetría del móvil: token, validación e idempotencia
    notes.py                 Notas geolocalizadas, y el progreso del mapa
    miniaturas.py            La foto reducida: nombre derivado, presupuesto y borrado
    photo_meta.py            EXIF de una foto: cuándo y dónde. Sin dependencias
    waypoints.py             Puntos del viaje sacados de las fotos
    ruta.py                  Notas + fotos en una línea de tiempo, y su medida
    timeparse.py             Instantes ISO 8601: validar, canonizar a UTC, volver a local
    storage.py               SQLite: caché, notas, puntos y telemetría
    auth.py                  Login de un solo usuario (sesión; NO cubre la ingesta)
  static/
    js/app.js                Pantalla principal: GPS → lugar, tiempo y recomendación
    js/notas.js              Cola offline en IndexedDB. Guarda primero, envía después
    js/mapa.js               Mapa, trayecto, progreso y "revivir el viaje"
    js/diario.js             El muro: qué pasó cada día, con sus fotos
    js/chat.js               Conversación. El historial lo pone el servidor, no este
    js/perfil.js             El perfil. Pinta /api/perfil; sin GPS y sin red
    vendor/leaflet/          Leaflet 1.9.4, servido por nosotros (decisión 28)
```

Regla: `app.py` valida la entrada, llama a un módulo y formatea la respuesta.
Cada módulo tiene una función de entrada tipada y lanza su propia excepción
(`LocationError`, `WeatherError`, `AIError`, `IngestError`, `NoteError`,
`WaypointError`, `PhotoMetaError`, `MiniaturaError`). Solo `storage.py` abre la BD.

Hay **dos** formas de autenticarse, y no se cruzan: la sesión (`auth.py`) para
todo lo que usa una persona con un navegador, y el token de `ingest.py` para lo
que usa una máquina. Ver decisión 24.

## 4. Comandos

```bash
pip install -r requirements.txt            # producción (lo que va al servidor)
pip install -r requirements-dev.txt        # + pytest, para desarrollar
python run.py                              # servidor local (127.0.0.1:5000)
python -m pytest -q                        # tests (sin red, sin API keys)
python tools/verificar.py                  # las 6 pantallas EN UN NAVEGADOR (sin red)
python tools/verificar.py --ver            # con ventana, para mirarlo
python tools/verificar.py --solo mapa      # una: inicio | perfil | mapa | diario | fuego | chat
tools/verificar_sabotaje.sh                # ¿el guion caza un fallo metido a propósito?
python tools/pre_despliegue.py             # semáforo: git, PWA y datos reales
python tools/pre_despliegue.py --tests --navegador  # semáforo completo local
python tools/pre_despliegue.py --para-commit        # exige git limpio antes de push/pull
python tools/pre_despliegue.py --estrenar           # exige viaje vacío
python tools/medir_pantallas.py            # cuánto tarda cambiar de pantalla (local)
python tools/medir_pantallas.py --url https://tuapp…   # contra el DESPLEGADO, que es el que decide
python tools/estado_limpio.py              # ¿queda algo simulado? (código 1 si sí)
python tools/estado_limpio.py --limpiar    # borra SOLO lo simulado; no toca lo real
python tools/estado_limpio.py --borrar-todo-el-viaje   # reset. IRREVERSIBLE
python tools/diagnostico.py                # config, datos, fuentes y contexto
python tools/diagnostico.py --todos        # prueba todos los proveedores de LLM
python tools/diagnostico.py -v             # con la traza completa de cada fallo
python tools/listar_modelos.py             # qué modelos de Gemini sirven con tu key
python tools/hash_password.py              # genera SECRET_KEY y APP_PASSWORD_HASH
python tools/token_ingesta.py              # genera el token del iPhone y su hash
python tools/ver_telemetria.py             # últimas muestras recibidas del móvil
python tools/ver_telemetria.py 50          # las 50 últimas
python tools/ver_telemetria.py --coords    # con lat/lon en vez del nombre del sitio
python tools/ver_telemetria.py --borrar 3,4  # borra muestras malas por id
python tools/simular_telemetria.py         # siembra 7 días de telemetría SIMULADA
python tools/simular_telemetria.py 14      # 14 días
python tools/simular_telemetria.py --ver   # enseña lo que haría, sin guardar nada
python tools/simular_telemetria.py --limpiar  # borra lo simulado; no toca lo real
tools/logs.sh                              # (EN EL SERVIDOR) qué ha hecho la app
tools/logs.sh -f                           # en directo, mientras ejecutas el atajo
tools/logs.sh fotos                        # solo las importaciones de fotos
python tools/medir_contexto.py             # dónde se va el tiempo de construir()
python tools/ver_notas.py                  # notas del viaje + progreso del mapa
python tools/ver_notas.py 50               # las 50 últimas
python tools/ver_notas.py --borrar 3,4     # borra notas malas por id
python tools/importar_fotos.py CARPETA     # qué EXIF traen tus fotos (no guarda nada)
python tools/importar_fotos.py CARPETA --detalle    # foto a foto
python tools/importar_fotos.py CARPETA --importar   # a la BD local
python tools/importar_fotos.py CARPETA --enviar https://tuapp…   # al servidor
python tools/importar_fotos.py --limpiar   # vacía los puntos (se regeneran importando)
```

## 5. Estado actual

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Esqueleto Flask, login, GPS → nombre del lugar | ✅ Hecho |
| 2 | Open-Meteo + Overpass + recomendaciones con LLM | ✅ Hecho |
| 2b | Proveedor de LLM intercambiable (Anthropic / Gemini / Kimi) | ✅ Hecho |
| — | **Verificado de extremo a extremo con Gemini** (`gemini-3.6-flash`) | ✅ |
| 2c | Preparación del despliegue (deps fijadas, `/healthz`, cookie `Secure`) | ✅ Hecho |
| — | **Desplegado en PythonAnywhere y validado en iPhone** | ✅ 27-07-2026 |
| 2d | Ingesta de telemetría del iPhone (pasos, ubicación, batería) | 🟨 MVP funcionando y **formato dado por bueno**; sin cerrar (faltan días de datos reales). El volumen se **simula** para no bloquear lo de encima (decisión 36) |
| 3 | Notas geolocalizadas (cola offline) y mapa Leaflet | 🟨 Hecho; **falta validarlo en el móvil** |
| 3b | Ruta del viaje a partir del EXIF de las fotos, y "revivir el viaje" | ✅ **Cerrada** 28-07-2026, con el atajo del álbum y fotos reales |
| 4 | Miniaturas, perfil, PWA y resumen narrativo | ⬜ Pendiente — encargo en [`docs/prompt-fase4.md`](docs/prompt-fase4.md) |
| 5 | Contexto único, luna, limpieza de la pantalla | 🟨 **Hecha y DESPLEGADA**, validada en iPhone el 28-07-2026. Sin cerrar: ver §4 de [`prompt-fase6.md`](docs/prompt-fase6.md) |
| 6 | Pasos ciertos, cerrar la 2d y el chatbot | 🟨 **Pasos ciertos** (filtro `Origen`, contrastado contra la app Salud el 29-07-2026) y **chatbot hecho** (`/chat`, decisión 37). Pagada además la deuda de la Fase 5: sin datos duplicados en `/api/recommendations` y con el aviso de disco arreglado (decisión 38). Falta cerrar la 2d, y eso es tiempo, no trabajo |
| 6b | **Cuatro pantallas separadas**: Inicio, Perfil, Mapa, Chat | ✅ **Cerrada** 29-07-2026, validada en el iPhone contra el servidor (decisiones 40 a 46) |
| 8 | Diseño, el avance del viaje, el diario y la PWA | 🟨 **§1, §2, §3 y §4 hechos en local**: sistema visual con gramática de certeza, avance del viaje, Diario integrado en Viaje sin miniaturas visibles por defecto, Fuego al final de Inicio bajo botón, y `manifest.json` + iconos + meta de iOS **sin service worker** (decisiones 55, 56, 59 y 61). Falta validar la instalación en el iPhone y decidir si el atajo manda copias reducidas de fotos — encargo en [`docs/prompt-fase8.md`](docs/prompt-fase8.md) |
| 7 | Verificar todo, navegación fluida, el diario, y la PWA | 🟨 **§1 y §2 hechos**: `tools/verificar.py` recorre las cuatro pantallas en Chromium y `tools/verificar_sabotaje.sh` demuestra que caza cinco fallos metidos a propósito (decisión 47). Falta el §2 en adelante — encargo en [`docs/prompt-fase7.md`](docs/prompt-fase7.md) |

**La Fase 3 está hecha, no cerrada,** y la diferencia es la misma que en la 2d.
Lo que hay: notas de **solo texto** con cola offline en IndexedDB, mapa con
Leaflet servido por nosotros, y progreso del viaje (sitios, días, racha,
tablero de 19 comunidades, comparación entre años). Las fotos se aplazaron a
propósito (decisión 27) y su diseño queda escrito para cuando toquen.

Lo que **sí** está probado, y no solo por la suite (491 tests): la cola offline
se ejecutó entera en un Chrome de escritorio, cortando la red a mano, y los
cuatro caminos se comportaron como debían. Con `fetch` fallando, la nota se
guardó en IndexedDB y la interfaz enseñó "1 nota por enviar"; al disparar
`online` se envió sola y el servidor pasó de 5 notas a 6; un reintento con un
`client_id` que ya existía devolvió *duplicada*, se borró de la cola y el total
del servidor **no subió**; y una nota con `lat: 999` quedó marcada como
rechazada con su motivo a la vista, **sin volver a intentarse** al recargar la
app. El mapa pinta con Leaflet servido por nosotros, con sus tiles y su
atribución.

Lo que falta para cerrarla es lo que no se puede probar desde un escritorio:
**escribir una nota en un sitio sin cobertura de verdad, desde el iPhone, y
verla aparecer en el mapa al volver la señal.** Concretamente siguen sin
ejecutarse nunca el GPS real de Safari en iOS (aquí se sustituyó por un doble
que devolvía coordenadas fijas), el IndexedDB de iOS —que Apple purga tras
siete días sin abrir la app, y eso importa en un viaje— y el comportamiento del
evento `online` en una red móvil que va y viene, que no es lo mismo que
desenchufar un cable. En la 2d, esa misma distancia entre "probado en
escritorio" y "probado en el móvil" escondía cuatro trampas
([`docs/atajo-iphone.md`](docs/atajo-iphone.md)).

**La Fase 3b (la ruta) está CERRADA desde el 28-07-2026.** Lo que hay: un lector
de EXIF sin dependencias, `tools/importar_fotos.py`, la tabla `waypoints`, el
endpoint `/api/waypoints`, el mapa dibujando el trayecto con el modo *revivir el
viaje*, y —lo que faltaba— **el atajo del iPhone montado y funcionando**.

Lo que la cierra: se montó el atajo del álbum `Viaje` en un iPhone real, se
ejecutó contra el servidor desplegado y devolvió `guardados: 4, duplicados: 0`;
al reejecutarlo, `guardados: 0, duplicados: 4`. Y la comprobación que no se
puede leer mal: tras **tres** ejecuciones, `/mapa` sigue diciendo **4 fotos, 4
días y 41 km**, exactamente lo que predecía el cálculo local (40,5 km). Los
puntos caen en Albatera, que es donde se hicieron las fotos.

Montar el atajo costó **nueve trampas**, todas documentadas ahora en
[`docs/atajo-fotos.md`](docs/atajo-fotos.md). Tres merecen estar aquí porque
cambian cómo se razona sobre Atajos:

- **El campo `Número` de la acción `Diccionario` destruye los decimales**, con
  coma *y* con punto (lee el punto como separador de miles). La receta anterior
  recomendaba el `Diccionario` precisamente como protección contra eso, y era
  **falso**. El JSON se construye con una acción `Texto`, igual que en la 2d.
- **Un chip de variable en rojo no da error: devuelve vacío.** Y un vacío en un
  campo `Número` se convierte en `0`, así que `lat: 0, lon: 0` —el golfo de
  Guinea— llegó a enviarse con el servidor respondiendo `guardados: 4` sin una
  sola queja. Es el fallo mudo de la decisión 11 en su peor forma: una chincheta
  convincente en un sitio inventado.
- **`Altura` en *Obtener detalles de imágenes* son píxeles, no altitud.** 3024 px
  cae dentro del rango válido de altitud (-500 a 9000 m), así que se habría
  guardado el viaje entero a tres mil metros sin ningún error.

Lo que **no** se ha probado y hay que decirlo: el lector de EXIF nunca ha leído
un **HEIC del carrete** (sí un JPEG real de iPhone y HEIC fabricados a mano).
Ya no bloquea nada, porque por la vía del atajo **el servidor no lee la foto**:
los metadatos los extrae iOS. Solo importa el día que se use
`tools/importar_fotos.py` con la carpeta volcada por cable.

Dato comprobado que ahorra tiempo: **las fotos que pasan por WhatsApp llegan sin
ningún metadato**, así que hay que partir de los originales.

**La Fase 2d está APARCADA como MVP, no cerrada,** y la diferencia importa.
Probado contra el servidor desplegado y desde un iPhone real (28-07-2026): el
token, la idempotencia (un reenvío devolvió `duplicadas: 1`, que es lo correcto)
y una muestra completa con hora ISO, batería, coordenadas y pasos. Lo que falta
no es trabajo, es **tiempo**: el atajo se ha ejecutado a mano, no solo, y el
criterio de cierre es uno solo — ¿llegan los datos sin huecos durante varios
días? Mientras tanto la automatización va acumulando muestras y se retoma para
comprobarlo.

La consecuencia práctica de estar aparcada y no cerrada: **no se construye
análisis sobre estos datos todavía**, porque hacerlo sobre una fuente que aún no
se ha demostrado fiable es trabajo que hay que tirar. Por eso esta fase no tiene
pantalla, gráfica ni resumen; para mirar los datos está
`python tools/ver_telemetria.py`, desde una consola. El mapa de la Fase 3 se
construye sobre las **notas**, que son otra fuente; cuando la telemetría lleve
días demostrando que llega, dibujar el trayecto encima es casi gratis, porque
`lat`/`lon` ordenados por `medido_en` ya *son* la ruta.

**Cambio del 28-07-2026: el FORMATO se da por bueno, el VOLUMEN se simula.** El
atajo se pide a mano y la muestra llega completa y bien formada, así que la
forma del dato ya no es la pregunta abierta. Lo que falta sigue siendo tiempo, y
esperarlo bloqueaba todo lo que va encima. Desde ahora
`tools/simular_telemetria.py` siembra la serie que aún no existe para poder
escribir y probar el dashboard y el chatbot (decisión 36). **Lo que NO cambia:**
la fase sigue sin cerrar, y si la telemetría es fiable se responde mirando las
filas `atajos-iphone` y ninguna otra. Una simulación no cierra nada.

Montar el atajo dejó cuatro trampas documentadas en
[`docs/atajo-iphone.md`](docs/atajo-iphone.md) que no se ven venir: los
decimales salen con coma en un iPhone en español y rompen el JSON, una variable
rota se envía **vacía** sin que Atajos avise, `"lat:"` con dos puntos dentro es
JSON válido y guarda la ubicación como `NULL` sin protestar, y una cabecera
`Authorization` con texto de sobra pegado la corta el proxy con un 400 que ni
llega a la app. De ahí salieron dos cambios en el endpoint: el 400 devuelve
ahora el cuerpo que recibió, y la respuesta correcta devuelve **qué** se guardó
y no solo cuánto.

**Desplegado y validado.** `https://d10sdrebrasov.pythonanywhere.com` (plan
gratuito). Las seis comprobaciones del móvil en verde desde un iPhone con datos
móviles: GPS a **±18 m** (satélite, no triangulación por wifi), lugar resuelto,
tiempo con sus veredictos, y recomendaciones de Gemini en ~13 s. Con eso queda
probada por primera vez la funcionalidad central del proyecto: hasta desplegar
en HTTPS no había forma de saber si el GPS funcionaba.

La degradación se validó **sola y de verdad**: Overpass estaba caído durante la
prueba, salió su aviso en la interfaz, y todas las actividades aparecieron
marcadas como "sugerencia general" en vez de "✓ verificado en el mapa". El
sistema no fingió haber verificado nada.

- Checklist de despliegue: sección *Despliegue* del `README.md`.
- Las seis comprobaciones en el móvil: [`docs/validacion-movil.md`](docs/validacion-movil.md).
- Cuando algo falle: [`docs/troubleshooting.md`](docs/troubleshooting.md).
- Montar el atajo del iPhone: [`docs/atajo-iphone.md`](docs/atajo-iphone.md).
- Montar el atajo de las fotos: [`docs/atajo-fotos.md`](docs/atajo-fotos.md).

**Los encargos de cada fase viven en `docs/prompt-*.md`.** No son tareas
pendientes: son el registro de **qué se pidió**, que es lo que permite luego
contrastarlo con lo que se hizo. Los de fases terminadas se quedan como están.
Hoy hay seis: [`prompt-despliegue.md`](docs/prompt-despliegue.md),
[`prompt-fase3.md`](docs/prompt-fase3.md), [`prompt-fase5.md`](docs/prompt-fase5.md)
y [`prompt-fase6.md`](docs/prompt-fase6.md) (hechos),
[`prompt-fase4.md`](docs/prompt-fase4.md) (hecho a medias: la 3b se cerró desde
su §1, y las miniaturas y la PWA pasan a la 7), y
[`prompt-fase7.md`](docs/prompt-fase7.md), que es **el que describe el trabajo
que viene**.

**Si vienes con el contexto en blanco, el orden de lectura es:** este documento
→ [`prompt-fase7.md`](docs/prompt-fase7.md), que es **el que describe el trabajo
que viene** → y solo si toca esa parte, [`prompt-fase6.md`](docs/prompt-fase6.md)
o [`prompt-fase4.md`](docs/prompt-fase4.md).

**Cuenta de PythonAnywhere gratuita.** Importa para el diseño, no solo para la
factura: el plan gratuito saca todo el tráfico por un proxy con lista blanca de
dominios, y un host no permitido devuelve un **403 del proxy** que la app ve
como "fuente caída" y degrada en silencio. Por eso el checklist obliga a correr
`tools/diagnostico.py` **en el servidor** antes de tocar el móvil.

**Proveedor activo en el servidor: Gemini** (`gemini-3.6-flash`), comprobado en
el diagnóstico del 28-07-2026 (`LLM_PROVIDER=gemini`, 4 actividades en 11,8 s).
**Kimi** (`kimi-k3`) está configurado y verificado generando recomendaciones
reales, pero hoy no es el que corre. Es de prepago y queda saldo; el diagnóstico lo consulta al final para
que la cifra ya refleje lo que acaba de gastarse. **Anthropic sigue sin saldo**
(confirmado: la API devuelve 400 con *"Your credit balance is too low"*), y
**Gemini queda como alternativa gratuita**, también verificada en ~11 s con
`gemini-3.6-flash`. Cambiar entre los tres es una línea del `.env`
(decisión 10); comprueba cuáles responden hoy con
`python tools/diagnostico.py --todos`.

## 6. Registro de decisiones

Por qué las cosas son como son. Si algo parece raro, probablemente está aquí.

1. **Sesión de Flask propia en vez de Flask-Login.** Flask-Login gestiona
   *conjuntos* de usuarios. Con uno solo son 30 líneas que se entienden enteras.

2. **Excepciones propias por módulo en vez de devolver `None`.** Un `None` se
   ignora por accidente y revienta tres capas más arriba con un error
   incomprensible. Una excepción no se puede ignorar y transporta un mensaje
   legible hasta el usuario.

3. **Caché de APIs externas en SQLite por coordenada redondeada (3 decimales,
   ~110 m).** Nominatim limita a 1 petición/segundo y puede bloquear la IP.
   Redondear es lo que hace útil la caché: sin ello, moverte un metro genera
   una clave nueva y nunca aciertas. Además permite volver a un sitio ya
   visitado sin cobertura.

4. **`client_id` generado en el móvil para las notas.** Permite reintentar el
   envío al recuperar cobertura sin duplicar la nota. Está en el esquema desde
   la Fase 1 aunque las notas sean de la Fase 3: cambiar el modelo de datos con
   datos reales dentro es mucho más caro que crear una columna vacía antes.

5. **La lógica meteorológica vive en Python, no en el prompt.** Si se puede
   hacer paddle surf lo deciden reglas explícitas sobre oleaje y viento
   (`weather_context.water_sports()`), y al modelo le llega el veredicto ya
   calculado. Es determinista, testeable y auditable. Relacionado: la API marina
   de Open-Meteo responde **200 con `null`** tierra adentro, no un 4xx —
   verificado contra la API real; asumir un código de error habría sido un bug
   silencioso.

6. **Caché de las recomendaciones del LLM por ubicación + día + franja de 3 h.**
   La recomendación depende de la hora (el mismo sitio a las 10:00 y a las 21:00
   merece planes distintos), pero dos pulsaciones seguidas deben acertar.

7. **Peticiones escalonadas a los espejos de Overpass.** Medido en Llanes:
   probar los tres espejos en serie tardaba **53 s** (el primero agotaba su
   timeout de 30 s y el segundo tardaba otros 23). Ahora se lanza el primero y,
   si a los 6 s no ha contestado, se lanza el siguiente sin cancelarlo. Peor
   caso medido: **13,7 s**. No se lanzan los tres a la vez porque triplicaría la
   carga sobre un servicio comunitario gratuito en el caso normal.

8. **Salida estructurada con JSON Schema.** El proveedor garantiza que la
   respuesta cumple el esquema. Es la diferencia entre un frontend que se rompe
   cuando el modelo escribe markdown y uno que no se rompe nunca.

9. **Degradación en cascada.** Solo la ubicación es imprescindible. Tiempo, POIs
   y LLM pueden fallar por separado y la respuesta sigue siendo útil; cada fallo
   añade una entrada a `warnings` que la interfaz muestra. Una app que oculta que
   le falta la mitad del contexto no es fiable, es opaca.

10. **Proveedor de LLM intercambiable detrás de una única interfaz.**
    `llm_providers.py` es el único módulo que sabe que existen Anthropic o
    Google; hacia fuera solo hay `LLMProvider`, `build_provider()` y `AIError`.
    El motivo inmediato fue poder afinar el prompt gratis con Gemini sin comprar
    saldo, pero el motivo real es que **afinar el prompt es el trabajo, y no
    debe depender de qué proveedor tenga saldo hoy**. El contrato es estrecho a
    propósito (`generate(system, context, schema) -> str`): recibe el contexto
    ya construido y devuelve texto. El prompt de sistema y el esquema se definen
    **una vez** en `ai_orchestrator`; si cada proveedor tuviera su copia,
    divergirían y no sabrías cuál estás afinando. `AIError` vive en
    `llm_providers` (no en `ai_orchestrator`) porque los proveedores tienen que
    lanzarla y el orquestador tiene que importarlos: al revés sería un import
    circular. Se reexporta, así que el resto de la app no se entera.

11. **La clave de caché incluye proveedor y modelo.** Sin esto, tras afinar el
    prompt con Gemini y cambiar a Claude seguirías viendo las respuestas
    cacheadas de Gemini. **No da error: da conclusiones equivocadas** sobre qué
    modelo lo hace mejor, que es peor que un fallo ruidoso. Cubre también
    comparar dos modelos del mismo proveedor (flash contra pro).

12. **Sin reintento automático ante un 429.** Un 429 de capa gratuita suele ser
    cuota *por minuto* agotada: reintentar dentro de la misma petición choca
    contra el mismo muro y quema el timeout. Bloquear 20-60 s una petición móvil
    es peor que fallar rápido, y con un solo usuario un reintento humano está
    mejor informado que un `sleep` a ciegas. Se propaga el mensaje de la API
    (que suele decir cuánto esperar) y la app degrada como con cualquier otra
    fuente caída. Se reconsideraría si algún día se generan recomendaciones en
    segundo plano, donde nadie espera.

13. **`SHOW_AI_ERROR_DETAIL` es un interruptor, no una decisión permanente.**
    Activado, la interfaz enseña el error crudo del proveedor (cómodo afinando);
    desactivado —por defecto y en producción— mensaje genérico en la interfaz y
    detalle completo en logs y en `tools/diagnostico.py`. **La redacción de la
    API key no depende del interruptor**: `llm_providers.redact()` se aplica
    siempre, en todos los modos, y borra también fragmentos parciales. Una key
    en un mensaje de error acaba en un log, en una captura o en un issue, y a
    partir de ahí está comprometida.

14. **El modelo de Gemini se fija, y se elige probando, no leyendo la lista.**
    La lista que devuelve `models.list()` **no** es la lista de modelos
    usables: comprobado contra la API real, `gemini-2.5-flash` aparece listado
    y responde 404 (*"no longer available to new users"*), y varios modelos
    listados devuelven 429 porque su cuota gratuita está agotada. De 20
    candidatos de texto, solo 8 funcionaban. Por eso existe
    `tools/listar_modelos.py`: prueba cada uno con la misma configuración de
    salida estructurada que usa la app, que es la única comprobación que
    significa algo. Se fija un modelo concreto en vez de un alias
    (`gemini-flash-latest`) porque al afinar un prompt necesitas
    reproducibilidad: un alias cambia de modelo bajo tus pies sin avisar.
    Corolario: **el prefijo de la key no sirve para validarla** — las hay que
    empiezan por `AIza` y por `AQ.`, y ambas son buenas.

15. **La cookie de sesión sale `Secure` por defecto, no por configuración.**
    Es la cookie que da acceso a *toda* la app, y basta una petición por
    `http://` para que viaje en claro por el wifi de un camping. Se dejó como
    variable (`SESSION_COOKIE_SECURE`) pero **activada por defecto**: lo seguro
    tiene que ser lo que pasa si no haces nada. El precio es una trampa que hay
    que conocer: si *Force HTTPS* está desactivado en PythonAnywhere, el
    navegador descarta la cookie y la app entra en **bucle de login sin ningún
    mensaje de error**. Está en el checklist y en la tabla de síntomas, porque
    un fallo mudo que no está documentado cuesta una tarde.

16. **`/healthz` pregunta por el proveedor ACTIVO, no por una API key concreta.**
    Miraba solo `ANTHROPIC_API_KEY`, así que un despliegue sano con Gemini
    informaba `ia_configurada: false`. Es la decisión 11 otra vez —un fallo que
    no da error, solo una respuesta equivocada— pero en el peor sitio posible:
    la herramienta con la que compruebas si el despliegue ha ido bien. Usa
    `build_provider()`, que valida nombre y key **sin llamar a la API**: un
    health check no debe gastar cuota ni tardar 10 s. No revela cuál es el
    proveedor porque el endpoint es público; el detalle está en el diagnóstico.

17. **Dependencias con versión fijada (`==`).** Con `>=` instalas en el servidor
    lo que publicaran esa mañana, no lo que probaste. Y cuando algo se rompa
    dentro de un mes no podrás volver a lo que funcionaba, porque nunca quedó
    escrito qué era. `requirements-dev.txt` está aparte para que `pytest` no
    acabe en el servidor: la cuota gratuita son 512 MB y el virtualenv ya ocupa
    ~100 MB.

18. **Kimi se habla por HTTP con `requests`, sin el SDK de OpenAI.** Su API es
    compatible con la de OpenAI, así que el SDK habría funcionado; pero es una
    sola llamada (`POST /chat/completions`), y ahí el SDK no ahorra código, solo
    lo esconde, a cambio de un paquete más que mantener y que ocupa cuota en un
    PythonAnywhere gratuito de 512 MB. `requests` ya era dependencia. La
    contrapartida —ver los códigos HTTP a pelo— resulta ser una ventaja cuando
    algo falla: se ve exactamente qué se envió.

19. **`redact()` descubre las API keys por convención, no por una lista.**
    Recorre `Config` buscando atributos `*_API_KEY` en vez de enumerarlos a
    mano. El motivo es concreto y ya casi pasa: al añadir Kimi, una lista
    escrita a mano se habría quedado corta, y ese olvido **no da error** — solo
    hace que la key nueva salga sin tapar en el primer mensaje de error del
    proveedor. Decisión 11 otra vez (fallo silencioso), pero pagando con un
    secreto en vez de con una respuesta equivocada.

    Corolario del mismo trabajo: **cada proveedor tiene su propio mando de
    razonamiento y no son intercambiables**. `ANTHROPIC_EFFORT` acepta cinco
    valores; `KIMI_REASONING_EFFORT` acepta tres, y solo lo entiende `kimi-k3`
    (los demás modelos usan `thinking`, y los `-code` ninguno). Por eso son
    variables distintas y cada una se valida por su cuenta antes de llamar: un
    typo debe dar un error que nombre la variable, no un 400 críptico de la API.

20. **En Kimi, un 429 no significa una sola cosa.** En Gemini el 429 es siempre
    cuota gratuita agotada (decisión 12). En Kimi, que es de prepago, el mismo
    código cubre tres situaciones que se arreglan de formas **opuestas**, y hay
    que mirar el campo `type` del cuerpo para distinguirlas:

    | `type` | Qué pasa | Qué hacer |
    |--------|----------|-----------|
    | `rate_limit_reached_error` | Vas demasiado rápido (con 1 $ de recarga son 3 peticiones/minuto) | Esperar un minuto |
    | `engine_overloaded_error` | Sus servidores saturados | Esperar; no es cosa tuya |
    | `exceeded_current_quota_error` | **Sin saldo** | Recargar; esperar no arregla nada |

    Tratarlos igual sería activamente dañino: decirle "prueba en un minuto" a
    quien se ha quedado sin saldo lo manda a reintentar contra un muro, en el
    peor momento posible (a mitad de viaje). Sigue sin haber reintento
    automático, por lo mismo que la decisión 12.

    Del mismo verificar-en-vez-de-suponer salieron otras tres cosas: el cuerpo
    de error **no siempre trae el campo `code`** (comprobado contra la API real,
    un 401 devuelve solo `type` y `message`), `max_tokens` está **deprecado** en
    favor de `max_completion_tokens`, y un JSON truncado llega con **HTTP 200** y
    `finish_reason="length"` — como la API marina de Open-Meteo (decisión 5), un
    200 no significa que la respuesta sirva. Sin comprobarlo, el fallo aparecería
    tres capas más arriba como "no era JSON válido", que no dice cómo arreglarlo.

21. **`api.moonshot.ai` está en la lista blanca de PythonAnywhere.** Verificado
    sobre el HTML de la página de la lista, no preguntando: el plan gratuito saca
    todo el tráfico por un proxy que solo deja pasar dominios permitidos, y un
    host no permitido devuelve un 403 del proxy que la app degrada en silencio
    (ver *Estado actual*). Kimi habría sido inservible en producción de no
    estarlo, y eso hay que saberlo **antes** de pagar la recarga, no después.
    También están `api.anthropic.com`, `api.moonshot.cn` y `openrouter.ai`.

22. **Los espejos de Overpass están casi todos muertos, y no se sustituyen a
    ciegas.** Medido el 27-07-2026: de los tres configurados, `kumi.systems` y
    `private.coffee` no responden a nadie, y `overpass-api.de` devuelve **504
    intermitente** por saturación (falla en una coordenada y a los segundos
    funciona en otra). Es decir: la estrategia de peticiones escalonadas de la
    decisión 7 está corriendo sobre un solo servidor, y flojo.

    Se buscaron reemplazos y **no se añadió ninguno**, que es la parte que
    importa: `overpass.osm.ch` responde `200` en 0,5 s pero devuelve **cero
    elementos** en España porque es un espejo regional suizo. Añadirlo habría
    sido peor que el fallo actual — la app diría "aquí no hay nada que ver" en
    vez de "no he podido consultarlo", convirtiendo un fallo ruidoso en uno
    silencioso (decisión 11). `maps.mail.ru` funciona a veces, en 22-36 s.

    Corolario para cuando se retome: un espejo no se valida con que responda
    `200`, sino comprobando que **devuelve elementos para una coordenada
    española conocida**. Y si algún día se añaden espejos regionales, el código
    tendría que distinguir "sin resultados" de "no consultado".

23. **Ventana solapada en cada envío, en vez de una cola en el móvil.** Es la
    decisión no obvia de la Fase 2d, y la razón de que el endpoint reciba un
    array en vez de una muestra.

    El problema: el iPhone envía telemetría cada hora desde un atajo, y el
    viaje es en camper por el norte de España, donde la cobertura va y viene.
    Si un POST falla por falta de señal, esa muestra no se puede perder.

    La solución obvia sería una cola en el móvil: guardar lo que no se pudo
    enviar y reintentarlo. Y es la mala. Una cola es **estado que hay que
    mantener sincronizado** entre dos sistemas: hay que persistirla, purgarla,
    decidir qué pasa si el reintento también falla, y depurarla desde un iPhone
    en una gasolinera. Además Atajos es un entorno pésimo para eso: no hay
    almacenamiento fiable entre ejecuciones y una automatización que no se lanza
    no reintenta nada.

    Lo que se hace en su lugar aprovecha una propiedad del origen de los datos:
    **Salud se puede consultar hacia atrás**. Así que cada envío incluye las
    últimas N horas de muestras, no solo la actual. Con envíos cada hora y una
    ventana de 6 h hacen falta **seis fallos seguidos** para perder algo, y el
    sistema se cura solo al recuperar cobertura: no hay nada que sincronizar,
    porque no hay estado. El móvil es *stateless* y el servidor es la única
    fuente de verdad.

    El precio —y es lo que hay que entender para no romperlo— es que **el
    endpoint tiene que ser idempotente**: recibirá la misma muestra muchas
    veces, por diseño, no por avería. En régimen normal, cinco de cada seis
    muestras de un envío son duplicadas. Eso se sostiene con dos cosas:

    - `UNIQUE(fuente, medido_en)` en la tabla e `INSERT OR IGNORE`. La
      idempotencia vive en el **esquema**, no en un `SELECT` previo en el
      código: con dos peticiones a la vez, comprobar-y-luego-insertar tiene una
      carrera; una restricción de unicidad no.
    - `medido_en` se canoniza a UTC con precisión de segundos antes de
      guardarlo. Sin eso, `10:00:00+02:00` y `08:00:00Z` son el mismo instante
      y dos filas distintas, y la deduplicación sería mentira sin dar ningún
      error: solo pasos duplicados (decisión 11 otra vez).

    Y la respuesta dice `guardadas` / `duplicadas` / `descartadas` en vez de un
    OK a secas. Que la mayoría salgan como duplicadas no es ruido: es la señal
    de que la ventana solapada está funcionando, y es lo que se mira desde el
    móvil para saberlo.

    Corolario de la misma idea: **una muestra inválida no tumba el lote**. Se
    descarta, se cuenta y se informa. Lo contrario haría que un dato raro
    tirase seis horas de datos buenos, justo en el envío más largo —el que
    llega tras horas sin cobertura—, que es el que más probabilidades tiene de
    traer algo torcido.

24. **La ingesta se autentica con su propio token, y la sesión NO sirve.**
    `auth.login_required` no vale aquí: el endpoint es público en internet y
    Atajos no inicia sesión ni guarda cookies. Hasta ahí es una necesidad, no
    una decisión. La decisión es la otra mitad: **no se acepta también la
    cookie de sesión como alternativa.**

    Aceptar las dos parece cómodo (podrías probar el endpoint desde el
    navegador ya logueado) y es como se cuelan los fallos de *confused deputy*:
    a partir de ahí, cualquier cosa capaz de hacer que un navegador ya
    autenticado emita la petición —una pestaña abierta, un enlace, un formulario
    de otro sitio— estaría escribiendo en la tabla de telemetría con tus
    permisos. Un solo camino de autenticación es un camino que se puede razonar
    entero. Lo fija un test, igual que
    `test_cualquier_fallo_del_proveedor_sale_como_aierror` fija la frontera del
    proveedor.

    Detalles que no son adorno:

    - Se guarda **hasheado** (`INGEST_TOKEN_HASH`), como la contraseña, y se
      verifica con `check_password_hash` (tiempo constante). Un `.env` filtrado
      no entrega el token.
    - Es un secreto **distinto** de la contraseña de la app. Este vive en claro
      dentro del iPhone, así que perder el móvil obliga a rotar este y solo
      este.
    - El 401 es **idéntico** para los tres modos de fallar: sin cabecera, mal
      formada, o token incorrecto. Distinguirlos le confirma a quien prueba a
      ciegas que ya acertó el formato y solo le queda adivinar el secreto. Para
      depurar está el diagnóstico, que dice si el hash está configurado.
    - El hash se genera con **PBKDF2 y no con el scrypt** que Werkzeug pone por
      defecto. No es preferencia criptográfica: scrypt es *memory-hard* y
      reserva ~32 MB por verificación, y aquí cada intento fallido de cualquiera
      en internet paga esa verificación. En un worker de PythonAnywhere gratuito
      quedarse sin memoria no degrada, mata el proceso. PBKDF2 es igual de
      constante en tiempo y el coste se queda en CPU.
    - El token no puede aparecer en un log ni en un mensaje de error. Y no basta
      con no escribirlo: `redact()` descubre las API keys recorriendo `Config`
      (decisión 19), y ese truco **no alcanza a este token**, porque en el
      servidor solo vive su hash. Por eso se le añadió un patrón explícito de
      cabecera `Bearer`.

25. **Los pasos se envían como acumulado de 24 h, no en cubos por hora.** El
    diseño de la decisión 23 pedía que cada envío trajera las últimas N horas
    de pasos, cada una con su hora en punto. En Atajos eso significa un bucle
    con aritmética de fechas: unas doce acciones, redondeo al inicio de la hora,
    consulta a Salud por rango y suma. Montado a mano en la pantalla de un
    móvil, cada una de esas acciones es un sitio donde equivocarse — y ya se
    perdieron horas con erratas mucho más simples (ver
    [`docs/atajo-iphone.md`](docs/atajo-iphone.md)).

    Lo que se hace en su lugar: una sola consulta a Salud por los pasos de las
    **últimas 24 h**, enviada en la misma muestra que la batería y la ubicación.
    Cuatro acciones en vez de doce.

    **Y no pierde la propiedad que importaba.** El objetivo de la ventana
    solapada era que un envío fallido no perdiera datos. Un acumulado es un
    contador monótono: si falla un envío, el siguiente ya trae el total
    *incluyendo* lo que se perdió. Se cura solo por construcción, igual que la
    ventana, pero sin estado ni bucle. La ventana solapada sigue siendo la
    decisión correcta para métricas puntuales (una lectura de batería perdida
    no se recupera nunca); para un contador acumulado es innecesaria.

    **Corrección del 28-07-2026, y es importante:** el atajo pedía `los últimos
    1 día`, que en Atajos son las últimas 24 horas **móviles** y NO "hoy". Se vio
    en los datos: una muestra de las 02:48 traía 12.427 pasos, que arrastraban la
    tarde anterior entera. Ese número no es ni el día ni el viaje, y **no da
    ningún error**: solo medias infladas si se analiza como días. Se cambió a
    `Fecha de inicio` **es hoy**, que Atajos ya trae hecho, y ahora cada muestra
    dice *pasos de hoy hasta este momento* — se reinicia sola a medianoche, los
    envíos del día dibujan la curva y el total del día es el máximo.

    **Y una segunda corrección el mismo día, más grave: los pasos se contaban
    DOS VECES.** La app Salud daba 5.428 pasos de hoy y el atajo enviaba 10.675.
    Con más de una fuente escribiendo pasos (un Apple Watch, o ciertas apps),
    HealthKit guarda las muestras de cada dispositivo por separado: Salud enseña
    el total deduplicado, pero `Calcular Suma` sobre las muestras crudas las suma
    todas. **No da ningún error**, solo un histórico entero al doble. Se arregla
    filtrando por `Origen` en la propia búsqueda. La comprobación que lo caza, y
    que hay que hacer siempre al montar esto en un móvil nuevo: **ejecutar el
    atajo y comparar con lo que dice la app Salud para hoy.**

    **Cerrado el 29-07-2026.** El filtro `Origen` está puesto en el atajo y el
    número que envía **coincide con la app Salud**. Se anota la fecha porque la
    diferencia entre "el arreglo está descrito" y "el arreglo está comprobado"
    es justamente lo que este proyecto se toma en serio: un doble conteo no da
    error, así que solo la comparación contra la app que ya enseña el dato
    demuestra que se acabó. Lo que sigue sin estar cerrado es otra cosa —que
    lleguen datos **solos y sin huecos** durante días—, y eso no lo arregla
    ningún filtro.

    Consecuencia obligatoria: **la columna `pasos` cambia de significado**, así
    que las muestras anteriores a ese cambio hay que borrarlas. Mezclar ventanas
    rodantes con acumulados del día en la misma columna es el análisis
    silenciosamente equivocado que esta misma decisión advierte dos párrafos más
    abajo.

    **Lo que sí se pierde, dicho claramente:** el desglose por horas de los
    envíos fallidos. Si el móvil está sin cobertura de 10:00 a 14:00, sabrás el
    total al recuperarla pero no cuánto se anduvo en cada una de esas cuatro
    horas. Para "cuánto anduve este día" es irrelevante; para "¿a qué hora del
    día camino más?" no lo sería. Si algún día hace falta esa granularidad, hay
    que montar el bucle — y entonces habrá que decidir qué significa `pasos`,
    porque **mezclar cubos horarios y acumulados en la misma columna daría
    análisis silenciosamente equivocados** (decisión 11 otra vez). La salida
    limpia sería una tabla estrecha con nombre de métrica; está en el roadmap.

26. **Aquí SÍ hay cola offline, y en la 2d no. La asimetría es el diseño, no
    una incoherencia.** Las dos fases resuelven el mismo problema —el norte de
    España se queda sin cobertura a ratos— y lo resuelven al revés. El motivo
    está en el **origen del dato**, no en el transporte:

    | | Telemetría (2d) | Notas (3) |
    |---|---|---|
    | ¿Existe el dato en otro sitio? | Sí: Salud lo guarda y se consulta hacia atrás | **No.** Solo está en la cabeza de quien la escribió |
    | Si se pierde un envío | El siguiente lo trae otra vez | Se ha perdido para siempre |
    | Solución | Ventana solapada, sin estado | Cola en el navegador, con estado |

    Una cola es estado que hay que mantener sincronizado entre dos sistemas, y
    en la 2d se descartó a propósito (decisión 23) porque el origen ya
    garantizaba la recuperación. Aquí no la garantiza nadie, así que el estado
    hay que pagarlo. Pagar por lo que no hace falta es lo que se evitó allí;
    ahorrárselo aquí sería perder notas.

    De ahí sale la regla que ordena todo el JavaScript de esta fase: **se
    escribe primero en IndexedDB y se le dice al usuario que está guardada; el
    envío es un intento posterior.** Nunca al revés.

    IndexedDB y no `localStorage`: `localStorage` es síncrono (bloquea la
    interfaz al escribir), guarda solo texto y ronda los 5 MB. IndexedDB además
    guarda `Blob`, que es lo que hará falta el día que haya fotos.

    **Y la decisión que no se ve venir hasta que pasa: qué hacer con un 400.**
    El encargo decía que solo se borra de la cola con un `201` o un
    "duplicada", y que un `500` o un timeout la dejan. No decía nada del `400`,
    y ahí estaba la trampa: una nota que el servidor rechaza por inválida y que
    se reintenta para siempre **atasca la cola detrás de ella** y las notas
    buenas no salen nunca. Se resuelve por clases de fallo, no por códigos:

    | Respuesta | Qué significa | Qué hace la cola |
    |---|---|---|
    | `201` / `200` | Está a salvo en el servidor | La borra |
    | `400` | No va a entrar nunca | La marca *rechazada* y deja de intentarlo |
    | `401` | Sesión caducada; la nota no tiene la culpa | La conserva y pide entrar |
    | `5xx`, timeout, sin red | No se sabe si llegó | La conserva |

    El `200` de "duplicada" no es un caso raro que haya que tolerar: es el
    reintento normal cuando el POST llegó bien y la respuesta se perdió al
    entrar en un túnel. Por eso el servidor distingue `creada` de `duplicada`
    en el cuerpo y no solo en el código de estado, igual que la ingesta
    devuelve `guardadas`/`duplicadas`.

    Sin `setInterval`: se sincroniza al abrir la app, al guardar una nota y en
    el evento `online`. Un temporizador machacando gasta batería en un móvil
    que va en un camper y no adelanta nada; y `online` por sí solo no basta,
    porque el navegador cree que hay red en cualquier wifi de camping aunque no
    llegue a ninguna parte. El reintento al abrir la app es el que de verdad
    recupera las notas atascadas.

27. **El MVP es solo texto. Las fotos se aplazan, y el razonamiento se guarda
    hecho.** Se decidió durante la fase: lo que hace falta durante el viaje es
    marcar sitios con texto, y las fotos traen consigo la mitad del riesgo de
    la fase (presupuesto de disco, validación de imagen, límite por ruta,
    nombres de archivo hostiles). Hacerlas a medias habría sido peor que no
    hacerlas.

    `photo_path` se queda en el esquema, a NULL. Es la decisión 4 otra vez: una
    columna vacía es gratis hoy y cara con un mes de viaje dentro.

    Lo que ya está decidido para cuando toque, para no volver a discutirlo:

    - **Las fotos viajarán en `multipart/form-data`, no en base64 dentro del
      JSON.** base64 infla un 33 % (medio segundo más por el wifi de un camping
      y más ventana para que se caiga), obliga a materializar el cuerpo entero
      como cadena en memoria y a decodificarlo en la ruta —CPU, que en
      PythonAnywhere es **cuota diaria**— y hace que el límite por ruta mida el
      tamaño inflado, así que "2 MiB" dejaría de significar 2 MiB de foto.
      IndexedDB además guarda `Blob` nativo: `FormData.append` lo envía sin
      conversión, mientras que base64 obligaría a un `FileReader` en **cada
      reintento**, con poca batería y peor red.
    - **Se redimensionarán en el NAVEGADOR** (canvas, lado máximo ~1600 px,
      JPEG ~0,8). Subir 4 MB por la red de un camping es medio minuto en el que
      la conexión puede caerse, y redimensionar en el servidor gasta esa misma
      cuota diaria de CPU. `Pillow` sigue comentado en `requirements.txt`.
    - **El orden de escritura será archivo primero, fila después.** Al revés
      —fila y luego archivo— un fallo al escribir deja una fila apuntando a una
      foto que no existe, y como el reintento devuelve "duplicada", **la foto
      se pierde para siempre**. En el orden bueno el peor caso es un archivo
      huérfano: desperdicia disco, se ve desde el diagnóstico y el reintento lo
      reescribe idéntico. Un fallo recuperable contra uno irrecuperable.
    - **El nombre del archivo saldrá del `client_id`, jamás del cliente.** Por
      eso el `client_id` se valida ya hoy como UUID canónico estricto: hace que
      el nombre sea seguro **por construcción** en vez de por saneado. Se
      descartó pasar cualquier cadena por `secure_filename()`, porque sanear
      puede colapsar dos ids distintos en el mismo nombre y una nota se comería
      el archivo de otra sin dar ningún error.

28. **Leaflet servido desde `app/static/vendor/`, con la versión fijada.** Un
    CDN es un tercero más que puede caerse, y con mala cobertura el navegador
    tiene más probabilidades de tener nuestro archivo en caché (ya ha entrado a
    la app) que de alcanzar `unpkg.com` desde un camping. Es la decisión 17
    aplicada al frontend: lo que se probó es lo que se sirve.

    **Lo que hay que tener claro para no razonar mal:** los *tiles* del mapa los
    pide el **navegador**, no el servidor. La lista blanca del proxy de
    PythonAnywhere (decisión 21) afecta solo al tráfico saliente del servidor,
    así que no interviene aquí para nada, y vendorizar Leaflet no es una forma
    de esquivarla. Que `tile.openstreetmap.org` no esté en esa lista da igual.

    **Dos fondos, y ninguno sobra.** *Mapa* (OSM) lleva escritos los nombres
    de los pueblos, las carreteras y los senderos: es lo que hace falta para
    saber **por dónde** fuiste. *Satélite* (Esri World Imagery) enseña la playa
    y el bosque de verdad: es lo que hace falta para **recordar** dónde
    estuviste. El satélite va con una capa de etiquetas encima porque sin
    nombres es bonito y no se sabe dónde estás, y la elección se guarda en
    `localStorage` para no cobrar un peaje en cada visita por una preferencia
    que no cambia. Cada fondo lleva su atribución, que es obligatoria por sus
    condiciones de uso.

    Detalle que parece un bug y no lo es: el satélite se sirve con
    `maxNativeZoom: 18`. Por encima del zoom que Esri tiene de verdad, pedir
    tiles devuelve **cuadros en blanco**; con esto Leaflet amplía el último
    bueno. Borroso es peor que nítido, pero blanco es peor que las dos cosas.

    Consecuencia sin cobertura, que se documenta en vez de disimularse
    (decisión 9): **los tiles no cargan y el mapa sale gris, pero las
    chinchetas y el listado siguen ahí**, porque salen de nuestro servidor. La
    página lo dice en voz alta tras tres tiles fallidos seguidos —uno suelto
    pasa al hacer zoom rápido y no significa nada—. No se implementan mapas
    offline: está fuera del alcance de la fase.

29. **El progreso del mapa se calcula en Python y sale solo de las notas.**
    Podría haber sido un puñado de `if` en el JavaScript del mapa; es una
    función pura en `notes.py` con sus tests. El motivo es que estas cifras son
    justamente las que **no dan error cuando están mal**: una racha o un
    contador de comunidades equivocados no rompen nada, solo mienten.

    Tres decisiones dentro de esa función:

    - **Un "sitio" son ~1,1 km (2 decimales), no los ~110 m de la caché de
      APIs.** Son preguntas distintas: allí es "¿puedo reutilizar la respuesta
      de Nominatim?", y aquí "¿he estado ya aquí?". Con la precisión fina,
      pasear por un pueblo escribiendo notas contaría como varios lugares
      visitados, y el contador premiaría caminar en vez de viajar.
    - **Los días se cuentan en hora LOCAL, no en UTC.** Una nota escrita a las
      00:30 en España es del día siguiente en UTC: contarla ahí desplaza un día
      entero del viaje. Por eso `offset_original` está en la tabla.
    - **El tablero de comunidades enseña también las que faltan.** "Llevas 2 de
      19" se entiende sola; "2 regiones" no. Y el encaje de nombres es código
      con tests porque Nominatim devuelve "Principado de Asturias": comparar
      las cadenas a pelo dejaría la casilla apagada habiendo estado allí. Lo
      que no encaja con ninguna comunidad conocida (una nota de Portugal) no se
      descarta, se enseña aparte.

    Todo esto sale de las **notas** y de nada más. La telemetría sigue aparcada
    a la espera de demostrar que llega sin huecos, y construir el progreso
    sobre una fuente que aún no es fiable es trabajo que habría que tirar.

30. **Las fotos no se suben: se leen sus metadatos y se quedan donde están.**
    Una foto son ~3 MB y el plan gratuito tiene 512 MB; sus metadatos EXIF son
    ~100 bytes y contienen lo único que el mapa necesita —cuándo y dónde— así
    que **el trayecto entero se reconstruye sin subir un solo megabyte**. Es la
    decisión que convierte "guardar fotos" (caro, y aplazado en la decisión 27)
    en "tener la ruta" (gratis, y hecho).

    El lector de EXIF no trae dependencias. Pillow o exifread habrían sido un
    paquete más para leer cuatro etiquetas; y aquí se puede evitar porque el
    EXIF es un TIFF incrustado y sacar cuatro etiquetas de un TIFF son cien
    líneas que además se prueban **con bytes fabricados a mano**, sin meter
    ningún binario en el repositorio. Es la decisión 18 otra vez.

    Tres cosas comprobadas contra archivos reales, no supuestas:

    - **WhatsApp borra el EXIF entero.** Ni fecha, ni GPS, ni cámara: cero
      bytes. Para esto solo sirven los originales del carrete, y la herramienta
      lo dice explícitamente cuando detecta que no hay metadatos, porque si no
      parecería que está rota.
    - **En un JPEG se recorren los segmentos, no se busca la palabra "Exif".**
      Un JPEG puede llevar esos bytes dentro de la imagen comprimida por
      casualidad, y fabricar coordenadas a partir de eso sería el peor fallo
      posible aquí: una chincheta convincente en un sitio inventado.
    - **El huso horario es opcional en el EXIF.** `DateTimeOriginal` es hora
      local *sin zona*; el desfase va en otra etiqueta que el iPhone escribe y
      muchas cámaras no. Sin ella se guarda la hora local tal cual y **no se
      inventa ninguna zona**: suponer "+02:00 porque el viaje es por España"
      dejaría las fotos de Canarias una hora corridas sin dar ningún error.
      Por eso `waypoints.capturado_en` es hora local sin huso y `notes.created_at`
      es UTC canónico: son cosas distintas y llamarlas igual habría escondido
      la diferencia.

    Los puntos van a **su propia tabla** y no a una `fuente` más dentro de
    `telemetria`, que era lo tentador porque esa tabla ya tiene `fuente`,
    `lat`, `lon` y su UNIQUE. Se descartó porque la regla vigente es que no se
    construye análisis sobre `telemetria` hasta cerrar la Fase 2d, y meter ahí
    una fuente que **sí** es fiable obligaría a recordar un `WHERE fuente` en
    cada consulta futura. Una regla que depende de que alguien se acuerde no es
    una regla.

    La idempotencia va por `UNIQUE(fuente, archivo)` y no por la fecha: dos
    fotos de una ráfaga comparten el segundo y son dos puntos distintos.
    Reimportar la carpeta entera —que es lo que se hace cada vez que se vuelca
    el móvil— deja el viaje igual.

31. **La ruta mezcla notas y fotos por su hora LOCAL, y los kilómetros se
    calculan una sola vez.** Una nota trae su instante en UTC con el huso
    aparte; una foto trae la hora de la cámara y puede que sin huso. Para
    ponerlas en la misma línea se usa la hora local de cada una, que además es
    la que se recuerda ("esa foto es de después de comer"). El precio, dicho
    claro: un viaje que cruce husos podría ordenar dos momentos con el desfase
    entre zonas. Para el norte de España es exacto.

    Dos decisiones sobre la distancia, y una es una corrección:

    - **Haversine, no restar grados.** Un grado de longitud mide 111 km en el
      ecuador y 78 km en el norte de España: restar daría un 40 % de error
      justo en la zona del viaje.
    - **Un salto de más de 300 km entre dos puntos seguidos no suma.** No es un
      tramo recorrido: es un vuelo, o dos viajes importados juntos. Sumarlo
      daría un total espectacular y falso, y un total modesto y cierto vale
      más. Se dice cuántos saltos se han ignorado, en vez de callarlo.
    - **Los kilómetros de cada día y el total salen del MISMO cálculo.** Se
      hacían por separado y no cuadraban: el total incluía los tramos entre
      días (el trayecto nocturno de Cudillero a Laredo) y ningún día los
      contaba. Dos números que no suman y no dan ningún error son el fallo
      silencioso de manual (decisión 11). Ahora hay un solo sitio que decide
      qué es un tramo, y cada tramo se le apunta al día en que se **llegó**.

    Y una de seguridad que va con la herramienta: **`--enviar` se niega a
    mandar el token por `http://`** salvo a `localhost`. La cabecera
    `Authorization` viaja sin cifrar por http, así que una errata en la URL
    entregaría el token a cualquiera en la misma red — y sería un fallo
    **silencioso**, porque la petición funcionaría igual y el secreto ya
    estaría comprometido cuando te enterases. Se comprueba en vez de confiar
    en escribir bien la URL.

    Y lo que la ruta no puede enseñar se enseña igual: cuántas fotos se
    quedaron sin fecha (no se pueden colocar) y cuántas sin GPS (cuentan en el
    relato, no en el mapa). Esconderlas haría creer que el viaje está entero.

32. **El contexto es un módulo, no un trozo de una vista.** Es la decisión 10
    aplicada al contexto en vez de al proveedor: **una definición, tres
    consumidores.** `contexto.construir(lat, lon)` devuelve el estado del viaje
    y de ahí beben la pantalla (`/api/contexto`), el recomendador
    (`ai_orchestrator`) y, cuando llegue, el chatbot. Si cada uno armara el
    suyo divergirían, y no habría forma de saber cuál es el bueno.

    Antes vivía dentro de `/api/recommendations`, y eso costaba tres cosas a la
    vez: no se podía mirar el tiempo sin pagar una llamada al modelo, la
    pantalla tardaba lo que tardase la fuente más lenta, y no existía ninguna
    forma de pedir "el contexto" sin pedir también una recomendación.
    **Medido tras separarlo: 0,52 s en frío y 0,01 s con la caché caliente**,
    contra los ~43 s del peor caso anterior.

    Dentro del módulo, la red y el razonamiento van separados a propósito:
    `construir()` hace las llamadas y `ensamblar()` es **pura**. Toda la lógica
    que merece pruebas —qué hora local es, en qué ha quedado cada fuente, qué
    se avisa— vive en la pura, así que la degradación se comprueba con datos
    escritos a mano y la suite sigue corriendo sin cobertura.

    **`/api/contexto` devuelve `200` con partes vacías, y eso es seguro por una
    razón concreta.** Las decisiones 5 y 20 avisan de lo contrario: un `200`
    cuyo cuerpo *parece* bueno. Aquí el cuerpo trae su propio veredicto en
    `fuentes`, con cuatro estados que no son intercambiables:

    | Estado | Qué significa | ¿Avisa? |
    |---|---|---|
    | `ok` | Se consultó y trajo dato | no |
    | `sin_datos` | Se consultó, respondió bien, y **aquí no hay dato** | no |
    | `fallo` | Se consultó y no se pudo | **sí** |
    | `no_consultada` | No se pidió, a propósito | no |

    Distinguir `sin_datos` de `fallo` es el corolario de la decisión 22 —el que
    quedó escrito y sin implementar al descartar el espejo suizo—: un `null` no
    puede expresar la diferencia entre "aquí no hay mar" y "la API del mar se
    ha caído", y confundirlas hace que la app diga "aquí no hay nada que ver"
    cuando lo que pasa es "no he podido consultarlo". Por eso `Marine` lleva
    ahora un campo `fallo`. La alternativa —`null` más la prosa de `warnings`—
    vale para una pantalla que enseña texto y **no vale para el chatbot**, que
    necesita saber si callar o avisar; un estado es un dato, una frase en
    castellano no lo es.

    Y `warnings` se **deriva** de los `fallo` en vez de irse rellenando a mano.
    Así es imposible que una fuente falle sin aviso o que salga un aviso de algo
    que no ha fallado: la invariante la garantiza la construcción, igual que la
    idempotencia de la ingesta vive en el `UNIQUE` de la tabla y no en un
    `SELECT` previo (decisión 23). Los `no_consultada` (la luna, las métricas)
    **no** avisan: un aviso permanente durante las semanas que tarde en cerrarse
    la 2d es el ruido inútil que hace que se dejen de leer los avisos.

    Dos consecuencias que salieron al hacerlo:

    - **La zona horaria supuesta ahora se marca.** La aporta Open-Meteo
      (`timezone=auto`); si el tiempo falla no hay zona y se cae a
      `Europe/Madrid`. Eso pasaba **en silencio**, y en Canarias significa una
      hora de error en todo lo que cuelga de la hora local: la franja de la
      caché de recomendaciones, el "queda poca luz" del prompt y el resumen del
      día cuando exista. `Momento.zona_es_supuesta` lo hace visible, y el prompt
      se lo dice al modelo. Es la misma regla del huso horario del EXIF
      (decisión 30): no se inventa una zona en silencio.
    - **`/api/recommendations` rearma el contexto en el servidor, no lo recibe
      del navegador.** La letra del encargo decía "recibe el contexto ya
      construido", y hacerlo por el cuerpo del POST habría sido alimentar al
      modelo con lo que diga el cliente: habría que revalidarlo entero, y un
      cuerpo manipulado pondría al modelo a razonar sobre un sitio y un tiempo
      inventados sin dar ningún error. Sale casi gratis rearmarlo, porque la
      pantalla acaba de pedirlo y Nominatim y Open-Meteo están cacheados.

    **Cada punto de interés es un enlace que abre Mapas con la ruta puesta**, y
    los POIs **no** se pintan en `/mapa`. Las dos mitades son la misma decisión:
    `/mapa` es el registro de **dónde has estado** —notas, fotos, trayecto,
    kilómetros, comunidades— y los POIs son **dónde podrías ir**: no los has
    visitado, salen de una consulta que caduca en 7 días y cambian según dónde
    estés parado. Mezclarlos haría que el mapa del viaje dejara de significar
    una cosa sola, justo el mapa que dentro de un mes tiene que contar el viaje.
    Para verlos sobre un mapa está el enlace, que los abre en el que ya llevas
    en el bolsillo.

    Se usa el enlace **universal** de Google (`google.com/maps/dir/?api=1`) y
    no un esquema propio (`comgooglemaps://`, `maps.apple.com`). El esquema
    abre la app algo más directo, pero si esa app no está instalada **pulsar no
    hace nada**: ni abre, ni avisa, ni da error. Un enlace que no reacciona es
    el fallo mudo de siempre, y en marcha es de los que más desesperan. El
    universal abre la app si está y la web si no. De regalo, funciona igual en
    Android y en un escritorio, así que aquí ya no queda nada atado a iOS.

    Y `destination` lleva las **coordenadas, no el nombre**: con el nombre,
    Google buscaría y podría llevarte a otro sitio que se llame parecido —una
    ruta convincente hacia el lugar equivocado, que es peor que no tener
    enlace—. El nombre ya se está leyendo en la lista.

    Los POIs **no** entran en el contexto. Son la fuente cara y poco fiable
    (Overpass, decisión 22), así que van aparte y quien llama decide si los
    paga: la pantalla rápida no los pide.

    Y un renombre que no es cosmético: `ai_orchestrator.build_context()` pasa a
    llamarse `formatear_para_prompt()`. Lo que hace es **renderizar** el
    contexto como texto, no construirlo, y tener dos funciones llamadas
    "contexto" que devuelven cosas distintas —una datos, otra una cadena— es la
    ambigüedad que este proyecto ya evitó llamando de forma distinta a
    `waypoints.capturado_en` y `notes.created_at`.

33. **Se quita la FUENTE de Overpass del camino normal, no el aviso.** El
    usuario pidió "quitar los avisos porque no funcionan", y el aviso tenía
    razón: los tres espejos fallan y cuestan **31,3 s por petición** medidos
    desde el servidor (decisión 22). Eso era el 70 % de lo que tardaba la
    pantalla, gastado en no obtener nada. Silenciar el aviso habría convertido
    un fallo ruidoso en uno silencioso, que es exactamente lo que se evitó al
    descartar el espejo suizo que respondía `200` con cero elementos.

    **Se descarta quitarlo del todo**, que era la otra opción sobre la mesa. Sin
    POIs se pierde la distinción `lista_cercana` / `conocimiento_general`, y con
    ella la única forma que tiene la app de decir "esto está verificado en el
    mapa" en vez de "esto me lo sé de memoria". Poder distinguirlo es lo que
    hace fiable una recomendación; renunciar a ello para ahorrar una espera que
    ya no se paga habría sido un mal cambio.

    Lo que se hace en su lugar sale gratis de la caché que ya existía:

    - **buscar** es un botón (`/api/pois`), donde esperar treinta segundos es
      una decisión de quien pulsa y no un peaje;
    - **`/api/recommendations` usa `pois_cacheados()` y NUNCA espera a
      Overpass.** La caché dura 7 días, así que buscar una vez en un sitio deja
      los puntos disponibles toda la semana para las recomendaciones de esa
      zona — que es justo como se viaja en camper, parándose días en la misma
      comarca.

    Y la pieza que impide que esto se convierta en otro fallo mudo:
    `pois_cacheados()` devuelve **`None` cuando no hay nada cacheado y `[]`
    cuando se buscó y no había nada**. Devolver `[]` para los dos casos era lo
    cómodo y habría hecho que la app dijera "aquí no hay nada que ver" sin haber
    mirado. Los cuatro estados de `contexto.Fuente` (decisión 32) sirven tal
    cual para expresarlo, sin inventar vocabulario nuevo: `ok`, `sin_datos`,
    `no_consultada`, `fallo`.

34. **La luna es híbrida: la fase se calcula, la salida se consulta.** Y no es
    indecisión, es que las dos mitades tienen costes distintos.

    El motivo de calcular la fase está **medido**: `api.met.no` devuelve la fase
    de las **00:00 del día pedido, no la del momento**. Para el 28-07-2026 da
    `moonphase: 162.1` (97,6 %), que es la luna a medianoche; a las 17:20 de ese
    mismo día está al 99,1 %, y cerca de los cuartos la diferencia llega a
    varios puntos. Una tarjeta que dice "la luna de esta noche" enseñando la de
    medianoche pasada da un dato de hace diecisiete horas. Sacarlo de la API
    exigiría pedir dos días e interpolar: más código que las ~25 líneas de
    aritmética de Meeus que hay ahora.

    El segundo motivo es la degradación: si met.no falla o el proxy lo bloquea,
    la luna se queda a medias en vez de desaparecer. La salida, la puesta y el
    azimut son bastante más código —dependen de la latitud, del paralaje y de
    la refracción— y met.no los da hechos, así que se piden y degradan como
    cualquier otra fuente (decisión 9).

    **Y una corrección, porque este documento tuvo escrita una razón falsa.**
    Decía que la fase se calcula para "seguir sabiendo qué luna hay en un
    camper sin cobertura". Eso **no se sostiene**: la app corre en el servidor,
    así que un móvil sin cobertura no llega a `/api/contexto` y no ve nada —ni
    luna, ni tiempo, ni el nombre del pueblo—. El argumento de la cobertura
    vale para la cola de notas del navegador (decisión 26) y para los tiles ya
    cacheados, no para nada que resuelva el servidor. Queda escrito el error
    para que nadie vuelva a deducirlo: la decisión sigue siendo la misma, pero
    por el motivo de arriba y no por este.

    **La precisión está medida, no supuesta.** Contrastado contra `api.met.no`
    en 20 fechas de julio y agosto de 2026: peor error **0,46° de ángulo de
    fase y 0,31 puntos de iluminación**. Y contra tutiempo.net para el
    28-07-2026, 97,58 % calculado contra 97,56 % de la referencia.

    La consecuencia de partirlo así, que es lo que compra la decisión: **`luna`
    nunca es `None`**. Con met.no caído sigue habiendo fase, iluminación y
    veredicto; lo único que falta es la hora de salida, y `fuentes.luna` lo
    dice. Una fuente que degrada a la mitad en vez de desaparecer.

    **El veredicto se calcula en Python, no se le pregunta al modelo**
    (decisión 5, la misma que `water_sports()`). "Luna llena y despejado: se
    puede caminar sin frontal" es una regla explícita con sus umbrales
    razonados: el 70 % de iluminación, porque la luz de la luna no es lineal
    con la fracción visible —al 50 % da del orden de un 8 % de la luz de la
    llena, porque el terminador proyecta sombras largas sobre el propio disco—,
    así que "media luna" no es "media luz". Y **sin datos del cielo NO se
    afirma que se pueda caminar**: se dice que no se sabe si estará tapada.
    Equivocarse hacia el lado seguro importa más aquí que en ningún otro
    veredicto, porque al otro lado hay alguien de noche en un monte.

    Dos cosas que salieron de comprobar la API real antes de escribir el módulo,
    y que no se habrían visto de otra forma:

    - **met.no rechaza el User-Agent por defecto del proyecto.** El que trae
      `.env.example` lleva `example.com`, y met.no devuelve un **403 de nginx
      sin ningún mensaje**; los genéricos tipo `Mozilla/5.0` también. Con un
      contacto real devuelve 200. Reutilizar `NOMINATIM_USER_AGENT` era lo
      obvio —es el mismo contacto— y habría dejado la luna apagada para siempre
      en cualquier despliegue que no hubiera tocado esa variable, con un motivo
      indescifrable. Por eso el módulo **se niega a llamar** si detecta un
      dominio reservado por la RFC 2606 y nombra la variable que hay que
      arreglar. La lista es corta a propósito: una heurística más lista
      ("¿lleva arroba?") rechazaría contactos buenos, y un falso positivo aquí
      apaga la luna sin motivo.
    - **Sin el parámetro `offset`, met.no responde en UTC.** Comprobado: la
      misma consulta da `18:54+00:00` en vez de `20:54+02:00`. "La luna sale a
      las 18:54" habría sido falso por dos horas en España, y de las que no dan
      ningún error.

    Y un bug propio que merece quedar escrito porque es del tipo caro: la
    primera versión eligió el nombre de la fase con un bucle de umbrales
    crecientes, y **no cerraba el círculo**. Una luna nueva a 350° salía llamada
    "menguante cóncava". No daba error: solo escribía una tontería en la
    pantalla y en el prompt del modelo. Lo caza un test con la luna del
    12-08-2026, que met.no sitúa a 350,28°.

35. **La pantalla pide el contexto antes que la recomendación, y son dos
    botones.** Es la consecuencia visible de la decisión 32: *¿dónde estoy?*
    responde en menos de un segundo y no cuesta nada, y *recomiéndame algo*
    cuesta tokens y unos segundos, así que se pide aparte. Antes las dos cosas
    iban en el mismo botón y no había forma de mirar el tiempo sin pagar el
    modelo.

    Un detalle del JavaScript que no es cosmético: **los dos caminos pintan con
    la misma función** (`renderContexto`). Si cada uno pintase lo suyo, la
    pantalla acabaría enseñando cosas distintas según qué botón hubieras
    pulsado — el mismo problema que resolvió tener un solo módulo de contexto,
    en pequeño.

    **Fuera las coordenadas crudas de la tarjeta.** `38.39099, -0.52101 ·
    ±1020 m` no le dice nada a nadie. Quedan el pueblo, la comunidad y la
    altitud; las coordenadas y la precisión del GPS bajan a un detalle plegado,
    porque cuando algo parece raro son lo primero que se mira y borrarlas sería
    quitarse la única forma de depurar una chincheta en el sitio equivocado.

    **La altitud sale gratis de Open-Meteo**, en la misma respuesta del tiempo,
    así que no cuesta ni una llamada más. Con dos avisos escritos donde tocan:
    es la altitud de la CELDA del modelo y no la del punto exacto (sirve para
    "estoy a 1.200 m", no para calcular un desnivel), y si el tiempo falla no
    hay altitud — y entonces **no se pone un cero**, que sería afirmar que
    estás al nivel del mar.

    **El primer sitio de cada día se registra, y no se analiza.** Lo pidió el
    usuario. Vive en su propia tabla (`lugar_del_dia`) y en `diario.py`, con
    tres decisiones dentro:

    - **La idempotencia va en el esquema**: `UNIQUE(fecha_local)` más
      `INSERT OR IGNORE`, así que la primera del día gana y las demás rebotan
      solas. Comprobar-y-luego-insertar tendría una carrera con dos peticiones
      a la vez (decisión 23).
    - **El día es el LOCAL.** Preguntar a las 00:30 en España es del día
      siguiente en UTC, y contarlo ahí desplaza un día entero del viaje sin dar
      ningún error (decisión 29).
    - **No se registra desde `contexto.construir()`.** Era lo cómodo —ya tiene
      el sitio y la hora— y es un error: esa función la van a llamar también el
      recomendador y el chatbot, así que preguntarle algo al chatbot escribiría
      en la base de datos, y encima marcaría como "el sitio del día" uno que a
      lo mejor venía de una consulta sobre ayer. Se registra desde la ruta.

    Y se aplica la regla del proyecto sin excepción: **es un dato nuevo, así
    que no se construye nada encima hasta que demuestre que llega sin huecos**.
    `tools/diagnostico.py` enseña justamente los huecos y no el total, porque
    un total alto con huecos no es una serie: son anécdotas sueltas. Por el
    mismo motivo, **el hueco de pasos y batería está preparado y vacío**, con
    su tarjeta explicando por qué — un hueco declarado se entiende, uno ausente
    parece un olvido.

    Lo que **no** se ha tocado, a propósito: la presentación del tiempo. El
    usuario lo aplazó ("eso con el tiempo") y el orden es el que manda el
    proyecto: los datos primero, la estética después.

36. **La telemetría se SIMULA para poder construir, y la simulación vive en su
    propia fuente.** Decidido por el usuario el 28-07-2026, y conviene entender
    exactamente qué se ha dado por bueno y qué no, porque roza la regla central
    del proyecto.

    El problema: la 2d estaba esperando "días de datos sin huecos", y esa espera
    bloqueaba el perfil de actividad, el dashboard y el contexto del chatbot. No
    es una espera de trabajo, es de calendario.

    Lo que se da por bueno: **el formato**. Se pide una ejecución del atajo a
    mano y la muestra llega completa —hora ISO con su huso, pasos, batería y
    coordenadas—, se guarda, y un reenvío devuelve `duplicadas`. Eso ya no es
    una incógnita. Lo que sigue sin demostrarse es que **llegue sola y sin
    huecos durante días**, que es otra pregunta y no la responde ningún
    simulador.

    Lo que se hace: `tools/simular_telemetria.py` siembra la serie que aún no
    existe, con las horas de las seis automatizaciones reales.

    **La decisión que hace que esto no sea el error del que avisa la decisión
    11:** las muestras se guardan con `fuente = "simulado"`, nunca como
    `atajos-iphone`. Sembrarlas bajo la fuente real habría sido fabricar una
    serie inventada que dentro de un mes se lee como medida —el fallo silencioso
    en su peor forma, y esta vez con la fuente sobre la que se iba a construir
    el perfil de actividad—. Con fuente propia:

    - `UNIQUE(fuente, medido_en)` mantiene las dos series **en paralelo**, así
      que una muestra simulada nunca puede ocupar el hueco de una real ni
      desplazarla. La invariante la garantiza el esquema, no que alguien se
      acuerde (igual que la idempotencia de la ingesta, decisión 23);
    - `ver_telemetria.py` marca las filas simuladas con `~` y avisa arriba;
    - `--limpiar` las borra todas y deja intacto lo que llegó de verdad.

    Y la regla no se levanta, se **acota**: cuando toque decidir si la
    telemetría es fiable, eso se mira sobre las filas `atajos-iphone` y ninguna
    otra.

    Tres decisiones dentro del simulador, y las tres son "parecerse a lo que va
    a pasar" antes que "quedar bonito":

    - **Todo pasa por `ingest.ingest()`**, el mismo camino que una petición del
      móvil. Escribir directo en la tabla habría permitido generar muestras que
      el endpoint real rechaza, y entonces estaríamos probando contra una forma
      que no existe. Lo fija un test que valida cada muestra generada.
    - **Un día sin cobertura son muestras que FALTAN, no que llegan tarde.** El
      atajo no tiene cola ni reintenta (decisión 23): un envío fallido se pierde.
      Lo que se cura solo es el contador, porque el siguiente ya trae el
      acumulado (decisión 25). Fabricar filas con `retraso` de horas habría
      dibujado un comportamiento que este sistema no tiene, y alguien acabaría
      construyendo encima contando con él.
    - **`recibido_en` se inyecta** (`ingest(payload, recibido_en=...)`). No es
      comodidad: se siembran días pasados, y con el reloj real cada muestra de
      hace cinco días saldría con un `retraso` de cinco días, inutilizando justo
      la columna que esta fase mira. En producción no se pasa y sale del reloj,
      y hay un test que lo fija.

    Y el simulador **no envía a producción**: escribe en la base de datos local.
    Sembrar el servidor desplegado con datos inventados es otra decisión y
    tendría que tomarse a propósito, no de rebote.

37. **Guardar y enviar son cosas distintas, y confundirlas es lo que hace caro
    un chatbot.** Es la decisión que ordena la Fase 6, y sale de una objeción
    del usuario que estaba bien puesta: *"si no, se va a llenar todo de contexto
    y acabará siendo carísimo hacer peticiones"*.

    Las dos mitades:

    - **Se guarda la conversación entera** (`chat_mensajes`). Es texto en
      SQLite: no cuesta nada, y poder releer dentro de un mes qué preguntaste en
      cada sitio es la mitad del "cuaderno de a bordo" del §1. Por eso cada
      mensaje guarda **dónde** se escribió: la ubicación de mañana no sitúa la
      pregunta de hoy.
    - **Se envían solo los últimos 3 turnos** (`chat.VENTANA_HISTORIAL = 6`).
      Eso sí se paga en tokens, en **cada** mensaje, y crecería sin parar.

    Y es un fallo de los que no dan error: si la ventana se rompe, todo sigue
    funcionando y lo único que pasa es que la factura sube con el viaje —lo
    último que alguien mira—. Por eso `ventana()` es una función con nombre y
    con test propio en vez de un `[-6:]` escondido dentro del prompt, y por eso
    hay un segundo test que comprueba que **el prompt la usa**: recortar y
    enviar el recorte son dos cosas distintas.

    La misma regla se aplica al contexto, y ahí es donde está el trabajo de
    verdad: **entra todo lo que se puede recoger en directo, pero resumido**.
    `metricas.py` no vuelca las muestras (hoy 81, en un mes más de mil): da los
    pasos de hoy, el máximo por día de la semana y la media. `viaje.py` no
    vuelca las notas: da los agregados y las **diez últimas** con su texto
    recortado. El resto sigue en la base de datos y se consulta desde el mapa.

    **Cómo habla con el modelo, y la alternativa que se descarta.** Se reutiliza
    `LLMProvider.generate(system, context, schema)` tal cual, con un esquema de
    una sola clave y el historial empaquetado como texto dentro del contexto. Lo
    obvio habría sido añadir un método `converse()` al `ABC`, y se descarta:
    obligaría a implementarlo en los cuatro proveedores —`OllamaProvider` ni
    siquiera está escrito— para ganar, con un usuario y tres turnos,
    exactamente nada. Es la decisión 18 otra vez. Lo que se pierde, dicho:
    el proveedor no puede cachear el prefijo de la conversación como haría con
    turnos reales.

    Tres cosas más que son decisiones y no detalles:

    - **La pregunta se guarda ANTES de llamar al modelo.** Si el proveedor falla
      o se agota la cuota, la pregunta no se pierde. Es el criterio de "archivo
      primero, fila después" de la decisión 27: entre dos fallos, el recuperable.
    - **Sin caché**, al revés que las recomendaciones (decisión 6). La clave
      tendría que incluir la pregunta *y* el historial, así que casi nunca
      acertaría; y dos preguntas iguales en momentos distintos merecen
      respuestas distintas, que es lo que una conversación es.
    - **Sin cola offline**, al revés que las notas (decisión 26). Una nota
      escrita sin cobertura no existe en ningún otro sitio y hay que salvarla;
      una pregunta sin respuesta no vale nada, y reintentarla dos horas después
      daría una respuesta sobre un sitio en el que ya no estás.

    Y lo que el prompt **no puede afirmar**, que salió al mirar el texto real
    generado y son tres fallos silenciosos de manual:

    - un dato **simulado** se marca en el propio texto que lee el modelo, no
      solo en un campo del JSON. Sin eso, Kimi respondía "hoy llevas 12.757
      pasos" con total seguridad sobre una cifra que nos hemos inventado;
    - **sin muestra de hoy no se dice que no ha andado.** Un bloque titulado "su
      actividad de hoy" al que le faltan los pasos se lee como cero pasos, y a
      las 00:30 lo normal es que aún no haya llegado ninguna muestra;
    - **no se afirma que no hay POIs sin haberlos buscado.** El chatbot nunca
      llama a Overpass, así que su lista siempre está vacía; traducir ese vacío
      a "no hay nada mapeado aquí" haría que el modelo descartara la zona por un
      dato inventado por nosotros. Es el corolario de la decisión 22 llegando
      hasta el prompt.

38. **El aviso de disco mide lo NUESTRO contra una cuota declarada, no el
    espacio libre del volumen.** Es un fallo de la decisión 11 que sobrevivió
    meses en el peor sitio posible —la herramienta con la que compruebas si el
    despliegue está sano— y que nadie vio porque **imprimía una línea verde**.

    `shutil.disk_usage()` contesta por el sistema de archivos, y en
    PythonAnywhere eso son **1,6 TB**: la cuota de 512 MB es un límite de la
    **cuenta**, impuesto aparte. Con ese número, el aviso de "por debajo de 50
    MB" no podía saltar jamás, y el presupuesto de las miniaturas —que es lo
    siguiente que toca— colgaba de él.

    Ahora la cuota se **declara** (`DISCO_CUOTA_MB`, 512 por defecto) porque no
    se puede preguntar, y se compara contra lo que ocupamos. Tres decisiones
    dentro:

    - **Se suma `st_blocks * 512`, no `st_size`.** Es lo que mide una cuota y lo
      mismo que da `du`, que es con lo que se va a contrastar desde la consola
      del servidor. `st_size` ignora el redondeo a bloques, y un virtualenv son
      decenas de miles de archivos pequeños: la diferencia no es cosmética.
    - **Se mide el repositorio y el virtualenv, y el virtualenv solo si cae
      fuera del repo.** En el servidor vive en `~/.virtualenvs/`; en local suele
      ser un `.venv/` dentro, que el recorrido del repo ya suma. Contarlo en los
      dos casos daría el doble justo del mayor inquilino de la cuota y el aviso
      saltaría con la mitad del disco libre — cambiar un fallo mudo por uno
      ruidoso *y* falso no es una mejora.
    - **El hueco se declara.** Contra la misma cuota cuentan los logs de
      PythonAnywhere y cualquier cosa del `$HOME`, que aquí no se recorren. Así
      que la cifra es un **suelo** y lo dice; `du -sh ~` sigue siendo la verdad
      de referencia. Medido en local: 153 MB contra los 157 de `du`, y la
      diferencia son las entradas de directorio. Un hueco declarado se entiende;
      lo que se estaba arreglando es justo lo contrario.

    Y `libres_mb()` es una función con nombre y con test en vez de tres líneas
    dentro del diagnóstico, por la regla de siempre: lo único que **decide** algo
    aquí es si sale un aviso o no, y eso es exactamente lo que estuvo roto sin
    dar la cara.

39. **El diagnóstico mide continuidad, no volumen — y no cuenta lo simulado.**
    Arreglado el disco quedó a la vista que la herramienta tenía el mismo
    problema en más sitios: imprimía cifras que **parecían** contestar la
    pregunta y no la contestaban.

    La peor era la telemetría. Decía `81 muestras, última medida <ISO>`, y las
    dos mitades engañaban:

    - **el total mezclaba lo real con lo simulado.** La decisión 36 separó las
      dos series en la tabla para que una simulación no pudiera pasar por dato
      medido — y luego la herramienta que se abre en el servidor las volvía a
      sumar. La garantía estaba en el esquema y se perdía al leer, que es donde
      nadie la estaba vigilando. Hoy salen separadas y lo simulado va marcado,
      igual que en `ver_telemetria.py`. Con la base de datos de desarrollo, la
      línea pasó de un tranquilizador `81 muestras` a `0 muestras REALES`.
    - **un total no dice si hay huecos**, y lo que cierra la 2d no es el volumen
      sino la continuidad: seis envíos diarios que llegan tres días y fallan dos
      suman igual que cinco días completos.

    De ahí sale `metricas.cobertura()`, con dos decisiones dentro:

    - **La ventana va de la primera muestra hasta HOY, no de hoy hacia atrás.**
      Contando los últimos siete días, el día siguiente a montar las
      automatizaciones saldrían cinco huecos que no son fallos, y un aviso que
      salta cuando todo va bien se aprende a ignorar. Que el tramo llegue hasta
      hoy tiene la otra mitad del valor: una fuente que dejó de llegar hace tres
      días no puede salir como "sin huecos", que sería certificar una serie
      muerta.
    - **`dias_incompletos` es una señal aparte, y hace falta.** Un día con dos
      muestras de seis no es un hueco y tampoco es un día bueno. Sin esa cifra,
      "sin huecos" se leería como "cerrado" con un tercio de los envíos
      perdiéndose cada día.

    Lo demás que se añadió responde al mismo criterio —¿esta línea contesta una
    pregunta que alguien se hace de verdad?—:

    - **`contexto.construir()` se prueba entero y cronometrado.** Es lo único
      que recorre el mismo camino que la app, y su tiempo es un contrato: por
      debajo de un segundo. Falla a propósito por encima de dos, porque ahí lo
      que ha pasado es que alguien devolvió una fuente lenta al camino normal
      (Overpass costaba 31,3 s, decisión 33) y eso **no da ningún error**: solo
      una app que se abandona por lenta.
    - **`hace 3 h` en vez de una marca ISO.** La pregunta en una consola del
      servidor es "¿esto sigue llegando?", y restar el huso de cabeza es
      justamente el trabajo que un diagnóstico existe para ahorrar. Una muestra
      **en el futuro** se dice en voz alta: es un reloj mal puesto en el móvil, y
      si se imprimiera como "hace -3 h" se leería como recentísima.
    - **La cookie de sesión y el contacto del User-Agent**, que son dos fallos
      mudos documentados (decisiones 15 y 34) que la herramienta no miraba: uno
      deja la app en bucle de login sin mensaje, el otro apaga la luna con un 403
      de nginx sin cuerpo. El contacto se valida llamando a la **misma** función
      con la que el módulo de la luna decide si llama, para que el diagnóstico no
      pueda decir una cosa distinta de la que hace la app.
    - **Código de salida**: 0 también en modo degradado. Degradar es un estado de
      funcionamiento diseñado a propósito (decisión 9), no un despliegue roto, y
      hacerlo fallar convertiría un Overpass caído —que lo está casi siempre— en
      un rojo permanente. Solo devuelve 1 cuando la app no se puede usar.

    Y una consolidación que va en la misma dirección: `"atajos-iphone"` estaba
    escrita a mano en tres sitios. Ahora sale de `metricas.FUENTE_REAL`, porque
    esa cadena decide qué cuenta para cerrar la 2d y dos copias que se separen
    harían que una herramienta diga que hay datos reales y otra que no, sin dar
    ningún error.

40. **Cuatro pantallas, cuatro preguntas, y ninguna se repite.** La primera
    versión del panel enseñaba el tiempo (que ya estaba en Inicio) y los
    kilómetros y las comunidades (que ya estaban en el Mapa). El usuario lo
    cazó en cuanto lo abrió, y tenía razón: un dato en dos sitios acaba
    divergiendo, y mientras tanto obliga a mirar dos veces para saber lo mismo.

    | Pantalla | La pregunta | Qué NO lleva |
    |---|---|---|
    | **Inicio** | ¿Qué hago aquí, ahora? | nada histórico |
    | **Perfil** | ¿Cómo estoy, y de qué me fío? | ni el tiempo ni el viaje |
    | **Mapa** | ¿Dónde he estado? | nada del cuerpo |
    | **Chat** | Preguntar en vez de buscar | — |

    Es la decisión 32 aplicada a la interfaz: **una definición, varios
    consumidores.** El contexto se construye una sola vez en `contexto.py`, y
    cada pantalla pide lo que enseña — Inicio llama a `/api/contexto` (rápido,
    con GPS, con red), Perfil a `/api/perfil` (SQLite, sin GPS y sin red), el
    Mapa a `/api/ruta`. Ninguna pide lo que va a enseñar otra.

    La consecuencia práctica es que quitar duplicados se hace en el **payload**
    y no solo en el HTML: `Perfil` ya no devuelve `viaje` ni `progreso`. Dejar
    los campos "por si acaso" es como vuelven a colarse en la pantalla.

    Y la fiabilidad va **junto al dato**, que es lo que este proyecto exige y
    hasta ahora solo se veía desde una consola: cada fuente sale con su veredicto
    —`demostrada`, `con_huecos`, `sin_datos`, `simulada`— y con la cifra que lo
    sostiene. Tres decisiones dentro:

    - **Un día sin muestras se dibuja como HUECO, no como cero.** Un cero dice
      "no anduvo" y un hueco dice "no lo sé".
    - **Lo simulado se enseña pero no se certifica a sí mismo.** `armar()` recibe
      la telemetría dos veces —todas las muestras para pintar, solo las de
      `atajos-iphone` para dar por buena una fuente—, porque leerlas juntas
      anularía la separación que la decisión 36 puso en el esquema.
    - **Vocabulario propio, distinto del de `contexto.Fuente`.** Allí se responde
      "¿respondió la consulta?" y aquí "¿se puede construir encima?": una fuente
      que contesta bien cada vez que se le pregunta puede llevar tres días sin
      llegar.

    Y los nombres dejan de repetir "del viaje" en bucle. La app **es** el viaje;
    decirlo en cada título no informa de nada.

41. **Ninguna respuesta de `/api/` se cachea. Las páginas, sí.** Salió de un
    síntoma del usuario: metió una foto en el álbum, ejecutó el atajo y el mapa
    siguió igual.

    No había ni una cabecera de caché en el proyecto, y un GET sin
    `Cache-Control` un navegador lo puede reutilizar por su cuenta —Safari en iOS
    lo hace— con lo que la pantalla enseña la respuesta de antes **sin dar ningún
    error**: parece que la importación no llegó cuando lo que pasa es que no se
    ha vuelto a preguntar. Es el fallo mudo de la decisión 11 en el sitio más
    confuso posible, porque hace dudar del atajo, que es lo caro de depurar.

    Se pone en un `after_request` y no vista por vista: la regla es "todo lo que
    cuelga de `/api/`", y una lista escrita a mano se quedaría corta en el
    siguiente endpoint sin avisar (decisión 19).

    **Y solo la API.** El HTML y el JavaScript se siguen cacheando a propósito:
    es lo que hace que las páginas abran con mala cobertura (decisión 28).
    Marcarlos `no-store` habría cambiado un fallo por otro peor.

    Corolario que ya está puesto: la fila de *Fotos* del Perfil dice **cuándo fue
    la última importación**, no cuándo se hizo la foto. La pregunta tras ejecutar
    el atajo es "¿ha entrado lo que acabo de mandar?", y una foto de hace tres
    semanas puede llegar hoy.

    **Y la otra mitad, que faltaba: los estáticos llevan `?v=<mtime>`.** Aquí se
    decidió que el HTML y el JavaScript SÍ se cachean; lo que no había era forma
    de invalidarlos. En PythonAnywhere los sirve nginx **sin `Cache-Control` ni
    `ETag`**, solo con `Last-Modified` (comprobado con `curl -I`), así que Safari
    aplica caché heurística y puede seguir con el archivo viejo días después de
    desplegar: pulsas *Reload*, el servidor ya tiene el código nuevo, y el móvil
    ejecuta el de antes. No da ningún error — la pantalla se comporta como la
    versión anterior — y entonces se depura el despliegue en vez del código, o
    peor, se valida a ciegas una versión que no es. Lo resuelve un
    `@app.url_defaults` que añade la query a todo `url_for('static', ...)`, sin
    tocar plantillas. `mtime` y no un hash del contenido: `git pull` reescribe la
    fecha, que es exactamente el momento en que la caché debe romperse.

42. **Un id muerto en el JavaScript no lo caza ninguna prueba de Python.** Al
    llevarse las métricas a Perfil (decisión 40) desapareció `metricas-card` de
    `index.html`, pero `app.js` lo seguía escondiendo en `hideAll()`. Resultado:
    pulsar *¿Dónde estoy?* —**el botón principal de la app**— lanzaba un
    `TypeError`, y como `hideAll()` estaba **fuera** del `try`, no había ni catch
    que lo contara ni finally que soltara el botón: se quedaba deshabilitado
    para siempre y la pantalla en blanco, **sin un solo mensaje**. Los 511 tests
    pasaban, porque eran todos de Python y el HTML lo pinta el navegador.

    Dos arreglos, y el segundo importa más: se va el id muerto, y `hideAll()`
    entra en el `try`, de modo que el próximo fallo de esta familia salga escrito
    en la interfaz en vez de dejar la app muda.

    Y `tests/test_frontend_ids.py`, que comprueba que cada id nombrado por el
    JavaScript existe en la plantilla que lo sirve. Se verificó que **falla** al
    reintroducir el bug, que es la única forma de saber que un test sirve.

    La lección general: **la frontera entre Python y el navegador no tiene red**,
    y es justo por donde pasan los renombrados. Cada vez que algo se mueva de
    pantalla, es el sitio donde mirar.

43. **No se paraleliza lo que escribe en la misma base de datos.** Es la
    corrección más cara del proyecto y la que más fácil sería volver a cometer,
    porque el código paralelo era "el correcto".

    `contexto.construir()` lanzaba sus tres fuentes con un `ThreadPoolExecutor`:
    tres llamadas de red independientes, y en paralelo se paga la mayor en vez de
    la suma. En el portátil, impecable. Medido en PythonAnywhere con coordenadas
    nuevas:

    ```
    reverse_geocode   0.56s en frío   0.05s cacheado
    get_weather       0.57s en frío   0.05s cacheado
    efemerides        0.47s en frío   0.05s cacheado
    construir()      34.20s  ← con las tres YA cacheadas
    ```

    Treinta y cuatro segundos para envolver 0,15 s de trabajo. **En serie: 0,18 s**
    en el mismo servidor.

    **El motivo NO es que los hilos sean caros**, que fue la primera explicación
    y era falsa: montar un pool de tres y ejecutar tres tareas vacías cuesta
    0,00 s ahí mismo. Lo que se atasca es lo que hacen dentro — las tres fuentes
    leen y **escriben** la caché de SQLite, y en PythonAnywhere la base de datos
    vive en un disco de red donde el bloqueo de escritura se paga carísimo. Queda
    escrito el error y no solo la conclusión, porque desde el motivo falso la
    regla sería "aquí no se paraleliza nada" y eso descartaría casos donde sí
    conviene.

    **Por qué no se vio antes, que es la parte reutilizable:** el diagnóstico
    medía siempre las mismas coordenadas, ya cacheadas, y decía 0,05 s. El móvil
    pedía el sitio donde estabas, que no se había consultado nunca. La misma
    función, dos números que se diferencian en un factor de setecientos. **Un
    punto de prueba que nunca cambia deja de probar la parte que falla**, así que
    `tools/diagnostico.py` usa ahora el último sitio conocido —de `lugar_del_dia`,
    de la telemetría **real**, o de la última foto— y dice de dónde salió.

    Y tenía una segunda mitad peor que la lentitud: en el plan gratuito hay **UN
    worker**, así que mientras esa petición esperaba, abrir Perfil o el Mapa
    fallaba con un «Load failed» que no tenía nada que ver con ellos. De ahí
    salen tres cosas más: el timeout de las fuentes rápidas baja de 10 s a 6 s
    (con todo en serie, ese número **es** el peor caso de la pantalla, y las
    cuatro APIs responden en 0,2-0,3 s), el contexto gana el `AbortController`
    que las recomendaciones y el chat ya tenían, y ese texto crudo de Safari se
    traduce en las cuatro pantallas, porque «Load failed» no dice qué pasó ni qué
    hacer y suena a pantalla rota.

    Corolario para cuando se quiera volver a intentar: **se mide en el servidor**
    (`tools/medir_contexto.py`), no en el portátil, donde las dos versiones salen
    a 0,00 s.

44. **El log registra también los aciertos, o el silencio no significa nada.**
    Metes una foto en el álbum, ejecutas el atajo, el mapa no cambia. Miras el
    log y no hay ninguna línea. ¿El atajo no envió, o envió y se guardó? Las dos
    cosas se ven igual y se arreglan en sitios opuestos. Costó una mañana.

    Ahora las importaciones y las ingestas dejan rastro con sus cifras. Que en
    los puntos salga `duplicados: 6` es la respuesta normal al reenviar el álbum
    entero, y que en la telemetría las duplicadas sean mayoría es la señal de que
    la ventana solapada funciona (decisión 23): son justo los números que hay que
    poder mirar para saber que la automatización sigue viva.

    Hizo falta bajar el logger a `INFO`: **Flask filtra a WARNING en producción**,
    así que esas líneas se habrían escrito en el vacío — el mismo arreglo dando
    la sensación de estar hecho sin estarlo.

    Y cuando el cuerpo no es JSON, el 400 devuelve **qué llegó**, como ya hacía
    la ingesta. Fue lo que resolvió el caso: el cuerpo llegaba **vacío**. Nada de
    esto registra cabeceras — un token en un log es un token comprometido.

    Del mismo trabajo salió `force=True` al parsear: Atajos manda el cuerpo como
    *Archivo* y entonces va sin `Content-Type`, y Flask devolvía `None` sin
    mirarlo. Exigir esa cabecera no protege de nada aquí (al otro lado hay una
    máquina con token, no un navegador con CORS); lo único que hacía era romper
    la ingesta por un desplegable mal puesto en la pantalla de un móvil.

45. **El álbum es un ESTADO, no una lista de altas.** `INSERT OR IGNORE` nunca
    borra, así que sacar una foto de `Viaje` la dejaba en el mapa para siempre. Y
    eso rompe lo que hace útil al álbum: quitar una foto es decir *"esta no
    cuenta"*, y **una curación que solo suma no es una curación** (decisión 30).

    Con `"completo": true` en el cuerpo, un envío pasa a ser el estado del álbum:
    lo que no viene, se borra.

    Va detrás de una bandera explícita y no como comportamiento normal porque
    **el precio de equivocarse no es simétrico**: una foto de más se ve y se
    quita; una foto de menos es historia borrada. Un lote parcial —una prueba a
    mano, otro camino de importación, un atajo a medio montar— no puede llevarse
    el viaje por delante sin decirlo.

    Tres protecciones que no dependen de que nadie se acuerde: una lista vacía se
    rechaza con un 400 **antes** de llegar al borrado (el caso que más miedo da:
    un atajo roto mandando cero puntos); `delete_waypoints_ausentes` con lista
    vacía devuelve 0 sin tocar nada, porque un `NOT IN ()` sin elementos vaciaría
    la tabla; y el borrado se acota a la `fuente`, así que el álbum del iPhone no
    puede tocar lo que entró por la carpeta del portátil. El borrado va **después**
    de insertar: si el insert falla, no se ha borrado nada.

    `eliminados` sale en la respuesta y en el log, y no es adorno: el atajo manda
    como mucho 300 fotos, así que si el álbum crece por encima un envío deja de
    ser el álbum entero, y ese número es la única señal de que el límite se quedó
    corto.

46. **Las pantallas se recargan al volver a la pestaña, con anti-rebote.** iOS
    **no tiene ningún disparador de "he metido una foto en el álbum"**, así que
    el envío siempre lo dispara algo. Se añadió un botón que lanza el atajo desde
    la app (`shortcuts://run-shortcut?name=…`, con el nombre en una variable de
    entorno porque lo elige quien monta el atajo), y entonces apareció el hueco
    obvio: pulsas, iOS te lleva a Atajos, vuelves… y la pantalla sigue con lo de
    antes, justo cuando quieres comprobar si ha entrado.

    `visibilitychange` recarga los **datos** y no la página: las respuestas de
    `/api/` salen con `no-store` (decisión 41), así que traen lo nuevo, y el mapa
    conserva el zoom en vez de volver a pedir los tiles.

    El anti-rebote de 3 s no es cosmético: cambiar de app y volver es un gesto
    constante en un móvil y hay **un solo worker**; sin él, cada vistazo dejaría
    esperando detrás a la petición que importa. Medido en Chrome: cinco vistazos
    seguidos hacen una petición.

    Y el cambio se **dice** —"2 fotos nuevas", "+1650 pasos"—, porque un contador
    que sube de 4 a 6 no se nota cuando acabas de venir de otra app y no te sabes
    el número de antes. Importa más de lo que parece: **cuando el envío falla, el
    síntoma es exactamente que ese número no cambia.** La primera carga nunca
    anuncia nada, que sin un valor anterior todo sería nuevo.

47. **La verificación pasa por el navegador, y se demuestra saboteándola.** Es
    el §1 de la Fase 7 y sale de la decisión 42: los 534 tests son de Python, y
    ninguno habría cazado que el botón principal de la app estaba muerto por un
    id huérfano. Lo que faltaba no eran más tests unitarios, era abrir la
    página.

    `tools/verificar.py` arranca la app, entra, y recorre las cuatro pantallas
    en Chromium: los tres botones de Inicio, los cuatro caminos de la cola de
    notas, el filtro y el *revivir* del Mapa, el reintento del Perfil tras un
    503 y una conversación entera en el Chat. Once segundos de principio a fin.

    Cuatro decisiones dentro, y la primera es la que más se va a cuestionar:

    - **Es un guion en `tools/`, no una carpeta más de `pytest`.**
      `tests/conftest.py` corta `socket.connect` para toda la suite, y Playwright
      necesita hablar por TCP con el navegador y con el servidor de prueba.
      Meterlo en la suite obligaba a un `conftest` que levantara esa prohibición
      justo donde vive la garantía de "los tests corren sin cobertura". Fuera de
      `pytest` el problema no existe y `python -m pytest -q` sigue siendo lo que
      era.
    - **Sin red y sin API keys, en dos capas.** `tools/servidor_de_prueba.py`
      dobla `requests` con respuestas enlatadas y sustituye `build_provider` por
      un proveedor falso, así que verificar no cuesta un token; y el navegador
      **aborta toda petición que no vaya a 127.0.0.1**. Lo segundo no es
      redundante: es lo que convierte "no hay cobertura" en un caso que se
      prueba en vez de en una suposición. Se dobla al nivel del HTTP y no de las
      funciones del módulo para que los parseadores reales entren en el
      recorrido.
    - **Cero excepciones de JavaScript en cada pantalla.** Es la única
      comprobación que caza fallos que nadie previó —un id huérfano, un
      renombrado a medias—, que es exactamente lo que pasó. Los errores de
      consola se filtran **por la URL de origen** y no por el texto del mensaje:
      un tile bloqueado a propósito y un 500 nuestro escriben literalmente la
      misma línea ("Failed to load resource"), así que filtrar por texto habría
      escondido el segundo para callar el primero. Y los dos errores que provoca
      el propio guion —cortar la red, forzar un 503— se descartan **uno a uno y
      por su texto exacto**, nunca vaciando la consola.
    - **Un guion de verificación que nunca ha fallado está sin estrenar.**
      `tools/verificar_sabotaje.sh` rompe la app a propósito cinco veces —un id
      muerto en el JavaScript (el bug de la decisión 42 tal cual), un id
      renombrado en una plantilla, una ruta de la API movida, el contenedor del
      mapa sin id, la lista de fuentes fuera del Perfil— y exige que el guion
      falle en las cinco. Los cinco salen cazados. Es lo mismo que se hizo con
      `test_frontend_ids.py`: comprobar que el test falla al reintroducir el bug.

    **Y encontró un fallo mudo a la primera, que es lo que justifica todo lo
    anterior:** el aviso de «el fondo del mapa no carga» se pintaba y se borraba
    solo. Leaflet emite `load` cuando ha **terminado de intentarlo**, con tiles
    buenos o sin ellos, así que el `tilesFallidos = 0` del manejador borraba el
    aviso en el mismo instante en que se había puesto. Sin cobertura el mapa
    salía gris y callado — justo lo que la decisión 28 dice que no puede pasar—
    y no daba ningún error. Se arregla escuchando `tileload`, que es un tile que
    **sí** ha cargado.

    **Lo que este guion NO cierra, y hay que decirlo:** corre en un Chromium de
    escritorio, así que no dice nada del GPS real de iOS, de la purga de
    IndexedDB a los siete días, ni de lo que tarda la app en el servidor con un
    solo worker (decisión 43: eso se mide donde corre). Cierra la distancia
    entre "la suite pasa" y "la página funciona", no la de "va en mi portátil"
    a "va en el iPhone".

48. **Lo que hacía lenta la app no era la app: era pedir otra vez lo que ya
    estaba en el móvil.** El §2 de la Fase 7 pedía que cambiar de pantalla fuese
    inmediato, y lo primero fue medirlo con `tools/medir_pantallas.py` **contra
    el desplegado**, que es la lección de la decisión 43. Medianas de 5 pasadas,
    en milisegundos:

    | pantalla | html | estáticos | api | pintar | TOTAL |
    |---|---|---|---|---|---|
    | Inicio | 455 | 0 | 0 | 52 | 514 |
    | Perfil | 394 | 1499 | 842 | 115 | 2640 |
    | Mapa | 206 | **4306** | 412 | 67 | 3921 |
    | Chat | 320 | 652 | 482 | 54 | 1246 |

    El documento no era el problema. **Los estáticos sí**, y la explicación
    estaba escrita desde la decisión 41 sin haber sacado la consecuencia: nginx
    de PythonAnywhere los sirve **sin `Cache-Control` ni `ETag`**, solo con
    `Last-Modified` (vuelto a comprobar con `curl -I`). Sin `Cache-Control`, el
    navegador aplica **caché heurística**: da por fresco el archivo un ~10 % del
    tiempo que lleva sin modificarse. Recién desplegado eso son minutos, así que
    durante las horas siguientes a un `git pull` **cada navegación revalida cada
    archivo**. Entrar al Mapa pagaba Leaflet entero otra vez.

    Y no daba ningún error: la app funcionaba, solo iba lenta, que es lo que se
    achaca al plan gratuito y no se investiga.

    El arreglo son dos piezas que ya estaban medio puestas:

    - **`Cache-Control: public, max-age=31536000, immutable` en `/static/`**, que
      **solo es admisible porque la URL lleva `?v=<mtime>`** (decisión 41): al
      desplegar cambia la URL, así que un archivo viejo cacheado para siempre no
      se puede volver a pedir. Sin la versión en la URL, esto sería la peor idea
      del proyecto.
    - **Se quita el mapeo de *Static files* de PythonAnywhere**, y esto es lo
      que menos se ve venir: mientras nginx los sirva, `/static/` no llega a la
      app y su cabecera no se aplica. El arreglo estaría desplegado **y sin
      efecto**, que es la forma más cara de creer que algo está hecho. Por eso
      `tools/medir_pantallas.py` imprime **quién los sirve** antes de la tabla.

    El precio, dicho: los estáticos pasan por el único worker. Se pagan una vez
    por despliegue en lugar de en cada salto de pantalla, y se vuelve atrás
    añadiendo el mapeo otra vez.

    **Lo que NO se hace, y es media decisión:** el encargo ofrecía precargar la
    pantalla siguiente. Con **un solo worker**, una precarga compite con la
    petición que el usuario está esperando — es la decisión 43 otra vez, donde
    lo paralelo salía peor que lo secuencial. No se toca hasta que los números
    de después digan que hace falta.

    Queda pendiente el otro bulto: `api` entre 412 y 842 ms para leer SQLite.
    Eso no lo arregla ninguna caché del navegador, y se mira cuando esta medida
    esté confirmada.

    Y tres artefactos del propio medidor, corregidos, porque los tres daban un
    número que parecía bueno: `estáticos` se calculaba como el intervalo del
    primer recurso al último —y Leaflet pide los iconos de las chinchetas mucho
    después de arrancar, así que ese hueco se contaba como red—, e **Inicio se
    daba por pintado antes de cargar su JavaScript**, porque su marcador está en
    el HTML. Un medidor con un sesgo no avisa de nada: confirma lo que ya creías.

    El tercero es el peor y merece quedar escrito aparte, porque **el medidor
    impedía justo lo que venía a medir**: en Playwright, poner **una sola ruta
    de interceptación desactiva la caché HTTP del contexto entero**. Con
    `context.route("**/*", …)` para bloquear los tiles, los estáticos se volvían
    a descargar en cada navegación, así que la primera medida tras el arreglo
    decía **3550 ms y 205.913 bytes en caliente** — con un `max-age` de un año
    puesto y funcionando. El veredicto habría sido "la caché no sirvió de nada"
    y era falso.

    Lo de fuera se bloquea ahora con un **proxy que no escucha nadie**
    (`http://127.0.0.1:9`, con `bypass` para el propio host), que corta igual y
    no toca la caché: medido, 0 bytes y 0 ms en caliente. `tools/verificar.py`
    hace lo mismo por el mismo motivo — con interceptación, la app se comportaba
    allí de una forma que no tiene en ningún navegador de verdad.

    La regla general, que vale para cualquier medida futura: **un instrumento
    que altera lo que mide no da un error, da una conclusión.** Es la decisión
    11 aplicada a las herramientas en vez de al código.

    **El resultado, medido con el instrumento ya arreglado** (TOTAL en caliente,
    contra el desplegado, ms):

    | pantalla | antes | después |
    |---|---|---|
    | Mapa | 6829 | **675** |
    | Perfil | 3737 | 1538 |
    | Chat | 1762 | 730 |
    | Inicio | 378 | 285 |

    La columna `estáticos` es **0** en las cuatro: al cambiar de pantalla ya no
    se descarga nada. Y el agujero de 1,8 s que había aparecido en el Mapa era la
    caché desactivada por el medidor, no Leaflet: `arranque` en caliente son
    20-65 ms.

    **Lo que queda, y por qué no se toca todavía:** `api` entre 298 y 593 ms.
    Desde el navegador esa cifra mezcla la latencia hasta PythonAnywhere con lo
    que el servidor gasta de verdad, y son problemas opuestos —uno se arregla
    cacheando en el cliente, el otro tocando la consulta—. Por eso lo primero es
    poder restar: cada `/api/` deja ahora en el log lo que tardó **el servidor**
    (`tools/logs.sh tiempos`). Se decide con ese número, no antes.

50. **Un suelo no es un total, y una fuente parada no "va regular".** Los dos
    salieron de la misma pregunta —*¿por qué el dashboard no enseña los pasos de
    hoy?*— y los dos son números o etiquetas que **parecen** buenos, que es la
    familia de fallos de la decisión 11.

    **El suelo.** La columna de pasos es un acumulado (decisión 25), así que el
    total de un día lo trae su último envío. Perder los de enmedio no pierde
    nada: el de las 23:55 ya trae el total, y está comprobado sobre la serie
    simulada del 27-07-2026, que perdió las 10:00 y las 14:00 y cuadra. Lo único
    que trunca un día es perder **el último**, y entonces lo que queda es lo que
    llevabas a las 18:00: el día parece de sofá y hunde la media, sin dar ningún
    error. Ahora `resumir()` los marca en `dias_parciales` mirando la hora local
    de la última muestra (`HORA_CIERRE_LOCAL = 22`) y la barra sale con `≥`.

    El criterio es la **hora**, no cuántos envíos llegaron, y esa parte no se ve
    venir: contar envíos marcaría como parcial un día que perdió dos de enmedio
    y está entero.

    Del mismo trabajo salieron dos bugs mudos más, los dos en el mismo sitio:
    `media_diaria` descartaba el **último elemento** de la lista dando por hecho
    que era hoy —si hoy aún no ha llegado nada, el último es ayer y se tiraba un
    día bueno—, y el alto de la barra se decidía con `barra.pasos` como
    condición, así que un día de **cero pasos** caía en la rama del 100 % y se
    dibujaba a tope: el peor día del viaje pintado como el mejor.

    **La fuente parada.** Los datos reales del servidor el 29-07-2026: cinco
    muestras, todas del mismo rato de la noche anterior, disparadas a mano.
    Ninguna automatización había corrido nunca. El Perfil lo contaba como huecos
    y decía *con huecos*, que es lo que también dice una serie que perdió un
    envío en un valle sin cobertura — y esa se cura sola. Dos averías distintas
    con el mismo nombre hacen esperar en vez de ir a mirar Atajos.

    `perfil.PARADA` es un estado propio, salta a las **12 h** sin recibir nada y
    lo dice antes que ninguna otra cifra. Las 12 h no son un número redondo: el
    hueco normal más largo es el nocturno (último envío ~23:55, primero a las
    06:00, unas 8 h), y un aviso que salta con todo funcionando se aprende a
    ignorar. `horas_desde()` se separa de `hace_cuanto()` porque una frase en
    castellano se lee pero no se compara, y las dos tienen que hablar de la
    misma marca de tiempo.

51. **El puerto ocupado hacía que la verificación probara OTRO servidor.**
    `tools/verificar.py` arrancaba su servidor de prueba y esperaba a que el
    puerto 5099 respondiera. Si el puerto ya estaba cogido —una verificación
    anterior que no acabó de morir— el hijo moría con *Address already in use* y
    quien contestaba era el servidor **viejo**: su base de datos y, lo que de
    verdad importa, **su código**.

    A partir de ahí el guion no verifica lo que hay en disco y **no da ningún
    error**: salía rojo por datos que no había sembrado —lo que se vio, tres
    pasadas fallando en pantallas distintas— o, en el caso peligroso, **verde
    sobre la versión de antes**. Es el instrumento que altera lo que mide de la
    decisión 49, pero peor: aquí ni siquiera medía lo que creía.

    Se mira el puerto **antes** de arrancar y se falla con el motivo y el
    comando para soltarlo, sin traza — no arrancar es cosa del entorno, y una
    traza manda a buscar el fallo en el código que se iba a verificar. El
    sabotaje número 6 ocupa el puerto y exige que lo cace.

52. **Dónde entrenar y dónde dormir salen de OSM, no de park4night.** El usuario
    pidió las dos cosas: sitios para entrenar (gimnasios, barras, parques) y
    sitios donde pasar la noche, "o alguna API tipo park4night".

    **park4night no tiene API pública.** Lo que circula son endpoints sacados
    por ingeniería inversa de su app —sin documentación, sin soporte y con
    alguno ya retirado—, y encima el dominio tendría que estar en la lista
    blanca del proxy de PythonAnywhere o no funcionaría en producción
    (decisión 21). Construir el "dónde duermo" sobre eso es deuda garantizada en
    la pieza que más falta hace estando de viaje.

    Se cubre con Overpass, que ya está montado, ya está en la lista blanca y ya
    tiene caché de 7 días. Dos categorías nuevas:

    - **deporte**: `leisure=fitness_centre` (gimnasio cerrado) y
      `fitness_station` (la barra de calistenia al aire libre). En OSM son
      etiquetas **distintas**, y buscar solo una deja fuera justo la que hay en
      la mitad de los pueblos — sin dar ningún error, solo una lista corta que
      parece decir que ahí no hay nada. Van con polideportivo y pista de
      atletismo. Fuera queda `leisure=pitch`: hay un campo de fútbol en cada
      pueblo y ahogaría al resto.
    - **servicios de camper**: `amenity=sanitary_dump_station` y agua potable.
      Es lo que convierte un sitio bonito en un sitio donde se puede parar.

    Y un detalle que no se ve venir: `_MAX_TOTAL` sube de 24 a 30. El balanceo
    reparte 5 por categoría y luego un recorte global corta por distancia, así
    que con ocho categorías las dos nuevas entraban y el recorte se las comía.
    Una categoría añadida que no aparece nunca, y nada que lo diga.

    Lo que **no** cambia: los POIs siguen detrás de un botón y fuera del camino
    normal (decisión 33), porque Overpass sigue siendo la fuente cara y poco
    fiable. Más categorías no la hacen mejor.

53. **Los incendios los pide el NAVEGADOR, y no se llaman incendios.** Dos
    decisiones que salen de comprobar la API real antes de escribir el módulo, y
    las dos cambian el diseño.

    **Quién hace la petición.** `firms.modaps.eosdis.nasa.gov` **no está en la
    lista blanca** del proxy de PythonAnywhere — comprobado sobre la página de
    la lista, donde sí están otros diez dominios de la NASA (decisión 21). Desde
    el servidor la llamada devolvería un 403 que la app leería como "fuente
    caída", y la tarjeta estaría permanentemente vacía sin que nada explicara
    por qué. Lo que sí se puede: FIRMS responde
    `access-control-allow-origin: *` (comprobado con `curl -D -`), así que el
    **navegador** llega.

    De ahí el reparto: el navegador trae el CSV crudo y `/api/incendios` lo
    interpreta. El navegador es una tubería, no un cerebro — con el veredicto en
    JavaScript no habría forma de probar los umbrales sin abrir un navegador. Y
    los parámetros (sensor, radio, días) los pone el servidor en los `data-` de
    la tarjeta: repartidos entre Python y JavaScript serían dos copias que se
    separan sin dar error.

    Esto **no** contradice la decisión 32, que prohíbe recibir el contexto ya
    construido. Allí lo que llegaba del cliente era dónde estás y qué tiempo
    hace, y un cuerpo manipulado ponía al modelo a razonar sobre un sitio
    inventado; aquí son datos públicos de satélite que solo se le enseñan a
    quien los manda, en su sesión.

    **Y las palabras.** VIIRS no ve fuego: ve **anomalías térmicas**. Medido con
    la API real el 30-07-2026, a 2 km de San Vicente del Raspeig salían dos
    detecciones nocturnas de **0,62 y 1,85 MW** — casi con seguridad industria.
    Escribir "incendio a 2 km" con eso es la alarma que se aprende a ignorar, y
    entonces tampoco se lee el día que arde el monte de al lado. El veredicto se
    calcula en Python con umbrales razonados (decisión 5, la misma que el oleaje
    y la luna) y elige las palabras por **potencia radiativa** y distancia:

    | Qué hay | Qué se dice |
    |---|---|
    | nada en el radio | "sin detecciones", **y qué no se ha visto**: VIIRS no ve fuegos pequeños ni bajo nubes |
    | detecciones flojas | "puntos de calor", y que el satélite marca hornos, industria y quemas |
    | ≥ 20 MW y a ≤ 15 km | "foco activo", e infórmate antes de dormir aquí |

    El umbral de 20 MW está muy por debajo de lo que da un incendio declarado
    (pasa de 100 con facilidad) a propósito: equivocarse hacia el lado seguro
    importa más aquí que en ningún otro veredicto.

    Dos cosas más que no se ven venir:

    - **FIRMS contesta los errores con HTTP 200 y texto plano** ("Invalid
      MAP_KEY"). Sin comprobar la cabecera del CSV, ese mensaje se parsearía
      como cero detecciones y la pantalla diría "sin detecciones" — una
      afirmación tranquilizadora y falsa. Es la decisión 5 otra vez: un 200 no
      significa que la respuesta sirva.
    - **La tarjeta se renderiza SIEMPRE, aunque no haya clave.** Ponerla dentro
      de un `{% if %}` dejaba a `hideAll()` buscando un id que no existe, que es
      literalmente el fallo de la decisión 42.

    Y no va en `/mapa`: esa pantalla es *dónde he estado* (decisión 40). Un foco
    activo es *qué pasa ahora*, y va en Inicio con el tiempo y la luna. Se pide
    **después** del contexto y sin esperarlo, para que una NASA lenta no retrase
    lo que ya estaba resuelto (decisión 33).

54. **Y una pantalla propia, porque «¿me preocupo aquí?» y «¿hacia dónde me
    muevo?» no son la misma pregunta.** La tarjeta de la decisión 53 contestaba
    la primera, y el usuario quería la segunda: *«no quiero puntos de calor,
    quiero ver los putos incendios»*. Tenía razón — un veredicto sobre 55 km no
    sirve para decidir la ruta del día.

    `/fuego` es un mapa de Leaflet con **3° a la redonda** (unos 330 km, una
    jornada de camper) y hasta 7 días. Lo que lo hace legible son tres cosas, y
    ninguna es decoración:

    - **Color por antigüedad**, el mismo código que el tutorial de la NASA:
      <1 h, 1-4 h, 4-12 h, >12 h. En un incendio grande hay cientos de
      detecciones acumuladas y lo único que dice hacia dónde va el frente es
      **cuáles son de la última hora**. Se pintan de más viejo a más nuevo para
      que los recientes queden encima.
    - **Tamaño por potencia**, con raíz cuadrada y no lineal: con lineal, un
      foco de 300 MW tapa media provincia y esconde justo lo que hay al lado.
    - **Un filtro de «solo focos potentes»**, que es lo que convierte el mapa en
      útil. Medido con la API real desde Alicante, 3 días y 3°: **219
      detecciones, de las que 26 son ≥ 20 MW y señalan un incendio de 117 MW a
      168 km**. Las tres más cercanas eran de 0,6 a 1,9 MW — la industria de
      siempre. Sin filtro, 219 puntos indistinguibles; con él, un frente y una
      dirección. Filtrar **no vuelve a consultar**: los datos ya están, y
      repetir la petición por marcar una casilla es tiempo regalado con mala
      cobertura.

    Detalles que no se ven venir y ya costaron una vez:

    - **`acq_time` llega sin ceros a la izquierda**: las 01:58 son `"158"`.
      Leerlo como cuatro dígitos sin rellenar da una hora que no existe y la
      detección sale del color equivocado — el propio tutorial de la NASA usa
      `zfill(4)` por esto. La hora se calcula en el servidor y no en el
      JavaScript: esa aritmética repetida en dos sitios es donde se cuela un
      desfase que no da ningún error, solo colores mentirosos.
    - **El marcador de «estás aquí» también es un círculo de Leaflet**, así que
      contar focos contaba uno de más. Los focos llevan `className: "foco"`.
    - **El techo de 600 detecciones recorta las MÁS LEJANAS**, nunca por orden
      de llegada del CSV: así el foco grande no se cae por casualidad.

    **Y dos ámbitos, porque el país entero es otra pregunta.** El selector ofrece
    *a mi alrededor* (3°) y *España*, con dos consecuencias medidas:

    - **FIRMS acepta como mucho 5 días**, no 7: con 7 devuelve `Invalid day
      range` y —como todo en esta API— lo manda con **HTTP 200**. El desplegable
      llegó a ofrecer una opción que siempre fallaba.
    - **España en 3 días son 986 filas y 80 KB de CSV**, y con 5 días se pasa del
      techo de 128 KiB que la app impone a cualquier cuerpo. Se sube a 1 MiB
      **solo en esa ruta**, que es justo lo que preveía la decisión del techo: no
      se toca el límite global, que es el que protege la ingesta.

    Y el recorte al techo de 600 cambia de criterio con el ámbito: se conservan
    **las más potentes**, no las más cercanas. Recortar por cercanía dejaba fuera
    un incendio de 200 MW a 300 km para hacer sitio a cien hornos del polígono de
    al lado — y en un mapa de país eso es perder justo lo único que importa.

    **Y el fallo que se vio en pantalla: «28 focos potentes» y dos manchas.** No
    faltaban puntos: sobraba una palabra. VIIRS ve píxeles de 375 m y un
    incendio declarado enciende decenas seguidos, así que a zoom de país se
    superponen en una sola mancha. El número contaba **detecciones** y quien lo
    leía entendía **fuegos** — el mapa y el texto decían cosas distintas y el
    que mentía era el texto.

    `agrupar()` junta lo que está a menos de **2 km** (algo más de cinco
    píxeles: junta un frente y separa dos incendios de un mismo valle). Medido
    con la API real, España en 3 días: **600 detecciones, 211 potentes, 29
    focos**. Tres decisiones dentro, y las tres se ven en el mapa:

    - **El grupo se forma empezando por el píxel MÁS POTENTE**, no por el
      primero del CSV: así el punto cae en el corazón del incendio y no en su
      borde, que es lo que se mira para decidir por dónde no pasar.
    - **La hora del foco es la de su detección más reciente**, no la media.
      Promediarla pintaría apagado un frente con cien píxeles de anteayer y uno
      de hace media hora.
    - **El filtro de potencia se aplica al FOCO**, por su pico. Lo que dice si
      un grupo es industria o un incendio es su máximo, no cada píxel suelto.

    **El mapa oficial de la NASA no se puede empotrar.** Manda
    `X-Frame-Options: SAMEORIGIN` (comprobado con `curl -I`), así que un
    `<iframe>` saldría en blanco y sin decir por qué — el fallo mudo de siempre.
    Lo que sí se puede es abrirlo con un enlace centrado donde estés, y eso está
    puesto: su hash es `#d:24hrs;@lon,lat,zoomz`, con el orden **lon,lat**, al
    revés que todo lo demás del proyecto.

    Lo que sigue sin poder afirmarse, y se dice en la pantalla: el satélite pasa
    dos veces al día y no ve fuegos pequeños ni bajo nubes. Que no salga nada no
    garantiza que no haya nada.

55. **La trama significa «no lo sé», y por eso las cinco pantallas son la misma
    app.** Es el §1 de la Fase 8. El encargo pedía un sistema visual y una
    identidad «que no sea el gris por defecto», y la trampa estaba en de dónde
    sacarla: lo que había —crema `#f6f4ee` con acento verde bosque— es
    literalmente uno de los tres aspectos que produce cualquier generador cuando
    no se le pide nada, así que «quedarse como está» tampoco era neutral.

    La identidad sale de lo que esta app **es**: cada número viene con su
    procedencia (`demostrada`, `con_huecos`, `parcial`, `simulada`, `parada`).
    Eso no lo hace ninguna otra, y es lo que se convierte en gramática:

    | Forma | Qué dice |
    |---|---|
    | sólido | medido |
    | discontinuo | hay dato, pero es un suelo: eso o más |
    | **rayado** | hueco: no lo sé |
    | magenta | la fuente falló |

    Tres decisiones dentro, y ninguna es de gusto:

    - **Es una gramática de FORMA, no de color.** Sobrevive al modo oscuro, al
      daltonismo y a mirar el móvil al sol — tres cosas que pasan en un camper y
      ninguna en un portátil. Ya existía a medias en las barras de pasos
      (`.barra-hueco`, decisión 50); aquí se define **una vez** en
      `--trama-hueco` y la usan las barras, el filete de las tarjetas y la cola
      de notas. Una definición, varios consumidores (decisión 32).
    - **El filete lo pone el DATO, no la plantilla.** `perfil.js` calcula
      `data-certeza` de la serie real y manda lo peor que sepa: un solo día
      simulado quita a la tarjeta entera el derecho a presumir de medida, porque
      quien la mira no sabe cuál de las barras era la inventada. Escribirlo en el
      HTML habría sido una afirmación fija sobre algo que cambia cada día.
    - **Atención ≠ fallo, y estaban pintados igual.** «El sendero del faro está
      expuesto al viento» es una advertencia sobre el MUNDO; «estos pasos son
      simulados» es una advertencia sobre el DATO. Compartían `.aviso`, así que
      el primero salía en el color de «algo se ha roto». Ahora el ámbar avisa y
      el magenta dice que lo que ves no es lo que crees.

    **Y el magenta está elegido por descarte, no por gusto:** el rojo, el naranja
    y el amarillo ya son los focos de la NASA en `/fuego`, y los verdes los
    veredictos del tiempo. Un aviso naranja al lado de ese mapa se lee como un
    incendio más. De regalo es la convención de las cartas náuticas.

    **Cero descargas de tipografía.** Un archivo de fuente son decenas de KB con
    mala cobertura y un dominio más que `tools/verificar.py` bloquea. La
    personalidad se gasta en la escala, el tracking y en dar a las medidas su
    propia voz: `ui-monospace` con `tabular-nums` para pasos, MW, horas y
    coordenadas, que son lecturas que se comparan **entre filas** y con cifras de
    ancho variable bailan. Solo cifras: al aplicarla a frases enteras («a 400 m ·
    30-45 minutos · verificado en el mapa») se lee peor, no mejor. Los iconos son
    SVG escritos en la plantilla por lo mismo.

    **La navegación baja al pie.** Se usa a una mano y en marcha, y el pulgar no
    llega a la franja superior de un iPhone actual. 56 px de alto, por encima del
    mínimo de 48.

    Dos bugs que llevaban ahí desde antes y solo se vieron al mirar la pantalla:

    - **`leaflet.css` se carga en `head_extra`, o sea DESPUÉS del nuestro**, así
      que con la misma especificidad ganaba su `#ddd` y **el mapa salía gris
      claro en modo oscuro** — un rectángulo que deslumbra, de noche, justo en la
      pantalla de incendios. No daba ningún error. Se arregla con un punto más de
      especificidad (`body .leaflet-container`), sin tocar el archivo
      vendorizado, que tiene que seguir siendo el de Leaflet tal cual
      (decisión 28). La atribución necesitó dos, porque Leaflet la escribe como
      `.leaflet-container .leaflet-control-attribution`.
    - **Dos variables CSS que no existían**: `.pois-grupo` usaba
      `var(--borde, …)` cuando el token es `--border`, así que siempre pintaba el
      gris oscuro de reserva, también en claro; y `.pois a:hover` usaba `--fg`,
      que tampoco existe, así que el hover no cambiaba nada. Un `var()` a un
      token inexistente **no da error**: cae al valor de reserva o hereda.

    **Lo que costó, medido CONTRA EL DESPLEGADO**, que es el único sitio donde
    esto significa algo (decisión 43). El CSS pasa de 5,5 a 11,6 KB comprimidos y
    se paga **una vez por despliegue**, porque los estáticos van `immutable` con
    `?v=<mtime>` (decisión 48). TOTAL en caliente, en milisegundos:

    | pantalla | decisión 48 | tras el rediseño |
    |---|---|---|
    | Mapa | 675 | **504** |
    | Perfil | 1538 | **716** |
    | Chat | 730 | **409** |
    | Inicio | 285 | **180** |

    Mejoran las cuatro, y la columna `estáticos` sale **0** en todas: los seis
    kilobytes de más no se pagan al cambiar de pantalla. La medida confirma
    además que el mapeo de *Static files* sigue quitado en PythonAnywhere —el
    medidor imprime `los sirve la app: public, max-age=31536000, immutable`—,
    que es la comprobación sin la cual el arreglo de la decisión 48 estaría
    desplegado y sin efecto.

    **Y fuera las coordenadas de todas las pantallas, no solo de una.** La
    decisión 35 las sacó de la tarjeta de Inicio y quedaron en otros dos sitios
    donde nadie las había mirado: el Chat abría con «Preguntas desde 43.5622,
    -6.1456 (±18 m)» y el Mapa caía a las coordenadas como **título** de una nota
    cuyo `lugar` fuera nulo. Un sitio se dice con su nombre; un número que hay
    que traducir mentalmente no es información, es trabajo.

    El Chat resuelve el nombre con `/api/location` y **no** con `/api/contexto`:
    lo único que hace falta es el sitio, esa ruta solo llama a Nominatim —
    cacheado por coordenada redondeada a ~110 m (decisión 3)— y pedir el contexto
    entero traería el tiempo y la luna para tirarlos, que con un solo worker es
    espera que se le quita a la pregunta de detrás (decisión 43). Los dos fallos
    posibles se dicen distinto porque se arreglan en sitios distintos: sin GPS no
    se puede preguntar, sin nombre sí — al modelo le llegan las coordenadas
    igual. Y en el Mapa se distingue «Sitio sin nombre» de «Sin sitio», que no
    son lo mismo: el primero está en el mapa y el segundo no.

    Sobreviven en un solo sitio, plegado: el desplegable de Inicio, ahora
    titulado *«¿La ubicación parece mal?»*. Ahí son lo único con lo que se depura
    una chincheta en el sitio equivocado, y el título dice para qué sirve en vez
    de invitar a abrirlo.

    **Segundo pase, Mapa y Perfil: seis cifras iguales son una tabla.** El §2 del
    encargo pedía enseñar el viaje «como algo que avanza y no como una tabla», y
    el Mapa daba seis cajas idénticas donde «223 km» y «4 racha» pesaban lo
    mismo. Se separan por lo que **son**: kilómetros, días y sitios son lo que
    crece al viajar y se quedan arriba en grande; notas y fotos son cuánto has
    **registrado** —otra cosa— y bajan a una línea en pequeño. Fuera las cajas:
    lo que separa tres cifras es el espacio, no tres bordes.

    Y el tablero de comunidades gana **la barra que le faltaba**. Era el único
    dato de la pantalla que dice cuánto queda, y estaba dibujado como diecinueve
    píldoras que no dan la proporción de un vistazo. Las píldoras siguen —las que
    faltan tienen que verse (decisión 29)—, con el texto **antes** de la barra:
    «3 de 19» se lee entero, y la barra confirma; al revés hay que estimar y
    luego buscar el número que ya estaba.

    En el Perfil, los pasos de hoy pasan a ser la cifra que preside, y las
    fuentes llevan la gramática **fila por fila**: sin eso, «fiable» y «simulado»
    tenían el mismo aspecto salvo por una palabra al final de la línea, en la
    tarjeta cuyo único trabajo es distinguirlos. La correspondencia entre los
    estados del Perfil y la gramática se escribe una vez (`CERTEZA_DE_ESTADO`)
    porque son vocabularios distintos a propósito: allí se contesta «¿se puede
    construir encima?» y aquí «¿lo sé o no?».

    Dos correcciones del mismo pase, y las dos son la misma regla: **la
    monoespaciada es para cifras, no para frases**. «9100 pasos hoy» entero en
    mono sale con los espaciados de una tabla y se lee como una salida de
    consola; ahora el número va en mono y la palabra en la del texto. Y cuando no
    hay muestra, ese hueco lleva una frase — que a cuerpo de titular se lee como
    un grito—, así que el JavaScript la marca: el CSS no puede saber cuál de los
    dos textos ha entrado.

    **Y el rediseño encontró su propio fallo, que es la prueba de que las redes
    puestas sirven:** el estado vacío del Chat se creó con un `id`, y
    `test_frontend_ids.py` lo cazó al primer intento — el test de la decisión 42
    haciendo exactamente su trabajo. Se arregló buscando por clase: un nodo que
    crea y destruye el propio JavaScript no tiene por qué estar en el HTML. Y
    `tools/verificar.py` daba «conversación borrada» por «hilo sin texto», que
    dejó de ser lo mismo al haber un vacío escrito; ahora cuenta **burbujas**,
    que es más preciso y no más laxo.

56. **El diario tiene pantalla propia, y las miniaturas no tienen tabla.** Es el
    §3 de la Fase 8, y son dos decisiones que se apoyan.

    **La pantalla.** El «día a día» vivía dentro del Mapa, contestando la
    pregunta de otra pantalla: el Mapa es *dónde he estado* —el avance, el
    trayecto, los sitios a los que vuelves— y el Diario es *qué pasó*. Son las
    dos caras del §1 de este documento, decidir y recordar, y mezclarlas dejaba
    el Mapa en 4.886 px de alto. Se **mudó**, no se duplicó: las dos beben del
    mismo `/api/ruta` y ninguna enseña lo de la otra (decisión 40).

    Dentro del muro, el orden es cronológico y las fotos **no** se separan de las
    notas: así es como se recuerda un día. Lo único que se agrupa son las fotos
    seguidas, que se pintan como una tira y una nota corta. Y los días van del
    más reciente al más antiguo, al revés que el Mapa: allí se recorre el
    trayecto desde el principio, y aquí lo que quieres ver al abrir es lo de hoy.

    **Las miniaturas no tienen tabla, y esa es la decisión que simplifica el
    resto.** El nombre en disco se deriva de `(fuente, archivo)` con un hash, así
    que saber si una foto tiene miniatura es preguntarle al disco. Una columna en
    `waypoints` podría afirmar que la hay cuando el archivo se perdió al
    desplegar —o al revés— y esa desincronización no daría ningún error: solo
    imágenes rotas o huecos que no lo son. Es la idea de la decisión 23 otra vez:
    la invariante la garantiza la construcción, no que alguien la mantenga.

    Que el nombre lo derivemos **nosotros** es la decisión 27 y no se rediscute:
    llega de iOS y podría traer `../` o separadores. Se descartó sanearlo con
    `secure_filename()`, porque sanear puede colapsar dos nombres distintos en el
    mismo archivo y entonces una foto se comería la miniatura de otra sin dar
    ningún error. El separador `\0` entre los dos campos tampoco sobra: sin él,
    `("ab", "c")` y `("a", "bc")` darían el mismo hash. Y al servir, el nombre de
    la URL se valida contra la **forma** que producimos (32 hex + `.jpg`) en vez
    de limpiarlo: una lista blanca de forma no se escapa con `..%2f`.

    Las dos preguntas que el encargo dejaba abiertas, contestadas:

    - **Al quitar una foto del álbum se borra también su miniatura.** Quitarla es
      decir «esta no cuenta» (decisión 45), y dejar la imagen sería gastar cuota
      para siempre en algo que no puede ver nadie. Hizo falta
      `waypoint_archivos_ausentes()`, porque un `DELETE` devuelve un contador y
      no nombres. El disco se limpia **después** de que la fila se haya ido: al
      revés, un fallo en el `DELETE` dejaría un punto en el mapa apuntando a una
      miniatura que ya no existe — un hueco visible contra un archivo de más que
      no molesta.
    - **Cuando no cabe, se rechaza y se dice; no se borra lo más antiguo.** Un
      507 con los MB usados. Es la asimetría de siempre: una miniatura de más se
      quita a mano, y una borrada sola es una foto del viaje que desaparece sin
      que nadie lo pida. El presupuesto son 40 MB —unas 5.000 fotos— y se mide
      contra las miniaturas y **no** contra la cuota global de la cuenta: lo
      global lo llenan el virtualenv y el repositorio, que no crecen, y medirlo
      sería recorrer el `$HOME` entero en cada foto recibida.

    Detalles que decidieron algo:

    - **Ruta aparte de `/api/waypoints`.** Allí viaja un JSON con hasta 300
      fotos; aquí, binario. Juntarlos haría que un fallo en cualquiera tirase el
      lote entero, y los puntos son lo que dibuja el mapa: tienen que poder
      entrar aunque las imágenes no quepan.
    - **Se escribe a un temporal y se renombra.** `os.replace` es atómico dentro
      del mismo sistema de archivos, así que nunca hay un JPEG a medias que el
      diario llegue a servir. Con mala cobertura una petición se corta a mitad
      más de lo que parece.
    - **Una imagen mala no tumba el lote**, igual que en la ingesta
      (decisión 23), y una que ya está **no se reescribe**: reenviar el álbum
      entero es lo normal (decisión 45), y en PythonAnywhere el disco es de red.
    - **Se sirven con `max-age` de un año.** El nombre es un hash, así que una
      imagen distinta tiene otra URL — la misma razón que hace seguro el
      `immutable` de los estáticos (decisión 48).
    - **Una foto sin miniatura sale como hueco declarado, con la trama de «no lo
      sé».** Dejarla fuera del muro haría creer que ese día hubo menos de lo que
      hubo, y es el estado normal de todo lo anterior a esta fase.

    **Y dos fallos que solo se vieron mirando la pantalla**, los dos de los que
    no dan error:

    - **El `height="200"` del `<img>` ganaba al `aspect-ratio` del CSS.** Los
      atributos `width`/`height` se mapean a reglas de presentación; el CSS
      anulaba el ancho y dejaba vivo el alto, así que las miniaturas salían
      rectángulos verticales mientras los huecos, que son `<span>`, salían
      cuadrados. Los atributos están puestos a propósito —sin ellos el muro salta
      con cada imagen que llega—, así que la solución es `height: auto`, no
      quitarlos.
    - **Con `1fr` de máximo, un día de UNA sola foto la estiraba a todo el ancho**
      de la tarjeta, y el muro perdía el ritmo justo en los días tranquilos, que
      son la mayoría.

    En la verificación, el bloque del Diario va **antes** que el del Mapa: la
    comprobación del álbum manda `completo`, que se lleva las fotos sembradas y
    sus miniaturas, y repone las fotos pero no las imágenes —el endpoint de
    puntos no las manda—. Mirarlo después dejaría el muro sin una sola imagen y
    el fallo parecería del Diario. Y se comprueba que las miniaturas **cargan de
    verdad** (`naturalWidth > 0`) y no solo que el `<img>` existe: un 404 deja la
    etiqueta en su sitio y el muro se vería lleno de recuadros rotos sin que nada
    fallara.

    **Lo que falta para que esto sirva de algo**, y no es código: montar el
    segundo envío en el atajo del iPhone. Está escrito en
    [`docs/atajo-fotos.md`](docs/atajo-fotos.md) §4b. Hasta entonces el diario
    funciona y enseña huecos, que es exactamente lo que debe hacer.

57. **«Limpio» y «demostrado» son dos preguntas, y borrar lo inventado no es lo
    mismo que borrar el viaje.** Sale de una decisión del usuario: la PWA no
    entra hasta que la app esté limpia, *"sin muestras de prueba ni nada"*.

    El problema era que las limpiezas estaban repartidas por cuatro herramientas
    (`simular_telemetria --limpiar`, `ver_notas --borrar`, `ver_telemetria
    --borrar`, `importar_fotos --limpiar`) y ninguna contestaba la pregunta que
    de verdad se hace antes de estrenar: **¿queda algo aquí que no sea de
    verdad?** Con la respuesta repartida, saberlo dependía de acordarse de las
    cuatro, y una regla que depende de que alguien se acuerde no es una regla
    (decisión 30).

    `tools/estado_limpio.py` la contesta de una vez, y devuelve **código 1** si
    queda algo simulado, para que sirva de comprobación sin leer la salida.

    La distinción que ordena la herramienta, y por la que hay **dos banderas y no
    una**:

    | | Qué es | Se reconoce | Borrarlo |
    |---|---|---|---|
    | **Lo simulado** | muestras de `simular_telemetria.py` | sí: `fuente = "simulado"` | seguro |
    | **El viaje** | tus notas, fotos y conversaciones | **no**: solo tú sabes si son de prueba | irreversible |

    Nada en la base de datos distingue «una nota de prueba en Albatera» de «una
    nota del viaje». Por eso `--limpiar` solo toca lo simulado, el reset va
    detrás de `--borrar-todo-el-viaje` **y pide escribir `BORRAR` entero** —un
    `s/n` se contesta por inercia—, y **no existe ningún `--todo`** que haga las
    dos: juntarlas sería esconder la irreversible detrás de la inocua. Es la
    asimetría de la decisión 45.

    Tres detalles que son decisiones:

    - **La caché se cuenta pero no se borra**, ni siquiera en el reset. No es
      dato del viaje —son respuestas de Nominatim y Open-Meteo que se regeneran
      solas— y borrarla solo hace que la primera consulta de cada sitio se vuelva
      a pagar, justo al llegar a un sitio nuevo.
    - **Las miniaturas se borran después de las filas**, por lo mismo que en el
      álbum: si el `DELETE` falla, no se ha perdido ninguna imagen.
    - **La herramienta dice en voz alta lo que NO puede contestar**: que no haya
      nada inventado no significa que las fuentes estén demostradas. La
      continuidad la mide `diagnostico.py` (decisión 39), y son preguntas
      distintas. Sin esa línea, un «sin datos simulados» en verde se leería como
      «todo listo».

    **Y lo que esto no cambia: una simulación sigue sin cerrar nada.** La 2d se
    cierra cuando la telemetría llegue sola y sin huecos durante días, y la 3
    cuando se escriba una nota sin cobertura de verdad y aparezca al volver la
    señal. Las dos son calendario y móvil, no trabajo — y ninguna herramienta las
    puede sustituir (decisión 36).

58. **Inicio deja de ser una lista de tarjetas y pasa a ser un panel.** Lo que
    contesta «¿qué hago aquí?» no son los datos: son los **veredictos**, y ya se
    calculaban todos en Python (decisión 5). Estaban repartidos en cuatro
    tarjetas apiladas —tiempo, agua, luna, fuego—, cada una con su título y su
    párrafo, así que para saber si podías salir a andar había que leer las
    cuatro. De nueve tarjetas a cinco.

    Ahora son **tres señales en una fila**: `Aire · Agua · Luna`, cada
    una con su valor corto y un filete del color de su grado. El dato que las
    justifica —oleaje, salida y puesta, motivo de la luna, coordenadas— baja a
    un `<details>` titulado *«Por qué»*.

    Tres decisiones dentro:

    - **Reparto fijo en tres columnas, no `auto-fit`.** Son siempre tres y
      siempre las mismas: si cambiaran de sitio o de ancho según cuántas hayan
      contestado, en marcha se leería la equivocada. Una que falta deja su hueco,
      y el hueco dice que falta.
    - **El grado va en un `data-` del contenedor**, no como clase del texto. Es
      lo que permite teñir filete y letra con una sola regla, y lo que hace que
      «no se pudo consultar» se pinte con la trama de «no lo sé» en vez de
      parecer que todo está bien — tranquilizar sin haber mirado es el fallo del
      que avisa la decisión 22.
    - **Fuego salió de Inicio por decisión del usuario.** Sigue en `/fuego`,
      que es donde de verdad decide una ruta, pero ya no aparece en el panel ni
      en el detalle plegado de Inicio. Ver decisión 59.

    **Y el contexto se pide solo al abrir.** Es la decisión 32 llevada hasta el
    final: cuesta 0,18 s con la caché caliente y no gasta un token, así que
    exigir una pulsación para saber dónde estás era cobrar un peaje por lo único
    que esta pantalla enseña siempre. Lo que sigue detrás de un botón es la
    **recomendación**, que cuesta tokens y segundos — esa mitad de la decisión 35
    no cambia. El botón se queda como *Actualizar*, porque el GPS puede tardar,
    denegarse o dar un sitio viejo y hay que poder reintentar sin recargar.

    Con eso las acciones bajan **debajo** del panel: antes presidían la pantalla
    con un título que repetía el de la pestaña y no informaban de nada.

    Dos cosas que salieron al hacerlo:

    - **Los textos se acortan en la presentación, no en el módulo.**
      «desaconsejado» no cabe en una casilla de cuatro columnas y recortado deja
      «desacon…», que no dice nada. Pero ese valor lo lee también el prompt del
      modelo, donde «no» a secas se entendería peor. Así que la tabla de
      abreviaturas vive en el JavaScript y `weather_context.py` no se toca.
    - **Dos cargas solapadas dejaban el panel en blanco.** La automática y un
      click encima: la segunda hace `hideAll()` justo cuando la primera acababa
      de pintar. `hideAll()` se queda —es lo que garantiza que ningún veredicto
      del sitio anterior sobreviva a un cambio de sitio, y un dato viejo con
      pinta de nuevo es peor que un parpadeo— y lo que se arregla es el guion,
      que ahora espera a la carga automática antes de pulsar. El síntoma apuntaba
      al sitio equivocado: «no salió la tarjeta del tiempo».

59. **Fuego se queda fuera de Inicio, y la PWA entra sin service worker.** Es el
    cierre del repaso de la Fase 8 tras la petición del usuario.

    **Inicio no enseña fuego ni satélite.** La pantalla principal contesta
    «¿qué hago aquí, ahora?», y el usuario fue explícito: no quería fuego ni
    satélite ahí. La funcionalidad no se borra; vive en `/fuego`, que contesta
    otra pregunta: «¿hacia dónde me muevo?». Por eso se quitaron de Inicio la
    cuarta señal, el texto de satélite, la lista de detecciones y la llamada a
    FIRMS. También se quitó el test end-to-end que doblaba NASA en Inicio: la
    verificación de incendios queda en la pantalla de incendios.

    **En `/fuego` no se escriben cifras de cada detección.** El mapa conserva el
    color por antigüedad, el tamaño por potencia y el filtro de focos potentes,
    pero ya no hay popup ni lista con «0,62 MW». El usuario no quiere leer esas
    detecciones en ningún sitio; para decidir ruta basta el mapa. La leyenda
    conserva la palabra MW porque explica qué significa el tamaño del círculo,
    no enumera detecciones.

    **El enlace al mapa oficial de la NASA abre fuera.** Lleva
    `target="_blank"` y además `window.open(..., "_blank", "noopener,noreferrer")`
    porque en una PWA instalada un enlace normal puede navegar dentro del modo
    standalone y dejarte sin barra de volver.

    **La PWA es instalable, pero sin service worker.** Se añade
    `manifest.webmanifest`, iconos locales y los meta de iOS. No se registra un
    service worker: volvería a abrir las decisiones 28 y 41 —qué se cachea y
    cómo se invalida— y meterlo sin un plan puede dejar JavaScript viejo
    atrapado justo cuando más falta fiarse de la app.

60. **`verificar.py` prueba código con Cudillero; `pre_despliegue.py` decide si
    se puede desplegar.** Son dos preguntas distintas y mezclarlas asusta con
    razón.

    `tools/verificar.py` usa un servidor temporal en `/tmp` y datos falsos de
    Cudillero para correr sin red, sin API keys y sin gastar tokens. Si dice
    "Cudillero, Asturias", no está enseñando la base real ni lo que hay en el
    servidor: está enseñando el muñeco de prueba. Cambiarlo por "donde estés
    ahora" rompería justo lo que lo hace útil: que sea reproducible y funcione
    en un camper sin cobertura.

    Lo que faltaba era el semáforo contrario: mirar TU instalación. Por eso
    existe `tools/pre_despliegue.py`. Comprueba git, PWA, ausencia de service
    worker, datos simulados, miniaturas, y opcionalmente `pytest` y el navegador.
    Tiene dos modos que importan antes de tocar producción:

    - `--para-commit`: exige árbol limpio. Lo que no está comitteado no llega al
      servidor con `git pull`.
    - `--estrenar`: exige que no haya notas, fotos, chat ni días. No borra nada:
      una nota de prueba y una nota del viaje son indistinguibles para la base de
      datos, así que borrarlas requiere la confirmación explícita de
      `tools/estado_limpio.py --borrar-todo-el-viaje`.

    En producción `pytest` puede no estar instalado porque vive en
    `requirements-dev.txt`. Si se pide `--tests` y falta, `pre_despliegue.py`
    da un aviso, no un fallo: un servidor sin dependencias de desarrollo puede
    estar listo para reload. En una máquina de desarrollo, donde sí está
    instalado, `--tests` sigue corriendo la suite y falla si falla la suite.

## 7. Roadmap

### El orden que viene, y por qué es ese

Los cinco pasos que se decidieron el 28-07-2026 tras cerrar la 3b **están todos
hechos**: partir `/api/recommendations` en dos, limpiar la pantalla principal, la
luna, el perfil y el chatbot. Se quedan escritos en
[`prompt-fase5.md`](docs/prompt-fase5.md) y [`prompt-fase6.md`](docs/prompt-fase6.md).

**Lo que viene ahora está en [`prompt-fase7.md`](docs/prompt-fase7.md)**, y el
orden sale de lo que costó el 29-07-2026:

**1. Una verificación que pase por el navegador.** ✅ **Hecho** el 29-07-2026:
`tools/verificar.py` y `tools/verificar_sabotaje.sh` (decisión 47). Encontró de
salida un fallo mudo real —el aviso de tiles caídos se borraba solo—, que es la
prueba de que hacía falta.

**2. Que cambiar de pantalla sea instantáneo.** 🟨 Medido contra el desplegado y
arreglado lo gordo: los estáticos se revalidaban en cada salto (decisión 48).
Falta **volver a medir con el arreglo puesto** —hace falta quitar el mapeo de
*Static files* en PythonAnywhere— y decidir qué hacer con los 0,4-0,8 s que
tarda la API en leer SQLite.

**3. El diario y las miniaturas.** Es lo que convierte el mapa en el álbum del
viaje. ~8 KB por miniatura, mil fotos son 8 MB de los 512 del plan, y reutiliza
entera la tubería que ya funciona.

**4. Personalizar el mapa.** Ahora sí toca: la regla del proyecto era *primero
que los datos sean ciertos*, y eso ya está —el álbum se refleja de verdad
(decisión 45) y las pantallas se actualizan solas (decisión 46)—.

**5. La PWA instalable.** Con el aviso escrito en el encargo: un service worker
vuelve a plantear enteras las decisiones 28 y 41, así que no entra sin un plan
para invalidarlo.

> **Sobre "quitar los avisos que no funcionan".** El aviso de POIs salta casi
> siempre porque Overpass está muerto (decisión 22), y como ruido constante es
> inútil. Pero **la solución no es callar el aviso**: la decisión 9 dice que una
> app que oculta que le falta la mitad del contexto no es fiable, es opaca. Lo
> honesto es **quitar la fuente que no funciona del camino normal** —hecho en la
> decisión 33: Overpass salió del camino normal y quedó detrás de un botón—.
> Ocultar el síntoma dejaría la app diciendo "aquí no hay nada que ver" cuando lo
> que pasa es "no he podido consultarlo".

### Lo demás, sin orden fijo

- **Cerrar la Fase 2d.** El atajo ya está montado y envía bien a mano. Falta lo
  único que cierra la fase: **dejarlo corriendo solo varios días** con una
  automatización de *Hora del día*, y comprobar con
  `python tools/ver_telemetria.py` que el total crece sin huecos y que la
  columna *retraso* enseña recuperaciones reales tras pasar por una zona sin
  cobertura. Hasta entonces, nada de análisis encima de estos datos.
  Ojo: las automatizaciones de iOS son **diarias**, no horarias; para varios
  envíos al día hay que crear una por cada hora.

- **Poner nombre a las coordenadas, y hacerlo al CONSULTAR, no al ingerir.**
  ✅ **Hecho en `tools/ver_telemetria.py`** (28-07-2026): la tabla enseña
  "Cudillero, Asturias" en vez de `43.56220, -6.14560`, con `--coords` para ver
  los números cuando lo que se depura es el GPS. Queda pendiente aplicarlo en el
  contexto del chatbot, que es el otro consumidor natural.
  Una fila con `38.39064, -0.51648` no dice nada; "Cudillero, Asturias" sí. La
  pieza ya existe: `location_context.reverse_geocode()`, con su caché en SQLite
  por coordenada redondeada a ~110 m.

  La tentación es resolver el nombre en el endpoint de ingesta, al recibir la
  muestra. **Sería un error**, y por tres motivos que se acumulan: metería una
  llamada de red dentro de la ruta que tiene que ser rápida y no puede fallar;
  haría que un Nominatim caído impidiera **guardar** datos que están perfectos;
  y rompería la regla de la fase, que es que la ingesta sea la tubería y no el
  análisis. Un dato crudo se guarda siempre; interpretarlo es otro trabajo.

  Al consultar sale casi gratis: en un camper te quedas horas en el mismo sitio,
  así que decenas de muestras caen en la misma clave de caché y son **una sola**
  petición a Nominatim (que además limita a 1/segundo, otro motivo para no
  hacerlo en caliente). Y si Nominatim está caído, sigues teniendo las
  coordenadas: se degrada, no se pierde nada. Encaja en `consultas.py` cuando
  exista, y sirve igual para el mapa y para el contexto del agente.

- **Decidir la forma de la tabla ANTES de la cuarta métrica.** Hoy `telemetria`
  tiene una columna por métrica (`pasos`, `bateria`, `lat`, `lon`). Con tres va
  bien; con quince serían quince columnas casi siempre `NULL` y cada métrica
  nueva un cambio de esquema. La alternativa es una tabla **estrecha**
  (`fuente, medido_en, metrica, valor, unidad`), donde añadir una métrica es
  cero cambios de esquema, a cambio de consultas más incómodas y de perder la
  validación por tipo que hoy da cada columna.

  Es la decisión 4 a lo grande: **con la tabla casi vacía la migración es
  gratis; con un mes de viaje dentro cuesta un fin de semana.** Por eso la
  decisión toca ahora, no cuando duela.

  Métricas candidatas, por orden de utilidad real (el criterio: un dato solo
  merece un bloque en el atajo si **cambia una recomendación**):
  *altitud* (un bloque más, ya se pide la ubicación), *sueño* y *frecuencia
  cardíaca en reposo* (las que distinguen "hoy toca ruta" de "hoy toca playa"),
  *energía activa*. Peso, ruido ambiental y pisos subidos no cambian nada.

- **Datos de Hevy (entrenamientos).** Es un caso distinto al del iPhone y
  conviene tenerlo claro antes de empezar: **es *pull* desde el servidor, no
  *push* desde el móvil**, así que necesita una tarea programada, y el plan
  gratuito de PythonAnywhere da **una sola al día** — ese es el techo real, no
  la API. Antes de escribir nada hay que verificar dos cosas contra la
  realidad, como con Kimi (decisión 21): que la API existe y qué plan exige, y
  que su dominio está en la **lista blanca del proxy**. Si no lo está, no
  funciona en producción y no te enterarías hasta desplegar. La columna
  `fuente` ya está preparada para esto desde el primer día.

- **Análisis en segundo plano (el "agente").** Un resumen diario generado de
  madrugada a partir de la telemetría. Cambia una regla que hoy es correcta:
  **la decisión 12 (sin reintento ante un 429) deja de aplicar** cuando nadie
  está esperando — ahí sí conviene esperar un minuto y reintentar, justo al
  revés que en una petición móvil. `llm_providers` ya permite elegir proveedor;
  con `kimi-k3` a ~0,03 $ por análisis, un resumen diario sale por unos 11 $ al
  año.
- **Espejos de Overpass** (ver decisión 22). Hoy solo hay uno vivo y saturado,
  así que los POIs son intermitentes en producción. Hay que buscar espejos y
  validarlos con datos españoles reales, no con que devuelvan `200`.
- **Cerrar la Fase 3.** Escribir una nota de verdad en una zona sin cobertura y
  comprobar en el mapa que aparece al volver la señal, sin duplicarse. Es lo
  único que falta y no se puede hacer desde un escritorio.

- **Fotos en las notas.** Aplazadas a propósito (decisión 27), con el diseño ya
  decidido: multipart, redimensionado en el navegador, archivo antes que fila,
  nombre derivado del `client_id`. Lo que hay que calcular al retomarlo es el
  **presupuesto de disco**: de los 512 MB del plan gratuito, el virtualenv se
  come ~101 MB, así que quedan ~355 MB tras reservar 50 MB de margen, o sea
  ~780 fotos de 450 KB (~26 al día en un mes). `tools/diagnostico.py` ya avisa
  por debajo de 50 MB libres.

- **Fase 4.** Resumen narrativo del viaje generado por el LLM a partir de todas
  las notas, y `manifest.json` + iconos para instalar como PWA en el iPhone.

- **Los dos caminos de las fotos NO deduplican entre sí.** Salió al cerrar la
  3b y no se arregló ahí para no ampliar el alcance. La clave anti-duplicados
  es el nombre del archivo, y **Atajos lo devuelve sin extensión** (`IMG_4638`)
  mientras que `tools/importar_fotos.py` lo manda con ella (`IMG_4638.HEIC`).
  Para el servidor son dos archivos distintos, así que una foto que entre por
  los dos caminos aparecería **dos veces en el mapa**. Dentro de cada camino la
  deduplicación es perfecta, y hoy solo se usa el atajo, así que no molesta.

  Cuando se toque, la decisión no es obvia y conviene pensarla entera:
  normalizar quitando la extensión al guardar pierde información y colapsaría
  `IMG_1.JPG` con `IMG_1.HEIC`, que **son dos fotos distintas** en un carrete
  de iPhone que ha cambiado de formato. La alternativa es guardar el nombre tal
  cual y deduplicar por una clave aparte. No hay que elegir a ciegas: mide
  primero cuántas colisiones reales habría en el carrete.

- **Las fotos se eligen, no se vuelcan.** ✅ **Montado y funcionando**
  (28-07-2026). El atajo mira un álbum concreto (`Viaje`), no el carrete entero. Es privacidad y menos permisos, pero sobre
  todo es que **la curación es el dato**: el carrete de un mes son cientos de
  fotos y la mitad son capturas de pantalla y tickets. Un álbum es una versión
  del viaje contada por ti. Y el álbum se manda **entero** en cada envío, no
  solo lo reciente, porque la deduplicación va por nombre de archivo: así una
  foto que metas tres semanas después entra igual. Receta en
  [`docs/atajo-fotos.md`](docs/atajo-fotos.md), con una variante por hoja de
  compartir que no pide ningún permiso de Fotos.

  Y un tercer camino para el portátil: **una carpeta vigilada**
  (`~/Pictures/viaje`) que se lee sola al soltar fotos dentro. Lo dispara una
  unidad `.path` de systemd y no un temporizador, y la diferencia no es
  cosmética: un temporizador cada 5 minutos son 288 ejecuciones diarias para
  una carpeta que casi siempre está igual, y además tarda hasta 5 minutos en
  enterarse. La configuración vive en `~/.config/roadtrip/fotos.env`, fuera del
  repositorio, porque ahí está el token en claro.

- **Enseñar la foto, no solo el punto.** Hoy el mapa dice "📷 IMG_4213.JPG" y
  no puede enseñarla, porque la foto vive en tu disco y no se sube (decisión
  30). Lo que falta no es subirlas: es una miniatura. Una de 200×150 a JPEG
  bajo son ~8 KB, así que **mil fotos serían 8 MB** —cabe de sobra en los 512
  MB— y el mapa pasaría de una lista de nombres a un álbum del viaje. Es la
  mejor relación entre lo que aporta y lo que cuesta que queda pendiente, y
  reutiliza entera la tubería que ya existe: solo cambia qué se manda.

- **La app también en casa.** Hoy el uso pensado es el viaje, pero la mitad de
  lo que hace ya sirve desde el sofá: oleaje y tiempo con veredicto propio,
  recomendaciones, y el mapa como registro de sitios. Lo que falta para eso no
  es código nuevo sino una pantalla que no dé por supuesto que estás de ruta.

- **El asistente que sabe de ti (universidad, exámenes, entrenamientos).**
  Es la ambición grande y conviene decir por qué **no** toca todavía: un
  asistente útil se construye sobre contexto que ya está guardado y es fiable,
  y hoy la única fuente demostrada son las notas. La telemetría sigue sin
  cerrar, Hevy no está ni empezado y no hay ningún dato de asignaturas. El
  orden que lo hace posible es: cerrar la 2d → decidir la forma de la tabla de
  métricas (ancha contra estrecha) → análisis en segundo plano. Construirlo
  antes es exactamente el trabajo que hay que tirar.
- **Personalizar el mapa (idea del usuario, 28-07-2026).** Skins, decorar "tu
  espacio", quién sabe si algún día alguien pagaría por ello. Se anota porque la
  idea es buena y porque el orden ya está decidido y no cambia: **primero que los
  datos sean ciertos, la estética después.** Un mapa precioso sobre pasos
  contados dos veces sigue siendo un mapa que miente, y encima uno que te crees.

- **Sin fecha.** Implementar `OllamaProvider` (el diseño está documentado en la
  propia clase): permitiría afinar el prompt sin conexión, en el propio camper.

61. **Inicio absorbe Fuego y Viaje absorbe Diario.** El usuario volvió a pedir
    que el fuego esté en Inicio, pero no como cuarta señal del panel ni como
    una consulta automática: va **al final**, bajo botón, porque mirar FIRMS
    cuesta GPS, red y atención, y no debe robar el arranque de "¿qué hago
    aquí?". `/fuego` queda como redirección compatible a ese bloque.

    El Diario deja de ser pantalla principal y vive dentro de `/mapa`, que en
    la navegación se llama **Viaje**: progreso, trayecto, revivir y muro son la
    misma cosa desde el punto de vista del uso real. `/diario` redirige a
    `/mapa#diario` para no romper enlaces viejos.

    Además, el diario **no pinta miniaturas por defecto**. Si solo han llegado
    metadatos de una foto, no hay imagen que enseñar; inventar un recuadro roto
    comunica peor que una línea clara con archivo, hora y lugar. La tubería de
    miniaturas sigue existiendo para cuando el atajo mande una copia reducida,
    pero la UI no depende de ella. La forma óptima sigue siendo no subir el
    HEIC/JPEG original del iPhone: subir un JPEG reducido sin EXIF, de decenas
    de KB, protege disco y ancho de banda.

62. **El chat gana herramientas, no un prompt infinito.** Para preguntas como
    "bar más cerca" o "cuánto tardo de Burgos a Vitoria", la solución buena no
    es meter más contexto permanente en cada llamada al modelo: eso cuesta
    tokens siempre y sigue dejando al LLM inventar. Se añade
    `app/modules/map_tools.py` como capa determinista:

    - detecta si una pregunta necesita **sitios**, **rutas** o **memoria del
      viaje**;
    - consulta Google Places/Routes solo si `GOOGLE_MAPS_API_KEY` está
      configurada;
    - usa `FieldMask` mínimo, caché corta y errores legibles;
    - si no puede mirar, mete el aviso en el prompt para que el modelo no
      finja haber mirado;
    - no toca `app.py`: el chat la llama antes del proveedor de LLM y añade un
      bloque `HERRAMIENTAS CONSULTADAS`.

    Esta fase deja montada la frontera escalable. Hoy no hay function calling
    real entre proveedor y herramienta: se ejecuta una selección heurística
    antes de llamar al modelo, porque ya cubre las preguntas útiles y funciona
    igual con Anthropic, Gemini, Kimi y el futuro Ollama. Si mañana se añade
    tool calling nativo, se cambia solo `chat.py`; `map_tools.py` sigue siendo
    la capa de verdad.

63. **Fallback entre proveedores ante fallos recuperables.** La decisión 12
    ("sin reintento automático ante un 429") sigue siendo correcta para el
    MISMO proveedor: reintentar Gemini cuando acaba de decir cuota agotada solo
    bloquea el worker. Pero desde que hay varias keys configurables, no tiene
    sentido rendirse si Gemini devuelve 429 y Kimi o Anthropic están listos.

    `build_provider()` sin nombre explícito monta una cadena: proveedor activo
    primero y luego alternativas configuradas. `build_provider("gemini")`
    sigue devolviendo Gemini exacto para diagnóstico y tests. El fallback solo
    salta ante fallos recuperables (429, cuota, saturación, timeout, conexión);
    errores de configuración como key mala, modelo inexistente o 400 no se
    esconden probando otro motor. Cuando una alternativa responde, el proveedor
    compuesto cambia su `name/model` al proveedor real, para que la trazabilidad
    y la caché digan quién contestó.

64. **El chat ya distingue preguntas abiertas de búsquedas concretas.** "Bar
    cerca" sigue siendo una consulta de Places, pero "qué hago cerca",
    "dónde duermo" o "necesito comprar/repostar" se convierten en un pequeño
    paquete de búsquedas prácticas. El límite son tres consultas de Places por
    pregunta: suficiente para dar contexto real y pequeño para no gastar cuota
    de Google ni tokens de Kimi sin darte cuenta.

    La misma herramienta también entiende rutas "desde aquí" (`cuánto tardo a
    Vitoria`) usando el lugar actual como origen, y mete el veredicto calculado
    de paddle surf cuando la pregunta habla de tabla, mar o playa. Ese dato no
    se recalcula en el modelo: sale de `weather_context.water_sports()`, igual
    que en el dashboard.

## 8. Conceptos de esta fase

Ideas que conviene entender para mantener y extender esto.

- **Inversión de dependencias.** `ai_orchestrator` no importa `anthropic` ni
  `google.genai`: depende de la *abstracción* `LLMProvider`, y son los
  proveedores concretos los que se adaptan a ella. Por eso añadir Ollama no
  toca ni una línea del orquestador. En C++ sería una clase base abstracta con
  métodos virtuales puros; en Python basta con heredar de `ABC` y marcar el
  método con `@abstractmethod`.

- **Inyección de dependencias para testear.** `get_recommendations()` acepta un
  `provider` opcional. En producción no se pasa (usa el de `LLM_PROVIDER`); en
  los tests se inyecta un `FakeProvider` que devuelve lo que se le pida. Por eso
  la suite entera corre sin red ni API keys.

- **Frontera de módulo.** "Nada específico de un proveedor sale del módulo" es
  una regla comprobable: si mañana `app.py` tuviera que hacer
  `except anthropic.RateLimitError`, la abstracción habría fracasado. El test
  `test_cualquier_fallo_del_proveedor_sale_como_aierror` fija esa frontera.

- **Fallo silencioso contra fallo ruidoso.** La decisión 11 existe porque hay
  bugs que no dan error, solo respuestas equivocadas. Son los más caros de
  encontrar: cuando la caché sirve una respuesta del proveedor anterior, todo
  "funciona". Merece la pena gastar una línea en la clave de caché para
  convertir un fallo silencioso en algo imposible.

- **Equivocarse hacia el lado seguro.** `_env_bool()` no parsea un booleano sin
  más: decide qué hacer con un valor que no reconoce, y la respuesta depende de
  hacia dónde duele el error. Un flag activado por defecto (la cookie `Secure`)
  solo se apaga con un "no" reconocible; uno apagado por defecto
  (`SHOW_AI_ERROR_DETAIL`) solo se enciende con un "sí" reconocible. Escribir
  `flase` en el `.env` no puede desproteger nada. Es la misma idea que el
  *fail-safe* de un freno: cuando el sistema no sabe, cae del lado que no hace
  daño.

- **La configuración se lee al importar, y eso condiciona los tests.** `Config`
  resuelve el entorno una vez, al importarse. La tentación al testear es
  `importlib.reload(app.config)` para probar otro valor, y es una trampa:
  recargar sustituye la clase `Config` por una nueva, pero los módulos que
  hicieron `from app.config import Config` se quedan con la vieja. A partir de
  ahí el test parchea un objeto y el código de producción lee de otro. Pasó de
  verdad: `storage` acabó escribiendo en la base de datos **real** a mitad de la
  suite, y solo fallaba según el orden en que corrieran los archivos. La salida
  no fue un `reload` más listo, sino **extraer el parseo a una función**
  (`_env_bool`) que se puede llamar directamente. Regla general: si para testear
  algo necesitas reimportar un módulo, lo que quieres probar merece ser una
  función.

- **Defensa en profundidad para los secretos.** No basta con "no meter la key en
  el mensaje": también se redacta al salir, se redacta por patrón aunque la
  clave no sea la nuestra, y se redactan fragmentos parciales. Cada capa asume
  que la anterior puede fallar.
