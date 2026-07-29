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
#   tools/logs.sh tiempos    cuánto tarda el SERVIDOR en cada /api/
#   tools/logs.sh todo       sin filtrar nada, por si falta algo
#
set -euo pipefail

# El dominio sale del usuario, que en PythonAnywhere es el mismo que el
# subdominio — pero EN MINÚSCULAS. `whoami` devuelve el nombre tal y como se
# registró ("D10SdreBrasov") y el archivo se llama "d10sdrebrasov…", así que sin
# bajarlo a minúsculas esto no encuentra el log de nadie que se registrara con
# mayúsculas. Se puede forzar con ROADTRIP_LOG.
LOG="${ROADTRIP_LOG:-/var/log/$(whoami | tr '[:upper:]' '[:lower:]').pythonanywhere.com.error.log}"

if [[ ! -f "$LOG" ]]; then
  # Si en /var/log solo hay un candidato, es ese: preguntar por una ruta que la
  # máquina ya sabe es el trabajo que esta herramienta existe para ahorrar.
  candidatos=(/var/log/*.error.log)
  if [[ ${#candidatos[@]} -eq 1 && -f "${candidatos[0]}" ]]; then
    LOG="${candidatos[0]}"
  else
    echo "No encuentro el log en: $LOG" >&2
    echo "Si tu usuario no coincide con el subdominio:" >&2
    echo "  ROADTRIP_LOG=/var/log/TU_DOMINIO.error.log tools/logs.sh" >&2
    for candidato in "${candidatos[@]}"; do
      [[ -f "$candidato" ]] && echo "  hay: $candidato" >&2
    done
    exit 1
  fi
fi

# Cada línea aparece dos veces; la de "INFO in app" es la copia. Se queda la
# otra, que ya lleva la hora del servidor delante.
sin_duplicados() { grep -v " in app: "; }

# Lo que escribe la app. Fuera queda el ruido de las librerías: las llamadas
# HTTP del proveedor de IA y los "write error", que son el móvil cerrando la
# conexión antes de recibir la respuesta y no significan nada.
NUESTRO='Puntos importados|Telemetría recibida|rechazad|Cuerpo recibido|Fallo de IA|Fallo inesperado'

# Un filtro sin resultados imprime POR QUÉ, en vez de no imprimir nada. Que no
# salga ninguna línea es la respuesta más frecuente aquí —"el atajo no ha
# enviado"— y sin decirlo se confunde con la herramienta rota, que es el mismo
# tiempo perdido que resolvió registrar también los aciertos (decisión 44).
mostrar() {
  local que="$1" patron="$2" salida
  salida="$(grep -E "$patron" "$LOG" | sin_duplicados | tail -20 || true)"
  if [[ -z "$salida" ]]; then
    echo "Ni una línea de $que en $(basename "$LOG")"
    echo "($(wc -l < "$LOG") líneas en total; para verlo crudo: tools/logs.sh todo)"
    return
  fi
  echo "$salida"
}

case "${1:-}" in
  -f|--seguir)
    echo "Mirando $LOG   (Ctrl+C para salir)"
    tail -f "$LOG" | sin_duplicados | grep --line-buffered -E "$NUESTRO"
    ;;
  fotos)
    mostrar "fotos" 'Puntos importados|Importación de puntos|Cuerpo recibido'
    ;;
  pasos)
    mostrar "telemetría" 'Telemetría recibida|Ingesta rechazada'
    ;;
  tiempos)
    # Cuánto tarda el SERVIDOR en contestar cada /api/. Desde el navegador solo
    # se mide el viaje entero, y ahí van juntos el camino y el trabajo: esta
    # lista es la única forma de restar uno del otro (decisión 48).
    mostrar "tiempos de la API" ' -> [0-9]+ en [0-9]+ ms'
    ;;
  todo)
    tail -40 "$LOG"
    ;;
  "")
    mostrar "la app" "$NUESTRO"
    ;;
  *)
    echo "No conozco «$1». Prueba: (nada) | -f | fotos | pasos | todo" >&2
    exit 2
    ;;
esac
