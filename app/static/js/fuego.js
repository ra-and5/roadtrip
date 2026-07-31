/* El mapa de focos, al final de Inicio. Contesta "¿hacia dónde me muevo, y
 * hacia dónde no?" sin convertir cada punto caliente en una alarma.
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
  if (!controles) return;
  const estadoEl = document.getElementById("fuego-estado");
  const avisoEl = document.getElementById("fuego-aviso");
  const diasEl = document.getElementById("fuego-dias");
  const ambitoEl = document.getElementById("fuego-ambito");
  const soloFuertesEl = document.getElementById("fuego-solo-fuertes");

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

  let detecciones = [];   // los píxeles crudos, solo para contar
  let focos = [];         // lo que se pinta: detecciones ya agrupadas por fuego
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

  /* El filtro se aplica al FOCO y no a cada detección: lo que dice si un grupo
   * es industria o un incendio es su pico de potencia, no cada píxel suelto. */
  function visibles() {
    if (!soloFuertesEl.checked) return focos;
    return focos.filter(function (f) { return f.frp_max_mw >= FRP_POTENTE; });
  }

  function pintar() {
    capaFocos.clearLayers();
    const lista = visibles();

    /* De más viejo a más nuevo: Leaflet dibuja en orden de inserción, así que
     * los recientes tienen que ir los últimos para quedar encima. */
    const porAntiguedad = lista.slice().sort(function (a, b) {
      return (b.horas === null ? 1e9 : b.horas) - (a.horas === null ? 1e9 : a.horas);
    });

    // Sin popup con números: color (antigüedad) y radio (potencia) son toda
    // la lectura que se pide aquí. El texto de cada detección —MW, km,
    // horas— se quitó a propósito (decisión 59): el punto en el mapa ya dice
    // "aquí y así de reciente", y la cifra exacta invitaba a comparar 0,62
    // contra 1,85 MW, que es ruido para decidir una ruta.
    porAntiguedad.forEach(function (f) {
      const tramo = tramoDe(f.horas);
      L.circleMarker([f.lat, f.lon], {
        radius: radioDe(f.frp_max_mw),
        color: tramo.color,
        fillColor: tramo.color,
        fillOpacity: 0.65,
        weight: 1,
        // Clase propia para poder distinguirlos del marcador de "estás aquí",
        // que también es un círculo. Sin ella, contar focos cuenta uno de más.
        className: "foco",
      }).addTo(capaFocos);
    });

    return lista.length;
  }

  /* La caja de búsqueda: alrededor de ti, o el país entero. Los dos rectángulos
   * salen del servidor (`incendios.py`), aquí solo se elige cuál. */
  function urlDeFirms(lat, lon) {
    const clave = controles.dataset.firmsKey;
    if (!clave) return null;

    let caja;
    if (ambitoEl.value === "espana") {
      caja = controles.dataset.firmsEspana;
    } else {
      const grados = parseFloat(controles.dataset.firmsGrados);
      caja = [
        (lon - grados).toFixed(4), (lat - grados).toFixed(4),
        (lon + grados).toFixed(4), (lat + grados).toFixed(4),
      ].join(",");
    }

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
        L.circleMarker([coords.latitude, coords.longitude], {
          radius: 6, color: "#1b3a2f", fillColor: "#fff", fillOpacity: 1, weight: 2,
        }).bindPopup("Estás aquí").addTo(capaYo);
      }

      /* El encuadre lo manda el ámbito: mirando el país entero, centrar en ti
       * con zoom de comarca deja el mapa enseñando tu barrio mientras arde
       * Galicia. */
      if (ambitoEl.value === "espana") {
        mapa.setView([40.0, -3.7], 5);
      } else {
        mapa.setView([coords.latitude, coords.longitude], 8);
      }

      apuntarEnlaceNasa(coords.latitude, coords.longitude);

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
      focos = data.focos || [];
      const pintados = pintar();

      const dias = diasEl.value;
      const donde = ambitoEl.value === "espana" ? "en España" : "a la redonda";
      /* Se cuentan FOCOS y no detecciones. Un incendio enciende decenas de
       * píxeles pegados, así que "211 detecciones" se lee como doscientos
       * fuegos y en el mapa se ven dos manchas — el número y lo que se ve
       * decían cosas distintas, y el que estaba mal era el número. */
      if (focos.length === 0) {
        decir("Ningún foco en " + dias + " día(s) " + donde + ".");
      } else if (pintados === 0) {
        decir(
          focos.length + " focos, ninguno potente. Suelen ser hornos, industria " +
          "o quemas agrícolas: quita el filtro para verlos."
        );
      } else {
        decir(pintados + " focos activos " + donde + " en " + dias + " día(s).", "ok");
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

  /* El enlace se apunta a donde estás. El formato del hash es el suyo:
   * `#d:24hrs;@lon,lat,zoomz` — con el orden lon,lat, que es al revés del que
   * usa todo lo demás en este proyecto y por eso está escrito aquí. */
  function apuntarEnlaceNasa(lat, lon) {
    const enlace = document.getElementById("fuego-nasa");
    const dias = diasEl.value === "1" ? "24hrs" : diasEl.value + "days";
    const zoom = ambitoEl.value === "espana" ? 6 : 9;
    enlace.href = "https://firms.modaps.eosdis.nasa.gov/map/#d:" + dias +
                  ";@" + lon.toFixed(1) + "," + lat.toFixed(1) + "," + zoom + "z";
  }

  /* El enlace ya lleva `target="_blank"`, que basta en un navegador normal.
   * Pero en una PWA instalada (modo standalone, sin barra de Safari) un enlace
   * normal a veces navega DENTRO de la propia app en lugar de saltar fuera, y
   * ahí no hay "atrás": la app se ha ido. `window.open` con `noopener` fuerza
   * un contexto de navegador aparte del que sí se sale con el cambiador de
   * apps del sistema, y es lo que de verdad soluciona el enlace atrapado. */
  document.getElementById("fuego-nasa").addEventListener("click", function (evento) {
    evento.preventDefault();
    window.open(this.href, "_blank", "noopener,noreferrer");
  });

  diasEl.addEventListener("change", cargar);
  ambitoEl.addEventListener("change", cargar);
  soloFuertesEl.addEventListener("change", function () {
    // Filtrar NO vuelve a pedir nada: los datos ya están, y en un móvil con
    // mala cobertura repetir la consulta por marcar una casilla es tiempo
    // regalado.
    const pintados = pintar();
    if (detecciones.length && pintados === 0) {
      decir("Ningún foco potente. Quita el filtro para ver los demás.");
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

  decir("Pulsa Actualizar si quieres mirar la ruta.");
})();
