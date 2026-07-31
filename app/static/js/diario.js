/* El diario: qué pasó cada día, fotos y notas mezcladas.
 *
 * Pinta `/api/ruta`, el mismo endpoint que el Mapa. No es duplicación: allí se
 * usa para dibujar el trayecto y aquí para contarlo, y son las dos caras del §1
 * del CLAUDE.md — decidir y recordar. Lo que sí se movió entero desde el Mapa es
 * el «día a día»: estaba contestando la pregunta de esta pantalla dentro de la
 * otra (decisión 40).
 *
 * El orden es cronológico y las fotos NO se separan de las notas: así es como se
 * recuerda un día. Las fotos se enseñan como eventos, no como miniaturas: si
 * solo han llegado metadatos no hay píxeles que cargar, y un recuadro roto
 * comunica peor que una línea honrada en la cronología.
 */

(function () {
  "use strict";

  var TODOS = "todos";
  var anioActual = TODOS;

  function el(id) { return document.getElementById(id); }

  function estado(mensaje, clase) {
    var p = el("diario-estado");
    p.textContent = mensaje || "";
    p.className = "status" + (clase ? " " + clase : "");
    p.hidden = !mensaje;
  }

  function diaLegible(iso) {
    var partes = iso.split("-");
    var fecha = new Date(Date.UTC(+partes[0], +partes[1] - 1, +partes[2]));
    return fecha.toLocaleDateString("es-ES", {
      weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
    });
  }

  /* --- Piezas del muro ---------------------------------------------------- */

  function tiraDeFotos(fotos) {
    var tira = document.createElement("div");
    tira.className = "tira";

    fotos.forEach(function (foto) {
      var caja = document.createElement("div");
      caja.className = "foto-evento";

      var hora = document.createElement("span");
      hora.className = "momento-hora";
      hora.textContent = foto.cuando.slice(11, 16);
      caja.appendChild(hora);

      var cuerpo = document.createElement("div");
      cuerpo.className = "foto-evento-cuerpo";

      var nombre = document.createElement("strong");
      nombre.textContent = foto.archivo || "Foto del viaje";
      cuerpo.appendChild(nombre);

      var meta = document.createElement("span");
      meta.className = "apunte-lugar";
      meta.textContent = foto.lugar || "Foto con metadatos, sin imagen subida";
      cuerpo.appendChild(meta);

      caja.appendChild(cuerpo);
      tira.appendChild(caja);
    });

    return tira;
  }

  function bloqueDeNota(nota) {
    var caja = document.createElement("div");
    caja.className = "apunte";

    var hora = document.createElement("span");
    hora.className = "momento-hora";
    hora.textContent = nota.cuando.slice(11, 16);
    caja.appendChild(hora);

    var cuerpo = document.createElement("div");
    cuerpo.className = "apunte-cuerpo";

    var texto = document.createElement("p");
    texto.className = "apunte-texto";
    /* `textContent` y NUNCA `innerHTML`: esto lo escribe una persona en el móvil
     * y con innerHTML acabaría ejecutándose. */
    texto.textContent = nota.texto || "(sin texto)";
    cuerpo.appendChild(texto);

    if (nota.lugar) {
      var lugar = document.createElement("span");
      lugar.className = "apunte-lugar";
      lugar.textContent = nota.lugar;
      cuerpo.appendChild(lugar);
    }

    caja.appendChild(cuerpo);
    return caja;
  }

  /* Recorre los momentos en orden y va soltando tiras de fotos y apuntes. Las
   * fotos seguidas se acumulan; una nota cierra la tira. Así el álbum no rompe
   * la cronología, que es lo que hace que un diario se lea como un diario. */
  function cuerpoDelDia(momentos) {
    var trozos = document.createDocumentFragment();
    var fotosPendientes = [];

    function soltarFotos() {
      if (fotosPendientes.length) {
        trozos.appendChild(tiraDeFotos(fotosPendientes));
        fotosPendientes = [];
      }
    }

    momentos.forEach(function (momento) {
      if (momento.tipo === "foto") {
        fotosPendientes.push(momento);
      } else {
        soltarFotos();
        trozos.appendChild(bloqueDeNota(momento));
      }
    });
    soltarFotos();

    return trozos;
  }

  function pintarMuro(dias) {
    var muro = el("diario-muro");
    muro.innerHTML = "";

    if (!dias.length) {
      var vacio = document.createElement("p");
      vacio.className = "vacio";
      vacio.textContent =
        "Aquí no hay nada todavía. Escribe una nota desde Inicio o mete fotos " +
        "en el álbum: el diario se escribe solo con lo que vayas dejando.";
      muro.appendChild(vacio);
      return;
    }

    /* Del más reciente al más antiguo: lo que se quiere ver al abrir el diario
     * es lo de hoy, no el primer día del viaje. El Mapa los ordena al revés
     * porque allí se recorre el trayecto. */
    dias.slice().reverse().forEach(function (jornada) {
      var bloque = document.createElement("section");
      bloque.className = "jornada";

      var cabecera = document.createElement("h3");
      cabecera.textContent = diaLegible(jornada.dia);

      /* El recuento va en su propia línea y no pegado al título: «jueves, 30 de
       * julio de 2026» ya es largo, y con el meta detrás el año se quedaba solo
       * en el renglón siguiente. */
      var meta = document.createElement("span");
      meta.className = "jornada-meta";
      var fotos = jornada.momentos.filter(function (m) { return m.tipo === "foto"; }).length;
      var notas = jornada.momentos.length - fotos;
      var partes = [];
      if (fotos) partes.push(fotos + (fotos === 1 ? " foto" : " fotos"));
      if (notas) partes.push(notas + (notas === 1 ? " nota" : " notas"));
      if (jornada.km_linea_recta) partes.push(jornada.km_linea_recta + " km");
      meta.textContent = partes.join("  ·  ");

      bloque.appendChild(cabecera);
      bloque.appendChild(meta);
      bloque.appendChild(cuerpoDelDia(jornada.momentos));
      muro.appendChild(bloque);
    });
  }

  function pintarAvisos(resumen) {
    var avisos = [];
    if (resumen.fotos_sin_fecha) {
      avisos.push(
        resumen.fotos_sin_fecha +
        (resumen.fotos_sin_fecha === 1
          ? " foto sin fecha: no se puede colocar en ningún día"
          : " fotos sin fecha: no se pueden colocar en ningún día")
      );
    }
    if (resumen.fotos_sin_lugar) {
      avisos.push(resumen.fotos_sin_lugar + " sin GPS: cuentan aquí, no en el mapa");
    }
    var nodo = el("diario-avisos");
    nodo.textContent = avisos.join("  ·  ");
    nodo.hidden = avisos.length === 0;
  }

  function pintarAnios(porAnio) {
    var filtro = el("diario-anio");
    var anios = Object.keys(porAnio || {}).sort().reverse();
    /* El selector solo aparece cuando hay más de un año que comparar. Con un
     * único verano de datos sería un desplegable de un elemento: ruido. */
    filtro.hidden = anios.length < 2;
    if (anios.length < 2) return;

    filtro.innerHTML = "";
    var todos = document.createElement("option");
    todos.value = TODOS;
    todos.textContent = "Todos los años";
    filtro.appendChild(todos);

    anios.forEach(function (anio) {
      var o = document.createElement("option");
      o.value = anio;
      o.textContent = anio;
      filtro.appendChild(o);
    });
    filtro.value = anioActual;
  }

  /* --- Carga --------------------------------------------------------------- */

  async function cargar() {
    estado("Cargando…");
    try {
      var url = "/api/ruta" + (anioActual === TODOS ? "" : "?year=" + encodeURIComponent(anioActual));
      var respuesta = await fetch(url);
      if (!respuesta.ok) throw new Error("HTTP " + respuesta.status);
      var datos = await respuesta.json();

      /* Los años salen de `progreso`, que se calcula siempre sobre TODAS las
       * notas aunque el filtro recorte la línea de tiempo. Sacarlos de los días
       * ya filtrados dejaría el desplegable con un solo año en cuanto eligieras
       * uno, y no habría forma de volver. */
      pintarAnios(datos.progreso && datos.progreso.por_anio);
      pintarAvisos(datos.resumen || {});
      pintarMuro(datos.dias || []);
      el("diario-card").hidden = false;
      estado("");
    } catch (err) {
      /* Esta pantalla no sale a internet: `/api/ruta` solo lee SQLite. Así que
       * un fallo aquí casi nunca es del diario — es que el único worker del plan
       * gratuito estaba ocupado. «Load failed» es el texto crudo de Safari
       * cuando un fetch no llega, y no dice ni qué pasó ni qué hacer. */
      estado(
        err.name === "TypeError"
          ? "No se pudo cargar. Puede que el servidor esté ocupado: prueba otra vez."
          : "No se pudo cargar el diario.",
        "error"
      );
    }
  }

  el("diario-anio").addEventListener("change", function (evento) {
    anioActual = evento.target.value;
    cargar();
  });

  /* Al volver a la pestaña se recargan los DATOS, no la página: las respuestas
   * de `/api/` salen con `no-store`, así que traen lo nuevo (decisión 46). El
   * anti-rebote importa porque hay un solo worker: sin él, cada vistazo dejaría
   * esperando detrás a la petición que de verdad importa. */
  var ultimaRecarga = Date.now();
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - ultimaRecarga < 3000) return;
    ultimaRecarga = Date.now();
    cargar();
  });

  cargar();
})();
