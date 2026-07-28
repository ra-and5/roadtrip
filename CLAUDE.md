# CLAUDE.md — Compañero de viaje

Documento de trabajo del proyecto. Lo lee Claude Code al empezar cada sesión.

---

## 1. Qué es esto

Aplicación web (PWA) para un viaje de un mes por el norte de España en coche
camperizado. Usa el GPS del móvil para saber dónde estás, recomienda qué hacer
con ayuda de un LLM, guarda notas geolocalizadas y construye el mapa acumulado
del viaje.

Es un proyecto de portfolio **que se va a usar de verdad durante el viaje**. Eso
condiciona todo: la fiabilidad con mala cobertura importa más que las features.

## 2. Cómo trabajamos

- **Fases cerradas.** No se empieza una fase sin la anterior funcionando de
  extremo a extremo. Antes que una feature avanzada a medias, algo completo.
- **Verificar, no suponer.** Las APIs externas se comprueban contra la API real
  antes de escribir el módulo que las consume. Varios bugs de este proyecto
  salieron así (ver decisiones 5 y 7).
- **Explicar el porqué, no el qué.** Los comentarios del código justifican
  decisiones no obvias. El "qué hace" ya lo dice el código.
- **Tests sin red.** La suite no debe necesitar conexión ni API keys: tiene que
  poder correr en un camper sin cobertura.
- **Nunca hardcodear secretos.** Todo por variables de entorno.

## 3. Arquitectura

```
app/
  app.py                     Rutas Flask. SIN lógica de negocio.
  config.py                  Configuración desde variables de entorno
  modules/
    location_context.py      Nominatim (dónde estoy) + Overpass (qué hay cerca)
    weather_context.py       Open-Meteo (tiempo + oleaje) e interpretación
    ai_orchestrator.py       Prompt, esquema de salida y caché. AGNÓSTICO del proveedor.
    llm_providers.py         Único módulo que conoce Anthropic / Gemini / Kimi / Ollama
    ingest.py                Telemetría del móvil: token, validación e idempotencia
    notes.py                 Notas geolocalizadas, y el progreso del mapa
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
    vendor/leaflet/          Leaflet 1.9.4, servido por nosotros (decisión 28)
```

Regla: `app.py` valida la entrada, llama a un módulo y formatea la respuesta.
Cada módulo tiene una función de entrada tipada y lanza su propia excepción
(`LocationError`, `WeatherError`, `AIError`, `IngestError`, `NoteError`,
`WaypointError`, `PhotoMetaError`). Solo `storage.py` abre la BD.

Hay **dos** formas de autenticarse, y no se cruzan: la sesión (`auth.py`) para
todo lo que usa una persona con un navegador, y el token de `ingest.py` para lo
que usa una máquina. Ver decisión 24.

## 4. Comandos

