// Map rendering and motion: glowing cities, taste heatmap, self-drawing route.

import { baseStyle, HEAT_RAMP } from "./map-style.js";

const EMPTY = { type: "FeatureCollection", features: [] };
const US_BOUNDS = [[-125.5, 24.0], [-66.5, 49.5]];

const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const clamp01 = (t) => Math.max(0, Math.min(1, t));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Quadratic arc between two stops, bulging to the left of travel.
function arc(a, b, steps = 48) {
  const [x1, y1] = a;
  const [x2, y2] = b;
  const dx = x2 - x1;
  const dy = y2 - y1;
  // Bulge grows with leg length but is capped so long legs stay on the map.
  const len = Math.hypot(dx, dy) || 1;
  const offset = Math.min(len * 0.14, 2.2);
  const cx = (x1 + x2) / 2 - (dy / len) * offset;
  const cy = (y1 + y2) / 2 + (dx / len) * offset;
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    pts.push([u * u * x1 + 2 * u * t * cx + t * t * x2, u * u * y1 + 2 * u * t * cy + t * t * y2]);
  }
  return pts;
}

function pathLength(pts) {
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return total;
}

export class TourMap {
  constructor(container, { padding } = {}) {
    this.padding = padding || { top: 140, bottom: 90, left: 60, right: 440 };
    this.cities = new Map(); // id -> { city, glow, target, start, chosen, order }
    this.route = null; // { coords, stopIndex[], length }
    this.routeProgress = 0;
    this.comet = null;
    this.markers = [];
    this._raf = null;
    this._t0 = performance.now();

    this.map = new maplibregl.Map({
      container,
      style: baseStyle(),
      bounds: US_BOUNDS,
      fitBoundsOptions: { padding: this.padding },
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      maxPitch: 0,
    });
    this.map.touchZoomRotate.disableRotation();
    this.map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");
    this.map.on("moveend", () => this._layoutLabels());
    this.ready = new Promise((resolve) => this.map.on("load", () => { this._addLayers(); resolve(); }));
  }

  _addLayers() {
    const m = this.map;
    m.addSource("taste", { type: "geojson", data: EMPTY });
    m.addSource("cities", { type: "geojson", data: EMPTY });
    m.addSource("route", { type: "geojson", data: EMPTY, lineMetrics: true });
    m.addSource("comet", { type: "geojson", data: EMPTY });

    m.addLayer({
      id: "taste-heat",
      type: "heatmap",
      source: "taste",
      paint: {
        "heatmap-weight": ["^", ["get", "a"], 2.2],
        "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 3, 1.1, 6, 1.8],
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 3, 22, 5, 42, 7, 70],
        "heatmap-color": HEAT_RAMP,
        "heatmap-opacity": 0,
        "heatmap-opacity-transition": { duration: 1800 },
      },
    });

    const glowRadius = (base) => [
      "interpolate", ["linear"], ["zoom"],
      3, ["*", base, ["get", "glow"], ["+", 0.45, ["get", "affinity"]]],
      8, ["*", base * 2.2, ["get", "glow"], ["+", 0.45, ["get", "affinity"]]],
    ];

    m.addLayer({
      id: "city-halo",
      type: "circle",
      source: "cities",
      paint: {
        "circle-radius": glowRadius(26),
        "circle-color": "#ff7a1a",
        "circle-blur": 1,
        "circle-opacity": ["*", 0.32, ["min", ["get", "glow"], 1]],
      },
    });
    m.addLayer({
      id: "city-glow",
      type: "circle",
      source: "cities",
      paint: {
        "circle-radius": glowRadius(10),
        "circle-color": ["interpolate", ["linear"], ["get", "affinity"], 0, "#b8420c", 0.6, "#ff9a3c", 1, "#ffd59a"],
        "circle-blur": 0.7,
        "circle-opacity": ["min", ["get", "glow"], 1],
      },
    });

