#!/usr/bin/env python3
"""Semáforo antes de desplegar o estrenar la app.

`tools/verificar.py` responde otra pregunta: "¿el código funciona en un
navegador?". Para hacerlo sin red ni API keys arranca una app temporal en `/tmp`
y siembra datos falsos de Cudillero. Eso está bien para probar código, pero NO
sirve para decidir si tu instalación real está limpia.

Este guion mira lo que importa antes de hacer `git push`, `git pull` o abrir la
app en el iPhone: que no haya datos simulados, que la PWA esté completa sin
service worker, que los tests pasen si se piden, y que el árbol de git esté en
el estado esperado.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.config import Config  # noqa: E402
from app.modules import miniaturas, storage  # noqa: E402
from tools import estado_limpio  # noqa: E402

VERDE = "\033[32m"
AMARILLO = "\033[33m"
ROJO = "\033[31m"
GRIS = "\033[90m"
FIN = "\033[0m"


class Resultado:
    def __init__(self) -> None:
        self.fallos = 0
        self.avisos = 0

    def ok(self, nombre: str, detalle: str = "") -> None:
        print(f"  {nombre:.<34} {VERDE}OK{FIN}     {detalle}")

    def aviso(self, nombre: str, detalle: str) -> None:
        self.avisos += 1
        print(f"  {nombre:.<34} {AMARILLO}AVISO{FIN}  {detalle}")

    def fallo(self, nombre: str, detalle: str) -> None:
        self.fallos += 1
        print(f"  {nombre:.<34} {ROJO}FALLO{FIN}  {detalle}")


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=RAIZ,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def comprobar_git(res: Resultado, *, exigir_limpio: bool) -> None:
    """Estado de git: antes de desplegar, lo que no esté comitteado no existe."""
    rama = _git(["branch", "--show-current"]).stdout.strip() or "(sin rama)"
    head = _git(["log", "-1", "--oneline"]).stdout.strip()
    estado = _git(["status", "--short"]).stdout.strip().splitlines()

    res.ok("rama", rama)
    res.ok("último commit", head)

    if estado:
        muestra = ", ".join(linea.strip() for linea in estado[:5])
        if len(estado) > 5:
            muestra += f", ... (+{len(estado) - 5})"
        detalle = f"{len(estado)} cambios sin commit: {muestra}"
        if exigir_limpio:
            res.fallo("git limpio", detalle + "; el servidor no los verá con pull")
        else:
            res.aviso("git limpio", detalle + "; correcto si aún estás preparando el commit")
    else:
        res.ok("git limpio", "todo lo local está en el commit")


def comprobar_pwa(res: Resultado) -> None:
    """Manifest e iconos locales. Sin service worker por decisión de proyecto."""
    manifest = RAIZ / "app/static/manifest.webmanifest"
    if not manifest.is_file():
        res.fallo("manifest PWA", "falta app/static/manifest.webmanifest")
        return

    try:
        datos = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        res.fallo("manifest PWA", f"JSON inválido: {exc}")
        return

    iconos = datos.get("icons") or []
    faltan = []
    for icono in iconos:
        src = str(icono.get("src", "")).lstrip("/")
        # En el manifest la URL pública es /static/..., pero en el repo Flask lo
        # sirve desde app/static/.... Mirar RAIZ/static daría un fallo falso
        # justo en el semáforo que existe para evitar falsos sustos.
        ruta = RAIZ / "app" / src if src.startswith("static/") else RAIZ / src
        if not ruta.is_file():
            faltan.append(icono.get("src", "(sin src)"))
    if faltan:
        res.fallo("iconos PWA", "faltan: " + ", ".join(faltan))
    else:
        res.ok("PWA", f"{len(iconos)} iconos locales, display={datos.get('display')}")

    textos = []
    for ruta in (RAIZ / "app").rglob("*"):
        if ruta.is_file() and ruta.suffix in {".html", ".js"}:
            textos.append(ruta.read_text(encoding="utf-8", errors="ignore"))
    unido = "\n".join(textos)
    if "serviceWorker" in unido or "service-worker" in unido or "sw.js" in unido:
        res.fallo("service worker", "hay referencias; no entra sin plan de invalidación")
    else:
        res.ok("service worker", "no registrado")


def comprobar_datos(res: Resultado, *, exigir_viaje_vacio: bool) -> None:
    """Datos reales de ESTA instalación, no los de `tools/verificar.py`."""
    storage.init_db()
    datos = estado_limpio.inventario()
    minis = estado_limpio._miniaturas_en_disco()  # noqa: SLF001 - herramienta hermana

    res.ok("base de datos", str(Config.DB_PATH))
    viaje = datos["notas"] + datos["fotos"] + datos["chat"] + datos["dias"]
    detalle = (
        f"{datos['notas']} notas, {datos['fotos']} fotos, {minis} miniaturas, "
        f"{datos['chat']} mensajes, {datos['dias']} días"
    )
    if exigir_viaje_vacio and viaje:
        res.fallo("datos del viaje", detalle + "; usa --borrar-todo-el-viaje si SON pruebas")
    else:
        res.ok("datos del viaje", detalle)

    if datos["telemetria_simulada"]:
        res.fallo(
            "telemetría simulada",
            f"{datos['telemetria_simulada']} muestras; limpia con python tools/estado_limpio.py --limpiar",
        )
    else:
        res.ok("telemetría simulada", "0")

    if datos["telemetria_otras"]:
        res.aviso("otras fuentes telemetría", str(datos["telemetria_otras"]))

    res.ok("miniaturas", f"{miniaturas.usado_mb():.1f} MB usados")


def comprobar_python(res: Resultado) -> None:
    """La consola debe usar el mismo virtualenv que la web app."""
    if sys.prefix == sys.base_prefix:
        res.aviso(
            "virtualenv",
            "no activo; activa ~/.virtualenvs/roadtrip antes de diagnosticar o instalar",
        )
        return
    res.ok("virtualenv", sys.prefix)


def ejecutar(res: Resultado, nombre: str, comando: list[str]) -> None:
    proc = subprocess.run(
        comando,
        cwd=RAIZ,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode == 0:
        ultima = [linea for linea in proc.stdout.splitlines() if linea.strip()][-1:]
        res.ok(nombre, ultima[0] if ultima else "")
    else:
        salida = "\n".join(proc.stdout.splitlines()[-8:])
        res.fallo(nombre, salida or f"código {proc.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--para-commit",
        action="store_true",
        help="exige git limpio; úsalo después de commitear y antes de push/pull",
    )
    parser.add_argument(
        "--estrenar",
        action="store_true",
        help="exige que no haya notas, fotos, chat ni días registrados",
    )
    parser.add_argument("--tests", action="store_true", help="corre pytest -q")
    parser.add_argument(
        "--navegador",
        action="store_true",
        help="corre tools/verificar.py; usa datos falsos en /tmp, no los tuyos",
    )
    args = parser.parse_args()

    res = Resultado()
    print("\nPre-despliegue")
    print("==============")
    print(f"{GRIS}Esto mira tu instalación real. Cudillero solo aparece en tools/verificar.py.{FIN}\n")

    comprobar_git(res, exigir_limpio=args.para_commit)
    comprobar_python(res)
    comprobar_pwa(res)
    comprobar_datos(res, exigir_viaje_vacio=args.estrenar)

    if args.tests:
        ejecutar(res, "pytest", [sys.executable, "-m", "pytest", "-q"])
    else:
        res.aviso("pytest", "saltado; añade --tests para correrlo aquí")

    if args.navegador:
        ejecutar(res, "verificación navegador", [sys.executable, "tools/verificar.py"])
    else:
        res.aviso("verificación navegador", "saltada; añade --navegador si estás en local")

    print()
    if res.fallos:
        print(f"{ROJO}No despliegues todavía: {res.fallos} fallo(s).{FIN}")
        return 1
    if res.avisos:
        print(f"{AMARILLO}Sin fallos, con {res.avisos} aviso(s) que debes entender antes de desplegar.{FIN}")
        return 0
    print(f"{VERDE}Listo para desplegar.{FIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
