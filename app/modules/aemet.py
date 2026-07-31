"""AEMET OpenData para el modo copiloto territorial.

La app ya sabe el tiempo donde estás con Open-Meteo. Esta capa responde otra
pregunta: "¿qué está pasando en España alrededor de mí o de la ruta?". AEMET es
la fuente oficial para avisos, predicción nacional y radar nacional, pero exige
API key. Si falta, se devuelve un aviso; no se inventa un mapa meteorológico.
"""

from __future__ import annotations

import re
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import requests

from app.config import Config
from app.modules import storage

_BASE_URL = "https://opendata.aemet.es/opendata/api"
_CACHE_TTL = 30 * 60
_NACIONAL_HOY = "/prediccion/nacional/hoy"
_NACIONAL_MANANA = "/prediccion/nacional/manana"
_AVISOS_ESPANA = "/avisos_cap/ultimoelaborado/area/esp"
_RADAR_NACIONAL = "/red/radar/nacional"


class AemetError(Exception):
    """AEMET no pudo devolver el dato pedido."""


@dataclass(frozen=True)
class AemetInforme:
    """Bloque compacto para que lo lea el LLM."""

    prediccion: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    radar: str = ""
    avisos_herramienta: list[str] = field(default_factory=list)

    def hay_algo(self) -> bool:
        return bool(self.prediccion or self.avisos or self.radar or self.avisos_herramienta)


def _limpiar(texto: Any, *, max_chars: int = 900) -> str:
    limpio = re.sub(r"\s+", " ", str(texto or "")).strip()
    if len(limpio) <= max_chars:
        return limpio
    return limpio[: max_chars - 1].rstrip() + "..."


