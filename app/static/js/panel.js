/* El panel del viaje. Pinta /api/panel, que no necesita red ni GPS.
 *
 * El "aquí y ahora" va aparte y bajo botón porque es lo único que cuesta una
 * llamada a Nominatim y a Open-Meteo. */

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
    const p = el("panel-estado");
    p.textContent = mensaje;
    p.className = "status" + (clase ? " " + clase : "");
    p.hidden = !mensaje;
  }

  function diaLegible(iso) {
    const ymd = String(iso).slice(0, 10).split("-");
    return ymd[2] + "/" + ymd[1] + "/" + ymd[0];
  }

  function miles(n) {
    return n.toLocaleString("es-ES");
  }

  function cifra(valor, etiqueta, detalle) {
    const div = document.createElement("div");
    div.className = "marcador";
    const n = document.createElement("span");
    n.className = "marcador-valor";
    n.textContent = String(valor);
    div.appendChild(n);
    const l = document.createElement("span");
    l.className = "marcador-etiqueta";
    l.textContent = etiqueta;
    div.appendChild(l);
    if (detalle) {
      const d = document.createElement("span");
      d.className = "marcador-detalle";
      d.textContent = detalle;
      div.appendChild(d);
    }
    return div;
  }

  // --- Renderizado ---------------------------------------------------------

  function renderCabecera(panel) {
    if (panel.dia_del_viaje) {
      text("panel-titulo", "Día " + panel.dia_del_viaje + " del viaje");
    }
    text("panel-fecha", diaLegible(panel.hoy));
  }

  function renderViaje(viaje) {
    if (!viaje.hay_datos) return;

    const caja = el("viaje-marcadores");
    caja.innerHTML = "";
    caja.appendChild(cifra(viaje.dias, viaje.dias === 1 ? "día" : "días"));
    caja.appendChild(cifra(viaje.lugares, "sitios"));
    caja.appendChild(cifra(viaje.km, "km", "en línea recta"));
    caja.appendChild(cifra(viaje.notas_totales, "notas"));
    caja.appendChild(cifra(viaje.fotos, "fotos"));

    if (viaje.primera && viaje.ultima) {
      text("viaje-tramo", "Del " + diaLegible(viaje.primera) + " al " + diaLegible(viaje.ultima));
    }
    show("viaje-card");
  }

  function renderCuerpo(panel) {
    const cuerpo = panel.cuerpo;

    /* La tarjeta sale aunque no haya datos: un hueco declarado se entiende, uno
     * ausente parece un olvido. */
    show("cuerpo-card");

    if (panel.hay_simulado) {
      const aviso = el("cuerpo-simulado");
      aviso.textContent =
        "Estos pasos son SIMULADOS. Sirven para construir la pantalla; no son " +
        "lo que has andado.";
      aviso.hidden = false;
    }

    if (!cuerpo.hay_datos) {
      text("cuerpo-hoy", "Todavía no ha llegado ninguna muestra del móvil.");
      text("cuerpo-pie", "Los envía el atajo del iPhone seis veces al día.");
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

    renderBarras(panel.serie);

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

      col.title = barra.fecha + (barra.pasos === null ? ": sin datos" : ": " + miles(barra.pasos) + " pasos");
      caja.appendChild(col);
    });
  }

  function renderRegiones(progreso) {
    const tablero = progreso && progreso.tablero;
    if (!tablero) return;

    text("regiones-resumen", tablero.completadas + " de " + tablero.total +
      (progreso.racha_maxima ? " · racha máxima: " + progreso.racha_maxima + " días" : ""));

    const lista = el("regiones-casillas");
    lista.innerHTML = "";
    tablero.casillas.forEach(function (casilla) {
      const li = document.createElement("li");
      li.className = "casilla" + (casilla.visitada ? " casilla-hecha" : "");
      li.textContent = casilla.nombre;
      lista.appendChild(li);
    });
    show("regiones-card");
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

  function renderNotas(recientes) {
    if (!recientes || !recientes.length) return;

    const lista = el("notas-lista");
    lista.innerHTML = "";
    /* De la más reciente a la más antigua: `viaje.recientes` viene en orden de
     * diario y aquí lo que se quiere es lo último. */
    recientes.slice().reverse().forEach(function (nota) {
      const li = document.createElement("li");

      const cabecera = document.createElement("div");
      cabecera.className = "nota-cabecera";
      const lugar = document.createElement("strong");
      lugar.textContent = nota.lugar || "Sin nombre";
      cabecera.appendChild(lugar);
      const cuando = document.createElement("span");
      cuando.className = "muted";
      cuando.textContent = nota.cuando ? diaLegible(nota.cuando) : "";
      cabecera.appendChild(cuando);
      li.appendChild(cabecera);

      /* textContent y no innerHTML: el texto lo escribe una persona y esto no
       * pasa por Jinja. */
      const texto = document.createElement("p");
      texto.textContent = nota.texto;
      li.appendChild(texto);

      lista.appendChild(li);
    });
    show("notas-card");
  }

  // --- Aquí y ahora (lo único que cuesta red) ------------------------------

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

  function renderAhora(ctx) {
    text("ahora-lugar", ctx.ubicacion.short_label +
      (ctx.momento ? " · " + ctx.momento.hora : ""));

    if (ctx.tiempo) {
      text("ahora-tiempo", ctx.tiempo.summary);

      const outdoor = el("ahora-outdoor");
      outdoor.textContent = "Aire libre: " + ctx.tiempo.outdoor_rating;
      outdoor.className = "tag tag-" + ctx.tiempo.outdoor_rating;

      const agua = el("ahora-agua");
      agua.textContent = "Agua: " + ctx.tiempo.water_sports.rating;
      agua.className = "tag tag-" + (ctx.tiempo.water_sports.suitable ? "bueno" : "malo");

      if (ctx.tiempo.sunrise && ctx.tiempo.sunset) {
        text("ahora-sol", "Amanece " + ctx.tiempo.sunrise.slice(-5) +
          " · Anochece " + ctx.tiempo.sunset.slice(-5));
      }
    } else {
      text("ahora-tiempo", "Sin datos meteorológicos.");
    }

    if (ctx.luna) {
      const ef = ctx.luna.efemerides;
      text("ahora-luna", "Luna: " + ctx.luna.fase.nombre + " al " +
        ctx.luna.fase.iluminacion_pct.toFixed(0) + " %" +
        (ef && ef.salida ? " · sale " + ef.salida.slice(11, 16) : ""));
    }

    show("ahora-contenido");
  }

  async function pedirAhora() {
    const boton = el("ahora-btn");
    const aviso = el("ahora-estado");
    boton.disabled = true;
    aviso.className = "status";
    aviso.textContent = "Buscando el GPS…";

    try {
      const pos = await getPosition();
      aviso.textContent = "Consultando…";

      const respuesta = await fetch("/api/contexto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      });
      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }
      const datos = await respuesta.json().catch(function () { return {}; });
      if (!respuesta.ok) throw new Error(datos.error || "Error del servidor.");

      renderAhora(datos);
      aviso.textContent = (datos.warnings || []).join(" · ");
    } catch (err) {
      aviso.className = "status error";
      aviso.textContent = err.message || "No se pudo consultar.";
    } finally {
      boton.disabled = false;
    }
  }

  // --- Arranque ------------------------------------------------------------

  async function cargar() {
    /* La zona la pone el navegador: el servidor va en UTC y el día del panel es
     * el local (a las 00:30 en España, "hoy" en UTC todavía es ayer). */
    let zona = "";
    try {
      zona = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (err) {
      zona = "";
    }

    try {
      const respuesta = await fetch("/api/panel?zona=" + encodeURIComponent(zona));
      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!respuesta.ok) throw new Error("Error del servidor (" + respuesta.status + ").");

      const panel = await respuesta.json();
      renderCabecera(panel);
      renderViaje(panel.viaje);
      renderCuerpo(panel);
      renderRegiones(panel.progreso);
      renderFuentes(panel.fuentes);
      renderNotas(panel.viaje.recientes);
      estado("");
    } catch (err) {
      estado(err.message || "No se pudo cargar el panel.", "error");
    }
  }

  el("ahora-btn").addEventListener("click", pedirAhora);
  cargar();
})();
