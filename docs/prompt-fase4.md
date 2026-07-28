# Fase 4 — Que el viaje se vea y se cuente

Encargo de la fase. Lee `CLAUDE.md` antes de empezar: sus reglas mandan sobre
este documento si hay conflicto.

---

## 0. Contexto que no hace falta redescubrir

- **Despliegue:** PythonAnywhere gratuito, usuario `D10SdreBrasov`, en
  `https://d10sdrebrasov.pythonanywhere.com`. Repo
  `git@github.com:ra-and5/roadtrip.git`, rama `main`.
- **Flujo de trabajo, siempre:** commit + push aquí → `git pull` **y pulsar
  Reload** en la pestaña *Web* del servidor. Sin el Reload, PythonAnywhere
  sigue ejecutando el código anterior en memoria.
- **Suite:** `python -m pytest -q`, 352 tests en verde, sin red y sin API keys.
- **Versiones:** Flask 3.1.3, Werkzeug 3.1.8, Python 3.11. Dependencias con `==`.
- **Límites del plan gratuito**, que condicionan el diseño: **512 MB de disco**
  (el virtualenv ocupa ~101 MB), **cuota diaria de CPU**, **una sola tarea
  programada al día**, y un proxy con lista blanca que afecta **solo al tráfico
  saliente del servidor**.
- **Lo que ya existe y hay que reutilizar, no reinventar:**
  - `notes` (texto geolocalizado, cola offline en IndexedDB)
  - `waypoints` (dónde y cuándo se hizo cada foto, sin subir la foto)
  - `ruta.py` (notas + fotos en una línea de tiempo, con días y kilómetros)
  - `/mapa` (trayecto, tablero de comunidades, *revivir el viaje*)
  - `llm_providers.py` (Anthropic / Gemini / Kimi detrás de una sola interfaz)
  - `ingest.token_valido()` (autenticación por token para clientes máquina)

---

## 1. LO PRIMERO: montar el atajo de fotos y cerrar la 3b

**Esta es la tarea principal de la sesión y va antes que todo lo demás.** El
usuario quiere una cosa concreta: que el iPhone mande solo los metadatos de un
**álbum** de fotos, y que el mapa se dibuje con eso.

Todo el lado servidor está hecho y probado (`/api/waypoints`, idempotente por
`(fuente, archivo)`, token). El lector de EXIF está probado contra una foto
real de iPhone. **Lo único que no ha montado nadie es el atajo**, y la receta
está en [`atajo-fotos.md`](atajo-fotos.md).

Cómo trabajarlo:

1. Pregunta en qué punto está: ¿existe el álbum `Viaje`? ¿existe el atajo?
   ¿qué devuelve al ejecutarlo?
2. **Acompáñalo acción por acción.** Al montar el atajo de la Fase 2d salieron
   cuatro trampas que no se ven venir (decimales con coma, variables que se
   envían vacías, `"lat:"` con dos puntos, la cabecera con texto de sobra), y
   están en [`atajo-iphone.md`](atajo-iphone.md). Espera más.
3. **Corrige la receta con lo que se vea en pantalla.** Los nombres exactos de
   las acciones de iOS pueden no coincidir; el documento avisa de ello. Lo que
   se aprenda se escribe ahí, que para eso existe.
4. La prueba que cierra la fase: ejecutar el atajo **dos veces seguidas** y ver
   `guardados: N, duplicados: 0` y luego `guardados: 0, duplicados: N`. Eso
   demuestra que reenviar el álbum entero no duplica el viaje.
5. Después, abrir `/mapa` y comprobar que el trayecto sale.

**No propongas otros caminos.** Existen un importador por cable
(`tools/importar_fotos.py`) y unos archivos de systemd para vigilar una carpeta
(`tools/systemd/`, **desinstalados a propósito**), pero el usuario ya dijo
claramente que lo que quiere es el atajo. Los otros están ahí como respaldo,
no como alternativa que haya que vender.

### Y de paso, qué más se puede cerrar

```bash
python tools/ver_telemetria.py 50    # ¿llegan muestras sin huecos?
python tools/ver_notas.py            # ¿hay notas escritas desde el móvil?
python tools/diagnostico.py          # línea "puntos de las fotos"
```

- **Si la telemetría lleva días llegando sin huecos, la 2d se cierra**, y solo
  entonces se desbloquea el *perfil* del §4. Escríbelo en `CLAUDE.md`.
