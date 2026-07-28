# Fase 3 — Notas geolocalizadas y mapa del viaje

Encargo de la fase. Lee `CLAUDE.md` antes de empezar: sus reglas mandan sobre
este documento si hay conflicto.

---

## 0. Contexto que no hace falta redescubrir

Para no gastar medio contexto averiguando cosas que ya están decididas:

- **Despliegue:** PythonAnywhere gratuito, usuario `D10SdreBrasov`, en
  `https://d10sdrebrasov.pythonanywhere.com`. Repo
  `git@github.com:ra-and5/roadtrip.git`, rama `main`.
- **Flujo de trabajo, siempre:** commit + push aquí → `git pull` **y pulsar
  Reload** en la pestaña *Web* del servidor. Sin el Reload, PythonAnywhere sigue
  ejecutando el código anterior en memoria, y eso ya costó una hora de depurar
  algo que no estaba roto.
- **Suite:** `python -m pytest -q`, 211 tests en verde, sin red y sin API keys.
- **Versiones:** Flask 3.1.3, Werkzeug 3.1.8, Python 3.11 como objetivo (el
  servidor). Dependencias con `==`.
- **Límites reales del plan gratuito**, que condicionan el diseño y no solo la
  factura: **512 MB de disco** en total (el virtualenv ya ocupa ~100 MB),
  **cuota diaria de CPU** (agotarla ralentiza la app entera el resto del día),
  **una sola tarea programada al día**, y un proxy con lista blanca que afecta
  **solo al tráfico saliente del servidor** — no a lo que pida el navegador.
- **Fase 2d aparcada, no cerrada.** La telemetría del iPhone llega, pero falta
  demostrar que llega sin huecos durante varios días. **No construyas análisis,
  gráficas ni el trayecto sobre esos datos en esta fase.** Cuando se cierre,
  dibujar la ruta será una capa más de Leaflet, porque `lat`/`lon` ordenados por
  `medido_en` ya son la ruta.

---

## 1. Objetivo

Que durante el viaje se pueda **marcar un sitio con una nota y una foto**, que
eso funcione **sin cobertura**, y que después todo aparezca sobre un **mapa**
que va dibujando el viaje.

Es la primera fase con interfaz de verdad. Hasta ahora la app respondía
preguntas ("¿qué hago aquí?"); ésta **acumula** algo.

---

## 2. Alcance: qué NO se hace

- **Nada de telemetría.** Ni trayecto, ni gráficas de pasos, ni batería en el
  mapa. Está aparcada a propósito (ver §0).
- **Ningún resumen narrativo ni llamada al LLM.** Eso es la Fase 4.
- **Ninguna edición ni borrado de notas desde la web.** Crear y ver. Borrar una
  nota mala se hace desde consola, como en la 2d.
- **Ni compartir, ni exportar, ni multiusuario.**
- **Nada de mapas offline (tiles descargados).** Se documenta qué pasa sin
  cobertura, no se implementa.

El criterio de cierre es uno: **¿se puede crear una nota con foto en un sitio
sin cobertura, y aparece en el mapa al volver la señal, sin duplicarse?**

---

## 3. Diseño obligatorio

### 3.1 Idempotencia: `client_id`, que ya está en el esquema

La tabla `notes` de `storage.py` ya tiene `client_id TEXT NOT NULL UNIQUE` desde
la Fase 1, y la decisión 4 de `CLAUDE.md` explica por qué. Úsalo, no inventes
otra cosa:

- El **móvil** genera el `client_id` (`crypto.randomUUID()`) **antes** del primer
  intento de envío, y lo reutiliza en cada reintento.
- El servidor inserta con `INSERT OR IGNORE` y la respuesta distingue
  **creada** de **ya existía**. Reenviar una nota nunca la duplica.
- Es la misma propiedad que la ingesta de la 2d (decisión 23), por el mismo
  motivo y con la misma solución: la garantía vive en la **restricción de
  unicidad del esquema**, no en un `SELECT` previo, que con dos peticiones a la
  vez tiene una carrera.

### 3.2 Cola offline en el navegador

