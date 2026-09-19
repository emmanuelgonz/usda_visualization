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
    opacity: 0.7,
    playing: false,
    timer: null
  };

  var map = L.map("map", { center: [39.5, -96.0], zoom: 4, minZoom: 3, maxZoom: 15 });
  map.createPane("cdl");
  map.createPane("cpc");
  map.getPane("cdl").style.zIndex = 400;
  map.getPane("cpc").style.zIndex = 450;

  var cdlLayer = null;
  var cpcLayer = null;
  var marker = null;
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
    if (cdlLayer) { map.removeLayer(cdlLayer); }
    cdlLayer = L.tileLayer("/tiles/cdl/" + state.cdlYear + "/{z}/{x}/{y}.png", {
      pane: "cdl", maxNativeZoom: 15, maxZoom: 15, noWrap: true,
      attribution: "USDA NASS Cropland Data Layer " + state.cdlYear
    }).addTo(map);
  }

  function drawCpc() {
    if (cpcLayer) { map.removeLayer(cpcLayer); cpcLayer = null; }
    if (state.week === null || state.week === undefined) { return; }
    var url = "/tiles/cpc/" + state.crop + "/" + state.var + "/" + state.year +
              "/" + state.week + "/{z}/{x}/{y}.png" +
              (state.mask ? "?mask=" + state.cdlYear : "");
    cpcLayer = L.tileLayer(url, {
      pane: "cpc", maxNativeZoom: 15, maxZoom: 15, noWrap: true,
      opacity: state.opacity,
      attribution: "USDA NASS Crop Progress and Condition " + state.year
    }).addTo(map);
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
      positionSwipe();
    } else {
      if (swipeHandle) { swipeHandle.style.display = "none"; }
      pane.style.clipPath = "";
      pane.style.opacity = 1;
      if (cpcLayer) { cpcLayer.setOpacity(state.opacity); }
    }
  }

  function positionSwipe() {
    var width = map.getContainer().clientWidth;
    var x = Math.round(width * swipeFraction);
    swipeHandle.style.left = x + "px";
    map.getPane("cpc").style.clipPath = "inset(0 0 0 " + x + "px)";
    if (cpcLayer) { cpcLayer.setOpacity(1); }
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
    var text = "CPC " + state.year + " week " + state.week + " over CDL " + state.cdlYear;
    if (Number(state.cdlYear) !== Number(state.year)) {
      text += " (no CDL for " + state.year + "; nearest is " + paired + ")";
    }
    if (!maskAvailable()) {
      text += ". No crop mask built for CDL " + state.cdlYear + ".";
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
    var html =
      "<h2>" + (report.cdl_class || "Unknown") + "</h2>" +
      "<table>" +
      "<tr><th>" + report.crop + " cover</th><td>" + pct(total) + "</td></tr>" +
      "<tr><th>&nbsp;&nbsp;primary</th><td>" + pct(cover.primary) + "</td></tr>" +
      "<tr><th>&nbsp;&nbsp;double-crop</th><td>" + pct(cover.double) + "</td></tr>" +
      "</table>" +
      "<p class=\"note\">Condition, weeks " + state.year + "</p>" +
      sparkline(report.series.cond || [], 1, 5) +
      "<p class=\"note\">Progress, weeks " + state.year + "</p>" +
      sparkline(report.series.prog || [], 0, 1);
    el("readout").innerHTML = html;
  }

  function refresh() {
    syncMaskControl();
    drawCdl();
    drawCpc();
    drawLegend();
    drawPairing();
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

  function syncYearSelect() {
    var years = yearsFor(state.crop, state.var);
    if (years.indexOf(state.year) < 0) { state.year = years[years.length - 1]; }
    fillSelect(el("year"), years, state.year);
    state.cdlYear = state.catalog.cdl_pairing[String(state.year)];
    fillSelect(el("cdlYear"), state.catalog.cdl_years, state.cdlYear);
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
      state.cdlYear = state.catalog.cdl_pairing[String(state.year)];
      fillSelect(el("cdlYear"), state.catalog.cdl_years, state.cdlYear);
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
    el("opacity").addEventListener("input", function (e) {
      state.opacity = Number(e.target.value) / 100;
      el("opacityOut").textContent = e.target.value + "%";
      if (cpcLayer && state.mode === "overlay") { cpcLayer.setOpacity(state.opacity); }
    });
    el("mask").addEventListener("change", function (e) {
      state.mask = e.target.checked; drawCpc();
    });
    Array.prototype.forEach.call(document.getElementsByName("mode"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (e.target.checked) { state.mode = e.target.value; applyMode(); }
      });
    });

    map.on("click", function (e) {
      if (marker) { map.removeLayer(marker); }
      marker = L.circleMarker(e.latlng, { radius: 5, color: "#fff", weight: 2,
                                          fillColor: "#111", fillOpacity: 1 }).addTo(map);
      el("readout").innerHTML = '<p class="note">Reading…</p>';
      var url = "/api/point?lon=" + e.latlng.lng.toFixed(6) +
                "&lat=" + e.latlng.lat.toFixed(6) +
                "&crop=" + state.crop + "&year=" + state.year + "&cdl_year=" + state.cdlYear;
      fetch(url).then(function (r) { return r.json(); }).then(showReadout)
        .catch(function () { el("readout").innerHTML = '<p class="note">Read failed.</p>'; });
    });

    map.on("resize", function () { if (state.mode === "swipe") { positionSwipe(); } });
  }

  fetch("/api/catalog").then(function (r) { return r.json(); }).then(function (catalog) {
    state.catalog = catalog;
    fillSelect(el("crop"), catalog.crops, state.crop);
    fillSelect(el("var"), catalog.vars, state.var, function (v) { return catalog.var_labels[v]; });
    syncYearSelect();
    syncWeekSlider();
    wire();
    refresh();
  });
})();
