# El atajo de las fotos: que el mapa se dibuje solo

Cómo hacer que el iPhone mande cada día **dónde y cuándo** se hicieron las
fotos, sin subir ninguna foto y sin tocar nada.

Es el hermano del atajo de la telemetría ([`atajo-iphone.md`](atajo-iphone.md)):
mismo endpoint con token, mismo token, misma idea de ventana solapada. Si ya
montaste aquel, este es más corto.

> **Estado honesto de este documento.** El servidor está probado de extremo a
> extremo por HTTP real, y el lector de EXIF está probado contra una foto real
> de iPhone (ver §1). **El atajo en sí todavía no lo ha montado nadie**, así
> que los nombres exactos de algunas acciones pueden variar según la versión de
> iOS. Cuando lo montes y algo no encaje, corrige aquí lo que veas en pantalla:
> eso es exactamente lo que pasó con el atajo de la telemetría, y las cuatro
> trampas que salieron están al final.

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
  borras su punto: `python tools/importar_fotos.py --limpiar` y reimportas, o
  se queda hasta que limpies. Con el carrete entero no habría nada que decidir.

> **Nota sobre permisos y automatización.** Con acceso *limitado*, iOS solo deja
> ver las fotos que autorizaste una vez, así que una automatización diaria
> acabaría sin ver las nuevas del álbum. Si la quieres desatendida, hace falta
> acceso completo — y el álbum sigue haciendo su trabajo, porque el filtro lo
> aplica el atajo. Si prefieres no dar acceso completo, usa la variante de la
> **hoja de compartir** (§8): no pide ningún permiso de Fotos.

### Y el cable, ¿para qué queda?

|  | Atajo + álbum | Cable + `tools/importar_fotos.py` |
|---|---|---|
| Cuándo | Cada día, solo | Una vez, para el volcado gordo |
| Para qué | Mantener el viaje al día | Meter años anteriores de golpe |
| Velocidad | Segundos (pocas fotos) | Mil fotos en segundos |
| Permisos | Fotos | Ninguno |

Los dos escriben en el mismo sitio y ninguno duplica al otro: la clave es el
nombre del archivo, así que da igual que una foto entre por los dos caminos.

---

## 3. Lo que tiene que acabar enviando

```jsonc
// POST https://TU_USUARIO.pythonanywhere.com/api/waypoints
// Authorization: Bearer <el MISMO token del atajo de telemetría>
{
  "fuente": "fotos",
  "puntos": [
    { "archivo": "IMG_4736.jpeg",             // el nombre. Es la clave anti-duplicados
      "capturado_en": "2026-07-26T14:23:37",  // hora de la cámara. Con "+02:00" también vale
      "lat": 38.1764611,
      "lon": -0.8707361,
      "altitud": 12.9 }
  ]
}
```

Tres cosas que no son evidentes:

- **`capturado_en` es la hora que marcaba el reloj de la cámara**, no un
  instante en UTC. Al revés que `medido_en` en la telemetría, aquí no se
  canoniza: ponerle una zona que no está en el EXIF sería inventarse la hora.
  Si la mandas con huso (`...+02:00`, que es lo que devuelve Atajos), el
  servidor lo **separa**: la hora va a `capturado_en` y el desfase a
  `offset_original`. No hay que quitarlo a mano.
- **`lat`/`lon` son opcionales**, pero o van las dos o ninguna. Una foto sin
  ubicación se guarda igual: ordena el relato del viaje aunque no ponga una
  chincheta.
- **`archivo` tiene que ser un nombre, no una ruta.** Con barras el servidor lo
  rechaza.

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

## 4. El atajo, bloque a bloque

Atajos → **+** → nombre: `Enviar fotos del viaje`.

Antes: en la app **Fotos** → pestaña *Álbumes* → **+** → *Álbum nuevo* →
nómbralo **`Viaje`**. Ahí vas metiendo las que quieras que cuenten.

### Bloque A — Buscar las fotos del álbum

1. **Buscar fotos**
   - Filtro: **Álbum** — *es* — **`Viaje`**
   - Ordenar por *Fecha de captura*, ascendente
   - **Límite: 300 elementos.** No es capricho: el cuerpo de la petición no
     puede pasar de los 128 KB que acepta el servidor, y si te pasas llega un
     413 y no se guarda nada. Con un álbum curado no lo vas a rozar.

   > **Sin segundo filtro por fecha, y es deliberado.** Podrías añadir *"y la
   > fecha está en los últimos 3 días"* para acortar el bucle, pero entonces
   > una foto que metas al álbum **una semana después** no entraría nunca:
   > justo el caso normal cuando ordenas las fotos al volver del viaje. Mandar
   > el álbum entero cada vez no cuesta nada porque el servidor deduplica por
   > nombre de archivo, y a cambio el álbum siempre acaba entero en el mapa,
   > metas lo que metas y cuando lo metas.

2. **Definir variable** → `puntos` → dejarla **vacía** (será la lista).

### Bloque B — Sacar los metadatos de cada una

3. **Repetir con cada elemento** de *Fotos*. Dentro del bucle:

4. **Obtener detalles de imágenes** → *Nombre de archivo* → variable `nombre`
5. **Obtener detalles de imágenes** → *Fecha de captura* → variable `cuando`
6. **Obtener detalles de imágenes** → *Ubicación* → variable `sitio`
7. **Obtener detalles de ubicación** (sobre `sitio`) → *Latitud* → `lat`
8. Igual con *Longitud* → `lon`, y *Altitud* → `alt`

> Si tu versión de iOS ofrece *Latitud* directamente en **Obtener detalles de
> imágenes**, sáltate los pasos 6-8 y úsalo: son tres bloques menos.

