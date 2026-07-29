#!/usr/bin/env bash
#
# Los logs del servidor, sin tener que acordarse de la ruta.
#
# En PythonAnywhere el log vive en /var/log/<dominio>.error.log, y ese nombre no
# se recuerda ni se teclea bien a la primera. Peor: cada línea sale DOS veces
# —una con el formato de Flask y otra con el del servidor— así que la mitad de
# lo que lees es repetido, y con el móvil en la mano eso importa.
#
# Uso:
#   tools/logs.sh            lo último que ha pasado (lo de la app, sin ruido)
#   tools/logs.sh -f         se queda mirando en directo (Ctrl+C para salir)
#   tools/logs.sh fotos      solo las importaciones de fotos
#   tools/logs.sh pasos      solo la telemetría
#   tools/logs.sh todo       sin filtrar nada, por si falta algo
#
set -euo pipefail

# El dominio sale del nombre del directorio del usuario, que en PythonAnywhere
# es el mismo que el subdominio. Se puede forzar con ROADTRIP_LOG.
LOG="${ROADTRIP_LOG:-/var/log/$(whoami).pythonanywhere.com.error.log}"

if [[ ! -f "$LOG" ]]; then
  echo "No encuentro el log en: $LOG" >&2
  echo "Si tu usuario no coincide con el subdominio:" >&2
  echo "  ROADTRIP_LOG=/var/log/TU_DOMINIO.error.log tools/logs.sh" >&2
  ls /var/log/*.error.log 2>/dev/null | sed 's/^/  hay: /' >&2 || true
  exit 1
fi

# Cada línea aparece dos veces; la de "INFO in app" es la copia. Se queda la
# otra, que ya lleva la hora del servidor delante.
sin_duplicados() { grep -v " in app: "; }

# Lo que escribe la app. Fuera queda el ruido de las librerías: las llamadas
# HTTP del proveedor de IA y los "write error", que son el móvil cerrando la
# conexión antes de recibir la respuesta y no significan nada.
NUESTRO='Puntos importados|Telemetría recibida|rechazad|Cuerpo recibido|Fallo de IA|Fallo inesperado'

case "${1:-}" in
  -f|--seguir)
    echo "Mirando $LOG   (Ctrl+C para salir)"
    tail -f "$LOG" | sin_duplicados | grep --line-buffered -E "$NUESTRO"
    ;;
  fotos)
    grep -E 'Puntos importados|Importación de puntos|Cuerpo recibido' "$LOG" | sin_duplicados | tail -20
    ;;
  pasos)
    grep -E 'Telemetría recibida|Ingesta rechazada' "$LOG" | sin_duplicados | tail -20
    ;;
  todo)
    tail -40 "$LOG"
    ;;
  "")
    grep -E "$NUESTRO" "$LOG" | sin_duplicados | tail -20
    ;;
  *)
    echo "No conozco «$1». Prueba: (nada) | -f | fotos | pasos | todo" >&2
    exit 2
    ;;
esac
