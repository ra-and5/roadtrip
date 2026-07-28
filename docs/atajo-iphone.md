# El atajo del iPhone que envía la telemetría

Receta para construir en la app **Atajos** la automatización que manda pasos,
ubicación y batería a `/api/telemetria`.

> ## ⚠️ Dos avisos antes de empezar
>
> **1. Qué está probado en un iPhone de verdad y qué no.** Conviene saberlo
> antes de dar por buena una instrucción:
>
> - ✅ **Probado el 27-07-2026 en un iPhone real**: el envío completo (URL,
>   método POST, las dos cabeceras, cuerpo como *Archivo* desde una acción
>   *Texto*) contra el servidor desplegado; la **idempotencia** —el segundo
>   envío devolvió `{"guardadas":0,"duplicadas":1}`, que es justo lo que tiene
>   que pasar—; y una muestra con **fecha y batería reales** guardada desde el
>   móvil. De ahí salen las cuatro trampas documentadas al final de la §4.
> - ❌ **Sin probar**: el bloque de pasos de Salud (el bucle de la ventana
>   solapada), el envío de ubicación —se aparcó, ver la trampa 1— y las
>   automatizaciones por hora. Esos pasos están escritos a partir de cómo
>   funciona Atajos, no de haberlos ejecutado.
>
> Los nombres de las acciones cambian entre versiones de iOS y entre idiomas,
> así que espera tener que buscar alguna por un nombre parecido. Lo que no
> cambia es el JSON que hay que acabar enviando, que está más abajo y es el
> contrato de verdad.
>
> **2. El token queda guardado EN CLARO dentro del atajo.** Cualquiera que abra
> el atajo lo ve. Consecuencias prácticas:
>
> - **No compartas este atajo con nadie.** Al compartir un atajo se comparte
>   todo lo que lleva escrito dentro, incluido el token.
> - No lo publiques en iCloud ni en una galería.
> - No uses aquí la contraseña de la app (`tools/token_ingesta.py` genera un
>   secreto distinto justo por esto).
> - Si pierdes el móvil: regenera el token con `python tools/token_ingesta.py`,
>   actualiza el `.env` del servidor, pulsa *Reload*, y el atajo viejo queda
>   muerto.

---

## 1. Qué tiene que acabar enviando

Todo lo demás son medios para llegar aquí:

```http
POST https://d10sdrebrasov.pythonanywhere.com/api/telemetria
Authorization: Bearer <token de tools/token_ingesta.py>
Content-Type: application/json
```

```json
{
  "fuente": "atajos-iphone",
  "muestras": [
    {"medido_en": "2026-07-27T09:00:00+02:00", "pasos": 512},
    {"medido_en": "2026-07-27T10:00:00+02:00", "pasos": 1340},
    {"medido_en": "2026-07-27T11:00:00+02:00", "pasos": 890},
    {"medido_en": "2026-07-27T12:00:00+02:00", "pasos": 1204},
    {"medido_en": "2026-07-27T13:07:41+02:00", "bateria": 78,
     "lat": 43.5622, "lon": -6.1456}
  ]
}
```

Reglas que el servidor hace cumplir (todas dan un mensaje diciendo qué campo
falla, menos el 401):

| Campo | Regla |
|---|---|
| `medido_en` | Obligatorio. ISO 8601 **con zona horaria**. Entre 30 días atrás y 24 h adelante |
| `pasos` | Opcional. Entero ≥ 0 |
| `bateria` | Opcional. Entero **0-100** (porcentaje, no fracción) |
| `lat` / `lon` | Opcionales, pero **o las dos o ninguna**. [-90, 90] y [-180, 180] |
| — | Cada muestra necesita **al menos un dato** además de la fecha |
| `fuente` | Opcional. Solo se admite `atajos-iphone` |

---

## 2. Por qué el cuerpo lleva varias muestras

Es el diseño central de esta fase (decisión 23 de `CLAUDE.md`), y si no se
entiende, el atajo se construye mal.

En un camper por el norte de España la cobertura va y viene. Si un envío falla
por falta de señal, esa muestra no se puede perder. La solución **no** es una
cola en el iPhone: es que **cada envío incluya las últimas horas**, no solo la
actual. Con envíos cada hora y una ventana de 6 h harían falta seis fallos
seguidos para perder algo, y al volver la cobertura el sistema se cura solo,
sin nada que sincronizar.

Eso significa que **mandar la misma muestra muchas veces es lo normal**. El
servidor es idempotente: se queda con la primera y cuenta las demás como
duplicadas. Una respuesta como

