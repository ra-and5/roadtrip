# Qué mirar cuando algo falla

Pensado para consultarse en una gasolinera de Asturias, con prisa y sin ganas de
depurar. Busca tu síntoma y sigue la columna de la derecha.

**Lo primero, siempre, y resuelve la mitad de los casos:**

```bash
# En una consola Bash de PythonAnywhere:
cd ~/roadtrip && python tools/diagnostico.py 43.5622 -6.1456
```

Te dice **qué pieza** está rota, no solo que hay un error. Y desde el móvil:

```
https://TU_USUARIO.pythonanywhere.com/healthz
```

Los errores del servidor están en *Web* → **Error log** (los de arranque) y
*Server log* (los de ejecución).

---

## Tabla de síntomas

| Síntoma | Causa más probable | Qué mirar |
|---|---|---|
| **La app no carga.** El navegador no llega, o da "no se puede conectar" | La web app está parada, o expiró (las cuentas gratuitas caducan cada 3 meses y hay que pulsar un botón para renovarlas) | Pestaña *Web*: ¿está en verde? ¿Sale el aviso de "expired"? Pulsa **Reload**. Comprueba que la URL es exactamente `TU_USUARIO.pythonanywhere.com` |
| **Vuelve al login en bucle**, sin mensaje de error, con la contraseña buena | *Force HTTPS* desactivado y estás entrando por `http://`. La cookie de sesión sale marcada `Secure` y el navegador la descarta **en silencio** | *Web* → **Force HTTPS: activado** → *Reload*. Entra otra vez escribiendo `https://` a mano. (Solo si de verdad necesitas `http`, se puede poner `SESSION_COOKIE_SECURE=0`, pero nunca en el servidor) |
| **Error 500 nada más arrancar** | Falta una variable obligatoria en el `.env`, o el `.env` no está donde la app lo busca | *Error log*, última línea. Si pone `Falta la variable de entorno obligatoria: X`, ya sabes cuál. Comprueba que el archivo es `~/roadtrip/.env` y no `~/.env`, y que el WSGI apunta a `/home/TU_USUARIO/roadtrip` |
| **Error 500 después de haber funcionado** | Casi siempre un cambio sin *Reload*, o la cuota de disco llena (la BD no puede escribir) | *Error log*. Cuota: `du -sh ~` y la barra de la pestaña *Files*. Pulsa *Reload* |
| **El GPS no pide permiso.** Pulsas y no pasa nada | No estás en HTTPS. `navigator.geolocation` está bloqueado y **no da error visible** | Mira el candado en la barra de direcciones. Si pone `http://`, ahí está. Activa *Force HTTPS* |
| **"Has denegado el permiso de ubicación"** | Se pulsó "No permitir". iOS **no vuelve a preguntar** por ese sitio | Ajustes → Safari → Ubicación → *Preguntar* o *Permitir*. También Ajustes → Privacidad → Localización → Safari. Cierra la pestaña y vuelve a abrir |
| **"El GPS tardó demasiado"** | Timeout de 15 s sin fix de satélite | Sal a cielo abierto. Dentro del coche con el móvil en la guantera es el caso típico. Si urge, abre Mapas de Apple para forzar un fix y vuelve |
| **El CSS no se aplica.** Todo texto plano | Los *Static files* no están configurados, o apuntan mal | *Web* → *Static files*: URL `/static/` → Directory `/home/TU_USUARIO/roadtrip/app/static/` (con la barra final). Prueba `curl -I https://.../static/css/style.css`: un 404 lo confirma. *Reload* |
| **La IA no responde pero el resto sí.** Sale ubicación, tiempo y POIs, con el aviso *"Sin recomendación de IA"* | El proveedor falla: sin key, key mala, cuota agotada, o el proxy bloquea el host | `/healthz`: si `ia_configurada` es `false` es el `.env` (`LLM_PROVIDER` o `GEMINI_API_KEY`). Si es `true`, corre el diagnóstico en el servidor: ahí sale el **error crudo** del proveedor |
| **"Cuota agotada" / error 429 con Gemini** | Límite **por minuto** de la capa gratuita | Si hay `ANTHROPIC_API_KEY` o `KIMI_API_KEY`, la app prueba otro proveedor automáticamente. Si solo tienes Gemini, espera un minuto y vuelve a pulsar. Si es persistente, prueba otro modelo con `python tools/listar_modelos.py` |
| **Error 429 con Kimi** | **Tres causas distintas que se arreglan al revés.** El mensaje de la app dice cuál | *Vas demasiado rápido* (con 1 $ son 3 peticiones/min) → espera un minuto. *Servidores saturados* → espera, no es cosa tuya. ***Sin saldo*** → **esperar no arregla nada**: recarga en platform.kimi.ai, o pasa a `LLM_PROVIDER=gemini` (gratis) mientras tanto. Consulta el saldo con `python tools/diagnostico.py`; con `kimi-k3` cada recomendación cuesta ~0,03 $, y `KIMI_MODEL=kimi-k2.6` sale 5 veces más barato |
| **Falta el tiempo, o los POIs, o ambos**, con su aviso | La API está caída, o el proxy del plan gratuito la bloquea | Diagnóstico **en el servidor**. Un **403 con HTML** es el proxy, no la API (ver abajo) |
| **Los POIs fallan a menudo** y todas las actividades salen como *sugerencia general* | Overpass saturado. Medido: `overpass-api.de` da **504 intermitente**, y los otros dos espejos llevan tiempo muertos | Es lo esperable hoy, no un fallo tuyo: falla en un sitio y a los segundos funciona en otro. La app degrada bien. Ver decisión 22 de `CLAUDE.md` |
| **El chat dice que no puede mirar avisos/radar de España** | Falta `AEMET_API_KEY`, o AEMET OpenData no responde | Saca la key en `https://opendata.aemet.es/` → *Obtención de API Key*, pon `AEMET_API_KEY=...` en `.env`, haz *Reload* y corre `python tools/diagnostico.py`. Sin key, el chat avisa y no inventa meteo nacional |
| **`ModuleNotFoundError: No module named '_posixsubprocess'`** al instalar | El virtualenv se creó con `mkvirtualenv` y quedó mal montado | Recréalo con `python3.11 -m venv ~/.virtualenvs/roadtrip`. Ese módulo viene con Python: si "falta", no hay nada que instalar, el venv está roto |
| **Todo va lentísimo** (más de 40 s) | Overpass lento (es un servicio comunitario gratuito) más el modelo | Es en parte normal: peor caso medido de Overpass 13,7 s, más 10-14 s de Gemini. A los 150 s corta solo. Si es sistemático, mira los *CPU seconds* de la pestaña *Web*: agotada la cuota diaria, la cuenta gratuita te ralentiza a propósito |
| **La segunda consulta en el mismo sitio también tarda** | La caché no está escribiendo: `DATA_DIR` mal o sin permisos | `ls -la ~/roadtrip/data/`. Debe existir `roadtrip.db` y crecer. Comprueba que `DATA_DIR` del `.env` es una ruta **absoluta** |
| **El atajo da 400 y la respuesta menciona `openresty`** | **No es la app.** `openresty` es el servidor web que hay delante en PythonAnywhere: la petición ni siquiera llegó a Flask. Medido el 27-07-2026: pasa cuando una cabecera supera los **8 KB**, y el mensaje real es *Request Header Or Cookie Too Large* | Casi siempre es la cabecera `Authorization` con texto de sobra pegado dentro: la salida de `tools/token_ingesta.py` lleva avisos alrededor del token, y al copiar de más se va entera al campo. Ábrelo: tiene que ser **una sola línea corta** (`Bearer` + ~43 caracteres). Bórralo, escribe `Bearer ` a mano y pega solo el token. **Cómo distinguirlo de un fallo nuestro:** si la respuesta es JSON (`{"error": ...}`) el problema es de la app; si es HTML con `openresty` al final, es la puerta de entrada. **Y no siempre es la cabecera:** el 28-07-2026 el mismo 400 lo provocó el **campo de la URL** del atajo, corrompido con algo invisible dentro. Aísla cuál de los dos es así: **quita la cabecera `Authorization` y pon el método en `GET`**; si aun así sale `openresty`, es la URL — ver la fila siguiente |
| **El atajo del iPhone dice que falló** (error al enviar) | Depende del código. **401**: token mal. **400**: el JSON no tiene la forma esperada. **413**: cuerpo demasiado grande. **405**: se está usando GET. Sin respuesta: la app está caída | Haz que el atajo enseñe el cuerpo de la respuesta (`Mostrar Alerta` con el resultado de *Obtener contenido de URL*): salvo en el 401, el mensaje dice **qué campo** está mal. Para el 401, ver la fila siguiente. La receta completa está en [`atajo-iphone.md`](atajo-iphone.md) |
| **El atajo da 401 y el token "es el bueno"** | El 401 no distingue entre "sin cabecera", "cabecera mal formada" y "token incorrecto", **a propósito** (decisión 24) | Por orden: (1) `python tools/diagnostico.py` en el servidor → si `INGEST_TOKEN_HASH` sale **AUSENTE**, es eso, y ninguna cabecera funcionará; (2) comprueba que la cabecera es `Authorization` con valor `Bearer <token>` — con la palabra `Bearer`, un espacio, y **sin comillas ni saltos de línea** (pegar desde Notas mete espacios invisibles); (3) si sigue, regenera las dos puntas con `python tools/token_ingesta.py`. **Y pulsa *Reload*** tras tocar el `.env` |
| **El atajo dice OK pero no llegan datos** | Casi siempre están llegando y se están contando como `duplicadas`, o el atajo manda un lote vacío (Salud no devolvió nada) | Mira la respuesta que devuelve el atajo: `{"guardadas":0,"duplicadas":6}` significa que **sí llegan** y ya estaban. `guardadas` y `duplicadas` a 0 con `descartadas` alto = las muestras se rechazan, y `errores` dice por qué. Un lote vacío da **400** (`'muestras' está vacío`): el atajo no leyó nada de Salud, revisa los permisos de Salud en Ajustes → Privacidad. Confirma desde el servidor con `python tools/ver_telemetria.py` |
| **Llegan duplicados** (la misma hora varias veces en la tabla) | No debería pasar: el `UNIQUE(fuente, medido_en)` lo impide. Si pasa, es que dos muestras del mismo instante **no producen la misma clave** | Mira la columna `huso` de `ver_telemetria.py`. Si el atajo cambió de formato de fecha, o manda la hora **sin zona horaria**, o con segundos/milisegundos variables, cada envío genera un `medido_en` distinto. La fecha tiene que salir de *Formato de fecha → ISO 8601* con zona incluida. Ojo también con `fuente`: solo se admite `atajos-iphone`, cualquier otra da 400 justo para que una errata no cree una serie paralela |
| **La nota no se envía.** Dice "guardada" pero se queda en la lista de pendientes | **Casi siempre es lo correcto, no un fallo**: la nota está a salvo en el móvil y espera cobertura. La cola solo borra una nota cuando el servidor la confirma | Abre la app con cobertura: se reintenta sola al abrirla y en el evento `online`. Si sigue ahí con red buena, mira la consola del navegador. Confirma desde el servidor con `python tools/ver_notas.py`; si la nota aparece allí, llegó y lo que falló fue la respuesta — el siguiente reintento la contará como *duplicada* y la cola la borrará |
| **Una nota sale "rechazada por el servidor"** en rojo, con un motivo | El servidor devolvió **400**: esa nota no va a entrar nunca (texto vacío, coordenada imposible, fecha sin zona horaria). Se deja de reintentar **a propósito**: si no, atascaría la cola y las notas buenas no saldrían | El motivo lo dice la propia línea. Púlsale a *Descartar*. Si pasa con notas normales, es un bug: mira el *Error log* de PythonAnywhere y `python tools/ver_notas.py` |
| **"Ha caducado la sesión"** al enviar notas | La cookie de sesión ha expirado (dura 90 días) o se borró | Vuelve a entrar. **Las notas siguen guardadas en el móvil**: no se pierde nada, se reenvían solas después de entrar |
| **"No se pudo obtener la ubicación"** al guardar una nota | Igual que en la pantalla principal: sin HTTPS el GPS está bloqueado sin dar error, o no hay fix de satélite | Comprueba el candado (`https://`). Sal a cielo abierto. Una nota sin coordenadas no se guarda a propósito: no podría aparecer en el mapa, que es lo único que construye esta fase |
| **El mapa sale gris**, pero las chinchetas y la lista de notas sí están | **No hay conexión con OpenStreetMap.** Es el comportamiento previsto sin cobertura: los tiles los pide el navegador, las notas vienen de nuestro servidor | La propia página lo avisa tras tres tiles fallidos. No hay nada que arreglar: no se implementan mapas offline (fuera del alcance). Ojo con el razonamiento fácil: **la lista blanca del proxy de PythonAnywhere no interviene aquí**, porque el servidor no pide ningún tile |
| **El mapa sale gris Y no hay chinchetas**, con la app cargando por lo demás | Eso ya no es la conexión: o no hay ninguna nota todavía, o `/api/notes` está fallando | Mira el mensaje bajo el listado. `curl -s https://TU_USUARIO.pythonanywhere.com/api/notes` con la cookie, o `python tools/ver_notas.py` en el servidor. Si Leaflet no cargó, la consola del navegador dirá `L is not defined`: comprueba que `app/static/vendor/leaflet/` llegó con el `git pull` |
| **Las chinchetas salen sin icono** (marcas rotas o invisibles) | Falta `app/static/vendor/leaflet/images/`. Leaflet los busca ahí **relativo al CSS** y no da ningún error de consola cuando no están | `ls app/static/vendor/leaflet/images/` en el servidor. Si está vacío, se perdieron al desplegar: vuelve a hacer `git pull` y *Reload*. Cómo bajarlos otra vez está en `app/static/vendor/leaflet/VERSION.md` |
| **Se ha llenado el disco** (error 500 al escribir, o la app entera va rara) | Cuota de 512 MB agotada. Un disco lleno en PythonAnywhere no degrada: **rompe la app**, porque SQLite necesita sitio hasta para leer (escribe el WAL) | `python tools/diagnostico.py` lo dice: enseña *usado de 512* y avisa por debajo de **50 MB libres**. Ojo: mide el repositorio y el virtualenv, no el `$HOME` entero, así que es un suelo — mira dónde se fue de verdad con `du -sh ~/* ~/.virtualenvs/*`. Los sospechosos habituales son el virtualenv (~101 MB; se puede adelgazar instalando solo el SDK del proveedor que uses) y los logs de PythonAnywhere (pestaña *Web* → *Log files*, se pueden vaciar). Las notas de texto no son el problema: un mes entero no llega a 1 MB |
| **`SchemaError: La tabla 'notes' es de la Fase 1`** al arrancar | La base de datos trae la tabla vieja **y con filas dentro**. La migración recrea la tabla, así que se niega a hacerlo si perdería datos | Es intencionado: mejor no arrancar que borrar notas. Exporta lo que haya (`sqlite3 data/roadtrip.db ".dump notes" > notas.sql`), borra la tabla, arranca para que se cree con la forma nueva y reimporta a mano. Con la tabla vacía la migración es automática y no verás esto |
| **`importar_fotos.py` dice que ninguna foto trae metadatos** | Casi siempre pasaron por WhatsApp o Telegram, que **borran el EXIF entero** al comprimir (comprobado contra archivos reales: cero bytes). También lo quitan algunas opciones de exportación ("quitar información de ubicación") | Parte de los **originales del carrete**. En iPhone: Fotos → seleccionar → Compartir → Opciones → activar *Todos los datos de las fotos*. Si exportas por AirDrop o cable, los originales conservan el EXIF; si los reenvías por una app de mensajería, no |
| **Hay fechas pero ningún GPS** | La cámara tenía la ubicación desactivada cuando se hicieron | Ajustes → Privacidad y seguridad → Localización → Cámara → *Al usar la app*. Las fotos ya hechas no se pueden arreglar: cuentan en el relato del viaje, pero no en el mapa. La herramienta lo dice y el mapa también |
| **El trayecto sale con líneas rectas larguísimas** | Entre dos fotos seguidas hay un salto grande: un tramo sin fotos, o dos viajes distintos importados juntos | Es lo esperado, no un fallo: la línea une los puntos que hay. Los saltos de **más de 300 km no suman kilómetros**, y el mapa dice cuántos ha ignorado. Si sobran fotos de otro viaje, quítalas con `python tools/ver_notas.py` (notas) o bórralas de la tabla `waypoints` |
| **Los kilómetros parecen pocos** | Son en **línea recta** entre puntos consecutivos, no los del cuentakilómetros | Es a propósito y está etiquetado así: entre dos fotos separadas por dos horas de carretera de montaña hay muchas más curvas que la recta que las une. Es un mínimo cierto, no una estimación optimista |
| **`--enviar` da 401** | Falta `INGEST_TOKEN` o no es el bueno. Es el token **en claro**, el mismo del atajo del iPhone | En el servidor vive solo el hash (`INGEST_TOKEN_HASH`) y así debe seguir; el token en claro va en el `.env` de tu portátil. Si lo perdiste, `python tools/token_ingesta.py` genera los dos y hay que actualizar el hash en el servidor **y** el token en el atajo. Y pulsar *Reload* |
| **`--enviar` se corta a mitad** | Se fue la red. Los lotes que ya llegaron están guardados | Vuelve a lanzarlo entero cuando tengas red: reenviar no duplica nada (`UNIQUE(fuente, archivo)`), así que dirá "ya estaban" de lo anterior y guardará solo lo que falte |
| **`openresty` 400 incluso con `GET`, sin cuerpo y sin `Authorization`** | El campo de la URL del atajo lleva algo invisible dentro (un salto de línea, un espacio). La petición sale malformada y el servidor web la corta antes de Flask. Señal visual: la URL se dibuja partida en **varios bloques** en vez de una sola pastilla | **Vaciar el campo y reescribirlo NO lo arregla** (comprobado). Abre la URL en **Safari** —tiene que salir `Method Not Allowed`, que además confirma que el móvil llega al servidor—, copia la dirección ya normalizada de la barra, **borra la acción `Obtener contenido de la URL` entera** y añade una nueva pegando ahí |
| **El atajo de fotos manda coordenadas gigantes** (`"lat":38176441666666667`) y el servidor responde `'lat' fuera del rango [-90, 90]` | El JSON se está construyendo con la acción **`Diccionario`** y sus campos de tipo `Número`. Ese tipo reinterpreta el valor con la configuración regional y **se come el separador decimal, tanto la coma como el punto** (lee el punto como separador de miles) | Construye el cuerpo con una acción **`Texto`**, no con `Diccionario`: ahí lo que escribes es lo que se envía. Es lo que hace el atajo de telemetría. Receta completa en [`atajo-fotos.md`](atajo-fotos.md), trampa 1. Ojo: **no siempre falla ruidosamente** — una longitud `-0,5` convertida en `-5` es válida y se guardaría mal en silencio |
| **El atajo dice `guardados: N` pero las fotos salen en mitad del Atlántico** | Llegó `lat: 0, lon: 0`, que es el **golfo de Guinea** y una coordenada perfectamente válida. Casi siempre es un chip de variable **en rojo** en el atajo: una referencia rota devuelve **vacío**, no error, y un vacío en un campo `Número` se convierte en `0` | En el atajo, ningún chip en rojo y ningún `Definir variable` con el nombre en gris (esos no hacen nada). Comprueba el JSON **antes** de enviarlo con un `Mostrar aviso` encima del envío: tiene *Cancelar*, así que puedes mirar y no mandar. Para limpiar lo ya guardado, `python tools/importar_fotos.py --limpiar` **en el servidor** |
| **Se arregla el atajo y las fotos siguen mal en el mapa** | La deduplicación va por nombre de archivo: el servidor ve que `IMG_4638` ya existe, responde `duplicado` y **descarta la versión buena**. La fila mala se queda para siempre | No hay forma de corregir un punto reenviándolo. `python tools/importar_fotos.py --limpiar` en una consola del servidor —vacía **todos** los puntos, no toca notas ni telemetría— y vuelve a ejecutar el atajo |
| **La segunda ejecución del atajo de fotos dice `guardados: 4` otra vez** (debería decir `duplicados: 4`) | Los nombres de archivo no salen iguales entre ejecuciones, así que no hay deduplicación posible | Pon un `Mostrar aviso` con el cuerpo antes del envío y compara los nombres entre dos ejecuciones. **Antes de dar esto por cierto, míralo dos veces:** Atajos saca las claves en **orden alfabético** (`errores, descartados, duplicados, guardados`) y es facilísimo leerlas cruzadas. La comprobación que no se puede leer mal es abrir `/mapa` y ver si el número de fotos crece |
| **Los HEIC no dan metadatos y los JPEG sí** | El lector recorre bien la estructura de un JPEG; en un HEIC busca el bloque EXIF de forma aproximada y puede no encontrarlo | Exporta como JPEG (iPhone: Ajustes → Cámara → Formatos → *Más compatible*, o al compartir elige convertir). El informe dice cuántas fotos hay de cada formato, así que se ve enseguida si el problema es ese |
| **Los datos son de otro sitio** | Coordenadas malas, o caché de una ubicación anterior | La tarjeta de ubicación enseña tus coordenadas: contrástalas. La caché redondea a ~110 m, así que a menos de eso es el mismo sitio a efectos de la app |

