"""Configuración central de la aplicación.

Regla de oro: NADA de secretos en este archivo. Todo sale de variables de
entorno. En local las cargamos de un `.env` (ver `_load_dotenv`), en
PythonAnywhere se definen en el panel de la web app.

Si vienes de C++: piensa en esto como un header de constantes de
configuración, pero cuyos valores se resuelven en tiempo de ejecución en vez
de en tiempo de compilación.
"""

from __future__ import annotations

import os
from pathlib import Path

# Raíz del proyecto: config.py está en <raiz>/app/config.py, así que subimos dos.
BASE_DIR: Path = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Carga un archivo .env en os.environ, sin dependencias externas.

    Usamos una implementación mínima en vez de python-dotenv porque solo
    necesitamos `CLAVE=valor` y una dependencia menos es una dependencia menos
    que instalar en PythonAnywhere.

    Importante: NO sobrescribe variables que ya existan en el entorno. Así, en
    producción, las variables reales del servidor siempre ganan sobre un .env
    que se haya subido por accidente.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    """Lee una variable de entorno.

    `required=True` hace que la app reviente al arrancar si falta, en vez de
    fallar a mitad de una petición con un error incomprensible. Es preferible
    fallar pronto y ruidosamente.
    """
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Falta la variable de entorno obligatoria: {name}. "
            f"Revisa tu .env (en local) o el panel de PythonAnywhere (en producción)."
        )
    return value or ""


_VALORES_FALSOS = ("0", "false", "no", "off")
_VALORES_VERDADEROS = ("1", "true", "yes", "si", "sí", "on")


def _env_bool(name: str, *, default: bool) -> bool:
    """Lee un interruptor del entorno.

    Vacío o sin definir se queda con `default`. Lo interesante es qué se hace
    con un valor que no se reconoce ("flase", "activado", "1 "), y la respuesta
    depende de hacia dónde es seguro equivocarse: un flag que por defecto está
    ACTIVADO solo se apaga con un "no" reconocible, y uno que por defecto está
    APAGADO solo se enciende con un "sí" reconocible. Así una errata nunca
    desprotege nada; como mucho no surte efecto, que se nota enseguida.

    Está como función y no repetido en cada atributo porque este parseo se
    puede probar sin arrancar la app: los tests lo llaman directamente en vez
    de reimportar el módulo, que es frágil (recargar `app.config` sustituye la
    clase `Config` y los módulos que ya la habían importado siguen con la
    antigua).
    """
    bruto = _env(name, default="").strip().lower()
    if not bruto:
        return default
    return bruto not in _VALORES_FALSOS if default else bruto in _VALORES_VERDADEROS


