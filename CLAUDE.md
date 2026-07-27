# CLAUDE.md — Compañero de viaje

Documento de trabajo del proyecto. Lo lee Claude Code al empezar cada sesión.

> **Nota sobre este archivo.** Hasta ahora `CLAUDE.md` contenía en realidad un
> prompt de despliegue, no este documento (el mensaje del commit prometía
> "reglas, decisiones y roadmap" pero el contenido era otra cosa). Ese prompt
> se conserva íntegro en [`docs/prompt-despliegue.md`](docs/prompt-despliegue.md);
> este archivo es ya el documento de proyecto con las secciones numeradas.

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
    llm_providers.py         Único módulo que conoce Anthropic / Gemini / Ollama
    storage.py               SQLite: caché y notas
    auth.py                  Login de un solo usuario
```

Regla: `app.py` valida la entrada, llama a un módulo y formatea la respuesta.
Cada módulo tiene una función de entrada tipada y lanza su propia excepción
(`LocationError`, `WeatherError`, `AIError`). Solo `storage.py` abre la BD.

## 4. Comandos

```bash
python run.py                              # servidor local (127.0.0.1:5000)
python -m pytest -q                        # tests (sin red, sin API keys)
python tools/diagnostico.py                # estado de cada dependencia
python tools/diagnostico.py --todos        # prueba todos los proveedores de LLM
python tools/hash_password.py              # genera SECRET_KEY y APP_PASSWORD_HASH
```

## 5. Estado actual

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Esqueleto Flask, login, GPS → nombre del lugar | ✅ Hecho |
| 2 | Open-Meteo + Overpass + recomendaciones con LLM | ✅ Hecho |
| 2b | Proveedor de LLM intercambiable (Anthropic / Gemini) | ✅ Hecho |
| — | **Verificado de extremo a extremo con Gemini** (`gemini-3.6-flash`) | ✅ |
| 3 | Notas geolocalizadas (cola offline) y mapa Leaflet | ⬜ Pendiente |
| 4 | Resumen narrativo del viaje + manifest PWA | ⬜ Pendiente |

**Pendiente de validar:** despliegue en PythonAnywhere y prueba desde el iPhone
real (ver [`docs/prompt-despliegue.md`](docs/prompt-despliegue.md)). El GPS solo
funciona en HTTPS, así que hasta desplegar no se puede probar de verdad.

**Saldo de Anthropic agotado** (confirmado: la API devuelve 400 con *"Your credit
balance is too low"*). Por eso se usa Gemini, ya verificado generando
recomendaciones reales en ~11 s con `gemini-3.6-flash`.

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

## 7. Roadmap

- **Fase 3.** Notas geolocalizadas con cola offline (IndexedDB en el móvil,
  sincronización cuando hay red) y mapa acumulado con Leaflet.
- **Fase 4.** Resumen narrativo del viaje generado por el LLM a partir de todas
  las notas, y `manifest.json` + iconos para instalar como PWA en el iPhone.
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

- **Defensa en profundidad para los secretos.** No basta con "no meter la key en
  el mensaje": también se redacta al salir, se redacta por patrón aunque la
  clave no sea la nuestra, y se redactan fragmentos parciales. Cada capa asume
  que la anterior puede fallar.
