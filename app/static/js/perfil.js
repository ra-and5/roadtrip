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
    /* "parada" no es un grado peor de "con huecos": es otra avería y se arregla
     * en otro sitio —una automatización que no corre, no un valle sin cobertura—
     * así que lleva su propia etiqueta en vez de un rojo más. */
    parada: { texto: "parada", clase: "tag-malo" },
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
    /* "días completos" ya no es una etiqueta optimista: `media_diaria` descarta
     * por nombre los días parciales, así que la frase y el cálculo dicen lo
     * mismo. Antes prometía completos y contaba días truncados. */
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

      const sinDatos = barra.pasos === null || barra.pasos === undefined;

      const valor = document.createElement("span");
      valor.className = "barra-valor";
      /* Un día sin muestras se dibuja como hueco y no como cero: un cero dice
       * "no anduvo" y un hueco dice "no lo sé". Y un día parcial lleva "≥"
       * porque su cifra es un suelo: se anduvo eso o más. */
      valor.textContent = sinDatos
        ? "—"
        : (barra.parcial ? "≥" : "") + Math.round(barra.pasos / 100) / 10 + "k";
      col.appendChild(valor);

      /* El tallo va dentro de una pista de alto fijo para que el porcentaje
       * tenga contra qué resolverse. */
      const pista = document.createElement("div");
      pista.className = "barra-pista";
      const tallo = document.createElement("div");
      tallo.className = "barra-tallo" +
        (sinDatos ? " barra-hueco" : "") +
        (!sinDatos && barra.parcial ? " barra-parcial" : "");
      /* El alto se decide por `sinDatos`, NO por si `barra.pasos` es cierto.
       * Con la comprobación anterior un día de CERO pasos caía en la rama del
       * 100 % y se dibujaba sólido y a tope: el peor día del viaje pintado como
       * el mejor, sin dar ningún error. Un hueco sí ocupa el alto entero, pero
       * con la trama de `barra-hueco`, que se lee como silueta y no como dato. */
      tallo.style.height = sinDatos || !maximo
        ? "100%"
        : Math.max(Math.round((barra.pasos / maximo) * 100), 4) + "%";
      pista.appendChild(tallo);
      col.appendChild(pista);

      const pie = document.createElement("span");
      pie.className = "barra-etiqueta";
      pie.textContent = barra.etiqueta;
      col.appendChild(pie);

      col.title = barra.fecha + (
        sinDatos
          ? ": sin datos"
          : ": " + (barra.parcial ? "al menos " : "") + miles(barra.pasos) + " pasos" +
            (barra.parcial
              ? barra.es_hoy
                ? " (el día no ha terminado)"
                : " (faltó el último envío del día)"
              : "")
      );
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

  /* Esta pantalla NO sale a internet: `/api/perfil` solo lee SQLite. Así que
   * un fallo aquí casi nunca es cosa del perfil — es que el único worker del
   * plan gratuito estaba ocupado atendiendo el contexto de Inicio, que sí sale
   * a la red. Se veía como un «Load failed» en rojo, que es el texto crudo que
   * lanza Safari cuando un fetch no llega: no dice qué pasó ni qué hacer, y
   * suena a que la pantalla está rota cuando lo que hay que hacer es esperar
   * dos segundos. */
  function reintentable(err) {
    return err.name === "TypeError" || err.name === "AbortError" || err.recuperable === true;
  }

  async function pedir(zona) {
    const respuesta = await fetch("/api/perfil?zona=" + encodeURIComponent(zona));
    if (respuesta.status === 401) {
      window.location.href = "/login";
      return null;
    }
    if (!respuesta.ok) {
      const fallo = new Error("El servidor respondió con un error (" + respuesta.status + ").");
      // Un 5xx es del servidor y puede pasarse solo; un 4xx no se arregla
      // repitiendo la misma petición.
      fallo.recuperable = respuesta.status >= 500;
      throw fallo;
    }
    return respuesta.json();
  }

  /* Los pasos de la última carga, para poder decir cuántos han entrado al
   * volver del atajo. `null` es "aún no se ha cargado nunca": el primer dibujo
   * no anuncia novedades porque entonces todo sería nuevo. */
  let pasosAntes = null;
  let ultimaCarga = 0;

  async function cargar() {
    /* La zona la pone el navegador: el servidor va en UTC y el día del perfil es
     * el local (a las 00:30 en España, "hoy" en UTC todavía es ayer). */
    let zona = "";
    try {
      zona = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (err) {
      zona = "";
    }

    ultimaCarga = Date.now();
    estado("Cargando…");
    for (let intento = 0; intento < 2; intento += 1) {
      try {
        const perfil = await pedir(zona);
        if (perfil === null) return;        // se está yendo al login
        renderCabecera(perfil);
        renderCuerpo(perfil);
        renderFuentes(perfil.fuentes);

        /* Lo que se ve al volver de Atajos. Un número de pasos que sube sin
         * decir nada no se nota: acabas de venir de otra app y no te sabes el
         * de antes. Y es justo lo que hay que mirar, porque cuando el envío
         * falla el síntoma es que ese número NO cambia. */
        const hoy = perfil.cuerpo ? perfil.cuerpo.pasos_hoy : null;
        const nuevos =
          pasosAntes === null || hoy === null || hoy === undefined ? 0 : hoy - pasosAntes;
        if (hoy !== null && hoy !== undefined) pasosAntes = hoy;

        estado(nuevos > 0 ? "+" + miles(nuevos) + " pasos" : "", nuevos > 0 ? "ok" : "");
        return;
      } catch (err) {
        if (intento === 0 && reintentable(err)) {
          estado("El servidor está ocupado. Reintentando…");
          await new Promise(function (listo) { setTimeout(listo, 2000); });
          continue;
        }
        estado(
          err.name === "TypeError"
            ? "No se pudo conectar con el servidor. Comprueba la cobertura y recarga."
            : err.message || "No se pudo cargar el perfil.",
          "error"
        );
        return;
      }
    }
  }

  /* Al volver a la pestaña se recarga, igual que el Mapa. Es lo que hace que el
   * botón de «Enviar pasos y batería ahora» sirva de algo: pulsas, iOS te lleva
   * a Atajos, vuelves, y el número ya está al día sin acordarte de recargar.
   *
   * Con el mismo anti-rebote de tres segundos y por el mismo motivo: cambiar de
   * app y volver es constante en un móvil, y en el plan gratuito hay UN worker.
   * Sin él, cada vistazo dejaría esperando detrás a la petición que importa. */
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - ultimaCarga < 3000) return;
    cargar();
  });

  cargar();
})();
