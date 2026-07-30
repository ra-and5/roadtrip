/* Fase 2: GPS -> ubicación + tiempo + POIs + recomendación de Claude.
 *
 * Nota crítica: navigator.geolocation SOLO funciona en HTTPS o en localhost.
 * Si abres esto desde el móvil apuntando a http://192.168.x.x, el navegador
 * bloqueará el GPS sin dar un error claro. Prueba en localhost o ya desplegado.
 */

(function () {
  "use strict";

  const contextoBtn = document.getElementById("contexto-btn");
  const btn = document.getElementById("locate-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  const poisBtn = document.getElementById("pois-btn");
  const categoriaEl = document.getElementById("pois-categoria");
  const statusEl = document.getElementById("status");

  /* Guardamos la última posición para que "generar otra" no tenga que volver
   * a esperar un fix de GPS, que es la parte más lenta del proceso. */
  let lastCoords = null;

  /* `pois-card` NO está aquí: es la tarjeta que lleva el botón de buscar
   * sitios, y esconderla al pedir una recomendación dejaría el botón
   * desapareciendo y reapareciendo sin motivo. Lo que se oculta es su
   * desplegable de resultados, que sí depende de haber buscado. */
  const SECTIONS = [
    "place-card", "warnings-card", "weather-card", "luna-card",
    "reco-card",
  ];

  function setStatus(message, kind) {
    statusEl.textContent = message;
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  function hideAll() {
    SECTIONS.forEach(function (id) {
      document.getElementById(id).hidden = true;
    });
  }

  function show(id) {
    document.getElementById(id).hidden = false;
  }

  function text(id, value) {
    document.getElementById(id).textContent = value || "";
  }

  function geolocationErrorMessage(err) {
    switch (err.code) {
      case err.PERMISSION_DENIED:
        return "Has denegado el permiso de ubicación. Actívalo en los ajustes del navegador.";
      case err.POSITION_UNAVAILABLE:
        return "No se pudo determinar la posición. Puede que no haya señal GPS.";
      case err.TIMEOUT:
        return "El GPS tardó demasiado. Sal a cielo abierto e inténtalo de nuevo.";
      default:
        return "Error de geolocalización: " + (err.message || "desconocido");
    }
  }

  /* Traduce CUALQUIER fallo a algo que se pueda leer en una gasolinera.
   *
   * Existe por un mensaje concreto: cuando un `fetch` no llega, Safari lanza un
   * TypeError cuyo texto es «Load failed», y eso es exactamente lo que salía en
   * rojo en el iPhone. No dice qué pasó, no dice qué hacer, y encima suena a
   * error de programación cuando lo normal es que sea el servidor ocupado o la
   * cobertura. En el plan gratuito hay UN worker, así que basta con que otra
   * pantalla esté esperando para que esta falle así. */
  function mensajeDeError(err) {
    if (typeof err.code === "number") return geolocationErrorMessage(err);
    if (err.name === "AbortError") {
      return "El servidor tardó demasiado. Puede estar ocupado con otra consulta; inténtalo otra vez.";
    }
    if (err.name === "TypeError") {
      return "No se pudo conectar con el servidor. Comprueba la cobertura e inténtalo otra vez.";
    }
    return err.message || "Error inesperado.";
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

  async function fetchRecommendations(lat, lon, refresh) {
    /* Overpass puede tardar 20 s y el modelo otro tanto. Un AbortController
     * evita que una petición colgada deje el botón bloqueado para siempre. */
    const controller = new AbortController();
    const timer = setTimeout(function () {
      controller.abort();
    }, 150000);

    try {
      const response = await fetch("/api/recommendations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: lat, lon: lon, refresh: !!refresh }),
        signal: controller.signal,
      });

      const data = await response.json().catch(function () {
        return {};
      });

      if (!response.ok) {
        if (response.status === 401) {
          window.location.href = "/login";
          return null;
        }
        throw new Error(data.error || "Error del servidor (" + response.status + ").");
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  // --- Renderizado ---------------------------------------------------------

  function renderPlace(place, coords, momento, weather) {
    text("place-label", place.short_label);

    /* La altitud la da Open-Meteo gratis en la misma respuesta del tiempo, así
     * que si el tiempo falló tampoco hay altitud. Se calla en vez de poner un
     * cero: "estás a 0 m" es una afirmación, y falsa. */
    text(
      "place-altitud",
      weather && weather.elevation_m !== null && weather.elevation_m !== undefined
        ? "A " + Math.round(weather.elevation_m) + " m de altitud"
        : ""
    );

    text(
      "place-momento",
      momento
        ? momento.dia_semana + " " + momento.hora +
          (momento.zona_es_supuesta ? " (hora aproximada: zona sin confirmar)" : "")
        : ""
    );

    /* Las coordenadas bajan al detalle plegado. Siguen estando porque cuando
     * algo parece raro son lo primero que se mira, pero no presiden la tarjeta:
     * "38.39099, -0.52101 · ±1020 m" no le dice nada a nadie. */
    text(
      "place-coords",
      place.lat.toFixed(5) + ", " + place.lon.toFixed(5) +
        (coords && coords.accuracy ? "  ·  GPS ±" + Math.round(coords.accuracy) + " m" : "")
    );
    show("place-card");

    /* Se avisa por un evento del DOM en vez de llamar a notas.js o compartir
     * una variable global: los dos archivos siguen sin conocerse, y si mañana
     * el formulario de notas no está en esta página, aquí no hay que tocar
     * nada. El nombre del sitio se aprovecha porque ya lo tenemos resuelto y
     * cacheado: resolverlo otra vez al crear la nota metería una llamada a
     * Nominatim dentro de la ruta que no puede fallar. */
    document.dispatchEvent(
      new CustomEvent("lugar-resuelto", {
        detail: { place_name: place.short_label, region: place.region || null },
      })
    );
  }

  function renderWarnings(warnings) {
    if (!warnings || warnings.length === 0) return;
    const list = document.getElementById("warnings-list");
    list.innerHTML = "";
    warnings.forEach(function (w) {
      const li = document.createElement("li");
      li.textContent = w;
      list.appendChild(li);
    });
    show("warnings-card");
  }

  function renderWeather(weather) {
    if (!weather) return;
    text("weather-summary", weather.summary);

    const outdoor = document.getElementById("weather-outdoor");
    outdoor.textContent = "Aire libre: " + weather.outdoor_rating;
    outdoor.className = "tag tag-" + weather.outdoor_rating;

    const water = document.getElementById("weather-water");
    water.textContent = "Deportes de agua: " + weather.water_sports.rating;
    water.className = "tag tag-" + (weather.water_sports.suitable ? "bueno" : "malo");

    text("weather-water-reason", weather.water_sports.reason);

    if (weather.sunrise && weather.sunset) {
      text(
        "weather-sun",
        "Amanece " + weather.sunrise.slice(-5) + " · Anochece " + weather.sunset.slice(-5)
      );
    }
    show("weather-card");
  }

  function renderLuna(luna) {
    if (!luna) return;

    text(
      "luna-fase",
      luna.fase.nombre.charAt(0).toUpperCase() + luna.fase.nombre.slice(1) +
        " · " + luna.fase.iluminacion_pct.toFixed(0) + "% iluminada" +
        (luna.fase.creciendo ? " (creciendo)" : " (menguando)")
    );

    /* Sin efemérides la tarjeta NO desaparece: la fase se calcula en local y
     * sigue siendo cierta. Lo que falta se dice, no se disimula. */
    const ef = luna.efemerides;
    text(
      "luna-horas",
      ef && (ef.salida || ef.puesta)
        ? "Sale " + (ef.salida ? ef.salida.slice(11, 16) : "—") +
          " · Se pone " + (ef.puesta ? ef.puesta.slice(11, 16) : "—")
        : "Sin hora de salida ni de puesta: no se pudieron consultar."
    );

    const tag = document.getElementById("luna-veredicto");
    tag.textContent = luna.veredicto.hay_luz
      ? "Se puede caminar de noche"
      : "Hace falta frontal";
    tag.className = "tag tag-" + (luna.veredicto.hay_luz ? "bueno" : "malo");

    text("luna-motivo", luna.veredicto.motivo);
    show("luna-card");
  }

  function renderRecommendation(reco) {
    if (!reco) return;
    text("reco-summary", reco.resumen);

    const aviso = document.getElementById("reco-aviso");
    aviso.textContent = reco.aviso || "";
    aviso.hidden = !reco.aviso;

    const container = document.getElementById("reco-activities");
    container.innerHTML = "";

    reco.actividades.forEach(function (act) {
      const article = document.createElement("article");
      article.className = "activity";

      const head = document.createElement("div");
      head.className = "activity-head";

      const title = document.createElement("h3");
      title.textContent = act.titulo;
      head.appendChild(title);

      const cat = document.createElement("span");
      cat.className = "tag tag-cat";
      cat.textContent = act.categoria;
      head.appendChild(cat);
      article.appendChild(head);

      const meta = document.createElement("p");
      meta.className = "activity-meta";
      /* Marcamos de dónde sale cada plan. Distinguir "está en el mapa a 3 km"
       * de "el modelo lo conoce" es lo que hace que puedas fiarte de la app. */
      meta.textContent =
        [act.distancia, act.duracion].filter(Boolean).join(" · ") +
        (act.origen === "lista_cercana" ? "  ·  ✓ verificado en el mapa" : "  ·  sugerencia general");
      article.appendChild(meta);

      const desc = document.createElement("p");
      desc.textContent = act.descripcion;
      article.appendChild(desc);

      if (act.por_que_ahora) {
        const why = document.createElement("p");
        why.className = "activity-why";
        why.textContent = act.por_que_ahora;
        article.appendChild(why);
      }

      container.appendChild(article);
    });

    text(
      "reco-meta",
      (reco.desde_cache ? "Recomendación cacheada" : "Generada ahora") +
        (reco.modelo ? " · " + reco.modelo : "")
    );
    show("reco-card");
  }

  /* Los sitios salen AGRUPADOS por categoría, cada grupo plegable y con su
   * número. Antes era una sola lista ordenada por distancia, y con ocho
   * categorías eso es un revoltijo: cuando lo que buscas es una barra de
   * calistenia o un sitio donde vaciar aguas, tener que leerte veinte líneas
   * mezcladas es no encontrarlo.
   *
   * El primer grupo se abre solo: una pantalla de acordeones todos cerrados
   * parece que no ha encontrado nada. */
  function renderPois(pois) {
    const grupos = document.getElementById("pois-grupos");
    grupos.innerHTML = "";
    if (!pois || pois.length === 0) {
      grupos.hidden = true;
      return;
    }

    const porCategoria = new Map();
    pois.forEach(function (poi) {
      if (!porCategoria.has(poi.category)) porCategoria.set(poi.category, []);
      porCategoria.get(poi.category).push(poi);
    });

    Array.from(porCategoria.keys()).sort().forEach(function (categoria, indice) {
      const bloque = document.createElement("details");
      bloque.className = "pois-grupo";
      bloque.open = indice === 0;

      const titulo = document.createElement("summary");
      titulo.textContent = categoria + " (" + porCategoria.get(categoria).length + ")";
      bloque.appendChild(titulo);

      const list = document.createElement("ul");
      list.className = "pois";
      porCategoria.get(categoria).forEach(function (poi) {
        list.appendChild(itemPoi(poi));
      });
      bloque.appendChild(list);
      grupos.appendChild(bloque);
    });

    grupos.hidden = false;
  }

  function itemPoi(poi) {
      const km = poi.distance_m / 1000;
      const li = document.createElement("li");

      /* Cada punto es un enlace que abre Google Maps con la ruta puesta. Una
       * lista de nombres y distancias a la que no puedes ir es media función:
       * lo que se quiere saber al leerla es "¿cómo llego?".
       *
       * Se usa el enlace UNIVERSAL (`https://www.google.com/maps/dir/?api=1`) y
       * no el esquema propio `comgooglemaps://`, y esa es la decisión. El
       * esquema abre la app un poco más directo, pero si Google Maps no está
       * instalado **no pasa nada al pulsar**: ni abre, ni avisa, ni da error.
       * Un enlace que no hace nada es el fallo mudo de siempre. El universal
       * abre la app si está y la web si no, así que nunca deja al usuario
       * mirando una pantalla que no reacciona. De paso funciona igual en
       * Android y en un escritorio, así que ya no hay nada específico de iOS.
       *
       * `destination` lleva las COORDENADAS y no el nombre, a propósito: con
       * el nombre, Google buscaría y podría llevarte a otro sitio que se llame
       * parecido — una ruta convincente hacia el lugar equivocado, que es peor
       * que no tener enlace. El nombre ya lo estás leyendo en esta lista. */
      const enlace = document.createElement("a");
      enlace.href =
        "https://www.google.com/maps/dir/?api=1&destination=" +
        poi.lat + "," + poi.lon;
      enlace.rel = "noopener";
      /* Sin la categoría entre paréntesis: ahora la dice el grupo, y repetirla
       * en cada línea solo alarga lo que hay que leer en marcha. */
      enlace.textContent =
        (km < 1 ? poi.distance_m + " m" : km.toFixed(1) + " km") + " — " + poi.name;

      li.appendChild(enlace);
      return li;
  }

  /* El estado de los POIs se enseña con las palabras de cada caso, no con un
   * "no hay resultados" para todo. Los cuatro significan cosas distintas y
   * confundirlos es el fallo que costó descartar un espejo de Overpass: decir
   * "aquí no hay nada que ver" cuando lo que pasa es "no he podido mirar". */
  function renderEstadoPois(fuente) {
    if (!fuente) return;
    const MENSAJES = {
      ok: "",
      sin_datos: "Buscado: OpenStreetMap no tiene nada mapeado en esta zona.",
      no_consultada: "Todavía no se han buscado. La recomendación va sin datos del mapa.",
      fallo: "No se pudo consultar OpenStreetMap: " + (fuente.motivo || ""),
    };
    const mensaje = MENSAJES[fuente.estado];
    text("pois-estado", mensaje === "" ? "Datos de OpenStreetMap." : mensaje);
  }

  function renderContexto(ctx, coords) {
    renderPlace(ctx.ubicacion, coords, ctx.momento, ctx.tiempo);
    renderWarnings(ctx.warnings);
    renderWeather(ctx.tiempo);
    renderLuna(ctx.luna);
    renderEstadoPois(ctx.fuentes && ctx.fuentes.pois);
  }

  // --- Flujo principal -----------------------------------------------------

  /* Dónde estoy y qué tiempo hace. Sin modelo, sin Overpass y sin esperas: es
   * lo que la pantalla puede enseñar en menos de un segundo, y es el motivo de
   * haber partido el endpoint en dos. */
  async function verContexto() {
    contextoBtn.disabled = true;

    /* `hideAll()` va DENTRO del try, y no es colocación caprichosa: cuando una
     * tarjeta se mueve de pantalla y su id deja de existir aquí, esto lanza. Si
     * lanzara fuera del try no habría ni catch que lo cuente ni finally que
     * suelte el botón: la pantalla se quedaba con el botón muerto y el estado
     * en blanco, sin decir nada (decisión 11). Pasó con `metricas-card` al
     * llevarse las métricas a Perfil. */
    try {
      hideAll();
      setStatus("Obteniendo posición del GPS…");
      const position = await getPosition();
      lastCoords = position.coords;

      setStatus("Consultando…");

      /* Con todo cacheado esto tarda centésimas, y en un sitio nuevo lo que
       * tarde la fuente más lenta. Pasado ese margen no es que vaya lento: es
       * que algo va mal, y entonces vale más soltar el botón y decirlo que
       * dejar la pantalla girando sin fin. Es lo mismo que ya hacían las
       * recomendaciones y el chat; esta llamada se había quedado sin ello. */
      const controller = new AbortController();
      const timer = setTimeout(function () {
        controller.abort();
      }, 15000);

      let response;
      try {
        response = await fetch("/api/contexto", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            lat: position.coords.latitude,
            lon: position.coords.longitude,
          }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }

      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }

      const data = await response.json().catch(function () {
        return {};
      });

      if (!response.ok) {
        throw new Error(data.error || "Error del servidor (" + response.status + ").");
      }

      // El evento `lugar-resuelto` que necesita notas.js ya lo emite
      // renderPlace(), así que no se repite aquí.
      renderContexto(data, position.coords);
      setStatus("");
    } catch (err) {
      setStatus(mensajeDeError(err), "error");
    } finally {
      contextoBtn.disabled = false;
    }
  }

  async function run(refresh) {
    btn.disabled = true;
    refreshBtn.disabled = true;

    try {
      hideAll();                       // dentro del try, ver `verContexto()`
      let coords = lastCoords;
      if (!coords || !refresh) {
        setStatus("Obteniendo posición del GPS…");
        const position = await getPosition();
        coords = position.coords;
        lastCoords = coords;
      }

      setStatus("Consultando mapa, tiempo y recomendaciones… (puede tardar)");
      const data = await fetchRecommendations(coords.latitude, coords.longitude, refresh);
      if (!data) return;

      /* El mismo renderizado que el botón de contexto: una sola función pinta
       * el estado del viaje, venga de donde venga. Si cada camino pintase lo
       * suyo, la pantalla acabaría enseñando cosas distintas según qué botón
       * hubieras pulsado — que es, en pequeño, el mismo problema que resolvió
       * partir el contexto en un solo módulo.
       *
       * `warnings` se toma del nivel superior porque ahí vienen ya los del
       * contexto MÁS los de los POIs, que no son parte del contexto. */
      if (data.contexto) {
        renderContexto(
          Object.assign({}, data.contexto, { warnings: data.warnings }),
          coords
        );
      }
      renderRecommendation(data.recommendation);
      renderPois(data.pois);

      setStatus("");
      refreshBtn.hidden = false;
    } catch (err) {
      setStatus(mensajeDeError(err), "error");
    } finally {
      btn.disabled = false;
      refreshBtn.disabled = false;
    }
  }

  function rellenarCategorias(categorias) {
    if (!categorias || categoriaEl.options.length > 1) return;
    categorias.forEach(function (nombre) {
      const opcion = document.createElement("option");
      opcion.value = nombre;
      opcion.textContent = nombre;
      categoriaEl.appendChild(opcion);
    });
  }

  /* Buscar sitios cerca. Va por su cuenta y no bloquea nada más: Overpass
   * puede tardar 30 s o no contestar, y esa espera es una decisión de quien
   * pulsa. Lo que deja hecho vale para toda la semana, porque el servidor
   * cachea los puntos 7 días. */
  async function buscarSitios() {
    poisBtn.disabled = true;
    text("pois-estado", "Buscando en OpenStreetMap… puede tardar media minuto.");

    try {
      let coords = lastCoords;
      if (!coords) {
        const position = await getPosition();
        coords = position.coords;
        lastCoords = coords;
      }

      const response = await fetch("/api/pois", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: coords.latitude,
          lon: coords.longitude,
          categoria: categoriaEl.value || null,
        }),
      });

      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }

      const data = await response.json().catch(function () {
        return {};
      });

      if (!response.ok) {
        throw new Error(data.error || "Error del servidor (" + response.status + ").");
      }

      renderPois(data.pois);
      renderEstadoPois(data.fuente);
      // Las opciones las manda el servidor, así que una categoría nueva aparece
      // sola sin tocar este archivo.
      rellenarCategorias(data.categorias);
    } catch (err) {
      text("pois-estado", mensajeDeError(err));
    } finally {
      poisBtn.disabled = false;
    }
  }

  contextoBtn.addEventListener("click", verContexto);
  btn.addEventListener("click", function () {
    run(false);
  });
  refreshBtn.addEventListener("click", function () {
    run(true);
  });
  poisBtn.addEventListener("click", buscarSitios);
})();
