# El atajo de las fotos: que el mapa se dibuje solo

Cómo hacer que el iPhone mande cada día **dónde y cuándo** se hicieron las
fotos, sin subir ninguna foto y sin tocar nada.

Es el hermano del atajo de la telemetría ([`atajo-iphone.md`](atajo-iphone.md)):
mismo endpoint con token, mismo token, misma idea de reenviarlo todo cada vez.

> **Estado: montado y funcionando en un iPhone real (28-07-2026).** Lo que hay
> aquí abajo no es una propuesta: es la configuración exacta que envía bien,
> corregida acción por acción contra lo que se veía en la pantalla. La versión
> anterior de este documento estaba escrita a partir de cómo *debería* funcionar
> Atajos, y **casi todo lo que decía del bucle y del diccionario era falso**.
> Las nueve trampas que salieron están en la §6, y ninguna se ve venir.
>
> Los nombres de las acciones cambian entre versiones de iOS y entre idiomas.
> Los de aquí son los de un **iPhone 16 Pro en español**. Lo que no cambia es el
> JSON que hay que acabar enviando (§3), que es el contrato de verdad.

---

## 1. Lo que ya está comprobado

Leído de una foto real hecha con un iPhone 16 Pro (28-07-2026):

| Campo | Valor |
|---|---|
| Fecha | `2026-07-26T14:23:37` |
| Desfase horario | `+02:00` — **el iPhone sí lo escribe** |
| Coordenadas | `38.1764611, -0.8707361` |
| Altitud | 12,9 m |
| Cámara | Apple iPhone 16 Pro |

Y esas coordenadas resuelven a **Albatera, Comunidad Valenciana**, o sea que la
cadena entera funciona: foto → metadatos → punto en el mapa → comunidad
encendida en el tablero.

Lo que **no** sirve, y también está comprobado: **cualquier foto que haya pasado
por WhatsApp**. Se lo quitan todo al comprimir; no queda ni un byte de EXIF.

---

## 2. Un álbum, no el carrete entero

**El atajo mira un álbum concreto, no todas tus fotos.** Creas un álbum
—`Viaje`— y vas metiendo dentro las que quieras que cuenten. Solo esas se leen
y solo esas acaban en el mapa.

Parece una decisión de privacidad y también lo es, pero el motivo principal es
otro: **la curación es el dato**. El carrete de un mes son cientos de fotos, y
la mitad son capturas de pantalla, tickets y fotos de un tornillo de la
furgoneta. Volcarlo entero llenaría el trayecto de puntos que no significan
nada y haría ilegible el relato. Un álbum es una versión del viaje contada por
ti, no un registro de todo lo que disparó la cámara.

Tres consecuencias prácticas, todas buenas:

- **Menos permisos.** Al pedir acceso a Fotos puedes elegir *Seleccionar
  fotos…* en vez de *Permitir acceso a todas*.
- **Bucles cortos.** 20 fotos elegidas frente a 600: el atajo tarda segundos.
- **Se puede corregir.** ¿Metiste una que no querías? La sacas del álbum y
  borras su punto con `python tools/importar_fotos.py --limpiar` y reenvías.

> **Nota sobre permisos y automatización.** Con acceso *limitado*, iOS solo deja
> ver las fotos que autorizaste una vez, así que una automatización diaria
> acabaría sin ver las nuevas del álbum. Si la quieres desatendida, hace falta
> acceso completo — y el álbum sigue haciendo su trabajo, porque el filtro lo
> aplica el atajo. Al ejecutarlo por primera vez iOS pide además permiso para
> **acceder a las fotos en segundo plano**; sin él la automatización no lee nada.

### Los otros dos caminos, que son respaldo y no alternativa

El atajo es la forma de hacer esto. Existen otras dos y están documentadas por
si algún día hacen falta, pero **no son el plan**:

|  | Atajo + álbum | Cable + `tools/importar_fotos.py` |
|---|---|---|
| Cuándo | Cada día, solo | Una vez, para el volcado gordo |
| Para qué | Mantener el viaje al día | Meter años anteriores de golpe |
| Velocidad | Segundos (pocas fotos) | Mil fotos en segundos |
| Permisos | Fotos | Ninguno |

