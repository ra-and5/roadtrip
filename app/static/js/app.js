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
  /* Todo lo que se esconde antes de volver a pedir el contexto. Las cuatro
   * señales entran en la lista aunque vivan DENTRO del panel: si no se
   * escondieran, al cambiar de sitio se quedaría la señal del anterior mientras
   * llega la nueva — un veredicto de otro sitio, con su color, mientras la
   * pantalla dice que ya estás aquí. */
  const SECTIONS = [
    "place-card", "warnings-card", "reco-card",
    "weather-card", "agua-card", "luna-card", "fuego-card",
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

  /* Pinta una señal del panel: su valor y el grado que le da color.
   *
   * El grado va en un `data-` del contenedor y no como clase del texto, y esa
   * diferencia es la que permite teñir el filete, el fondo y la letra con una
   * sola regla de CSS. Antes cada veredicto era un `.tag` suelto dentro de un
   * párrafo, y para saber si podías salir a andar había que leer cuatro
   * tarjetas enteras. */
  function senal(idCaja, idValor, texto_, grado) {
    const caja = document.getElementById(idCaja);
    document.getElementById(idValor).textContent = texto_;
    caja.dataset.grado = grado;
    caja.hidden = false;
  }

  /* En una casilla de cuatro columnas no cabe «desaconsejado», y cortarlo con
   * puntos suspensivos deja «desacon…», que no dice nada. Se acorta aquí, en la
   * presentación, y NO en `weather_context.py`: allí el valor lo lee también el
   * prompt del modelo, y «no» a secas se leería peor que «desaconsejado». */
  const AGUA_CORTO = {
    excelente: "buena",
    aceptable: "regular",
    desaconsejado: "no",
    "sin datos": "—",
  };

  function renderWeather(weather) {
    if (!weather) return;
    text("weather-summary", weather.summary);

    /* La etiqueta ya la pone el HTML («Aire libre»), así que el valor es solo
     * el veredicto: repetirla aquí daría «Aire libre  Aire libre: bueno». */
    senal("weather-card", "weather-outdoor", weather.outdoor_rating, weather.outdoor_rating);
    senal(
      "agua-card", "weather-water",
      AGUA_CORTO[weather.water_sports.rating] || weather.water_sports.rating,
      weather.water_sports.suitable ? "bueno" : "malo"
    );

    text("weather-water-reason", weather.water_sports.reason);

    if (weather.sunrise && weather.sunset) {
      text(
        "weather-sun",
        "Amanece " + weather.sunrise.slice(-5) + " · Anochece " + weather.sunset.slice(-5)
      );
    }
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

    /* En la señal va la ILUMINACIÓN, que es un número y se lee de un vistazo;
     * el veredicto entero («se puede caminar sin frontal») está debajo, en el
     * detalle. Una frase de cinco palabras dentro de una casilla de panel no se
     * lee en marcha: se adivina. El color sí lo pone el veredicto, que es lo
     * que de verdad decide si sales de noche. */
    senal(
      "luna-card", "luna-veredicto",
      luna.fase.iluminacion_pct.toFixed(0) + "%",
      luna.veredicto.hay_luz ? "bueno" : "malo"
    );

    text("luna-motivo", luna.veredicto.motivo);
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


  /* --- Fuego cerca (NASA FIRMS) ------------------------------------------
   *
   * La petición al satélite la hace ESTE navegador y no el servidor, porque el
   * dominio de la NASA no está en la lista blanca del proxy de PythonAnywhere
   * (decisión 21) y FIRMS permite CORS. Pero el veredicto NO se decide aquí: el
   * CSV se manda a `/api/incendios` y lo interpreta Python, donde los umbrales
   * se pueden probar sin abrir un navegador (decisión 5, como el oleaje).
   *
   * Va aparte del contexto y después de él: si la NASA tarda o falla, la
   * pantalla ya ha pintado dónde estás y qué tiempo hace. */
  async function verFuego(lat, lon) {
    const card = document.getElementById("fuego-card");
    if (!card) return;                       // sin clave configurada

    try {
      const url = urlDeFirms(lat, lon);
      if (!url) return;

      const csv = await (await fetch(url)).text();

      const respuesta = await fetch("/api/incendios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: lat, lon: lon, csv: csv }),
      });
      const data = await respuesta.json().catch(function () { return {}; });
      if (!respuesta.ok || !data.situacion) {
        /* Que no se haya podido mirar NO se pinta como "todo limpio": eso
         * sería tranquilizar sin haber mirado, que es el fallo del que avisa
         * la decisión 22. Se dice, y se dice corto. */
        senal("fuego-card", "fuego-veredicto", "sin datos", "");
        text("fuego-detalle", "No se pudo consultar el satélite de incendios.");
        document.getElementById("fuego-detalle-lista").hidden = true;
        return;
      }
      renderFuego(data.situacion);
    } catch (err) {
      senal("fuego-card", "fuego-veredicto", "sin datos", "");
      text("fuego-detalle", "No se pudo consultar el satélite: " + mensajeDeError(err));
    }
  }

  /* El sensor, el radio y los días los pone el SERVIDOR en los `data-` de la
   * tarjeta, y aquí solo se concatenan. Así siguen siendo una sola definición
   * —viven en `incendios.py`— en vez de dos copias que se separan sin dar
   * ningún error. Sin clave configurada, esto devuelve null y no se consulta
   * nada. */
  function urlDeFirms(lat, lon) {
    const card = document.getElementById("fuego-card");
    const clave = card.dataset.firmsKey;
    if (!clave) return null;

    const grados = parseFloat(card.dataset.firmsGrados);
    const caja = [
      (lon - grados).toFixed(4), (lat - grados).toFixed(4),
      (lon + grados).toFixed(4), (lat + grados).toFixed(4),
    ].join(",");

    return [card.dataset.firmsBase, clave, card.dataset.firmsSensor,
            caja, card.dataset.firmsDias].join("/");
  }

  /* Del nivel que calcula Python a lo que se lee en la señal. El texto corto y
   * el grado se deciden aquí porque son presentación; QUÉ nivel es —dónde está
   * la frontera entre un horno industrial y un incendio— lo decide
   * `incendios.py`, que es donde se puede probar sin abrir un navegador. */
  const FUEGO_SENAL = {
    tranquilo: { texto: "sin focos", grado: "bueno" },
    puntos: { texto: "puntos", grado: "regular" },
    foco: { texto: "activo", grado: "peligroso" },
  };

  function renderFuego(situacion) {
    /* El veredicto entero («foco activo a 12 km, 117 MW») va al detalle: en la
     * casilla del panel no cabe y se leería a medias, que en esto es peor que
     * no leerlo. */
    const marca = FUEGO_SENAL[situacion.nivel] || FUEGO_SENAL.tranquilo;
    senal("fuego-card", "fuego-veredicto", marca.texto, marca.grado);
    text("fuego-detalle", situacion.veredicto + "  " + situacion.detalle);

    const detalle = document.getElementById("fuego-detalle-lista");
    const lista = document.getElementById("fuego-lista");
    lista.innerHTML = "";

    if (situacion.detecciones && situacion.detecciones.length) {
      text("fuego-count", String(situacion.cuantas));
      situacion.detecciones.forEach(function (d) {
        const li = document.createElement("li");
        const enlace = document.createElement("a");
        enlace.href = "https://www.google.com/maps/dir/?api=1&destination=" + d.lat + "," + d.lon;
        enlace.rel = "noopener";
        enlace.textContent =
          d.distancia_km + " km — " + d.frp_mw + " MW · " + d.fecha +
          (d.de_noche ? " (noche)" : " (día)");
        li.appendChild(enlace);
        lista.appendChild(li);
      });
      detalle.hidden = false;
    } else {
      detalle.hidden = true;
    }
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

      // Después del contexto y sin `await`: si la NASA tarda, la pantalla ya
      // está pintada. Es la lección de la decisión 33 aplicada a una fuente
      // nueva — lo lento no se mete en el camino de lo que responde rápido.
      verFuego(position.coords.latitude, position.coords.longitude);
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

  /* El contexto se pide SOLO al abrir, y el botón pasa a ser «Actualizar».
   *
   * Es la consecuencia de la decisión 32 llevada hasta el final: el contexto
   * cuesta 0,18 s con la caché caliente y no gasta ni un token, así que exigir
   * una pulsación para saber dónde estás era cobrar un peaje por lo único que
   * esta pantalla tiene que enseñar siempre. Lo que sigue detrás de un botón es
   * la RECOMENDACIÓN, que cuesta tokens y segundos — esa parte de la decisión 35
   * no cambia.
   *
   * El botón no sobra: el GPS puede tardar, denegarse o dar un sitio viejo, y
   * entonces hace falta poder reintentar sin recargar la página. */
  verContexto();
})();
