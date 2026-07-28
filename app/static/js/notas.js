/* Notas geolocalizadas con cola offline (Fase 3).
 *
 * La regla que ordena todo este archivo: **se escribe primero en la cola local
 * y se le dice al usuario que está guardada; el envío es un intento
 * posterior**. Nunca al revés. Una nota que se pierde porque el POST falló es
 * exactamente el fallo que esta fase existe para impedir: los pasos de Salud
 * se pueden volver a consultar hacia atrás, pero una nota escrita a mano en un
 * mirador no existe en ningún otro sitio.
 *
 * IndexedDB y no localStorage: localStorage guarda solo texto, ronda los 5 MB
 * y es SÍNCRONO (bloquea la interfaz al escribir). IndexedDB además guarda
 * `Blob` directamente, que es lo que hará falta el día que haya fotos.
 *
 * Qué se reintenta y qué no, que es la decisión de verdad de este archivo:
 *
 *   201 / 200  -> está a salvo en el servidor. Se borra de la cola.
 *                 (200 = "duplicada": el envío anterior sí llegó y se perdió
 *                 la respuesta. Es funcionamiento normal, no un error.)
 *   400        -> esta nota no va a entrar NUNCA. Se marca como rechazada y se
 *                 deja de reintentar. Reintentarla para siempre atascaría la
 *                 cola detrás de ella y las notas buenas nunca saldrían.
 *   401        -> ha caducado la sesión. Se deja en la cola y se pide entrar
 *                 otra vez; la nota no tiene ninguna culpa.
 *   5xx / red  -> no se sabe si llegó. Se deja en la cola.
 *
 * No hay setInterval: se sincroniza al abrir la app, al guardar una nota y
 * cuando el navegador avisa de que ha vuelto la conexión. Un temporizador
 * machacando cada pocos segundos gasta batería en un móvil que va en un camper
 * y no adelanta nada.
 */