> ⚠️ **Los dos NO deduplican entre sí, al contrario de lo que decía este
> documento.** La clave es el nombre del archivo, y resulta que **Atajos lo
> devuelve sin extensión** (`IMG_4638`) mientras que `importar_fotos.py` lo manda
> con ella (`IMG_4638.HEIC`). Para el servidor son dos archivos distintos, así
> que la misma foto entraría **dos veces** si usas los dos caminos. Dentro de
> cada camino la deduplicación es perfecta. Está apuntado en el roadmap.

---

## 3. Lo que tiene que acabar enviando

```jsonc
// POST https://TU_USUARIO.pythonanywhere.com/api/waypoints
// Authorization: Bearer <el MISMO token del atajo de telemetría>
// Content-Type: application/json
{
  "fuente": "fotos",
  "puntos": [
    { "archivo": "IMG_4638",                  // el nombre. Es la clave anti-duplicados
      "capturado_en": "2026-07-20T15:15:39+02:00",  // hora de la cámara, con huso
      "lat": 38.390445,
      "lon": -0.516225 }
  ]
}
```

Este cuerpo exacto está **probado contra el servidor** (`{"guardados": 4}` la
primera vez, `{"duplicados": 4}` la segunda). Ocupa 475 bytes con cuatro fotos,
de los 128 KB que acepta el servidor.

Cuatro cosas que no son evidentes:

- **`capturado_en` es la hora que marcaba el reloj de la cámara**, no un
  instante en UTC. Al revés que `medido_en` en la telemetría, aquí no se
  canoniza: ponerle una zona que no está en el EXIF sería inventarse la hora.
  Si la mandas con huso (`...+02:00`, que es lo que devuelve Atajos), el
  servidor lo **separa**: la hora va a `capturado_en` y el desfase a
  `offset_original`. No hay que quitarlo a mano.
- **`lat`/`lon` son opcionales**, pero o van las dos o ninguna. Una foto sin
  ubicación se guarda igual: ordena el relato del viaje aunque no ponga una
  chincheta. Si no las tienes, **omite las claves enteras**; mandarlas vacías
  (`"lat": ""`) hace que el punto se descarte.
- **`archivo` tiene que ser un nombre, no una ruta.** Con barras el servidor lo
  rechaza.
- **`altitud` es opcional y aquí no se manda.** Sacarla obliga a un bloque más
  y no la usa ninguna pantalla. Si algún día hace falta, se añade
  `Obtener [Altitud] de SITIO` y su fila en el JSON.

### Por qué se manda el álbum entero cada vez

Es la decisión 23 otra vez, y aquí sale aún más barata. Como la deduplicación
va por **nombre de archivo**, reenviar una foto que ya estaba no cuesta nada:
el servidor responde `duplicados` y no la guarda dos veces. Así que el atajo
manda **el álbum completo** en cada envío, y con eso:

- Un día sin cobertura no pierde nada: al siguiente va todo otra vez.
- Una foto que metas al álbum tres semanas después entra igual.
- No hay ninguna cola que mantener en el móvil, ni estado que sincronizar. El
  móvil no recuerda nada; el servidor es la única fuente de verdad.

En régimen normal, casi todo el envío saldrá como `duplicados`. **Eso no es
ruido: es la señal de que está funcionando.**

---

## 4. El atajo, acción por acción

Atajos → **+** → nombre: `Enviar fotos del viaje`.

Antes: en la app **Fotos** → pestaña *Álbumes* → **+** → *Álbum nuevo* →
nómbralo **`Viaje`**. Ahí vas metiendo las que quieras que cuenten.

### La lista completa

Esta es la configuración verificada. Compárala línea a línea; el orden importa
y hay dos sitios donde equivocarse no da ningún error.