class AemetClient:
    """Cliente mínimo de AEMET OpenData, sin SDK ni dependencias nuevas."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else Config.AEMET_API_KEY

    def _require_key(self) -> str:
        if not self.api_key:
            raise AemetError("AEMET_API_KEY no está configurada.")
        return self.api_key

    def _endpoint(self, path: str) -> Any:
        key = "aemet:endpoint:" + path
        cached = storage.cache_get(key, _CACHE_TTL)
        if cached is not None:
            return cached

        try:
            response = requests.get(
                _BASE_URL + path,
                params={"api_key": self._require_key()},
                timeout=Config.HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise AemetError("AEMET tardó demasiado.") from exc
        except requests.RequestException as exc:
            raise AemetError("No se pudo consultar AEMET OpenData.") from exc
        except ValueError as exc:
            raise AemetError("AEMET devolvió una respuesta inicial ilegible.") from exc

        storage.cache_set(key, payload)
        return payload

    def _descargar_datos(self, path: str) -> str:
        inicial = self._endpoint(path)
        datos_url = inicial.get("datos") if isinstance(inicial, dict) else ""
        if not datos_url:
            descripcion = inicial.get("descripcion") if isinstance(inicial, dict) else ""
            raise AemetError(_limpiar(descripcion or "AEMET no devolvió URL de datos."))

        key = "aemet:datos:" + datos_url
        cached = storage.cache_get(key, _CACHE_TTL)
        if cached is not None:
            return str(cached)

        try:
            response = requests.get(datos_url, timeout=Config.HTTP_TIMEOUT)
            response.raise_for_status()
            texto = response.text
        except requests.Timeout as exc:
            raise AemetError("La descarga de datos de AEMET tardó demasiado.") from exc
        except requests.RequestException as exc:
            raise AemetError("No se pudo descargar el recurso de AEMET.") from exc

        storage.cache_set(key, texto)
        return texto

    def prediccion_nacional(self) -> list[str]:
        lineas: list[str] = []
        for etiqueta, path in (("hoy", _NACIONAL_HOY), ("mañana", _NACIONAL_MANANA)):
            texto = self._descargar_datos(path)
            for linea in _parse_prediccion_textual(texto):
                lineas.append(f"{etiqueta}: {linea}")
        return lineas[:4]

    def avisos_espana(self) -> list[str]:
        return _parse_avisos_cap(self._descargar_datos(_AVISOS_ESPANA))[:8]

    def radar_nacional(self) -> str:
        inicial = self._endpoint(_RADAR_NACIONAL)
        datos_url = inicial.get("datos") if isinstance(inicial, dict) else ""
        if not datos_url:
            raise AemetError("AEMET no devolvió imagen de radar nacional.")
        return f"Radar nacional disponible: {datos_url}"


def _parse_prediccion_textual(texto: str) -> list[str]:
    """Acepta JSON de AEMET o texto plano; devuelve frases compactas."""
    try:
        payload = json.loads(texto)
    except ValueError:
        return [_limpiar(texto)] if texto.strip() else []

    candidatos: list[str] = []

    def visitar(valor: Any) -> None:
        if isinstance(valor, dict):
            for clave, subvalor in valor.items():
                if clave.lower() in {"texto", "prediccion", "resumen", "descripcion"}:
                    if isinstance(subvalor, str) and subvalor.strip():
                        candidatos.append(_limpiar(subvalor))
                visitar(subvalor)
        elif isinstance(valor, list):
            for item in valor:
                visitar(item)

    visitar(payload)
    return candidatos or [_limpiar(texto)]


def _texto_xml(elem: ET.Element, nombre: str) -> str:
    encontrado = elem.find(".//{*}" + nombre)
    return _limpiar(encontrado.text if encontrado is not None else "")


def _parse_avisos_cap(texto: str) -> list[str]:
    """Parsea CAP XML. Si AEMET cambia el envoltorio, falla hacia aviso útil."""
    if not texto.strip():
        return []
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError:
        return [_limpiar(texto)]

    alertas = raiz.findall(".//{*}alert")
    if raiz.tag.endswith("alert"):
        alertas.append(raiz)
    salida: list[str] = []
    for alerta in alertas:
        info = alerta.find(".//{*}info")
        if info is None:
            continue
        evento = _texto_xml(info, "event")
        severidad = _texto_xml(info, "severity")
        urgencia = _texto_xml(info, "urgency")
        zona = _texto_xml(info, "areaDesc")
        inicio = _texto_xml(info, "onset")
        fin = _texto_xml(info, "expires")
        partes = [p for p in (evento, severidad, urgencia, zona, inicio, fin) if p]
        if partes:
            salida.append(" · ".join(partes))
    return salida


def informe_territorio(*, incluir_radar: bool = False, client: AemetClient | None = None) -> AemetInforme:
    """Consulta AEMET para el chat, degradando cada pieza por separado."""
    client = client or AemetClient()
    prediccion: list[str] = []
    avisos: list[str] = []
    radar = ""
    errores: list[str] = []

    try:
        prediccion = client.prediccion_nacional()
    except AemetError as exc:
        errores.append(f"predicción nacional: {exc}")
    try:
        avisos = client.avisos_espana()
        if not avisos:
            avisos = ["Sin avisos CAP activos devueltos por AEMET para España."]
    except AemetError as exc:
        errores.append(f"avisos España: {exc}")
    if incluir_radar:
        try:
            radar = client.radar_nacional()
        except AemetError as exc:
            errores.append(f"radar nacional: {exc}")

    return AemetInforme(
        prediccion=prediccion,
        avisos=avisos,
        radar=radar,
        avisos_herramienta=errores,
    )


def formatear(informe: AemetInforme) -> list[str]:
    """Líneas listas para `map_tools.ToolBundle.lecturas`."""
    if not informe.hay_algo():
        return []
    lineas = ["AEMET_TERRITORIO:"]
    if informe.prediccion:
        lineas.append("Predicción nacional:")
        lineas.extend("- " + linea for linea in informe.prediccion)
    if informe.avisos:
        lineas.append("Avisos oficiales:")
        lineas.extend("- " + linea for linea in informe.avisos)
    if informe.radar:
        lineas.append("- " + informe.radar)
    if informe.avisos_herramienta:
        lineas.append("Avisos de herramienta:")
        lineas.extend("- " + aviso for aviso in informe.avisos_herramienta)
    return lineas
