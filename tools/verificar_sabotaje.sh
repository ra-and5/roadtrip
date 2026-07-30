#!/usr/bin/env bash
# Comprueba que `tools/verificar.py` sirve para algo: rompe la app a propósito,
# una cosa cada vez, y exige que el guion lo cace.
#
# Un guion de verificación que nunca ha fallado no está probado, está sin
# estrenar. Es lo mismo que se hizo con `tests/test_frontend_ids.py`: se
# reintrodujo el bug para ver si el test lo pillaba (decisión 42).
#
# Cada sabotaje toca UNA cosa —un id de plantilla, un id muerto en el
# JavaScript, una ruta de la API— y se deshace pase lo que pase.
#
# Uso:  tools/verificar_sabotaje.sh
# Salida: 0 si el guion caza los seis; 1 si alguno se le escapa.

set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python"

fallos=0
copias=()

restaurar() {
  for archivo in "${copias[@]:-}"; do
    [ -n "$archivo" ] && [ -f "$archivo.sabotaje" ] && mv "$archivo.sabotaje" "$archivo"
  done
  copias=()
}
trap restaurar EXIT INT TERM

# sabotear <descripcion> <archivo> <sed> <pantalla>
sabotear() {
  local descripcion="$1" archivo="$2" expresion="$3" pantalla="$4"

  printf '  %-46s' "$descripcion"
  cp "$archivo" "$archivo.sabotaje"
  copias=("$archivo")
  sed -i "$expresion" "$archivo"

  if cmp -s "$archivo" "$archivo.sabotaje"; then
    echo "SIN EFECTO  (el sed no cambió nada: revisa el patrón)"
    fallos=$((fallos + 1))
    restaurar
    return
  fi

  local salida
  salida=$("$PY" tools/verificar.py --solo "$pantalla" 2>&1)
  local codigo=$?
  restaurar

  if [ "$codigo" -ne 0 ]; then
    echo "CAZADO"
    echo "$salida" | grep -E "^  .*FALLO|^     " | head -3 | sed 's/^/       /'
  else
    echo "SE ESCAPÓ  <-- el guion no vale para esto"
    fallos=$((fallos + 1))
  fi
}

echo
echo "Sabotajes   (cada uno tiene que salir CAZADO)"
echo "======================================================================"

# El fallo real del 29-07-2026: un id que ya no existe dentro de `hideAll()`.
sabotear "id muerto en el JavaScript de Inicio" \
  app/static/js/app.js \
  's/"reco-card",/"reco-card", "metricas-card",/' \
  inicio

sabotear "id renombrado en la plantilla de Inicio" \
  app/templates/index.html \
  's/id="place-label"/id="place-label-roto"/' \
  inicio

sabotear "endpoint de la ruta cambiado de sitio" \
  app/app.py \
  's|@app.route("/api/ruta", methods=\["GET"\])|@app.route("/api/ruta-roto", methods=["GET"])|' \
  mapa

sabotear "contenedor del mapa sin su id" \
  app/templates/mapa.html \
  's/id="mapa"/id="mapa-roto"/' \
  mapa

sabotear "aviso de telemetría parada fuera del Perfil" \
  app/templates/perfil.html \
  's/id="cuerpo-parada"/id="cuerpo-parada-roto"/' \
  perfil

# El sexto no toca ningún archivo: ocupa el puerto. Es el sabotaje que imita a
# una verificación anterior que no acabó de morir, y sin la comprobación previa
# el guion se conectaba a ESE servidor —otro código, otra base de datos— y
# contaba lo que viera como si fuera lo del disco.
printf '  %-46s' "puerto 5099 ocupado por otro servidor"
salida=$("$PY" - <<'PY' 2>&1
import socket, subprocess, sys
escucha = socket.socket()
escucha.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
escucha.bind(("127.0.0.1", 5099))
escucha.listen(5)
try:
    hecho = subprocess.run(
        [sys.executable, "tools/verificar.py", "--solo", "perfil"],
        capture_output=True, text=True, timeout=180,
    )
finally:
    escucha.close()
print(hecho.stdout.strip())
sys.exit(hecho.returncode)
PY
)
if [ $? -ne 0 ]; then
  echo "CAZADO"
  echo "$salida" | grep -i "ocupado" | head -2 | sed 's/^/       /'
else
  echo "SE ESCAPÓ  <-- verificó contra el servidor de otro"
  fallos=$((fallos + 1))
fi

echo "======================================================================"
if [ "$fallos" -eq 0 ]; then
  echo "El guion caza los seis. Sirve para lo que se hizo."
  echo
  exit 0
fi
echo "$fallos sabotaje(s) pasaron desapercibidos: el guion tiene un agujero ahí."
echo
exit 1
