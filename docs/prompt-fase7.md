# Encargo de la Fase 7 — que se sostenga, que vuele, y que se pueda enseñar

> Escrito el 29-07-2026, al cerrar la 6b. Como los demás `prompt-*.md`, esto es
> el registro de **qué se pidió**, para poder contrastarlo luego con lo que se
> hizo.

---

## De dónde venimos, y por qué esta fase es la que toca

El 29-07-2026 se dedicó el día entero a perseguir una cadena de fallos que
tenían todos la misma forma: **funcionaban en el portátil y no en el servidor, y
ninguno daba un error**. El botón principal muerto por un id huérfano. El
contexto tardando 34 s por tres hilos peleándose por SQLite. Un despliegue que
Safari no llegaba a ver. Un log que solo contaba los fallos, así que el silencio
no significaba nada. El atajo enviando el cuerpo vacío. El álbum que solo sumaba.

Todo eso está arreglado y **cerrado con datos medidos**, y de ahí salen las
decisiones 42 a 46. Pero deja una lección que ordena esta fase entera:

> Lo que no se comprueba en el sitio donde corre, no está comprobado. Y una
> suite verde no dice nada del navegador.

Así que la Fase 7 empieza por ahí. Y luego, por fin, lo que hace que la app se
pueda enseñar y apetezca abrirla.

---

## §1. Una verificación de verdad, y que quede escrita

Hoy hay **534 tests** y ninguno habría cazado el botón muerto: eran todos de
Python. Lo que falta no son más tests unitarios, es un recorrido que pase por
donde de verdad se rompe.

- **Un guion de verificación ejecutable**, no una lista en un documento. Que
  arranque la app, recorra las cuatro pantallas, pulse lo que hay que pulsar y
  falle con un motivo. Puede ser Playwright, o el navegador que ya se usa desde
  Claude Code — lo que importa es que **corra entero de un tirón** y diga qué se
  ha roto.
- **Que cubra los caminos que hoy solo se prueban a mano**: los botones de
  Inicio, guardar una nota con la cola offline, el filtro y el *revivir* del
  Mapa, el reintento del Perfil, y una pregunta al chat con un proveedor falso
  (esto último **sin gastar tokens**: se inyecta un `FakeProvider`, como ya hace
  la suite).
- **Sin red y sin API keys**, como el resto (§2 del `CLAUDE.md`). Un guion de
  verificación que necesita cobertura no sirve en un camper.
- Y una regla que viene de hoy: **medir en el servidor, no en el portátil**.
  `tools/medir_contexto.py` ya existe; conviene un modo que lo compare con lo que
  tarda de verdad la pantalla desde fuera.

Criterio de cierre: se puede romper algo a propósito —un id, un endpoint, una
plantilla— y el guion lo dice **antes** de desplegar.

## §2. Que cambiar de pantalla sea instantáneo

Hoy cada pantalla es una carga completa: HTML, CSS, JavaScript, y luego su
`fetch`. En el móvil, con un solo worker y mala cobertura, eso se nota y es lo
que hace que una app parezca lenta aunque sus datos vayan rápidos.

Lo que se pide es que **ir de Perfil a Mapa o a Chat sea inmediato**. Antes de
elegir cómo, hay que medirlo: cuánto tarda hoy cada salto, y **cuánto de eso es
red y cuánto es pintar**. Con ese número se decide. Opciones sobre la mesa, de
menos a más invasiva:

- **Precargar lo que se va a pedir** (`<link rel="prefetch">` o un `fetch` al
  pasar el dedo por encima). Barato y no cambia la arquitectura.
- **Cachear en memoria la última respuesta de cada pantalla** y pintarla al
  instante mientras se revalida por detrás. Encaja con la decisión 46: el dato
  viejo se enseña ya, el nuevo entra solo, y el cambio se anuncia.
- **Navegación sin recarga** (una sola página con las cuatro vistas). Es la que
  más gana y la que más riesgo trae: rompe el "cada pantalla pide lo que
  enseña" de la decisión 40, y hay que decidir qué pasa con el mapa de Leaflet al
  ocultarlo. **No se hace sin haber medido que las dos anteriores no bastan.**

Restricción que no se negocia: **las páginas tienen que seguir abriendo con mala
cobertura** (decisión 28). Lo que se cachee no puede impedir ver una versión
anterior cuando no hay red.

## §3. El diario: tus publicaciones, en orden

Es la pieza que falta para que el §1 del `CLAUDE.md` —"recordar, después"— exista
de verdad. Hoy las notas están en el Mapa como chinchetas y las fotos como
nombres de archivo. Lo que se pide es **un muro cronológico**: cada día, lo que
publicaste ese día, fotos y notas mezcladas, como se recuerda.

