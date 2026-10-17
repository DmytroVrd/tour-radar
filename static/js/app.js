import { TourMap } from "./tour-map.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => Math.round((x ?? 0) * 100);
const signed = (n) => (n > 0 ? `+${n}` : `${n}`);

const els = {
  input: $("artist-input"), field: $("artist-field"), chips: $("chips"), suggest: $("suggest"),
  dates: $("dates"), scan: $("scan-btn"), examples: $("examples"),
  tabs: document.querySelectorAll(".tab"), tabAgent: $("tab-agent"), tabTour: $("tab-tour"), cityCard: $("city-card"),
  trace: $("trace"), stats: $("trace-stats"), runBadge: $("run-badge"),
  tourTitle: $("tour-title"), tourHeadline: $("tour-headline"), audience: $("audience"), list: $("tour-list"),
  total: $("tour-total"), copy: $("copy-sheet"), csv: $("download-csv"),
  badge: $("mode-badge"), toast: $("toast"), legendTitle: $("legend-title"),
};

const MAX_ARTISTS = 3;
const isNarrow = () => window.matchMedia("(max-width: 860px)").matches;

const state = {
  artists: [],
  running: false,
  source: null,
  stream: null,
  cities: [],
  profiles: {},
  route: null,
  plan: null,
  currentTab: "agent",
};

const tourMap = new TourMap("map", {
  padding: isNarrow()
    ? { top: 230, bottom: Math.round(window.innerHeight * 0.42) + 20, left: 30, right: 30 }
    : { top: 130, bottom: 100, left: 480, right: 560 },
  onCityClick: (id) => openCity(id),
});

// A small queue so map animations never overlap while events keep streaming.
let mapQueue = Promise.resolve();
const onMap = (fn) => (mapQueue = mapQueue.then(fn).catch((e) => console.error(e)));

// --- tabs ---------------------------------------------------------------------------

function showTab(name) {
  state.currentTab = name;
  els.tabs.forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle("is-active", on);
    t.setAttribute("aria-selected", on);
  });
  els.tabAgent.hidden = name !== "agent";
  els.tabTour.hidden = name !== "tour";
  els.cityCard.hidden = name !== "city";
}
els.tabs.forEach((t) => t.addEventListener("click", () => {
  if (t.disabled) return;
  if (tourMap.inCity) closeCity();
  showTab(t.dataset.tab);
}));

// --- artist input + autocomplete ------------------------------------------------------

function renderChips() {
  els.chips.innerHTML = "";
  state.artists.forEach((name, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name;
    const x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", `Remove ${name}`);
    x.textContent = "×";
    x.onclick = (e) => { e.stopPropagation(); state.artists.splice(i, 1); renderChips(); };
    chip.appendChild(x);
    els.chips.appendChild(chip);
  });
  const full = state.artists.length >= MAX_ARTISTS;
  els.input.placeholder = full ? "" : state.artists.length ? "Add another (optional)" : "Type an artist";
  els.input.disabled = full;
}

function addArtist(name) {
  const clean = name.trim();
  if (!clean || state.artists.length >= MAX_ARTISTS) return;
  if (!state.artists.some((a) => a.toLowerCase() === clean.toLowerCase())) state.artists.push(clean);
  els.input.value = "";
  hideSuggest();
  renderChips();
}