```json
{"guardadas": 1, "duplicadas": 4, "descartadas": 0, "errores": []}
```

no es un aviso de nada: es exactamente lo que tiene que salir.

**Consecuencia para construir el atajo:** las muestras de una misma hora, en
envíos distintos, tienen que llevar **el mismo `medido_en`**. Por eso los pasos
se fechan **al inicio de cada hora en punto** y no "cuando se ejecutó el
atajo". Si cada envío fechara la misma hora de forma distinta, no habría
deduplicación que valga y la tabla se llenaría de copias.

### Lo que sí es retroactivo y lo que no

Esta es la asimetría que da forma al atajo:

- **Pasos: sí.** Salud guarda el histórico, así que se pueden consultar horas
  pasadas. Son los que forman la ventana solapada.
- **Batería y ubicación: no.** No existe "¿dónde estaba y cuánta batería tenía
  hace tres horas?". Solo se puede medir el instante actual.

Por eso el lote tiene dos partes: N muestras de pasos con hora en punto, y una
sola muestra "de ahora" con batería y ubicación. Y por eso el servidor acepta
muestras con solo pasos o solo batería, sin exigir las tres cosas.

Y por eso los pasos que se envían son los de **horas ya terminadas**: si se
enviara la hora en curso a medias, ese recuento parcial quedaría fijado para
siempre (el reenvío posterior, ya completo, se descartaría como duplicado). Es
la trampa menos evidente de todo esto.

---

## 3. Antes de tocar Atajos

- [ ] La app está desplegada y responde:
      `curl https://d10sdrebrasov.pythonanywhere.com/healthz`
- [ ] Token generado en el servidor y `.env` actualizado:
      ```bash
      cd ~/roadtrip && python tools/token_ingesta.py
      # INGEST_TOKEN_HASH=... -> al .env    |    Bearer ... -> al atajo
      nano ~/roadtrip/.env
      ```
- [ ] **Reload** pulsado en la pestaña *Web* de PythonAnywhere
- [ ] Comprobado desde el servidor:
      ```bash
      python tools/diagnostico.py    # INGEST_TOKEN_HASH: configurado
      ```

---

## 4. El atajo, acción por acción

Atajos → **+** → nombre: `Enviar telemetría`.

### Bloque A — Preparar

1. **Obtener nivel de batería.**
2. **Obtener ubicación actual** → **Obtener `Latitud` de Ubicación actual**
   → *Definir variable* `LAT`.
3. **Obtener `Longitud` de Ubicación actual** → *Definir variable* `LON`.
4. **Reemplazar** `,` por `.` en `LAT` → *Definir variable* `LAT` a *Texto
   actualizado*. **Y lo mismo con `LON`.**

   No es opcional ni cosmético: en un iPhone en español los decimales salen con
   **coma** (`38,39099`), y eso rompe el JSON. Es la primera trampa de §*Trampas
   comprobadas* y la que más tiempo costó.

### Bloque B — Los pasos del día

> **Esto NO es la ventana solapada, y es a propósito.** El diseño original
> (decisión 23) pedía un bucle que reenviara las últimas N horas, cada una con
> su hora en punto. En Atajos eso son unas doce acciones montadas a mano en la
> pantalla de un móvil, y cada una es un sitio donde equivocarse. Se cambió por
> una sola consulta de las últimas 24 h (**decisión 25**), y **no pierde la
> propiedad que importaba**: un acumulado es un contador monótono, así que si
> falla un envío, el siguiente ya trae el total incluyendo lo que se perdió. Se
> cura solo por construcción, pero sin bucle y sin estado.
>
> Lo que sí se pierde, dicho claro: el desglose por horas. Sabrás cuánto
> anduviste el día, no a qué hora. Para "¿cuánto he andado hoy?" da igual; para
> "¿a qué hora camino más?" no. Si algún día hace falta esa granularidad hay que
> montar el bucle — y entonces hay que decidir qué significa `pasos`, porque
> mezclar cubos horarios y acumulados en la misma columna daría análisis
> silenciosamente equivocados.

5. **Buscar muestras de salud** *donde todas las condiciones sean verdaderas*:
   - Tipo **es** `Steps`
   - `Fecha de inicio` **es hoy**  ← **esta opción y ninguna otra**
   - Unidad: **contar** · Agrupar por: Ninguno · Ordenar por: Ninguno
   - **Límite: desactivado.** Con el límite puesto te devuelve solo las primeras
     muestras y el total sale corto **sin dar ningún error**.
