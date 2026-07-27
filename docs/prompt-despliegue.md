# Prompt: despliegue y validación en móvil

Pégaselo a Claude Code en una sesión nueva (haz `/clear` antes: esto no necesita
el contexto de la Fase 2, y arrastrarlo solo añade ruido).

---

Vamos a desplegar el MVP en PythonAnywhere y validarlo desde mi iPhone real.
Esto **no es la Fase 3**: no toques funcionalidad nueva. El objetivo es tener lo
que ya existe funcionando en producción y verificado en el móvil.

Ten en cuenta que **no puedes desplegar tú**: PythonAnywhere se configura desde su
panel web y no tienes acceso a esa cuenta. Tu papel es preparar el terreno,
guiarme, y ayudarme a interpretar lo que falle.

## Paso 1 — Verificación previa en local

Antes de subir nada, comprueba y arregla lo que haga falta:

1. **`requirements.txt` completo y con versiones fijadas.** Instálalo en un
   virtualenv limpio y arranca la app para confirmar que no falta nada. Un
   import que funciona en mi máquina por casualidad y no en el servidor es el
   fallo de despliegue número uno.
2. **Versión de Python.** El README asume 3.11 en PythonAnywhere. Confirma que el
   código no usa sintaxis o módulos de una versión superior a la que voy a
   desplegar.
3. **`.env.example` al día**: debe listar todas las variables que `config.py` lee
   de verdad, incluidas las añadidas en la Fase 2. Si falta una, en el servidor
   la app fallará al arrancar y no sabré por qué.
4. **`config.py` falla rápido y claro** si falta una variable obligatoria: el
   mensaje debe decir *qué* variable falta, no lanzar un `KeyError` pelado.
5. **`git status` limpio** y `.env` correctamente ignorado. Confírmame
   explícitamente que `.env` no está bajo control de versiones.
6. **Suite de tests en verde.**

Luego ejecuta `python tools/diagnostico.py 43.5622 -6.1456` y dime el estado de
cada dependencia. La línea de Anthropic debe dar OK: ya tengo la API key en el
`.env` local. Si falla, arreglar eso es prioridad absoluta antes de desplegar.

## Paso 2 — Checklist de despliegue

Dame una lista numerada de pasos concretos para ejecutar en PythonAnywhere,
partiendo de la sección de despliegue del README pero **actualizada a lo que hay
ahora en el repo** (rutas reales, versión de Python real, variables reales de la
Fase 2). Que sea algo que pueda seguir sin pensar, marcando casillas.

Incluye explícitamente:

- Los comandos exactos de la consola Bash de PythonAnywhere, con el nombre de mi
  usuario como marcador claro (`TU_USUARIO`).
- Cómo crear el `.env` en el servidor y qué variables tiene que llevar.
- El contenido exacto del archivo WSGI.
- La configuración de virtualenv y de archivos estáticos.
- Cómo comprobar que ha arrancado (`/healthz`) y dónde ver los errores.

## Paso 3 — Validación en el iPhone (el paso que de verdad importa)

Dame una lista de comprobaciones concretas para hacer desde el móvil, en orden,
con lo que debería ver en cada una y qué significa si no lo veo:

1. Login.
2. Permiso de geolocalización y captura de coordenadas.
3. Nombre del lugar resuelto correctamente.
4. Condiciones meteorológicas.
5. Recomendación generada por la IA.
6. Degradación: qué debería pasar con cobertura mala o intermitente.

**El GPS es el punto crítico**: solo funciona en HTTPS, así que esta es la primera
vez que se puede probar de verdad. Dime también cómo comprobar que la carga en
móvil es razonable con datos móviles, no solo con wifi.

## Paso 4 — Tabla de diagnóstico

Una tabla de "síntoma → causa más probable → qué mirar", pensada para cuando esté
en una gasolinera de Asturias sin ganas de depurar. Cubre al menos: la app no
carga, error 500 al arrancar, el GPS no pide permiso, el GPS da error de permiso
denegado, el CSS no se aplica, la IA no responde pero el resto sí, y todo va
lentísimo.

Guárdala en `docs/troubleshooting.md` y enlázala desde el README.

## Al terminar

Actualiza `README.md` y `CLAUDE.md` (§5 Estado actual) con el hecho de que está
desplegado y validado en móvil, y haz un commit. Añade también la sección de
"Conceptos de esta fase" que pide el `CLAUDE.md`.

No pasamos a la Fase 3 hasta que los seis puntos del Paso 3 estén en verde.