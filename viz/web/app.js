/* Interface for the local CPC-over-CDL map. All data comes from this server. */
(function () {
  "use strict";

  var state = {
    catalog: null,
    crop: "corn",
    var: "cond",
    year: 2025,
    week: 30,
    cdlYear: 2025,
    mode: "overlay",
    mask: false,
    cdlVisible: true,
    cdlOpacity: 1.0,
    dim: "white",
    opacity: 0.7,
    playing: false,
    timer: null,
    days: 7,
    emit: false,
    emitCloud: 30,
    emitOnlyWindow: false,
    coincide: false,
    coincideStep: 2,
    coincideAll: false,
    coincideOnly: false,
    eco: false,
    ecoDay: "DAY",
    hls: false,
    hlsMode: "week",
    hlsCloud: 30,
    hlsSensor: "ALL"
  };

  // Hardcoded so the first paint fits CONUS before the boundary file loads.
  var CONUS_BOUNDS = L.latLngBounds([24.4, -125.0], [49.4, -66.9]);
  var map = L.map("map", { minZoom: 3, maxZoom: 15, zoomSnap: 0.25 });
  function fitConus() { map.fitBounds(CONUS_BOUNDS, { padding: [8, 8] }); }
  fitConus();
  window.addEventListener("resize", fitConus);
  map.createPane("cdl");
  map.createPane("cpc");
  map.createPane("states");
  map.getPane("cdl").style.zIndex = 400;
  map.getPane("cpc").style.zIndex = 450;
  map.getPane("states").style.zIndex = 460;

  map.createPane("emit");
  map.getPane("emit").style.zIndex = 455;   // above CPC, below state lines
  var emitRenderer = L.canvas({ pane: "emit" });

  // Validated categorical palette; fixed order, never cycled within the mission.
  var EMIT_YEAR_COLOURS = { 2022: "#1c5cab", 2023: "#8b45d9", 2024: "#e0338e", 2025: "#00a3c4", 2026: "#b5651d" };
  var EMIT_HIGHLIGHT = "#111111";
  var emitLayer = null;      // L.geoJSON over the whole file
  var emitLoaded = false;

  map.createPane("eco");
  map.getPane("eco").style.zIndex = 452;   // below EMIT, above CPC
  var ecoRenderer = L.canvas({ pane: "eco" });
  var ECO_COLOUR = "#5b6770";
  var COINCIDE_STEPS = [1, 5, 15, 30, 60, 120, 360, 720, 1440];   // minutes
  var ecoLayer = null;
  var ecoLoaded = false;

  map.createPane("hls");
  map.getPane("hls").style.zIndex = 451;   // above CPC, below ECOSTRESS and EMIT
  var hlsRenderer = L.canvas({ pane: "hls" });
  // Sequential purples, away from the CDL crop colours and both CPC ramps.
  var HLS_CLASSES = [
    { min: 1, max: 1, colour: "#e7d4e8", label: "1" },
    { min: 2, max: 2, colour: "#c2a5cf", label: "2" },
    { min: 3, max: 4, colour: "#9970ab", label: "3–4" },
    { min: 5, max: 8, colour: "#762a83", label: "5–8" },
    { min: 9, max: Infinity, colour: "#40004b", label: "9+" }
  ];
  var HLS_OUTLINE = "#40004b";
  var hlsLayer = null;
  var hlsLoaded = false;
  var hlsCounts = {};
  var hlsTimer = null;
  var hlsRequest = 0;

  function hlsColour(n) {
    for (var i = 0; i < HLS_CLASSES.length; i++) {
      if (n >= HLS_CLASSES[i].min && n <= HLS_CLASSES[i].max) { return HLS_CLASSES[i].colour; }
    }
    return null;
  }

  function coincideSeconds() { return COINCIDE_STEPS[state.coincideStep] * 60; }

  // EMIT features carry the HLS acquisitions of their tile within ±HLS_PAIR_DAYS
  // (viz/hls_pairs.py), so the marking clamps the shared window to that span.
  var HLS_PAIR_DAYS = 15;
  var HLS_PAIR_FILL = "#762a83";

  // True when the scene has a stored HLS acquisition inside the shared window
  // that passes the HLS cloud threshold and sensor filter.
  function hlsPaired(p) {
    var days = Math.min(state.days, HLS_PAIR_DAYS);
    var list = p.hls_near || [];
    for (var i = 0; i < list.length; i++) {
      var h = list[i];
      if (Math.abs(h.dt) > days) { continue; }
      if (h.cloud === null || h.cloud === undefined || h.cloud > state.hlsCloud) { continue; }
      if (state.hlsSensor !== "ALL" && h.sensor !== state.hlsSensor) { continue; }
      return true;
    }
    return false;
  }

  function formatDt(seconds) {
    var sign = seconds < 0 ? "−" : "+";
    var s = Math.abs(seconds);
    if (s < 60) { return sign + s + " s"; }
    // Round to whole minutes FIRST, then split, so 7199 s is "+2 h 0 m",
    // never "+1 h 60 m", and 3599 s is "+1 h 0 m", never "+60 m".
    var totalM = Math.round(s / 60);
    if (totalM < 60) { return sign + totalM + " m"; }
    return sign + Math.floor(totalM / 60) + " h " + (totalM % 60) + " m";
  }

  function formatDays(dt) {
    if (dt === 0) { return "same day"; }
    return (dt < 0 ? "−" : "+") + Math.abs(dt) + " d";
  }

  function stepLabel(step) {
    var m = COINCIDE_STEPS[step];
    return m < 60 ? m + " min" : (m / 60) + " h";
  }

  // State names are shown below this zoom; above it one state fills the view.
  var LABEL_MAX_ZOOM = 9;
  var stateLines = null;
  var stateLabels = null;

  var cdlLayer = null;
  var cpcLayer = null;
  var readoutPopup = null;
  var readoutOpen = {};      // which collapsible readout sections the user left open
  var swipeHandle = null;
  var swipeFraction = 0.5;

  function el(id) { return document.getElementById(id); }

  function weeksFor(crop, variable, year) {
    var byVar = (state.catalog.cpc[crop] || {})[variable] || {};
    return byVar[String(year)] || [];
  }

  function yearsFor(crop, variable) {
    return Object.keys((state.catalog.cpc[crop] || {})[variable] || {})
      .map(Number).sort(function (a, b) { return a - b; });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function fillSelect(node, values, selected, labeller) {
    node.innerHTML = "";
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = labeller ? labeller(value) : value;
      if (String(value) === String(selected)) { option.selected = true; }
      node.appendChild(option);
    });
  }

  function drawCdl() {
    // With the mask on, every CDL class except the selected crop's is greyed
    // server-side so the crop's fields and the CPC cells are the only colour.
    var focus = state.mask ? state.crop : null;
    // Visibility is checked before the "unchanged" shortcut, or unticking the
    // box would leave a layer whose year and focus still match in place.
    if (!state.cdlVisible) {
      if (cdlLayer) { map.removeLayer(cdlLayer); cdlLayer = null; }
      return;
    }
    var dim = focus ? state.dim : null;
    if (cdlLayer && cdlLayer.options.usdaYear === state.cdlYear &&
        cdlLayer.options.usdaFocus === focus && cdlLayer.options.usdaDim === dim) { return; }
    if (cdlLayer) { map.removeLayer(cdlLayer); cdlLayer = null; }
    if (state.cdlYear === null || state.cdlYear === undefined) { return; }
    cdlLayer = L.tileLayer("/tiles/cdl/" + state.cdlYear + "/{z}/{x}/{y}.png" +
                           "?t=" + state.catalog.server_token +
                           (focus ? "&focus=" + focus + "&dim=" + dim : ""), {
      pane: "cdl", maxNativeZoom: 15, maxZoom: 15, noWrap: true,
      opacity: state.cdlOpacity,
      usdaYear: state.cdlYear, usdaFocus: focus, usdaDim: dim,
      attribution: "USDA NASS Cropland Data Layer " + state.cdlYear
    }).addTo(map);
  }

  function drawCpc() {
    if (state.week === null || state.week === undefined) {
      if (cpcLayer) { map.removeLayer(cpcLayer); cpcLayer = null; }
      return;
    }
    var url = "/tiles/cpc/" + state.crop + "/" + state.var + "/" + state.year +
              "/" + state.week + "/{z}/{x}/{y}.png" +
              "?t=" + state.catalog.server_token +
              (state.mask ? "&mask=" + state.cdlYear : "");
    // Recreate rather than swap the URL in place: Leaflet 1.9.4's redraw does not round
    // the map zoom, so at a fractional zoom it requests tiles at z=4.75 and
    // every one 404s. A new layer takes the rounding path and works.
    if (cpcLayer) { map.removeLayer(cpcLayer); cpcLayer = null; }
    {
      cpcLayer = L.tileLayer(url, {
        pane: "cpc", maxNativeZoom: 15, maxZoom: 15, noWrap: true,
        opacity: state.opacity,
        attribution: "USDA NASS Crop Progress and Condition " + state.year
      }).addTo(map);
    }
    applyMode();
  }

  function applyMode() {
    var pane = map.getPane("cpc");
    if (state.mode === "swipe") {
      if (!swipeHandle) {
        swipeHandle = document.createElement("div");
        swipeHandle.id = "swipeHandle";
        map.getContainer().appendChild(swipeHandle);
        var dragging = false;
        swipeHandle.addEventListener("mousedown", function (e) {
          dragging = true; e.preventDefault(); map.dragging.disable();
        });
        document.addEventListener("mousemove", function (e) {
          if (!dragging) { return; }
          var box = map.getContainer().getBoundingClientRect();
          swipeFraction = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
          positionSwipe();
        });
        document.addEventListener("mouseup", function () {
          dragging = false; map.dragging.enable();
        });
      }
      swipeHandle.style.display = "block";
      pane.style.opacity = 1;
      map.on("move", positionSwipe);
      map.on("zoom", positionSwipe);
      map.on("resize", positionSwipe);
      positionSwipe();
    } else {
      if (swipeHandle) { swipeHandle.style.display = "none"; }
      map.off("move", positionSwipe);
      map.off("zoom", positionSwipe);
      map.off("resize", positionSwipe);
      pane.style.clipPath = "";
      pane.style.opacity = 1;
      if (cpcLayer) { cpcLayer.setOpacity(state.opacity); }
    }
  }

  function positionSwipe() {
    var size = map.getSize();
    var x = Math.round(size.x * swipeFraction);
    swipeHandle.style.left = x + "px";
    // Leaflet panes are 0x0 boxes, so an edge-inset clip measured from the pane's
    // own edges collapses to nothing. Build the clip from the container's
    // corners converted into the pane's (layer) coordinate space instead, and
    // re-apply it on every map move because panning translates that space.
    var nw = map.containerPointToLayerPoint([0, 0]);
    var se = map.containerPointToLayerPoint(size);
    var clipX = nw.x + (se.x - nw.x) * swipeFraction;
    map.getPane("cpc").style.clipPath = "polygon(" +
      clipX + "px " + nw.y + "px, " + se.x + "px " + nw.y + "px, " +
      se.x + "px " + se.y + "px, " + clipX + "px " + se.y + "px)";
    if (cpcLayer) { cpcLayer.setOpacity(1); }
  }

  function loadStates() {
    fetch("/static/vendor/states.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      stateLines = L.geoJSON(geo, {
        pane: "states", interactive: false,
        style: { color: "#222", weight: 1, opacity: 0.6, fill: false }
      });
      stateLabels = L.layerGroup();
      geo.features.forEach(function (f) {
        var p = f.properties;
        stateLabels.addLayer(L.marker([Number(p.INTPTLAT), Number(p.INTPTLON)], {
          pane: "states", interactive: false, keyboard: false,
          icon: L.divIcon({ className: "state-label", html: p.NAME, iconSize: [0, 0] })
        }));
      });
      syncStates();
    }).catch(function () { /* boundaries are optional; the map works without them */ });
  }

  function syncStates() {
    if (!stateLines) { return; }
    var on = el("states").checked;
    var showLabels = on && map.getZoom() < LABEL_MAX_ZOOM;
    if (on && !map.hasLayer(stateLines)) { stateLines.addTo(map); }
    if (!on && map.hasLayer(stateLines)) { map.removeLayer(stateLines); }
    if (showLabels && !map.hasLayer(stateLabels)) { stateLabels.addTo(map); }
    if (!showLabels && map.hasLayer(stateLabels)) { map.removeLayer(stateLabels); }
  }

  // Sunday ending ISO week N. Mirrors viz/emit.py: 2024 week 15 -> 2024-04-14.
  function weekSunday(year, week) {
    var jan4 = new Date(Date.UTC(year, 0, 4));
    var jan4Dow = jan4.getUTCDay() || 7;             // Monday=1 .. Sunday=7
    var week1Monday = new Date(jan4.getTime() - (jan4Dow - 1) * 86400000);
    return new Date(week1Monday.getTime() + ((week - 1) * 7 + 6) * 86400000);
  }

  function windowBounds() {
    if (!state.days || state.week === null) { return null; }
    var centre = weekSunday(state.year, state.week).getTime();
    var span = state.days * 86400000;
    return [centre - span, centre + span];
  }

  function isoDate(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  // The HLS counting range: the shared ±days window around the CPC week's Sunday,
  // or the crop's reporting season (Monday of its first CPC week to Sunday of its last).
  function hlsRange() {
    if (state.week === null || state.week === undefined) { return null; }
    if (state.hlsMode === "season") {
      var weeks = weeksFor(state.crop, state.var, state.year);
      if (!weeks.length) { return null; }
      var first = weekSunday(state.year, weeks[0]).getTime() - 6 * 86400000;
      var last = weekSunday(state.year, weeks[weeks.length - 1]).getTime();
      return { start: isoDate(first), end: isoDate(last) };
    }
    var centre = weekSunday(state.year, state.week).getTime();
    var span = state.days * 86400000;
    return { start: isoDate(centre - span), end: isoDate(centre + span) };
  }

  function emitStyleFor(bounds) {
    var maxDt = coincideSeconds();
    return function (feature) {
      var p = feature.properties;
      var inWindow = !!(bounds && p._t >= bounds[0] && p._t <= bounds[1]);
      var nearest = p.eco && p.eco.length ? p.eco[0].dt : null;
      var ecoHit = nearest !== null && Math.abs(nearest) <= maxDt;
      var triple = state.coincideAll && ecoHit && hlsPaired(p);
      var coincident = state.coincide && ecoHit;
      var marked = triple || coincident;
      var anyMark = state.coincide || state.coincideAll;
      var hidden = (p.cloud !== null && p.cloud > state.emitCloud) ||
                   (state.emitOnlyWindow && bounds && !inWindow) ||
                   (anyMark && state.coincideOnly && !marked);
      var yearColour = EMIT_YEAR_COLOURS[p.year] || "#666";
      return {
        stroke: !hidden, interactive: !hidden,
        fill: marked && !hidden, fillColor: triple ? HLS_PAIR_FILL : yearColour,
        fillOpacity: !marked || hidden ? 0 : (triple ? 0.35 : 0.25),
        color: inWindow ? EMIT_HIGHLIGHT : yearColour,
        weight: inWindow ? 2.5 : 1,
        opacity: inWindow ? 0.95 : (bounds ? 0.35 : 0.8)
      };
    };
  }

  function loadEmit() {
    if (emitLoaded || !(state.catalog.emit_count > 0)) { return; }
    emitLoaded = true;
    fetch("/api/emit/footprints.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      emitLayer = L.geoJSON(geo, {
        pane: "emit", renderer: emitRenderer, style: emitStyleFor(windowBounds()),
        onEachFeature: function (f, layer) {
          var p = f.properties;
          f.properties._t = Date.parse(f.properties.start);
          layer.bindTooltip(p.start.slice(0, 10) + " · " + (p.cloud === null ? "?" : p.cloud + "%") + " cloud",
                            { sticky: true, className: "emit-tip" });
        }
      });
      syncEmit();
    }).catch(function () { emitLoaded = false; });
  }

  function drawEmitLegend() {
    var years = Object.keys(EMIT_YEAR_COLOURS).sort();
    el("emitLegend").innerHTML =
      years.map(function (y) { return '<span style="--swatch:' + EMIT_YEAR_COLOURS[y] + '">' + y + "</span>"; }).join("") +
      '<span class="hl" style="--swatch:' + EMIT_HIGHLIGHT + '">within window</span>' +
      (state.catalog.eco_count > 0 ? '<span class="fillsw">filled = ECOSTRESS coincident</span>' : "") +
      (state.catalog.eco_count > 0 && state.catalog.emit_hls_paired > 0
        ? '<span class="fillsw hls">purple = ECOSTRESS + HLS coincident</span>' : "");
  }

  function ecoStyleFor(bounds) {
    return function (feature) {
      var p = feature.properties;
      var inWindow = !!(bounds && p._t >= bounds[0] && p._t <= bounds[1]);
      var hidden = state.ecoDay !== "ALL" && p.daynight !== state.ecoDay;
      return {
        stroke: !hidden, interactive: !hidden, fill: false,
        color: inWindow ? EMIT_HIGHLIGHT : ECO_COLOUR,
        dashArray: "4 4",
        weight: inWindow ? 2.5 : 1,
        opacity: inWindow ? 0.95 : (bounds ? 0.35 : 0.7)
      };
    };
  }

  function loadEco() {
    if (ecoLoaded || !(state.catalog.eco_count > 0)) { return; }
    ecoLoaded = true;
    fetch("/api/eco/footprints.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      ecoLayer = L.geoJSON(geo, {
        pane: "eco", renderer: ecoRenderer, style: ecoStyleFor(windowBounds()),
        onEachFeature: function (f, layer) {
          var p = f.properties;
          p._t = Date.parse(p.start);
          layer.bindTooltip((p.start || "").slice(0, 10) + " " + (p.start || "").slice(11, 16) + " UTC · " +
                            (p.daynight || "?").toLowerCase() + " · bounding box",
                            { sticky: true, className: "emit-tip" });
        }
      });
      syncEco();
    }).catch(function () { ecoLoaded = false; });
  }

  function drawEcoLegend() {
    el("ecoLegend").innerHTML = "<span>ECOSTRESS swath, bounding box (≈550 km), not the true outline</span>";
  }

  function hlsStyleFor(counts) {
    return function (feature) {
      var n = counts[feature.properties.tile] || 0;
      var colour = hlsColour(n);
      return {
        stroke: true, interactive: true,
        color: HLS_OUTLINE, weight: 0.5, opacity: n ? 0.8 : 0.25,
        fill: !!colour, fillColor: colour || "#ffffff", fillOpacity: colour ? 0.55 : 0
      };
    };
  }

  function applyHlsCounts() {
    if (!hlsLayer) { return; }
    hlsLayer.setStyle(hlsStyleFor(hlsCounts));
    hlsLayer.eachLayer(function (layer) {
      var tile = layer.feature.properties.tile;
      layer.setTooltipContent(tile + ": " + (hlsCounts[tile] || 0) + " clear");
    });
    drawHlsLegend();
  }

  function fetchHlsCounts() {
    var range = hlsRange();
    if (!hlsLayer || !state.hls || !range) { return; }
    var seq = ++hlsRequest;
    fetch("/api/hls/counts?start=" + range.start + "&end=" + range.end +
          "&cloud=" + state.hlsCloud + "&sensor=" + state.hlsSensor)
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (seq !== hlsRequest) { return; }   // a newer request has superseded this one
        hlsCounts = body.counts || {};
        applyHlsCounts();
      })
      .catch(function () { /* keep the last counts */ });
  }

  function refreshHlsCounts() {
    clearTimeout(hlsTimer);
    hlsTimer = setTimeout(fetchHlsCounts, 150);
  }

  function loadHls() {
    if (hlsLoaded || !(state.catalog.hls_count > 0 && state.catalog.hls_tiles > 0)) { return; }
    hlsLoaded = true;
    fetch("/api/hls/tiles.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      hlsLayer = L.geoJSON(geo, {
        pane: "hls", renderer: hlsRenderer, style: hlsStyleFor(hlsCounts),
        onEachFeature: function (f, layer) {
          layer.bindTooltip(f.properties.tile + ": 0 clear", { sticky: true, className: "emit-tip" });
        }
      });
      syncHls();
    }).catch(function () { hlsLoaded = false; });
  }

  function drawHlsLegend() {
    var range = hlsRange();
    if (!state.hls) {
      el("hlsRange").textContent = "";
      el("hlsLegend").innerHTML = "";
      return;
    }
    el("hlsRange").textContent = range
      ? (state.hlsMode === "season" ? "Season " : "Window ") + range.start + " to " + range.end
      : "No CPC weeks for this selection";
    el("hlsLegend").innerHTML =
      '<span class="zero" style="--swatch:#fff">0</span>' +
      HLS_CLASSES.map(function (c) {
        return '<span style="--swatch:' + c.colour + '">' + c.label + "</span>";
      }).join("") + "<span>clear acquisitions per MGRS tile</span>";
  }

  function syncHls() {
    // Rows without rings colour nothing, and a running fetch holds the store's
    // write lock, so both leave the layer off with the reason on the label.
    var busy = !!state.catalog.hls_busy;
    var available = !busy && state.catalog.hls_count > 0 && state.catalog.hls_tiles > 0;
    var box = el("hls");
    box.disabled = !available;
    box.parentNode.title = available ? ""
      : busy ? "HLS store busy; a fetch is in progress. Reload when it finishes."
      : state.catalog.hls_count > 0 ? "HLS tile rings not fetched yet; let ./run.sh hls finish."
      : "No HLS store; run ./run.sh hls";
    if (!available && state.hls) { state.hls = false; box.checked = false; }
    // Cloud and Sensor also drive the ECOSTRESS + HLS mark, so they stay live
    // while that mark is on; Mode only shapes the tile counts.
    var active = available && (state.hls || state.coincideAll);
    el("hlsControls").classList.toggle("disabled", !active);
    el("hlsCloud").disabled = !active;
    Array.prototype.forEach.call(document.getElementsByName("hlsSensor"), function (radio) {
      radio.disabled = !active;
    });
    Array.prototype.forEach.call(document.getElementsByName("hlsMode"), function (radio) {
      radio.disabled = !(available && state.hls);
    });
    if (!state.hls && hlsLayer && map.hasLayer(hlsLayer)) { map.removeLayer(hlsLayer); }
    if (!state.hls) { drawHlsLegend(); }
    if (state.hls) {
      if (!hlsLayer) { loadHls(); return; }
      if (!map.hasLayer(hlsLayer)) { hlsLayer.addTo(map); }
      drawHlsLegend();
      refreshHlsCounts();
    }
  }

  function syncEco() {
    var available = state.catalog.eco_count > 0;
    var box = el("eco");
    box.disabled = !available;
    box.parentNode.title = available ? "" : "No ECOSTRESS footprints; run ./run.sh footprints";
    if (!available && state.eco) { state.eco = false; box.checked = false; }
    el("ecoControls").classList.toggle("disabled", !(available && state.eco));
    Array.prototype.forEach.call(document.getElementsByName("ecoDay"), function (radio) {
      radio.disabled = !(available && state.eco);
    });
    if (!state.eco && ecoLayer && map.hasLayer(ecoLayer)) { map.removeLayer(ecoLayer); }
    if (state.eco) {
      if (!ecoLayer) { loadEco(); return; }
      if (!map.hasLayer(ecoLayer)) { ecoLayer.addTo(map); }
      ecoLayer.setStyle(ecoStyleFor(windowBounds()));
    }
  }

  function syncEmit() {
    var available = state.catalog.emit_count > 0;
    var box = el("emit");
    box.disabled = !available;
    box.parentNode.title = available ? "" : "No EMIT footprints; run ./run.sh emit";
    el("emitControls").classList.toggle("disabled", !(available && state.emit));
    el("emitCloud").disabled = !(available && state.emit);
    // "Only within window" needs a window to filter by.
    var onlyOk = available && state.emit && state.days > 0;
    el("emitOnlyWindow").disabled = !onlyOk;
    if (!onlyOk && state.emitOnlyWindow) { state.emitOnlyWindow = false; el("emitOnlyWindow").checked = false; }
    var coincideOk = available && state.emit && state.catalog.eco_count > 0;
    el("coincide").disabled = !coincideOk;
    // The mark reads the stored ±HLS_PAIR_DAYS lists through the shared window,
    // so it needs a window (like "Only scenes within window") and caps at the span.
    var paired = coincideOk && state.catalog.emit_hls_paired > 0;
    var allOk = paired && state.days > 0;
    el("coincideAll").disabled = !allOk;
    el("coincideAll").parentNode.title =
      !coincideOk ? "" :
      !paired ? "No HLS pairs on the EMIT scenes; run ./run.sh hls" :
      state.days === 0 ? "Needs a time window" :
      state.days > HLS_PAIR_DAYS ? "Uses ±" + HLS_PAIR_DAYS + " days of HLS acquisitions" : "";
    if (!coincideOk && state.coincide) { state.coincide = false; el("coincide").checked = false; }
    if (!allOk && state.coincideAll) { state.coincideAll = false; el("coincideAll").checked = false; }
    var anyMark = state.coincide || state.coincideAll;
    el("coincideWindow").disabled = el("coincideOnly").disabled = !(coincideOk && anyMark);
    if (!anyMark && state.coincideOnly) { state.coincideOnly = false; el("coincideOnly").checked = false; }
    if (!state.emit && emitLayer && map.hasLayer(emitLayer)) { map.removeLayer(emitLayer); }
    if (state.emit) {
      if (!emitLayer) { loadEmit(); return; }
      if (!map.hasLayer(emitLayer)) { emitLayer.addTo(map); }
      emitLayer.setStyle(emitStyleFor(windowBounds()));
    }
  }

  function drawLegend() {
    var stops = state.var === "cond"
      ? [{ v: 1, l: "Very poor" }, { v: 2, l: "Poor" }, { v: 3, l: "Fair" },
         { v: 4, l: "Good" }, { v: 5, l: "Excellent" }]
      : [{ v: 0, l: "0%" }, { v: 0.5, l: "50%" }, { v: 1, l: "100%" }];
    var ramp = state.var === "cond"
      ? ["#a50f15", "#de5c37", "#f7e08f", "#78b75c", "#1a6e2f"]
      : ["#f7f4e9", "#c4d6ac", "#6ea694", "#194a6e"];

    el("legend").innerHTML =
      '<div class="swatches">' +
      ramp.map(function (c) { return '<span style="background:' + c + '"></span>'; }).join("") +
      "</div><div class=\"labels\">" +
      stops.map(function (s) { return "<span>" + s.l + "</span>"; }).join("") +
      "</div>";
  }

  function drawPairing() {
    var paired = state.catalog.cdl_pairing[String(state.year)];
    var text = "CPC " + state.year + " week " + state.week;
    if (state.cdlYear === null) {
      text += ". No CDL rasters available";
    } else {
      text += " over CDL " + state.cdlYear;
      if (Number(state.cdlYear) === Number(paired) && Number(paired) !== Number(state.year)) {
        text += " (no CDL for " + state.year + "; nearest is " + paired + ")";
      } else if (Number(state.cdlYear) !== Number(state.year)) {
        text += " (CDL year chosen manually)";
      }
      if (!maskAvailable()) {
        text += ". No crop mask built for CDL " + state.cdlYear + ".";
      }
    }
    el("pairing").textContent = text;
  }

  function maskAvailable() {
    return (state.catalog.mask_years || []).indexOf(Number(state.cdlYear)) >= 0;
  }

  function syncMaskControl() {
    var box = el("mask");
    var available = maskAvailable();
    box.disabled = !available;
    box.parentNode.title = available
      ? "Scale opacity by the fraction of each 9 km cell growing this crop"
      : "Masks are built for CDL 2024 and 2025 only";
    if (!available && state.mask) { state.mask = false; box.checked = false; }
    syncDimControl();
  }

  function syncDimControl() {
    var row = el("dimRow");
    var active = state.mask && !el("mask").disabled;
    row.classList.toggle("disabled", !active);
    Array.prototype.forEach.call(document.getElementsByName("dim"), function (radio) {
      radio.disabled = !active;
    });
    // The map background matches the off-crop colour so the area outside
    // CONUS and the "CDL hidden" view read the same as the dimmed classes.
    el("map").style.background = (state.mask && state.dim === "grey") ? "#e9e9e6" : "#fff";
  }

  function sparkline(series, domainLo, domainHi) {
    var points = series.filter(function (p) { return p.value !== null; });
    if (points.length < 2) { return ""; }
    var weeks = points.map(function (p) { return p.week; });
    var w0 = Math.min.apply(null, weeks), w1 = Math.max.apply(null, weeks);
    var path = points.map(function (p, i) {
      var x = (p.week - w0) / (w1 - w0 || 1) * 100;
      var y = 30 - (p.value - domainLo) / (domainHi - domainLo) * 28;
      return (i ? "L" : "M") + x.toFixed(2) + " " + y.toFixed(2);
    }).join(" ");
    return '<svg viewBox="0 0 100 32" preserveAspectRatio="none">' +
           '<path d="' + path + '" fill="none" stroke="#1a6e2f" stroke-width="1.2"' +
           ' vector-effect="non-scaling-stroke"/></svg>';
  }

  function pct(value) {
    return value === null || value === undefined ? "—" : (value * 100).toFixed(1) + "%";
  }

  // A collapsible readout section: the heading with its count stays visible,
  // the list opens on click. A heading with nothing to list is a plain line.
  function foldable(key, heading, body) {
    if (!body) { return '<p class="section">' + heading + "</p>"; }
    return '<details class="fold" data-fold="' + key + '"' + (readoutOpen[key] ? " open" : "") + ">" +
           '<summary class="section">' + heading + "</summary>" + body + "</details>";
  }

  // Wire the toggles inside an open popup: remember the choice and re-measure
  // the box so an expanded list gets the height cap and stays on screen.
  // popup.update() would re-render the content from its HTML string, which
  // rebuilds the section closed, so the layout, position, and pan steps of
  // Leaflet 1.9.4's update() are called directly instead.
  function wireFolds(popup) {
    var node = popup.getElement();
    if (!node) { return; }
    Array.prototype.forEach.call(node.querySelectorAll("details.fold"), function (details) {
      details.addEventListener("toggle", function () {
        readoutOpen[details.dataset.fold] = details.open;
        popup._updateLayout();
        popup._updatePosition();
        popup._adjustPan();
      });
    });
  }

  function showReadout(report) {
    var cover = report.cover || {};
    var noCover = (cover.primary === null || cover.primary === undefined) &&
                  (cover.double === null || cover.double === undefined);
    var total = noCover ? null : (cover.primary || 0) + (cover.double || 0);
    var cond = report.series.cond || [];
    var prog = report.series.prog || [];
    var hasSeries = cond.some(function (p) { return p.value !== null; }) ||
                    prog.some(function (p) { return p.value !== null; });

    var html =
      "<h2>" + (report.cdl_class || "Unknown") + "</h2>" +
      '<p class="scale">CDL ' + report.cdl_year + " · 30 m pixel</p>" +
      '<p class="section">9 km CPC cell</p>' +
      "<table>" +
      "<tr><th>" + report.crop + " cover</th><td>" + pct(total) + "</td></tr>" +
      "<tr><th>&nbsp;&nbsp;primary</th><td>" + pct(cover.primary) + "</td></tr>" +
      "<tr><th>&nbsp;&nbsp;double-crop</th><td>" + pct(cover.double) + "</td></tr>" +
      "</table>";

    if (!hasSeries) {
      html += '<p class="note">No CPC data at this cell.</p>';
    } else {
      if (total === 0) {
        html += '<p class="note">No ' + report.crop + " in this 9 km cell. The CPC surface " +
                "extends beyond mapped " + report.crop + ", so the series below is not field-based.</p>";
      }
      html += '<p class="note">Condition, weeks ' + state.year + "</p>" + sparkline(cond, 1, 5) +
              '<p class="note">Progress, weeks ' + state.year + "</p>" + sparkline(prog, 0, 1);
    }

    var granules = report.emit || [];
    var emitBody = "";
    if (granules.length) {
      var centre = report.week_sunday ? Date.parse(report.week_sunday + "T00:00:00Z") : null;
      var bounds = windowBounds();
      var sorted = granules.slice().sort(function (a, b) {
        if (centre === null) { return Date.parse(b.start) - Date.parse(a.start); }
        return Math.abs(Date.parse(a.start) - centre) - Math.abs(Date.parse(b.start) - centre);
      });
      var shown = sorted.slice(0, 15);
      emitBody += '<ul class="emit-list">' + shown.map(function (g) {
        var t = Date.parse(g.start);
        var inWin = bounds && t >= bounds[0] && t <= bounds[1];
        return "<li" + (inWin ? ' class="in"' : "") + '><span class="when">' + g.start.slice(0, 10) + "</span>" +
               "<span>" + (g.cloud === null ? "?" : g.cloud.toFixed(0) + "%") + " cloud</span>" +
               (g.browse ? ' <a href="' + g.browse + '" target="_blank" rel="noopener">browse</a>' : "") +
               (g.data ? ' <a href="' + g.data + '" target="_blank" rel="noopener">data</a>' : "") +
               (g.eco && g.eco.length && state.coincide && Math.abs(g.eco[0].dt) <= coincideSeconds()
                 ? ' <span class="eco-tag">ECOSTRESS ' + formatDt(g.eco[0].dt) + "</span>" : "") +
               (g.hls
                 ? ' <span class="eco-tag">HLS ' + g.hls.sensor + " " + formatDays(g.hls.dt) + ", " +
                   (g.hls.cloud === null ? "?" : g.hls.cloud) + "% cloud</span>"
                 : (g.hls === null ? ' <span class="eco-tag">no clear HLS within ±' + report.hls.window + " d</span>" : "")) +
               (inWin ? " <span>★</span>" : "") + "</li>";
      }).join("") + "</ul>";
      if (granules.length > shown.length) {
        emitBody += '<p class="note">and ' + (granules.length - shown.length) + " more</p>";
      }
    }
    html += foldable("emit", "EMIT scenes covering this point: " + granules.length, emitBody);
    var swaths = report.eco || [];
    var ecoBody = "";
    if (swaths.length) {
      var centreE = report.week_sunday ? Date.parse(report.week_sunday + "T00:00:00Z") : null;
      var sortedE = swaths.slice().sort(function (a, b) {
        if (centreE === null) { return Date.parse(b.start) - Date.parse(a.start); }
        return Math.abs(Date.parse(a.start) - centreE) - Math.abs(Date.parse(b.start) - centreE);
      });
      var shownE = sortedE.slice(0, 10);
      ecoBody += '<ul class="emit-list">' + shownE.map(function (s) {
        return '<li><span class="when">' + s.start.slice(0, 10) + " " + s.start.slice(11, 16) + "</span>" +
               "<span>" + (s.daynight || "?").toLowerCase() + "</span></li>";
      }).join("") + "</ul>";
      if (swaths.length > shownE.length) {
        ecoBody += '<p class="note">and ' + (swaths.length - shownE.length) + " more</p>";
      }
    }
    html += foldable("eco", "ECOSTRESS swaths covering this point: " + swaths.length, ecoBody);
    var hlsBlock = report.hls;
    if (hlsBlock) {
      var totalAcq = 0, totalClear = 0;
      hlsBlock.tiles.forEach(function (t) { totalAcq += t.acq.length; totalClear += t.clear; });
      var hlsBody = "";
      if (!hlsBlock.tiles.length) {
        hlsBody += '<p class="note">No MGRS tile ring covers this point.</p>';
      }
      hlsBlock.tiles.forEach(function (t) {
        hlsBody += '<p class="note">' + t.tile + ": " + t.clear + " clear of " + t.acq.length + "</p>";
        var shownH = t.acq.slice(0, 20);
        hlsBody += '<ul class="emit-list">' + shownH.map(function (a) {
          var clear = a.cloud !== null && a.cloud <= hlsBlock.cloud &&
                      (hlsBlock.sensor === "ALL" || a.sensor === hlsBlock.sensor);
          return "<li" + (clear ? ' class="in"' : "") + '><span class="when">' + a.date + "</span>" +
                 "<span>" + a.sensor + "</span>" +
                 "<span>" + (a.cloud === null ? "?" : a.cloud + "%") + " cloud</span>" +
                 (clear ? " <span>✓</span>" : "") + "</li>";
        }).join("") + "</ul>";
        if (t.acq.length > shownH.length) {
          hlsBody += '<p class="note">and ' + (t.acq.length - shownH.length) + " more</p>";
        }
      });
      html += foldable("hls", "HLS acquisitions, " +
                       (hlsBlock.start ? hlsBlock.start + " to " + hlsBlock.end : "no week selected") +
                       ": " + totalClear + " clear of " + totalAcq, hlsBody);
    }
    return html;
  }

  function refresh() {
    syncMaskControl();
    drawCdl();
    drawCpc();
    drawLegend();
    drawPairing();
    syncEmit();
    syncEco();
    syncHls();
    el("weekOut").textContent = state.week === null ? "no weeks" : "w" + pad(state.week);
  }

  function syncWeekSlider() {
    var weeks = weeksFor(state.crop, state.var, state.year);
    var slider = el("week");
    slider.min = 0;
    slider.max = Math.max(0, weeks.length - 1);
    var index = weeks.indexOf(state.week);
    if (index < 0 && weeks.length) {
      index = Math.floor(weeks.length / 2);
      state.week = weeks[index];
    }
    // An empty week list must not index weeks[-1] and leave state.week undefined,
    // which would request /undefined/ tiles and print "wundefined".
    if (!weeks.length) { state.week = null; }
    slider.disabled = !weeks.length;
    slider.value = Math.max(0, index);
    slider.dataset.weeks = JSON.stringify(weeks);
  }

  function syncCdlYear() {
    // If no CDL rasters are extracted at all, cdl_pairing is {} and a lookup
    // would leave state.cdlYear undefined, which requests /tiles/cdl/undefined/.
    state.cdlYear = (state.catalog.cdl_years || []).length
      ? state.catalog.cdl_pairing[String(state.year)]
      : null;
    fillSelect(el("cdlYear"), state.catalog.cdl_years, state.cdlYear);
  }

  function syncYearSelect() {
    var years = yearsFor(state.crop, state.var);
    if (years.indexOf(state.year) < 0) { state.year = years[years.length - 1]; }
    fillSelect(el("year"), years, state.year);
    syncCdlYear();
  }

  function stepWeek(delta) {
    var weeks = JSON.parse(el("week").dataset.weeks || "[]");
    if (!weeks.length) { return; }
    var index = (weeks.indexOf(state.week) + delta + weeks.length) % weeks.length;
    state.week = weeks[index];
    el("week").value = index;
    refresh();
  }

  function wire() {
    el("crop").addEventListener("change", function (e) {
      state.crop = e.target.value; syncYearSelect(); syncWeekSlider(); refresh();
    });
    el("var").addEventListener("change", function (e) {
      state.var = e.target.value; syncYearSelect(); syncWeekSlider(); refresh();
    });
    el("year").addEventListener("change", function (e) {
      state.year = Number(e.target.value);
      syncCdlYear();
      syncWeekSlider(); refresh();
    });
    el("cdlYear").addEventListener("change", function (e) {
      state.cdlYear = Number(e.target.value); refresh();
    });
    el("week").addEventListener("input", function (e) {
      var weeks = JSON.parse(e.target.dataset.weeks || "[]");
      state.week = weeks[Number(e.target.value)];
      refresh();
    });
    el("prev").addEventListener("click", function () { stepWeek(-1); });
    el("next").addEventListener("click", function () { stepWeek(1); });
    el("play").addEventListener("click", function () {
      state.playing = !state.playing;
      el("play").textContent = state.playing ? "Pause" : "Play";
      if (state.playing) {
        state.timer = setInterval(function () { stepWeek(1); }, 700);
      } else {
        clearInterval(state.timer);
      }
    });
    el("cdlOpacity").addEventListener("input", function (e) {
      state.cdlOpacity = Number(e.target.value) / 100;
      el("cdlOpacityOut").textContent = e.target.value + "%";
      if (cdlLayer) { cdlLayer.setOpacity(state.cdlOpacity); }
    });
    el("opacity").addEventListener("input", function (e) {
      state.opacity = Number(e.target.value) / 100;
      el("opacityOut").textContent = e.target.value + "%";
      if (cpcLayer && state.mode === "overlay") { cpcLayer.setOpacity(state.opacity); }
    });
    el("cdl").addEventListener("change", function (e) {
      state.cdlVisible = e.target.checked; drawCdl();
    });
    el("mask").addEventListener("change", function (e) {
      state.mask = e.target.checked; syncDimControl(); drawCdl(); drawCpc();
    });
    Array.prototype.forEach.call(document.getElementsByName("dim"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (e.target.checked) { state.dim = e.target.value; syncDimControl(); drawCdl(); }
      });
    });
    el("states").addEventListener("change", syncStates);
    map.on("zoomend", syncStates);
    el("emit").addEventListener("change", function (e) { state.emit = e.target.checked; syncEmit(); });
    el("emitCloud").addEventListener("input", function (e) {
      state.emitCloud = Number(e.target.value); el("emitCloudOut").textContent = e.target.value + "%"; syncEmit();
    });
    el("emitOnlyWindow").addEventListener("change", function (e) {
      state.emitOnlyWindow = e.target.checked; syncEmit();
    });
    el("timeWindow").addEventListener("input", function (e) {
      state.days = Number(e.target.value);
      el("timeWindowOut").textContent = e.target.value === "0" ? "off" : e.target.value;
      syncEmit();
      syncEco();
      if (state.hls) { drawHlsLegend(); refreshHlsCounts(); }
    });
    el("coincide").addEventListener("change", function (e) { state.coincide = e.target.checked; syncEmit(); });
    el("coincideAll").addEventListener("change", function (e) { state.coincideAll = e.target.checked; syncEmit(); syncHls(); });
    el("coincideWindow").addEventListener("input", function (e) {
      state.coincideStep = Number(e.target.value);
      el("coincideWindowOut").textContent = stepLabel(state.coincideStep);
      syncEmit();
    });
    el("coincideOnly").addEventListener("change", function (e) { state.coincideOnly = e.target.checked; syncEmit(); });
    el("eco").addEventListener("change", function (e) { state.eco = e.target.checked; syncEco(); });
    Array.prototype.forEach.call(document.getElementsByName("ecoDay"), function (radio) {
      radio.addEventListener("change", function (e) { if (e.target.checked) { state.ecoDay = e.target.value; syncEco(); } });
    });
    el("hls").addEventListener("change", function (e) { state.hls = e.target.checked; syncHls(); });
    el("hlsCloud").addEventListener("input", function (e) {
      state.hlsCloud = Number(e.target.value);
      el("hlsCloudOut").textContent = e.target.value + "%";
      refreshHlsCounts();
      if (state.coincideAll) { syncEmit(); }
    });
    ["hlsMode", "hlsSensor"].forEach(function (name) {
      Array.prototype.forEach.call(document.getElementsByName(name), function (radio) {
        radio.addEventListener("change", function (e) {
          if (!e.target.checked) { return; }
          state[name] = e.target.value;
          drawHlsLegend();
          refreshHlsCounts();
          if (state.coincideAll) { syncEmit(); }
        });
      });
    });
    Array.prototype.forEach.call(document.getElementsByName("mode"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (e.target.checked) { state.mode = e.target.value; applyMode(); }
      });
    });

    map.on("click", function (e) {
      // Cap the box at 60% of the map so long lists scroll inside it (Leaflet
      // adds leaflet-popup-scrolled) instead of running off the map.
      readoutPopup = L.popup({ maxWidth: 320, maxHeight: Math.round(map.getSize().y * 0.6),
                               className: "readout-popup" })
        .setLatLng(e.latlng)
        .setContent('<p class="note">Reading…</p>')
        .openOn(map);
      var range = hlsRange();
      var url = "/api/point?lon=" + e.latlng.lng.toFixed(6) +
                "&lat=" + e.latlng.lat.toFixed(6) +
                "&crop=" + state.crop + "&year=" + state.year + "&cdl_year=" + state.cdlYear +
                (state.week !== null ? "&week=" + state.week : "") +
                (range ? "&start=" + range.start + "&end=" + range.end : "") +
                "&cloud=" + state.hlsCloud + "&sensor=" + state.hlsSensor + "&window=" + state.days;
      var popup = readoutPopup;
      fetch(url).then(function (r) { return r.json(); })
        .then(function (report) {
          if (popup.isOpen()) { popup.setContent(showReadout(report)); wireFolds(popup); }
        })
        .catch(function () { if (popup.isOpen()) { popup.setContent('<p class="note">Read failed.</p>'); } });
    });

  }

  fetch("/api/catalog").then(function (r) { return r.json(); }).then(function (catalog) {
    state.catalog = catalog;
    fillSelect(el("crop"), catalog.crops, state.crop);
    fillSelect(el("var"), catalog.vars, state.var, function (v) { return catalog.var_labels[v]; });
    syncYearSelect();
    syncWeekSlider();
    wire();
    refresh();
    loadStates();
    drawEmitLegend(); drawEcoLegend(); drawHlsLegend(); syncEmit(); syncEco(); syncHls();
  }).catch(function () {
    el("pairing").textContent = "Catalog request failed; is the server running? Reload to retry.";
  });
})();