| # | Acción | Campo 1 | Campo 2 | Icono |
|---|---|---|---|---|
| 1 | `Buscar Fotos` | Filtro: `Álbum` — *es/está* — `Viaje` | Ordenar por `Fecha de la foto` · Orden `Más antiguo primero` · Límite `300 ítems` | 🌸 Fotos |
| 2 | `Repetir con cada ítem en` | `Fotos` | — | 🔁 |
| — | **↓ dentro del bucle ↓** | | | |
| 3 | `Obtener [ Nombre ] de` | `Ítem de repetición` | — | 🖼️ azul |
| 4 | `Definir variable` | `NOMBRE` | chip `Nombre` | ✖️ naranja |
| 5 | `Obtener [ Fecha de la foto ] de` | `Ítem de repetición` | — | 🖼️ azul |
| 6 | `Definir variable` | `CUANDO` | chip `Fecha de la foto` | ✖️ naranja |
| 7 | `Obtener [ Ubicación ] de` | `Ítem de repetición` | — | 🖼️ azul |
| 8 | `Definir variable` | `SITIO` | chip `Ubicación` | ✖️ naranja |
| 9 | `Obtener [ Latitud ] de` | `SITIO` | — | 📍 verde |
| 10 | `Definir variable` | `LAT` | chip `Latitud` | ✖️ naranja |
| 11 | `Obtener [ Longitud ] de` | `SITIO` | — | 📍 verde |
| 12 | `Definir variable` | `LON` | chip `Longitud` | ✖️ naranja |
| 13 | `Reemplazar` | `,` por `.` en chip `LAT` | *Expresión regular: NO* | 📝 amarillo |
| 14 | `Definir variable` | `LAT_OK` | chip `Texto actualizado` | ✖️ naranja |
| 15 | `Reemplazar` | `,` por `.` en chip `LON` | *Expresión regular: NO* | 📝 amarillo |
| 16 | `Definir variable` | `LON_OK` | chip `Texto actualizado` | ✖️ naranja |
| 17 | `Texto` | ver abajo | — | 📝 amarillo |
| 18 | `Añadir` | chip `Texto` | a `PUNTOS` | ✖️ naranja |
| 19 | `Terminar repetición` | — | — | 🔁 |
| — | **↑ fin del bucle ↑** | | | |
| 20 | `Combinar` | chip `PUNTOS` | con `Personalizar` → `,` | 📝 amarillo |
| 21 | `Texto` | `{"fuente":"fotos","puntos":[«Texto combinado»]}` | — | 📝 amarillo |
| 22 | `Obtener contenido de la URL` | ver abajo | — | ⬇️ verde |
| 23 | `Mostrar aviso` | chip `Contenido de URL` | — | 🟨 |

**El icono es la comprobación rápida.** Los pasos 3-7 llevan el cuadro azul de
foto (son `Obtener detalles de imágenes`) y los 9-11 llevan la chincheta verde
(son `Obtener detalles de ubicación`). Si alguno lleva el icono que no toca,
está usando la acción equivocada.

### El texto del paso 17

```
{"archivo":"«NOMBRE»","capturado_en":"«CUANDO»","lat":«LAT_OK»,"lon":«LON_OK»}
```

Lo que va entre `«»` son **chips** insertados desde la barra del teclado; el
resto se escribe a mano.

- `NOMBRE` y `CUANDO` van **entre comillas**; `LAT_OK` y `LON_OK` **sin**.
- Toca el chip `CUANDO` → **Formato de fecha: `ISO 8601`** → **`Incluir hora`
  ACTIVADO**. Sin la hora, el servidor rechaza el punto (`'capturado_en' tiene
  que ser 'AAAA-MM-DDTHH:MM:SS'`) y además la línea de tiempo no podría ordenar
  dos fotos del mismo día.
- **Antes de escribirlo:** *Ajustes → General → Teclado → `Puntuación
  inteligente`* **DESACTIVADO**. Si está encendida, el iPhone cambia `"` por
  comillas tipográficas `"` y `"`, y el JSON deja de ser JSON. Las dos comillas
  se parecen tanto que no lo ves mirando la pantalla.

### El envío del paso 22

