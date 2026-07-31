"""Notificaciones locales del chat.

No hay service worker en este proyecto, así que esto no prueba push con la app
cerrada. Protege lo que sí se implementa: aviso local del navegador cuando la
respuesta llega y la PWA/pestaña está en segundo plano.
"""

from __future__ import annotations

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
CHAT_JS = RAIZ / "app" / "static" / "js" / "chat.js"


def test_chat_pide_permiso_y_lanza_notificacion_sin_service_worker() -> None:
    fuente = CHAT_JS.read_text(encoding="utf-8")

    assert "Notification.requestPermission()" in fuente
    assert 'new Notification("WhereAmAi respondió"' in fuente
    assert 'tag: "roadtrip-chat-respuesta"' in fuente
    assert "document.hidden" in fuente
    assert "serviceWorker" not in fuente