```bash
pip install -r requirements.txt            # producción (lo que va al servidor)
pip install -r requirements-dev.txt        # + pytest, para desarrollar
python run.py                              # servidor local (127.0.0.1:5000)
python -m pytest -q                        # tests (sin red, sin API keys)
python tools/diagnostico.py                # estado de cada dependencia
python tools/diagnostico.py --todos        # prueba todos los proveedores de LLM
python tools/listar_modelos.py             # qué modelos de Gemini sirven con tu key
python tools/hash_password.py              # genera SECRET_KEY y APP_PASSWORD_HASH
python tools/token_ingesta.py              # genera el token del iPhone y su hash
python tools/ver_telemetria.py             # últimas muestras recibidas del móvil
python tools/ver_telemetria.py 50          # las 50 últimas
python tools/ver_telemetria.py --borrar 3,4  # borra muestras malas por id
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
| 2d | Ingesta de telemetría del iPhone (pasos, ubicación, batería) | 🟨 MVP funcionando; **aparcada** a la espera de días de datos |
| 3 | Notas geolocalizadas (cola offline) y mapa Leaflet | 🟨 Hecho; **falta validarlo en el móvil** |
| 3b | Ruta del viaje a partir del EXIF de las fotos, y "revivir el viaje" | 🟨 Hecho; **falta probarlo con fotos reales** |
| 4 | Resumen narrativo del viaje + manifest PWA | ⬜ Pendiente |

**La Fase 3 está hecha, no cerrada,** y la diferencia es la misma que en la 2d.
Lo que hay: notas de **solo texto** con cola offline en IndexedDB, mapa con
Leaflet servido por nosotros, y progreso del viaje (sitios, días, racha,
tablero de 19 comunidades, comparación entre años). Las fotos se aplazaron a
propósito (decisión 27) y su diseño queda escrito para cuando toquen.

Lo que **sí** está probado, y no solo por la suite (340 tests): la cola offline
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

**La Fase 3b (la ruta) también está hecha y sin cerrar, por el mismo motivo:
todavía no ha pasado por unas fotos de verdad.** Lo que hay: un lector de EXIF
sin dependencias, `tools/importar_fotos.py`, la tabla `waypoints`, el endpoint
`/api/waypoints` y el mapa dibujando el trayecto con el modo *revivir el viaje*.

Probado de extremo a extremo, no solo con tests: se fabricaron fotos con EXIF
conocido, se leyeron, se importaron por HTTP real contra un servidor (401 con
token malo, 10 guardados con el bueno, y 0 nuevos / 10 duplicados al reenviar),
y en Chrome se recorrió el viaje entero con el reproductor —el mapa se centra
en cada momento, el halo marca dónde estás y la barra del tiempo sigue—.

Lo que falta es una sola cosa y no se puede simular: **pasarle la carpeta de
fotos del viaje de verdad**. De ahí saldrá qué conservan realmente, que es lo
único que decide si esto sirve. Empieza por
`python tools/importar_fotos.py <carpeta>`, que no guarda nada y solo informa.
Ya hay un dato comprobado que ahorra tiempo: **las fotos que pasan por WhatsApp
llegan sin ningún metadato**, así que hay que partir de los originales.

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
Hoy hay dos: [`prompt-despliegue.md`](docs/prompt-despliegue.md) (hecho) y
[`prompt-fase3.md`](docs/prompt-fase3.md) (por hacer, y el único que describe
trabajo futuro).

**Cuenta de PythonAnywhere gratuita.** Importa para el diseño, no solo para la
factura: el plan gratuito saca todo el tráfico por un proxy con lista blanca de
dominios, y un host no permitido devuelve un **403 del proxy** que la app ve
como "fuente caída" y degrada en silencio. Por eso el checklist obliga a correr
`tools/diagnostico.py` **en el servidor** antes de tocar el móvil.

**Proveedor activo: Kimi** (`kimi-k3`), verificado generando recomendaciones
reales. Es de prepago y queda saldo; el diagnóstico lo consulta al final para
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

## 7. Roadmap

- **Cerrar la Fase 2d.** El atajo ya está montado y envía bien a mano. Falta lo
  único que cierra la fase: **dejarlo corriendo solo varios días** con una
  automatización de *Hora del día*, y comprobar con
  `python tools/ver_telemetria.py` que el total crece sin huecos y que la
  columna *retraso* enseña recuperaciones reales tras pasar por una zona sin
  cobertura. Hasta entonces, nada de análisis encima de estos datos.
  Ojo: las automatizaciones de iOS son **diarias**, no horarias; para varios
  envíos al día hay que crear una por cada hora.

- **Poner nombre a las coordenadas, y hacerlo al CONSULTAR, no al ingerir.**
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

- **Cerrar la 3b.** Pasarle `tools/importar_fotos.py` a la carpeta de fotos
  del viaje de verdad y ver qué conservan. Es lo único que falta, y decide
  cuánto de esto sirve.

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
- **Sin fecha.** Implementar `OllamaProvider` (el diseño está documentado en la
  propia clase): permitiría afinar el prompt sin conexión, en el propio camper.

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