let suggestTimer;
let suggestIndex = -1;
let suggestItems = [];
function hideSuggest() {
  els.suggest.hidden = true;
  els.input.setAttribute("aria-expanded", "false");
  suggestItems = [];
  suggestIndex = -1;
}
function renderSuggest() {
  els.suggest.innerHTML = suggestItems.map((s, i) => `
    <li role="option" class="${i === suggestIndex ? "is-active" : ""}" data-i="${i}">
      <span>${esc(s.name)}</span><small>${s.note && s.note !== s.name ? esc(s.note) : ""}</small>
    </li>`).join("");
  els.suggest.hidden = !suggestItems.length;
  els.input.setAttribute("aria-expanded", String(!!suggestItems.length));
}
els.suggest.addEventListener("mousedown", (e) => {
  const li = e.target.closest("li");
  if (li) { e.preventDefault(); addArtist(suggestItems[+li.dataset.i].name); }
});
els.input.addEventListener("input", () => {
  clearTimeout(suggestTimer);
  const q = els.input.value.trim();
  if (q.length < 2) return hideSuggest();
  suggestTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      if (!res.ok || els.input.value.trim() !== q) return;
      suggestItems = (await res.json()).slice(0, 5);
      suggestIndex = -1;
      renderSuggest();
    } catch { /* autocomplete is optional */ }
  }, 300);
});
els.input.addEventListener("keydown", (e) => {
  if (!els.suggest.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    suggestIndex = (suggestIndex + (e.key === "ArrowDown" ? 1 : -1) + suggestItems.length) % suggestItems.length;
    renderSuggest();
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (suggestIndex >= 0) addArtist(suggestItems[suggestIndex].name);
    else if (els.input.value.trim()) addArtist(els.input.value);
    else if (state.artists.length) start();
  } else if (e.key === "Escape") {
    hideSuggest();
  } else if (e.key === "Backspace" && !els.input.value && state.artists.length) {
    state.artists.pop();
    renderChips();
  }
});
els.input.addEventListener("blur", () => setTimeout(hideSuggest, 120));
els.field.addEventListener("click", () => els.input.focus());
els.scan.addEventListener("click", () => {
  if (els.input.value.trim()) addArtist(els.input.value);
  start();
});

// --- feedback ---------------------------------------------------------------------------

let toastTimer;
function toast(msg) {
  els.toast.textContent = msg;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (els.toast.hidden = true), 4500);
}

function setBusy(busy) {
  state.running = busy;
  els.scan.disabled = busy;
  els.scan.classList.toggle("is-busy", busy);
  els.scan.querySelector(".scan-btn-text").textContent = busy ? "Agent at work…" : "Plan my tour";
  els.examples.querySelectorAll("button").forEach((b) => (b.disabled = busy));
}

// --- trace ------------------------------------------------------------------------------

const TOOL_ICON = {
  search_artists: "search", scan_region: "radar", taste_profile: "spark",
  plan_route: "route", profile_cities: "pin", submit_plan: "check",
};

function traceItem(html, cls = "") {
  const li = document.createElement("li");
  li.className = `trace-item ${cls}`;
  li.innerHTML = html;
  els.trace.appendChild(li);
  requestAnimationFrame(() => li.classList.add("is-in"));
  els.trace.scrollTo({ top: els.trace.scrollHeight, behavior: "smooth" });
  return li;
}

function argChips(args) {
  const bits = [];
  for (const [k, v] of Object.entries(args || {})) {
    if (k === "artist_ids") continue;
    const val = Array.isArray(v) ? v.join(", ") : v;
    bits.push(`<span class="arg"><b>${esc(k)}</b> ${esc(val)}</span>`);
  }
  return bits.length ? `<div class="args">${bits.join("")}</div>` : "";
}

const toolRows = new Map();

function onToolStart(ev) {
  const li = traceItem(`
    <span class="ti-icon ti-${TOOL_ICON[ev.tool] || "dot"}" aria-hidden="true"></span>
    <div class="ti-body">
      <div class="ti-head"><span class="ti-label">${esc(ev.label)}</span><code>${esc(ev.tool)}</code></div>
      ${argChips(ev.args)}
      <ul class="ti-progress"></ul>
      <div class="ti-summary"></div>
    </div>
    <span class="ti-ms"></span>`, "is-running");
  toolRows.set(ev.id, li);
}

function onProgress(ev) {
  const li = toolRows.get(ev.id);
  if (li && ev.text) {
    const p = document.createElement("li");
    p.textContent = ev.text;
    li.querySelector(".ti-progress").appendChild(p);
    els.trace.scrollTo({ top: els.trace.scrollHeight, behavior: "smooth" });
  }
  if (ev.city_profile) {
    const prof = ev.city_profile;
    state.profiles[prof.city.id] = prof;
    tourMap.pulseCity(prof.city.id);
    markListReady(prof.city.id);
  }
}