| Campo | Valor |
|---|---|
| URL | `https://TU_USUARIO.pythonanywhere.com/api/waypoints` |
| Método | **POST** |
| Cabecera 1 | `Content-Type` → `application/json` |
| Cabecera 2 | `Authorization` → `Bearer <token>` |
| Cuerpo de la solicitud | **Archivo** |
| Archivo | chip **`Texto`** (el del paso 21) |

**Cuerpo `Archivo`, no `JSON`.** Es la combinación verificada, la misma que usa
el atajo de telemetría. Con `Cuerpo: JSON` volverías a dejar que Atajos
reinterprete los números, que es exactamente el problema de la trampa 6.

---

## 5. Probarlo

Ejecuta el atajo a mano. La respuesta buena tiene esta forma (ojo, **Atajos
saca las claves en orden alfabético**, no en el que las escribió el servidor):

```json
{"errores":[],"descartados":0,"duplicados":0,"guardados":4}
```

Y al ejecutarlo **otra vez seguida**, esta:

```json
{"errores":[],"descartados":0,"duplicados":4,"guardados":0}
```

**Esa segunda respuesta es la prueba que importa.** Significa que reenviar no
duplica, que es lo que permite mandar el álbum entero cada día sin ensuciar el
mapa.

> Ese orden de claves ya provocó un susto real: leídas cruzadas parecían
> `guardados: 3`. La comprobación que no se puede leer mal es **abrir `/mapa`**:
> tras tres ejecuciones tiene que seguir diciendo el mismo número de fotos.

Si sale `descartados` alto, el campo `errores` dice qué falla y en qué punto.
Y desde el servidor:

```bash
python tools/diagnostico.py     # línea "puntos de las fotos"
```

Si algo entró mal, se vacía y se vuelve a enviar:

```bash
python tools/importar_fotos.py --limpiar   # borra TODOS los puntos, no toca notas
```

Hay que hacerlo desde una consola **del servidor**, no del portátil. Y hace
falta porque **arreglar el atajo no arregla los datos ya guardados**: al
reenviar, el servidor ve que el archivo ya existe y devuelve `duplicado`,
descartando la versión buena. La fila mala se queda.

---

## 6. Las nueve trampas

Todas salieron montándolo el 28-07-2026 y ninguna se ve venir. Las cuatro
primeras son las heredadas del atajo de telemetría; las cinco siguientes son
nuevas y **contradicen lo que decía la versión anterior de este documento**.

### 1. Los decimales y la coma — y por qué el `Diccionario` NO te salva

La versión anterior decía: *"usa la acción **Diccionario**, que no pasa los
números por texto"*. **Es falso.** El campo de tipo `Número` de un `Diccionario`
reinterpreta el valor con la configuración regional, y en un iPhone en español:

| Le metes | Manda |
|---|---|
| `38,176441` (coma decimal) | `38176441` |
| `38.176441` (punto decimal) | `38176441` — lee el punto como separador de **miles** |

O sea que **destruye el decimal de las dos maneras** y no hay forma de arreglarlo
desde dentro del diccionario. Por eso el atajo construye el JSON con una acción
**`Texto`**: ahí lo que escribes es literalmente lo que se envía y nadie
reinterpreta nada.

Al menos falla ruidosamente: el servidor valida `-90 ≤ lat ≤ 90` y devuelve
`'lat' fuera del rango`. Pero cuidado, no siempre — una longitud `-0,5`
convertida en `-5` **es válida** y se guardaría mal en silencio.

Los dos `Reemplazar` (pasos 13-15) se dejan puestos aunque **este iPhone ya
devuelve punto decimal**: hoy no hacen nada, y si algún iOS o idioma devolviera
coma, la arreglarían.

### 2. La fecha se formatea en el propio chip

Inserta la variable en el `Texto`, **toca el chip** y elige *Formato de fecha →
ISO 8601* con ***Incluir hora* activado**. Sin `Incluir hora` sale `2026-07-20`
a secas y el servidor lo rechaza.

