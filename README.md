# Compañero de viaje

Aplicación web (PWA) que usa el GPS del móvil para saber dónde estás,
recomendarte qué hacer cerca con ayuda de un LLM, guardar notas
geolocalizadas y construir el mapa acumulado de un viaje.

---

## Estado

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Esqueleto Flask, login, GPS → nombre del lugar | ✅ Hecho |
| 2 | Open-Meteo + Overpass + recomendaciones con LLM | ✅ Hecho |
| 2b | Proveedor intercambiable: Anthropic / Gemini / Kimi | ✅ Hecho |
| — | **Desplegado y validado en iPhone real** (27-07-2026) | ✅ |
| 2d | Ingesta de telemetría del iPhone (pasos, ubicación, batería) | 🟨 MVP funcionando; aparcada a la espera de días de datos |
| 3 | Notas geolocalizadas (con cola offline) y mapa Leaflet | 🟨 Hecho; falta validarlo en el móvil |
| 3b | Ruta del viaje desde el EXIF de las fotos, y revivirla | 🟨 Hecho; falta probarlo con fotos reales |
| 4 | Resumen narrativo del viaje + manifest PWA | ⬜ Pendiente |

Desplegado en PythonAnywhere (plan gratuito) y probado desde un iPhone con datos
móviles: GPS con precisión de ±18 m, lugar resuelto, tiempo, y recomendaciones
generadas por Gemini en ~13 s. Las seis comprobaciones de
[`docs/validacion-movil.md`](docs/validacion-movil.md) en verde.

## Diagnóstico rápido

Cuando algo no funcione (y estés lejos de casa), esto te dice **qué** pieza
está rota, no solo que hay un error:

```bash
python tools/diagnostico.py            # Cudillero por defecto
python tools/diagnostico.py 43.38 -4.29
python tools/diagnostico.py --todos    # prueba todos los proveedores de LLM
python tools/diagnostico.py -v         # con la traza completa de cada fallo
```

Salen cuatro bloques, y el orden va de lo que no puede fallar a lo que degrada:
**CONFIGURACIÓN** (lo que ni se intenta si está mal), **DATOS DEL VIAJE** (lo
nuestro: SQLite, el disco y si las fuentes propias llegan sin huecos), **FUENTES
EXTERNAS** (lo de fuera, que se cae y se sustituye por un aviso) y **EL
CONTEXTO**, que prueba `contexto.construir()` por el mismo camino que usa la app
y **cronometrado**: su tiempo es un contrato de menos de un segundo, y si sube
de dos falla a propósito, porque alguien habrá metido una fuente lenta en el
camino normal.

Dos cosas que se leen mal si no se avisan:

- **La telemetría separa lo real de lo simulado.** `14 reales en 3/5 días — 2
  días SIN datos` dice mucho más que un total, y un total mezclado diría que
  está llegando cuando no llega nada (decisión 36).
- **El código de salida es 0 también en modo degradado**, porque degradar es un
  estado de funcionamiento diseñado a propósito y no un despliegue roto. Solo
  devuelve 1 cuando la app no se puede usar.

Si ya sabes el síntoma pero no la causa,
[`docs/troubleshooting.md`](docs/troubleshooting.md) va por síntomas: la app no
carga, bucle de login, el GPS no pide permiso, la IA no responde, todo lento…

---

## Estructura

```
roadtrip/
├── run.py                  Arranque en local (python run.py)
├── wsgi.py                 Punto de entrada para PythonAnywhere
├── requirements.txt
├── .env.example            Plantilla de variables de entorno
├── app/
│   ├── app.py              Rutas Flask. Sin lógica de negocio.
│   ├── config.py           Configuración desde variables de entorno
│   ├── modules/
│   │   ├── auth.py                 Login de un solo usuario
│   │   ├── contexto.py             El estado del viaje: una definición, tres consumidores
│   │   ├── luna.py                 Fase e iluminación sin red; salida y puesta de met.no
│   │   ├── diario.py               El primer sitio de cada día. Registra; NO analiza
│   │   ├── location_context.py     Nominatim (dónde estoy) + Overpass (qué hay cerca)
│   │   ├── weather_context.py      Open-Meteo (tiempo + oleaje) e interpretación
│   │   ├── ai_orchestrator.py      Prompt, esquema y caché. AGNÓSTICO del proveedor.
│   │   ├── llm_providers.py        Único módulo que conoce Anthropic / Gemini / Kimi / Ollama
│   │   ├── ingest.py               Telemetría del móvil: token, validación, idempotencia
│   │   ├── notes.py                Notas geolocalizadas y progreso del mapa
│   │   ├── photo_meta.py           EXIF de una foto: cuándo y dónde. Sin dependencias
│   │   ├── waypoints.py            Puntos del viaje sacados de las fotos
│   │   ├── ruta.py                 Notas + fotos en una línea de tiempo, y su medida
│   │   ├── timeparse.py            Instantes ISO 8601: validar, canonizar, volver a local
│   │   └── storage.py              SQLite: caché, notas, puntos y telemetría
│   ├── templates/          HTML (Jinja2)
│   └── static/
│       ├── js/notas.js     Cola offline en IndexedDB: guarda primero, envía después
│       ├── js/mapa.js      Mapa, trayecto, progreso y "revivir el viaje"
│       └── vendor/leaflet/ Leaflet 1.9.4, servido por nosotros (no por un CDN)
├── tools/
│   ├── hash_password.py    Genera SECRET_KEY y APP_PASSWORD_HASH
│   ├── token_ingesta.py    Genera el token del iPhone y su hash
│   ├── diagnostico.py      Estado de cada pieza: config, datos, fuentes y contexto
│   ├── ver_telemetria.py   Últimas muestras del móvil, y borrado de las malas
│   ├── ver_notas.py        Notas del viaje, progreso, y borrado de las malas
│   ├── importar_fotos.py   Lee el EXIF de una carpeta y monta la ruta
│   └── listar_modelos.py   Qué modelos de Gemini funcionan con tu key
├── tests/                  pytest
└── data/                   BD e imágenes. NO va a git.
```