    m.addLayer({
      id: "route-glow",
      type: "line",
      source: "route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ff8a2a", "line-width": 12, "line-blur": 10, "line-opacity": 0.45 },
    });
    m.addLayer({
      id: "route-core",
      type: "line",
      source: "route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-width": 2.2,
        "line-gradient": [
          "interpolate", ["linear"], ["line-progress"],
          0, "#ff9a3c", 0.5, "#ffc874", 1, "#fff1d6",
        ],
      },
    });

    m.addLayer({
      id: "city-core",
      type: "circle",
      source: "cities",
      paint: {
        "circle-radius": ["case", ["==", ["get", "chosen"], 1],
          ["+", 3.6, ["*", 1.4, ["get", "pulse"]]],
          ["*", 1.8, ["min", ["get", "glow"], 1]]],
        "circle-color": "#fff6e6",
        "circle-opacity": ["min", ["get", "glow"], 1],
        "circle-stroke-width": ["case", ["==", ["get", "chosen"], 1], 1.5, 0],
        "circle-stroke-color": "#ffb347",
        "circle-stroke-opacity": ["min", ["get", "glow"], 1],
      },
    });

    m.addLayer({
      id: "comet-halo",
      type: "circle",
      source: "comet",
      paint: { "circle-radius": 18, "circle-color": "#ffb347", "circle-blur": 1, "circle-opacity": 0.7 },
    });
    m.addLayer({
      id: "comet-core",
      type: "circle",
      source: "comet",
      paint: { "circle-radius": 3.5, "circle-color": "#ffffff" },
    });
  }

  // --- state ---------------------------------------------------------------

  setCandidates(cities, { glow = 0.12 } = {}) {
    this.cities.clear();
    for (const city of cities) {
      this.cities.set(city.id, { city, glow, from: glow, target: glow, start: 0, dur: 1, chosen: false, affinity: 0.15 });
    }
    this._loop();
  }

  setTaste(cells) {
    const features = cells.map(([lon, lat, a]) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: { a },
    }));
    this.map.getSource("taste").setData({ type: "FeatureCollection", features });
    this.map.setPaintProperty("taste-heat", "heatmap-opacity", cells.length ? 0.85 : 0);
  }

  reset() {
    this.markers.forEach((m) => m.remove());
    this.markers = [];
    this.route = null;
    this.routeProgress = 0;
    this.comet = null;
    this.map.getSource("route").setData(EMPTY);
    this.map.getSource("comet").setData(EMPTY);
    this.map.setPaintProperty("taste-heat", "heatmap-opacity", 0);
    for (const s of this.cities.values()) {
      s.chosen = false;
      this._animateGlow(s, 0.08, 400, 0);
    }
  }

  _animateGlow(state, target, dur, delay) {
    state.from = state.glow;
    state.target = target;
    state.start = performance.now() + delay;
    state.dur = dur;
    state.flash = target > state.glow + 0.2;
  }

  // Cities ignite one after another, strongest first.
  async ignite(ranked, chosenIds, { stepMs = 55 } = {}) {
    const chosen = new Set(chosenIds);
    const top = ranked[0]?.affinity || 1;
    const bottom = ranked[ranked.length - 1]?.affinity || 0;
    const span = Math.max(top - bottom, 1e-6);
    ranked.forEach((c, i) => {
      const s = this.cities.get(c.id);
      if (!s) return;
      s.affinity = (c.affinity - bottom) / span;
      s.chosen = chosen.has(c.id);
      const target = s.chosen ? 1 : 0.18 + 0.55 * s.affinity;
      this._animateGlow(s, target, 900, i * stepMs);
    });
    await wait(ranked.length * stepMs + 700);
  }

  async focus(stops, duration = 1600) {
    const b = new maplibregl.LngLatBounds();
    stops.forEach((s) => b.extend([s.lon, s.lat]));
    this.map.fitBounds(b, { padding: this.padding, duration, maxZoom: 6.2, essential: true });
    await wait(duration);
  }

  // Draw the route leg by leg. onStop(i) fires when the head reaches stop i.
  drawRoute(stops, { msPerUnit = 55, minLegMs = 380, onStop } = {}) {
    const coords = [];
    const stopIndex = [0];
    for (let i = 1; i < stops.length; i++) {
      const leg = arc([stops[i - 1].lon, stops[i - 1].lat], [stops[i].lon, stops[i].lat]);
      coords.push(...(i === 1 ? leg : leg.slice(1)));
      stopIndex.push(coords.length - 1);
    }
    if (stops.length === 1) coords.push([stops[0].lon, stops[0].lat]);
    this.route = { coords, stopIndex, stops };
    this._placeMarkers(stops);
    onStop?.(0);
    this._revealMarker(0);

    return new Promise((resolve) => {
      const legs = stopIndex.slice(1).map((end, i) => {
        const pts = coords.slice(stopIndex[i], end + 1);
        return { from: stopIndex[i], to: end, ms: Math.max(minLegMs, pathLength(pts) * msPerUnit) };
      });
      let leg = 0;
      let legStart = performance.now();
      const step = (now) => {
        if (!this.route || leg >= legs.length) {
          this.routeProgress = coords.length - 1;
          this._setRouteData(coords.length - 1);
          this.comet = { start: performance.now() };
          resolve();
          return;
        }
        const L = legs[leg];
        const t = easeInOutCubic(clamp01((now - legStart) / L.ms));
        const head = L.from + (L.to - L.from) * t;
        this._setRouteData(head);
        if (t >= 1) {
          leg += 1;
          onStop?.(leg);
          this._revealMarker(leg);
          this._flash(stops[leg]);
          legStart = now + 120;
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }

  _setRouteData(head) {
    const { coords } = this.route;
    const i = Math.floor(head);
    const f = head - i;
    const pts = coords.slice(0, i + 1);
    if (f > 0 && i + 1 < coords.length) {
      const [a, b] = [coords[i], coords[i + 1]];
      pts.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
    }
    const tip = pts[pts.length - 1];
    this.map.getSource("route").setData(pts.length > 1
      ? { type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: {} }
      : EMPTY);
    this.map.getSource("comet").setData({ type: "Feature", geometry: { type: "Point", coordinates: tip }, properties: {} });
  }

  _flash(stop) {
    const s = stop && this.cities.get(stop.id);
    if (!s) return;
    s.glow = 1.8;
    this._animateGlow(s, 1, 700, 0);
  }

  _placeMarkers(stops) {
    this.markers.forEach((m) => m.remove());
    this.markers = stops.map((stop, i) => {
      const el = document.createElement("div");
      el.className = "stop-label";
      el.innerHTML = `
        <span class="stop-num">${String(i + 1).padStart(2, "0")}</span>
        <span class="stop-text">
          <span class="stop-name">${stop.name}</span>
          <span class="stop-meta">${stop.state} · ${stop.affinity.toFixed(3)}</span>
        </span>`;
      return new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([stop.lon, stop.lat])
        .addTo(this.map);
    });
    this._layoutLabels();
  }

  _revealMarker(i) {
    this.markers[i]?.getElement().classList.add("is-visible");
  }

  // Greedy label placement: for each stop pick the side (right, left, above,
  // below) whose box overlaps least with earlier labels, stop dots and the UI
  // panels. Panels and the screen edge weigh most, since a hidden label is worse
  // than two touching ones.
  _layoutLabels() {
    if (!this.markers.length) return;
    const canvas = this.map.getCanvas().getBoundingClientRect();
    const panels = [...document.querySelectorAll(".panel, .composer")].map((n) => n.getBoundingClientRect());
    const dots = this.markers.map((m) => this.map.project(m.getLngLat()))
      .map((p) => ({ x: canvas.left + p.x, y: canvas.top + p.y }));
    const placed = [];
    const overlap = (a, b) =>
      Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) *
      Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));

    this.markers.forEach((m, i) => {
      const el = m.getElement();
      const w = el.offsetWidth || 120;
      const h = el.offsetHeight || 32;
      const { x, y } = dots[i];
      const options = [
        { side: "right", offset: [14 + w / 2, 0], bias: 0 },
        { side: "left", offset: [-14 - w / 2, 0], bias: 1 },
        { side: "above", offset: [0, -14 - h / 2], bias: 2 },
        { side: "below", offset: [0, 14 + h / 2], bias: 3 },
      ].map((o) => {
        const cx = x + o.offset[0];
        const cy = y + o.offset[1];
        const r = { left: cx - w / 2, right: cx + w / 2, top: cy - h / 2, bottom: cy + h / 2 };
        let cost = o.bias;
        placed.forEach((p) => (cost += overlap(r, p)));
        dots.forEach((d, j) => {
          if (j !== i) cost += overlap(r, { left: d.x - 10, right: d.x + 10, top: d.y - 10, bottom: d.y + 10 }) * 2;
        });
        panels.forEach((p) => (cost += overlap(r, p) * 10));
        const offscreen = Math.max(0, -r.left) + Math.max(0, r.right - window.innerWidth) +
          Math.max(0, -r.top) + Math.max(0, r.bottom - window.innerHeight);
        cost += offscreen * h * 10;
        return { ...o, r, cost };
      });
      const choice = options.reduce((a, b) => (b.cost < a.cost ? b : a));
      placed.push(choice.r);
      el.classList.toggle("is-left", choice.side === "left");
      m.setOffset(choice.offset);
    });
  }

  // --- frame loop ------------------------------------------------------------

  _loop() {
    if (this._raf) return;
    const frame = (now) => {
      this._raf = requestAnimationFrame(frame);
      this._renderCities(now);
      this._renderComet(now);
    };
    this._raf = requestAnimationFrame(frame);
  }

  _renderCities(now) {
    const features = [];
    const breathe = (Math.sin((now - this._t0) / 520) + 1) / 2;
    for (const s of this.cities.values()) {
      if (now >= s.start) {
        const t = clamp01((now - s.start) / s.dur);
        // A brief overshoot makes each city "ignite" rather than fade in.
        const flash = s.flash ? Math.sin(Math.PI * Math.min(t * 1.6, 1)) * 0.6 : 0;
        s.glow = s.from + (s.target - s.from) * easeOutCubic(t) + flash * (1 - t);
      }
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.city.lon, s.city.lat] },
        properties: { glow: s.glow, affinity: s.affinity, chosen: s.chosen ? 1 : 0, pulse: s.chosen ? breathe : 0 },
      });
    }
    this.map.getSource("cities")?.setData({ type: "FeatureCollection", features });
  }

  _renderComet(now) {
    if (!this.comet || !this.route) return;
    const { coords } = this.route;
    const period = Math.max(4000, coords.length * 22);
    const t = ((now - this.comet.start) % period) / period;
    const head = easeInOutCubic(t) * (coords.length - 1);
    const i = Math.floor(head);
    const f = head - i;
    const a = coords[i];
    const b = coords[Math.min(i + 1, coords.length - 1)];
    this.map.getSource("comet").setData({
      type: "Feature",
      geometry: { type: "Point", coordinates: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f] },
      properties: {},
    });
  }
}
