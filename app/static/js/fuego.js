/* El mapa de incendios. Contesta "¿hacia dónde me muevo, y hacia dónde no?".
 *
 * Es pantalla propia y no una capa de /mapa: aquel es el registro de dónde has
 * estado (decisión 40) y esto es lo que está pasando ahora y caduca en horas.
 *
 * El reparto de trabajo es el de la decisión 53: la petición al satélite la
 * hace ESTE navegador —el dominio de la NASA no está en la lista blanca del
 * proxy de PythonAnywhere— y el servidor interpreta el CSV. Aquí no se decide
 * nada sobre los datos: solo se pintan.
 *
 * El código de colores es el del propio tutorial de la NASA, y no es adorno:
 * en un incendio grande hay cientos de detecciones acumuladas de días, y lo
 * único que dice hacia dónde va el frente es CUÁLES son de la última hora.
 */

(function () {
  "use strict";

  const ATRIB_OSM =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  const ATRIB_ESRI = "Imágenes &copy; Esri";

  /* Cuatro tramos por antigüedad, del más reciente al más viejo. Se pintan en
   * orden inverso para que los recientes queden ENCIMA: al revés, un foco de
   * hace veinte minutos desaparecería debajo de los de anteayer. */
  const TRAMOS = [
    { horas: 1, color: "#7f0000", etiqueta: "menos de 1 h" },
    { horas: 4, color: "#e02b0a", etiqueta: "1-4 h" },
    { horas: 12, color: "#f28c28", etiqueta: "4-12 h" },
    { horas: Infinity, color: "#f7e017", etiqueta: "más de 12 h" },
  ];

  // A partir de aquí deja de parecer industria. El mismo umbral que usa el
  // servidor para su veredicto (`incendios.FRP_LLAMATIVA_MW`).
  const FRP_POTENTE = 20;

  const controles = document.getElementById("fuego-controles");
  const estadoEl = document.getElementById("fuego-estado");
  const avisoEl = document.getElementById("fuego-aviso");
  const diasEl = document.getElementById("fuego-dias");
  const soloFuertesEl = document.getElementById("fuego-solo-fuertes");
  const listaCard = document.getElementById("fuego-lista-card");
  const listaEl = document.getElementById("fuego-lista");

  const mapa = L.map("fuego-mapa").setView([40.0, -3.7], 6);

  const capaMapa = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: ATRIB_OSM, maxZoom: 19,
  });
  const capaSatelite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: ATRIB_ESRI, maxZoom: 19, maxNativeZoom: 18 }
  );
  capaMapa.addTo(mapa);
  L.control.layers({ "Mapa": capaMapa, "Satélite": capaSatelite }, null,
                   { position: "topright" }).addTo(mapa);

  /* Mismo aviso que en /mapa y por el mismo motivo: sin cobertura los tiles no
   * cargan y el mapa sale gris, pero los círculos siguen ahí porque vienen de
   * nuestro servidor. Se escucha `tileload` (un tile que SÍ cargó) y no `load`,
   * que Leaflet dispara aunque hayan fallado todos (decisión 47). */
  let tilesFallidos = 0;
  [capaMapa, capaSatelite].forEach(function (capa) {
    capa.on("tileerror", function () {
      tilesFallidos += 1;
      if (tilesFallidos === 3) {
        avisoEl.textContent =
          "El fondo del mapa no carga: no hay conexión con el servidor de mapas. " +
          "Los focos sí están, porque vienen del nuestro.";
        avisoEl.hidden = false;
      }
    });
    capa.on("tileload", function () {
      tilesFallidos = 0;
      avisoEl.hidden = true;
    });
  });

  const capaFocos = L.layerGroup().addTo(mapa);
  const capaYo = L.layerGroup().addTo(mapa);

  let detecciones = [];
  let ultimaCarga = 0;

  function decir(mensaje, clase) {
    estadoEl.textContent = mensaje;
    estadoEl.className = "status" + (clase ? " " + clase : "");
  }

  function tramoDe(horas) {
    // Sin hora no se puede fechar: se pinta como lo más viejo en vez de
    // colarlo como reciente. Equivocarse hacia el lado que no alarma de más.
    const h = horas === null || horas === undefined ? Infinity : horas;
    for (const tramo of TRAMOS) {
      if (h <= tramo.horas) return tramo;
    }
    return TRAMOS[TRAMOS.length - 1];
  }

  /* El radio dice la potencia. Raíz cuadrada y no lineal: con lineal, un foco
   * de 300 MW taparía media provincia y no se vería nada de lo que tiene al
   * lado, que es justo lo que hay que mirar para decidir por dónde pasar. */
  function radioDe(frp) {
    return 4 + Math.sqrt(Math.max(frp, 0)) * 1.2;
  }

  function visibles() {
    if (!soloFuertesEl.checked) return detecciones;
    return detecciones.filter(function (d) { return d.frp_mw >= FRP_POTENTE; });
  }

  function pintar() {
    capaFocos.clearLayers();
    const lista = visibles();

    /* De más viejo a más nuevo: Leaflet dibuja en orden de inserción, así que
     * los recientes tienen que ir los últimos para quedar encima. */
    const porAntiguedad = lista.slice().sort(function (a, b) {
      return (b.horas === null ? 1e9 : b.horas) - (a.horas === null ? 1e9 : a.horas);
    });

    porAntiguedad.forEach(function (d) {
      const tramo = tramoDe(d.horas);
      L.circleMarker([d.lat, d.lon], {
        radius: radioDe(d.frp_mw),
        color: tramo.color,
        fillColor: tramo.color,
        fillOpacity: 0.65,
        weight: 1,
        // Clase propia para poder distinguirlos del marcador de "estás aquí",
        // que también es un círculo. Sin ella, contar focos cuenta uno de más.
        className: "foco",
      })
        .bindPopup(
          "<strong>" + d.frp_mw + " MW</strong><br>" +
          (d.horas === null ? "sin hora" : "hace " + d.horas + " h") + "<br>" +
          "a " + d.distancia_km + " km de ti<br>" +
          "confianza: " + (d.confianza || "?")
        )
        .addTo(capaFocos);
    });

    pintarLista(lista);
    return lista.length;
  }

  function pintarLista(lista) {
    const fuertes = lista.slice()
      .sort(function (a, b) { return b.frp_mw - a.frp_mw; })
      .slice(0, 10);

    listaEl.innerHTML = "";
    listaCard.hidden = fuertes.length === 0;

    fuertes.forEach(function (d) {
      const li = document.createElement("li");
      const enlace = document.createElement("a");
      enlace.href = "https://www.google.com/maps/dir/?api=1&destination=" + d.lat + "," + d.lon;
      enlace.rel = "noopener";
      enlace.textContent =
        d.frp_mw + " MW — a " + d.distancia_km + " km" +
        (d.horas === null ? "" : " · hace " + d.horas + " h");
      li.appendChild(enlace);
      listaEl.appendChild(li);
    });
  }

  function urlDeFirms(lat, lon) {
    const clave = controles.dataset.firmsKey;
    if (!clave) return null;

    const grados = parseFloat(controles.dataset.firmsGrados);
    const caja = [
      (lon - grados).toFixed(4), (lat - grados).toFixed(4),
      (lon + grados).toFixed(4), (lat + grados).toFixed(4),
    ].join(",");

    return [controles.dataset.firmsBase, clave, controles.dataset.firmsSensor,
            caja, diasEl.value].join("/");
  }

  function posicionActual() {
    return new Promise(function (resolve, reject) {
      if (!("geolocation" in navigator)) {
        reject(new Error("Este navegador no sabe dónde estás."));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (p) { resolve(p.coords); }, reject,
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 }
      );
    });
  }

  let coords = null;

  async function cargar() {
    if (!controles.dataset.firmsKey) {
      decir("Falta FIRMS_MAP_KEY en el servidor: no se puede consultar el satélite.", "error");
      return;
    }

    ultimaCarga = Date.now();
    decir("Consultando el satélite…");

    try {
      if (!coords) {
        coords = await posicionActual();
        mapa.setView([coords.latitude, coords.longitude], 8);
        L.circleMarker([coords.latitude, coords.longitude], {
          radius: 6, color: "#1b3a2f", fillColor: "#fff", fillOpacity: 1, weight: 2,
        }).bindPopup("Estás aquí").addTo(capaYo);
      }

      const csv = await (await fetch(urlDeFirms(coords.latitude, coords.longitude))).text();

      const respuesta = await fetch("/api/incendios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: coords.latitude, lon: coords.longitude, csv: csv, mapa: true,
        }),
      });

      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }

      const data = await respuesta.json().catch(function () { return {}; });
      if (!respuesta.ok || !data.detecciones) {
        /* Que no se haya podido mirar NO se pinta como "no hay nada": eso sería
         * tranquilizar sin haber mirado (decisión 22). */
        decir((data.fuente && data.fuente.motivo) ||
              "No se pudo consultar el satélite.", "error");
        return;
      }

      detecciones = data.detecciones;
      const pintados = pintar();

      const dias = diasEl.value;
      if (detecciones.length === 0) {
        decir("Ningún foco en " + dias + " día(s) a la redonda.");
      } else if (pintados === 0) {
        decir(
          detecciones.length + " detecciones, ninguna potente. Suelen ser hornos, " +
          "industria o quemas: quita el filtro para verlas."
        );
      } else {
        decir(pintados + " focos potentes de " + detecciones.length +
              " detecciones (" + dias + " día(s)).", "ok");
      }
    } catch (err) {
      decir(
        err.name === "TypeError"
          ? "No se pudo conectar con el satélite. Comprueba la cobertura."
          : (err.message || "No se pudo consultar el satélite."),
        "error"
      );
    }
  }

  diasEl.addEventListener("change", cargar);
  soloFuertesEl.addEventListener("change", function () {
    // Filtrar NO vuelve a pedir nada: los datos ya están, y en un móvil con
    // mala cobertura repetir la consulta por marcar una casilla es tiempo
    // regalado.
    const pintados = pintar();
    if (detecciones.length && pintados === 0) {
      decir("Ninguna detección potente. Quita el filtro para ver las demás.");
    }
  });
  document.getElementById("fuego-refrescar").addEventListener("click", cargar);

  /* Al volver a la pestaña se recarga, con el mismo anti-rebote de 3 s que el
   * Mapa y el Perfil (decisión 46): cambiar de app y volver es constante en un
   * móvil, y aquí cada recarga es una petición a la NASA. */
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - ultimaCarga < 3000) return;
    cargar();
  });

  cargar();
})();
