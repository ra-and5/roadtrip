# Handoff: identidad de marca WhereAmAi (acabado «Menta sólida» / B1)

## Overview
Logo, icono de app y tokens de marca para **WhereAmAi** (`d10sdrebrasov.pythonanywhere.com`), una PWA
en Flask — cuaderno de viaje con fotos geolocalizadas, rutas, extracción de metadatos de álbumes y
una guía/IA conversacional con acceso a los datos del usuario.

El logo es un monograma **Ai** donde el punto de la «i» es una chincheta de mapa (gota con hueco).
La misma chincheta se usa como última letra del logotipo `WhereAmAi`, así que logotipo e icono son
la misma pieza. Acabado elegido: **B1 Menta sólida** — monograma en tinta casi negra sobre un
degradado menta→lima, con curvas de nivel muy sutiles al fondo.

## About the design files
Dos tipos de archivo en este bundle:

1. **`assets/` es producción.** Los SVG y PNG están listos para servirse tal cual desde
   `static/` — no hay que redibujarlos.
2. **`WhereAmAi Logo v2.dc.html` (en la raíz del proyecto de diseño) es una referencia visual.**
   Es un prototipo HTML que muestra el logo en contexto (icono, favicon, lockup en claro y en
   oscuro). El lockup de texto hay que **reconstruirlo con las plantillas/CSS del propio
   proyecto** siguiendo la especificación de abajo, no copiando el HTML del prototipo.

## Fidelity
**Alta fidelidad.** Colores, tipografía, proporciones y geometría son definitivos. Los assets se
integran tal cual; el lockup se reproduce con los valores exactos de este documento.

---

## Design tokens

```css
:root {
  /* Marca */
  --brand-ink:        #0D1310; /* tinta del monograma */
  --brand-mint-light: #6FDCB6;
  --brand-mint:       #35C39A; /* color de marca principal */
  --brand-lime:       #9BDD6E;
  --brand-deep:       #12604A; /* menta profundo, para texto sobre fondo claro */

  /* Interfaz (base ya existente en la app) */
  --bg:               #121917; /* = theme-color actual de la PWA */
  --bg-elevated:      #0E1512;
  --text:             #EEF2F0;
  --text-muted:       rgba(238, 242, 240, .55);
  --surface-light:    #F4F7F5;

  /* Degradado del icono */
  --brand-gradient: linear-gradient(150deg, #6FDCB6 0%, #35C39A 46%, #9BDD6E 100%);

  /* Tipografía */
  --font-display: 'Space Grotesk', system-ui, sans-serif; /* logotipo y titulares */
  --font-body:    'Manrope', system-ui, sans-serif;       /* interfaz y texto */
}
```