**Regla de arquitectura:** `app.py` solo valida la entrada, llama a un módulo y
formatea la respuesta. Cada módulo tiene una función de entrada clara, tipada, y
lanza sus propias excepciones (`LocationError`, `WeatherError`, `AIError`,
`IngestError`, `NoteError`, `WaypointError`) en vez de devolver `None`. Ningún módulo salvo `storage.py` abre la base de datos.

---

## Instalación en local

```bash
git clone <tu-repo> roadtrip && cd roadtrip

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Genera SECRET_KEY y APP_PASSWORD_HASH
cp .env.example .env
python tools/hash_password.py     # copia su salida en el .env

python run.py                     # http://127.0.0.1:5000
```

Tests:

```bash
python -m pytest -q
```

> **⚠️ El GPS solo funciona en HTTPS o en `localhost`.**
> Es una restricción de seguridad del navegador, no un bug. Consecuencia
> práctica: **no puedes probar desde el móvil apuntando a `http://192.168.x.x`**
> (el navegador bloqueará la ubicación sin dar un error claro). Prueba en
> `localhost` desde el portátil, y en el móvil ya contra PythonAnywhere.

---

## Cambiar de proveedor de LLM

El proveedor está detrás de una única interfaz (`app/modules/llm_providers.py`).
Cambiarlo es una variable de entorno:

```bash
LLM_PROVIDER=gemini      # Google AI Studio: capa gratuita, sin tarjeta
LLM_PROVIDER=anthropic   # Claude: requiere saldo
LLM_PROVIDER=kimi        # Moonshot AI: sin capa gratuita, se activa con 1 $
LLM_PROVIDER=ollama      # local: estructura preparada, sin implementar
```

`ai_orchestrator.py` no importa ningún SDK: el prompt de sistema y el esquema de
salida se definen ahí una sola vez y se pasan al proveedor activo. Para
comprobar cuáles tienes operativos:

```bash
python tools/diagnostico.py --todos     # ¿qué proveedores tengo operativos?
python tools/listar_modelos.py          # ¿qué modelos de Gemini funcionan con mi key?
```

> **La lista de modelos de la API no es la lista de modelos usables.** Varios
> aparecen listados y devuelven 404 (*"no longer available to new users"*) o
> 429 por cuota agotada. `listar_modelos.py` prueba cada uno de verdad y te
> dice qué poner en `GEMINI_MODEL`.

---

## Variables de entorno