6. **Obtener `Valor` de Muestras médicas.**
7. **Calcular `Suma` de Valor.**
8. **Redondear** la suma a **Número entero** → *Definir variable* `PASOS`.

   Salud devuelve las muestras con decimales y el servidor espera un entero.

> **La trampa que costó una hora, y la lección de fondo.**
>
> El filtro que trae la receta original es `está entre los últimos 1 día`, y
> **no es lo que parece**: son las últimas 24 horas MÓVILES, no "hoy". Se ve en
> los propios datos — una muestra de las 02:48 de la madrugada traía 12.427
> pasos, que evidentemente no se han dado "hoy": arrastraba toda la tarde
> anterior. Es un número que no sirve ni para el día ni para el viaje, y **no da
> ningún error**: solo produce medias infladas y comparaciones falsas si algún
> día lo analizas creyendo que son días.
>
> El arreglo bueno es `es hoy`, que Atajos ya trae hecho. Antes de dar con él se
> montó un rodeo de tres acciones (`Fecha actual` → `Obtener inicio del día` →
> `INICIO_DIA`) y un filtro `está entre` con dos fechas, que **devolvía cero
> muestras** y dejaba `pasos` vacío. Toda esa hora se habría ahorrado mirando
> primero qué opciones ofrece el desplegable del filtro: `es el`, `es hoy`,
> `está entre`, `está entre los últimos`.
>
> **La regla del proyecto ("verificar, no suponer") también aplica a Atajos, no
> solo a las APIs.** Antes de diseñar encima de una herramienta, hay que mirar
> qué sabe hacer.

### Bloque C — La muestra de ahora (batería y ubicación)

> La batería y la ubicación ya se han cogido en el bloque A, que es donde las
> tiene el atajo real. Lo que queda aquí es el montaje del JSON.

**Esta parte está construida y funcionando en un iPhone real (28-07-2026).** Lo
que sigue es la configuración exacta que envía bien, no una propuesta. Es más
larga de lo que parecería necesario, y cada rodeo evita una de las trampas de
más arriba.

15. **Obtener el nivel de batería**. No hace falta guardarla en una variable:
    se inserta directamente en el texto del paso 21. Cuantos menos
    `Definir variable` haya, menos cadenas que se rompan al reordenar acciones
    (que es como se acabó enviando `"bateria":` sin valor y como se envió la
    fecha vacía).
    ⚠️ **Comprueba qué devuelve tu iOS**: hace falta un entero de 0 a 100.
    Verificado que devuelve `20` (no `0,20`), pero si vieras la fracción, mete
    un **Calcular** ×100 y un **Redondear**. El servidor rechaza `0,20` con el
    mensaje `'bateria' tiene que ser un entero`: se entera uno, no se guarda mal
    en silencio.
16. **Obtener la ubicación actual**.
17. **Obtener detalles de la ubicación** → detalle `Latitud`, entrada
    `Ubicación actual` → *Definir variable* `LAT`.
18. **Obtener detalles de la ubicación** → detalle `Longitud`, entrada
    `Ubicación actual` → *Definir variable* `LON`.
    ⚠️ Al añadir el segundo, su salida se llama igual que la del primero
    (*Detalles de Ubicaciones*). Si el `Definir variable` se engancha al bloque
    equivocado, **`LON` acaba valiendo lo mismo que `LAT`** — y `43,5` es una
    longitud perfectamente válida, así que se guarda sin protestar. Pasó.
19. **Reemplazar texto**: buscar `,`, reemplazar por `.`, en `LAT`
    → *Definir variable* `LAT` (a *Texto actualizado*).
20. Lo mismo para `LON`.
    Los pasos 19 y 20 son la trampa 1: con el iPhone en español, `38.3906` se
    convierte en texto como `38,3906` y rompe el JSON. El reemplazo se hace
    sobre **cada número por separado**; sobre el JSON entero se cargaría las
    comas que separan campos.
21. **Texto**, con este contenido exacto. Lo que va entre `«»` son variables
    insertadas desde la barra del teclado, no texto escrito:

    ```
    {"muestras":[{"medido_en":"«Fecha actual»","bateria":«Nivel de batería»,"lat":«LAT»,"lon":«LON»}]}
    ```

    - `«Fecha actual»` es la **variable especial** del sistema, no una acción.
      Tócala una vez insertada y elige **Formato de fecha: ISO 8601** con
      **Incluir hora** activado. Así el formato vive en el mismo sitio donde se
      usa y no hay cadena de tres acciones que se rompa.
    - La fecha va **entre comillas** (es texto); los tres números, **sin**.
    - Sin tildes en las claves (`bateria`, no `batería`) y sin dos puntos de
      más dentro de las comillas (`"lat":`, no `"lat:":`).