- **Si no**, el perfil no se hace. No se negocia: está razonado en el registro
  de decisiones y construir análisis sobre una fuente no demostrada es trabajo
  que hay que tirar.

---

## 2. Alcance: qué NO se hace

- **Nada de subir fotos a tamaño completo.** El presupuesto de disco está
  calculado en el README y las miniaturas son otra cosa (§3).
- **Ninguna edición de notas ni de puntos desde la web.** Se corrige por
  consola, como hasta ahora.
- **Ni compartir, ni exportar, ni multiusuario.**
- **El perfil de telemetría, solo si la 2d se ha cerrado** (§1).
- **El chatbot no es esta fase** (§6). Se deja preparado, no montado.

---

## 3. Miniaturas: que el mapa enseñe las fotos

Hoy el mapa dice `📷 IMG_4736.jpeg` y no puede enseñarla, porque la foto vive
en el móvil o en el portátil y no se sube (decisión 30). Lo que falta no es
subir las fotos: es **una miniatura**.

Las cuentas, que son las que hacen viable esto: una miniatura de 200×150 a JPEG
de calidad media son ~8 KB, así que **mil fotos son 8 MB** de los ~355 MB
disponibles. Es la mejor relación entre lo que aporta y lo que cuesta que queda
pendiente.

Diseño obligatorio:

- **Se generan donde ya se leen los metadatos**, no en el servidor. En
  `tools/importar_fotos.py` (Pillow *sí* puede entrar aquí: es una herramienta
  local, va en `requirements-dev.txt`, **no en `requirements.txt`**), y en el
  atajo del iPhone con la acción *Redimensionar imagen*. El servidor no
  redimensiona nada: la CPU es cuota diaria.
- **Viajan en `multipart/form-data`, no en base64.** El razonamiento completo
  está en la decisión 27 y sigue valiendo entero.
- **El nombre del archivo sale del `client_id`/nombre ya validado, jamás del
  cliente.** Ponle un test con `../../etc/passwd`.
- **Se valida que sea una imagen de verdad** por magic bytes, no por la
  extensión ni por el `Content-Type`.
- **Se escribe el archivo ANTES que la fila**, y de forma atómica
  (`os.replace`). Al revés, un fallo deja una fila apuntando a una foto que no
  existe y el reintento la da por duplicada: la miniatura se pierde para
  siempre. Está razonado en la decisión 27.
- **Presupuesto de disco explícito y aviso antes del muro.** `diagnostico.py`
  ya avisa por debajo de 50 MB libres; extiéndelo con lo que ocupan las
  miniaturas. Quedarse sin disco a mitad de viaje no puede ser una sorpresa.
- **Una foto sin miniatura sigue siendo un punto válido.** El mapa no puede
  romperse porque falte una imagen.

Y una decisión que hay que tomar y dejar escrita: **qué pasa si la miniatura
llega y el punto no existía todavía** (o al revés). Elige, y explica la
alternativa que descartas.

---

## 4. El perfil — **solo si la 2d está cerrada**

Un sitio donde se ve todo junto: pasos, batería, sitios, kilómetros, notas.

- **Antes de la primera métrica nueva, decide la forma de la tabla.** Hoy
  `telemetria` tiene una columna por métrica. Está razonado en el roadmap de
  `CLAUDE.md`: con la tabla casi vacía la migración es gratis, con un mes de
  viaje dentro cuesta un fin de semana. **Esta decisión toca antes de escribir
  la pantalla, no después.**
- **Pon nombre a las coordenadas al CONSULTAR, no al ingerir.** La pieza existe
  (`location_context.reverse_geocode()`, con caché por coordenada redondeada).
  Resolverlo en la ingesta metería una llamada de red en la ruta que no puede
  fallar. Está razonado en el roadmap.
- **Nada de gráficas hasta que haya datos que dibujar.** Un gráfico de tres
  puntos miente más que una tabla.

---

## 5. `manifest.json` y PWA

- `manifest.json` + iconos para instalar la app en el iPhone.
- `theme-color` y los `safe-area` ya están en `base.html`.
- **Comprueba que la sesión sobrevive** al modo instalado: la cookie dura 90
  días y es `Secure`, y en PWA una redirección por `http://` la descarta en
  silencio (es la trampa de la decisión 15).

---

## 6. El resumen narrativo del viaje

Lo que convierte el mapa en un recuerdo: un texto generado por el LLM a partir
de las notas y de la ruta.

