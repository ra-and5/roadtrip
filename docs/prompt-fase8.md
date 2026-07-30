# Encargo de la Fase 8 — que apetezca abrirla

> Escrito el 30-07-2026, al terminar la 7. Como los demás `prompt-*.md`, es el
> registro de **qué se pidió**, para poder contrastarlo luego con lo que se hizo.

---

## De dónde venimos

Las siete fases anteriores han ido en un orden deliberado, y está escrito en el
§1 del `CLAUDE.md`: **primero que los datos sean ciertos, la estética después.**
Un dashboard precioso sobre pasos contados dos veces sigue siendo un dashboard
que miente, y encima uno que te crees.

Ese requisito ya está cumplido, y esta vez con números:

- los **pasos** cuadran con la app Salud, y una serie que no puede salir de un
  acumulado se detecta sola (decisiones 50 y 52 bis);
- el **álbum de fotos** se refleja de verdad, y quitar una foto la quita del mapa;
- **cambiar de pantalla** pasó de 6,8 s a 675 ms medidos contra el desplegado
  (decisión 48);
- hay un guion que **recorre las cinco pantallas en un navegador** y demuestra
  que caza fallos metidos a propósito (decisión 47);
- y las fuentes nuevas —POIs por categoría, incendios— llegan con su veredicto
  y sus límites declarados.

Así que ahora sí toca lo otro.

## Lo que se pide

**Que la app deje de parecer una herramienta de depuración y se convierta en el
cuaderno de a bordo que dice el §1 del `CLAUDE.md`**: el sitio donde miras qué
hacer hoy, y donde dentro de un año se ve el viaje entero.

En palabras del usuario: *"un puto centralizado de todos los datos que puedo
necesitar en el viaje, e ir teniendo constancia del viaje y de cómo avanza"*.

### 1. El diseño, en serio

Hoy el CSS son ~500 líneas escritas a mano, mobile-first y sin framework — y eso
**no se toca por gusto**: sin frameworks son menos kilobytes que descargar con
mala cobertura, que es una decisión del proyecto y no una limitación.

Lo que hace falta es un sistema visual coherente: tipografía, escala de
espaciados, jerarquía, estados (cargando, error, vacío, degradado) y una
identidad que no sea "el gris por defecto". Se espera usar una skill de diseño
frontend para no improvisarlo.

Restricciones que **no** se negocian, porque cada una está pagada con un fallo:

- **Nada de CDNs.** Ni fuentes, ni iconos, ni CSS. Lo que se sirve es lo que se
  probó (decisiones 17 y 28), y `tools/verificar.py` bloquea todo lo externo, así
  que un CDN sale como fallo, no como "carga un poco más lenta".
- **Los estáticos se cachean un año e `immutable`**, y eso solo es seguro porque
  la URL lleva `?v=<mtime>` (decisión 48). Si se añaden archivos, van por
  `url_for('static', ...)` o se quedan sin invalidar.
- **Modo oscuro incluido**: ya existe con `prefers-color-scheme` y hay que
  mantenerlo. Se usa de noche en un camper.
- **Área táctil de 48 px** como mínimo. Esto se usa en marcha y con una mano.
- **Cada pantalla sigue contestando UNA pregunta** (decisión 40). Lo bonito no
  puede traer de vuelta los datos duplicados que se quitaron.

### 2. Que se vea el avance del viaje

Es la mitad de "constancia" que aún no existe visualmente. Los datos ya están:
`ruta.py` mezcla notas y fotos por hora local, `notes.progreso()` da días, sitios,
racha y el tablero de comunidades, y `perfil.py` da la serie de pasos con sus
huecos declarados.

Lo que falta es enseñarlo como un viaje que avanza y no como una tabla.

### 3. El diario (§3 de la Fase 7, que quedó sin hacer)

Un muro cronológico: cada día, lo que publicaste ese día, fotos y notas
mezcladas. Y con ello **las miniaturas**, que es lo que convierte el mapa en un
álbum: ~8 KB por foto, mil fotos son 8 MB de los 512 del plan.

Antes de tocarlo hay dos decisiones escritas y sin discutir (decisión 27):
multipart y no base64, redimensionado en el navegador, **archivo primero y fila
después**, y el nombre derivado del identificador. Y dos que sí hay que tomar:
qué pasa con la miniatura cuando se quita la foto del álbum (decisión 45), y qué
se hace cuando quede poco disco.

### 4. La PWA instalable

`manifest.json`, iconos y el `<meta>` de iOS. Con el aviso de siempre: **un
service worker vuelve a plantear enteras las decisiones 28 y 41**, así que no
entra sin un plan para invalidarlo.

## Lo que NO se toca aquí

- **Cerrar la Fase 2d.** Sigue siendo calendario: que la telemetría llegue sola
  y sin huecos varios días. Las automatizaciones se pusieron el 30-07.
- **Cerrar la Fase 3**: escribir una nota sin cobertura de verdad y verla
  aparecer al volver la señal.
- **Espejos de Overpass** (decisión 22) y los dos caminos de las fotos que no
  deduplican entre sí.

## Cómo se sabe que la fase está cerrada

1. Las cinco pantallas comparten un sistema visual y se reconocen como la misma
   app, en claro y en oscuro.
2. `tools/verificar.py` sigue en verde y `tools/verificar_sabotaje.sh` sigue
   cazando los seis: el rediseño **no puede** romper ids ni dejar botones
   muertos, que es exactamente lo que pasó el 29-07 (decisión 42).
3. `tools/medir_pantallas.py` contra el desplegado no empeora los números de la
   decisión 48.
4. El diario enseña las publicaciones de cada día con sus miniaturas.
5. La app se instala en la pantalla de inicio del iPhone y abre sin barra.
6. `CLAUDE.md` tiene las decisiones nuevas con el porqué y con los números.
