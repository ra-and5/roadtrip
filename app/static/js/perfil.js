/* El perfil: cómo estás tú y de qué datos te puedes fiar.
 *
 * Pinta /api/perfil, que no necesita red ni GPS. El tiempo y el sitio son
 * Inicio; el viaje es el Mapa. Aquí no se repite ninguno de los dos. */

(function () {
  "use strict";

  const ESTADOS = {
    demostrada: { texto: "fiable", clase: "tag-bueno" },
    con_huecos: { texto: "con huecos", clase: "tag-regular" },
    sin_datos: { texto: "sin datos", clase: "tag-cat" },
    simulada: { texto: "simulado", clase: "tag-malo" },
  };

  function el(id) { return document.getElementById(id); }
  function text(id, valor) { el(id).textContent = valor || ""; }
  function show(id) { el(id).hidden = false; }

  function estado(mensaje, clase) {
    const p = el("perfil-estado");
    p.textContent = mensaje;
    p.className = "status" + (clase ? " " + clase : "");
    p.hidden = !mensaje;
  }

  function diaLegible(iso) {
    const ymd = String(iso).slice(0, 10).split("-");
    return ymd[2] + "/" + ymd[1] + "/" + ymd[0];
  }

  function miles(n) { return n.toLocaleString("es-ES"); }

  // --- Renderizado ---------------------------------------------------------

  function renderCabecera(perfil) {
    if (perfil.dia_del_viaje) {
      text("perfil-titulo", "Día " + perfil.dia_del_viaje);
    }
    text("perfil-fecha", diaLegible(perfil.hoy));
  }

  function renderCuerpo(perfil) {
    const cuerpo = perfil.cuerpo;

    /* La tarjeta sale aunque no haya datos: un hueco declarado se entiende, uno
     * ausente parece un olvido. */
    show("cuerpo-card");

    if (perfil.hay_simulado) {
      const aviso = el("cuerpo-simulado");
      aviso.textContent =
        "Estos pasos son SIMULADOS. Sirven para construir la pantalla; no son " +
        "lo que has andado.";
      aviso.hidden = false;
    }

    if (!cuerpo.hay_datos) {
      text("cuerpo-hoy", "Todavía no ha llegado ninguna muestra del móvil.");
      text("cuerpo-pie", "Las envía el atajo del iPhone seis veces al día.");
      return;
    }

    /* Sin muestra de hoy NO se dice que no has andado: a las 00:30 lo normal es
     * que aún no haya llegado nada, y un "0 pasos" ahí es falso. */
    text(
      "cuerpo-hoy",
      cuerpo.pasos_hoy === null || cuerpo.pasos_hoy === undefined
        ? "Hoy todavía no ha llegado ninguna muestra."
        : miles(cuerpo.pasos_hoy) + " pasos hoy"
    );

    renderBarras(perfil.serie);

    const pie = [];
    if (cuerpo.media_diaria) pie.push("Media de los días completos: " + miles(cuerpo.media_diaria));
    if (cuerpo.bateria !== null && cuerpo.bateria !== undefined) {
      pie.push("Batería del móvil: " + cuerpo.bateria + " %");
    }
    if (cuerpo.muestras_hoy) pie.push(cuerpo.muestras_hoy + " muestras hoy");
    text("cuerpo-pie", pie.join(" · "));
  }

  function renderBarras(serie) {
    const caja = el("cuerpo-barras");
    caja.innerHTML = "";
    if (!serie || !serie.length) return;

    const maximo = Math.max.apply(
      null,
      serie.map(function (b) { return b.pasos || 0; })
    );

    serie.forEach(function (barra) {
      const col = document.createElement("div");
      col.className = "barra" + (barra.es_hoy ? " barra-hoy" : "");

      const valor = document.createElement("span");
      valor.className = "barra-valor";
      /* Un día sin muestras se dibuja como hueco y no como cero: un cero dice
       * "no anduvo" y un hueco dice "no lo sé". */
      valor.textContent = barra.pasos === null ? "—" : Math.round(barra.pasos / 100) / 10 + "k";
      col.appendChild(valor);

      /* El tallo va dentro de una pista de alto fijo para que el porcentaje
       * tenga contra qué resolverse. */
      const pista = document.createElement("div");
      pista.className = "barra-pista";
      const tallo = document.createElement("div");
      tallo.className = "barra-tallo" + (barra.pasos === null ? " barra-hueco" : "");
      tallo.style.height = maximo && barra.pasos
        ? Math.max(Math.round((barra.pasos / maximo) * 100), 4) + "%"
        : "100%";
      pista.appendChild(tallo);
      col.appendChild(pista);

      const pie = document.createElement("span");
      pie.className = "barra-etiqueta";
      pie.textContent = barra.etiqueta;
      col.appendChild(pie);

      col.title = barra.fecha +
        (barra.pasos === null ? ": sin datos" : ": " + miles(barra.pasos) + " pasos");
      caja.appendChild(col);
    });
  }

  function renderFuentes(fuentes) {
    if (!fuentes || !fuentes.length) return;

    const lista = el("fuentes-lista");
    lista.innerHTML = "";
    fuentes.forEach(function (fuente) {
      const li = document.createElement("li");

      const cabecera = document.createElement("div");
      cabecera.className = "fuente-cabecera";

      const nombre = document.createElement("strong");
      nombre.textContent = fuente.nombre;
      cabecera.appendChild(nombre);

      const marca = ESTADOS[fuente.estado] || { texto: fuente.estado, clase: "tag-cat" };
      const tag = document.createElement("span");
      tag.className = "tag " + marca.clase;
      tag.textContent = marca.texto;
      cabecera.appendChild(tag);

      li.appendChild(cabecera);

      const detalle = document.createElement("p");
      detalle.className = "muted";
      detalle.textContent = fuente.detalle;
      li.appendChild(detalle);

      lista.appendChild(li);
    });
    show("fuentes-card");
  }

  // --- Arranque ------------------------------------------------------------

  async function cargar() {
    /* La zona la pone el navegador: el servidor va en UTC y el día del perfil es
     * el local (a las 00:30 en España, "hoy" en UTC todavía es ayer). */
    let zona = "";
    try {
      zona = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (err) {
      zona = "";
    }

    try {
      const respuesta = await fetch("/api/perfil?zona=" + encodeURIComponent(zona));
      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!respuesta.ok) throw new Error("Error del servidor (" + respuesta.status + ").");

      const perfil = await respuesta.json();
      renderCabecera(perfil);
      renderCuerpo(perfil);
      renderFuentes(perfil.fuentes);
      estado("");
    } catch (err) {
      estado(err.message || "No se pudo cargar el perfil.", "error");
    }
  }

  cargar();
})();
