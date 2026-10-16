import { TourMap } from "./tour-map.js";

const $ = (id) => document.getElementById(id);
const els = {
  input: $("artist-input"),
  field: $("artist-field"),
  chips: $("chips"),
  dates: $("dates"),
  scan: $("scan-btn"),
  list: $("tour-list"),
  sub: $("tour-sub"),
  total: $("tour-total"),
  badge: $("mode-badge"),
  toast: $("toast"),
};

const MAX_ARTISTS = 3;
let artists = [];
let running = false;

const isNarrow = () => window.matchMedia("(max-width: 860px)").matches;
const tourMap = new TourMap("map", {
  padding: isNarrow()
    ? { top: 220, bottom: Math.round(window.innerHeight * 0.4) + 20, left: 30, right: 30 }
    : { top: 130, bottom: 100, left: 480, right: 560 },
});

// --- artist chips ---------------------------------------------------------------

function renderChips() {
  els.chips.innerHTML = "";
  artists.forEach((name, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name;
    const x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", `Remove ${name}`);
    x.textContent = "×";
    x.onclick = () => { artists.splice(i, 1); renderChips(); };
    chip.appendChild(x);
    els.chips.appendChild(chip);
  });
  els.input.placeholder = artists.length >= MAX_ARTISTS ? "" : artists.length ? "Add another" : "Type an artist and press Enter";
  els.input.disabled = artists.length >= MAX_ARTISTS;
}

function addArtist(name) {
  const clean = name.trim();
  if (!clean || artists.length >= MAX_ARTISTS) return;
  if (!artists.some((a) => a.toLowerCase() === clean.toLowerCase())) artists.push(clean);
  els.input.value = "";
  renderChips();
}

els.input.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === ",") && els.input.value.trim()) {
    e.preventDefault();
    addArtist(els.input.value);
  } else if (e.key === "Enter" && artists.length) {
    e.preventDefault();
    scan();
  } else if (e.key === "Backspace" && !els.input.value && artists.length) {
    artists.pop();
    renderChips();
  }
});
els.field.addEventListener("click", () => els.input.focus());
document.querySelectorAll("[data-example]").forEach((btn) => {
  btn.addEventListener("click", () => {
    artists = btn.dataset.example.split(",").map((s) => s.trim());
    renderChips();
    scan();
  });
});
els.scan.addEventListener("click", () => {
  if (els.input.value.trim()) addArtist(els.input.value);
  scan();
});

// --- feedback -----------------------------------------------------------------------

let toastTimer;
function toast(msg) {
  els.toast.textContent = msg;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (els.toast.hidden = true), 4200);
}

function setBusy(busy) {
  running = busy;
  els.scan.disabled = busy;
  els.scan.classList.toggle("is-busy", busy);
  els.scan.lastChild.textContent = busy ? "Scanning…" : "Scan the map";
}

// --- tour list -----------------------------------------------------------------------

function renderTourList(route) {
  els.list.innerHTML = "";
  const max = Math.max(...route.stops.map((s) => s.affinity));
  route.stops.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "tour-stop";
    const leg = i === 0 ? "Opening night" : `${route.legs_km[i - 1].toLocaleString()} km from ${route.stops[i - 1].name}`;
    li.innerHTML = `
      <span class="tour-stop-num">${String(i + 1).padStart(2, "0")}</span>
      <div>
        <div class="tour-stop-city">${s.name}</div>
        <div class="tour-stop-meta">${s.state} · ${leg}</div>
      </div>
      <div class="tour-stop-aff"><strong>${s.affinity.toFixed(3)}</strong><span>affinity</span></div>
      <div class="aff-bar"><i style="width:${Math.round((s.affinity / max) * 100)}%"></i></div>`;
    els.list.appendChild(li);
  });
}

function revealStop(i) {
  const li = els.list.children[i];
  if (!li) return;
  li.classList.add("is-visible");
  li.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// --- scan ----------------------------------------------------------------------------

async function scan() {
  if (running) return;
  if (!artists.length) {
    toast("Add at least one artist you sound like.");
    els.input.focus();
    return;
  }
  setBusy(true);
  els.total.hidden = true;
  els.sub.textContent = `Reading the taste of ${artists.join(" + ")}…`;
  els.list.innerHTML = "";
  tourMap.reset();
  try {
    const qs = new URLSearchParams({ artists: artists.join(","), dates: els.dates.value });
    const res = await fetch(`/api/scan?${qs}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Scan failed");

    const stops = data.route.stops;
    tourMap.setTaste(data.cells);
    els.sub.textContent = `Scoring ${data.cities.length} cities by taste affinity…`;
    await tourMap.ignite(data.cities, stops.map((s) => s.id));

    els.sub.textContent = `Routing ${stops.length} dates through the strongest markets…`;
    renderTourList(data.route);
    await tourMap.focus(stops);
    await tourMap.drawRoute(stops, { onStop: revealStop });

    els.sub.textContent = `${stops.length} dates for fans of ${data.artists.map((a) => a.name).join(" + ")}`;
    els.total.innerHTML = `<span>Total drive</span><strong>${data.route.total_km.toLocaleString()} km</strong>`;
    els.total.hidden = false;
  } catch (err) {
    console.error(err);
    els.sub.textContent = "Something went wrong";
    els.list.innerHTML = `<li class="tour-empty">We couldn't finish this scan. Try again, or pick one of the examples.</li>`;
    toast(err.message || "Scan failed");
  } finally {
    setBusy(false);
  }
}

// --- boot ----------------------------------------------------------------------------

async function boot() {
  try {
    const [health, cities] = await Promise.all([
      fetch("/api/health").then((r) => r.json()),
      fetch("/api/cities").then((r) => r.json()),
      tourMap.ready,
    ]);
    els.badge.hidden = health.mode !== "mock";
    tourMap.setCandidates(cities);
  } catch (err) {
    console.error(err);
    toast("Could not reach the server.");
  }
  renderChips();
}

boot();