function onToolEnd(ev) {
  const li = toolRows.get(ev.id);
  if (li) {
    li.classList.remove("is-running");
    li.classList.add(ev.ok ? "is-done" : "is-failed");
    li.querySelector(".ti-ms").textContent = ev.ms >= 1000 ? `${(ev.ms / 1000).toFixed(1)}s` : `${ev.ms}ms`;
    li.querySelector(".ti-summary").innerHTML = esc(ev.summary) + (ev.ok ? "" : ` <em>· the agent will adjust</em>`);
  }
  const data = ev.data || {};
  if (data.artists) {
    state.artists = data.artists.map((a) => a.name);
    renderChips();
  }
  if (data.cities) {
    state.cities = data.cities;
    if (li) li.querySelector(".ti-summary").insertAdjacentHTML("afterend", miniBars(data.cities.slice(0, 5)));
    onMap(async () => {
      tourMap.setTaste(data.cells || []);
      await tourMap.ignite(data.cities);
    });
  }
  if (data.profile) {
    state.profile = data.profile;
    if (li) li.querySelector(".ti-summary").insertAdjacentHTML("afterend", profileChips(data.profile, 3));
  }
  if (data.route) {
    state.route = data.route;
    renderRouteSkeleton(data.route);
    onMap(async () => {
      tourMap.markChosen(data.route.stops.map((s) => s.id));
      await tourMap.focus(data.route.stops);
      await tourMap.drawRoute(data.route.stops, { onStop: revealStop });
    });
  }
}

function miniBars(cities) {
  const max = Math.max(...cities.map((c) => c.score));
  return `<ol class="mini-bars">${cities.map((c) => `
    <li><span>${esc(c.name)}</span><i style="width:${Math.round((c.score / max) * 100)}%"></i><b>${pct(c.score)}</b></li>`).join("")}</ol>`;
}

function profileChips(p, n = 6) {
  const group = (label, items) => items?.length
    ? `<div class="pchips"><span>${label}</span>${items.slice(0, n).map((x) => `<em>${esc(x.name)}</em>`).join("")}</div>` : "";
  return group("Artists", p.similar_artists) + group("Brands", p.brands) + group("Travel", p.destinations);
}

function onThought(ev) {
  traceItem(`<span class="ti-icon ti-agent" aria-hidden="true"></span><p class="thought">${esc(ev.text)}</p>`, "is-thought");
}

function onRun(ev) {
  state.source = ev.source;
  els.badge.hidden = ev.source !== "mock";
  let text;
  if (ev.replay) text = `Replay of a live run · ${ev.replay.recorded_at || ""}`;
  else if (ev.mode === "gemini") text = `Live · Qloo + ${prettyModel(ev.model)}`;
  else text = "Scripted dev agent";
  els.runBadge.textContent = text;
  els.runBadge.hidden = false;
  els.runBadge.classList.toggle("is-replay", !!ev.replay);
}

const prettyModel = (m) => (m || "Gemini").replace("gemini-", "Gemini ").replace(/-/g, " ").replace(/\bflash\b/i, "Flash").replace(/\blite\b/i, "Lite");

function onDone(ev) {
  els.stats.innerHTML = `
    <span><b>${ev.qloo_calls ?? "–"}</b> Qloo requests</span>
    <span><b>${ev.llm_calls ?? 0}</b> model turns</span>
    <span><b>${((ev.ms || 0) / 1000).toFixed(1)}s</b> live run</span>`;
  els.stats.hidden = false;
}

function onError(ev) {
  traceItem(`<span class="ti-icon ti-error" aria-hidden="true"></span>
    <div class="ti-body"><div class="ti-head"><span class="ti-label">Run stopped</span></div>
    <div class="ti-summary">${esc(ev.message)}</div></div>`, "is-failed is-done");
  toast(ev.message);
}

// --- tour list --------------------------------------------------------------------------