Con esto el cuerpo es una sola muestra. El bloque B (los pasos de la ventana
solapada) añade las suyas a la misma lista; mientras no esté montado, este
bloque C funciona solo y ya cumple para comprobar que la ingesta es fiable.

### Bloque D — Enviar

22. **Obtener contenido de la URL**:
    - URL: `https://d10sdrebrasov.pythonanywhere.com/api/telemetria`
    - Método: **POST**
    - **Cabeceras**:
      | Clave | Valor |
      |---|---|
      | `Authorization` | `Bearer eyJ...` (lo que imprimió `token_ingesta.py`) |
      | `Content-Type` | `application/json` |
    - **Cuerpo de la petición: Archivo** → y como archivo, la variable del
      bloque **Texto** del paso 21.

    Esta combinación (*Archivo* + `Content-Type` a mano) es la verificada
    funcionando. La alternativa teórica —*Cuerpo: JSON* con un campo `muestras`
    de tipo **Matriz** apuntando a una variable de lista— no se ha llegado a
    probar; si algún día se monta el bloque B con su bucle, habrá que
    comprobarla entonces.

23. **Mostrar** → *Contenido de URL*.
    **Solo mientras pruebas.** En cuanto pases a automatización hay que
    borrarla: una acción que enseña algo por pantalla convierte cada ejecución
    en una interrupción, y en un envío cada 4 horas eso son seis interrupciones
    diarias por un dato que no vas a mirar. Para comprobar qué se guardó está
    `python tools/ver_telemetria.py` desde la consola, que además es la única
    forma de ver si hay huecos.

---

### Trampas comprobadas montándolo de verdad

Las cuatro salieron la primera noche (27-07-2026) y ninguna es evidente. Si
construyes el JSON a mano con una acción **Texto** en vez de con
**Diccionario**, te vas a encontrar con las dos primeras seguro.

1. **Los decimales salen con coma.** Con el iPhone en español, una latitud
   `43.5622` se convierte en texto como `43,5622`, y eso rompe el JSON: para el
   parser, `"lat":43,5622` es un `43`, una coma de separación y un `5622` suelto
   donde debería ir una clave. El síntoma es un 400 diciendo que el cuerpo no es
   un objeto JSON. Por eso conviene la acción **Diccionario**, que no pasa los
   números por texto. Si aun así usas Texto, hace falta un **Reemplazar texto**
   (`,` → `.`) sobre **cada número por separado** — nunca sobre el JSON entero,
   donde las comas separan campos.

2. **La fecha se formatea en el propio chip, sin bloques aparte.** No hacen falta
   `Fecha actual` + `Aplicar formato` + `Definir variable`: basta insertar la
   variable especial **Fecha actual** dentro del texto, **tocar el chip** y
   elegir *Formato de fecha → ISO 8601* con *Incluir hora* activado. Tres
   bloques menos y, sobre todo, una cadena menos que se rompa: al reordenar
   acciones, un `Definir variable` puede quedar apuntando a nada y entonces la
   fecha **se envía vacía**, sin ningún aviso en Atajos. El servidor sí lo dice
   (`falta 'medido_en'`), y por eso el mensaje nombra el campo.

3. **El teclado del iPhone mete tildes y dobles.** `"batería"` en vez de
   `"bateria"`, o `"lat:":` en vez de `"lat":`. Esta segunda es la peligrosa:
   `{"lat:": 43.5}` es JSON **válido** —solo que la clave se llama `lat:`—, así
   que el servidor responde `guardadas: 1` tan contento y guarda la ubicación
   como `NULL`. Un fallo mudo de manual: no se detecta mirando si hubo error,
   sino mirando lo que llegó, con `tools/ver_telemetria.py`.

4. **Cuidado al pegar el token en la cabecera.** Ver la fila de `openresty` en
   la tabla de abajo.

## 5. Probarlo

Pulsa ▶︎ con el móvil en wifi.

**Lo que debes ver la primera vez:**

```json
{"guardadas": 7, "duplicadas": 0, "descartadas": 0, "errores": []}
```

**Púlsalo otra vez, sin esperar.** Ahora tiene que salir algo así:

```json
{"guardadas": 1, "duplicadas": 6, "descartadas": 0, "errores": []}
```