| Variable | Obligatoria | Descripción |
|----------|:-----------:|-------------|
| `SECRET_KEY` | ✅ | Firma la cookie de sesión. Genérala con `tools/hash_password.py`. Cambiarla cierra todas las sesiones. |
| `APP_PASSWORD_HASH` | ✅ | Hash de tu contraseña. **Nunca la contraseña en claro.** |
| `LLM_PROVIDER` | ❌ | `anthropic`\|`gemini`\|`kimi`\|`ollama`. Por defecto `anthropic`. Cambiar esto es lo único necesario para cambiar de modelo. |
| `GEMINI_API_KEY` | si usas gemini | Clave de Google AI Studio (capa gratuita, sin tarjeta). El prefijo varía (`AIza…`, `AQ.…`): no lo uses para validarla. |
| `GEMINI_MODEL` | ❌ | Por defecto `gemini-3.6-flash`. Averigua cuáles sirven con tu key: `python tools/listar_modelos.py`. |
| `ANTHROPIC_API_KEY` | si usas anthropic | Clave de la API de Claude (console.anthropic.com). |
| `ANTHROPIC_MODEL` | ❌ | Por defecto `claude-opus-5`. |
| `ANTHROPIC_EFFORT` | ❌ | `low`\|`medium`\|`high`\|`xhigh`\|`max`. Por defecto `low`. Mando de latencia contra calidad. |
| `KIMI_API_KEY` | si usas kimi | Clave de Moonshot AI (platform.kimi.ai → Console → API Keys). No hay capa gratuita: se activa con una recarga mínima de 1 $. |
| `KIMI_MODEL` | ❌ | Por defecto `kimi-k3`. |
| `KIMI_REASONING_EFFORT` | ❌ | `low`\|`high`\|`max`. Por defecto `low`. **Ojo: no son los mismos valores que `ANTHROPIC_EFFORT`**, y solo aplica a `kimi-k3`. |
| `KIMI_BASE_URL` | ❌ | Por defecto `https://api.moonshot.ai/v1`. Cámbialo solo si usas el endpoint de China (`api.moonshot.cn`). |
| `SHOW_AI_ERROR_DETAIL` | ❌ | Muestra el error crudo del proveedor en la interfaz. Desactivado por defecto; el detalle va siempre al log y al diagnóstico. La API key nunca aparece, esté activado o no. |
| `INGEST_TOKEN_HASH` | para la ingesta | Hash del token con el que el atajo del iPhone envía telemetría. Se genera con `python tools/token_ingesta.py`. **Nunca el token en claro, y nunca el mismo secreto que `APP_PASSWORD_HASH`**: el token vive en claro dentro del iPhone. Vacío = el endpoint responde 401 a todo. |
| `INGEST_MAX_SAMPLES` | ❌ | Máximo de muestras por envío. Por defecto 500. |
| `MAX_CONTENT_LENGTH` | ❌ | Bytes máximos del cuerpo de una petición. Por defecto 131072 (128 KiB). Lo corta Flask **antes** de parsear el JSON. |
| `SESSION_COOKIE_SECURE` | ❌ | La cookie de sesión solo viaja por HTTPS. **Activado por defecto: déjalo así en el servidor.** Ponlo a `0` únicamente para probar por `http://` desde otro aparato de tu red local. En `localhost` no hace falta tocarlo. |
| `NOMINATIM_USER_AGENT` | ❌ (pero ponla) | La política de uso de Nominatim exige identificarse con un contacto real. Sin ello pueden bloquear la IP del servidor. |
| `DATA_DIR` | ❌ | Dónde viven la BD y las fotos. Por defecto `./data`. |
| `HTTP_TIMEOUT` | ❌ | Segundos de timeout para APIs externas. Por defecto 10. |

La app **falla al arrancar** si falta una obligatoria. Es intencionado: mejor un
error claro al desplegar que un fallo raro a mitad de una petición.

Los interruptores (`SHOW_AI_ERROR_DETAIL`, `SESSION_COOKIE_SECURE`) entienden
`1/true/yes/si/on` y `0/false/no/off`. Vacío o mal escrito los deja en su valor
por defecto, que siempre es el seguro: una errata no enciende la depuración ni
desprotege la cookie.

---

## Despliegue en PythonAnywhere

Checklist para seguir de arriba abajo marcando casillas. Sustituye `TU_USUARIO`
por tu usuario de PythonAnywhere en todas partes.

Si algo falla, la tabla de [`docs/troubleshooting.md`](docs/troubleshooting.md)
va por síntomas.

### Antes de empezar

- [ ] La suite pasa en local: `python -m pytest -q`
- [ ] El diagnóstico da OK en local: `python tools/diagnostico.py 43.5622 -6.1456`
- [ ] Tienes a mano la `GEMINI_API_KEY` y una contraseña para la app

### 1. Código y entorno

- [ ] **Clonar** en una consola Bash de PythonAnywhere:
      ```bash
      git clone <tu-repo> ~/roadtrip
      ```
- [ ] **Crear el virtualenv** (Python 3.11; el código no usa nada posterior).
      Con `venv`, el módulo estándar, **no con `mkvirtualenv`**:
      ```bash
      python3.11 -m venv ~/.virtualenvs/roadtrip
      source ~/.virtualenvs/roadtrip/bin/activate
      ```
- [ ] **Comprobar que el venv está sano antes de instalar nada:**
      ```bash
      python -c "import subprocess, _posixsubprocess; print('venv OK')"
      ```
- [ ] **Instalar** (no instales `requirements-dev.txt`: el servidor no corre los tests):
      ```bash
      pip install -r ~/roadtrip/requirements.txt
      ```

> **Por qué `venv` y no `mkvirtualenv`.** La documentación de PythonAnywhere
> recomienda `mkvirtualenv --python=/usr/bin/python3.11`, y **en esta cuenta no
> funciona**: crea un virtualenv cuyo `sys.path` no incluye el directorio de
> módulos compilados de C, y el primer `pip install` muere con
> `ModuleNotFoundError: No module named '_posixsubprocess'`. Ese módulo es parte
> del núcleo de Python: si "falta", el intérprete está mal montado y no hay nada
> que instalar para arreglarlo. `python3.11 -m venv` lo crea bien y en la misma
> ruta, así que la pestaña *Web* se configura igual. De ahí la comprobación de
> arriba: son dos segundos, y te ahorran depurar después de bajar 100 MB.

> **Cuota de disco (plan gratuito: 512 MB).** El virtualenv completo ocupa unos
> 100 MB. Entra de sobra, pero si andas justo, `anthropic` son 13 MB que puedes
> omitir mientras uses `LLM_PROVIDER=gemini`: instala solo `google-genai`.
> Comprueba con `du -sh ~/.virtualenvs/roadtrip`.

