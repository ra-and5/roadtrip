"""Tests de la medida de disco de `tools/diagnostico.py`.

Un diagnóstico sin tests parece lo último que merece pruebas, y aquí es al
revés: esta comprobación **ya estuvo rota meses sin que nadie lo notara**.
Preguntaba por el espacio libre del volumen (`shutil.disk_usage`), y en
PythonAnywhere eso son 1,6 TB, porque la cuota de 512 MB es un límite de la
cuenta impuesto aparte del sistema de archivos. Resultado: el aviso de "por
debajo de 50 MB" no podía saltar jamás, y aun así el diagnóstico imprimía una
línea verde y tranquilizadora en cada ejecución.

Es el fallo silencioso de la decisión 11 en el peor sitio posible —la
herramienta con la que compruebas si el despliegue está sano— y lo que se fija
aquí es lo que impide que vuelva:

  - se mide lo que ocupa de verdad (bloques), que es lo que cuenta una cuota;
  - un enlace duro no se cuenta dos veces;
  - el aviso salta por debajo del umbral, y no salta por encima.

Sin red, sin API keys y sin tocar la base de datos: todo pasa en un `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

# `tools/` son scripts, no un paquete: se cargan por ruta, igual que en
# `test_simulador.py`. Convertir tools/ en paquete solo para poder testear una
# función sería peor.
_RUTA = Path(__file__).resolve().parent.parent / "tools" / "diagnostico.py"
_spec = importlib.util.spec_from_file_location("diagnostico", _RUTA)
assert _spec and _spec.loader
diagnostico = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diagnostico)


def _escribir(ruta: Path, kib: int) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"\0" * kib * 1024)


def test_mide_lo_que_hay_dentro_y_no_el_volumen(tmp_path: Path) -> None:
    """Lo que se pregunta es "cuánto ocupo yo", no "cuánto cabe en el disco".

    Un tera libre en la máquina no dice nada cuando el límite que te frena son
    512 MB de cuota de la cuenta.
    """
    _escribir(tmp_path / "a.bin", 400)
    _escribir(tmp_path / "sub" / "b.bin", 600)

    medido = diagnostico.uso_mb([tmp_path])

    # ~1 MiB de contenido. El margen es por el redondeo a bloques del sistema de
    # archivos, que es justamente lo que esta función NO ignora.
    assert 0.9 < medido < 1.2


def test_un_enlace_duro_no_se_cuenta_dos_veces(tmp_path: Path) -> None:
    """Dos nombres, un solo juego de bloques.

    Contarlo dos veces inventaría ocupación, y un aviso de disco que salta sin
    motivo se aprende a ignorar — con lo que el día que sea de verdad tampoco
    se mirará.
    """
    original = tmp_path / "grande.bin"
    _escribir(original, 1024)
    solo_original = diagnostico.uso_mb([tmp_path])

    os.link(original, tmp_path / "copia.bin")

    assert diagnostico.uso_mb([tmp_path]) == pytest.approx(solo_original)


def test_una_ruta_que_no_existe_no_revienta(tmp_path: Path) -> None:
    """`UPLOAD_DIR` no existe hasta la primera foto, y eso no es un error.

    Esta función corre dentro del diagnóstico, que existe para cuando las cosas
    ya van mal: reventar aquí escondería el resto de las comprobaciones.
    """
    assert diagnostico.uso_mb([tmp_path / "no-existe"]) == 0.0


def test_el_aviso_salta_por_debajo_del_umbral() -> None:
    """El umbral existe para avisar ANTES del problema, no a la vez.

    Los MB se calculan contra `MIN_DISCO_MB` y no contra un 50 escrito a mano:
    si alguien mueve el umbral, este test sigue comprobando lo mismo.
    """
    cuota = 512.0
    usado = cuota - diagnostico.MIN_DISCO_MB + 10  # quedan 40, y el umbral es 50

    with pytest.raises(RuntimeError, match="libera sitio"):
        diagnostico.libres_mb(usado, cuota)


def test_con_margen_no_avisa_y_dice_cuanto_queda() -> None:
    """Y no avisar es la mitad del contrato: un aviso permanente se ignora."""
    cuota = 512.0
    usado = 118.0

    assert diagnostico.libres_mb(usado, cuota) == pytest.approx(394.0)


def test_hace_cuanto_contesta_la_pregunta_que_se_hace_uno() -> None:
    """"¿esto sigue llegando?" no la contesta una marca ISO.

    En una consola del servidor, `2026-07-28T21:32:11+00:00` obliga a mirar la
    hora, restar el huso y hacer la cuenta — que es justo el trabajo que un
    diagnóstico existe para ahorrarte.
    """
    ahora = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

    assert diagnostico.hace_cuanto("2026-07-29T11:30:00+00:00", ahora) == "hace 30 min"
    assert diagnostico.hace_cuanto("2026-07-29T04:00:00+00:00", ahora) == "hace 8 h"
    assert diagnostico.hace_cuanto("2026-07-26T12:00:00+00:00", ahora) == "hace 3 días"
    assert diagnostico.hace_cuanto(None, ahora) == "nunca"


def test_una_muestra_en_el_futuro_se_dice_en_vez_de_disimularse() -> None:
    """Un reloj mal puesto en el móvil es un dato, no un adorno.

    Sin este caso, una muestra fechada mañana saldría como "hace -3 h", que se
    lee como recentísima y tranquiliza justo cuando no debe: el que la envía
    tiene la hora mal y todo lo que dependa del día local está corrido.
    """
    ahora = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

    assert "FUTURO" in diagnostico.hace_cuanto("2026-07-30T12:00:00+00:00", ahora)


def test_una_marca_sin_huso_se_lee_como_UTC() -> None:
    """Es lo que guarda la base de datos, y suponer hora local la desplazaría
    dos horas en España sin dar ningún error."""
    ahora = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

    assert diagnostico.hace_cuanto("2026-07-29T10:00:00", ahora) == "hace 2 h"


def test_la_cuota_no_se_pregunta_al_sistema_de_archivos() -> None:
    """El bug original, fijado donde se pueda ver.

    Este test corre en una máquina con cientos de GB libres y aun así el aviso
    tiene que saltar, porque lo que frena no es el volumen sino la cuota de la
    cuenta. Volver a `shutil.disk_usage()` lo pondría en rojo.
    """
    with pytest.raises(RuntimeError):
        diagnostico.libres_mb(usado_mb=500.0, cuota_mb=512.0)


def test_el_virtualenv_no_se_cuenta_dos_veces_si_esta_dentro_del_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un `.venv/` en la raíz ya lo suma el recorrido del repositorio.

    En el servidor el virtualenv vive fuera (`~/.virtualenvs/`) y hay que
    sumarlo aparte; en local suele estar dentro. Sumarlo en los dos casos daría
    el doble justo del mayor inquilino de la cuota —~101 MB de 512— y el aviso
    saltaría con la mitad del disco libre.
    """
    monkeypatch.setattr(diagnostico, "BASE_DIR", tmp_path)
    monkeypatch.setattr(diagnostico.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(diagnostico.sys, "base_prefix", "/usr")

    assert diagnostico._raiz_venv() is None


def test_sin_virtualenv_no_se_mide_el_python_del_sistema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sys.prefix == sys.base_prefix` significa que no hay virtualenv.

    Medirlo entonces sería recorrer un miniconda de varios GB que no tiene nada
    que ver con este proyecto, y dar por ocupada una cuota que está vacía.
    """
    monkeypatch.setattr(diagnostico.sys, "prefix", "/usr")
    monkeypatch.setattr(diagnostico.sys, "base_prefix", "/usr")

    assert diagnostico._raiz_venv() is None


# ---------------------------------------------------------------------------
# El veredicto final
# ---------------------------------------------------------------------------


def test_un_contexto_lento_no_dice_que_falle_la_ubicacion() -> None:
    """El fallo que se vio en el servidor y mandó a depurar lo que no era.

    `contexto.construir()` devolvió su ubicación y sus 6/6 fuentes, y solo
    incumplió el contrato de tiempo. El veredicto anunciaba, dos líneas más
    abajo, "la ubicación no se puede resolver". Eso no es un matiz: es la
    herramienta de diagnóstico nombrando mal el fallo, que es peor que callarse.
    """
    lineas = diagnostico.veredicto(ok=True, contexto_ok=False, todo_fino=False, lento="33.8s")
    texto = " ".join(lineas)

    assert "33.8s" in texto
    assert "ubicación no se puede resolver" not in texto
    assert "medir_contexto" in texto, "hay que decir con qué se averigua cuál es"


def test_sin_ubicacion_si_se_dice_que_la_app_no_sirve() -> None:
    """Y el caso de verdad grave no se puede haber ablandado al arreglar el otro."""
    lineas = diagnostico.veredicto(ok=False, contexto_ok=False, todo_fino=False, lento="")

    assert "no será utilizable" in " ".join(lineas)


def test_una_fuente_opcional_caida_es_modo_degradado_y_no_un_fallo() -> None:
    """Degradar es un estado diseñado a propósito (decisión 9), no un roto."""
    lineas = diagnostico.veredicto(ok=True, contexto_ok=True, todo_fino=False, lento="")

    assert "degradado" in " ".join(lineas)


def test_con_todo_en_verde_no_se_avisa_de_nada() -> None:
    assert diagnostico.veredicto(
        ok=True, contexto_ok=True, todo_fino=True, lento=""
    ) == ["Todo correcto."]
