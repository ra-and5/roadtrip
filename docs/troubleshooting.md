# Qué mirar cuando algo falla

Pensado para consultarse en una gasolinera de Asturias, con prisa y sin ganas de
depurar. Busca tu síntoma y sigue la columna de la derecha.

**Lo primero, siempre, y resuelve la mitad de los casos:**

```bash
# En una consola Bash de PythonAnywhere:
cd ~/roadtrip && python tools/diagnostico.py 43.5622 -6.1456
```

Te dice **qué pieza** está rota, no solo que hay un error. Y desde el móvil:

```
https://TU_USUARIO.pythonanywhere.com/healthz
```

Los errores del servidor están en *Web* → **Error log** (los de arranque) y
*Server log* (los de ejecución).

---

## Tabla de síntomas

| Síntoma | Causa más probable | Qué mirar |
|---|---|---|
| **La app no carga.** El navegador no llega, o da "no se puede conectar" | La web app está parada, o expiró (las cuentas gratuitas caducan cada 3 meses y hay que pulsar un botón para renovarlas) | Pestaña *Web*: ¿está en verde? ¿Sale el aviso de "expired"? Pulsa **Reload**. Comprueba que la URL es exactamente `TU_USUARIO.pythonanywhere.com` |
| **Vuelve al login en bucle**, sin mensaje de error, con la contraseña buena | *Force HTTPS* desactivado y estás entrando por `http://`. La cookie de sesión sale marcada `Secure` y el navegador la descarta **en silencio** | *Web* → **Force HTTPS: activado** → *Reload*. Entra otra vez escribiendo `https://` a mano. (Solo si de verdad necesitas `http`, se puede poner `SESSION_COOKIE_SECURE=0`, pero nunca en el servidor) |
| **Error 500 nada más arrancar** | Falta una variable obligatoria en el `.env`, o el `.env` no está donde la app lo busca | *Error log*, última línea. Si pone `Falta la variable de entorno obligatoria: X`, ya sabes cuál. Comprueba que el archivo es `~/roadtrip/.env` y no `~/.env`, y que el WSGI apunta a `/home/TU_USUARIO/roadtrip` |
| **Error 500 después de haber funcionado** | Casi siempre un cambio sin *Reload*, o la cuota de disco llena (la BD no puede escribir) | *Error log*. Cuota: `du -sh ~` y la barra de la pestaña *Files*. Pulsa *Reload* |
| **El GPS no pide permiso.** Pulsas y no pasa nada | No estás en HTTPS. `navigator.geolocation` está bloqueado y **no da error visible** | Mira el candado en la barra de direcciones. Si pone `http://`, ahí está. Activa *Force HTTPS* |
| **"Has denegado el permiso de ubicación"** | Se pulsó "No permitir". iOS **no vuelve a preguntar** por ese sitio | Ajustes → Safari → Ubicación → *Preguntar* o *Permitir*. También Ajustes → Privacidad → Localización → Safari. Cierra la pestaña y vuelve a abrir |
| **"El GPS tardó demasiado"** | Timeout de 15 s sin fix de satélite | Sal a cielo abierto. Dentro del coche con el móvil en la guantera es el caso típico. Si urge, abre Mapas de Apple para forzar un fix y vuelve |
| **El CSS no se aplica.** Todo texto plano | Los *Static files* no están configurados, o apuntan mal | *Web* → *Static files*: URL `/static/` → Directory `/home/TU_USUARIO/roadtrip/app/static/` (con la barra final). Prueba `curl -I https://.../static/css/style.css`: un 404 lo confirma. *Reload* |
| **La IA no responde pero el resto sí.** Sale ubicación, tiempo y POIs, con el aviso *"Sin recomendación de IA"* | El proveedor falla: sin key, key mala, cuota agotada, o el proxy bloquea el host | `/healthz`: si `ia_configurada` es `false` es el `.env` (`LLM_PROVIDER` o `GEMINI_API_KEY`). Si es `true`, corre el diagnóstico en el servidor: ahí sale el **error crudo** del proveedor |
| **"Cuota agotada" / error 429 con Gemini** | Límite **por minuto** de la capa gratuita | Espera un minuto y vuelve a pulsar. No hay reintento automático a propósito: reintentar dentro de la misma petición choca contra el mismo muro y bloquea la app 20-60 s. Si es persistente, prueba otro modelo con `python tools/listar_modelos.py` |
| **Error 429 con Kimi** | **Tres causas distintas que se arreglan al revés.** El mensaje de la app dice cuál | *Vas demasiado rápido* (con 1 $ son 3 peticiones/min) → espera un minuto. *Servidores saturados* → espera, no es cosa tuya. ***Sin saldo*** → **esperar no arregla nada**: recarga en platform.kimi.ai, o pasa a `LLM_PROVIDER=gemini` (gratis) mientras tanto. Consulta el saldo con `python tools/diagnostico.py`; con `kimi-k3` cada recomendación cuesta ~0,03 $, y `KIMI_MODEL=kimi-k2.6` sale 5 veces más barato |
| **Falta el tiempo, o los POIs, o ambos**, con su aviso | La API está caída, o el proxy del plan gratuito la bloquea | Diagnóstico **en el servidor**. Un **403 con HTML** es el proxy, no la API (ver abajo) |
| **Todo va lentísimo** (más de 40 s) | Overpass lento (es un servicio comunitario gratuito) más el modelo | Es en parte normal: peor caso medido de Overpass 13,7 s, más 10-14 s de Gemini. A los 150 s corta solo. Si es sistemático, mira los *CPU seconds* de la pestaña *Web*: agotada la cuota diaria, la cuenta gratuita te ralentiza a propósito |
| **La segunda consulta en el mismo sitio también tarda** | La caché no está escribiendo: `DATA_DIR` mal o sin permisos | `ls -la ~/roadtrip/data/`. Debe existir `roadtrip.db` y crecer. Comprueba que `DATA_DIR` del `.env` es una ruta **absoluta** |
| **Los datos son de otro sitio** | Coordenadas malas, o caché de una ubicación anterior | La tarjeta de ubicación enseña tus coordenadas: contrástalas. La caché redondea a ~110 m, así que a menos de eso es el mismo sitio a efectos de la app |