9. **Diccionario** con cuatro o cinco claves:

   | Clave | Valor |
   |---|---|
   | `archivo` | variable `nombre` |
   | `capturado_en` | variable `cuando`, **con el chip en ISO 8601** (ver trampas) |
   | `lat` | variable `lat` |
   | `lon` | variable `lon` |
   | `altitud` | variable `alt` |

   **Usa la acción Diccionario, no escribas el JSON en un bloque de Texto.** Es
   la trampa nº 1 del otro atajo: en un iPhone en español, `38.1764` se escribe
   como texto `38,1764`, y eso rompe el JSON entero.

10. **Añadir a variable** → el diccionario → a `puntos`

### Bloque C — Enviar

11. **Diccionario**:
    - `fuente` → texto `fotos`
    - `puntos` → variable `puntos`

12. **Obtener contenido de la URL**
    - URL: `https://TU_USUARIO.pythonanywhere.com/api/waypoints`
    - Método: **POST**
    - Cabeceras: `Authorization` → `Bearer <token>`
      *(el mismo del atajo de telemetría; ojo al pegarlo, ver trampas)*
    - Cuerpo de la solicitud: **JSON**, y dentro el diccionario del paso 11

13. **Mostrar alerta** con el resultado — **solo mientras lo pruebas**. Quítalo
    antes de automatizarlo o te saltará una alerta cada día.

---

## 5. Probarlo

Ejecuta el atajo a mano. La respuesta buena tiene esta forma:

```json
{"guardados": 12, "duplicados": 0, "descartados": 0, "errores": []}
```

Y al ejecutarlo **otra vez seguida**, esta:

```json
{"guardados": 0, "duplicados": 12, "descartados": 0, "errores": []}
```

**Esa segunda respuesta es la prueba que importa.** Significa que reenviar no
duplica, que es lo que permite mandar los últimos 3 días cada día sin ensuciar
el mapa.

Si sale `descartados` alto, el campo `errores` dice qué falla y en qué punto.
Y desde el servidor:

```bash
python tools/diagnostico.py     # línea "puntos de las fotos"
```

---

## 6. Trampas heredadas del otro atajo

Estas cuatro salieron montando el de la telemetría y **aplican igual aquí**.
Están explicadas a fondo en [`atajo-iphone.md`](atajo-iphone.md); el resumen:

1. **Los decimales salen con coma.** `38,1764` rompe el JSON. Se evita usando
   la acción **Diccionario** en vez de escribir el JSON en un **Texto**. Si aun
   así usas Texto, hace falta un *Reemplazar texto* (`,` → `.`) sobre **cada
   número por separado**, nunca sobre el JSON entero.

2. **La fecha se formatea en el propio chip.** Inserta la variable, **toca el
   chip** y elige *Formato de fecha → ISO 8601* con *Incluir hora*. No hacen
   falta bloques aparte de `Aplicar formato` y `Definir variable`: al reordenar
   acciones, un `Definir variable` puede quedar apuntando a nada y entonces la
   fecha **se envía vacía** sin que Atajos avise.

   Aquí **no** hay que pelearse con el huso: Atajos devuelve
   `2026-07-26T14:23:37+02:00` y el servidor lo separa solo — la hora local va
   a `capturado_en` y el `+02:00` a su columna. Mándalo tal cual.

3. **El teclado mete tildes y dobles puntos.** `"lat:"` con dos puntos dentro es
   JSON válido, así que el servidor guardaría el punto **sin ubicación** y sin
   protestar. No se detecta mirando si hubo error: se detecta mirando lo que
   llegó.

4. **Al pegar el token, que quede una sola línea corta.** Si copias de más, la
   cabecera se pasa de 8 KB y el proxy de PythonAnywhere devuelve un 400 con
   HTML de `openresty` que ni llega a la app.

---

## 7. Convertirlo en automatización

Atajos → **Automatización** → **+** → **Hora del día**.

- [ ] Una vez al día basta, a una hora en la que el móvil suela tener wifi
      (por la noche, en el camping). Las fotos no se van a ninguna parte, y la
      ventana de 3 días cubre los fallos.
- [ ] Acción: **Ejecutar atajo** → `Enviar fotos del viaje`
- [ ] **"Preguntar antes de ejecutar": DESACTIVADO**
- [ ] Quita el *Mostrar alerta* del paso 13

Permiso que hay que conceder una vez: Atajos pedirá acceso a **Fotos**. Sin él
el bucle sale vacío y el envío da 400 (`'puntos' está vacío`), que es
justamente lo que tiene que pasar para que te enteres.

---

## 8. Variante sin permisos: la hoja de compartir

Si prefieres no dar acceso a Fotos, hay una versión que **no pide ninguno**:
en vez de buscar el álbum, el atajo recibe las fotos que le compartes.

Cambios respecto al de arriba:

- En los ajustes del atajo (botón ⓘ), activa **Mostrar en hoja de compartir** y
  acepta como entrada **Imágenes**.
- **Borra el Bloque A entero.** El bucle del paso 3 se hace sobre *Entrada del
  atajo* en vez de sobre *Fotos*.
- El resto es idéntico.

Cómo se usa: en Fotos seleccionas las que quieras → **Compartir** → `Enviar
fotos del viaje`. Se puede hacer con 30 de golpe.

|  | Álbum + automatización | Hoja de compartir |
|---|---|---|
| Permisos de Fotos | Sí (completo si es desatendida) | **Ninguno** |
| Cuándo se manda | Solo, cada día | Cuando tú lo compartes |
| Riesgo | Olvidarte de meterlas al álbum | Olvidarte de compartirlas |

Las dos escriben en el mismo sitio y no se estorban: puedes tener las dos
montadas. Y no duplican nada aunque una foto se mande por los dos caminos,
porque la clave es el nombre del archivo.
