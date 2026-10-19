# Tour Radar

**Plan a tour where your sound already has an audience.**

Tour Radar is an agent for independent musicians. You name up to three acts you sound like. The agent asks the [Qloo Taste AI API](https://docs.qloo.com/) where people with that taste are over-represented across the US, routes a drivable tour through the strongest cities, and for every stop picks the neighbourhood where the taste concentrates, venues that audience favours, pre-show spots, and a collab idea. It explains each stop in plain language and drafts the booking email.

> Spotify for Artists tells you where your fans are — if you already have them. Tour Radar tells you where they will be.

Built for the Qloo Agentic Hackathon (2026).

**Live demo:** https://tour-radar.onrender.com (free tier; the first visit after a quiet spell can take up to a minute to wake). The three examples replay real recorded runs and work instantly.

## Why this needs Qloo

Small acts can't plan from their own streaming stats: a few thousand monthly listeners spread over 40 cities is noise. Booking tools route by venue capacity or fuel cost, or by "where similar artists played", which only works once you're already on that circuit.

The question a touring act actually has is *"where do people with my taste live, block by block?"* No language model knows that. Qloo's heatmaps do. Every city, score, neighbourhood, venue, brand and similar artist in Tour Radar comes from a Qloo response; the model only orders, chooses among and explains what Qloo returned, and the server rejects any venue or city the model did not get from Qloo.

## What it does

1. **Finds the taste.** Resolves the acts you name with Qloo search (with autocomplete).
2. **Scans the country.** One nationwide Qloo heatmap for your acts, compared cell by cell with a heatmap for a broad mainstream audience, scores 44 US cities. Cities ignite on the map as they are scored.
3. **Reads the audience.** Similar artists, brands and travel destinations the same people like (Qloo insights across domains).
4. **Routes the tour.** The agent chooses the cities; a planner orders them into the shortest drivable path (nearest neighbour + 2-opt) and drops stops that would split one metro's audience. The route draws itself on the map.
5. **Maps each city.** A neighbourhood-level taste heatmap, the three strongest pockets (named), live-music venues and pre-show spots (record stores, coffee, cocktail bars) for that audience.
6. **Explains and acts.** A short "why this city" per stop, a collab idea, an exportable tour sheet (copy or CSV), and a booking email draft per venue with placeholders for anything the data can't know.

Every step streams into a live trace panel: the model's one-line intent, each tool call with its arguments, real numbers from Qloo, and timings.

Three one-click examples (Phoebe Bridgers + boygenius, Khruangbin, Turnstile) replay real recorded runs instantly, so you can see the whole flow without waiting on any API.

## How Qloo is used

All calls go to `https://hackathon.api.qloo.com` with the key held server-side.

| Step | Endpoint and key parameters | Why |
|---|---|---|
| Resolve artists | `GET /search?query=…&types=urn:entity:artist` | Map names typed by the user to Qloo entity IDs. Also powers autocomplete. |
| Nationwide taste | `GET /v2/insights?filter.type=urn:heatmap&filter.location=<WKT of the contiguous US>&signal.interests.entities=<artist ids>` | One heatmap for the whole country, so cells are comparable across cities. |
| Mainstream baseline | Same request with `signal.interests.entities=<Taylor Swift, Drake, Morgan Wallen, Bad Bunny, Ed Sheeran>` | Separates *this* taste from "where people are". Cached once per region/city. |
| Audience profile | `GET /v2/insights?filter.type=urn:entity:brand` / `urn:entity:artist` / `urn:entity:destination` (+ `filter.geocode.country_code=US`) with the artist signal | Cross-domain context for the "why" text and collab ideas. |
| Neighbourhood heat | `GET /v2/insights?filter.type=urn:heatmap&filter.location.query=<City, State>` for the artists and for the baseline | Block-level glow inside each tour city. |
| Venues | `GET /v2/insights?filter.type=urn:entity:place&filter.tags=urn:tag:genre:place:live_music_venue&filter.location.query=<City, State>` + artist signal | Venues this audience favours, with addresses and neighbourhood names. |
| Pre-show spots | Same with `filter.tags=urn:tag:category:place:record_store,…:coffee_shop,…:cocktail_bar` | Where to flyer, play an in-store or meet the scene. |

### What we learned from the data, and how the scoring works

Heatmap `affinity` is a rank percentile inside a single response, and inside a city it follows overall activity closely (Spearman ≈ 0.93–0.97 with `popularity` in our probes of Portland and New York). Shown raw, it mostly lights up downtowns. So Tour Radar always compares against the mainstream baseline over the same cells:

```
lift  = affinity(your acts) − affinity(mainstream)       per geohash cell
taste = 0.6 · affinity + 0.4 · clamp(lift + 0.5, 0, 1)    per cell, 0..1
city taste score = taste over all cells within 40 km, shown as 0–100
```

In a city, hotspots are the strongest *pockets* (taste averaged over ~800 m), not single cells, so they line up with the glow you see.

### A request → result walkthrough (key redacted)

```
GET /search?query=Phoebe%20Bridgers&types=urn:entity:artist&take=3
→ Phoebe Bridgers  AFBC71A7-9439-4AB0-AB68-D14B11AAF836

GET /v2/insights?filter.type=urn:entity:place
    &filter.tags=urn:tag:genre:place:live_music_venue
    &filter.location.query=Portland, Oregon
    &signal.interests.entities=AFBC71A7-9439-4AB0-AB68-D14B11AAF836&take=8
→ Wonder Ballroom (Eliot), Revolution Hall (Buckman), Polaris Hall (Humboldt),
  Mississippi Studios (Boise), Alberta Abbey (King), …

GET /v2/insights?filter.type=urn:entity:artist&signal.interests.entities=AFBC71A7-…&take=10
→ Lucy Dacus, Julien Baker, Faye Webster, Adrianne Lenker, Big Thief, …
```

## The agent

A plain tool-calling loop on Gemini (`gemini-3.5-flash`, with automatic fallback to lighter models when a free-tier quota is hit). Six tools:

| Tool | Does |
|---|---|
| `search_artists` | Qloo search |
| `scan_region` | Nationwide heatmap vs baseline → ranked cities |
| `taste_profile` | Similar artists, brands, destinations |
| `plan_route` | Validates the cities the model picked against the scan, removes same-metro duplicates, orders the route |
| `profile_cities` | Neighbourhood heat, hotspots, venues, spots for every routed city (in parallel) |
| `submit_plan` | Final plan. Rejected with specific errors if it cites a city not on the route or a venue Qloo didn't return; the model must fix and resubmit |

The model writes one sentence before each call, which the trace shows. Qloo facts are labelled **Qloo** in the UI; interpretations are labelled **Agent**.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # add your keys
uvicorn app.main:app --port 7860
```

Open http://localhost:7860.

| Variable | Meaning |
|---|---|
| `QLOO_API_KEY` | Qloo hackathon key (server-side only) |
| `QLOO_BASE_URL` | Defaults to `https://hackathon.api.qloo.com` |
| `QLOO_MOCK` | `1` serves offline fixtures shaped like real responses, for development and tests |
| `GEMINI_API_KEY` | Google AI Studio key (free tier works) |
| `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS` | Primary model and fallbacks |
| `AGENT_MODE=scripted` | Offline dev agent with no LLM; only allowed together with `QLOO_MOCK=1` |

The examples work with no keys at all, since they replay recorded runs.

Other useful commands:

```bash
pytest -q                                            # tests run against fixtures, no keys needed
python scripts/qloo_spike.py                         # probe the Qloo API (≈12 requests)
python scripts/record_example.py slug "Artist A, Artist B" 8 --label "…"   # record a new example
docker build -t tour-radar . && docker run -p 7860:7860 --env-file .env tour-radar
```

### Deploying

`render.yaml` is a Render Blueprint for a free Docker web service: connect the repository in Render, add `QLOO_API_KEY` and `GEMINI_API_KEY` when prompted, and every push to `main` redeploys. The container listens on `$PORT` (default 7860), so any Docker host works.

## Stack

FastAPI (Python 3.11) streaming the agent as server-sent events · Gemini via `google-genai` · MapLibre GL with OpenFreeMap vector tiles and a custom dark style · plain HTML/CSS/JS, no build step · Docker on Render's free tier. Neighbourhood names come from Qloo place data, with OpenStreetMap Nominatim as a cached fallback.

## Respecting the API

- The Qloo key never reaches the browser.
- Requests are throttled below the key's per-second limit, retried with backoff only on 429/5xx, and every response is cached on disk, so repeat views cost nothing.
- A full new tour costs about 45 Qloo requests; baseline heatmaps are shared across all users.
- Inputs are public artist names and city names. No personal data is sent to Qloo.

## Known limitations

- US only for now (the same pipeline works for any region; London heatmaps returned the same shape in testing).
- 44 candidate cities. Smaller college towns can be added to `app/data/us_cities.json`.
- Taste scores show where people with a taste are over-represented relative to a mainstream audience. They are not fan counts or ticket forecasts.
- Venue capacity isn't in the data, so the agent can pick a room that is too big for a small act; the city card lists every venue Qloo found so you can choose.
- On the free Gemini tier, heavy traffic can exhaust the quota; the app falls back to lighter models and the examples keep working.

## License

[MIT](LICENSE)