---

## El caso especial del plan gratuito: el proxy

Las cuentas gratuitas de PythonAnywhere sacan **todo** el tráfico saliente por un
proxy con **lista blanca de dominios**. Esto merece sección propia porque no se
parece a un fallo de red:

- No es un timeout ni un "connection refused".
- Es un **403 con cuerpo HTML** que devuelve el proxy, no la API.
- La app lo interpreta como "esta fuente ha fallado" y **degrada en silencio**:
  la app parece funcionar, solo que sin tiempo, sin POIs o sin IA, con su aviso.

Hosts que necesita la app:

| Host | Sin él… |
|---|---|
| `nominatim.openstreetmap.org` | **La app no funciona.** Es la única fuente imprescindible |
| `api.open-meteo.com` · `marine-api.open-meteo.com` | Sin tiempo ni oleaje |
| `overpass-api.de` · `overpass.kumi.systems` · `overpass.private.coffee` | Sin puntos de interés verificados; el modelo tira de conocimiento general |
| `generativelanguage.googleapis.com` | Sin recomendaciones (Gemini) |
| `api.anthropic.com` | Solo si cambias a `LLM_PROVIDER=anthropic` |
| `api.moonshot.ai` | Solo si cambias a `LLM_PROVIDER=kimi` |

`api.anthropic.com`, `api.moonshot.ai` y `api.moonshot.cn` están **confirmados**
en la lista blanca (verificado sobre la página de la lista, no preguntando). Los
demás hay que comprobarlos con el diagnóstico en el servidor.

**Cómo confirmarlo** desde una consola Bash del servidor:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://api.open-meteo.com/v1/forecast
```

Un `403` donde el mismo comando en tu portátil da `400` o `200` es el proxy. Se
solicita añadir un dominio en el foro de PythonAnywhere.

---

## Antes de dar nada por roto

1. **¿Pulsaste *Reload*?** Los cambios en el `.env` y en el código no surten
   efecto hasta entonces. Es el fallo más frecuente, con diferencia.
2. **¿Estás en HTTPS?** Explica el bucle de login y el GPS mudo a la vez.
3. **Corre el diagnóstico en el servidor**, no en el portátil. La mitad de los
   problemas de despliegue son cosas que en tu máquina funcionan.

## Si nada de esto encaja

Activa temporalmente el detalle de errores en el `.env` del servidor:

```bash
SHOW_AI_ERROR_DETAIL=1
```

y pulsa *Reload*. La interfaz pasará a enseñar el mensaje crudo del proveedor en
vez de uno genérico. **Desactívalo cuando termines**: expone detalles de
infraestructura a cualquiera que abra la app.

La API key **nunca** aparece en un mensaje de error, con esto activado o
desactivado; de eso se encarga `llm_providers.redact()`, no este interruptor.