No hacen falta bloques aparte de `Aplicar formato` y `Definir variable`: al
reordenar acciones, un `Definir variable` puede quedar apuntando a nada y
entonces la fecha **se envía vacía** sin que Atajos avise.

Aquí **no** hay que pelearse con el huso: Atajos devuelve
`2026-07-20T15:15:39+02:00` y el servidor lo separa solo. Mándalo tal cual.

### 3. El teclado mete tildes y dobles puntos

Pasó, literalmente: la clave salió como **`"capturado en:"`** — con un espacio
en vez del guion bajo **y** dos puntos de más dentro de las comillas.

Y `{"capturado en:": "..."}` es JSON **válido**: solo que la clave se llama así.
El servidor no encuentra `capturado_en`, da la foto por *sin fecha*, y si
además tuviera coordenadas buenas la guardaría **sin fecha ninguna** y
respondería `guardados` tan tranquilo. No se detecta mirando si hubo error: se
detecta mirando lo que llegó.

Los dos puntos **no se escriben**: los pone la acción sola.

### 4. Al pegar el token, que quede una sola línea corta

`Bearer` + ~43 caracteres. Si copias de más, la cabecera se pasa de 8 KB y el
proxy de PythonAnywhere devuelve un **400 con HTML de `openresty`** que ni llega
a la app.

### 5. `Altura` NO es la altitud: son píxeles

`Obtener detalles de imágenes` ofrece `Anchura` y `Altura`, que son las
**dimensiones de la imagen**. `Altura` de una foto de iPhone vale `3024`.

Y aquí está el veneno: el servidor valida `altitud` entre **-500 y 9000 metros**
para cazar EXIF corruptos, así que `3024` **cae dentro**. Habría respondido
`guardados: 4` sin un solo error y el viaje entero habría quedado registrado a
tres mil metros de altitud. Fallo mudo de manual.

### 6. `Latitud` y `Longitud` no existen en `Obtener detalles de imágenes`

La versión anterior las daba como opción directa *"si tu iOS las ofrece"*. **No
las ofrece.** Lo que hay es `Ubicación`, que devuelve un **objeto**, no números.
Hacen falta dos saltos:

```
Obtener [Ubicación] de [Ítem de repetición]   ← detalles de IMÁGENES  (🖼️ azul)
Definir variable SITIO
Obtener [Latitud] de [SITIO]                  ← detalles de UBICACIÓN (📍 verde)
```

Si mandas el objeto `Ubicación` a pelo, Atajos lo convierte a texto (`Calle
Mayor, Albatera, España`) y el servidor responde `'lat' y 'lon' tienen que ser
números`.

### 7. `Ítem de repetición` contra `Resultado de la repetición`

Los dos nombres aparecen juntos en la barra de variables y se parecen muchísimo:

- **`Ítem de repetición`** — la foto de la vuelta actual. Es el que hace falta.
- **`Resultado de la repetición`** — la lista entera que devuelve el bucle **al
  acabar**. Dentro del bucle todavía no existe, y el chip sale en **rojo**.

Relacionado: al añadir acciones nuevas, Atajos las coloca **detrás de
`Terminar repetición`**, o sea fuera del bucle. Para meterlas dentro se arrastra
el `Terminar repetición` hacia abajo. Un bucle vacío no da ningún error: el
atajo se ejecuta, no falla, y manda un envío incompleto.

### 8. Un chip en ROJO es una referencia rota, y no da error

Si borras y vuelves a crear un `Definir variable`, los chips que apuntaban a él
se quedan colgados y Atajos los pinta en **rojo**. La acción sigue ejecutándose:
simplemente recibe **vacío**.

Pasó con `SITIO`, y el efecto fue el peor posible: `LAT` y `LON` vacías → el
campo `Número` del diccionario las convirtió en **`0`** → `lat: 0, lon: 0`, que
es el **golfo de Guinea**, una coordenada perfectamente válida. El servidor
respondió `guardados: 4` sin una sola queja.

Regla: **antes de ejecutar, ningún chip en rojo y ningún `Definir variable` con
el nombre en gris.** Un `Definir variable` sin nombre no hace absolutamente nada
y tampoco avisa.