Aquí sí hay cola, al revés que en la 2d, y hay que **explicar en el registro de
decisiones por qué la asimetría no es una incoherencia**: los pasos de Salud se
pueden consultar hacia atrás, así que una ventana solapada los recupera sin
guardar estado; una nota escrita a mano en un mirador **no existe en ningún otro
sitio**. Si se pierde, se perdió.

- **IndexedDB, no `localStorage`.** `localStorage` guarda solo texto y ronda los
  5 MB: una foto lo revienta. IndexedDB guarda `Blob` directamente.
- Al pulsar *Guardar*: se escribe **primero** en la cola local y se le dice al
  usuario que está guardada. El envío es un intento posterior. Nunca al revés:
  una nota que se pierde porque el POST falló es exactamente el fallo que esta
  fase existe para impedir.
- Se reintenta al recuperar la conexión (`window.addEventListener('online')`) y
  al abrir la app. Sin `setInterval` machacando.
- La interfaz **enseña cuántas notas quedan por enviar.** Una cola invisible es
  una cola en la que no confías.
- Solo se borra de la cola local cuando el servidor confirma (`201` o
  "duplicada"). Un `500` o un timeout la dejan en la cola.

### 3.3 Fotos: el sitio donde se agota el disco

- **Se redimensionan en el NAVEGADOR** (canvas), antes de subirlas, a un lado
  máximo razonable (p. ej. 1600 px) y JPEG de calidad ~0,8. Dos motivos, y los
  dos son de este proyecto en concreto: subir 4 MB por una red de camping es
  medio minuto en el que puede caerse la conexión, y redimensionar en el
  servidor consume **cuota diaria de CPU**. `Pillow` está comentado en
  `requirements.txt`; **déjalo comentado si no acabas necesitándolo**.
- `MAX_CONTENT_LENGTH` global está en 128 KiB por la Fase 2d y **no se sube**:
  eso dejaría sin protección la ingesta. Se eleva **solo en esta ruta**, con
  `request.max_content_length` (Flask 3.1 lo permite; ya está anotado en
  `config.py`). Configurable por variable de entorno.
- **El nombre del archivo del cliente no se usa jamás.** Se genera a partir del
  `client_id` más la extensión deducida del tipo real. Un nombre de archivo
  recibido es una vía de *path traversal* (`../../`) y hay que tratarlo como
  hostil. **Ponle un test.**
- Se valida que sea imagen de verdad (por magic bytes o por Pillow), no por la
  extensión ni por el `Content-Type` que diga el cliente.
- **Presupuesto de disco explícito**, calculado y escrito en la documentación:
  cuántas fotos caben en lo que queda de los 512 MB, y qué pasa al llenarse.
  Que la app se quede sin disco a mitad de viaje **no puede ser una sorpresa**;
  `tools/diagnostico.py` debe avisar antes.

### 3.4 Autenticación: aquí SÍ manda la sesión

Al contrario que la ingesta de la 2d (decisión 24), estas rutas las usa una
persona con un navegador: van con `auth.login_required`. Que convivan los dos
modelos es correcto **siempre que cada ruta tenga exactamente uno**. Deja un
test que fije que el token de ingesta **no** abre las rutas de notas, simétrico
al que ya existe al revés.

### 3.5 El mapa

- **Leaflet servido desde `app/static/`, no desde un CDN.** Un CDN es un tercero
  más que puede caerse y que en la práctica no aporta nada aquí. Fija la
  versión, como las dependencias de Python (decisión 17).
- **Los tiles los pide el NAVEGADOR**, así que la lista blanca del proxy de
  PythonAnywhere **no aplica** — es un error fácil de cometer al razonar sobre
  esto, déjalo escrito.
- Respeta la política de uso de los tiles de OSM: atribución visible y nada de
  descargas masivas.
- **Qué pasa sin cobertura**: los tiles no cargan, pero las chinchetas y el
  listado sí, porque salen de tu servidor. Documenta ese comportamiento en vez
  de disimularlo — la app tiene que ser honesta sobre lo que no puede hacer
  (decisión 9).
- Chincheta por nota, con su texto, su foto y su fecha en hora local.

### 3.6 Arquitectura

Lo de siempre: `app.py` valida la entrada, llama a un módulo y formatea la
respuesta.

- Módulo nuevo `app/modules/notes.py` con su excepción `NoteError`, como
  `location_context`, `weather_context`, `ai_orchestrator` e `ingest`.