### 2. Secretos y configuración

- [ ] **Generar credenciales nuevas** (no reutilices las del portátil: si el
      `.env` local se filtra alguna vez, no quieres que abra también el
      servidor):
      ```bash
      cd ~/roadtrip && python tools/hash_password.py
      ```
- [ ] **Crear el `.env` en el servidor** con `nano ~/roadtrip/.env`.
      PythonAnywhere no lee `.env` solo; lo carga `config.py`:
      ```bash
      SECRET_KEY=<la que acaba de generar hash_password.py>
      APP_PASSWORD_HASH=<el hash que acaba de generar>
      LLM_PROVIDER=gemini
      GEMINI_API_KEY=<tu clave de aistudio.google.com>
      GEMINI_MODEL=gemini-3.6-flash
      NOMINATIM_USER_AGENT=roadtrip-companion/0.1 (tu-nombre; tu@email.com)
      DATA_DIR=/home/TU_USUARIO/roadtrip/data
      ```
      El `NOMINATIM_USER_AGENT` con contacto **real** no es cortesía: su
      política lo exige y pueden bloquear la IP del servidor, que compartes con
      otros usuarios de PythonAnywhere.

      `DATA_DIR` absoluto porque el proceso web no arranca necesariamente desde
      la raíz del proyecto, y una ruta relativa crearía la base de datos en un
      sitio distinto según quién la abra.

      **El `.env` nunca se sube a git**, ni aquí ni en local.

### 3. La web app

- [ ] *Web* → *Add a new web app* → **Manual configuration** → **Python 3.11**
      (manual, no "Flask": la configuración automática monta su propio esqueleto)
- [ ] **WSGI configuration file**: borra todo el contenido y deja exactamente
      ```python
      import sys
      path = '/home/TU_USUARIO/roadtrip'
      if path not in sys.path:
          sys.path.insert(0, path)
      from wsgi import application
      ```
- [ ] **Virtualenv**: `/home/TU_USUARIO/.virtualenvs/roadtrip`
- [ ] **Static files: NINGUNO. Si hay un mapeo `/static/`, bórralo.**
      Parece al revés de lo recomendado, y está medido (decisión 48): el nginx
      de PythonAnywhere sirve los estáticos **sin `Cache-Control` ni `ETag`**,
      solo con `Last-Modified`, así que el navegador aplica caché heurística y
      **revalida cada archivo en cada navegación** durante las horas siguientes
      a un despliegue. Medido contra el desplegado: 4-5 s de estáticos por
      entrar al Mapa. Sirviéndolos Flask salen con `max-age` de un año e
      `immutable`, que es seguro porque la URL lleva `?v=<mtime>`: se piden una
      vez por despliegue y ninguna más.
      Si algún día estorba, se vuelve atrás añadiendo el mapeo otra vez.
- [ ] **Force HTTPS: activado.** No es opcional. La cookie de sesión sale
      marcada `Secure`, así que si entras por `http://` el navegador la
      descarta y la app **entra en un bucle de login sin mensaje de error**.
- [ ] **Reload**

### 4. Comprobar que arrancó

- [ ] ```bash
      curl https://TU_USUARIO.pythonanywhere.com/healthz
      ```
      Esperado: `{"ia_configurada":true,"status":"ok"}`

      `ia_configurada:false` = la app está viva pero `LLM_PROVIDER` o
      `GEMINI_API_KEY` están mal en el `.env`. Sin respuesta = mira *Web* →
      **Error log**.

- [ ] **El diagnóstico, desde el servidor** (consola Bash de PythonAnywhere, no
      desde tu portátil):
      ```bash
      cd ~/roadtrip && python tools/diagnostico.py 43.5622 -6.1456
      ```

> **Este paso es la puerta de entrada al móvil, y en el plan gratuito es el que
> más probablemente falle.** PythonAnywhere gratuito saca *todo* el tráfico por
> un proxy con **lista blanca de dominios**. Un host no permitido no da error de
> conexión: devuelve un **403 con cuerpo HTML** que viene del proxy, no de la
> API. La app lo tratará como "esa fuente ha fallado" y degradará en silencio,
> así que descúbrelo aquí y no con el teléfono en la mano en Asturias.
>
> Hosts que necesita la app:
>
> | Host | Para qué |
> |---|---|
> | `nominatim.openstreetmap.org` | Dónde estoy |
> | `api.open-meteo.com` · `marine-api.open-meteo.com` | Tiempo y oleaje |
> | `overpass-api.de` · `overpass.kumi.systems` · `overpass.private.coffee` | Qué hay cerca |
> | `generativelanguage.googleapis.com` | Gemini |
> | `api.moonshot.ai` | Kimi (comprobado: **sí** está permitido) |
>
> Si alguno no está permitido, se pide en el foro de PythonAnywhere. Mientras
> tanto la app **sigue siendo utilizable**: solo la ubicación es imprescindible
> (ver *Degradación en cascada*).

Recuerda: **cada cambio en el `.env` o en el código exige pulsar *Reload***. Es
el fallo más tonto y el más frecuente.