> ⚠️ **Asunción a confirmar con el cliente.** De la app en producción solo se pudo leer
> `<meta name="theme-color" content="#121917">`. El verde menta (#35C39A) y las dos tipografías son
> una propuesta de diseño, no colores corporativos verificados. Si existe un verde o una tipografía
> oficial, sustituir los tokens de arriba: todo lo demás (geometría, proporciones, tamaños) se
> mantiene igual. Los SVG están escritos con `currentColor` y `var(--brand-mint, …)` para que el
> cambio sea trivial.

### Contraste
- `--brand-mint` sobre `--bg`: ~5.2:1 → válido para texto y para iconografía.
- `--brand-ink` sobre el degradado menta: >10:1 → el monograma del icono siempre legible.
- Para texto en menta sobre fondo claro usar `--brand-deep` (#12604A), no `--brand-mint`.

---

## Geometría del logo (fuente de verdad)

Todo se dibuja en una caja de diseño de **100 × 100**:

| Elemento | Definición | Grosor |
| --- | --- | --- |
| «A» diagonales | `M16 79 L37 21 L58 79` | 10.5 |
| «A» travesaño | `M25.5 60 H48.5` | 8.8 |
| «i» asta | `M76 76 V55` | 10.5 |
| Chincheta | `M76 46.5C67 37.2 62 32.4 62 24A14 14 0 1 1 90 24C90 32.4 85 37.2 76 46.5Z` | relleno |
| Hueco de la chincheta | `circle cx=76 cy=23.2 r=5.4` | relleno |

Reglas: `stroke-linecap="round"`, `stroke-linejoin="round"`. Hueco de 8.5 unidades entre la punta
de la chincheta (y=46.5) y el arranque del asta (y=55) — ese aire es lo que hace que se lea como
una «i» y no como un pin pegado. **No cerrar ese hueco ni cambiar los grosores.**

### Versión pequeña (≤32 px)
A partir de 32 px se usa una variante engordada y sin hueco en la chincheta:
diagonales `M18 78 L37 24 L56 78` (13), travesaño `M27 60 H47` (10), asta `M76 76 V56` (13),
chincheta `M76 48C66.5 38 61 33 61 24A15 15 0 1 1 91 24C91 33 85.5 38 76 48Z`.
Ya está aplicada en `favicon.svg`, `favicon-32.png` y `favicon-16.png`.

### Área de respeto y tamaños mínimos
- Margen libre alrededor del logotipo: la altura de la «A» (≈ 1× cap-height) por cada lado.
- Logotipo completo: mínimo **120 px** de ancho.
- Solo monograma: mínimo **16 px**.

---

## El logotipo (lockup)

Estructura: la palabra se compone como **un único bloque de texto** `WhereAmA` + el glifo SVG de la
«i». El SVG se alinea a la línea base como una letra más.

```html
<span class="wam-logo">WhereAmA<svg class="wam-i" viewBox="6 20 18 59" aria-hidden="true">
  <rect x="12.4" y="55" width="5.2" height="24" rx="2.6" fill="currentColor"/>
  <path d="M15 48C10.5 42.7 7.5 39.5 7.5 33.6A7.5 7.5 0 1 1 22.5 33.6C22.5 39.5 19.5 42.7 15 48Z"
        fill="var(--brand-mint-light)"/>
  <circle cx="15" cy="33.2" r="2.9" fill="var(--bg-elevated)"/>
</svg><span class="sr-only">i</span></span>
```

```css
.wam-logo {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 38px;           /* escala libre: todo lo demás está en em */
  letter-spacing: -.035em;
  color: var(--text);
  white-space: nowrap;
}
.wam-i {
  display: inline-block;
  vertical-align: baseline;  /* la caja del SVG termina EN la línea base */
  height: 1.279em;           /* 48.6px a 38px */
  width: .39em;              /* 14.8px a 38px */
  margin-left: .013em;
}
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
```

Por qué esos números: el `viewBox="6 20 18 59"` recorta la caja de diseño justo desde encima de la
chincheta hasta la línea base (y=79), de modo que `vertical-align: baseline` la asienta sola. La
altura del asta (24 unidades) queda igual a la altura de x de Space Grotesk 600, y su grosor (5.2)
al del trazo de la fuente: la chincheta parece parte de la palabra, no un icono pegado.

**Colores del glifo según fondo**

| Fondo | asta (`rect`) | chincheta (`path`) | hueco (`circle`) |
| --- | --- | --- | --- |
| Oscuro `#0E1512` / `#121917` | `#EEF2F0` | `#6FDCB6` | `#0E1512` |
| Claro `#F4F7F5` | `#121917` | `#12604A` | `#F4F7F5` |
| Sobre el degradado menta | `#0D1310` | `#0D1310` | `#6FDCB6` |

**Lockup con icono** (cabecera de la app): badge de `54px` con `border-radius: 15px`, fondo
`--brand-gradient`, `box-shadow: inset 0 1px 0 rgba(255,255,255,.45)`, monograma dentro al 66 %;
`gap: 15px` hasta el logotipo.

---

## Assets

Todos en `assets/`. Copiar a `static/brand/`.

| Archivo | Uso |
| --- | --- |
| `whereamai-mark.svg` | Monograma suelto, sin fondo. Letras en `currentColor`, hueco en `var(--brand-mint)`. Para cabeceras, emails, favicon vectorial en oscuro. |
| `whereamai-pin-i.svg` | Solo el glifo «i-chincheta», para componer el logotipo. |
| `whereamai-icon.svg` | Icono completo con fondo (monograma al 66 %). Fuente vectorial de los PNG. |
| `whereamai-icon-maskable.svg` | Igual con el monograma al 50 % → cabe en la zona segura de Android. |
| `favicon.svg` | 32 px, esquinas redondeadas, variante engordada. |
| `icon-192.png` · `icon-512.png` | Iconos PWA `purpose="any"`, a sangre (el sistema aplica su propia máscara). |
| `icon-maskable-512.png` | Icono PWA `purpose="maskable"`. |
| `apple-touch-icon-180.png` | iOS (iOS redondea solo; **no** llevar esquinas ya redondeadas). |
| `favicon-32.png` · `favicon-16.png` | Favicons raster de respaldo. |

No hay fotografías ni iconografía de terceros. Iconos de interfaz: pendiente de decidir
(recomendación: Phosphor o Lucide, un solo set).

---

## Integración en la PWA (Flask)

### 1. Archivos
```
static/brand/…        ← todo el contenido de assets/
```

### 2. `<head>` de la plantilla base
```html
<link rel="icon" href="{{ url_for('static', filename='brand/favicon.svg') }}" type="image/svg+xml">
<link rel="icon" href="{{ url_for('static', filename='brand/favicon-32.png') }}" sizes="32x32">
<link rel="apple-touch-icon" href="{{ url_for('static', filename='brand/apple-touch-icon-180.png') }}">
<link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">
<meta name="theme-color" content="#121917">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="WhereAmAi">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Manrope:wght@400;500;600;700&display=swap">
```
`apple-mobile-web-app-status-bar-style` pasa de `default` a `black-translucent` para que la barra de
estado acompañe al fondo oscuro (hay que reservar `env(safe-area-inset-top)` en la cabecera).

### 3. `manifest.json`
```json
{
  "name": "WhereAmAi — Cuaderno de a bordo",
  "short_name": "WhereAmAi",
  "description": "Tu viaje, tus fotos y una guía local que conoce tus datos.",
  "lang": "es",
  "dir": "ltr",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#121917",
  "theme_color": "#121917",
  "icons": [
    { "src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },
    { "src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" },
    { "src": "/static/brand/whereamai-icon.svg", "sizes": "any", "type": "image/svg+xml" }
  ]
}
```
`background_color` **debe** ser `#121917`: es el color de la pantalla de arranque, y con el icono
menta encima queda coherente con el splash del diseño.

### 4. Splash / pantalla de arranque
Android e iOS la generan con `background_color` + icono. Si se quiere una propia: fondo
`#121917`, monograma centrado a `64px`, debajo el logotipo a `18px` y, a `11.5px` con
`letter-spacing:.06em` y `--text-muted`, el texto `CUADERNO DE A BORDO`.

### 5. Service worker
Añadir los archivos de `static/brand/` a la precaché e **incrementar la versión de la caché** al
desplegar: si no, los navegadores que ya instalaron la PWA seguirán sirviendo el icono viejo.
En Android suele hacer falta reinstalar el acceso directo para ver el icono nuevo.

### 6. Sustituir el logo en la app
- Pantalla de login: `WhereAmAi` (lockup con icono) donde hoy está el texto plano, y el subtítulo
  «Cuaderno de a bordo» en `--text-muted`.
- Cabecera de la app: solo el monograma a 28 px + el logotipo a 20 px.
- Cualquier `<title>`/OG image: usar `icon-512.png`.

---

## Comprobaciones antes de cerrar
- [ ] Icono nítido a 16, 32, 192 y 512 px.
- [ ] Maskable: el monograma no se recorta con la máscara circular de Android (probar en Lighthouse → *Manifest*).
- [ ] iOS: el icono no sale con doble redondeo ni con fondo blanco.
- [ ] El logotipo no parte de línea a 320 px de ancho (`white-space: nowrap` + `font-size` fluido).
- [ ] Con las fuentes bloqueadas, el fallback `system-ui` no descoloca el glifo de la «i» (está en `em`, debería aguantar).
- [ ] `theme-color` y `background_color` coinciden con el fondo real de la app.

## Files
- `assets/` — los archivos de marca (producción).
- `../WhereAmAi Logo v2.dc.html` — prototipo HTML con los tres acabados (B1 es el elegido) y el logo en contexto.
