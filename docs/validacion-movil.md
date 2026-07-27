# Validación desde el iPhone

Seis comprobaciones en orden. Cada una tiene **qué deberías ver** y **qué
significa si no lo ves**. No pases a la siguiente sin la anterior en verde: si
el GPS no da coordenadas, todo lo de abajo falla por el mismo motivo y depurarás
el síntoma equivocado.

Esta es la primera vez que se prueba el GPS de verdad. `navigator.geolocation`
solo funciona bajo HTTPS o en `localhost`, así que en local no había forma de
saber si esto funcionaba.

**Antes de empezar:** ten a mano `https://TU_USUARIO.pythonanywhere.com` y la
pestaña *Web* → *Error log* abierta en el portátil. Usa **Safari**, que es donde
vas a instalar la PWA; Chrome en iOS usa el mismo motor pero gestiona los
permisos por su cuenta.

---

## 1. Login

Abre `https://TU_USUARIO.pythonanywhere.com` (con **https**, y comprueba el
candado).

**Deberías ver:** el formulario de contraseña. Al meterla, la pantalla principal
con el botón grande.

| Si no… | Qué significa |
|---|---|
| Vuelve al login una y otra vez, sin error | *Force HTTPS* está desactivado y entraste por `http://`. La cookie sale marcada `Secure` y el navegador la descarta sin avisar. Actívalo en *Web* y vuelve a entrar |
| "Contraseña incorrecta" con la buena | El `APP_PASSWORD_HASH` del servidor es de otra contraseña. Regenéralo **en el servidor** con `python tools/hash_password.py` |
| Error 500 | *Error log*. Casi siempre falta `SECRET_KEY` o `APP_PASSWORD_HASH` en el `.env`: la app lo dice por su nombre |
| La página se ve sin estilos | Los *Static files* están mal configurados. No bloquea la validación, pero arréglalo |

Marca "recordar" no hace falta: la sesión dura 90 días a propósito, para no
teclear la contraseña en mitad de la montaña.

## 2. Permiso de ubicación y coordenadas

Pulsa el botón principal.

**Deberías ver:** el aviso de Safari pidiendo permiso de ubicación, y tras
aceptar, el estado *"Obteniendo posición del GPS…"*.

| Si no… | Qué significa |
|---|---|
| No aparece el aviso de permiso | Casi seguro que no estás en HTTPS. Mira la barra de direcciones. Sin HTTPS el navegador **no pide permiso ni da error visible**, simplemente no funciona |
| *"Has denegado el permiso de ubicación…"* | Le diste a "No permitir". **iOS no vuelve a preguntar**: Ajustes → Safari → Ubicación (o Ajustes → Privacidad → Localización → Safari) |
| *"El GPS tardó demasiado…"* | Timeout de 15 s. Sal a cielo abierto; dentro de un edificio o en un garaje es normal |
| *"No se pudo determinar la posición…"* | Sin señal GPS. Comprueba que la localización del sistema está activada |

Estos mensajes salen de `geolocationErrorMessage()` en `app/static/js/app.js`.

## 3. Nombre del lugar

**Deberías ver:** la tarjeta de ubicación con el nombre del sitio y, debajo, tus
coordenadas con la precisión (`± N m`).

Contrasta el nombre con dónde estás de verdad. Un pueblo vecino es normal
—Nominatim devuelve la entidad más cercana con nombre—; otra provincia no.

| Si no… | Qué significa |
|---|---|
| Error 502 | Nominatim no responde, o el proxy del plan gratuito lo está bloqueando. Corre el diagnóstico **en el servidor** |
| La precisión es de cientos de metros | Estás con GPS de red (wifi/antenas), no satélite. Normal bajo techo; a cielo abierto debería bajar de 20 m |

Una segunda pulsación en el mismo sitio debe ir **mucho más rápida**: entra la
caché por coordenada redondeada a ~110 m.

## 4. Tiempo

**Deberías ver:** la tarjeta con temperatura y cielo, la etiqueta *Aire libre*,
la de *Deportes de agua* con su motivo, y las horas de amanecer y anochecer.