### 5. Validar en el móvil

El paso que de verdad importa: el GPS solo funciona bajo HTTPS, así que hasta
ahora no se ha podido probar nunca de verdad. Sigue
[`docs/validacion-movil.md`](docs/validacion-movil.md), que son seis
comprobaciones en orden con lo que deberías ver en cada una.

---

## Endpoints

| Método | Ruta | Auth | Descripción |
|--------|------|:----:|-------------|
| GET/POST | `/login` | — | Formulario de acceso |
| GET | `/logout` | — | Cierra sesión |
| GET | `/` | ✅ | Pantalla principal, y el formulario para marcar un sitio |
| GET | `/mapa` | ✅ | El mapa acumulado del viaje y el progreso |
| POST | `/api/location` | ✅ | `{lat, lon}` → datos del lugar |
| POST | `/api/contexto` | ✅ | `{lat, lon}` → dónde estás, qué hora es y qué tiempo hace. **Sin LLM** |
| POST | `/api/pois` | ✅ | `{lat, lon}` → sitios cerca (OpenStreetMap). **Lento, bajo botón** |
| POST | `/api/recommendations` | ✅ | `{lat, lon, refresh?}` → el contexto + recomendación |
| POST | `/api/notes` | ✅ | Crea una nota geolocalizada. Idempotente por `client_id` |
| GET | `/api/notes?year=` | ✅ | Las notas y el progreso del viaje |
| GET | `/api/ruta?year=` | ✅ | El viaje entero: notas + fotos en orden, días y progreso |
| POST | `/api/waypoints` | 🔑 | Metadatos de las fotos (`tools/importar_fotos.py`) |
| POST | `/api/telemetria` | 🔑 | Muestras del iPhone (pasos, ubicación, batería) |
| GET | `/healthz` | — | Comprobación de vida |

✅ = cookie de sesión · 🔑 = token propio en `Authorization: Bearer`, y **solo**
eso: la sesión no da acceso a `/api/telemetria`, a propósito (decisión 24 de
`CLAUDE.md`).

Códigos de error: `400` entrada ausente o inválida · `401` sin sesión o sin
token · `405` método incorrecto · `413` cuerpo por encima de
`MAX_CONTENT_LENGTH` · `502` el servicio de mapas falló (solo en
`/api/location` y `/api/contexto`).

### `/api/notes`

Lo llama la cola offline del navegador, una nota por petición. El `client_id`
lo genera el móvil **antes** del primer intento y lo reutiliza en cada
reintento: es lo que hace que reenviar una nota tras recuperar la cobertura no
la duplique.

```jsonc
// POST /api/notes
{
  "client_id": "6f4b1e2a-8c3d-4a91-b7e0-1f2c3d4e5a6b",  // UUID en minúsculas
  "text": "Mirador sobre la playa, viento fuerte",       // obligatorio, máx. 2000
  "lat": 43.5619, "lon": -6.1467,                        // obligatorias
  "created_at": "2026-07-28T11:32:05+02:00",             // ISO 8601 CON zona horaria
  "place_name": "Cudillero, Asturias",                   // opcional
  "region": "Asturias"                                   // opcional
}
```

Respuestas, que son lo que la cola usa para decidir si borra la nota o la
reintenta:

| Código | Cuerpo | Qué hace la cola |
|---|---|---|
| `201` | `{"estado": "creada", "id": 12, ...}` | La borra: está a salvo |
| `200` | `{"estado": "duplicada", "id": 12, ...}` | La borra: ya estaba (reintento normal) |
| `400` | `{"error": "…qué campo está mal"}` | La marca *rechazada* y deja de intentarlo |
| `401` | `{"error": "no_autenticado"}` | La conserva y pide entrar otra vez |

`received_at` lo pone **siempre el servidor** y nunca se acepta del cliente:
`received_at - created_at` es la medida del retraso de la cola offline, y es lo
que enseña `python tools/ver_notas.py`.

`GET /api/notes` devuelve `{total, notes, progreso}`. `progreso` se calcula
sobre **todas** las notas aunque `?year=` filtre la lista: el filtro cambia qué
se pinta, no cuánto llevas hecho.

### La ruta desde tus fotos

Las fotos **no se suben**. Se leen sus metadatos EXIF —cuándo y dónde se
hicieron— y con eso se dibuja el trayecto entero. Una foto son ~3 MB y el plan
gratuito tiene 512 MB; sus metadatos son ~100 bytes.

```bash
python tools/importar_fotos.py ~/Fotos/viaje              # solo informa, no guarda nada
python tools/importar_fotos.py ~/Fotos/viaje --detalle    # foto a foto
python tools/importar_fotos.py ~/Fotos/viaje --enviar https://TU_USUARIO.pythonanywhere.com
python tools/importar_fotos.py --limpiar                  # vacía los puntos importados
```

Empieza siempre por el primero: te dice cuántas fotos traen fecha, GPS y huso
horario **antes** de guardar nada. Tres cosas comprobadas contra archivos
reales que conviene saber:

- **WhatsApp borra el EXIF entero.** Ni fecha, ni GPS, ni cámara. Solo sirven
  los originales del carrete.
- Si la cámara tenía la ubicación desactivada, la foto trae fecha pero no
  sitio: cuenta en el relato del viaje, no en el mapa.
- El huso horario es opcional en el EXIF. Sin él se guarda la hora local tal
  cual y **no se inventa ninguna zona**.

Y se puede automatizar de dos formas, las dos en
[`docs/atajo-fotos.md`](docs/atajo-fotos.md): un **atajo del iPhone** que manda
cada día los metadatos de un álbum concreto, y una **carpeta vigilada** en el
portátil (`~/Pictures/viaje`) que se lee sola en cuanto sueltas fotos dentro.

Para `--enviar` hace falta `INGEST_TOKEN` (el token **en claro**, el mismo del
atajo del iPhone) en el `.env` de tu portátil. En el servidor vive solo el
hash, y así tiene que seguir.

```jsonc
// POST /api/waypoints
// Authorization: Bearer <token de tools/token_ingesta.py>
{
  "fuente": "fotos",
  "puntos": [
    { "archivo": "IMG_4213.JPG",              // clave de la idempotencia
      "capturado_en": "2026-07-28T14:32:05",  // hora LOCAL de la cámara, SIN huso
      "offset_original": "+02:00",            // si la cámara lo escribió
      "lat": 43.5619, "lon": -6.1467,         // opcionales, pero las dos o ninguna
      "altitud": 123.4, "camara": "Apple iPhone 15" }
  ]
}
```

Reenviar la misma carpeta no duplica nada (`UNIQUE(fuente, archivo)`), así que
se puede reimportar cada vez que vuelques el móvil.

### `/api/telemetria`

Lo llama una automatización de Atajos del iPhone cada hora. La receta paso a
paso para montarla está en [`docs/atajo-iphone.md`](docs/atajo-iphone.md).

```jsonc
// POST /api/telemetria
// Authorization: Bearer <token de tools/token_ingesta.py>
{
  "fuente": "atajos-iphone",          // opcional; solo se admite este valor
  "muestras": [                       // máx. INGEST_MAX_SAMPLES
    { "medido_en": "2026-07-27T12:00:00+02:00",   // ISO 8601 CON zona horaria
      "pasos": 4213,                              // entero >= 0, opcional
      "bateria": 78,                              // entero 0-100, opcional
      "lat": 43.5622, "lon": -6.1456 }            // opcionales, pero las dos o ninguna
  ]
}
```

```jsonc
// 200
{ "guardadas": 1, "duplicadas": 5, "descartadas": 0, "errores": [],
  "detalle": ["2026-07-28T01:29:29+00:00 pasos=4213 bat=16% lat=38.39064 lon=-0.51648"] }
```

**Que la mayoría salgan como `duplicadas` es lo normal y lo bueno.** Cada envío
repite a propósito las últimas horas de muestras para sobrevivir a la mala
cobertura sin ninguna cola en el móvil; el endpoint es idempotente y se queda
solo con lo nuevo. Es la decisión 23 de `CLAUDE.md`, y explica por qué el
cuerpo lleva un array.

Una muestra inválida se descarta con su motivo en `errores` y **no** tumba las
buenas del mismo lote.

**`detalle` dice qué se guardó, no solo cuánto**, y solo lista los campos que
llegaron con valor. Esa omisión es la información: `guardadas: 1` sale idéntico
tanto si la muestra iba completa como si perdió la ubicación por una clave mal
escrita (`"lat:"` con dos puntos dentro es JSON válido y se guarda como `NULL`
sin que nada proteste). Si el JSON no llega a parsearse, el `400` incluye
`recibido` con el principio del cuerpo tal cual llegó — depurar un atajo del
iPhone sin ver lo que se está enviando es adivinar.

**`/api/contexto` es la pieza central**, y devuelve `200` aunque falten partes.
Lo que hace seguro ese `200` —porque un `200` no significa que la respuesta
sirva— es que el cuerpo trae su propio veredicto en `fuentes`: nunca hay que
deducir de un `null` si el dato no existe, si aquí no aplica o si la fuente se
cayó.

```jsonc
{
  "ubicacion": { "short_label": "Cudillero, Asturias", "region": "Asturias", ... },
  "momento":   { "iso": "2026-07-28T16:26:57+02:00", "hora": "16:26",
                 "dia_semana": "martes", "zona": "Europe/Madrid",
                 "zona_es_supuesta": false },
  "tiempo":    { "summary": "...", "outdoor_rating": "bueno", ... },  // o null
  "luna":      { "fase": { "nombre": "luna llena", "iluminacion_pct": 99.1,
                           "angulo": 169.13, "creciendo": true },
                 "efemerides": { "salida": "2026-07-28T21:35+02:00", ... },  // o null
                 "veredicto": { "hay_luz": true, "motivo": "..." } },
  "metricas":  null,        // hueco reservado (pasos y batería, tras cerrar la 2d)
  // `tiempo.elevation_m` trae la altitud, gratis y en la misma respuesta.
  "fuentes": {
    "ubicacion": { "estado": "ok",            "motivo": "" },
    "tiempo":    { "estado": "ok",            "motivo": "" },
    "oleaje":    { "estado": "sin_datos",     "motivo": "Esta ubicación no está junto al mar." },
    "luna":      { "estado": "ok",            "motivo": "" },
    "metricas":  { "estado": "no_consultada", "motivo": "..." }
  },
  "warnings": []            // solo los `fallo`, en frases para el usuario
}
```