(function () {
  "use strict";

  const DB_NOMBRE = "roadtrip";
  const DB_VERSION = 1;
  const ALMACEN = "cola";

  /* El `client_id` es la clave: lo genera el móvil ANTES del primer intento y
   * se reutiliza en cada reintento, así que reenviar una nota no puede
   * duplicarla en el servidor. La idempotencia empieza aquí, no en el POST. */
  const CLAVE = "client_id";

  // --- IndexedDB, prometido -------------------------------------------------

  function abrirBD() {
    return new Promise(function (resolve, reject) {
      const peticion = indexedDB.open(DB_NOMBRE, DB_VERSION);
      peticion.onupgradeneeded = function () {
        const db = peticion.result;
        if (!db.objectStoreNames.contains(ALMACEN)) {
          db.createObjectStore(ALMACEN, { keyPath: CLAVE });
        }
      };
      peticion.onsuccess = function () {
        resolve(peticion.result);
      };
      peticion.onerror = function () {
        reject(peticion.error);
      };
    });
  }

  function conAlmacen(modo, trabajo) {
    return abrirBD().then(function (db) {
      return new Promise(function (resolve, reject) {
        const tx = db.transaction(ALMACEN, modo);
        const peticion = trabajo(tx.objectStore(ALMACEN));
        tx.oncomplete = function () {
          db.close();
          resolve(peticion ? peticion.result : undefined);
        };
        tx.onerror = function () {
          db.close();
          reject(tx.error);
        };
      });
    });
  }

  function guardarEnCola(nota) {
    return conAlmacen("readwrite", function (almacen) {
      return almacen.put(nota);
    });
  }

  function borrarDeCola(clientId) {
    return conAlmacen("readwrite", function (almacen) {
      return almacen.delete(clientId);
    });
  }

  function leerCola() {
    return conAlmacen("readonly", function (almacen) {
      return almacen.getAll();
    });
  }

  // --- Estado y pintado -----------------------------------------------------

  const form = document.getElementById("nota-form");
  if (!form) return; // Esta página no tiene formulario de notas.

  const textarea = document.getElementById("nota-texto");
  const boton = document.getElementById("nota-guardar");
  const estadoEl = document.getElementById("nota-estado");
  const colaEl = document.getElementById("nota-cola");
  const colaLista = document.getElementById("nota-cola-lista");
  const colaResumen = document.getElementById("nota-cola-resumen");

  /* Lo último que resolvió la pantalla principal. La nota se puede guardar sin
   * esto (el nombre del sitio es opcional y el mapa enseña las coordenadas),
   * pero si la app ya sabe dónde estás, aprovecharlo sale gratis y evita una
   * llamada a Nominatim dentro de la ruta que no puede fallar. */
  let ultimoLugar = null;
  document.addEventListener("lugar-resuelto", function (evento) {
    ultimoLugar = evento.detail;
  });

  let sincronizando = false;

  function decir(mensaje, tipo) {
    estadoEl.textContent = mensaje;
    estadoEl.className = "status" + (tipo ? " " + tipo : "");
  }

  function pintarCola(notas) {
    const pendientes = notas.filter(function (n) {
      return n.estado !== "rechazada";
    });
    const rechazadas = notas.filter(function (n) {
      return n.estado === "rechazada";
    });

    colaEl.hidden = notas.length === 0;
    if (notas.length === 0) return;

    /* Una cola invisible es una cola en la que no confías: el número de notas
     * por enviar tiene que estar a la vista, o no hay forma de saber si el
     * sistema está funcionando o perdiendo cosas en silencio. */
    const partes = [];
    if (pendientes.length) {
      partes.push(
        pendientes.length === 1
          ? "1 nota por enviar"
          : pendientes.length + " notas por enviar"
      );
    }
    if (rechazadas.length) {
      partes.push(
        rechazadas.length === 1
          ? "1 rechazada por el servidor"
          : rechazadas.length + " rechazadas por el servidor"
      );
    }
    colaResumen.textContent = partes.join(" · ");

    colaLista.innerHTML = "";
    notas.forEach(function (nota) {
      const li = document.createElement("li");
      li.className = nota.estado === "rechazada" ? "cola-rechazada" : "cola-pendiente";

      const texto = document.createElement("span");
      texto.className = "cola-texto";
      // textContent y no innerHTML: el texto lo escribe una persona y acabaría
      // ejecutándose como HTML.
      texto.textContent = nota.text;
      li.appendChild(texto);

      const meta = document.createElement("span");
      meta.className = "cola-meta";
      meta.textContent =
        nota.estado === "rechazada"
          ? "✕ " + (nota.error || "rechazada")
          : "⏳ pendiente de enviar";
      li.appendChild(meta);

      if (nota.estado === "rechazada") {
        /* Descartar es lo único que se puede hacer con una nota que el
         * servidor no va a aceptar nunca. Sin este botón se quedaría en la
         * lista para siempre, y una lista que no se puede vaciar deja de
         * mirarse. */
        const descartar = document.createElement("button");
        descartar.type = "button";
        descartar.className = "secondary pequeno";
        descartar.textContent = "Descartar";
        descartar.addEventListener("click", function () {
          borrarDeCola(nota[CLAVE]).then(refrescarCola);
        });
        li.appendChild(descartar);
      }

      colaLista.appendChild(li);
    });
  }

  function refrescarCola() {
    return leerCola().then(pintarCola);
  }

  // --- Envío ----------------------------------------------------------------

  function cuerpoDe(nota) {
    return {
      client_id: nota[CLAVE],
      text: nota.text,
      lat: nota.lat,
      lon: nota.lon,
      created_at: nota.created_at,
      place_name: nota.place_name || null,
      region: nota.region || null,
    };
  }

  async function enviar(nota) {
    let respuesta;
    try {
      respuesta = await fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpoDe(nota)),
      });
    } catch (err) {
      // Sin red, o se cortó a mitad. No se sabe si llegó: se queda en la cola.
      return "sin-red";
    }

    if (respuesta.ok) {
      // 201 creada o 200 duplicada: en los dos casos está a salvo.
      await borrarDeCola(nota[CLAVE]);
      return "enviada";
    }

    if (respuesta.status === 401) {
      return "sin-sesion";
    }

    if (respuesta.status === 400) {
      const datos = await respuesta.json().catch(function () {
        return {};
      });
      nota.estado = "rechazada";
      nota.error = datos.error || "el servidor la ha rechazado";
      await guardarEnCola(nota);
      return "rechazada";
    }

    // 413, 429, 5xx y cualquier cosa rara: puede arreglarse solo. Se conserva.
    return "reintentar";
  }

  async function sincronizar(silencioso) {
    if (sincronizando) return;
    sincronizando = true;

    try {
      const cola = (await leerCola()).filter(function (n) {
        return n.estado !== "rechazada";
      });
      if (cola.length === 0) {
        await refrescarCola();
        return;
      }

      if (!silencioso) decir("Enviando " + cola.length + "…");

      let enviadas = 0;
      let sinSesion = false;
      for (const nota of cola) {
        const resultado = await enviar(nota);
        if (resultado === "enviada") enviadas += 1;
        if (resultado === "sin-sesion") {
          sinSesion = true;
          break; // Sin sesión fallarían todas igual; no se insiste.
        }
        if (resultado === "sin-red") break; // Igual: no hay red para ninguna.
      }

      await refrescarCola();

      if (sinSesion) {
        decir("Ha caducado la sesión. Vuelve a entrar; las notas siguen guardadas aquí.", "error");
      } else if (enviadas > 0) {
        decir(
          enviadas === 1 ? "Nota enviada." : enviadas + " notas enviadas.",
          "ok"
        );
      } else if (!silencioso) {
        decir("Sin conexión. Las notas se enviarán solas cuando vuelva.", "");
      }
    } finally {
      sincronizando = false;
    }
  }

  // --- Guardar --------------------------------------------------------------

  function posicionActual() {
    return new Promise(function (resolve, reject) {
      if (!("geolocation" in navigator)) {
        reject(new Error("Este navegador no soporta geolocalización."));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (posicion) {
          resolve(posicion.coords);
        },
        reject,
        /* `maximumAge` alto a propósito: para marcar dónde estás vale una
         * posición de hace un minuto, y esperar un fix nuevo con mala señal
         * puede tardar 15 s. La nota es lo que no se puede perder; los metros
         * de más, sí. */
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
      );
    });
  }

  async function guardar() {
    const texto = textarea.value.trim();
    if (!texto) {
      decir("Escribe algo antes de guardar.", "error");
      return;
    }

    boton.disabled = true;
    try {
      decir("Buscando dónde estás…");
      let coords;
      try {
        coords = await posicionActual();
      } catch (err) {
        /* Sin coordenadas no hay nota: una nota sin sitio no puede aparecer en
         * el mapa, que es lo único que esta fase construye. Se dice claramente
         * en vez de guardarla en un limbo. */
        decir(
          "No se pudo obtener la ubicación, y una nota sin sitio no iría al mapa. " +
            "Sal a cielo abierto e inténtalo otra vez.",
          "error"
        );
        return;
      }

      const nota = {
        client_id: crypto.randomUUID(),
        text: texto,
        lat: coords.latitude,
        lon: coords.longitude,
        // Con huso: sin él el servidor no sabría qué instante es, y suponer
        // uno es inventarse la hora.
        created_at: new Date().toISOString(),
        place_name: ultimoLugar ? ultimoLugar.place_name : null,
        region: ultimoLugar ? ultimoLugar.region : null,
        estado: "pendiente",
      };

      // Primero la cola. A partir de esta línea la nota no se puede perder,
      // pase lo que pase con la red.
      await guardarEnCola(nota);
      textarea.value = "";
      decir("Guardada en el móvil. Enviando…", "ok");
      await refrescarCola();

      await sincronizar(true);
    } catch (err) {
      decir("No se pudo guardar la nota: " + (err.message || err), "error");
    } finally {
      boton.disabled = false;
    }
  }

  form.addEventListener("submit", function (evento) {
    evento.preventDefault();
    guardar();
  });

  /* Los dos únicos disparadores de reintento: abrir la app y que vuelva la
   * conexión. `online` no es infalible (el navegador puede creer que hay red
   * en un wifi de camping que no llega a ninguna parte), y por eso el reintento
   * al abrir la app es el que de verdad recupera las notas atascadas. */
  window.addEventListener("online", function () {
    sincronizar(true);
  });

  refrescarCola().then(function () {
    sincronizar(true);
  });
})();
