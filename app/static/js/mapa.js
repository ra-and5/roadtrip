/* El mapa acumulado del viaje (Fase 3).
 *
 * Leaflet se sirve desde `/static/vendor/leaflet/`, no desde un CDN. Un CDN es
 * un tercero más que puede caerse, y en un viaje por sitios con mala cobertura
 * el navegador tiene más probabilidades de tener nuestro archivo en caché que
 * de alcanzar unpkg.com. La versión se fija igual que las dependencias de
 * Python: ver `vendor/leaflet/VERSION.md`.
 *
 * Los TILES sí los pide el navegador a OpenStreetMap. Es el punto donde es
 * fácil razonar mal: la lista blanca del proxy de PythonAnywhere afecta solo
 * al tráfico saliente DEL SERVIDOR, y aquí el servidor no interviene. Que
 * osm.org no esté en esa lista da exactamente igual.
 *
 * Sin cobertura: los tiles no cargan y el mapa sale gris, pero las chinchetas
 * y el listado siguen apareciendo, porque salen de nuestro servidor. La página
 * lo dice en voz alta en vez de disimularlo.
 */

(function () {
  "use strict";

  const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  /* La atribución es obligatoria por la política de uso de OSM, no un adorno
   * que se pueda quitar para ganar sitio. */
  const ATRIBUCION =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

  /* Centro y zoom de partida cuando todavía no hay ninguna nota: el norte de
   * España. Un mapa vacío centrado en la isla Null a zoom 2 parece roto. */
  const INICIO = [43.2, -5.0];
  const ZOOM_INICIAL = 7;

  const mapaEl = document.getElementById("mapa");
  if (!mapaEl) return;

  const estadoEl = document.getElementById("notas-estado");
  const avisoEl = document.getElementById("mapa-aviso");
  const filtroEl = document.getElementById("filtro-anio");

  const mapa = L.map("mapa").setView(INICIO, ZOOM_INICIAL);
  const capaTiles = L.tileLayer(TILES, { attribution: ATRIBUCION, maxZoom: 19 });
  capaTiles.addTo(mapa);

  let capaChinchetas = L.layerGroup().addTo(mapa);
  let progresoGlobal = null;

  /* Un solo tile que no carga no significa nada (pasa al hacer zoom rápido);
   * varios seguidos sí. Se avisa a partir del tercero para no dar una alarma
   * falsa en cada gesto. */
  let tilesFallidos = 0;
  capaTiles.on("tileerror", function () {
    tilesFallidos += 1;
    if (tilesFallidos === 3) {
      avisoEl.textContent =
        "El fondo del mapa no carga: no hay conexión con OpenStreetMap. " +
        "Las chinchetas y las notas sí están, porque vienen del servidor.";
      avisoEl.hidden = false;
    }
  });
  capaTiles.on("load", function () {
    tilesFallidos = 0;
    avisoEl.hidden = true;
  });

  // --- Utilidades -----------------------------------------------------------

  function fechaLegible(iso) {
    /* La hora viene ya en el huso del sitio donde se escribió la nota
     * (`created_at_local`, calculado en el servidor). Se formatea sin volver a
     * convertir: una nota escrita en agosto en España tiene que enseñar la
     * hora de agosto en España aunque la mires en enero desde el sofá. */
    const sinHuso = iso.slice(0, 19).replace("T", " ");
    const partes = sinHuso.split(" ");
    const ymd = partes[0].split("-");
    return ymd[2] + "/" + ymd[1] + "/" + ymd[0] + " · " + partes[1].slice(0, 5);
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

  // --- Pintado --------------------------------------------------------------

  function pintarProgreso(progreso) {
    const cifras = document.getElementById("progreso-cifras");
    cifras.innerHTML = "";
    cifras.appendChild(cifra(progreso.total, progreso.total === 1 ? "nota" : "notas"));
    cifras.appendChild(cifra(progreso.lugares, "sitios"));
    cifras.appendChild(cifra(progreso.dias, "días con nota"));
    cifras.appendChild(
      cifra(progreso.racha_maxima, "racha", "días seguidos")
    );

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
     * de descartarse: una nota de Portugal no puede desaparecer del recuento
     * sin que nadie se entere. */
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
        mapa.setView([lugar.lat, lugar.lon], 13);
      });
      visitados.appendChild(li);
    });
  }

  function pintarAnios(porAnio, seleccionado) {
    const anios = Object.keys(porAnio).sort().reverse();
    /* El selector solo aparece cuando hay más de un año que comparar. Con un
     * único verano de datos es un desplegable de un elemento: ruido. */
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

  function pintarNotas(notas) {
    document.getElementById("notas-total").textContent = String(notas.length);

    capaChinchetas.clearLayers();
    const lista = document.getElementById("notas-lista");
    lista.innerHTML = "";

    const puntos = [];
    notas.forEach(function (nota) {
      const marcador = L.marker([nota.lat, nota.lon]);
      /* El popup se construye con nodos del DOM y no con una cadena de HTML:
       * el texto lo escribe una persona y con innerHTML acabaría ejecutándose.
       * Jinja escapa las plantillas, pero esto no pasa por Jinja. */
      const contenido = document.createElement("div");
      const titulo = document.createElement("strong");
      titulo.textContent = nota.place_name || nota.lat.toFixed(4) + ", " + nota.lon.toFixed(4);
      contenido.appendChild(titulo);
      contenido.appendChild(document.createElement("br"));
      const cuerpo = document.createElement("span");
      cuerpo.textContent = nota.text;
      contenido.appendChild(cuerpo);
      contenido.appendChild(document.createElement("br"));
      const fecha = document.createElement("small");
      fecha.textContent = fechaLegible(nota.created_at_local);
      contenido.appendChild(fecha);

      marcador.bindPopup(contenido);
      capaChinchetas.addLayer(marcador);
      puntos.push([nota.lat, nota.lon]);

      const li = document.createElement("li");
      const cabecera = document.createElement("div");
      cabecera.className = "nota-cabecera";
      const lugar = document.createElement("strong");
      lugar.textContent = nota.place_name || "Sin nombre";
      cabecera.appendChild(lugar);
      const cuando = document.createElement("span");
      cuando.className = "muted";
      cuando.textContent = fechaLegible(nota.created_at_local);
      cabecera.appendChild(cuando);
      li.appendChild(cabecera);

      const parrafo = document.createElement("p");
      parrafo.textContent = nota.text;
      li.appendChild(parrafo);

      li.addEventListener("click", function () {
        mapa.setView([nota.lat, nota.lon], 14);
        marcador.openPopup();
      });
      lista.appendChild(li);
    });

    if (puntos.length) {
      mapa.fitBounds(puntos, { padding: [40, 40], maxZoom: 13 });
    }
  }

  // --- Carga ----------------------------------------------------------------

  async function cargar(anio) {
    estadoEl.textContent = "Cargando notas…";
    try {
      const url = anio ? "/api/notes?year=" + encodeURIComponent(anio) : "/api/notes";
      const respuesta = await fetch(url);
      if (respuesta.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!respuesta.ok) {
        throw new Error("El servidor respondió " + respuesta.status + ".");
      }
      const datos = await respuesta.json();

      progresoGlobal = datos.progreso;
      pintarProgreso(datos.progreso);
      pintarAnios(datos.progreso.por_anio, anio);
      pintarNotas(datos.notes);

      estadoEl.textContent = datos.notes.length
        ? ""
        : "Todavía no hay ninguna nota. Marca un sitio desde la pantalla principal.";
    } catch (err) {
      /* Aquí no hay degradación posible: sin notas no hay mapa que enseñar.
       * Se dice qué ha pasado en vez de dejar la página en blanco. */
      estadoEl.textContent = "No se pudieron cargar las notas: " + (err.message || err);
      estadoEl.className = "status error";
    }
  }

  filtroEl.addEventListener("change", function () {
    cargar(filtroEl.value);
  });

  cargar("");
})();