### 9. El campo de la URL se corrompe, y no se arregla reescribiéndolo

Síntoma: **`400 Bad Request` con `openresty/1.21.4.2`**, incluso con `GET`, sin
cuerpo y sin cabecera `Authorization`. Es decir, la petición **no llega a la
app**: la corta el servidor web de PythonAnywhere por malformada.

La URL se dibujaba partida en tres bloques (`https://` / el dominio / la ruta),
que es la pinta de un campo con saltos de línea dentro. **Vaciar el campo y
reescribirlo no lo arregló.** Lo que funcionó:

1. Abrir la URL en **Safari** (tiene que salir `Method Not Allowed`, que además
   confirma que el móvil llega al servidor).
2. Copiarla de la barra de direcciones, ya normalizada.
3. **Borrar la acción `Obtener contenido de la URL` entera** — no basta con
   vaciar el campo — y añadir una nueva.
4. Pegar ahí.

Truco de diagnóstico que ahorra mucho tiempo: **quita la cabecera
`Authorization` y pon `GET`**. Si aun así sale `openresty`, el problema es la
URL; si sale `{"error":"no_autorizado"}` o un `405`, la petición llega bien y el
problema está en el token o en el cuerpo.

### 10. El envío colocado ANTES del texto: cuerpo vacío, y todo lo demás correcto

La que más ha costado, y estaba escrita aquí como "una de orden, que no es una
trampa". Sí lo es: costó una mañana entera el 29-07-2026.

**Síntoma, y es engañoso en las dos direcciones:**

- El servidor **recibe** la petición y **acepta el token** — o sea, la URL está
  bien, el `Bearer` está bien, y el móvil llega. Todo lo difícil funciona.
- Responde `400`: *el cuerpo tiene que ser `{"fuente": ..., "puntos": [...]}`*.
  Un mensaje correcto y una pista falsa: manda a revisar el JSON, que está
  perfecto.
- Y al ejecutar el atajo **se ve el JSON completo y bien formado**, lo que
  confirma la pista falsa.

**Lo que pasa:** Atajos deja que una acción referencie la salida de otra que va
**más abajo**, sin avisar de nada. Si el `Obtener contenido de la URL` está
colocado antes del `Texto` que arma el cuerpo, al ejecutarse el campo `Archivo`
apunta a algo que todavía no existe, y **sale una petición con el cuerpo vacío**.
El JSON que ves al final es el `Detener y generar`, un cuerpo que nunca se envió.

Orden correcto, y hay que mirarlo aunque todo "parezca" bien:

```
Combinar PUNTOS con ","
Texto: {"fuente":"fotos","puntos":[Texto combinado]}     ← primero el cuerpo
Obtener contenido de .../api/waypoints                   ← DESPUÉS el envío
Detener y generar                                        (opcional, para ver la respuesta)
```

**Cómo se caza en dos minutos**, desde una consola del servidor:

```bash
tail -5 /var/log/TU_USUARIO.pythonanywhere.com.error.log
```

| Lo que sale | Qué significa |
|---|---|
| `Puntos importados: N guardados, M duplicados` | entró; recarga el mapa |
| `Cuerpo recibido (recortado):` **vacío** | esta trampa: el envío va antes del texto |
| `Importación de puntos rechazada: credencial inválida` | el token del atajo no casa con `INGEST_TOKEN_HASH` |
| nada | la petición no sale del móvil: mira la URL (trampa 9) |

Esa tabla se puede leer porque el servidor registra también los **aciertos**.
Antes solo escribía los fallos, así que el silencio no distinguía "no envió" de
"envió y se guardó" — que es lo que convirtió esto en una mañana en vez de dos
ejecuciones.

---

## 7. Convertirlo en automatización

Atajos → **Automatización** → **+** → **Hora del día**.

- [ ] Una vez al día basta, a una hora en la que el móvil suela tener wifi
      (por la noche, en el camping). Las fotos no se van a ninguna parte, y como
      se manda el álbum entero, un día fallido no pierde nada.
