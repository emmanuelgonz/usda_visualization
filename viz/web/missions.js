// Registry-driven mission blocks in the sidebar and the generic tiled layer.
// app.js calls Missions.init(map, catalog, hooks) once the catalog is known;
// swath missions (EMIT, ECOSTRESS) stay hand-written in app.js until they migrate.
var Missions = (function () {
  "use strict";
  var TILED_CLASSES = [
    { min: 1, max: 1, colour: "#e7d4e8", label: "1" },
    { min: 2, max: 2, colour: "#c2a5cf", label: "2" },
    { min: 3, max: 4, colour: "#9970ab", label: "3–4" },
    { min: 5, max: 8, colour: "#762a83", label: "5–8" },
    { min: 9, max: Infinity, colour: "#40004b", label: "9+" }
  ];
  var TILED_OUTLINE = "#40004b";
  var COUNTS_DEBOUNCE_MS = 150;
  var map = null, hooks = null;
  var missions = {};        // key -> { spec, on, mode, filters, layer, loaded, counts, timer, request, renderer }

  function el(id) { return document.getElementById(id); }

  function classColour(n) {
    for (var i = 0; i < TILED_CLASSES.length; i++) {
      if (n >= TILED_CLASSES[i].min && n <= TILED_CLASSES[i].max) { return TILED_CLASSES[i].colour; }
    }
    return null;
  }

  function styleFor(counts) {
    return function (feature) {
      var n = counts[feature.properties.tile] || 0;
      var colour = classColour(n);
      return {
        stroke: true, interactive: true,
        color: TILED_OUTLINE, weight: 0.5, opacity: n ? 0.8 : 0.25,
        fill: !!colour, fillColor: colour || "#ffffff", fillOpacity: colour ? 0.55 : 0
      };
    };
  }

  function controlHtml(key, spec) {
    var html = '<label class="inline"><input autocomplete="off" type="checkbox" id="mission-' + key + '"> ' + spec.label + "</label>";
    html += '<div class="sub" id="mission-' + key + '-controls">';
    if (spec.archetype === "tiled") {
      html += '<div class="inline">Mode:' +
        '<label class="inline"><input autocomplete="off" type="radio" name="mission-' + key + '-mode" value="week" checked> Week window</label>' +
        '<label class="inline"><input autocomplete="off" type="radio" name="mission-' + key + '-mode" value="season"> Season</label></div>' +
        '<div class="note" id="mission-' + key + '-range"></div>';
    }
    spec.filters.forEach(function (f) {
      var id = "mission-" + key + "-f-" + f.attribute;
      if (f.control === "max") {
        html += "<label>" + f.label + ' <output id="' + id + '-out">' + f.default + "%</output>" +
          '<input autocomplete="off" type="range" id="' + id + '" min="0" max="100" step="5" value="' + f.default + '"></label>';
      } else {
        html += '<div class="inline">' + f.label + ":" + f.values.map(function (v) {
          return '<label class="inline"><input autocomplete="off" type="radio" name="' + id + '" value="' + v + '"' +
            (v === f.default ? " checked" : "") + "> " + (v === "ALL" ? "Both" : v) + "</label>";
        }).join("") + "</div>";
      }
    });
    html += '<div id="mission-' + key + '-legend"></div></div>';
    return html;
  }

  function filterQuery(key) {
    var m = missions[key];
    return Object.keys(m.filters).map(function (attr) { return "&f." + attr + "=" + encodeURIComponent(m.filters[attr]); }).join("");
  }

  function applyCounts(key) {
    var m = missions[key];
    if (!m.layer) { return; }
    m.layer.setStyle(styleFor(m.counts));
    m.layer.eachLayer(function (layer) {
      var tile = layer.feature.properties.tile;
      layer.setTooltipContent(tile + ": " + (m.counts[tile] || 0) + " clear");
    });
    drawLegend(key);
  }

  function fetchCounts(key) {
    var m = missions[key];
    var range = hooks.range(key);
    if (!m.layer || !m.on) { return; }
    // no CPC weeks for this selection: nothing to count, clear stale colours
    if (!range) { m.request += 1; m.counts = {}; applyCounts(key); return; }
    var seq = ++m.request;
    fetch("/api/missions/" + key + "/counts?start=" + range.start + "&end=" + range.end + filterQuery(key))
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (seq !== m.request) { return; }   // a newer request has superseded this one
        m.counts = body.counts || {};
        applyCounts(key);
      })
      .catch(function () { /* keep the last counts */ });
  }

  function refreshCounts(key) {
    var m = missions[key];
    clearTimeout(m.timer);
    m.timer = setTimeout(function () { fetchCounts(key); }, COUNTS_DEBOUNCE_MS);
  }

  function load(key) {
    var m = missions[key];
    if (m.loaded || !(m.spec.count > 0 && m.spec.tiles > 0)) { return; }
    m.loaded = true;
    fetch("/api/missions/" + key + "/tiles.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      m.layer = L.geoJSON(geo, {
        pane: "mission-" + key, renderer: m.renderer, style: styleFor(m.counts),
        onEachFeature: function (f, layer) {
          layer.bindTooltip(f.properties.tile + ": 0 clear", { sticky: true, className: "emit-tip" });
        }
      });
      sync(key);
    }).catch(function () { m.loaded = false; });
  }

  function drawLegend(key) {
    var m = missions[key];
    var range = hooks.range(key);
    if (!m.on) { el("mission-" + key + "-range").textContent = ""; el("mission-" + key + "-legend").innerHTML = ""; return; }
    el("mission-" + key + "-range").textContent = range
      ? (m.mode === "season" ? "Season " : "Window ") + range.start + " to " + range.end
      : "No CPC weeks for this selection";
    el("mission-" + key + "-legend").innerHTML =
      '<span class="zero" style="--swatch:#fff">0</span>' +
      TILED_CLASSES.map(function (c) { return '<span style="--swatch:' + c.colour + '">' + c.label + "</span>"; }).join("") +
      "<span>clear acquisitions per MGRS tile</span>";
  }

  function setAvailable(key, available, reason) {
    var m = missions[key];
    var box = el("mission-" + key);
    box.disabled = !available;
    box.parentNode.title = available ? "" : reason;
    if (!available && m.on) { m.on = false; box.checked = false; }
    m.available = available;
  }

  function setControlsActive(key, active, modeActive) {
    el("mission-" + key + "-controls").classList.toggle("disabled", !active);
    var m = missions[key];
    m.spec.filters.forEach(function (f) {
      var id = "mission-" + key + "-f-" + f.attribute;
      if (f.control === "max") { el(id).disabled = !active; }
      else { Array.prototype.forEach.call(document.getElementsByName(id), function (r) { r.disabled = !active; }); }
    });
    Array.prototype.forEach.call(document.getElementsByName("mission-" + key + "-mode"), function (r) { r.disabled = !modeActive; });
  }

  function sync(key) {
    var m = missions[key];
    if (!m.on && m.layer && map.hasLayer(m.layer)) { map.removeLayer(m.layer); }
    if (!m.on) { drawLegend(key); return; }
    if (!m.layer) { load(key); return; }
    if (!map.hasLayer(m.layer)) { m.layer.addTo(map); }
    drawLegend(key);
    refreshCounts(key);
  }

  function wire(key) {
    var m = missions[key];
    el("mission-" + key).addEventListener("change", function (e) { m.on = e.target.checked; hooks.onToggle(key); });
    m.spec.filters.forEach(function (f) {
      var id = "mission-" + key + "-f-" + f.attribute;
      if (f.control === "max") {
        el(id).addEventListener("input", function (e) {
          m.filters[f.attribute] = Number(e.target.value);
          el(id + "-out").textContent = e.target.value + "%";
          refreshCounts(key);
          hooks.onFilterChange(key);
        });
      } else {
        Array.prototype.forEach.call(document.getElementsByName(id), function (radio) {
          radio.addEventListener("change", function (e) {
            if (!e.target.checked) { return; }
            m.filters[f.attribute] = e.target.value;
            drawLegend(key); refreshCounts(key); hooks.onFilterChange(key);
          });
        });
      }
    });
    Array.prototype.forEach.call(document.getElementsByName("mission-" + key + "-mode"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (!e.target.checked) { return; }
        m.mode = e.target.value;
        drawLegend(key); refreshCounts(key); hooks.onFilterChange(key);
      });
    });
  }

  function init(theMap, catalog, theHooks) {
    map = theMap; hooks = theHooks;
    var container = el("missionControls");
    var html = "";
    (catalog.missions || []).forEach(function (spec) {
      if (spec.archetype !== "tiled") { return; }
      html += controlHtml(spec.key, spec);
    });
    container.innerHTML = html;
    (catalog.missions || []).forEach(function (spec) {
      if (spec.archetype !== "tiled") { return; }
      var key = spec.key;
      map.createPane("mission-" + key);
      map.getPane("mission-" + key).style.zIndex = (spec.style && spec.style.pane) || 451;
      var filters = {};
      spec.filters.forEach(function (f) { filters[f.attribute] = f.default; });
      missions[key] = { spec: spec, on: false, available: false, mode: "week", filters: filters, layer: null, loaded: false,
                        counts: {}, timer: null, request: 0, renderer: L.canvas({ pane: "mission-" + key }) };
      wire(key);
    });
  }

  return {
    init: init,
    keys: function () { return Object.keys(missions); },
    isOn: function (key) { return !!(missions[key] && missions[key].on); },
    isAvailable: function (key) { return !!(missions[key] && missions[key].available); },
    mode: function (key) { return missions[key] ? missions[key].mode : "week"; },
    filter: function (key, attr) { return missions[key] ? missions[key].filters[attr] : undefined; },
    setAvailable: setAvailable,
    setControlsActive: setControlsActive,
    sync: sync,
    syncAll: function () { Object.keys(missions).forEach(sync); },
    refreshCounts: refreshCounts,
    drawLegend: drawLegend
  };
})();
