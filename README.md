# Compañero de viaje

Aplicación web (PWA) que usa el GPS del móvil para saber dónde estás,
recomendarte qué hacer cerca con ayuda de Claude, guardar notas
geolocalizadas y construir el mapa acumulado de un viaje.

---

## Estado

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Esqueleto Flask, login, GPS → nombre del lugar | ✅ Hecho |
| 2 | Open-Meteo + Overpass + recomendaciones de Claude | ✅ Hecho |
| 3 | Notas geolocalizadas (con cola offline) y mapa Leaflet | ⬜ Pendiente |
| 4 | Resumen narrativo del viaje + manifest PWA | ⬜ Pendiente |

## Diagnóstico rápido

Cuando algo no funcione (y estés lejos de casa), esto te dice **qué** pieza
está rota, no solo que hay un error:

```bash
python tools/diagnostico.py            # Cudillero por defecto
python tools/diagnostico.py 43.38 -4.29
```

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
│   │   ├── location_context.py     Nominatim (dónde estoy) + Overpass (qué hay cerca)
│   │   ├── weather_context.py      Open-Meteo (tiempo + oleaje) e interpretación
│   │   ├── ai_orchestrator.py      Prompt, esquema y caché. AGNÓSTICO del proveedor.
│   │   ├── llm_providers.py        Único módulo que conoce Anthropic / Gemini / Ollama
│   │   └── storage.py              SQLite: caché y notas
│   ├── templates/          HTML (Jinja2)
│   └── static/             CSS, JS, manifest.json, iconos
├── tools/
│   ├── hash_password.py    Genera SECRET_KEY y APP_PASSWORD_HASH
│   ├── diagnostico.py      Estado de cada dependencia externa
│   └── listar_modelos.py   Qué modelos de Gemini funcionan con tu key
├── tests/                  pytest
└── data/                   BD e imágenes. NO va a git.
```

**Regla de arquitectura:** `app.py` solo valida la entrada, llama a un módulo y
formatea la respuesta. Cada módulo tiene una función de entrada clara, tipada, y
lanza sus propias excepciones (`LocationError`, `WeatherError`, `AIError`) en vez
de devolver `None`. Ningún módulo salvo `storage.py` abre la base de datos.

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
| `LLM_PROVIDER` | ❌ | `anthropic`\|`gemini`\|`ollama`. Por defecto `anthropic`. Cambiar esto es lo único necesario para cambiar de modelo. |
| `GEMINI_API_KEY` | si usas gemini | Clave de Google AI Studio (capa gratuita, sin tarjeta). El prefijo varía (`AIza…`, `AQ.…`): no lo uses para validarla. |
| `GEMINI_MODEL` | ❌ | Por defecto `gemini-3.6-flash`. Averigua cuáles sirven con tu key: `python tools/listar_modelos.py`. |
| `ANTHROPIC_API_KEY` | si usas anthropic | Clave de la API de Claude (console.anthropic.com). |
| `ANTHROPIC_MODEL` | ❌ | Por defecto `claude-opus-5`. |
| `ANTHROPIC_EFFORT` | ❌ | `low`\|`medium`\|`high`\|`xhigh`\|`max`. Por defecto `low`. Mando de latencia contra calidad. |
| `SHOW_AI_ERROR_DETAIL` | ❌ | Muestra el error crudo del proveedor en la interfaz. Desactivado por defecto; el detalle va siempre al log y al diagnóstico. La API key nunca aparece, esté activado o no. |
| `NOMINATIM_USER_AGENT` | ❌ (pero ponla) | La política de uso de Nominatim exige identificarse con un contacto real. Sin ello pueden bloquear la IP del servidor. |
| `DATA_DIR` | ❌ | Dónde viven la BD y las fotos. Por defecto `./data`. |
| `HTTP_TIMEOUT` | ❌ | Segundos de timeout para APIs externas. Por defecto 10. |

La app **falla al arrancar** si falta una obligatoria. Es intencionado: mejor un
error claro al desplegar que un fallo raro a mitad de una petición.

---

## Despliegue en PythonAnywhere

1. **Subir el código.** En una consola Bash de PythonAnywhere:
   ```bash
   git clone <tu-repo> ~/roadtrip
   ```

2. **Crear el virtualenv:**
   ```bash
   mkvirtualenv --python=/usr/bin/python3.11 roadtrip
   pip install -r ~/roadtrip/requirements.txt
   ```

3. **Crear la web app:** pestaña *Web* → *Add a new web app* → *Manual
   configuration* → Python 3.11.

4. **Configurar el archivo WSGI.** En *Web* → *WSGI configuration file*,
   borra todo el contenido y pon:
   ```python
   import sys
   path = '/home/TU_USUARIO/roadtrip'
   if path not in sys.path:
       sys.path.insert(0, path)
   from wsgi import application
   ```

5. **Virtualenv:** en la sección *Virtualenv*, escribe
   `/home/TU_USUARIO/.virtualenvs/roadtrip`.

6. **Archivos estáticos:** en *Static files*, añade
   URL `/static/` → Directory `/home/TU_USUARIO/roadtrip/app/static/`.
   Esto hace que el CSS y el JS los sirva el servidor web directamente en vez
   de Flask: más rápido y menos carga.

7. **Variables de entorno.** PythonAnywhere no lee `.env` automáticamente.
   Dos opciones:
   - **Recomendada:** crear el `.env` directamente en el servidor
     (`nano ~/roadtrip/.env`). `config.py` lo carga solo.
   - O declararlas en el archivo WSGI con `os.environ[...]` antes del import.

   En cualquier caso, **el `.env` nunca se sube a git.**

8. **Reload** en la pestaña *Web*. Comprueba
   `https://TU_USUARIO.pythonanywhere.com/healthz` → `{"status": "ok"}`.