function renderRouteSkeleton(route) {
  els.list.innerHTML = "";
  route.stops.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "tour-stop is-pending";
    li.dataset.city = s.id;
    li.innerHTML = `
      <button type="button" class="tour-stop-btn">
        <span class="tour-stop-num">${String(i + 1).padStart(2, "0")}</span>
        <span class="tour-stop-main">
          <span class="tour-stop-city">${esc(s.name)}</span>
          <span class="tour-stop-meta">${esc(s.state)} · ${i === 0 ? "Opening night" : `${route.legs_km[i - 1].toLocaleString()} km drive`}</span>
          <span class="tour-stop-why">Profiling neighbourhoods…</span>
        </span>
        <span class="tour-stop-score"><strong>${pct(s.score)}</strong><span>taste</span></span>
      </button>`;
    li.querySelector("button").addEventListener("click", () => openCity(s.id));
    els.list.appendChild(li);
  });
  els.total.textContent = `${route.total_km.toLocaleString()} km total`;
}

function revealStop(i) {
  els.list.children[i]?.classList.add("is-visible");
}

function markListReady(cityId) {
  const li = els.list.querySelector(`[data-city="${cityId}"]`);
  const prof = state.profiles[cityId];
  if (!li || !prof) return;
  const hood = prof.hotspots[0]?.name;
  li.querySelector(".tour-stop-why").textContent = hood ? `Strongest around ${hood}` : `${prof.venues.length} venues found`;
}

function renderPlan(plan) {
  state.plan = plan;
  els.tourTitle.textContent = `${plan.stops.length} dates`;
  els.tourHeadline.textContent = plan.headline;
  if (plan.profile) {
    els.audience.innerHTML = `<div class="audience-head"><span>This audience also loves</span><span class="src src-qloo">Qloo</span></div>${profileChips(plan.profile)}`;
    els.audience.hidden = false;
  }
  els.list.innerHTML = "";
  plan.stops.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "tour-stop is-visible";
    li.dataset.city = s.city.id;
    const venue = s.venues[0];
    li.innerHTML = `
      <button type="button" class="tour-stop-btn">
        <span class="tour-stop-num">${String(i + 1).padStart(2, "0")}</span>
        <span class="tour-stop-main">
          <span class="tour-stop-city">${esc(s.city.name)}</span>
          <span class="tour-stop-meta">${esc(s.neighborhood || s.city.state)}${venue ? ` · ${esc(venue.name)}` : ""}</span>
          <span class="tour-stop-why">${esc(s.why)}</span>
        </span>
        <span class="tour-stop-score"><strong>${pct(s.city.score)}</strong><span>taste</span></span>
      </button>`;
    li.querySelector("button").addEventListener("click", () => openCity(s.city.id));
    els.list.appendChild(li);
  });
  els.total.textContent = `${plan.total_km.toLocaleString()} km total`;
  const tourTab = [...els.tabs].find((t) => t.dataset.tab === "tour");
  tourTab.disabled = false;
  setTimeout(() => { if (!tourMap.inCity) showTab("tour"); }, 900);
}

// --- city drill-down --------------------------------------------------------------------

