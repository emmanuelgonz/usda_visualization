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
    emit: false,
    emitCloud: 30,
    emitWindow: 7
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

  // State names are shown below this zoom; above it one state fills the view.
  var LABEL_MAX_ZOOM = 9;
  var stateLines = null;
  var stateLabels = null;

  var cdlLayer = null;
  var cpcLayer = null;
  var readoutPopup = null;
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
    if (cpcLayer) {
      cpcLayer.setUrl(url);
    } else {
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

  function emitWindowBounds() {
    if (!state.emitWindow || state.week === null) { return null; }
    var centre = weekSunday(state.year, state.week).getTime();
    var span = state.emitWindow * 86400000;
    return [centre - span, centre + span];
  }

  function emitStyle(feature) {
    var p = feature.properties;
    if (p.cloud !== null && p.cloud > state.emitCloud) { return { stroke: false, fill: false }; }
    var bounds = emitWindowBounds();
    var t = Date.parse(p.start);
    var inWindow = bounds && t >= bounds[0] && t <= bounds[1];
    return {
      renderer: emitRenderer, fill: false,
      color: inWindow ? EMIT_HIGHLIGHT : (EMIT_YEAR_COLOURS[p.year] || "#666"),
      weight: inWindow ? 2.5 : 1,
      opacity: inWindow ? 0.95 : (bounds ? 0.35 : 0.8)
    };
  }

  function loadEmit() {
    if (emitLoaded || !(state.catalog.emit_count > 0)) { return; }
    emitLoaded = true;
    fetch("/api/emit/footprints.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      emitLayer = L.geoJSON(geo, {
        pane: "emit", renderer: emitRenderer, style: emitStyle,
        onEachFeature: function (f, layer) {
          var p = f.properties;
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
      '<span class="hl" style="--swatch:' + EMIT_HIGHLIGHT + '">within window</span>';
  }

  function syncEmit() {
    var available = state.catalog.emit_count > 0;
    var box = el("emit");
    box.disabled = !available;
    box.parentNode.title = available ? "" : "No EMIT footprints; run ./run.sh emit";
    el("emitControls").classList.toggle("disabled", !(available && state.emit));
    el("emitCloud").disabled = el("emitWindow").disabled = !(available && state.emit);
    if (!state.emit && emitLayer && map.hasLayer(emitLayer)) { map.removeLayer(emitLayer); }
    if (state.emit) {
      if (!emitLayer) { loadEmit(); return; }
      if (!map.hasLayer(emitLayer)) { emitLayer.addTo(map); }
      emitLayer.setStyle(emitStyle);
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
    html += '<p class="section">EMIT scenes covering this point: ' + granules.length + "</p>";
    if (granules.length) {
      var centre = report.week_sunday ? Date.parse(report.week_sunday + "T00:00:00Z") : null;
      var bounds = emitWindowBounds();
      var sorted = granules.slice().sort(function (a, b) {
        if (centre === null) { return Date.parse(b.start) - Date.parse(a.start); }
        return Math.abs(Date.parse(a.start) - centre) - Math.abs(Date.parse(b.start) - centre);
      });
      var shown = sorted.slice(0, 15);
      html += '<ul class="emit-list">' + shown.map(function (g) {
        var t = Date.parse(g.start);
        var inWin = bounds && t >= bounds[0] && t <= bounds[1];
        return "<li" + (inWin ? ' class="in"' : "") + '><span class="when">' + g.start.slice(0, 10) + "</span>" +
               "<span>" + (g.cloud === null ? "?" : g.cloud.toFixed(0) + "%") + " cloud</span>" +
               (g.browse ? ' <a href="' + g.browse + '" target="_blank" rel="noopener">browse</a>' : "") +
               (g.data ? ' <a href="' + g.data + '" target="_blank" rel="noopener">data</a>' : "") +
               (inWin ? " <span>★</span>" : "") + "</li>";
      }).join("") + "</ul>";
      if (granules.length > shown.length) {
        html += '<p class="note">and ' + (granules.length - shown.length) + " more</p>";
      }
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
    el("emitWindow").addEventListener("input", function (e) {
      state.emitWindow = Number(e.target.value);
      el("emitWindowOut").textContent = e.target.value === "0" ? "off" : e.target.value;
      syncEmit();
    });
    Array.prototype.forEach.call(document.getElementsByName("mode"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (e.target.checked) { state.mode = e.target.value; applyMode(); }
      });
    });

    map.on("click", function (e) {
      readoutPopup = L.popup({ maxWidth: 320, className: "readout-popup" })
        .setLatLng(e.latlng)
        .setContent('<p class="note">Reading…</p>')
        .openOn(map);
      var url = "/api/point?lon=" + e.latlng.lng.toFixed(6) +
                "&lat=" + e.latlng.lat.toFixed(6) +
                "&crop=" + state.crop + "&year=" + state.year + "&cdl_year=" + state.cdlYear +
                (state.week !== null ? "&week=" + state.week : "");
      var popup = readoutPopup;
      fetch(url).then(function (r) { return r.json(); })
        .then(function (report) { if (popup.isOpen()) { popup.setContent(showReadout(report)); } })
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
    drawEmitLegend(); syncEmit();
  });
})();