- Función de entrada tipada que recibe datos ya deserializados y devuelve un
  resultado tipado. La validación vive en el módulo, no en la ruta, para poder
  probarla sin Flask.
- **Solo `storage.py` abre la base de datos.** Sin excepciones.
- El JavaScript de la cola offline en su propio archivo de `app/static/js/`, no
  incrustado en la plantilla.

---

## 4. Verificación

- **Extiende `tools/diagnostico.py`**: cuántas notas hay, la fecha de la última,
  cuánto ocupa `UPLOAD_DIR` y **cuánto disco queda**.
- **Crea `tools/ver_notas.py`**: vuelca las últimas notas en texto, con su `id`
  para poder borrarlas, igual que `ver_telemetria.py`. Reutiliza ese patrón, que
  ya funciona.

---

## 5. Tests

Sin red y sin API keys. Con el test client de Flask, nunca con HTTP real. Como
mínimo:

- Enviar la misma nota (mismo `client_id`) dos veces la guarda **una sola vez**,
  y la segunda respuesta lo dice.
- Sin sesión → 401 en la API y redirección al login en la página.
- **El token de ingesta de la 2d NO abre las rutas de notas.**
- Cada regla de validación con su caso límite (coordenadas, longitud del texto,
  nota vacía sin texto ni foto).
- **Un nombre de archivo malicioso (`../../etc/passwd`, nombres con `\0` o con
  barras) no escribe fuera de `UPLOAD_DIR`.**
- Un archivo que no es una imagen se rechaza aunque se llame `.jpg`.
- Una foto por encima del límite se rechaza sin escribir nada en disco.
- La página del mapa carga y sirve las notas.
- **Lo de la cola offline que se pueda probar sin navegador**, que es la parte
  de servidor: reintento con el mismo `client_id`, y respuestas ante fallo.

---

## 6. Documentación

- **`CLAUDE.md`**: entradas nuevas en el registro de decisiones — por qué aquí
  sí hay cola offline y en la 2d no (la asimetría del §3.2), por qué se
  redimensiona en el cliente, y por qué Leaflet va servido y no por CDN.
  Actualiza la tabla de estado y la arquitectura.
- **`README.md`**: rutas nuevas en la tabla de endpoints, variables de entorno
  nuevas, y el presupuesto de disco.
- **`docs/troubleshooting.md`**: síntomas nuevos — "la nota no se envía", "la
  foto no sube", "el mapa sale gris", "se ha llenado el disco".
- **`.env.example`**: las variables nuevas con su explicación.

---

## 7. Cómo trabajar

1. **Antes de escribir nada**, enséñame el esquema definitivo de `notes` (ya
   existe: di si lo cambias y por qué), la forma exacta del JSON de creación, y
   cómo viaja la foto (multipart contra base64, y por qué). Si eso está mal, lo
   demás no importa.
2. Implementa por partes, con la suite en verde en cada paso: primero crear y
   listar notas sin foto, luego la foto, luego la cola offline, y el mapa al
   final. **Commit al terminar cada parte**, para que el progreso se vea y se
   pueda volver atrás.
3. Al terminar, dime **qué has verificado de verdad y qué no**. En particular,
   la cola offline y la cámara del iPhone no las puedes probar: dilo
   explícitamente en vez de darlo por bueno. Lo mismo pasó en la 2d y salieron
   cuatro trampas que no se veían venir (están en `docs/atajo-iphone.md`).
4. **No amplíes el alcance.** Si ves algo que merece la pena y no está aquí,
   anótalo al final y sigue.
5. Cuando estés a punto de decidir algo no obvio, **dilo y explica la
   alternativa que descartas**. Ese razonamiento es la mitad del valor de este
   proyecto y es lo que acaba en el registro de decisiones.

---

## 8. Pendiente de fases anteriores (no lo hagas aquí, pero tenlo presente)

- Cerrar la 2d: comprobar con `tools/ver_telemetria.py` que llegan datos sin
  huecos durante varios días.
- Decidir la forma de la tabla de métricas (ancha contra estrecha) **antes** de
  añadir una cuarta métrica. Está razonado en el roadmap de `CLAUDE.md`.
- Los espejos de Overpass siguen casi todos muertos (decisión 22).