function openCity(cityId) {
  const prof = state.profiles[cityId];
  if (!prof) return toast("This city is still being mapped. One moment.");
  const stop = state.plan?.stops.find((s) => s.city.id === cityId);
  const city = state.cities.find((c) => c.id === cityId) || prof.city;
  const order = state.route?.stops.findIndex((s) => s.id === cityId) ?? -1;

  const picked = new Set(stop?.venue_ids || []);
  const venues = [...prof.venues].sort((a, b) => (picked.has(b.id) - picked.has(a.id)));
  els.cityCard.innerHTML = `
    <div class="cc-scroll">
      <button type="button" class="back-btn" id="city-back">← Back to the tour</button>
      <header class="cc-head">
        <span class="cc-order">${order >= 0 ? `Stop ${String(order + 1).padStart(2, "0")}` : ""}</span>
        <h2>Why ${esc(city.name)}</h2>
        <div class="cc-metrics">
          <div><strong>${pct(city.score)}</strong><span>taste score</span></div>
          <div><strong>#${city.rank ?? "–"}</strong><span>of ${state.cities.length || 44} cities</span></div>
          <div><strong>${signed(pct(city.lift))}</strong><span>pts vs mainstream</span></div>
        </div>
      </header>

      ${stop ? `<section class="cc-block">
        <div class="cc-label"><span>The agent's read</span><span class="src src-agent">Agent</span></div>
        <p class="cc-why">${esc(stop.why)}</p>
        ${stop.collab ? `<p class="cc-collab"><b>Collab idea</b> ${esc(stop.collab)}</p>` : ""}
      </section>` : ""}

      <section class="cc-block">
        <div class="cc-label"><span>Where the taste concentrates</span><span class="src src-qloo">Qloo</span></div>
        <ol class="hotspots">${prof.hotspots.map((h, i) => `
          <li><button type="button" data-hotspot="${i}">
            <span class="hs-rank">#${h.rank}</span><span class="hs-name">${esc(h.name || "Unnamed pocket")}</span>
            <span class="hs-val">${pct(h.taste)}</span></button></li>`).join("")}</ol>
        <p class="cc-note">These are neighbourhoods, not venues: the blocks where this taste is most over-represented
          compared with a mainstream audience. Good places to play, poster and stay. Tap one to see the venues around it.</p>
      </section>

      <section class="cc-block">
        <div class="cc-label"><span id="venues-title">Venues this audience favours</span><span class="src src-qloo">Qloo</span></div>
        <ul class="venues" id="venues"></ul>
      </section>

      ${prof.spots.length ? `<section class="cc-block">
        <div class="cc-label"><span>Pre-show spots</span><span class="src src-qloo">Qloo</span></div>
        <ul class="spots">${prof.spots.slice(0, 4).map((p) => `
          <li><span>${esc(p.name)}</span><small>${esc([p.tags?.[0], p.neighborhood].filter(Boolean).join(" · "))}</small></li>`).join("")}</ul>
      </section>` : ""}
    </div>`;

  showTab("city");
  els.legendTitle.textContent = `Neighbourhood taste · ${city.name}`;
  els.cityCard.querySelector("#city-back").addEventListener("click", closeCity);
  const ctx = { prof, picked, city, stop, order };
  renderVenues(ctx, null);
  const selectHotspot = (i) => {
    const h = prof.hotspots[i];
    const same = ctx.anchor === h;
    els.cityCard.querySelectorAll("[data-hotspot]").forEach((b) => b.classList.toggle("is-active", !same && +b.dataset.hotspot === i));
    renderVenues(ctx, same ? null : h);
    if (!same) tourMap.focusPoint(h);
  };
  els.cityCard.querySelectorAll("[data-hotspot]").forEach((b) => b.addEventListener("click", () => selectHotspot(+b.dataset.hotspot)));
  tourMap.enterCity(prof, {
    onHotspot: selectHotspot,
    venues: venues.filter((v) => picked.has(v.id)),
    onVenue: (id) => {
      const row = els.cityCard.querySelector(`[data-venue="${CSS.escape(id)}"]`);
      row?.scrollIntoView({ block: "center", behavior: "smooth" });
      row?.parentElement.classList.add("is-flash");
      setTimeout(() => row?.parentElement.classList.remove("is-flash"), 900);
    },
  });
}

function kmBetween(a, b) {
  const r = Math.PI / 180;
  const dLat = (b.lat - a.lat) * r;
  const dLon = (b.lon - a.lon) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return null; } };
const prettyPhone = (p) => (/^\+1\d{10}$/.test(p) ? `(${p.slice(2, 5)}) ${p.slice(5, 8)}-${p.slice(8)}` : p);