Ya está casi todo hecho por debajo: `ruta.py` mezcla notas y fotos por hora
local, calcula los días y los kilómetros. Lo que falta es enseñarlo.

Y aquí entra lo que hoy no se puede hacer y es la mitad del valor:
**las miniaturas**. El mapa dice `📷 IMG_4736.jpeg` y no puede enseñarla, porque
la foto vive en el iPhone y no se sube (decisión 30). Una miniatura de 200×150 a
JPEG bajo son ~8 KB: **mil fotos son 8 MB** de los 512 MB del plan, y el diario
pasa de una lista de nombres a un álbum. Reutiliza entera la tubería que ya
funciona; lo único que cambia es que el atajo mande además la imagen reducida.

Cuando se toque, hay decisiones ya escritas que **no hay que volver a discutir**
(decisión 27): multipart y no base64, redimensionado en el navegador o en el
propio Atajos, **archivo primero y fila después**, y el nombre derivado del
identificador y jamás del cliente.

Dos cosas nuevas que sí hay que decidir, y conviene hacerlo antes de escribir:

- **Qué pasa al quitar una foto del álbum ahora que el borrado existe**
  (decisión 45): si su punto se borra, ¿se borra también su miniatura del disco?
  Un archivo huérfano gasta cuota y no lo ve nadie.
- **El presupuesto de disco deja de ser teórico.** `libres_mb()` ya vigila la
  cuota (decisión 38); con miniaturas entrando a diario, hay que decidir qué pasa
  cuando quede poco: dejar de aceptar, avisar, o borrar las más antiguas.

## §4. Personalizar el mapa

Pedido por el usuario y anotado en el roadmap desde el 28-07. Ahora sí toca,
porque **los datos ya son ciertos**: el álbum se refleja de verdad, los
kilómetros salen de un solo cálculo y el mapa se actualiza solo.

Sin alcance cerrado a propósito — es la parte donde la idea del usuario manda.
Lo que sí está decidido es el orden, y no cambia: *primero que los datos sean
ciertos, la estética después*. Ese requisito ya está cumplido, así que esto deja
de ser prematuro.

## §5. La PWA instalable

`manifest.json`, iconos, y el `<meta>` de iOS para que se instale en la pantalla
de inicio y abra a pantalla completa, sin la barra de Safari.

Es lo que convierte "una web que abro en el navegador" en "mi app del viaje", y
es barato. Dos cosas que hay que mirar y no son evidentes:

- **Un service worker cambia las reglas del juego.** La decisión 41 (nada de
  `/api/` se cachea) y la 28 (el HTML sí) se vuelven a plantear enteras si hay
  uno interceptando. Si se añade, tiene que respetar exactamente esa frontera, y
  hace falta un plan para actualizarlo — un service worker mal invalidado es la
  decisión 41 otra vez, pero mucho más difícil de depurar.
- **IndexedDB en iOS se purga tras siete días sin abrir la app**, y eso importa
  para la cola de notas (decisión 26). Conviene comprobar si instalarla como PWA
  cambia ese comportamiento, porque en un viaje largo es la diferencia entre
  perder notas o no.

## §6. Lo que sigue pendiente y no se toca aquí

Para que no se cuele por la puerta de atrás:

- **Cerrar la Fase 2d.** Sigue siendo tiempo, no trabajo: que la telemetría
  llegue sola y sin huecos varios días. El 29-07 ya había 5 muestras reales con
  1 día sin datos y 1 a medias, así que la serie existe y le faltan días. Nada de
  análisis encima hasta entonces.
- **Cerrar la Fase 3.** Escribir una nota sin cobertura de verdad y verla
  aparecer al volver la señal.
- **Espejos de Overpass** (decisión 22) y los dos caminos de las fotos que no
  deduplican entre sí.

---

## Cómo se sabe que la fase está cerrada

1. El guion del §1 corre entero y **caza un fallo introducido a propósito**.
2. Cambiar de pantalla en el iPhone se siente inmediato, y hay un número medido
   antes y después.
3. El diario enseña las publicaciones de cada día con sus miniaturas, y quitar
   una foto del álbum la quita también de ahí.
4. La app se instala en la pantalla de inicio del iPhone y abre sin barra.
5. `CLAUDE.md` tiene las decisiones nuevas escritas, con el **porqué** y con los
   números que las sostienen — igual que las 42 a 46, que es lo que hace que un
   día perdido no se repita.