- [ ] Acción: **Ejecutar atajo** → `Enviar fotos del viaje`
- [ ] **"Preguntar antes de ejecutar": DESACTIVADO**
- [ ] Quita el `Mostrar aviso` del paso 23, o te saltará una alerta cada día

Permisos que hay que conceder una vez: Atajos pide acceso a **Fotos** y, además,
permiso para **leerlas en segundo plano**. Sin ellos el bucle sale vacío y el
envío da 400 (`'puntos' está vacío`), que es justamente lo que tiene que pasar
para que te enteres.

---

## 8. Variante sin permisos: la hoja de compartir

Si prefieres no dar acceso a Fotos, hay una versión que **no pide ninguno**:
en vez de buscar el álbum, el atajo recibe las fotos que le compartes.

Cambios respecto al de arriba:

- En los ajustes del atajo (botón ⓘ), activa **Mostrar en hoja de compartir** y
  acepta como entrada **Imágenes**.
- **Borra la acción 1 (`Buscar Fotos`).** El bucle del paso 2 se hace sobre
  *Entrada del atajo* en vez de sobre *Fotos*.
- El resto es idéntico.

Cómo se usa: en Fotos seleccionas las que quieras → **Compartir** → `Enviar
fotos del viaje`. Se puede hacer con 30 de golpe.

|  | Álbum + automatización | Hoja de compartir |
|---|---|---|
| Permisos de Fotos | Sí (completo si es desatendida) | **Ninguno** |
| Cuándo se manda | Solo, cada día | Cuando tú lo compartes |
| Riesgo | Olvidarte de meterlas al álbum | Olvidarte de compartirlas |
| Estado | ✅ **Probado** | ⬜ Sin probar |

Las dos escriben en el mismo sitio y no se estorban, y no duplican nada aunque
una foto se mande por los dos caminos, porque la clave es el nombre del archivo.

---

## 9. Respaldo: la carpeta vigilada (en el portátil)

**Esto NO está instalado**, y es a propósito: el camino es el atajo. Queda
escrito por si algún día quieres leer fotos desde el ordenador sin ejecutar
nada — sueltas fotos en una carpeta y se importan solas.

Los archivos están en `tools/systemd/`. La carpeta sería `~/Pictures/viaje`.

Lo dispara una unidad `.path` de systemd, no un temporizador, y la diferencia
importa: un temporizador cada 5 minutos son 288 ejecuciones diarias para una
carpeta que casi siempre está igual, **y tarda hasta 5 minutos en enterarse**.
Esto reacciona al soltar la foto y no consume nada cuando no pasa nada.

### Lo único que falta: el token

```bash
# El token en claro, el mismo del atajo del iPhone
nano ~/.config/roadtrip/fotos.env      # rellena INGEST_TOKEN=
```

Ese archivo vive **fuera del repositorio** a propósito y con permisos `600`:
contiene el token en claro y no puede acabar en git por accidente.

### Comprobar y manejar

```bash
systemctl --user status roadtrip-fotos.path        # ¿está vigilando?
journalctl --user -u roadtrip-fotos.service -n 30  # qué hizo la última vez
systemctl --user disable --now roadtrip-fotos.path # pararlo
```

### Instalarlo

```bash
mkdir -p ~/.config/systemd/user
cp tools/systemd/roadtrip-fotos.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now roadtrip-fotos.path
```

Las rutas están escritas dentro de los dos archivos (`%h/Pictures/viaje` y la
ruta del repo); cámbialas si tu carpeta es otra.

> **Ojo con cómo llegan las fotos a esa carpeta.** Da igual el formato —JPEG o
> HEIC—, pero **no pueden haber pasado por WhatsApp ni Telegram**: esas apps
> borran el EXIF al comprimir y la foto llega muda. Comprobado con archivos
> reales: una foto original del iPhone trae fecha, GPS, altitud y cámara; la
> misma foto reenviada por WhatsApp no trae **nada**. Cable, AirDrop o
> exportar desde Fotos con *Todos los datos de las fotos* activado.