// Venue list for the city card, optionally re-sorted around one hotspot.
function renderVenues(ctx, anchor) {
  ctx.anchor = anchor;
  const { prof, picked, city, stop, order } = ctx;
  const list = [...prof.venues];
  if (anchor) list.sort((a, b) => kmBetween(anchor, a) - kmBetween(anchor, b));
  else list.sort((a, b) => picked.has(b.id) - picked.has(a.id));
  $("venues-title").textContent = anchor ? `Venues near ${anchor.name || "this pocket"}` : "Venues this audience favours";
  $("venues").innerHTML = list.map((v) => {
    const where = anchor && v.lat != null
      ? `${kmBetween(anchor, v).toFixed(1)} km from ${anchor.name || "this pocket"}`
      : v.km_to_hotspot != null ? `${v.km_to_hotspot} km from a hotspot` : null;
    const host = v.website && hostOf(v.website);
    return `
      <li class="${picked.has(v.id) ? "is-picked" : ""}">
        <button type="button" data-venue="${esc(v.id)}">
          <span class="v-name">${esc(v.name)}${picked.has(v.id) ? `<em>Agent pick</em>` : ""}</span>
          <span class="v-meta">${esc([v.neighborhood, where].filter(Boolean).join(" · "))}</span>
          <span class="v-addr">${esc(v.address || "")}</span>
        </button>
        <span class="v-aff">${pct(v.affinity)}</span>
        <div class="v-actions">
          ${host ? `<a href="${esc(v.website)}" target="_blank" rel="noopener noreferrer">${esc(host)}</a>` : ""}
          ${v.phone ? `<a href="tel:${esc(v.phone)}">${esc(prettyPhone(v.phone))}</a>` : ""}
          <button type="button" class="ghost-btn draft-btn" data-draft="${esc(v.id)}">Draft booking email</button>
        </div>
        <div class="draft" hidden></div>
      </li>`;
  }).join("");
  $("venues").querySelectorAll("[data-venue]").forEach((b) => b.addEventListener("click", () => {
    tourMap.focusVenue(prof.venues.find((v) => v.id === b.dataset.venue));
  }));
  $("venues").querySelectorAll("[data-draft]").forEach((b) => b.addEventListener("click", () => {
    draftEmail(b, prof.venues.find((v) => v.id === b.dataset.draft), city, stop, order);
  }));
}

