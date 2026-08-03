/* Pantalla de conversación.
 *
 * Igual que el resto del frontend del proyecto: sin framework, sin build, y con
 * el estado mínimo. Lo único que esta página guarda en memoria es la última
 * posición conocida; los mensajes viven en el servidor y se piden al abrir.
 *
 * Dos decisiones que no se ven en el código y conviene tener escritas:
 *
 * - **El historial NO se manda desde aquí.** Se manda solo la pregunta, y el
 *   servidor recompone la conversación desde su tabla. Si lo mandara el
 *   navegador, el modelo estaría razonando sobre lo que diga el cliente, que es
 *   la misma trampa que ya se evitó con el contexto en `/api/recommendations`.
 * - **No hay cola offline, al revés que en las notas.** Una nota escrita en un
 *   mirador sin cobertura no existe en ningún otro sitio y hay que salvarla
 *   (decisión 26). Una pregunta sin respuesta no vale nada: sin red no hay
 *   modelo, así que reintentarla más tarde daría una respuesta sobre un sitio
 *   en el que ya no estás. Se falla y se dice.
 */
(function () {
  "use strict";

  var posicion = null;
  var permisoNotificacionesPedido = false;
  var notificacionesPermitidas = false;
  var promesaPermisoNotificaciones = null;
  var ultimaNotificacion = null;

  function el(id) {
    return document.getElementById(id);
  }

  function estado(mensaje, esError) {
    var nodo = el("chat-estado");
    nodo.textContent = mensaje || "";
    nodo.classList.toggle("error", !!esError);
  }

  function actualizarBotonNotificaciones() {
    var boton = el("chat-notificaciones");
    if (!("Notification" in window) || !window.isSecureContext) {
      boton.hidden = true;
      return;
    }
    notificacionesPermitidas = Notification.permission === "granted";
    boton.hidden = Notification.permission === "denied";
    boton.disabled = notificacionesPermitidas;
    boton.textContent = notificacionesPermitidas ? "Avisos activos" : "Activar avisos";
  }

  function pedirPermisoNotificaciones() {
    if (!("Notification" in window) || !window.isSecureContext) return Promise.resolve(false);
    if (Notification.permission === "granted") {
      notificacionesPermitidas = true;
      actualizarBotonNotificaciones();
      return Promise.resolve(true);
    }
    if (Notification.permission === "denied") {
      actualizarBotonNotificaciones();
      return Promise.resolve(false);
    }
    if (promesaPermisoNotificaciones) return promesaPermisoNotificaciones;

    /* Se pide al enviar la primera pregunta, dentro de un gesto de usuario.
     * Pedirlo al cargar la página lo bloquean navegadores móviles, y además
     * sería ruido antes de que el usuario haya decidido usar el chat. */
    permisoNotificacionesPedido = true;
    promesaPermisoNotificaciones = Notification.requestPermission().then(function (permiso) {
      notificacionesPermitidas = permiso === "granted";
      actualizarBotonNotificaciones();
      return notificacionesPermitidas;
    }).catch(function () {
      notificacionesPermitidas = false;
      actualizarBotonNotificaciones();
      return false;
    });
    return promesaPermisoNotificaciones;
  }

  function resumenNotificacion(texto) {
    var limpio = String(texto || "").replace(/\s+/g, " ").trim();
    if (limpio.length <= 140) return limpio;
    return limpio.slice(0, 139).trim() + "…";
  }

  function lanzarNotificacion(texto) {
    if (!notificacionesPermitidas || !("Notification" in window)) return false;

    try {
      ultimaNotificacion = new Notification("WhereAmAi respondió", {
        body: resumenNotificacion(texto),
        tag: "roadtrip-chat-respuesta",
        icon: "/static/brand/icon-192.png",
      });
      return true;
    } catch (err) {
      /* La notificación es comodidad, no parte del dato. Si el navegador la
      * rechaza, la respuesta ya está pintada y guardada en el hilo. */
      return false;
    }
  }

  function notificarRespuesta(texto) {
    if (notificacionesPermitidas) {
      lanzarNotificacion(texto);
      return;
    }
    if (promesaPermisoNotificaciones) {
      promesaPermisoNotificaciones.then(function (permitidas) {
        if (permitidas) lanzarNotificacion(texto);
      });
    }
  }

  function geolocationErrorMessage(err) {
    switch (err.code) {
      case err.PERMISSION_DENIED:
        return "Has denegado el permiso de ubicación. Sin saber dónde estás no puedo responder gran cosa.";
      case err.POSITION_UNAVAILABLE:
        return "No se pudo determinar la posición. Puede que no haya señal GPS.";
      case err.TIMEOUT:
        return "El GPS tardó demasiado. Sal a cielo abierto e inténtalo de nuevo.";
      default:
        return "Error de geolocalización: " + (err.message || "desconocido");
    }
  }

  function getPosition() {
    return new Promise(function (resolve, reject) {
      if (!("geolocation" in navigator)) {
        reject(new Error("Este navegador no soporta geolocalización."));
        return;
      }
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true,
        timeout: 15000,
        maximumAge: 30000,
      });
    });
  }

  /* Se pinta con textContent y NUNCA con innerHTML. La respuesta viene de un
   * modelo, y un modelo puede escribir cualquier cosa: interpretarla como HTML
   * sería ejecutar en la página texto que no controlamos. */
  function pintarMensaje(rol, texto, lugar) {
    var burbuja = document.createElement("div");
    burbuja.className = "chat-mensaje chat-" + (rol === "usuario" ? "usuario" : "asistente");

    var cuerpo = document.createElement("p");
    cuerpo.textContent = texto;
    burbuja.appendChild(cuerpo);

    if (lugar) {
      var pie = document.createElement("span");
      pie.className = "chat-pie";
      pie.textContent = lugar;
      burbuja.appendChild(pie);
    }

    quitarVacio();
    el("chat-hilo").appendChild(burbuja);
    burbuja.scrollIntoView({ block: "end" });
  }

  function pintarAvisos(avisos) {
    var nodo = el("chat-avisos");
    if (!avisos || !avisos.length) {
      nodo.hidden = true;
      return;
    }
    /* Los avisos se enseñan en vez de esconderse (decisión 9): si el tiempo se
     * ha caído, la respuesta se ha dado sin él y hay que poder saberlo. */
    nodo.textContent = "⚠ " + avisos.join(" · ");
    nodo.hidden = false;
  }

  /* Un hilo vacío en blanco no dice si la conversación está por empezar o si ha
   * fallado al cargarse. Y ya que hay que escribir algo, que diga qué sabe:
   * media app se ha dedicado a reunir ese contexto, y aquí es donde se nota. */
  function pintarVacio() {
    var caja = document.createElement("p");
    caja.className = "vacio";
    caja.textContent =
      "Pregunta lo que quieras. Sé dónde estás, qué tiempo hace, qué luna " +
      "habrá esta noche y cuánto llevas andado. Lo del pescado bueno del " +
      "pueblo, eso ya te lo inventas tú.";
    el("chat-hilo").appendChild(caja);
  }

  /* Se quita en cuanto entra el primer mensaje. Va aquí y no en `pintarMensaje`
   * para que dejar de estar vacío sea una sola línea y no una condición
   * repetida en cada sitio que añade una burbuja.
   *
   * Se busca por CLASE y no por id, y no es indiferente: `test_frontend_ids.py`
   * exige que todo id nombrado por el JavaScript exista en la plantilla, porque
   * un id muerto revienta en el navegador sin que la suite se entere
   * (decisión 42). Un nodo que crea y destruye este mismo archivo no tiene por
   * qué estar en el HTML, así que darle id sería declarar una dependencia que
   * no existe — y el test lo cazó al primer intento. */
  function quitarVacio() {
    var caja = el("chat-hilo").querySelector(".vacio");
    if (caja) caja.remove();
  }

  async function cargarHistorial() {
    try {
      var respuesta = await fetch("/api/chat");
      if (!respuesta.ok) return;
      var datos = await respuesta.json();
      var mensajes = datos.mensajes || [];
      if (!mensajes.length) {
        pintarVacio();
        return;
      }
      mensajes.forEach(function (m) {
        pintarMensaje(m.rol, m.texto, m.rol === "usuario" ? m.lugar : null);
      });
    } catch (err) {
      /* Que no se pueda repintar la conversación no puede impedir preguntar. */
      estado("No se pudo cargar la conversación anterior.", true);
    }
  }

  async function situar() {
    try {
      var pos = await getPosition();
      posicion = pos.coords;

      /* Un sitio se dice con su nombre, no con sus coordenadas. «Preguntas
       * desde 43.5622, -6.1456 (±18 m)» no le dice nada a nadie: hay que
       * traducirlo mentalmente para entender algo que la app ya sabe. Es la
       * misma decisión que sacó las coordenadas de la tarjeta de Inicio
       * (decisión 35), que se había quedado sin aplicar aquí.
       *
       * Se usa `/api/location` y no `/api/contexto`: lo único que hace falta es
       * el nombre, y esa ruta solo llama a Nominatim, que está cacheado por
       * coordenada redondeada a ~110 m (decisión 3). Pedir el contexto entero
       * traería además el tiempo y la luna para tirarlos, y con un solo worker
       * eso es una espera que le quitas a la pregunta que viene detrás. */
      el("chat-contexto").textContent = "Viendo dónde estás…";
      var respuesta = await fetch("/api/location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: posicion.latitude, lon: posicion.longitude }),
      });
      if (!respuesta.ok) throw new Error("no se pudo resolver el sitio");
      var datos = await respuesta.json();
      el("chat-contexto").textContent = "Preguntas desde " + datos.place.short_label + ".";
    } catch (err) {
      /* El GPS y Nominatim fallan por motivos distintos y se arreglan en sitios
       * distintos, así que no se dicen igual. Sin GPS no se puede preguntar;
       * sin nombre sí — el modelo recibe las coordenadas igual. */
      el("chat-contexto").textContent = posicion
        ? "Sé dónde estás, pero no he podido ponerle nombre al sitio."
        : geolocationErrorMessage(err);
    }
  }

  async function preguntar(texto) {
    if (!posicion) {
      estado("Todavía no sé dónde estás. Espera al GPS o recarga.", true);
      return;
    }

    pedirPermisoNotificaciones();
    pintarMensaje("usuario", texto, null);
    estado("Pensando…");
    el("chat-enviar").disabled = true;

    /* El modelo puede tardar; un AbortController evita que una petición colgada
     * deje el botón bloqueado para siempre. */
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, 120000);

    try {
      var respuesta = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: posicion.latitude,
          lon: posicion.longitude,
          mensaje: texto,
        }),
        signal: controller.signal,
      });

      var datos = await respuesta.json().catch(function () {
        return {};
      });

      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!respuesta.ok) {
        pintarAvisos(datos.warnings);
        estado(datos.error || "No se pudo responder.", true);
        return;
      }

      pintarMensaje("asistente", datos.respuesta.texto, datos.respuesta.modelo);
      notificarRespuesta(datos.respuesta.texto);
      pintarAvisos(datos.warnings);
      estado("");
    } catch (err) {
      estado(
        err.name === "AbortError"
          ? "El modelo tardó demasiado. Inténtalo otra vez."
          : "Sin conexión con el servidor.",
        true
      );
    } finally {
      clearTimeout(timer);
      el("chat-enviar").disabled = false;
    }
  }

  el("chat-form").addEventListener("submit", function (evento) {
    evento.preventDefault();
    var campo = el("chat-texto");
    var texto = campo.value.trim();
    if (!texto) return;
    campo.value = "";
    preguntar(texto);
  });

  /* Enter envía, Mayús+Enter hace salto de línea. En un móvil el teclado manda
   * un Enter normal, que es lo que se quiere el 99 % de las veces. */
  el("chat-texto").addEventListener("keydown", function (evento) {
    if (evento.key === "Enter" && !evento.shiftKey) {
      evento.preventDefault();
      el("chat-form").dispatchEvent(new Event("submit"));
    }
  });

  el("chat-notificaciones").addEventListener("click", function () {
    pedirPermisoNotificaciones();
  });

  el("chat-borrar").addEventListener("click", async function () {
    try {
      await fetch("/api/chat", { method: "DELETE" });
      el("chat-hilo").textContent = "";
      pintarVacio();
      estado("Conversación borrada.");
    } catch (err) {
      estado("No se pudo borrar.", true);
    }
  });

  cargarHistorial();
  situar();
  actualizarBotonNotificaciones();
})();