class Config:
    """Configuración de la app. Se lee una vez al importar el módulo."""

    # --- Flask ---
    # Firma la cookie de sesión. Si cambia, todas las sesiones se invalidan.
    SECRET_KEY: str = _env("SECRET_KEY", required=True)

    # --- Autenticación (un solo usuario: yo) ---
    # Guardamos el HASH de la contraseña, nunca la contraseña. Generarlo con:
    #   python tools/hash_password.py
    APP_PASSWORD_HASH: str = _env("APP_PASSWORD_HASH", required=True)

    # La cookie de sesión solo viaja por HTTPS. Activado por defecto: es la
    # cookie que da acceso a toda la app, y basta UNA petición por http:// para
    # que viaje en claro y quede capturable en cualquier wifi de camping. En
    # local tampoco estorba, porque los navegadores tratan `localhost` como
    # contexto seguro y envían cookies `Secure` igualmente.
    # Se desactiva solo para probar por http desde otro dispositivo de la red
    # local; nunca en el servidor. Un valor no reconocido deja el flag activado
    # a propósito: si te equivocas escribiéndolo, el fallo debe ser hacia el
    # lado seguro.
    SESSION_COOKIE_SECURE: bool = _env_bool("SESSION_COOKIE_SECURE", default=True)

    # --- Ingesta de telemetría (Fase 2d) ---
    # HASH del token que autentica al iPhone, NUNCA el token. Se genera con:
    #   python tools/token_ingesta.py
    # No es obligatoria: sin ella la app arranca igual y el endpoint devuelve
    # 401 a todo. Es una función opcional, no un requisito de arranque; el
    # diagnóstico avisa de que está sin configurar.
    #
    # Este token NO es la contraseña de la app, ni su hash. Son dos secretos
    # distintos a propósito: el del atajo vive en claro dentro del iPhone y se
    # rota solo (perder el móvil no obliga a cambiar la contraseña de la web).
    #
    # El hash lo genera la herramienta con PBKDF2 y no con el scrypt que
    # Werkzeug usa por defecto. El motivo está explicado en `token_ingesta.py`:
    # scrypt reserva ~32 MB por verificación, y este endpoint es público, así
    # que cualquiera puede provocar esa reserva a voluntad contra un worker de
    # PythonAnywhere gratuito. Se verifica con `check_password_hash`, que lee
    # el algoritmo del propio hash: un hash antiguo con scrypt sigue valiendo.
    INGEST_TOKEN_HASH: str = _env("INGEST_TOKEN_HASH", default="")

    # Cuántas muestras se aceptan por petición. Con envíos cada hora y ventana
    # solapada de 6 h llegan ~6; 500 deja margen de sobra para recuperar un día
    # entero de fallos y sigue siendo un bucle de inserción trivial.
    INGEST_MAX_SAMPLES: int = int(_env("INGEST_MAX_SAMPLES", default="500"))

    # Techo del cuerpo de CUALQUIER petición, aplicado por Flask ANTES de
    # parsear el JSON. Ese "antes" es todo el motivo: el límite de muestras se
    # comprueba sobre una lista ya deserializada, y deserializar es justo donde
    # se gasta la CPU. En PythonAnywhere gratuito la CPU es cuota diaria, así
    # que un cuerpo de 50 MB no es una hipótesis académica: agotarla ralentiza
    # la app entera durante el resto del día.
    # 500 muestras ocupan ~50 KB; 128 KiB deja 2,5x de margen.
    # OJO Fase 3: subir fotos necesitará más. No se sube este número (dejaría
    # sin protección a la ingesta): se eleva por petición en esa ruta concreta
    # con `request.max_content_length`, que Flask 3.1 permite.
    MAX_CONTENT_LENGTH: int = int(_env("MAX_CONTENT_LENGTH", default=str(128 * 1024)))

    # --- Proveedor de LLM ---
    # Qué proveedor usa la app: anthropic | gemini | kimi | ollama (sin implementar).
    # Cambiarlo aquí es lo único que hace falta para cambiar de modelo: el
    # código de ai_orchestrator no distingue entre unos y otros.
    LLM_PROVIDER: str = _env("LLM_PROVIDER", default="anthropic").strip().lower()

    # Enseña en la INTERFAZ el mensaje de error crudo del proveedor. Cómodo
    # mientras afinas el prompt, inadecuado en producción (expone detalles de
    # infraestructura a cualquiera que abra la app). Desactivado por defecto.
    # El detalle completo va SIEMPRE al log y al diagnóstico, active o no.
    # Nota: la API key nunca aparece en un mensaje de error, con esto activado
    # o desactivado. Eso lo garantiza llm_providers.redact(), no este flag.
    SHOW_AI_ERROR_DETAIL: bool = _env_bool("SHOW_AI_ERROR_DETAIL", default=False)

    # --- Claude (Anthropic) ---
    ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY", default="")
    ANTHROPIC_MODEL: str = _env("ANTHROPIC_MODEL", default="claude-opus-5")
    # Cuánto razona el modelo antes de responder: low | medium | high | xhigh | max.
    # Es el mando de latencia contra calidad. "low" responde en pocos segundos;
    # "medium" hila mejor tiempo + hora + sitio. Pruébalo con tus datos reales.
    # Se normaliza aquí (espacios, mayúsculas) y se valida en ai_orchestrator
    # antes de cada llamada: un valor inválido debe dar un error que nombre la
    # variable, no un 400 críptico de la API.
    ANTHROPIC_EFFORT: str = _env("ANTHROPIC_EFFORT", default="low").strip().lower()

    # --- Gemini (Google AI Studio) ---
    # Capa gratuita sin tarjeta. La key se saca en aistudio.google.com
    # (Get API key > Create API key). El formato del prefijo VARÍA según cómo
    # se genere ("AIza...", "AQ...."): no lo uses para validar la clave, se
    # descarta una key buena. La única comprobación fiable es llamar a la API.
    GEMINI_API_KEY: str = _env("GEMINI_API_KEY", default="")
    # Modelo fijado a propósito, no un alias tipo "gemini-flash-latest": al
    # afinar un prompt necesitas reproducibilidad, y un alias cambia de modelo
    # bajo tus pies sin avisar. Comprobado contra la API: los "pro" agotan la
    # cuota gratuita enseguida, y gemini-2.5-flash ya no se sirve a cuentas
    # nuevas ("no longer available to new users"). Lista los disponibles para
    # tu key con: python tools/listar_modelos.py
    GEMINI_MODEL: str = _env("GEMINI_MODEL", default="gemini-3.6-flash").strip()

    # --- Kimi (Moonshot AI) ---
    # No hay capa gratuita: la key se activa con una recarga mínima de 1 $ (y al
    # llegar a 5 $ acumulados te abonan un vale de 5 $). Se saca en
    # platform.kimi.ai > Console > API Keys.
    KIMI_API_KEY: str = _env("KIMI_API_KEY", default="")
    # Modelo fijado, igual que en Gemini y por el mismo motivo (decisión 14):
    # afinar un prompt exige reproducibilidad. Se elige kimi-k3 porque es el
    # único modelo NO especializado en código que la documentación garantiza
    # como estable con salida estructurada; kimi-k2.6 es 4 veces más barato
    # pero avisan de que "se comporta de forma inestable con esquemas
    # complejos", y el nuestro tiene un array de objetos anidados con enums.
    KIMI_MODEL: str = _env("KIMI_MODEL", default="kimi-k3").strip()
    # Configurable porque Moonshot tiene plataformas regionales con endpoints
    # distintos y las keys NO son intercambiables entre ellas (una key de una
    # plataforma contra el endpoint de otra da un 401 que parece "key mala").
    # Sirve además para apuntar a una pasarela compatible con OpenAI.
    KIMI_BASE_URL: str = _env("KIMI_BASE_URL", default="https://api.moonshot.ai/v1").strip().rstrip("/")
    # Cuánto razona kimi-k3 antes de responder: low | high | max.
    # OJO: NO son los mismos valores que ANTHROPIC_EFFORT (que además acepta
    # medium, xhigh). Por defecto la API usa "max", que aquí sería tirar dinero:
    # el razonamiento se factura como tokens de salida, los más caros, y esto
    # es una recomendación turística, no una demostración de un teorema.
    KIMI_REASONING_EFFORT: str = _env("KIMI_REASONING_EFFORT", default="low").strip().lower()

    # --- Almacenamiento ---
    # data/ está en .gitignore: es estado de la app, no código.
    DATA_DIR: Path = Path(_env("DATA_DIR", default=str(BASE_DIR / "data")))
    DB_PATH: Path = DATA_DIR / "roadtrip.db"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"

    # --- APIs externas ---
    # La política de uso de Nominatim EXIGE un User-Agent identificable con
    # forma de contacto. Sin esto pueden bloquear tu IP.
    # https://operations.osmfoundation.org/policies/nominatim/
    NOMINATIM_USER_AGENT: str = _env(
        "NOMINATIM_USER_AGENT",
        default="roadtrip-companion/0.1 (proyecto-estudiante; contacto@example.com)",
    )
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/reverse"
    OVERPASS_URL: str = "https://overpass-api.de/api/interpreter"
    OPEN_METEO_URL: str = "https://api.open-meteo.com/v1/forecast"

    # Timeout para CUALQUIER llamada HTTP saliente. Sin timeout, una API caída
    # cuelga el worker de Flask indefinidamente y la app deja de responder.
    HTTP_TIMEOUT: float = float(_env("HTTP_TIMEOUT", default="10"))

    # Cuántos decimales conservamos al cachear por coordenada.
    # 3 decimales ~= 110 m. Suficiente para "estoy en el mismo sitio".
    CACHE_COORD_PRECISION: int = 3

    @classmethod
    def ensure_dirs(cls) -> None:
        """Crea los directorios de datos si no existen. Idempotente."""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