Los cuatro estados de una fuente, que no son intercambiables:

| Estado | Qué significa | ¿Avisa? |
|---|---|---|
| `ok` | Se consultó y trajo dato | no |
| `sin_datos` | Se consultó, respondió bien y **aquí no hay dato** (oleaje tierra adentro) | no |
| `fallo` | Se consultó y no se pudo | **sí** |
| `no_consultada` | No se pidió, a propósito | no |

`warnings` sale **derivado** de los `fallo`, no se va rellenando a mano: así es
imposible que una fuente falle sin aviso, o que aparezca un aviso de algo que no
ha fallado.

**`/api/recommendations` devuelve 200 aunque fallen fuentes opcionales.** Ver
"Degradación en cascada" más abajo. La respuesta tiene esta forma:

```jsonc
{
  "place":  { "short_label": "Cudillero, Asturias", ... },
  "weather": { "summary": "...", "outdoor_rating": "bueno",
               "water_sports": { "rating": "desaconsejado", "reason": "..." } },
  "pois":   [ { "name": "Playa de Aguilar", "category": "naturaleza",
                "distance_m": 1100 } ],
  "recommendation": {
    "resumen": "...",
    "actividades": [ { "titulo": "...", "por_que_ahora": "...",
                       "origen": "lista_cercana" } ],
    "aviso": ""
  },
  "warnings": []          // qué fuentes fallaron, si alguna
}
```

---

## Degradación en cascada

La propiedad de diseño más importante de la app. Solo **una** fuente es
imprescindible; el resto pueden caerse de forma independiente:

| Falla | Qué pasa |
|-------|----------|
| Ubicación (Nominatim) | `502`. Es lo único sin lo que no hay app. |
| Tiempo (Open-Meteo) | Se recomienda sin él, y el prompt le prohíbe al modelo inventárselo. |
| Efemérides (met.no) | La fase y la iluminación se calculan aquí, así que la luna sigue estando: solo falta la hora de salida. |
| POIs (Overpass) | Ya no está en el camino normal: ver abajo. El modelo tira de conocimiento general y lo marca como tal. |
| El LLM | Se devuelven igualmente ubicación, tiempo y puntos de interés. |

Cada fallo añade una entrada a `warnings`, que la interfaz muestra. Una app
que oculta que le falta la mitad del contexto no es fiable, es opaca.

**Overpass está fuera del camino normal, y el aviso NO se ha silenciado.** Los
tres espejos fallan desde el servidor y cuestan 31,3 s por petición (decisión 22
de `CLAUDE.md`): eso era el 70 % de lo que tardaba la pantalla, gastado en no
obtener nada. Lo que se ha quitado es la **fuente**, no el aviso — callarlo
convertiría un fallo ruidoso en uno silencioso. Ahora:

- buscar sitios cerca es un botón, donde esperar treinta segundos es una
  decisión de quien pulsa (`/api/pois`);
- `/api/recommendations` usa los POIs que ya estén en caché y **nunca** espera a
  Overpass, así que buscar una vez en un sitio los deja disponibles 7 días;
- y `fuentes.pois` distingue los cuatro casos, que no son el mismo:
  `ok`, `sin_datos` (buscado, y aquí no hay nada mapeado), `no_consultada` (no
  se ha buscado) y `fallo` (no se pudo).

Cada actividad recomendada lleva un campo `origen`:
`lista_cercana` (sale de OpenStreetMap, con distancia medida — la interfaz lo
marca como *verificado en el mapa*) o `conocimiento_general` (lo aporta el
modelo). Poder distinguirlos es lo que hace que puedas fiarte del resultado.

---

## Notas de diseño

- **Caché en SQLite (`api_cache`).** Nominatim limita a 1 petición/segundo y
  Overpass es lento e inestable. Cacheamos por coordenada redondeada a 3
  decimales (~110 m) con TTL por tipo de dato: el nombre de un pueblo 30 días,
  el tiempo ~1 hora. Además hace que volver a un sitio ya visitado funcione sin
  cobertura.
- **Las notas llevan un `client_id` generado en el móvil.** Es lo que permite
  reintentar el envío cuando vuelve la cobertura sin duplicar la nota. Está en
  el esquema desde la Fase 1 porque cambiar el modelo de datos con datos reales
  dentro es caro.
- **La cola offline guarda primero y envía después.** Al pulsar *Guardar* la
  nota se escribe en IndexedDB y se da por guardada; el POST es un intento
  posterior. Una nota que se pierde porque el POST falló es exactamente el
  fallo que la Fase 3 existe para impedir: los pasos de Salud se pueden volver
  a consultar hacia atrás, pero una nota escrita en un mirador no está en
  ningún otro sitio. La interfaz enseña cuántas quedan por enviar, porque una
  cola invisible es una cola en la que no confías.
