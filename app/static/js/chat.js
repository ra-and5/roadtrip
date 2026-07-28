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

  function el(id) {
    return document.getElementById(id);
  }

  function estado(mensaje, esError) {
    var nodo = el("chat-estado");
    nodo.textContent = mensaje || "";
    nodo.classList.toggle("error", !!esError);
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

  async function cargarHistorial() {
    try {
      var respuesta = await fetch("/api/chat");
      if (!respuesta.ok) return;
      var datos = await respuesta.json();
      (datos.mensajes || []).forEach(function (m) {
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
      el("chat-contexto").textContent =
        "Preguntas desde " + posicion.latitude.toFixed(4) + ", " +
        posicion.longitude.toFixed(4) + " (±" + Math.round(posicion.accuracy) + " m)";
    } catch (err) {
      el("chat-contexto").textContent = geolocationErrorMessage(err);
    }
  }

  async function preguntar(texto) {
    if (!posicion) {
      estado("Todavía no sé dónde estás. Espera al GPS o recarga.", true);
      return;
    }

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

  el("chat-borrar").addEventListener("click", async function () {
    try {
      await fetch("/api/chat", { method: "DELETE" });
      el("chat-hilo").textContent = "";
      estado("Conversación borrada.");
    } catch (err) {
      estado("No se pudo borrar.", true);
    }
  });

  cargarHistorial();
  situar();
})();