Los errores del servidor aparecen en la pestaña *Web* → *Error log*.

---

## Endpoints

| Método | Ruta | Auth | Descripción |
|--------|------|:----:|-------------|
| GET/POST | `/login` | — | Formulario de acceso |
| GET | `/logout` | — | Cierra sesión |
| GET | `/` | ✅ | Pantalla principal |
| POST | `/api/location` | ✅ | `{lat, lon}` → datos del lugar |
| POST | `/api/recommendations` | ✅ | `{lat, lon, refresh?}` → lugar + tiempo + POIs + recomendación |
| GET | `/healthz` | — | Comprobación de vida |

Códigos de error: `400` coordenadas ausentes o inválidas · `401` sin sesión ·
`502` el servicio de mapas falló (solo en `/api/location`).

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
| Tiempo (Open-Meteo) | Se recomienda sin él, y el prompt le prohíbe a Claude inventárselo. |
| POIs (Overpass) | Claude tira de conocimiento general y lo marca como tal. |
| Claude | Se devuelven igualmente ubicación, tiempo y puntos de interés. |

Cada fallo añade una entrada a `warnings`, que la interfaz muestra. Una app
que oculta que le falta la mitad del contexto no es fiable, es opaca.

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
- **Las notas llevarán un `client_id` generado en el móvil.** Es lo que permite
  reintentar el envío cuando vuelve la cobertura sin duplicar la nota. Está en
  el esquema desde la Fase 1 porque cambiar el modelo de datos con datos reales
  dentro es caro.
- **Fechas siempre en UTC (ISO-8601).** Se convierten a hora local solo al
  mostrarlas.
- **Timeout en toda llamada saliente.** Sin él, una API caída cuelga el worker
  de Flask indefinidamente y la app entera deja de responder.
- **La API key de Claude nunca sale del backend.** Si llega al navegador,
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
  (`weather_context.water_sports()`), y a Claude se le pasa el veredicto ya
  calculado. Es determinista, testeable y auditable; un LLM no debería hacer de
  meteorólogo cuando unas reglas dan una respuesta mejor.
- **La API marina responde 200 con `null`, no 4xx, tierra adentro.**
  Comprobado contra la API real. Asumir un código de error habría sido un bug
  silencioso: creerías tener datos de oleaje donde no los hay.
- **Salida estructurada (JSON Schema).** La API garantiza que la respuesta del
  modelo cumple el esquema. Es la diferencia entre un frontend que se rompe
  cuando el modelo decide escribir markdown, y uno que no se rompe nunca.
- **`build_context()` es una función pura.** Puedes imprimir el prompt exacto
  que recibe Claude sin gastar una sola llamada a la API. Iterar sobre un
  prompt a ciegas es la forma más cara de perder una tarde.