- **Dos fondos de mapa:** *Mapa* (OpenStreetMap), que lleva los nombres
  escritos y sirve para saber por dónde fuiste, y *Satélite* (Esri World
  Imagery con etiquetas encima), que sirve para reconocer el sitio. La
  elección se guarda en el navegador.
- **Leaflet se sirve desde `app/static/vendor/`, no desde un CDN,** con la
  versión fijada (ver `app/static/vendor/leaflet/VERSION.md`). Los **tiles** sí
  los pide el navegador a OpenStreetMap: la lista blanca del proxy de
  PythonAnywhere afecta solo al tráfico saliente del servidor y aquí no
  interviene. Sin cobertura los tiles no cargan y el mapa sale gris, pero las
  chinchetas y el listado siguen, porque salen de nuestro servidor; la página
  lo dice en vez de disimularlo.
- **Presupuesto de disco.** El plan gratuito son **512 MB**: el virtualenv
  ocupa ~101 MB, el repositorio ~3 MB y la base de datos crece despacio (una
  nota de texto son ~200 bytes; un mes entero escribiendo diez notas al día no
  llega a 1 MB). Con las fotos aplazadas, el disco **no es hoy un problema**, y
  aun así `python tools/diagnostico.py` enseña *cuánto ocupamos de la cuota* y
  avisa por debajo de **50 MB libres**: en PythonAnywhere un disco lleno no
  degrada, rompe la app entera, porque SQLite necesita sitio hasta para leer
  (escribe el WAL). Ojo con cómo se mide: la cuota es un límite de la **cuenta**,
  no del sistema de archivos, así que preguntar por el espacio libre del volumen
  contesta 1,6 TB y el aviso no salta jamás. Se declara en `DISCO_CUOTA_MB` (512
  por defecto) y se compara contra lo que ocupan el repositorio y el virtualenv;
  los logs de PythonAnywhere cuentan aparte, así que `du -sh ~` sigue siendo la
  referencia. Cuando lleguen las
  fotos: ~355 MB disponibles a ~450 KB por foto son **~780 fotos**, unas 26 al
  día durante un mes.
- **Fechas siempre en UTC (ISO-8601).** Se convierten a hora local solo al
  mostrarlas.
- **Timeout en toda llamada saliente.** Sin él, una API caída cuelga el worker
  de Flask indefinidamente y la app entera deja de responder.
- **La API key del proveedor nunca sale del backend.** Si llega al navegador,
  cualquiera puede leerla en las herramientas de desarrollo.
- **Overpass con peticiones escalonadas.** Medido en Llanes: probar los tres
  espejos en serie tardaba **53 s** (el primero agotaba su timeout de 30 s y el
  segundo tardaba otros 23). Ahora lanzamos el primero y, si a los 6 s no ha
  contestado, lanzamos el siguiente sin cancelarlo, quedándonos con el que
  llegue antes. Peor caso medido: **13,7 s**. No lanzamos los tres a la vez
  porque triplicaría la carga sobre un servicio comunitario gratuito en el caso
  normal, que es que el primero responda bien.
- **Tiempo y POIs se piden en paralelo.** Open-Meteo tarda <1 s y Overpass
  entre 2 y 25: en serie pagas la suma, en paralelo el máximo.
- **La lógica meteorológica vive en Python, no en el prompt.** Si se puede
  hacer paddle surf se decide con reglas explícitas sobre oleaje y viento
  (`weather_context.water_sports()`), y al modelo se le pasa el veredicto ya
  calculado. Es determinista, testeable y auditable; un LLM no debería hacer de
  meteorólogo cuando unas reglas dan una respuesta mejor.
- **`api.met.no` exige un User-Agent con contacto real, y el proxy de
  PythonAnywhere tiene que dejarlo pasar.** Las dos cosas las comprueba
  `tools/diagnostico.py` en su línea `api.met.no (salida y puesta)`, y por eso
  el checklist obliga a correrlo **en el servidor** antes de tocar el móvil: un
  dominio fuera de la lista blanca devuelve un 403 del proxy que la app degrada
  en silencio. Si esa línea falla, la luna NO desaparece — la fase y la
  iluminación se calculan en local; lo único que se pierde es la hora de salida.
- **La API marina responde 200 con `null`, no 4xx, tierra adentro.**
  Comprobado contra la API real. Asumir un código de error habría sido un bug
  silencioso: creerías tener datos de oleaje donde no los hay.
- **Salida estructurada (JSON Schema).** La API garantiza que la respuesta del
  modelo cumple el esquema. Es la diferencia entre un frontend que se rompe
  cuando el modelo decide escribir markdown, y uno que no se rompe nunca.
- **`formatear_para_prompt()` es una función pura.** Puedes imprimir el prompt exacto
  que recibe el modelo sin gastar una sola llamada a la API. Iterar sobre un
  prompt a ciegas es la forma más cara de perder una tarde.