- **Reutiliza `llm_providers` tal cual.** El prompt y el esquema se definen una
  vez, como en `ai_orchestrator`. No metas nada específico de un proveedor
  fuera de ese módulo.
- **Kimi (`kimi-k3`) es el proveedor pensado para esto**: ~0,03 $ por
  generación. Comprueba el saldo con `tools/diagnostico.py` antes.
- **Aquí SÍ conviene reintentar ante un 429**, al revés que en las
  recomendaciones. La decisión 12 dice explícitamente que deja de aplicar
  cuando nadie está esperando. Si el resumen se genera en segundo plano,
  esperar un minuto y reintentar es lo correcto — **y hay que dejarlo escrito
  en el registro de decisiones**, porque contradice una regla anterior a
  propósito.
- **El resumen se guarda, no se regenera en cada visita.** Cada generación
  cuesta dinero y tarda; y un recuerdo que cambia cada vez que lo abres no es
  un recuerdo.
- **El modelo no puede inventarse sitios ni fechas.** Recibe la ruta ya
  construida y el prompt se lo prohíbe explícitamente, igual que con el tiempo
  en la Fase 2.

### Lo que se deja preparado y NO se monta

El **chatbot sobre tus datos** es la fase siguiente, no esta. Lo que esta fase
tiene que dejar listo es la pieza que le hará falta: **una función que devuelva
el contexto del viaje en un formato que un modelo pueda leer** (la ruta, las
notas, y las métricas si la 2d se cerró). Si eso queda hecho y probado, el
chatbot es conectar el contexto a `llm_providers`.

---

## 7. Verificación

- **Extiende `tools/diagnostico.py`** con lo que ocupan las miniaturas.
- **Tests sin red y sin API keys**, con el test client de Flask:
  - Una miniatura con nombre malicioso no escribe fuera de su directorio.
  - Un archivo que no es imagen se rechaza aunque se llame `.jpg`.
  - Una miniatura por encima del límite se rechaza **sin escribir en disco**.
  - El token de ingesta no abre las rutas de sesión, y al revés.
  - El resumen se genera con un `FakeProvider` inyectado, nunca con la API real.

---

## 8. Documentación

- **`CLAUDE.md`**: decisiones nuevas (miniaturas, el reintento del 429 en
  segundo plano y por qué contradice la decisión 12, la forma de la tabla de
  métricas si se toca). Actualiza la tabla de estado y la arquitectura.
- **`README.md`**: rutas nuevas, variables nuevas, presupuesto de disco
  actualizado.
- **`docs/troubleshooting.md`**: síntomas nuevos.
- **`.env.example`**: las variables nuevas con su explicación.

---

## 9. Cómo trabajar

1. **Empieza por el §1**: comprueba qué fases se pueden cerrar y dilo. Puede
   que media fase sobre o que aparezca trabajo que no está aquí.
2. **Antes de escribir código**, enséñame cómo viajan las miniaturas y dónde se
   guardan. Si eso está mal, lo demás no importa.
3. Implementa por partes con la suite en verde, y **commit al terminar cada
   parte**.
4. Al terminar, dime **qué has verificado de verdad y qué no**. Lo que no
   puedas probar (la cámara del iPhone, el modo PWA instalado), dilo
   explícitamente en vez de darlo por bueno.
5. **No amplíes el alcance.** Si ves algo que merece la pena y no está aquí,
   anótalo al final y sigue.
6. Cuando estés a punto de decidir algo no obvio, **dilo y explica la
   alternativa que descartas**.

---

## 10. Pendiente de fases anteriores (no lo hagas aquí, pero tenlo presente)

- **Cerrar la 2d:** que la telemetría llegue sin huecos durante varios días.
- **Cerrar la 3:** escribir una nota desde el iPhone en una zona sin cobertura
  y verla aparecer en el mapa al volver la señal.
- **Cerrar la 3b:** montar el atajo de `docs/atajo-fotos.md` con un álbum real,
  y **probar un HEIC de verdad** — el lector está probado contra un JPEG real
  de iPhone y contra HEIC fabricados, pero no contra uno del carrete.
- Los espejos de Overpass siguen casi todos muertos (decisión 22).
- Los datos de Hevy: es *pull* desde el servidor y el plan gratuito da **una
  sola tarea programada al día**. Verificar la API y la lista blanca del proxy
  **antes** de escribir nada.
