/* Fase 2: GPS -> ubicación + tiempo + POIs + recomendación de Claude.
 *
 * Nota crítica: navigator.geolocation SOLO funciona en HTTPS o en localhost.
 * Si abres esto desde el móvil apuntando a http://192.168.x.x, el navegador
 * bloqueará el GPS sin dar un error claro. Prueba en localhost o ya desplegado.
 */

(function () {
  "use strict";

  const btn = document.getElementById("locate-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  const statusEl = document.getElementById("status");

  /* Guardamos la última posición para que "generar otra" no tenga que volver
   * a esperar un fix de GPS, que es la parte más lenta del proceso. */
  let lastCoords = null;

  const SECTIONS = [
    "place-card", "warnings-card", "weather-card", "reco-card", "pois-card",
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

  function renderPlace(place, coords) {
    text("place-label", place.short_label);
    text(
      "place-coords",
      coords.latitude.toFixed(5) + ", " + coords.longitude.toFixed(5) +
        (coords.accuracy ? "  ·  ±" + Math.round(coords.accuracy) + " m" : "")
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

  function renderPois(pois) {
    if (!pois || pois.length === 0) return;
    text("pois-count", String(pois.length));
    const list = document.getElementById("pois-list");
    list.innerHTML = "";
    pois.forEach(function (poi) {
      const li = document.createElement("li");
      const km = poi.distance_m / 1000;
      li.textContent =
        (km < 1 ? poi.distance_m + " m" : km.toFixed(1) + " km") +
        " — " + poi.name + " (" + poi.category + ")";
      list.appendChild(li);
    });
    show("pois-card");
  }

  // --- Flujo principal -----------------------------------------------------

  async function run(refresh) {
    btn.disabled = true;
    refreshBtn.disabled = true;
    hideAll();

    try {
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

      renderPlace(data.place, coords);
      renderWarnings(data.warnings);
      renderWeather(data.weather);
      renderRecommendation(data.recommendation);
      renderPois(data.pois);

      setStatus("");
      refreshBtn.hidden = false;
    } catch (err) {
      const message =
        err.name === "AbortError"
          ? "La consulta tardó demasiado. Inténtalo otra vez."
          : typeof err.code === "number"
          ? geolocationErrorMessage(err)
          : err.message;
      setStatus(message, "error");
    } finally {
      btn.disabled = false;
      refreshBtn.disabled = false;
    }
  }

  btn.addEventListener("click", function () {
    run(false);
  });
  refreshBtn.addEventListener("click", function () {
    run(true);
  });
})();