Contrástalo con la ventana. Si dice "despejado" y está lloviendo, sospecha de
las coordenadas antes que del tiempo.

Si estás **tierra adentro**, que los datos de oleaje no aparezcan es correcto:
la API marina responde `200` con `null`, no un error.

| Si no… | Qué significa |
|---|---|
| Aviso *"Sin datos meteorológicos"* y el resto sí | Open-Meteo caído o bloqueado por el proxy. La app sigue siendo útil: es la degradación funcionando |

## 5. Recomendación de la IA

**Deberías ver:** un resumen, entre 3 y 5 actividades con categoría, distancia y
duración, y al pie *"Generada ahora · gemini/gemini-3.6-flash"*.

Fíjate en la marca de cada actividad:

- **✓ verificado en el mapa** — el sitio existe en OpenStreetMap y está cerca.
- **sugerencia general** — lo pone el modelo de su conocimiento. Contrástalo
  antes de conducir 40 km.

Esa distinción es la que hace que puedas fiarte de la app: sin ella no sabrías
qué es un dato y qué es una suposición.

**Tarda entre 10 y 25 segundos.** Es normal: Overpass puede irse a 14 s y Gemini
a otros 10-14. El botón se queda deshabilitado mientras tanto.

| Si no… | Qué significa |
|---|---|
| Aviso *"Sin recomendación de IA"* y sale el resto | El proveedor falló. `curl .../healthz`: si `ia_configurada` es `false`, es el `.env`. Si es `true`, corre el diagnóstico en el servidor para ver el error crudo |
| *"Sin recomendación: cuota agotada"* o similar | Límite por minuto de la capa gratuita de Gemini. **No hay reintento automático a propósito**: espera un minuto y pulsa otra vez |
| Sale al instante y pone *"Recomendación cacheada"* | Correcto: mismo sitio, mismo día, misma franja de 3 h. Usa *generar otra* para forzar una nueva |

## 6. Degradación

Lo que se prueba aquí es que la app **avisa** de lo que le falta en vez de
fingir que está completa.

Pon el móvil en **modo avión un segundo y quítalo**, o vete a una zona de mala
cobertura, y pulsa el botón.

**Deberías ver:** o bien el resultado completo, o bien la **tarjeta de avisos**
diciendo qué fuente ha fallado, con el resto de la información igualmente. Lo
que **no** debe pasar es una pantalla en blanco o un error mudo.

| Situación | Comportamiento correcto |
|---|---|
| Sin red al pulsar | Mensaje de error legible, el botón vuelve a estar disponible |
| Overpass caído | Salen los planes igualmente, marcados como *sugerencia general*, con aviso |
| Todo tarda muchísimo | A los 150 s corta solo: *"La consulta tardó demasiado"*. Nunca se queda colgada para siempre |

---

## Carga con datos móviles

Wifi no vale como prueba: en el viaje vas a ir con 4G irregular.

1. **Apaga el wifi** en el iPhone. Comprueba en Ajustes que estás en datos.
2. Cronometra desde que pulsas hasta que aparece la recomendación. Referencia:
   **10-25 s** es lo esperable; más de 40 s merece mirarse.
3. Repite en el mismo sitio: la segunda vez debe ser casi instantánea (caché).
4. Si quieres el detalle de qué tarda, conecta el iPhone al Mac y usa el
   Inspector Web de Safari (Ajustes → Safari → Avanzado → Inspector Web). Sin
   Mac, el propio estado de la app ya te dice en qué fase está.

La carga inicial de la página es de pocos KB: CSS y JS propios, sin librerías
externas. Lo que tarda es el GPS y las APIs, no la descarga.

---

## Cuando los seis estén en verde

Anótalo en `CLAUDE.md` §5 y haz el commit. **No antes**: marcar como "validado
en móvil" algo que no se ha validado es exactamente el tipo de documentación que
este proyecto evita.

Ahí se abre la Fase 3 (notas geolocalizadas y mapa).
