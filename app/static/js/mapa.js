/* El mapa del viaje: el trayecto en orden, y cómo volver a recorrerlo.
 *
 * Leaflet se sirve desde `/static/vendor/leaflet/`, no desde un CDN. Un CDN es
 * un tercero más que puede caerse, y con mala cobertura el navegador tiene más
 * probabilidades de tener nuestro archivo en caché que de alcanzar unpkg.com.
 * La versión se fija igual que las dependencias de Python: ver
 * `vendor/leaflet/VERSION.md`.
 *
 * Los TILES sí los pide el navegador a OpenStreetMap. Es el punto donde es
 * fácil razonar mal: la lista blanca del proxy de PythonAnywhere afecta solo
 * al tráfico saliente DEL SERVIDOR, y aquí el servidor no interviene.
 *
 * Sin cobertura: los tiles no cargan y el mapa sale gris, pero el trayecto,
 * las chinchetas y el listado siguen apareciendo, porque salen de nuestro
 * servidor. La página lo dice en voz alta en vez de disimularlo.
 */

(function () {
  "use strict";

  /* Dos fondos, y ninguno sobra:
   *
   *   Mapa      lee mejor. Los nombres de los pueblos, las carreteras y los
   *             senderos están escritos, que es lo que hace falta para saber
   *             POR DÓNDE fuiste.
   *   Satélite  se reconoce. Ves la playa, el bosque y el mirador de verdad,
   *             que es lo que hace falta para RECORDAR dónde estuviste.
   *
   * El satélite va con una capa de etiquetas encima: sin nombres es bonito y
   * no se sabe dónde estás. La elección se guarda, porque cambiarla en cada
   * visita sería un peaje por una preferencia que no cambia.
   *
   * La atribución de cada uno es obligatoria por sus condiciones de uso, no un
   * adorno que se pueda quitar para ganar sitio. */
  const ATRIB_OSM =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  const ATRIB_ESRI = "Imágenes &copy; Esri, Maxar, Earthstar Geographics";

  const CLAVE_CAPA = "roadtrip-capa";

  /* Centro y zoom de partida cuando todavía no hay nada: el norte de España.
   * Un mapa vacío centrado en la isla Null a zoom 2 parece roto. */
  const INICIO = [43.2, -5.0];
  const ZOOM_INICIAL = 7;
  const ZOOM_MOMENTO = 13;

  /* Cuánto dura cada momento al revivir el viaje. 1,6 s es lo que tarda en
   * leerse una nota corta; más rápido no da tiempo y más lento aburre. */
  const MS_POR_MOMENTO = 1600;

  const mapaEl = document.getElementById("mapa");
  if (!mapaEl) return;

  const estadoEl = document.getElementById("notas-estado");
  const avisoEl = document.getElementById("mapa-aviso");
  const avisosRutaEl = document.getElementById("ruta-avisos");
  const filtroEl = document.getElementById("filtro-anio");
  const botonRevivir = document.getElementById("revivir-btn");
  const slider = document.getElementById("revivir-slider");
  const pieRevivir = document.getElementById("revivir-pie");

  const mapa = L.map("mapa").setView(INICIO, ZOOM_INICIAL);

  const capaMapa = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: ATRIB_OSM,
    maxZoom: 19,
  });

  /* `maxNativeZoom` es lo que evita el fallo que parece un bug: por encima del
   * zoom que Esri sirve de verdad, pedir más tiles devuelve cuadros en blanco.
   * Con esto Leaflet amplía el último tile bueno, que se ve borroso pero se
   * ve. Borroso es peor que nítido; blanco es peor que las dos cosas. */
  const capaSatelite = L.layerGroup([
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: ATRIB_ESRI, maxZoom: 19, maxNativeZoom: 18 }
    ),
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, maxNativeZoom: 18 }
    ),
  ]);

  const capasBase = { "Mapa": capaMapa, "Satélite": capaSatelite };
  const guardada = window.localStorage.getItem(CLAVE_CAPA);
  const capaInicial = capasBase[guardada] || capaMapa;
  capaInicial.addTo(mapa);
  L.control.layers(capasBase, null, { position: "topright" }).addTo(mapa);

  mapa.on("baselayerchange", function (evento) {
    window.localStorage.setItem(CLAVE_CAPA, evento.name);
    /* El contador de tiles fallidos se reinicia al cambiar de fondo: los que
     * fallaron con el anterior no dicen nada del nuevo, y sin esto el aviso de
     * "sin conexión" saltaría al tercer cambio de capa con la red perfecta. */
    tilesFallidos = 0;
    avisoEl.hidden = true;
  });

  const capaTrayecto = L.layerGroup().addTo(mapa);
  const capaChinchetas = L.layerGroup().addTo(mapa);
  const capaFoco = L.layerGroup().addTo(mapa);

  let momentos = [];      // la línea de tiempo completa que se está mirando
  let ubicados = [];      // solo los que tienen coordenadas: los que se pueden recorrer
  let marcadores = new Map();  // índice de momento -> marcador de Leaflet
  let reproduciendo = null;    // id del temporizador, o null

  /* Un solo tile que no carga no significa nada (pasa al hacer zoom rápido);
   * varios seguidos sí. Se avisa a partir del tercero para no dar una alarma
   * falsa en cada gesto. */
  let tilesFallidos = 0;

  /* Se enganchan los dos fondos, no solo el activo: en Leaflet los eventos de
   * una capa no llegan solos al mapa, así que vigilar únicamente el de arranque
   * dejaría el aviso mudo en cuanto cambiaras a satélite. */
  [capaMapa].concat(capaSatelite.getLayers()).forEach(function (capa) {
    capa.on("tileerror", function () {
      tilesFallidos += 1;
      if (tilesFallidos === 3) {
        avisoEl.textContent =
          "El fondo del mapa no carga: no hay conexión con el servidor de mapas. " +
          "El trayecto y los momentos sí están, porque vienen del nuestro.";
        avisoEl.hidden = false;
      }
    });
    capa.on("load", function () {
      tilesFallidos = 0;
      avisoEl.hidden = true;
    });
  });

  // --- Utilidades -----------------------------------------------------------

  function fechaLegible(iso) {
    /* La hora ya viene en el huso del sitio donde pasó: `created_at_local` en
     * una nota, la hora de la cámara en una foto. Se formatea sin volver a
     * convertir, porque lo que se recuerda es la hora que marcaba el reloj
     * allí, no la del navegador que lo mira en enero desde el sofá. */
    if (!iso) return "";
    const ymd = iso.slice(0, 10).split("-");
    return ymd[2] + "/" + ymd[1] + "/" + ymd[0] + " · " + iso.slice(11, 16);
  }

  function diaLegible(iso) {
    const ymd = iso.split("-");
    return ymd[2] + "/" + ymd[1] + "/" + ymd[0];
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

  function contenidoMomento(momento) {
    /* Se construye con nodos del DOM y no con una cadena de HTML: el texto lo
     * escribe una persona y con innerHTML acabaría ejecutándose. Jinja escapa
     * las plantillas, pero esto no pasa por Jinja. */
    const caja = document.createElement("div");

    const titulo = document.createElement("strong");
    if (momento.tipo === "foto") {
      titulo.textContent = "📷  " + momento.archivo;
    } else {
      titulo.textContent =
        momento.lugar ||
        (momento.lat === null ? "Sin sitio" : momento.lat.toFixed(4) + ", " + momento.lon.toFixed(4));
    }
    caja.appendChild(titulo);
    caja.appendChild(document.createElement("br"));

    if (momento.texto) {
      const cuerpo = document.createElement("span");
      cuerpo.textContent = momento.texto;
      caja.appendChild(cuerpo);
      caja.appendChild(document.createElement("br"));
    }

    const pie = document.createElement("small");
    pie.className = "muted";
    let detalle = fechaLegible(momento.cuando);
    if (momento.altitud !== null && momento.altitud !== undefined) {
      detalle += "  ·  " + Math.round(momento.altitud) + " m";
    }
    pie.textContent = detalle;
    caja.appendChild(pie);

    return caja;
  }

  // --- Pintado --------------------------------------------------------------

  function pintarProgreso(progreso, resumen) {
    const cifras = document.getElementById("progreso-cifras");
    cifras.innerHTML = "";
    cifras.appendChild(cifra(resumen.notas, resumen.notas === 1 ? "nota" : "notas"));
    cifras.appendChild(cifra(resumen.fotos, resumen.fotos === 1 ? "foto" : "fotos"));
    cifras.appendChild(cifra(progreso.lugares, "sitios"));
    cifras.appendChild(cifra(resumen.dias, "días"));
    /* "En línea recta" no es un tecnicismo que sobre: entre dos fotos
     * separadas por dos horas de carretera de montaña hay muchas más curvas
     * que la recta que las une, así que este número es un MÍNIMO. Llamarlo
     * "kilómetros del viaje" a secas sería prometer lo que no es. */
    cifras.appendChild(cifra(Math.round(resumen.km_linea_recta), "km", "en línea recta"));
    cifras.appendChild(cifra(progreso.racha_maxima, "racha", "días seguidos"));

    const tablero = progreso.tablero;
    document.getElementById("tablero-resumen").textContent =
      "Comunidades: " + tablero.completadas + " de " + tablero.total;

    const lista = document.getElementById("tablero-casillas");
    lista.innerHTML = "";
    tablero.casillas.forEach(function (casilla) {
      const li = document.createElement("li");
      li.className = "casilla" + (casilla.visitada ? " casilla-hecha" : "");
      li.textContent = casilla.nombre;
      lista.appendChild(li);
    });

    /* Lo que no encaja con ninguna comunidad conocida se enseña aparte en vez
     * de descartarse: una nota de Portugal no puede desaparecer del recuento. */
    const otras = document.getElementById("tablero-otras");
    otras.hidden = tablero.otras.length === 0;
    otras.textContent = tablero.otras.length
      ? "Fuera del tablero: " + tablero.otras.join(", ")
      : "";

    const visitadosCard = document.getElementById("visitados-card");
    const visitados = document.getElementById("visitados-lista");
    visitados.innerHTML = "";
    visitadosCard.hidden = progreso.mas_visitados.length === 0;
    progreso.mas_visitados.forEach(function (lugar) {
      const li = document.createElement("li");
      li.textContent =
        lugar.etiqueta + " — " + lugar.dias + " días distintos, " +
        lugar.visitas + (lugar.visitas === 1 ? " nota" : " notas");
      li.addEventListener("click", function () {
        mapa.setView([lugar.lat, lugar.lon], ZOOM_MOMENTO);
      });
      visitados.appendChild(li);
    });
  }

  function pintarAvisos(resumen) {
    /* Lo que el mapa NO puede enseñar, dicho en voz alta. Una foto sin fecha o
     * sin GPS no aparece en el trayecto, y callarlo haría creer que el viaje
     * está entero (decisión 9). */
    const plural = function (n, singular, plural_) {
      return n + " " + (n === 1 ? singular : plural_);
    };

    const partes = [];
    if (resumen.fotos_sin_fecha) {
      partes.push(
        plural(resumen.fotos_sin_fecha, "foto sin fecha", "fotos sin fecha") +
          ": no se pueden colocar en el viaje"
      );
    }
    if (resumen.fotos_sin_lugar) {
      partes.push(
        plural(resumen.fotos_sin_lugar, "foto sin GPS", "fotos sin GPS") +
          ": cuentan en el relato, no en el mapa"
      );
    }
    if (resumen.saltos_ignorados) {
      partes.push(
        plural(resumen.saltos_ignorados, "salto", "saltos") +
          " de más de 300 km no suman kilómetros (un vuelo no es un tramo recorrido)"
      );
    }
    avisosRutaEl.hidden = partes.length === 0;
    avisosRutaEl.textContent = partes.join(" · ");
  }

  function pintarTrayecto() {
    capaTrayecto.clearLayers();
    capaChinchetas.clearLayers();
    marcadores = new Map();

    if (ubicados.length > 1) {
      /* Una línea, no una por tramo: Leaflet dibuja mucho mejor una polyline
       * de 300 puntos que 300 polylines de dos. */
      L.polyline(
        ubicados.map(function (m) { return [m.lat, m.lon]; }),
        { color: "#1b3a2f", weight: 3, opacity: 0.65 }
      ).addTo(capaTrayecto);
    }

    momentos.forEach(function (momento, indice) {
      if (momento.lat === null || momento.lon === null) return;
      /* Las fotos van como círculo pequeño y las notas como chincheta: en un
       * día con treinta fotos y una nota, la nota tiene que poder encontrarse. */
      const marcador =
        momento.tipo === "foto"
          ? L.circleMarker([momento.lat, momento.lon], {
              radius: 5, color: "#1b3a2f", weight: 2,
              fillColor: "#7fbfa3", fillOpacity: 0.9,
            })
          : L.marker([momento.lat, momento.lon]);

      marcador.bindPopup(contenidoMomento(momento));
      capaChinchetas.addLayer(marcador);
      marcadores.set(indice, marcador);
    });

    if (ubicados.length) {
      mapa.fitBounds(
        ubicados.map(function (m) { return [m.lat, m.lon]; }),
        { padding: [40, 40], maxZoom: ZOOM_MOMENTO }
      );
    }
  }

  function pintarDias(dias) {
    const contenedor = document.getElementById("dias-lista");
    contenedor.innerHTML = "";

    dias.forEach(function (jornada) {
      const bloque = document.createElement("section");
      bloque.className = "jornada";

      const cabecera = document.createElement("h3");
      cabecera.textContent = diaLegible(jornada.dia);
      const km = document.createElement("span");
      km.className = "muted";
      km.textContent =
        "  " + jornada.momentos.length +
        (jornada.momentos.length === 1 ? " momento" : " momentos") +
        (jornada.km_linea_recta ? "  ·  " + jornada.km_linea_recta + " km" : "");
      cabecera.appendChild(km);
      bloque.appendChild(cabecera);

      const lista = document.createElement("ul");
      lista.className = "momentos";
      jornada.momentos.forEach(function (momento) {
        const li = document.createElement("li");
        li.className = "momento momento-" + momento.tipo;

        const hora = document.createElement("span");
        hora.className = "momento-hora";
        hora.textContent = momento.cuando.slice(11, 16);
        li.appendChild(hora);

        const cuerpo = document.createElement("span");
        cuerpo.className = "momento-texto";
        cuerpo.textContent =
          momento.tipo === "foto"
            ? "📷  " + momento.archivo
            : momento.texto || "(sin texto)";
        li.appendChild(cuerpo);

        if (momento.lat !== null) {
          li.addEventListener("click", function () {
            irA(momentos.indexOf(momento));
          });
        }
        lista.appendChild(li);
      });
      bloque.appendChild(lista);
      contenedor.appendChild(bloque);
    });
  }

  // --- Revivir el viaje -----------------------------------------------------

  function irA(indice) {
    if (indice < 0 || indice >= momentos.length) return;
    const momento = momentos[indice];
    slider.value = String(indice);

    pieRevivir.textContent =
      indice + 1 + " de " + momentos.length + "  ·  " + fechaLegible(momento.cuando);

    capaFoco.clearLayers();
    if (momento.lat === null || momento.lon === null) return;

    mapa.setView([momento.lat, momento.lon], Math.max(mapa.getZoom(), ZOOM_MOMENTO));
    /* Un halo alrededor del momento actual. Sin él, al recorrer un pueblo con
     * diez fotos juntas no se distingue en cuál estás. */
    L.circleMarker([momento.lat, momento.lon], {
      radius: 16, color: "#d19a2e", weight: 3, fill: false,
    }).addTo(capaFoco);

    const marcador = marcadores.get(indice);
    if (marcador) marcador.openPopup();
  }

  function parar() {
    if (reproduciendo !== null) {
      clearInterval(reproduciendo);
      reproduciendo = null;
    }
    botonRevivir.textContent = "▶  Revivir el viaje";
  }

  function reproducir() {
    if (reproduciendo !== null) {
      parar();
      return;
    }
    if (!momentos.length) return;

    /* Si ya se había llegado al final, empieza otra vez desde el principio en
     * vez de quedarse quieto pulsando play. */
    let indice = Number(slider.value);
    if (indice >= momentos.length - 1) indice = -1;

    botonRevivir.textContent = "⏸  Pausa";
    reproduciendo = setInterval(function () {
      indice += 1;
      if (indice >= momentos.length) {
        parar();
        return;
      }
      irA(indice);
    }, MS_POR_MOMENTO);
  }

  botonRevivir.addEventListener("click", reproducir);
  slider.addEventListener("input", function () {
    parar();  // Tocar la barra manda sobre la reproducción.
    irA(Number(slider.value));
  });

  // --- Carga ----------------------------------------------------------------

  function pintarAnios(porAnio, seleccionado) {
    const anios = Object.keys(porAnio).sort().reverse();
    /* El selector solo aparece cuando hay más de un año que comparar. Con un
     * único verano de datos sería un desplegable de un elemento: ruido. */
    filtroEl.hidden = anios.length < 2;
    if (anios.length < 2) return;

    filtroEl.innerHTML = "";
    const todos = document.createElement("option");
    todos.value = "";
    todos.textContent = "Todos los años";
    filtroEl.appendChild(todos);

    anios.forEach(function (anio) {
      const opcion = document.createElement("option");
      opcion.value = anio;
      opcion.textContent =
        anio + " (" + porAnio[anio].notas + " notas, " + porAnio[anio].lugares + " sitios)";
      filtroEl.appendChild(opcion);
    });
    filtroEl.value = seleccionado || "";
  }

  async function cargar(anio) {
    parar();
    estadoEl.textContent = "Cargando el viaje…";
    estadoEl.className = "status";
    try {
      const url = anio ? "/api/ruta?year=" + encodeURIComponent(anio) : "/api/ruta";
      const respuesta = await fetch(url);
      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!respuesta.ok) {
        throw new Error("El servidor respondió " + respuesta.status + ".");
      }
      const datos = await respuesta.json();

      momentos = datos.momentos;
      ubicados = momentos.filter(function (m) {
        return m.lat !== null && m.lon !== null;
      });

      pintarProgreso(datos.progreso, datos.resumen);
      pintarAvisos(datos.resumen);
      pintarAnios(datos.progreso.por_anio, anio);
      pintarTrayecto();
      pintarDias(datos.dias);

      slider.max = String(Math.max(0, momentos.length - 1));
      slider.value = "0";
      const hay = momentos.length > 0;
      botonRevivir.disabled = !hay;
      slider.disabled = !hay;
      pieRevivir.textContent = hay
        ? momentos.length + " momentos, de " + fechaLegible(datos.resumen.primera) +
          " a " + fechaLegible(datos.resumen.ultima)
        : "";

      estadoEl.textContent = hay
        ? ""
        : "Todavía no hay nada. Marca un sitio desde la pantalla principal, " +
          "o importa las fotos con tools/importar_fotos.py.";
    } catch (err) {
      /* Aquí no hay degradación posible: sin datos no hay viaje que enseñar.
       * Se dice qué ha pasado en vez de dejar la página en blanco.
       *
       * El TypeError se traduce porque su texto en Safari es «Load failed», que
       * no dice nada y suena a pantalla rota; lo normal es que el único worker
       * del plan gratuito estuviera ocupado con el contexto de Inicio. */
      estadoEl.textContent =
        err.name === "TypeError"
          ? "No se pudo conectar con el servidor. Comprueba la cobertura y recarga."
          : "No se pudo cargar el viaje: " + (err.message || err);
      estadoEl.className = "status error";
    }
  }

  filtroEl.addEventListener("change", function () {
    cargar(filtroEl.value);
  });

  cargar("");
})();