Las 6 duplicadas son los pasos de las horas ya enviadas y la 1 guardada es la
muestra de "ahora" (que lleva un instante distinto). **Si la segunda ejecución
vuelve a decir `"guardadas": 7`, algo va mal con las fechas**: lo más probable
es que `medido_en` no esté redondeado a la hora en punto (paso 6) o que el
formato de fecha no sea estable. Sin arreglar eso, la tabla se llena de copias.

Y desde una consola del servidor:

```bash
cd ~/roadtrip && python tools/ver_telemetria.py
```

Mira la columna **huso**: tiene que poner `+02:00` (o el que toque), no `UTC`
ni `?`.

### La prueba que de verdad importa

Pon el móvil en **modo avión** un par de horas, quítalo, y ejecuta el atajo.
Tienen que aparecer las muestras de esas horas, con la columna **retraso** de
`ver_telemetria.py` marcando un par de horas. Eso es la ventana solapada
haciendo su trabajo, y es lo único que demuestra que el diseño funciona.

---

## 6. Convertirlo en automatización

Atajos → pestaña **Automatización** → **+** → **Hora del día**.

> **Limitación de iOS que conviene saber antes de pelearse con ella:** las
> automatizaciones de *Hora del día* son **diarias, semanales o mensuales**. No
> existe "cada hora". Para tener envíos horarios hay que crear **una
> automatización por cada hora** apuntando al mismo atajo, lo cual es tedioso.
>
> La buena noticia es que el diseño lo absorbe, aunque **no por la ventana
> solapada**: esa se abandonó (decisión 25) y aquí no hay ninguna `VENTANA` que
> subir. Lo que lo absorbe es que **los pasos son un acumulado de 24 h**, así
> que un envío perdido lo recupera entero el siguiente.
>
> Lo que NO se recupera nunca es lo que se mide en el instante: **la batería y
> la ubicación de ese envío**. Por eso la frecuencia no da igual — no por los
> pasos, sino por las otras dos. Cada 4 horas (seis automatizaciones) es un
> punto de equilibrio razonable entre resolución y tedio de montarlas a mano.

En cada automatización:

- [ ] Hora elegida, **Diariamente**
- [ ] Acción: **Ejecutar atajo** → `Enviar telemetría`
- [ ] **"Preguntar antes de ejecutar": DESACTIVADO.** Si queda activado, la
      automatización solo manda cuando estés mirando el móvil, que es justo
      cuando no hace falta.
- [ ] Quita el *Mostrar alerta* del paso 23, o te saltará una alerta cada vez.

Deja el móvil funcionando un par de días y vuelve a mirar:

```bash
python tools/ver_telemetria.py 50
```

**El criterio para dar la fase por cerrada es ese y solo ese: que lleguen datos
de forma fiable durante varios días.** Mientras eso no esté demostrado, no se
construye nada encima.

---

## 7. Cuando falle

La tabla de síntomas está en [`troubleshooting.md`](troubleshooting.md), filas
del atajo. En resumen:

| Respuesta | Qué pasa |
|---|---|
| **400 con `openresty` en el texto** | **No ha llegado a la app.** Una cabecera pasa de 8 KB y la corta el servidor web de PythonAnywhere. Comprobado el 27-07-2026: es la cabecera `Authorization` con texto de sobra pegado dentro. Tiene que ser una línea corta, `Bearer` + ~43 caracteres |
| **401** | Token. Y el mensaje no dice más **a propósito**: no distingue "falta la cabecera" de "el token es otro". Empieza por `python tools/diagnostico.py` en el servidor |
| **400** | El cuerpo no tiene la forma esperada. El mensaje dice qué campo |
| **405** | Se está usando GET en vez de POST |
| **413** | Cuerpo por encima de `MAX_CONTENT_LENGTH`. Con una muestra por envío no debería pasar nunca; si pasa, mira qué está metiendo el bloque `Texto` |
| **`descartadas` > 0** | Las muestras llegan pero se rechazan. `errores` dice cuál y por qué |
| **`guardadas` siempre 0** | Está funcionando: son duplicadas. Mira `duplicadas` |
| Nada, se queda colgado | La app está caída o no hay cobertura. No pasa nada: el próximo envío recupera estas muestras |

Un truco para depurar sin ordenador: añade temporalmente un **Mostrar alerta**
justo antes del *Obtener contenido de la URL* enseñando la variable `MUESTRAS`.
Ver el JSON que se está construyendo resuelve casi todo.