---

## El caso especial del plan gratuito: el proxy

Las cuentas gratuitas de PythonAnywhere sacan **todo** el tráfico saliente por un
proxy con **lista blanca de dominios**. Esto merece sección propia porque no se
parece a un fallo de red:

- No es un timeout ni un "connection refused".
- Es un **403 con cuerpo HTML** que devuelve el proxy, no la API.
- La app lo interpreta como "esta fuente ha fallado" y **degrada en silencio**:
  la app parece funcionar, solo que sin tiempo, sin POIs o sin IA, con su aviso.

Hosts que necesita la app:

| Host | Sin él… |
|---|---|
| `nominatim.openstreetmap.org` | **La app no funciona.** Es la única fuente imprescindible |
| `api.open-meteo.com` · `marine-api.open-meteo.com` | Sin tiempo ni oleaje |
| `overpass-api.de` · `overpass.kumi.systems` · `overpass.private.coffee` | Sin puntos de interés verificados; el modelo tira de conocimiento general |
| `generativelanguage.googleapis.com` | Sin recomendaciones (Gemini) |
| `api.anthropic.com` | Solo si cambias a `LLM_PROVIDER=anthropic` |
| `api.moonshot.ai` | Solo si cambias a `LLM_PROVIDER=kimi` |

`api.anthropic.com`, `api.moonshot.ai` y `api.moonshot.cn` están **confirmados**
en la lista blanca (verificado sobre la página de la lista, no preguntando). Los
demás hay que comprobarlos con el diagnóstico en el servidor.

**Cómo confirmarlo** desde una consola Bash del servidor:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://api.open-meteo.com/v1/forecast
```

Un `403` donde el mismo comando en tu portátil da `400` o `200` es el proxy. Se
solicita añadir un dominio en el foro de PythonAnywhere.

---

## Antes de dar nada por roto

1. **¿Pulsaste *Reload*?** Los cambios en el `.env` y en el código no surten
   efecto hasta entonces. Es el fallo más frecuente, con diferencia.
2. **¿Estás en HTTPS?** Explica el bucle de login y el GPS mudo a la vez.
3. **Corre el diagnóstico en el servidor**, no en el portátil. La mitad de los
   problemas de despliegue son cosas que en tu máquina funcionan.

## Si nada de esto encaja

Activa temporalmente el detalle de errores en el `.env` del servidor:

```bash
SHOW_AI_ERROR_DETAIL=1
```

y pulsa *Reload*. La interfaz pasará a enseñar el mensaje crudo del proveedor en
vez de uno genérico. **Desactívalo cuando termines**: expone detalles de
infraestructura a cualquiera que abra la app.

La API key **nunca** aparece en un mensaje de error, con esto activado o
desactivado; de eso se encarga `llm_providers.redact()`, no este interruptor.
