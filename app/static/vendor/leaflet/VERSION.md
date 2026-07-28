# Leaflet 1.9.4 — copia servida por nosotros

Descargado de `https://unpkg.com/leaflet@1.9.4/dist/` el 28-07-2026.

## Por qué está aquí y no en un CDN

Un CDN es un tercero más que puede caerse, y esta app se usa con mala
cobertura. El navegador tiene más probabilidades de tener nuestro archivo en la
caché (ya ha entrado a la app) que de alcanzar `unpkg.com` desde un camping. Es
la misma razón por la que las dependencias de Python van con `==` en vez de
`>=` (decisión 17): lo que se probó es lo que se sirve.

Y una que no es obvia: **los tiles del mapa los pide el navegador, no el
servidor**, así que la lista blanca del proxy de PythonAnywhere no interviene
en nada de esto. Vendorizar Leaflet no es para esquivar el proxy.

## Archivos

| Archivo | SHA-256 |
|---|---|
| `leaflet.js` | `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a` |
| `leaflet.css` | `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6` |

`images/` son los iconos por defecto de las chinchetas. Leaflet los busca en
`images/` **relativo a `leaflet.css`**, así que esa carpeta tiene que quedarse
donde está o las chinchetas desaparecen sin dar ningún error de consola.

## Cómo actualizar

```bash
B=https://unpkg.com/leaflet@<version>/dist
for f in leaflet.js leaflet.css; do curl -sS -o app/static/vendor/leaflet/$f $B/$f; done
for f in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
  curl -sS -o app/static/vendor/leaflet/images/$f $B/images/$f
done
sha256sum app/static/vendor/leaflet/leaflet.js   # y anótalo arriba
```

Después: `python -m pytest -q` y abrir `/mapa` para comprobar que las
chinchetas siguen saliendo. Los tests comprueban que la página referencia los
archivos locales, no que Leaflet funcione: eso solo se ve en un navegador.