async function draftEmail(btn, venue, city, stop, order) {
  const box = btn.closest("li").querySelector(".draft");
  btn.disabled = true;
  btn.textContent = "Drafting…";
  try {
    const res = await fetch("/api/email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        artists: state.plan.artists.map((a) => a.name),
        city: `${city.name}, ${city.state}`,
        venue: venue.name,
        venue_address: venue.address || "",
        neighborhood: venue.neighborhood || stop?.neighborhood || "",
        taste_score: pct(city.score),
        stop_number: order + 1,
        total_stops: state.plan?.stops.length || 1,
        why: stop?.why || "",
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Drafting failed");
    box.innerHTML = `
      <div class="draft-head"><span>Draft</span><span class="src src-agent">Agent</span></div>
      <input class="draft-subject" value="${esc(data.subject)}" aria-label="Subject">
      <textarea class="draft-body" rows="9" aria-label="Email body">${esc(data.body)}</textarea>
      <button type="button" class="ghost-btn draft-copy">Copy email</button>`;
    box.hidden = false;
    box.querySelector(".draft-copy").addEventListener("click", async () => {
      const text = `Subject: ${box.querySelector(".draft-subject").value}

${box.querySelector(".draft-body").value}`;
      try { await navigator.clipboard.writeText(text); toast("Email copied."); } catch { toast("Copy is blocked in this browser."); }
    });
    btn.remove();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Draft booking email";
    toast(err.message);
  }
}

function closeCity() {
  tourMap.exitCity();
  els.legendTitle.textContent = "Taste over-representation";
  showTab(state.plan ? "tour" : "agent");
}

// --- export -------------------------------------------------------------------------------

function sheetRows() {
  return (state.plan?.stops || []).map((s, i) => ({
    date: i + 1,
    city: `${s.city.name}, ${s.city.state}`,
    neighborhood: s.neighborhood || "",
    venue: s.venues.map((v) => v.name).join(" / "),
    address: s.venues[0]?.address || "",
    taste: pct(s.city.score),
    drive_km: s.leg_km,
    why: s.why,
  }));
}

els.copy.addEventListener("click", async () => {
  const rows = sheetRows();
  if (!rows.length) return;
  const artists = state.plan.artists.map((a) => a.name).join(" + ");
  const text = [`Tour Radar · ${artists} · ${rows.length} dates · ${state.plan.total_km.toLocaleString()} km`, ""]
    .concat(rows.map((r) => `${String(r.date).padStart(2, "0")}. ${r.city} — ${r.venue} (${r.neighborhood})\n    ${r.why}`))
    .join("\n");
  try {
    await navigator.clipboard.writeText(text);
    toast("Tour sheet copied.");
  } catch {
    toast("Copy is blocked in this browser. Use CSV instead.");
  }
});

els.csv.addEventListener("click", () => {
  const rows = sheetRows();
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const cell = (v) => `"${String(v).replace(/"/g, '""')}"`;
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "tour-radar.csv";
  a.click();
  URL.revokeObjectURL(a.href);
});

// --- run ---------------------------------------------------------------------------------

function resetRun() {
  state.stream?.close();
  Object.assign(state, { cities: [], profiles: {}, route: null, plan: null, profile: null });
  toolRows.clear();
  els.trace.innerHTML = "";
  els.stats.hidden = true;
  els.list.innerHTML = "";
  els.audience.hidden = true;
  els.tourHeadline.textContent = "";
  [...els.tabs].find((t) => t.dataset.tab === "tour").disabled = true;
  showTab("agent");
  mapQueue = Promise.resolve();
  tourMap.reset();
}

async function start({ example } = {}) {
  if (state.running) return;
  if (!example && !state.artists.length) {
    toast("Add at least one artist you sound like.");
    els.input.focus();
    return;
  }
  setBusy(true);
  document.body.classList.add("has-run");
  await tourMap.ready;
  resetRun();
  const qs = example
    ? new URLSearchParams({ example })
    : new URLSearchParams({ artists: state.artists.join(","), dates: els.dates.value });
  const es = new EventSource(`/api/agent?${qs}`);
  state.stream = es;
  let finished = false;
  const finish = () => {
    finished = true;
    es.close();
    setBusy(false);
  };
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    switch (ev.type) {
      case "run": onRun(ev); break;
      case "thought": onThought(ev); break;
      case "tool_start": onToolStart(ev); break;
      case "progress": onProgress(ev); break;
      case "tool_end": onToolEnd(ev); break;
      case "plan": onMap(() => renderPlan(ev.plan)); break;
      case "done": onDone(ev); finish(); break;
      case "error": onError(ev); finish(); break;
    }
  };
  es.onerror = () => {
    if (finished) return;
    finish();
    onError({ message: "Lost the connection to the agent. Try again, or open an example." });
  };
}

// --- boot --------------------------------------------------------------------------------

async function boot() {
  renderChips();
  // Examples and status must not wait for map tiles.
  try {
    const [health, examples] = await Promise.all([
      fetch("/api/health").then((r) => r.json()),
      fetch("/api/examples").then((r) => r.json()),
    ]);
    els.badge.hidden = health.source !== "mock";
    examples.forEach((ex) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = ex.label;
      b.title = `Replay a live run: ${ex.artists.join(", ")}, ${ex.dates} dates`;
      b.addEventListener("click", () => {
        state.artists = [...ex.artists];
        els.dates.value = String(ex.dates);
        renderChips();
        start({ example: ex.slug });
      });
      els.examples.appendChild(b);
    });
    if (!examples.length) els.examples.hidden = true;
    if (health.agent === "unavailable") toast("The live agent is offline right now. Examples still work.");
  } catch (err) {
    console.error(err);
    toast("Could not reach the server.");
  }
  try {
    const [cities] = await Promise.all([fetch("/api/cities").then((r) => r.json()), tourMap.ready]);
    tourMap.setCandidates(cities);
  } catch (err) {
    console.error(err);
  }
}

boot();
